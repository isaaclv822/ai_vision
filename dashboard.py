"""
SENTINEL-X — Dashboard de supervision (tkinter).

Affiche, pour le jury et pendant le pentest :
  1. le score de menace unifié, en grand, avec la couleur de l'état global
  2. une tuile par source : vision, capteurs (ESP8266), réseau
  3. un journal horodaté, aussi enregistré sur disque (la preuve du pentest)
  4. le signal de vie de chaque source : une source qui se tait déclenche une alerte
  5. les indicateurs cyber : canal chiffré et payloads rejetés
  + un bouton « Alerte prise en compte », comme dans un vrai SOC

Sources :
  - VISION : vraies données, lues dans la BDD (table etat_courant, écrite par main.py)
  - capteurs, réseau, score, cyber : ENCORE SIMULÉES (classe DonneesSimulees),
    en attendant les données de l'équipe cyber
Point de branchement pour les vraies données : Dashboard.recevoir(source, donnees).
"""

import random
import sqlite3
import time
import tkinter as tk
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from base_donnees import BaseDonnees

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SILENCE_MAX_S = 5.0                # Au-delà, une source est déclarée muette
RAFRAICHISSEMENT_MS = 250          # Mise à jour de l'affichage
PERIODE_SIMULATION_MS = 1000       # Les sources simulées envoient une donnée par seconde
PERIODE_LECTURE_BDD_MS = 500       # Relecture de l'état de la vision dans la BDD
TAILLE_COURBES = 60                # Nombre de points par courbe (~1 min)
TAILLE_JOURNAL = 200               # Lignes gardées à l'écran (le fichier garde tout)
DOSSIER_JOURNAUX = Path(__file__).parent / "journaux"

FOND = "#14161a"
FOND_TUILE = "#1f2228"
TEXTE = "#e6e6e6"
GRIS = "#8a8f98"
COULEURS = {"VERT": "#2e9d4a", "ORANGE": "#e08a1e", "ROUGE": "#d0342c", None: "#3a3d44"}
POLICE = "Consolas"
ORDRE_GRAVITE = [None, "VERT", "ORANGE", "ROUGE"]

# Sources surveillées -> nom affiché
SOURCES = {"vision": "Vision", "capteurs": "ESP8266", "reseau": "Sonde reseau", "score": "IA fusion"}


def pire_etat(*etats):
    return max(etats, key=ORDRE_GRAVITE.index)


# ---------------------------------------------------------------------------
# Données simulées (à remplacer par les vraies sources)
# ---------------------------------------------------------------------------
class DonneesSimulees:
    """
    Fabrique de fausses données qui rejoue un scénario en boucle, pour
    développer et répéter la démo sans les autres briques du projet.
    """

    # (phase, durée en secondes)
    SCENARIO = [
        ("normal", 15),
        ("dos", 12),
        ("normal", 10),
        ("fuite_gaz", 10),
        ("normal", 10),
        ("capteur_muet", 8),
        ("payload_rejete", 1),
    ]

    def __init__(self):
        self.debut = time.monotonic()
        self.connexions_refusees = 0
        self.payloads_rejetes = 0

    def phase(self):
        duree_totale = sum(duree for _, duree in self.SCENARIO)
        ecoule = (time.monotonic() - self.debut) % duree_totale
        for nom, duree in self.SCENARIO:
            if ecoule < duree:
                return nom
            ecoule -= duree
        return "normal"

    def generer(self):
        """Renvoie la liste des (source, données) de cette seconde."""
        phase = self.phase()
        donnees = []

        # La vision n'est plus simulée : elle vient de la BDD (voir Dashboard.lire_bdd)

        # Capteurs (rien quand on simule un capteur coupé par un attaquant)
        if phase != "capteur_muet":
            gaz = random.gauss(2400, 150) if phase == "fuite_gaz" else random.gauss(320, 10)
            donnees.append(("capteurs", {"temperature": random.gauss(24.0, 0.3), "gaz": gaz}))

        # Réseau
        if phase == "dos":
            messages_par_s = random.gauss(850, 60)
            self.connexions_refusees += random.randint(30, 60)
        else:
            messages_par_s = random.gauss(12, 2)
        donnees.append(("reseau", {"messages_par_s": max(0, messages_par_s),
                                   "connexions_refusees": self.connexions_refusees}))

        # Score de menace unifié (calculé en vrai par l'Isolation Forest de l'autre dev)
        if phase == "dos":
            score, etat, raison = random.uniform(82, 92), "ROUGE", "DoS probable"
        elif phase == "fuite_gaz":
            score, etat, raison = random.uniform(60, 68), "ORANGE", "pic de gaz"
        else:
            score, etat, raison = random.uniform(5, 15), "VERT", None
        donnees.append(("score", {"score": score, "etat": etat, "raison": raison,
                                  "anomalie_capteurs": phase == "fuite_gaz",
                                  "anomalie_reseau": phase == "dos"}))

        # Indicateurs cyber
        dernier_rejet = None
        if phase == "payload_rejete":
            self.payloads_rejetes += 1
            dernier_rejet = "capteurs : temperature hors limites (9999)"
        donnees.append(("cyber", {"canal_chiffre": "TLSv1.3",
                                  "payloads_rejetes": self.payloads_rejetes,
                                  "dernier_rejet": dernier_rejet}))

        return donnees


# ---------------------------------------------------------------------------
# Composants graphiques
# ---------------------------------------------------------------------------
class Tuile:
    """Cadre avec un titre, un signal de vie et des lignes de texte."""

    def __init__(self, parent, titre):
        self.cadre = tk.Frame(parent, bg=FOND_TUILE, padx=14, pady=10)
        entete = tk.Frame(self.cadre, bg=FOND_TUILE)
        entete.pack(fill="x")
        tk.Label(entete, text=titre, bg=FOND_TUILE, fg=TEXTE,
                 font=(POLICE, 13, "bold")).pack(side="left")
        self.vie = tk.Label(entete, text="", bg=FOND_TUILE, fg=GRIS, font=(POLICE, 10))
        self.vie.pack(side="right")

    def ligne(self, texte="", taille=11):
        label = tk.Label(self.cadre, text=texte, bg=FOND_TUILE, fg=TEXTE, anchor="w",
                         font=(POLICE, taille))
        label.pack(fill="x", pady=1)
        return label


class Courbe:
    """Mini-graphe des dernières valeurs d'une mesure."""

    def __init__(self, parent, couleur):
        self.valeurs = deque(maxlen=TAILLE_COURBES)
        self.couleur = couleur
        self.canvas = tk.Canvas(parent, height=55, bg=FOND, highlightthickness=0)
        self.canvas.pack(fill="x", pady=(0, 6))

    def ajouter(self, valeur):
        self.valeurs.append(valeur)

    def dessiner(self):
        self.canvas.delete("all")
        largeur, hauteur = self.canvas.winfo_width(), self.canvas.winfo_height()
        if len(self.valeurs) < 2 or largeur < 10:
            return
        bas, haut = min(self.valeurs), max(self.valeurs)
        if haut - bas < 1e-6:
            haut = bas + 1
        points = []
        for i, valeur in enumerate(self.valeurs):
            points.append(i * (largeur - 1) / (TAILLE_COURBES - 1))
            points.append(hauteur - 4 - (valeur - bas) / (haut - bas) * (hauteur - 8))
        self.canvas.create_line(*points, fill=self.couleur, width=2)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class Dashboard:

    def __init__(self, racine):
        self.racine = racine

        self.derniere_vue = {source: None for source in SOURCES}
        self.muette = {source: False for source in SOURCES}
        self.score = None
        self.etat_score = None
        self.raison = None
        self.anomalies = {"capteurs": False, "reseau": False}
        self.etat_vision = None
        self.inscription_vision = None  # Prénom en cours d'inscription côté vision
        self.cause_vision = None
        self.canal_chiffre = None
        self.payloads_rejetes = 0
        self.dernier_rejet = None
        self.etat_global = None
        self.debut_rouge = None
        self.acquittee = False
        self.dernier_horodatage_vision = None   # Pour ne transmettre que les nouveaux états

        DOSSIER_JOURNAUX.mkdir(exist_ok=True)
        self.fichier_journal = DOSSIER_JOURNAUX / time.strftime("journal_%Y%m%d.log")

        self.construire_interface()
        self.noter("demarrage du dashboard (vision reelle, autres sources simulees)")

        self.base = BaseDonnees()
        self.simulation = DonneesSimulees()
        racine.protocol("WM_DELETE_WINDOW", self.fermer)
        racine.after(PERIODE_LECTURE_BDD_MS, self.lire_bdd)
        racine.after(PERIODE_SIMULATION_MS, self.tick_simulation)
        racine.after(RAFRAICHISSEMENT_MS, self.rafraichir)

    # --- Interface -----------------------------------------------------------
    def construire_interface(self):
        r = self.racine
        r.title("SENTINEL-X - Centre de supervision")
        r.configure(bg=FOND, padx=12, pady=12)
        r.geometry("1280x800")
        for colonne in range(3):
            r.columnconfigure(colonne, weight=1, uniform="colonnes")
        r.rowconfigure(3, weight=1)

        # Bandeau du haut
        haut = tk.Frame(r, bg=FOND)
        haut.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        tk.Label(haut, text="SENTINEL-X // CENTRE DE SUPERVISION", bg=FOND, fg=TEXTE,
                 font=(POLICE, 16, "bold")).pack(side="left")
        # Honnêteté envers le jury : on affiche clairement ce qui est encore simulé
        tk.Label(haut, text="VISION REELLE | CAPTEURS, RESEAU, CYBER SIMULES",
                 bg=COULEURS["ORANGE"], fg="black",
                 font=(POLICE, 10, "bold"), padx=6).pack(side="left", padx=14)
        self.horloge = tk.Label(haut, bg=FOND, fg=GRIS, font=(POLICE, 14))
        self.horloge.pack(side="right")

        # 1. Score de menace, en grand
        self.cadre_score = tk.Frame(r, bg=COULEURS[None], padx=20, pady=12)
        self.cadre_score.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(0, 6), pady=(0, 12))
        self.titre_score = tk.Label(self.cadre_score, text="SCORE DE MENACE", font=(POLICE, 14, "bold"))
        self.titre_score.pack(anchor="w")
        self.valeur_score = tk.Label(self.cadre_score, text="--", font=("Segoe UI", 72, "bold"))
        self.valeur_score.pack(anchor="w")
        self.detail_score = tk.Label(self.cadre_score, text="En attente des sources...",
                                     font=(POLICE, 13), anchor="w", justify="left")
        self.detail_score.pack(anchor="w", fill="x")
        self.vie_score = tk.Label(self.cadre_score, font=(POLICE, 10))
        self.vie_score.pack(anchor="e")
        self.widgets_score = [self.cadre_score, self.titre_score, self.valeur_score,
                              self.detail_score, self.vie_score]

        # 5. Indicateurs cyber
        cyber = Tuile(r, "CYBER")
        cyber.cadre.grid(row=1, column=2, sticky="nsew", padx=(6, 0), pady=(0, 12))
        self.label_canal = cyber.ligne("Canal chiffre : --", 13)
        cyber.ligne()
        self.label_rejets = cyber.ligne("Payloads rejetes : 0", 13)
        self.label_dernier_rejet = cyber.ligne("", 10)
        self.label_dernier_rejet.config(fg=GRIS, wraplength=360, justify="left")

        # 2. Une tuile par source
        self.tuile_vision = Tuile(r, "VISION")
        self.tuile_vision.cadre.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        self.label_etat_vision = self.tuile_vision.ligne("Etat : --", 14)
        self.label_identites = self.tuile_vision.ligne("Identites : --")
        self.label_cause_vision = self.tuile_vision.ligne("Cause : --")
        self.label_confiance = self.tuile_vision.ligne("Confiance du modele : --")
        self.label_traitement = self.tuile_vision.ligne("Traitement : --")

        self.tuile_capteurs = Tuile(r, "CAPTEURS")
        self.tuile_capteurs.cadre.grid(row=2, column=1, sticky="nsew", padx=6, pady=(0, 12))
        self.label_anomalie_capteurs = self.tuile_capteurs.ligne("Anomalie : --", 14)
        self.label_temperature = self.tuile_capteurs.ligne("Temperature : --")
        self.courbe_temperature = Courbe(self.tuile_capteurs.cadre, "#4fa3e0")
        self.label_gaz = self.tuile_capteurs.ligne("Gaz : --")
        self.courbe_gaz = Courbe(self.tuile_capteurs.cadre, "#c9a227")

        self.tuile_reseau = Tuile(r, "RESEAU")
        self.tuile_reseau.cadre.grid(row=2, column=2, sticky="nsew", padx=(6, 0), pady=(0, 12))
        self.label_anomalie_reseau = self.tuile_reseau.ligne("Anomalie : --", 14)
        self.label_messages = self.tuile_reseau.ligne("Messages / s : --")
        self.courbe_messages = Courbe(self.tuile_reseau.cadre, "#4fa3e0")
        self.label_refusees = self.tuile_reseau.ligne("Connexions refusees : --")

        self.tuiles_sources = {"vision": self.tuile_vision, "capteurs": self.tuile_capteurs,
                               "reseau": self.tuile_reseau}
        self.courbes = [self.courbe_temperature, self.courbe_gaz, self.courbe_messages]

        # 3. Journal + bouton d'acquittement
        bas = tk.Frame(r, bg=FOND_TUILE, padx=14, pady=10)
        bas.grid(row=3, column=0, columnspan=3, sticky="nsew")
        entete = tk.Frame(bas, bg=FOND_TUILE)
        entete.pack(fill="x")
        tk.Label(entete, text="JOURNAL", bg=FOND_TUILE, fg=TEXTE,
                 font=(POLICE, 13, "bold")).pack(side="left")
        tk.Label(entete, text=f"enregistre dans journaux/{self.fichier_journal.name}",
                 bg=FOND_TUILE, fg=GRIS, font=(POLICE, 10)).pack(side="left", padx=12)
        self.bouton_acquitter = tk.Button(entete, text="ALERTE PRISE EN COMPTE",
                                          font=(POLICE, 12, "bold"), state="disabled",
                                          bg=COULEURS["ROUGE"], fg="white",
                                          disabledforeground=GRIS, command=self.acquitter)
        self.bouton_acquitter.pack(side="right")
        self.journal = tk.Text(bas, bg=FOND, fg=TEXTE, font=(POLICE, 11), height=8,
                               relief="flat", state="disabled")
        self.journal.pack(fill="both", expand=True, pady=(8, 0))
        for etat, couleur in COULEURS.items():
            if etat is not None:
                self.journal.tag_config(etat, foreground=couleur)

    # --- Journal -------------------------------------------------------------
    def noter(self, texte, etat=None):
        """Ajoute une ligne horodatée en haut du journal, et dans le fichier."""
        ligne = f"{time.strftime('%H:%M:%S')} — {texte}\n"
        self.journal.config(state="normal")
        self.journal.insert("1.0", ligne, etat or ())
        self.journal.delete(f"{TAILLE_JOURNAL + 1}.0", "end")
        self.journal.config(state="disabled")
        with open(self.fichier_journal, "a", encoding="utf-8") as fichier:
            fichier.write(f"{time.strftime('%Y-%m-%d')} {ligne}")

    # --- Réception des données ----------------------------------------------
    def lire_bdd(self):
        """Relit l'état de la vision écrit par main.py, et le transmet s'il est nouveau."""
        try:
            etat = self.base.lire_etat_courant("vision")
        except sqlite3.OperationalError:
            etat = None     # Base momentanément occupée : on réessaie au prochain tour
        if etat is not None:
            horodatage, details = etat
            if horodatage != self.dernier_horodatage_vision:
                self.dernier_horodatage_vision = horodatage
                # Un vieil état (main.py arrêté depuis longtemps) ne prouve pas que la vision vit
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(horodatage)).total_seconds()
                if age < SILENCE_MAX_S:
                    self.recevoir("vision", details)
        self.racine.after(PERIODE_LECTURE_BDD_MS, self.lire_bdd)

    def tick_simulation(self):
        for source, donnees in self.simulation.generer():
            self.recevoir(source, donnees)
        self.racine.after(PERIODE_SIMULATION_MS, self.tick_simulation)

    def recevoir(self, source, donnees):
        """
        POINT DE BRANCHEMENT : à appeler pour chaque donnée reçue d'une source.
        Vision : alimentée par lire_bdd() ; autres sources : par DonneesSimulees.
        Pour la vision : donnees = le champ "details" des messages de main.py
        (etat, cause, identites, similarite, traitement_ms, inscription), pas l'enveloppe.
        """
        if source in SOURCES:
            self.derniere_vue[source] = time.monotonic()
            if self.muette[source]:
                self.muette[source] = False
                self.noter(f"{SOURCES[source]} de nouveau en ligne", "VERT")

        if source == "vision":
            etat, cause = donnees["etat"], donnees["cause"]
            inscription = donnees["inscription"]
            if inscription != self.inscription_vision:
                if inscription:
                    self.noter(f"inscription en cours : {inscription} (surveillance vision suspendue)",
                               "ORANGE")
                else:
                    self.noter("fin de l'inscription, surveillance vision reprise", "VERT")
                self.inscription_vision = inscription
            if inscription:
                # Pendant une inscription, l'état de la vision est figé : on l'ignore
                etat, cause = None, None
            if etat != self.etat_vision and etat is not None and self.etat_vision is not None:
                if etat == "ROUGE":
                    self.noter(f"INTRUSION detectee par la vision ({cause})", "ROUGE")
                else:
                    self.noter(f"vision {etat}" + (f" ({cause})" if cause else ""), etat)
            self.etat_vision, self.cause_vision = etat, cause
            noms = donnees["identites"]
            if noms:
                self.label_identites.config(text="ACCES AUTORISE : " + ", ".join(noms),
                                            fg=COULEURS["VERT"])
            else:
                self.label_identites.config(text="Identites : aucune reconnue", fg=TEXTE)
            # Similarité absente quand aucun visage n'est dans le champ
            similarite = donnees["similarite"]
            self.label_confiance.config(text="Confiance du modele : "
                                        + ("--" if similarite is None else f"{similarite * 100:.0f} %"))
            self.label_traitement.config(text=f"Traitement : {donnees['traitement_ms']:.0f} ms")

        elif source == "capteurs":
            self.courbe_temperature.ajouter(donnees["temperature"])
            self.courbe_gaz.ajouter(donnees["gaz"])
            self.label_temperature.config(text=f"Temperature : {donnees['temperature']:.1f} °C")
            self.label_gaz.config(text=f"Gaz : {donnees['gaz']:.0f} ppm")

        elif source == "reseau":
            self.courbe_messages.ajouter(donnees["messages_par_s"])
            self.label_messages.config(text=f"Messages / s : {donnees['messages_par_s']:.0f}")
            self.label_refusees.config(text=f"Connexions refusees : {donnees['connexions_refusees']}")

        elif source == "score":
            self.score, self.etat_score = donnees["score"], donnees["etat"]
            self.raison = donnees.get("raison")
            for nom in ("reseau", "capteurs"):
                nouvelle = donnees[f"anomalie_{nom}"]
                if nouvelle and not self.anomalies[nom]:
                    self.noter(f"anomalie {nom}" + (f", {self.raison}" if self.raison else ""), "ROUGE")
                elif self.anomalies[nom] and not nouvelle:
                    self.noter(f"fin de l'anomalie {nom}", "VERT")
                self.anomalies[nom] = nouvelle

        elif source == "cyber":
            self.canal_chiffre = donnees["canal_chiffre"]
            if donnees["payloads_rejetes"] > self.payloads_rejetes:
                self.dernier_rejet = f"{time.strftime('%H:%M:%S')} {donnees['dernier_rejet']}"
                self.noter(f"payload rejete : {donnees['dernier_rejet']}", "ORANGE")
            self.payloads_rejetes = donnees["payloads_rejetes"]

    # --- Rafraîchissement périodique ----------------------------------------
    def rafraichir(self):
        maintenant = time.monotonic()
        self.horloge.config(text=time.strftime("%H:%M:%S"))

        # 4. Signaux de vie
        for source, nom in SOURCES.items():
            texte, couleur = self.texte_vie(source, nom, maintenant)
            if source == "score":
                self.vie_score.config(text=texte, fg="white")
            else:
                self.tuiles_sources[source].vie.config(text=texte, fg=couleur)

        # État global = le pire de toutes les sources
        muettes = [SOURCES[s] for s, muette in self.muette.items() if muette]
        etat = pire_etat(self.etat_score, self.etat_vision, "ROUGE" if muettes else None)
        if etat != self.etat_global:
            self.etat_global = etat
            if etat is not None:
                self.noter(f"etat global {etat}", etat)
            if etat == "ROUGE":
                self.debut_rouge, self.acquittee = maintenant, False

        # 1. Score en grand
        couleur = COULEURS[self.etat_global]
        for widget in self.widgets_score:
            widget.config(bg=couleur)
            if widget is not self.cadre_score:
                widget.config(fg="white")
        self.valeur_score.config(text="--" if self.score is None else f"{self.score:.0f}")
        self.detail_score.config(text=self.resume_menace(muettes))

        # 2. Tuiles
        if self.inscription_vision:
            self.label_etat_vision.config(text=f"Etat : INSCRIPTION ({self.inscription_vision})",
                                          fg="#4fa3e0")
        else:
            self.label_etat_vision.config(text=f"Etat : {self.etat_vision or '--'}",
                                          fg=COULEURS[self.etat_vision] if self.etat_vision else TEXTE)
        self.label_cause_vision.config(text=f"Cause : {self.cause_vision or 'aucune'}")
        for nom, label in (("capteurs", self.label_anomalie_capteurs),
                           ("reseau", self.label_anomalie_reseau)):
            if self.score is None:      # Pas encore de verdict de l'IA fusion
                label.config(text="Anomalie : --", fg=TEXTE)
            else:
                anomalie = self.anomalies[nom]
                label.config(text=f"Anomalie : {'OUI' if anomalie else 'non'}",
                             fg=COULEURS["ROUGE"] if anomalie else COULEURS["VERT"])
        for courbe in self.courbes:
            courbe.dessiner()

        # 5. Cyber
        if self.canal_chiffre:
            self.label_canal.config(text=f"Canal chiffre {self.canal_chiffre} ✓", fg=COULEURS["VERT"])
        else:
            self.label_canal.config(text="Canal chiffre : non etabli ✗", fg=COULEURS["ROUGE"])
        self.label_rejets.config(text=f"Payloads rejetes : {self.payloads_rejetes}",
                                 fg=COULEURS["ORANGE"] if self.payloads_rejetes else TEXTE)
        self.label_dernier_rejet.config(text=self.dernier_rejet or "")

        # Bouton d'acquittement : actif seulement pendant une alerte non prise en compte
        actif = self.etat_global == "ROUGE" and not self.acquittee
        self.bouton_acquitter.config(state="normal" if actif else "disabled")

        self.racine.after(RAFRAICHISSEMENT_MS, self.rafraichir)

    def texte_vie(self, source, nom, maintenant):
        """Texte et couleur du signal de vie. Déclare la source muette si besoin."""
        vue = self.derniere_vue[source]
        if vue is None:
            return f"{nom} : en attente...", GRIS
        age = maintenant - vue
        if age > SILENCE_MAX_S:
            if not self.muette[source]:
                self.muette[source] = True
                self.noter(f"ALERTE : {nom} muet depuis {SILENCE_MAX_S:.0f} s", "ROUGE")
            return f"{nom} MUET depuis {age:.0f} s", COULEURS["ROUGE"]
        return f"{nom} vu il y a {age:.0f} s", COULEURS["VERT"]

    def resume_menace(self, muettes):
        """Une ligne qui dit d'où vient la menace."""
        causes = []
        if self.anomalies["reseau"]:
            causes.append("reseau" + (f" : {self.raison}" if self.raison else ""))
        if self.anomalies["capteurs"]:
            causes.append("capteurs" + (f" : {self.raison}" if self.raison else ""))
        if self.etat_vision in ("ORANGE", "ROUGE"):
            causes.append(f"vision : {self.cause_vision}")
        causes += [f"{nom} muet" for nom in muettes]
        etat = self.etat_global or "--"
        return f"ETAT GLOBAL : {etat}" + (f"   |   {' / '.join(causes)}" if causes else "")

    # --- Actions -------------------------------------------------------------
    def acquitter(self):
        delai = time.monotonic() - self.debut_rouge
        self.acquittee = True
        self.noter(f"alerte prise en compte par l'operateur ({delai:.0f} s apres le passage au rouge)")

    def fermer(self):
        self.base.fermer()
        self.racine.destroy()


if __name__ == "__main__":
    racine = tk.Tk()
    Dashboard(racine)
    racine.mainloop()
