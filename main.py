"""
SENTINEL-X — Module Vision Intelligente

Analyse la webcam en temps réel : un visage inconnu, caché ou absent
déclenche la machine à états VERT / ORANGE / ROUGE puis une alerte.
L'IA (détection + reconnaissance faciale) vit dans facial_recognition.py.
"""

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import cv2

import facial_recognition as ia
from base_donnees import BaseDonnees

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NODE_ID = "sentinel-x-vision-01"   # Identifiant du boîtier dans les messages envoyés
CAMERA_INDEX = 0                   # Webcam intégrée
LARGEUR, HAUTEUR = 640, 480        # Résolution imposée par le cahier des charges
SEUIL_TRAITEMENT_MS = 100          # Budget maximum de traitement par trame
LISSAGE = 0.9                      # Lissage des mesures (0 = brut, proche de 1 = très lisse)
NOM_FENETRE = "SENTINEL-X - Vision"

# Machine à états
DELAI_ALERTE_S = 3.0               # Menace continue avant de passer au ROUGE
DELAI_RETOUR_VERT_S = 0.5          # La situation doit rester normale ce temps-là pour revenir au VERT
DELAI_ORANGE_S = 0.3               # Menace continue avant de passer à l'ORANGE (évite le clignotement
                                   # sur une trame isolée sans visage ; le chrono ROUGE part quand même
                                   # de la première trame suspecte)
SEUIL_CONFIANCE = 0.80             # Réservé au futur modèle d'obstruction
                                   # (le seuil de reconnaissance est réglé dans facial_recognition.py)

# Simulation au clavier, pour tester sans webcam ni modèle
MODE_SIMULATION = False

# État envoyé régulièrement même sans changement : sinon le dashboard croirait
# la vision tombée (il déclare une source muette après 5 s de silence)
PERIODE_ETAT_S = 2.0

# Photo enregistrée au passage en ROUGE : reste sur ce PC, ne part jamais sur le réseau
DOSSIER_CAPTURES = Path(__file__).parent / "captures"

# Inscription d'une personne (uniquement avec : python main.py --inscription)
NB_PHOTOS_INSCRIPTION = 5          # Une empreinte par photo : plusieurs poses = reconnaissance plus fiable
PAUSE_ENTRE_PHOTOS_S = 3.0         # Le temps de changer de pose (aussi avant la 1re : on quitte le clavier)
INTERVALLE_REESSAI_S = 0.7         # Après une photo refusée, on réessaie vite
# Garde-fous validés par le binôme IA sur nos photos (0 refus à tort) :
SEUIL_COHERENCE = 0.50             # La photo doit ressembler aux photos déjà inscrites de la personne
SEUIL_COLLISION = 0.45             # ...et pas trop à celles des autres (reconnaissance à 0,50)
CONSIGNES_INSCRIPTION = [          # Sans accents : affichées par OpenCV
    "Regardez la camera",
    "Tournez legerement la tete a gauche",
    "Tournez legerement la tete a droite",
    "Levez legerement le menton",
    "Baissez legerement le menton",
]

# Priorité Windows "au-dessus de la normale" : sur un PC chargé, le pire temps de
# traitement passe de ~226 ms à ~61 ms (mesure du binôme IA). Pas "haute" ni
# "temps réel" : elles pourraient bloquer le reste du système.
PRIORITE_AU_DESSUS_NORMALE = True

COULEUR_VERT = (0, 200, 0)         # Couleurs OpenCV en BGR
COULEUR_ORANGE = (0, 165, 255)
COULEUR_ROUGE = (0, 0, 255)
COULEUR_BLANC = (255, 255, 255)
COULEUR_NOIR = (0, 0, 0)
COULEUR_BLEU = (230, 140, 0)

# Message affiché au bandeau ORANGE / ROUGE selon la cause (sans accents pour OpenCV)
MESSAGES_CAUSE = {
    "aucun_visage": "Aucun visage detecte",
    "visage_inconnu": "Visage non reconnu",
    "obstruction": "Veuillez degager votre visage",
}


# ---------------------------------------------------------------------------
# Intelligence artificielle (module du binôme IA)
# ---------------------------------------------------------------------------
def analyser_trame(frame):
    """Délègue au module IA. Voir docs/NOTES_POUR_DEV.md pour le format."""
    return ia.analyze_frame(frame)


def simuler_visages(inconnu, aucun_visage):
    """Faux résultat de l'IA, piloté au clavier."""
    if aucun_visage:
        return []
    box = (LARGEUR // 2 - 80, HAUTEUR // 2 - 100, 160, 200)
    # 0.21 et 0.88 : similarités réellement mesurées par le binôme IA
    return [{
        "box": box,
        "identity": None if inconnu else "simulation",
        "similarity": 0.21 if inconnu else 0.88,
        "obstructed": False,
        "confidence": 0.0,
    }]


# ---------------------------------------------------------------------------
# Réseau
# ---------------------------------------------------------------------------
def send_alert(type_alerte, details):
    """
    Construit le message JSON envoyé au reste du système.
    Jour 3 : seul le print sera remplacé par client.publish(...) en MQTTS.
    """
    message = {
        "node_id": NODE_ID,
        "type": type_alerte,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": details,
    }
    print(json.dumps(message, ensure_ascii=False))


def enregistrer_capture(frame):
    """Photo locale au passage en ROUGE : la preuve de l'intrusion."""
    DOSSIER_CAPTURES.mkdir(exist_ok=True)
    chemin = DOSSIER_CAPTURES / time.strftime("intrusion_%Y%m%d_%H%M%S.jpg")
    cv2.imwrite(str(chemin), frame)
    print(f"Capture enregistrée : {chemin}")
    return chemin


# ---------------------------------------------------------------------------
# Machine à états
# ---------------------------------------------------------------------------
def cause_menace(visages):
    """
    Décide si la trame est suspecte et pourquoi. Renvoie None si tout va bien.
    Règle stricte : un seul visage suspect suffit, même à côté d'une personne autorisée.
    """
    if not visages:
        return "aucun_visage"      # Dos tourné, hors champ
    for visage in visages:
        # Toujours faux pour l'instant : prêt pour le futur modèle d'obstruction
        if visage["obstructed"] and visage["confidence"] >= SEUIL_CONFIANCE:
            return "obstruction"
        if visage["identity"] is None:
            return "visage_inconnu"
    return None


def identites(visages):
    """Noms des personnes reconnues dans le champ."""
    return [visage["identity"] for visage in visages if visage["identity"] is not None]


class MachineEtats:
    """
    VERT   : visage reconnu, chrono à zéro.
    ORANGE : situation suspecte (inconnu, caché ou absent), chrono lancé.
    ROUGE  : situation suspecte depuis plus de DELAI_ALERTE_S secondes.
    """

    def __init__(self, base=None):
        self.base = base                # Historique des changements d'état (optionnel)
        self.dernier_evenement_id = None
        self.etat = "VERT"
        self.cause = None               # Dernière cause de menace vue
        self.identites = []             # Personnes reconnues sur la dernière trame
        self.debut_menace = None        # Quand le chrono a démarré (première trame suspecte)
        self.derniere_menace = None     # Dernière trame suspecte vue
        self.inscription = None         # Prénom en cours d'inscription : surveillance suspendue
        self.similarite = None          # Du visage le plus proche, sur la dernière trame
        self.traitement_ms = 0.0        # Temps de traitement lissé

    def duree_menace(self):
        """Durée du chrono en secondes (0 si on est au VERT)."""
        if self.debut_menace is None:
            return 0.0
        return time.monotonic() - self.debut_menace

    def mettre_a_jour(self, cause, identites, similarite, traitement_ms):
        """Fait avancer la machine d'une trame. Envoie un message si l'état change."""
        maintenant = time.monotonic()
        ancien_etat = self.etat
        self.identites = identites
        self.similarite = similarite
        self.traitement_ms = traitement_ms

        if cause is not None:
            self.derniere_menace = maintenant
            if self.debut_menace is None:
                self.debut_menace = maintenant
            if self.etat == "VERT" and self.duree_menace() >= DELAI_ORANGE_S:
                self.etat = "ORANGE"
            elif self.etat == "ORANGE" and self.duree_menace() >= DELAI_ALERTE_S:
                self.etat = "ROUGE"
            if self.etat != "VERT":
                self.cause = cause
        elif self.etat == "VERT":
            # Menace trop brève pour passer à l'ORANGE : on l'oublie
            self.debut_menace = None
        else:
            # Tolérance : quelques trames "tout va bien" ne suffisent pas à tout annuler,
            # il faut que la situation reste normale pendant DELAI_RETOUR_VERT_S.
            if maintenant - self.derniere_menace >= DELAI_RETOUR_VERT_S:
                self.etat = "VERT"
                self.cause = None
                self.debut_menace = None

        if self.etat != ancien_etat:
            self.publier_changement(ancien_etat)

    def details(self):
        """Contenu commun à tous les messages de la vision (lu tel quel par le dashboard)."""
        return {
            "etat": self.etat,
            "cause": self.cause,
            "identites": self.identites,
            "similarite": None if self.similarite is None else round(self.similarite, 2),
            "traitement_ms": round(self.traitement_ms, 1),
            "duree_menace_s": round(self.duree_menace(), 1),
            # Pendant une inscription, l'état est figé : le dashboard ne doit pas en tenir compte
            "inscription": self.inscription,
        }

    def reinitialiser(self):
        """Repart au VERT après une inscription : l'ancien chrono n'a plus de sens."""
        ancien_etat = self.etat
        self.etat = "VERT"
        self.cause = None
        self.debut_menace = None
        self.derniere_menace = None
        if ancien_etat != "VERT":
            self.publier_changement(ancien_etat)

    def publier_changement(self, ancien_etat):
        type_alerte = "ALERTE_INTRUSION" if self.etat == "ROUGE" else "CHANGEMENT_ETAT"
        details = {**self.details(), "etat_precedent": ancien_etat}
        send_alert(type_alerte, details)
        # Seuls les changements vont en base : l'état périodique n'y apporterait que du bruit
        if self.base is not None:
            self.dernier_evenement_id = self.base.enregistrer_evenement("vision", type_alerte, details)

    def publier_etat_periodique(self):
        """Compte rendu régulier, distinct d'un changement : prouve que la vision tourne."""
        send_alert("ETAT_PERIODIQUE", self.details())


# ---------------------------------------------------------------------------
# Inscription d'une personne autorisée
# ---------------------------------------------------------------------------
def terminer_inscription(machine):
    """Reprend la surveillance après une inscription (terminée ou interrompue)."""
    machine.inscription = None
    machine.reinitialiser()
    machine.publier_etat_periodique()


def demander_prenom(base):
    """
    Demande le prénom dans le terminal (la vidéo est figée pendant la saisie).
    Renvoie le prénom à inscrire, ou None si on annule.
    """
    prenom = input("Prénom de la personne à inscrire (Entrée vide = annuler) : ").strip()
    if not prenom:
        print("Inscription annulée.")
        return None

    deja_connue = {p["prenom"]: p for p in base.personnes()}.get(prenom)
    if deja_connue is None:
        try:
            base.ajouter_personne(prenom)
        except ValueError as erreur:
            print(f"Inscription refusée : {erreur}")
            return None
        print(f"{prenom} ajouté(e) à la base.")
    elif not deja_connue["actif"]:
        # Une personne révoquée ne revient pas par la petite porte
        print(f"Inscription refusée : l'accès de {prenom} a été révoqué.")
        return None
    else:
        print(f"{prenom} est déjà inscrit(e) : ajout de nouvelles photos.")
    return prenom


def charger_autorises(base):
    """
    Transmet au module IA la liste des personnes autorisées, lue dans la base.
    Tant que la base est vide (ou que le module IA n'a pas set_references),
    on garde l'ancien dossier autorises/ : personne ne perd sa reconnaissance.
    """
    references = base.charger_empreintes(modele=ia.MODEL_NAME)
    if references and hasattr(ia, "set_references"):
        ia.set_references(references)
        noms = sorted({nom for nom, _ in references})
        print(f"Autorisés (base) : {len(references)} empreinte(s) pour {', '.join(noms)}")
    else:
        raison = "base vide" if not references else "set_references() absente du module IA"
        print(f"Autorisés (dossier autorises/, {raison}) : {ia.reload_references()}")

    # Empreintes d'un ancien modèle : inutilisables, à refaire avec --inscription
    perimees = base.compter_empreintes_autre_modele(modele=ia.MODEL_NAME)
    if perimees:
        print(f"Attention : {perimees} empreinte(s) calculée(s) avec un autre modèle, ignorée(s).")


def verifier_empreinte(base, prenom, empreinte):
    """
    Garde-fous contre le mélange d'identités. Renvoie None si la photo est
    acceptable, sinon la raison du refus (sans accents : affichée par OpenCV).
    """
    references = base.charger_empreintes(modele=ia.MODEL_NAME)

    # 1. Si la personne a déjà des photos, la nouvelle doit lui ressembler.
    #    On prend le maximum : deux poses très différentes peuvent se ressembler peu.
    siennes = [e for nom, e in references if nom == prenom]
    if siennes:
        coherence = max(ia.compare(empreinte, e) for e in siennes)
        if coherence < SEUIL_COHERENCE:
            return f"ne ressemble pas aux photos de {prenom} ({coherence:.2f})"

    # 2. Elle ne doit pas trop ressembler à quelqu'un d'autre : sinon, en direct,
    #    l'un pourrait être reconnu à la place de l'autre.
    autres = [(nom, e) for nom, e in references if nom != prenom]
    if autres:
        proche, score = max(((nom, ia.compare(empreinte, e)) for nom, e in autres),
                            key=lambda couple: couple[1])
        if score >= SEUIL_COLLISION:
            return f"ressemble trop a {proche} ({score:.2f})"
    return None


class Inscription:
    """
    Prend NB_PHOTOS_INSCRIPTION photos automatiquement, avec une courte pause
    entre chacune pour changer de pose, et enregistre une empreinte par photo
    dans la base. Une photo refusée ne compte pas : on la retente.
    """

    def __init__(self, base, prenom):
        self.base = base
        self.prenom = prenom
        self.nb_photos = 0
        self.prochaine_tentative = time.monotonic() + PAUSE_ENTRE_PHOTOS_S
        self.probleme = None            # Raison du dernier refus, affichée à l'écran

    def termine(self):
        return self.nb_photos >= NB_PHOTOS_INSCRIPTION

    def consigne(self):
        return CONSIGNES_INSCRIPTION[self.nb_photos % len(CONSIGNES_INSCRIPTION)]

    def traiter(self, frame, visages):
        """À appeler à chaque trame, sur l'image BRUTE (avant tout dessin)."""
        if len(visages) != 1 or time.monotonic() < self.prochaine_tentative:
            return
        # Par défaut, une tentative refusée est retentée vite
        self.prochaine_tentative = time.monotonic() + INTERVALLE_REESSAI_S

        # La reconnaissance en direct sait déjà qui est là, quelle que soit sa source
        # (base ou dossier) : on n'inscrit pas un visage connu sous un autre prénom
        identite = visages[0]["identity"]
        if identite is not None and identite != self.prenom:
            self.probleme = f"visage deja reconnu : {identite}"
            return
        if visages[0]["obstructed"]:
            self.probleme = "visage obstrue"    # Empoisonnerait la base
            return
        # Toujours passer par le module IA : une empreinte calculée autrement
        # (sans son alignement) serait inutilisable, sans aucune erreur visible
        empreinte, probleme = ia.reference_embedding(frame)
        if probleme is None:
            probleme = verifier_empreinte(self.base, self.prenom, empreinte)
        if probleme is not None:
            self.probleme = probleme
            return

        self.base.ajouter_empreinte(self.prenom, empreinte, modele=ia.MODEL_NAME)
        self.nb_photos += 1
        self.probleme = None
        self.prochaine_tentative = time.monotonic() + PAUSE_ENTRE_PHOTOS_S
        print(f"Photo {self.nb_photos}/{NB_PHOTOS_INSCRIPTION} enregistrée pour {self.prenom}.")


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def dessiner_visages(frame, visages):
    """Encadre chaque visage : vert s'il est autorisé, rouge sinon."""
    for visage in visages:
        x, y, w, h = visage["box"]
        autorise = visage["identity"] is not None
        couleur = COULEUR_VERT if autorise else COULEUR_ROUGE
        cv2.rectangle(frame, (x, y), (x + w, y + h), couleur, 2)
        texte = (f"{visage['identity']} {visage['similarity'] * 100:.0f} %"
                 if autorise else "INCONNU")
        cv2.putText(frame, texte, (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, couleur, 2)


def dessiner_etat(frame, machine):
    """Bandeau en bas de l'image avec la couleur de l'état et le message."""
    if machine.etat == "VERT":
        couleur = COULEUR_VERT
        message = "ACCES AUTORISE : " + ", ".join(machine.identites)
    elif machine.etat == "ORANGE":
        couleur = COULEUR_ORANGE
        message = f"{MESSAGES_CAUSE[machine.cause]} ({machine.duree_menace():.1f} s)"
    else:
        couleur = COULEUR_ROUGE
        message = f"ALERTE INTRUSION ({machine.duree_menace():.1f} s)"

    # OpenCV n'affiche pas les accents : messages à l'écran sans accents
    cv2.rectangle(frame, (0, HAUTEUR - 50), (LARGEUR, HAUTEUR), couleur, -1)
    cv2.putText(frame, message, (15, HAUTEUR - 17),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, COULEUR_NOIR, 2)
    # Cadre autour de l'image, bien visible pendant la démo
    cv2.rectangle(frame, (0, 0), (LARGEUR - 1, HAUTEUR - 1), couleur, 6)


def dessiner_inscription(frame, inscription, visages):
    """Bandeau bleu à la place du bandeau d'état : la surveillance est suspendue."""
    if not visages:
        message = "Placez-vous face a la camera"
    elif len(visages) > 1:
        message = "Une seule personne dans le cadre"
    elif inscription.probleme:
        message = f"Refusee : {inscription.probleme}"
    else:
        message = f"Photo {inscription.nb_photos + 1}/{NB_PHOTOS_INSCRIPTION} : {inscription.consigne()}"
    cv2.rectangle(frame, (0, HAUTEUR - 75), (LARGEUR, HAUTEUR), COULEUR_BLEU, -1)
    cv2.putText(frame, f"MODE INSCRIPTION : {inscription.prenom}", (15, HAUTEUR - 47),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, COULEUR_BLANC, 2)
    cv2.putText(frame, message, (15, HAUTEUR - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, COULEUR_BLANC, 2)
    cv2.rectangle(frame, (0, 0), (LARGEUR - 1, HAUTEUR - 1), COULEUR_BLEU, 6)


def dessiner_performance(frame, traitement_ms, fps):
    """Incruste le temps de traitement (vert si < 100 ms, rouge sinon), le FPS et le détail IA."""
    couleur = COULEUR_VERT if traitement_ms < SEUIL_TRAITEMENT_MS else COULEUR_ROUGE
    cv2.putText(frame, f"Traitement : {traitement_ms:.1f} ms", (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, couleur, 2)
    cv2.putText(frame, f"FPS : {fps:.1f}", (15, 55),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COULEUR_BLANC, 2)
    if not MODE_SIMULATION:
        cv2.putText(frame, f"detection {ia.detection_ms:.1f} ms | IA {ia.recognition_ms:.1f} ms",
                    (15, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COULEUR_BLANC, 1)


def dessiner_aide_simulation(frame, inconnu, aucun_visage):
    """Rappel des touches du simulateur, en haut à droite."""
    lignes = [
        f"[i] inconnu : {'OUI' if inconnu else 'non'}",
        f"[n] aucun visage : {'OUI' if aucun_visage else 'non'}",
    ]
    for i, ligne in enumerate(lignes):
        cv2.putText(frame, ligne, (LARGEUR - 250, 30 + i * 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COULEUR_BLANC, 2)


def lisser(ancienne, nouvelle):
    """Moyenne glissante exponentielle : évite que les chiffres clignotent."""
    if ancienne is None:
        return nouvelle
    return LISSAGE * ancienne + (1 - LISSAGE) * nouvelle


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------
def augmenter_priorite():
    """Passe ce processus en priorité "au-dessus de la normale" (Windows uniquement)."""
    if not PRIORITE_AU_DESSUS_NORMALE or sys.platform != "win32":
        return
    ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
    kernel32 = ctypes.windll.kernel32
    # Types à déclarer : sinon ctypes tronque la poignée du processus en 32 bits
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    if kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS):
        print("Priorité du processus : au-dessus de la normale.")
    else:
        print("Impossible de changer la priorité : on continue en priorité normale.")


def main():
    parser = argparse.ArgumentParser(description="SENTINEL-X - module vision")
    parser.add_argument("--inscription", action="store_true",
                        help="autorise l'inscription de nouvelles personnes (touche e)")
    args = parser.parse_args()

    # L'inscription n'existe que si on relance le script exprès : en surveillance
    # normale, un intrus devant la caméra ne peut pas s'inscrire lui-même.
    inscription_possible = args.inscription and not MODE_SIMULATION

    augmenter_priorite()
    # Chargement de l'IA AVANT la boucle (~0,4 s) : sinon la première trame
    # mesurerait ~400 ms et l'incrustation passerait au rouge pendant la démo.
    base = BaseDonnees()
    if not MODE_SIMULATION:
        print(ia.initialize())
        charger_autorises(base)

    # CAP_DSHOW : backend Windows, ouverture de la webcam beaucoup plus rapide
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Erreur : impossible d'ouvrir la webcam.")
        base.fermer()
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEUR)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HAUTEUR)
    cv2.namedWindow(NOM_FENETRE)

    machine = MachineEtats(base)
    inscription = None      # Inscription en cours, sinon None
    inconnu = False         # Simulation : touche 'i'
    aucun_visage = False    # Simulation : touche 'n'

    traitement_ms = None
    fps = None
    dernier_etat_periodique = time.monotonic()
    instant_precedent = time.perf_counter()

    print("SENTINEL-X démarré. Appuyez sur 'q' pour quitter.")
    if MODE_SIMULATION:
        print("Mode simulation : 'i' = inconnu, 'n' = aucun visage.")
    else:
        print("Touche 'r' : recharger les visages autorisés.")
    if inscription_possible:
        print("MODE INSCRIPTION ACTIF : touche 'e' pour inscrire une personne, Echap pour annuler.")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Erreur : lecture de la webcam impossible.")
                break

            # --- Début de la mesure du traitement ---
            debut = time.perf_counter()

            # La caméra peut ignorer la résolution demandée : on force 640x480
            if frame.shape[1] != LARGEUR or frame.shape[0] != HAUTEUR:
                frame = cv2.resize(frame, (LARGEUR, HAUTEUR))

            # Analyse AVANT tout dessin : l'IA lit les pixels du visage
            if MODE_SIMULATION:
                visages = simuler_visages(inconnu, aucun_visage)
            else:
                visages = analyser_trame(frame)

            if inscription is not None:
                # Surveillance suspendue : la personne, encore inconnue, ne doit pas
                # déclencher une alerte d'intrusion pendant sa propre inscription
                inscription.traiter(frame, visages)
                if inscription.termine():
                    base.enregistrer_evenement("vision", "INSCRIPTION", {
                        "prenom": inscription.prenom, "nb_photos": inscription.nb_photos})
                    print(f"Inscription de {inscription.prenom} terminée.")
                    charger_autorises(base)
                    inscription = None
                    terminer_inscription(machine)
            else:
                ancien_etat = machine.etat
                # Visages triés par taille : le premier est le plus proche de la caméra
                similarite = visages[0]["similarity"] if visages else None
                machine.mettre_a_jour(cause_menace(visages), identites(visages),
                                      similarite, traitement_ms or 0.0)
                # Photo AVANT les dessins : on garde l'image brute comme preuve
                if machine.etat == "ROUGE" and ancien_etat != "ROUGE":
                    chemin = enregistrer_capture(frame)
                    # Relie la photo à l'alerte ROUGE qui vient d'être enregistrée
                    base.enregistrer_capture(chemin, machine.dernier_evenement_id)

            dessiner_visages(frame, visages)
            if inscription is not None:
                dessiner_inscription(frame, inscription, visages)
            else:
                dessiner_etat(frame, machine)

            # --- Fin de la mesure (avant l'affichage) ---
            fin = time.perf_counter()
            traitement_ms = lisser(traitement_ms, (fin - debut) * 1000)

            # FPS = intervalle entre deux trames (dépend surtout de la caméra)
            intervalle = fin - instant_precedent
            instant_precedent = fin
            if intervalle > 0:
                fps = lisser(fps, 1 / intervalle)

            if time.monotonic() - dernier_etat_periodique >= PERIODE_ETAT_S:
                machine.publier_etat_periodique()
                dernier_etat_periodique = time.monotonic()

            dessiner_performance(frame, traitement_ms, fps or 0)
            if MODE_SIMULATION:
                dessiner_aide_simulation(frame, inconnu, aucun_visage)
            cv2.imshow(NOM_FENETRE, frame)

            # Clavier : 'q' pour quitter, 'r' pour recharger, 'i' et 'n' pour la simulation
            touche = cv2.waitKey(1) & 0xFF
            if touche == ord("q"):
                break
            if not MODE_SIMULATION and touche == ord("r"):
                charger_autorises(base)
            if MODE_SIMULATION and touche == ord("i"):
                inconnu = not inconnu
            if MODE_SIMULATION and touche == ord("n"):
                aucun_visage = not aucun_visage
            if inscription_possible and inscription is None and touche == ord("e"):
                # Prévenir à l'écran avant de figer la vidéo pour la saisie
                cv2.putText(frame, "Saisissez le prenom dans le terminal", (15, 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, COULEUR_BLEU, 2)
                cv2.imshow(NOM_FENETRE, frame)
                cv2.waitKey(1)
                prenom = demander_prenom(base)
                if prenom is not None:
                    inscription = Inscription(base, prenom)
                    machine.inscription = prenom
                    machine.publier_etat_periodique()   # Prévenir le dashboard tout de suite
            if inscription is not None and touche == 27:      # Echap
                print(f"Inscription de {inscription.prenom} interrompue "
                      f"({inscription.nb_photos} photo(s) déjà enregistrée(s)).")
                charger_autorises(base)
                inscription = None
                terminer_inscription(machine)

            # Sortie aussi par la croix de la fenêtre
            if cv2.getWindowProperty(NOM_FENETRE, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        # Toujours libérer la caméra, même en cas d'erreur
        cap.release()
        base.fermer()
        cv2.destroyAllWindows()
        print("SENTINEL-X arrêté proprement.")


if __name__ == "__main__":
    main()
