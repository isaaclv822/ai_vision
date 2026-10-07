"""
SENTINEL-X — Banc d'essai IA.

Exerce facial_recognition.py isolement : webcam, affichage, mesures, reglage du
seuil. Pas de machine a etats, pas de reseau.

Ce fichier n'implemente aucune logique de reconnaissance : il appelle le meme
module que main.py, pour qu'on ne regle jamais un seuil sur du code qui n'est
pas celui de la demo.

    python test_ia.py

    q / Echap   quitter
    s           enregistrer la trame comme photo de reference
    r           recharger les photos de autorises/
    a           afficher / masquer le visage aligne envoye au modele

Fonctionnement detaille : docs/EXPLICATION_CODE.md
"""

import time

import cv2

import facial_recognition as ia

PERSON_NAME = "isaac"           # Dossier cible de la touche 's'
WINDOW_NAME = "Banc d'essai IA - Reconnaissance faciale"
ALIGNED_WINDOW = "Visage aligne (112x112) envoye a SFace"

GREEN = (0, 200, 0)             # Couleurs OpenCV en BGR
RED = (0, 0, 255)
WHITE = (255, 255, 255)


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def draw_face(frame, face):
    """Encadre un visage : vert s'il est autorise, rouge sinon."""
    x, y, w, h = face["box"]
    color = GREEN if face["identity"] else RED
    label = (f"{face['identity']} {face['similarity']:.2f}" if face["identity"]
             else f"INCONNU {face['similarity']:.2f}")

    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    cv2.putText(frame, label, (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def draw_metrics(frame, total_ms, detection_ms, recognition_ms, count):
    """Panneau de mesures : c'est ce qui prouve le respect des 100 ms."""
    metrics = [
        (f"Total      : {total_ms:5.1f} ms", GREEN if total_ms < 100 else RED),
        (f"Detection  : {detection_ms:5.1f} ms", WHITE),
        (f"Identif.   : {recognition_ms:5.1f} ms", WHITE),
        (f"Visages    : {count}", WHITE),
        (f"Seuil      : {ia.SIMILARITY_THRESHOLD}", WHITE),
    ]
    for index, (text, color) in enumerate(metrics):
        cv2.putText(frame, text, (12, 28 + index * 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)


def save_reference(frame, name):
    """Enregistre la trame dans autorises/<nom>/ et renvoie le chemin."""
    folder = ia.AUTHORIZED_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}_{int(time.time() * 1000)}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def smooth(previous, current):
    """Moyenne glissante : sans ca, les temps affiches sont illisibles."""
    return current if previous is None else 0.9 * previous + 0.1 * current


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------
def main():
    try:
        print(ia.initialize())
    except FileNotFoundError as error:
        print(f"Erreur : {error}")
        return

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Erreur : impossible d'ouvrir la webcam.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, ia.WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, ia.HEIGHT)
    cv2.namedWindow(WINDOW_NAME)

    total_ms = detection_ms = recognition_ms = None
    show_aligned = False
    print("\ns = photo de reference, r = recharger, a = visage aligne, q = quitter.")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Erreur : lecture de la webcam impossible.")
                break

            if frame.shape[1] != ia.WIDTH or frame.shape[0] != ia.HEIGHT:
                frame = cv2.resize(frame, (ia.WIDTH, ia.HEIGHT))

            # Copie avant dessin : les photos de reference doivent etre vierges
            clean_frame = frame.copy()

            start = time.perf_counter()
            faces = ia.analyze_frame(frame)
            total_ms = smooth(total_ms, (time.perf_counter() - start) * 1000)
            detection_ms = smooth(detection_ms, ia.detection_ms)
            recognition_ms = smooth(recognition_ms, ia.recognition_ms)

            for face in faces:
                draw_face(frame, face)
            draw_metrics(frame, total_ms, detection_ms, recognition_ms, len(faces))
            cv2.imshow(WINDOW_NAME, frame)

            if show_aligned and faces:
                aligned = ia.debug_aligned_face(clean_frame)
                if aligned is not None:
                    cv2.imshow(ALIGNED_WINDOW, cv2.resize(aligned, (224, 224)))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                if not faces:
                    print("Photo refusee : aucun visage dans le cadre.")
                else:
                    path = save_reference(clean_frame, PERSON_NAME)
                    print(f"Photo enregistree : {path.name} ('r' pour l'activer)")
            if key == ord("r"):
                print(ia.reload_references())
            if key == ord("a"):
                show_aligned = not show_aligned
                if not show_aligned:
                    cv2.destroyWindow(ALIGNED_WINDOW)

            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Banc d'essai arrete.")


if __name__ == "__main__":
    main()
