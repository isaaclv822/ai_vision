"""
SENTINEL-X — Module Vision Intelligente

Analyse la webcam en temps réel : un visage inconnu, caché ou absent
déclenche la machine à états VERT / ORANGE / ROUGE puis une alerte.
L'IA (détection + reconnaissance faciale) vit dans facial_recognition.py.
"""

import json
import time
from datetime import datetime, timezone

import cv2

import facial_recognition as ia

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
SEUIL_CONFIANCE = 0.80             # Réservé au futur modèle d'obstruction
                                   # (le seuil de reconnaissance est réglé dans facial_recognition.py)

# Simulation au clavier, pour tester sans webcam ni modèle
MODE_SIMULATION = False

COULEUR_VERT = (0, 200, 0)         # Couleurs OpenCV en BGR
COULEUR_ORANGE = (0, 165, 255)
COULEUR_ROUGE = (0, 0, 255)
COULEUR_BLANC = (255, 255, 255)
COULEUR_NOIR = (0, 0, 0)

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

    def __init__(self):
        self.etat = "VERT"
        self.cause = None               # Dernière cause de menace vue
        self.identites = []             # Personnes reconnues sur la dernière trame
        self.debut_menace = None        # Quand le chrono a démarré
        self.derniere_menace = None     # Dernière trame suspecte vue

    def duree_menace(self):
        """Durée du chrono en secondes (0 si on est au VERT)."""
        if self.debut_menace is None:
            return 0.0
        return time.monotonic() - self.debut_menace

    def mettre_a_jour(self, cause, identites):
        """Fait avancer la machine d'une trame. Envoie un message si l'état change."""
        maintenant = time.monotonic()
        ancien_etat = self.etat
        self.identites = identites

        if cause is not None:
            self.cause = cause
            self.derniere_menace = maintenant
            if self.etat == "VERT":
                self.etat = "ORANGE"
                self.debut_menace = maintenant
            elif self.etat == "ORANGE" and self.duree_menace() >= DELAI_ALERTE_S:
                self.etat = "ROUGE"
        elif self.etat != "VERT":
            # Tolérance : quelques trames "tout va bien" ne suffisent pas à tout annuler,
            # il faut que la situation reste normale pendant DELAI_RETOUR_VERT_S.
            if maintenant - self.derniere_menace >= DELAI_RETOUR_VERT_S:
                self.etat = "VERT"
                self.cause = None
                self.debut_menace = None

        if self.etat != ancien_etat:
            self.publier_changement(ancien_etat)

    def publier_changement(self, ancien_etat):
        type_alerte = "ALERTE_INTRUSION" if self.etat == "ROUGE" else "CHANGEMENT_ETAT"
        send_alert(type_alerte, {
            "etat": self.etat,
            "etat_precedent": ancien_etat,
            "cause": self.cause,
            "identites": self.identites,
            "duree_menace_s": round(self.duree_menace(), 1),
        })


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
def main():
    # Chargement de l'IA AVANT la boucle (~0,4 s) : sinon la première trame
    # mesurerait ~400 ms et l'incrustation passerait au rouge pendant la démo.
    if not MODE_SIMULATION:
        print(ia.initialize())

    # CAP_DSHOW : backend Windows, ouverture de la webcam beaucoup plus rapide
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Erreur : impossible d'ouvrir la webcam.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEUR)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HAUTEUR)
    cv2.namedWindow(NOM_FENETRE)

    machine = MachineEtats()
    inconnu = False         # Simulation : touche 'i'
    aucun_visage = False    # Simulation : touche 'n'

    traitement_ms = None
    fps = None
    instant_precedent = time.perf_counter()

    print("SENTINEL-X démarré. Appuyez sur 'q' pour quitter.")
    if MODE_SIMULATION:
        print("Mode simulation : 'i' = inconnu, 'n' = aucun visage.")
    else:
        print("Touche 'r' : recharger les visages autorisés.")

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

            machine.mettre_a_jour(cause_menace(visages), identites(visages))

            dessiner_visages(frame, visages)
            dessiner_etat(frame, machine)

            # --- Fin de la mesure (avant l'affichage) ---
            fin = time.perf_counter()
            traitement_ms = lisser(traitement_ms, (fin - debut) * 1000)

            # FPS = intervalle entre deux trames (dépend surtout de la caméra)
            intervalle = fin - instant_precedent
            instant_precedent = fin
            if intervalle > 0:
                fps = lisser(fps, 1 / intervalle)

            dessiner_performance(frame, traitement_ms, fps or 0)
            if MODE_SIMULATION:
                dessiner_aide_simulation(frame, inconnu, aucun_visage)
            cv2.imshow(NOM_FENETRE, frame)

            # Clavier : 'q' pour quitter, 'r' pour recharger, 'i' et 'n' pour la simulation
            touche = cv2.waitKey(1) & 0xFF
            if touche == ord("q"):
                break
            if not MODE_SIMULATION and touche == ord("r"):
                print(ia.reload_references())
            if MODE_SIMULATION and touche == ord("i"):
                inconnu = not inconnu
            if MODE_SIMULATION and touche == ord("n"):
                aucun_visage = not aucun_visage

            # Sortie aussi par la croix de la fenêtre
            if cv2.getWindowProperty(NOM_FENETRE, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        # Toujours libérer la caméra, même en cas d'erreur
        cap.release()
        cv2.destroyAllWindows()
        print("SENTINEL-X arrêté proprement.")


if __name__ == "__main__":
    main()
