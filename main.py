"""
SENTINEL-X — Module Vision Intelligente

Analyse la webcam en temps réel pour détecter un visage obstrué.
Étape 2 : machine à états VERT / ORANGE / ROUGE, testable au clavier
(l'IA n'est pas encore branchée).
"""

import json
import time
from datetime import datetime, timezone

import cv2

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
DELAI_ALERTE_S = 3.0               # Obstruction continue avant de passer au ROUGE
DELAI_RETOUR_VERT_S = 0.5          # Le visage doit rester découvert ce temps-là pour revenir au VERT
SEUIL_CONFIANCE = 0.80             # En dessous, on ne croit pas le modèle quand il dit "obstrué"

# Simulation au clavier, en attendant le modèle IA
MODE_SIMULATION = True             # Mettre à False quand analyser_trame() sera branchée

COULEUR_VERT = (0, 200, 0)         # Couleurs OpenCV en BGR
COULEUR_ORANGE = (0, 165, 255)
COULEUR_ROUGE = (0, 0, 255)
COULEUR_BLANC = (255, 255, 255)
COULEUR_NOIR = (0, 0, 0)


# ---------------------------------------------------------------------------
# Intelligence artificielle (point de branchement pour le binôme IA)
# ---------------------------------------------------------------------------
def analyser_trame(frame):
    """
    Analyse une image et renvoie la liste des visages détectés.

    Format attendu :
        [{"box": (x, y, w, h), "obstrue": True, "confiance": 0.92}]

    Pour l'instant : aucun modèle, on renvoie une liste vide.
    """
    return []


def simuler_visages(masque_actif, aucun_visage):
    """Faux résultat de l'IA, piloté au clavier : un visage au centre de l'image."""
    if aucun_visage:
        return []
    box = (LARGEUR // 2 - 80, HAUTEUR // 2 - 100, 160, 200)
    return [{"box": box, "obstrue": masque_actif, "confiance": 0.95}]


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
def visage_menace(visages):
    """
    Décide si la trame est suspecte :
    - aucun visage visible (dos tourné, visage hors champ) -> suspect
    - un visage obstrué avec une confiance suffisante      -> suspect
    """
    if not visages:
        return True
    for visage in visages:
        if visage["obstrue"] and visage["confiance"] >= SEUIL_CONFIANCE:
            return True
    return False


class MachineEtats:
    """
    VERT   : visage découvert, chrono à zéro.
    ORANGE : visage caché, chrono lancé.
    ROUGE  : visage caché depuis plus de DELAI_ALERTE_S secondes.
    """

    def __init__(self):
        self.etat = "VERT"
        self.debut_obstruction = None   # Quand le chrono a démarré
        self.derniere_menace = None     # Dernière trame suspecte vue

    def duree_obstruction(self):
        """Durée du chrono en secondes (0 si on est au VERT)."""
        if self.debut_obstruction is None:
            return 0.0
        return time.monotonic() - self.debut_obstruction

    def mettre_a_jour(self, menace):
        """Fait avancer la machine d'une trame. Envoie un message si l'état change."""
        maintenant = time.monotonic()
        ancien_etat = self.etat

        if menace:
            self.derniere_menace = maintenant
            if self.etat == "VERT":
                self.etat = "ORANGE"
                self.debut_obstruction = maintenant
            elif self.etat == "ORANGE" and self.duree_obstruction() >= DELAI_ALERTE_S:
                self.etat = "ROUGE"
        elif self.etat != "VERT":
            # Tolérance : quelques trames "visage OK" ne suffisent pas à tout annuler,
            # il faut que le visage reste découvert pendant DELAI_RETOUR_VERT_S.
            if maintenant - self.derniere_menace >= DELAI_RETOUR_VERT_S:
                self.etat = "VERT"
                self.debut_obstruction = None

        if self.etat != ancien_etat:
            self.publier_changement(ancien_etat)

    def publier_changement(self, ancien_etat):
        type_alerte = "ALERTE_INTRUSION" if self.etat == "ROUGE" else "CHANGEMENT_ETAT"
        send_alert(type_alerte, {
            "etat": self.etat,
            "etat_precedent": ancien_etat,
            "duree_obstruction_s": round(self.duree_obstruction(), 1),
        })


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def dessiner_visages(frame, visages):
    """Encadre chaque visage : rouge s'il est obstrué, vert sinon."""
    for visage in visages:
        x, y, w, h = visage["box"]
        couleur = COULEUR_ROUGE if visage["obstrue"] else COULEUR_VERT
        cv2.rectangle(frame, (x, y), (x + w, y + h), couleur, 2)
        texte = f"{visage['confiance'] * 100:.0f} %"
        cv2.putText(frame, texte, (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, couleur, 2)


def dessiner_etat(frame, machine):
    """Bandeau en bas de l'image avec la couleur de l'état et le message."""
    if machine.etat == "VERT":
        couleur, message = COULEUR_VERT, "ACCES AUTORISE"
    elif machine.etat == "ORANGE":
        couleur = COULEUR_ORANGE
        message = f"Veuillez degager votre visage ({machine.duree_obstruction():.1f} s)"
    else:
        couleur = COULEUR_ROUGE
        message = f"ALERTE INTRUSION ({machine.duree_obstruction():.1f} s)"

    # OpenCV n'affiche pas les accents : messages à l'écran sans accents
    cv2.rectangle(frame, (0, HAUTEUR - 50), (LARGEUR, HAUTEUR), couleur, -1)
    cv2.putText(frame, message, (15, HAUTEUR - 17),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, COULEUR_NOIR, 2)
    # Cadre autour de l'image, bien visible pendant la démo
    cv2.rectangle(frame, (0, 0), (LARGEUR - 1, HAUTEUR - 1), couleur, 6)


def dessiner_performance(frame, traitement_ms, fps):
    """Incruste le temps de traitement (vert si < 100 ms, rouge sinon) et le FPS."""
    couleur = COULEUR_VERT if traitement_ms < SEUIL_TRAITEMENT_MS else COULEUR_ROUGE
    cv2.putText(frame, f"Traitement : {traitement_ms:.1f} ms", (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, couleur, 2)
    cv2.putText(frame, f"FPS : {fps:.1f}", (15, 55),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COULEUR_BLANC, 2)


def dessiner_aide_simulation(frame, masque_actif, aucun_visage):
    """Rappel des touches du simulateur, en haut à droite."""
    lignes = [
        f"[m] masque : {'OUI' if masque_actif else 'non'}",
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
    # CAP_DSHOW : backend Windows, ouverture de la webcam beaucoup plus rapide
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Erreur : impossible d'ouvrir la webcam.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEUR)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HAUTEUR)
    cv2.namedWindow(NOM_FENETRE)

    machine = MachineEtats()
    masque_actif = False    # Simulation : touche 'm'
    aucun_visage = False    # Simulation : touche 'n'

    traitement_ms = None
    fps = None
    instant_precedent = time.perf_counter()

    print("SENTINEL-X démarré. Appuyez sur 'q' pour quitter.")
    if MODE_SIMULATION:
        print("Mode simulation : 'm' = masque, 'n' = aucun visage.")

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

            if MODE_SIMULATION:
                visages = simuler_visages(masque_actif, aucun_visage)
            else:
                visages = analyser_trame(frame)

            machine.mettre_a_jour(visage_menace(visages))

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
                dessiner_aide_simulation(frame, masque_actif, aucun_visage)
            cv2.imshow(NOM_FENETRE, frame)

            # Clavier : 'q' pour quitter, 'm' et 'n' pour la simulation
            touche = cv2.waitKey(1) & 0xFF
            if touche == ord("q"):
                break
            if MODE_SIMULATION and touche == ord("m"):
                masque_actif = not masque_actif
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
