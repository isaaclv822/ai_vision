"""
SENTINEL-X — Module Vision Intelligente

Analyse la webcam en temps réel pour détecter un visage obstrué.
Étape 1 : boucle vidéo + sonde de performance (l'IA n'est pas encore branchée).
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

VERT = (0, 200, 0)                 # Couleurs OpenCV en BGR
ROUGE = (0, 0, 255)
BLANC = (255, 255, 255)


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
# Affichage
# ---------------------------------------------------------------------------
def dessiner_visages(frame, visages):
    """Encadre chaque visage : rouge s'il est obstrué, vert sinon."""
    for visage in visages:
        x, y, w, h = visage["box"]
        couleur = ROUGE if visage["obstrue"] else VERT
        cv2.rectangle(frame, (x, y), (x + w, y + h), couleur, 2)
        texte = f"{visage['confiance'] * 100:.0f} %"
        cv2.putText(frame, texte, (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, couleur, 2)


def dessiner_performance(frame, traitement_ms, fps):
    """Incruste le temps de traitement (vert si < 100 ms, rouge sinon) et le FPS."""
    couleur = VERT if traitement_ms < SEUIL_TRAITEMENT_MS else ROUGE
    cv2.putText(frame, f"Traitement : {traitement_ms:.1f} ms", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, couleur, 2)
    cv2.putText(frame, f"FPS : {fps:.1f}", (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, BLANC, 2)


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

    traitement_ms = None
    fps = None
    instant_precedent = time.perf_counter()

    print("SENTINEL-X démarré. Appuyez sur 'q' pour quitter.")

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

            visages = analyser_trame(frame)
            dessiner_visages(frame, visages)

            # --- Fin de la mesure (avant l'affichage) ---
            fin = time.perf_counter()
            traitement_ms = lisser(traitement_ms, (fin - debut) * 1000)

            # FPS = intervalle entre deux trames (dépend surtout de la caméra)
            intervalle = fin - instant_precedent
            instant_precedent = fin
            if intervalle > 0:
                fps = lisser(fps, 1 / intervalle)

            dessiner_performance(frame, traitement_ms, fps or 0)
            cv2.imshow(NOM_FENETRE, frame)

            # Sortie : touche 'q' ou croix de la fenêtre
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if cv2.getWindowProperty(NOM_FENETRE, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        # Toujours libérer la caméra, même en cas d'erreur
        cap.release()
        cv2.destroyAllWindows()
        print("SENTINEL-X arrêté proprement.")


if __name__ == "__main__":
    main()
