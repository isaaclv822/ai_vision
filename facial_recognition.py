"""
SENTINEL-X — Module IA : reconnaissance faciale (YuNet + SFace, OpenCV).

Logique pure : ni webcam, ni affichage, ni reseau. main.py et test_ia.py
partagent ce meme module.

Contrat avec la partie Dev :

    import facial_recognition as ia

    ia.initialize()                     # une fois, AVANT la boucle video
    faces = ia.analyze_frame(frame)     # a chaque trame

    faces == [{"box": (x, y, w, h), "identity": "isaac" | None,
               "similarity": 0.87, "obstructed": False, "confidence": 0.0}]

Liste vide = aucun visage dans le champ, ce qui est une situation suspecte.

Pour l'inscription d'une personne, utiliser reference_embedding() et compare() :
une empreinte n'est comparable que si le pretraitement est identique.

Fonctionnement detaille  : docs/EXPLICATION_CODE.md
Notes pour la partie Dev : docs/NOTES_POUR_DEV.md
"""

import time
from pathlib import Path

import cv2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
DETECTION_MODEL = ROOT / "modeles" / "face_detection_yunet_2023mar.onnx"
RECOGNITION_MODEL = ROOT / "modeles" / "face_recognition_sface_2021dec.onnx"
AUTHORIZED_DIR = ROOT / "autorises"

WIDTH, HEIGHT = 640, 480        # Resolution imposee par le cahier des charges
DETECTION_THRESHOLD = 0.9       # Score minimal pour retenir un visage

# Similarite cosinus au-dela de laquelle c'est la meme personne.
# Mesure en validation leave-one-out sur 11 photos : meme personne 0.62-0.80,
# personnes differentes 0.366 au maximum. Seuil documente par OpenCV : 0.363.
# 0.50 tombe au milieu de 0.366 et 0.652 (la pire correspondance legitime).
SIMILARITY_THRESHOLD = 0.50

# Nom du modele qui calcule les empreintes. A garder identique a
# MODELE_EMPREINTE de base_donnees.py : une empreinte n'a de sens que pour le
# modele qui l'a produite. Changer de modele invalide toutes les empreintes.
MODEL_NAME = "sface_2021dec"

PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png")
MAX_PHOTO_SIZE = 640            # Cote maximal d'une photo de reference (voir _downscale)
MIN_REFERENCE_FACE = 80         # Visage plus petit = empreinte de reference mediocre

# Etat du module : les deux reseaux et les empreintes sont des ressources
# uniques et couteuses a charger. Les garder ici permet d'exposer a la partie
# Dev une fonction sans etat.
_detector = None
_recognizer = None
_references = []                # [(nom, empreinte)] — une entree par photo

# Durees de la derniere trame analysee, lues par main.py et test_ia.py.
detection_ms = 0.0
recognition_ms = 0.0


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def initialize():
    """Charge les deux reseaux puis les visages autorises. Renvoie un resume."""
    global _detector, _recognizer

    for model in (DETECTION_MODEL, RECOGNITION_MODEL):
        if not model.exists():
            raise FileNotFoundError(
                f"Modele introuvable : {model}\n"
                "Voir la section Installation de docs/NOTES_POUR_DEV.md"
            )

    # A appeler hors de la boucle video : le chargement prend ~0.4 s et
    # fausserait la mesure de la premiere trame.
    # Piege : la taille declaree ici doit etre exactement celle des trames
    # envoyees a detect(), sinon les boites sont fausses sans aucune erreur.
    _detector = cv2.FaceDetectorYN.create(
        str(DETECTION_MODEL), "", (WIDTH, HEIGHT), DETECTION_THRESHOLD
    )
    _recognizer = cv2.FaceRecognizerSF.create(str(RECOGNITION_MODEL), "")

    return reload_references()


def reload_references():
    """Encode les photos de autorises/<nom>/ en empreintes. Renvoie un resume."""
    global _references
    _references = []

    if _recognizer is None:
        raise RuntimeError("initialize() doit etre appele avant reload_references().")

    ignored = []
    if AUTHORIZED_DIR.is_dir():
        for path in sorted(AUTHORIZED_DIR.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            # Photo dans un sous-dossier -> le dossier donne le nom de la personne
            relative = path.relative_to(AUTHORIZED_DIR)
            name = relative.parts[0] if len(relative.parts) > 1 else path.stem

            image = cv2.imread(str(path))
            if image is None:
                ignored.append(f"{path.name} (illisible)")
                continue

            # Piege : une photo de telephone est trop grande pour YuNet
            image = _downscale(image)

            # Les photos n'ont pas forcement la resolution de la webcam
            _detector.setInputSize((image.shape[1], image.shape[0]))
            _, rows = _detector.detect(image)
            if rows is None:
                ignored.append(f"{path.name} (aucun visage detecte)")
                continue

            _references.append((name, _encode(image, rows[0])))

        # On rend au detecteur la taille de la webcam avant la boucle video
        _detector.setInputSize((WIDTH, HEIGHT))

    if not _references:
        summary = (f"Aucun visage autorise dans {AUTHORIZED_DIR} : "
                   "tout visage sera considere comme une intrusion.")
    else:
        summary = (f"{len(_references)} empreinte(s) pour "
                   f"{len(authorized_people())} personne(s) : "
                   f"{', '.join(authorized_people())}")
    if ignored:
        summary += "\n   Photos ignorees : " + ", ".join(ignored)
    return summary


def authorized_people():
    """Noms des personnes presentes dans la base, sans doublon."""
    return sorted({name for name, _ in _references})


# ---------------------------------------------------------------------------
# Inscription (utilise par la partie Dev)
# ---------------------------------------------------------------------------
def reference_embedding(image):
    """
    Calcule l'empreinte d'une photo destinee a etre enregistree comme reference.

    Renvoie (empreinte, probleme) :
        (empreinte de forme (1, 128), None)  photo exploitable
        (None, "message en francais")        photo a refuser, raison affichable

    Les controles sont plus stricts qu'a l'execution : une mauvaise trame en
    direct est oubliee a la trame suivante, une mauvaise reference pollue la
    base durablement.

    A utiliser impérativement plutot que de recalculer l'empreinte soi-meme :
    elle n'est comparable que si le pretraitement est identique a celui de
    analyze_frame() (reduction, puis alignCrop sur les 5 points de YuNet).
    Appeler SFace sans alignCrop produit une empreinte inutilisable, et
    silencieusement : aucune erreur, mais plus personne n'est reconnu.
    """
    if _detector is None:
        raise RuntimeError("facial_recognition.initialize() doit etre appele "
                           "avant reference_embedding().")
    if image is None:
        return None, "image illisible"

    image = _downscale(image)
    _detector.setInputSize((image.shape[1], image.shape[0]))
    _, rows = _detector.detect(image)

    if rows is None:
        return None, "aucun visage detecte"
    if len(rows) > 1:
        # On refuse au lieu de prendre le plus grand : on ne sait pas laquelle
        # des personnes presentes doit etre inscrite.
        return None, f"{len(rows)} visages detectes, une seule personne attendue"

    width, height = int(rows[0][2]), int(rows[0][3])
    if min(width, height) < MIN_REFERENCE_FACE:
        return None, (f"visage trop petit ({width}x{height} px, "
                      f"{MIN_REFERENCE_FACE} minimum) : rapprochez-vous")

    return _encode(image, rows[0]), None


def compare(embedding_a, embedding_b):
    """
    Similarite cosinus entre deux empreintes : 1 = identiques, 0 = sans rapport.

    Sert aux controles d'inscription : verifier qu'une nouvelle photo ressemble
    assez aux photos deja enregistrees de la personne, et pas trop a celles des
    autres.
    """
    if _recognizer is None:
        raise RuntimeError("facial_recognition.initialize() doit etre appele "
                           "avant compare().")
    return float(_recognizer.match(embedding_a, embedding_b,
                                   cv2.FaceRecognizerSF_FR_COSINE))


def debug_aligned_face(frame):
    """Visage aligne 112x112 recu par SFace, ou None. Mise au point uniquement."""
    if _detector is None:
        return None
    _, rows = _detector.detect(frame)
    if rows is None:
        return None
    largest = max(rows, key=lambda row: row[2] * row[3])
    return _recognizer.alignCrop(frame, largest)


# ---------------------------------------------------------------------------
# Analyse d'une trame
# ---------------------------------------------------------------------------
def analyze_frame(frame):
    """Detecte et identifie les visages d'une trame BGR 640x480."""
    global detection_ms, recognition_ms

    if _detector is None:
        raise RuntimeError("facial_recognition.initialize() doit etre appele "
                           "avant analyze_frame().")

    # YuNet travaille directement en BGR : aucune conversion de couleur
    start = time.perf_counter()
    _, rows = _detector.detect(frame)
    detection_ms = (time.perf_counter() - start) * 1000

    # Piege : sans visage, detect() renvoie None et non un tableau vide
    if rows is None:
        recognition_ms = 0.0
        return []

    start = time.perf_counter()
    faces = []
    for row in rows:
        name, similarity = _identify(_encode(frame, row))
        faces.append({
            "box": _clamp_box(row, frame.shape),
            "identity": name,
            "similarity": similarity,
            # Toujours inertes : places reservees a l'etape obstruction
            "obstructed": False,
            "confidence": 0.0,
        })
    recognition_ms = (time.perf_counter() - start) * 1000

    # Le plus grand visage d'abord : c'est le plus proche de la camera
    faces.sort(key=lambda face: face["box"][2] * face["box"][3], reverse=True)
    return faces


# ---------------------------------------------------------------------------
# Interne
# ---------------------------------------------------------------------------
def _downscale(image, max_size=MAX_PHOTO_SIZE):
    """
    Reduit une photo dont le plus grand cote depasse max_size.

    YuNet est entraine pour des visages d'environ 10x10 a 300x300 px. Au-dela,
    il trouve toujours le visage mais son score s'effondre : mesure sur nos
    photos, 0.94 pour un visage de 216 px, 0.89 a 375 px, 0.68 a 885 px. Le
    visage se fait donc rejeter par DETECTION_THRESHOLD, pas par le detecteur.
    Reduire l'image le ramene dans la plage ou YuNet est sur de lui.

    La webcam sort du 640x480 : elle n'est jamais concernee.
    """
    height, width = image.shape[:2]
    scale = max_size / max(height, width)
    if scale >= 1:
        return image
    return cv2.resize(image, (int(width * scale), int(height * scale)),
                      interpolation=cv2.INTER_AREA)


def _encode(image, row):
    """Visage -> empreinte de 128 nombres. alignCrop redresse sur un 112x112."""
    return _recognizer.feature(_recognizer.alignCrop(image, row))


def _identify(feature):
    """Compare une empreinte aux references. Renvoie (nom | None, similarite)."""
    if not _references:
        return None, 0.0

    # Initialise a -1 et non a 0 : la similarite cosinus peut etre negative
    best_name, best_similarity = None, -1.0
    for name, reference in _references:
        similarity = _recognizer.match(feature, reference,
                                       cv2.FaceRecognizerSF_FR_COSINE)
        if similarity > best_similarity:
            best_name, best_similarity = name, similarity

    if best_similarity < SIMILARITY_THRESHOLD:
        return None, best_similarity
    return best_name, best_similarity


def _clamp_box(row, shape):
    """Ligne YuNet -> (x, y, w, h) entiers, recadres dans l'image."""
    height, width = shape[:2]
    x, y, w, h = (int(value) for value in row[:4])
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return x, y, w, h
