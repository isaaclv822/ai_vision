"""
SENTINEL-X — Base de données locale (SQLite, inclus dans Python).

Trois usages :
  - les personnes autorisées et leurs empreintes faciales (remplace le dossier autorises/)
  - l'historique des événements (changements d'état, intrusions, acquittements)
  - les captures d'intrusion (la photo reste un fichier JPEG, la base garde son chemin)

Choix de sécurité :
  - on stocke l'EMPREINTE (128 nombres calculés par SFace), jamais la photo du visage :
    une fuite de la base n'expose aucune image exploitable
  - chaque empreinte garde le nom du modèle qui l'a calculée : avec un autre modèle,
    elle n'a plus aucun sens (et rien ne planterait, plus personne ne serait reconnu)
  - requêtes paramétrées partout (?) : pas d'injection SQL possible

Usage :
    base = BaseDonnees()
    base.ajouter_personne("joakim")
    base.ajouter_empreinte("joakim", empreinte)      # une ligne par photo
    references = base.charger_empreintes()           # [("joakim", empreinte), ...]
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CHEMIN_BASE = Path(__file__).parent / "donnees" / "sentinelx.db"   # Non versionné
MODELE_EMPREINTE = "sface_2021dec"     # Modèle qui calcule les empreintes aujourd'hui
TAILLE_EMPREINTE = 128                 # Nombre de valeurs dans une empreinte SFace
FORMAT_PRENOM = re.compile(r"[\w\- ]{1,50}")   # Lettres (accents compris), chiffres, - et espace

SCHEMA = """
CREATE TABLE IF NOT EXISTS personnes (
    id          INTEGER PRIMARY KEY,
    prenom      TEXT NOT NULL UNIQUE,
    actif       INTEGER NOT NULL DEFAULT 1 CHECK (actif IN (0, 1)),
    ajoute_le   TEXT NOT NULL,
    revoque_le  TEXT
);

-- Une ligne par photo : la reconnaissance garde le meilleur score parmi toutes
-- les empreintes d'une personne (une moyenne ne ressemblerait à aucune pose réelle).
CREATE TABLE IF NOT EXISTS empreintes (
    id           INTEGER PRIMARY KEY,
    personne_id  INTEGER NOT NULL REFERENCES personnes(id),
    empreinte    BLOB NOT NULL,
    modele       TEXT NOT NULL,
    ajoute_le    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evenements (
    id          INTEGER PRIMARY KEY,
    horodatage  TEXT NOT NULL,
    source      TEXT NOT NULL,      -- "vision", "dashboard"...
    type        TEXT NOT NULL,      -- "CHANGEMENT_ETAT", "ALERTE_INTRUSION"...
    etat        TEXT,
    cause       TEXT,
    details     TEXT                -- Le reste du message, en JSON
);
CREATE INDEX IF NOT EXISTS idx_evenements_horodatage ON evenements(horodatage);

CREATE TABLE IF NOT EXISTS captures (
    id            INTEGER PRIMARY KEY,
    evenement_id  INTEGER REFERENCES evenements(id),
    chemin        TEXT NOT NULL,
    horodatage    TEXT NOT NULL
);

-- Dernier état connu de chaque source : UNE ligne par source, écrasée à chaque
-- mise à jour (la table ne grossit pas). Le dashboard la relit en continu.
CREATE TABLE IF NOT EXISTS etat_courant (
    source      TEXT PRIMARY KEY,
    horodatage  TEXT NOT NULL,
    details     TEXT NOT NULL       -- JSON
);
"""


def maintenant():
    """Horodatage ISO 8601 en UTC, le même format que les messages de main.py."""
    return datetime.now(timezone.utc).isoformat()


class BaseDonnees:

    def __init__(self, chemin=CHEMIN_BASE):
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
        self.connexion = sqlite3.connect(chemin)
        self.connexion.row_factory = sqlite3.Row
        # WAL : main.py et le dashboard peuvent lire et écrire en même temps
        self.connexion.execute("PRAGMA journal_mode = WAL")
        self.connexion.execute("PRAGMA foreign_keys = ON")    # Désactivé par défaut en SQLite
        self.connexion.execute("PRAGMA busy_timeout = 2000")  # Attend au lieu d'échouer si occupée
        self.connexion.executescript(SCHEMA)

    def fermer(self):
        self.connexion.close()

    # --- Personnes autorisées -------------------------------------------------
    def ajouter_personne(self, prenom):
        """Ajoute une personne autorisée. Renvoie son id."""
        prenom = prenom.strip()
        if not FORMAT_PRENOM.fullmatch(prenom):
            raise ValueError(f"Prénom invalide : {prenom!r}")
        try:
            with self.connexion:
                curseur = self.connexion.execute(
                    "INSERT INTO personnes (prenom, ajoute_le) VALUES (?, ?)",
                    (prenom, maintenant()))
        except sqlite3.IntegrityError:
            raise ValueError(f"{prenom} existe déjà") from None
        return curseur.lastrowid

    def ajouter_empreinte(self, prenom, empreinte, modele=MODELE_EMPREINTE):
        """Enregistre l'empreinte d'une photo de la personne. Renvoie son id."""
        vecteur = np.asarray(empreinte, dtype=np.float32).reshape(-1)
        if vecteur.size != TAILLE_EMPREINTE:
            raise ValueError(f"Empreinte de {vecteur.size} valeurs, {TAILLE_EMPREINTE} attendues")
        if not np.all(np.isfinite(vecteur)):
            raise ValueError("Empreinte invalide (NaN ou infini)")

        ligne = self.connexion.execute(
            "SELECT id FROM personnes WHERE prenom = ?", (prenom,)).fetchone()
        if ligne is None:
            raise ValueError(f"Personne inconnue : {prenom}")
        with self.connexion:
            curseur = self.connexion.execute(
                "INSERT INTO empreintes (personne_id, empreinte, modele, ajoute_le) "
                "VALUES (?, ?, ?, ?)",
                (ligne["id"], vecteur.tobytes(), modele, maintenant()))
        return curseur.lastrowid

    def revoquer(self, prenom):
        """Retire l'accès sans effacer la personne (l'historique reste cohérent)."""
        with self.connexion:
            curseur = self.connexion.execute(
                "UPDATE personnes SET actif = 0, revoque_le = ? WHERE prenom = ? AND actif = 1",
                (maintenant(), prenom))
        return curseur.rowcount == 1

    def charger_empreintes(self, modele=MODELE_EMPREINTE):
        """
        Empreintes des personnes ACTIVES calculées avec CE modèle.
        Renvoie [(prenom, empreinte), ...] : le format de facial_recognition.py,
        avec une empreinte numpy de forme (1, 128) comme celle de SFace.
        """
        lignes = self.connexion.execute(
            "SELECT p.prenom, e.empreinte FROM empreintes e "
            "JOIN personnes p ON p.id = e.personne_id "
            "WHERE p.actif = 1 AND e.modele = ? ORDER BY p.prenom, e.id",
            (modele,)).fetchall()
        return [(ligne["prenom"],
                 np.frombuffer(ligne["empreinte"], dtype=np.float32).reshape(1, -1).copy())
                for ligne in lignes]

    def compter_empreintes_autre_modele(self, modele=MODELE_EMPREINTE):
        """Empreintes inutilisables car calculées avec un autre modèle : à recalculer."""
        return self.connexion.execute(
            "SELECT COUNT(*) FROM empreintes WHERE modele != ?", (modele,)).fetchone()[0]

    def personnes(self):
        """Liste des personnes (actives ou non) avec leur nombre d'empreintes."""
        lignes = self.connexion.execute(
            "SELECT p.prenom, p.actif, p.ajoute_le, p.revoque_le, COUNT(e.id) AS nb_empreintes "
            "FROM personnes p LEFT JOIN empreintes e ON e.personne_id = p.id "
            "GROUP BY p.id ORDER BY p.prenom").fetchall()
        return [dict(ligne) for ligne in lignes]

    # --- Événements et captures -------------------------------------------------
    def enregistrer_evenement(self, source, type_evenement, details):
        """Enregistre un événement (ex. le details d'un message de main.py). Renvoie son id."""
        with self.connexion:
            curseur = self.connexion.execute(
                "INSERT INTO evenements (horodatage, source, type, etat, cause, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (maintenant(), source, type_evenement, details.get("etat"),
                 details.get("cause"), json.dumps(details, ensure_ascii=False)))
        return curseur.lastrowid

    def enregistrer_capture(self, chemin, evenement_id=None):
        """Relie une photo d'intrusion (fichier local) à l'événement qui l'a déclenchée."""
        with self.connexion:
            curseur = self.connexion.execute(
                "INSERT INTO captures (evenement_id, chemin, horodatage) VALUES (?, ?, ?)",
                (evenement_id, str(chemin), maintenant()))
        return curseur.lastrowid

    def derniers_evenements(self, nombre=20):
        """Les événements les plus récents d'abord, avec le chemin de leur capture s'il y en a une."""
        lignes = self.connexion.execute(
            "SELECT ev.id, ev.horodatage, ev.source, ev.type, ev.etat, ev.cause, ev.details, "
            "c.chemin AS capture FROM evenements ev "
            "LEFT JOIN captures c ON c.evenement_id = ev.id "
            "ORDER BY ev.id DESC LIMIT ?", (nombre,)).fetchall()
        evenements = []
        for ligne in lignes:
            evenement = dict(ligne)
            evenement["details"] = json.loads(evenement["details"]) if evenement["details"] else {}
            evenements.append(evenement)
        return evenements

    # --- État courant (lu par le dashboard) -----------------------------------
    def mettre_a_jour_etat_courant(self, source, details):
        """Écrase le dernier état connu de la source (une seule ligne par source)."""
        with self.connexion:
            self.connexion.execute(
                "INSERT INTO etat_courant (source, horodatage, details) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET horodatage = excluded.horodatage, "
                "details = excluded.details",
                (source, maintenant(), json.dumps(details, ensure_ascii=False)))

    def lire_etat_courant(self, source):
        """Renvoie (horodatage, details) du dernier état de la source, ou None."""
        ligne = self.connexion.execute(
            "SELECT horodatage, details FROM etat_courant WHERE source = ?", (source,)).fetchone()
        if ligne is None:
            return None
        return ligne["horodatage"], json.loads(ligne["details"])
