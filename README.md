# SENTINEL-X — Module Vision Intelligente

Contrôle d'accès par reconnaissance faciale en temps réel, pour un boîtier de
surveillance autonome.

Le programme analyse la webcam, reconnaît les personnes autorisées et déclenche
une alerte d'intrusion quand un visage n'est pas identifié. Un dashboard de
supervision affiche l'état du système, et une base SQLite conserve les
empreintes, l'historique des événements et les captures d'intrusion.

Projet de la semaine d'intégration EPSI — octobre 2026. Sujet fictif : en 2050,
AetherCorp surveille des micro-centrales isolées. Notre groupe de 5 (2 dev,
3 cyber) a fusionné les volets IA et Cyber.

---

## Démarrage rapide

### 1. Environnement

Windows, **Python 3.11 ou plus récent**.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Une seule dépendance : `opencv-contrib-python`. Ne **pas** installer
`opencv-python` en plus — les deux fournissent le même module `cv2` et entrent en
conflit.

### 2. Modèles

Les deux fichiers `.onnx` ne sont pas versionnés (37 Mo). À récupérer une fois :

```bash
mkdir modeles
curl -L -o modeles/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -L -o modeles/face_recognition_sface_2021dec.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
```

### 3. Inscrire une personne

La base est vide au départ : sans personne inscrite, tout visage est une
intrusion.

```bash
python main.py --inscription
```

Touche **`e`**, prénom dans le terminal, puis 5 photos automatiques avec une
consigne de pose. `Échap` annule.

> **Inscrivez tout le monde en une seule session.** Dès que la base contient une
> empreinte, elle devient la source d'autorité et le dossier `autorises/` n'est
> plus consulté par `main.py`.

L'inscription n'est disponible qu'avec `--inscription` : en surveillance normale,
un intrus ne peut pas s'enrôler lui-même.

### 4. Lancer

```bash
python main.py                 # surveillance (q pour quitter, r pour recharger)
python dashboard.py            # dashboard de supervision, dans un autre terminal
```

---

## Les commandes

| Commande | Rôle |
|---|---|
| `python main.py` | surveillance : reconnaissance + machine à états + alertes |
| `python main.py --inscription` | idem, avec l'inscription (touche `e`) autorisée |
| `python dashboard.py` | console de supervision de tout le projet |
| `python test_ia.py` | banc d'essai IA isolé, pour régler et démontrer |
| `python -m unittest test_base_donnees -v` | les 9 tests de la base |

### Touches

| | `main.py` | `test_ia.py` |
|---|---|---|
| `q` / `Échap` | quitter | quitter |
| `r` | recharger les personnes autorisées | recharger `autorises/` |
| `e` | inscrire (si `--inscription`) | — |
| `s` | — | enregistrer une photo de référence |
| `a` | — | afficher le visage aligné envoyé au modèle |

---

## La stack

Tout le pipeline vision tient dans **OpenCV**, qui fournit à la fois la détection
et la reconnaissance faciale :

| Étape | Classe OpenCV | Modèle | Taille |
|---|---|---|---|
| Détection | `cv2.FaceDetectorYN` | YuNet | 228 Ko |
| Reconnaissance | `cv2.FaceRecognizerSF` | SFace | 37 Mo |

Plus la bibliothèque standard : `sqlite3` pour la base, `tkinter` pour le
dashboard, `ctypes` pour la priorité du processus.

**Ni TensorFlow, ni PyTorch, ni DeepFace, ni MediaPipe.** Ces pistes ont été
écartées sur mesure : les modèles de reconnaissance TensorFlow (ArcFace,
VGG-Face, Facenet512) prennent 335 à 473 ms par visage et dépassent à eux seuls
le budget de 100 ms du cahier des charges. SFace, qui tourne sur OpenCV DNN, le
fait en 13 ms.

### Comment ça marche

```
webcam → 640×480 → YuNet            où est le visage ?           15 ms
                 → alignCrop        redresse sur un 112×112
                 → SFace feature    visage → 128 nombres         13 ms
                 → similarité       qui est-ce ?               0,004 ms
                 → machine à états  VERT / ORANGE / ROUGE
                 → JSON + SQLite
```

La reconnaissance repose sur des **empreintes** : chaque visage est transformé en
128 nombres, et deux photos de la même personne donnent deux vecteurs proches. On
compare par similarité cosinus. L'intérêt opérationnel est décisif — **ajouter
une personne demande une photo et aucun entraînement**.

### Machine à états

| État | Condition | Action |
|---|---|---|
| **VERT** | tous les visages sont reconnus, ou personne devant la caméra | — |
| **ORANGE** | visage inconnu (ou obstrué) depuis 0,3 s | chrono lancé, message à l'écran |
| **ROUGE** | menace continue depuis 3 s | alerte + photo locale dans `captures/` |

Hystérésis de 0,5 s pour revenir au vert. Règle stricte : un seul visage inconnu
suffit, même à côté d'une personne autorisée.

---

## Structure

| Fichier | Rôle |
|---|---|
| `main.py` | capture, machine à états, inscription, sonde de performance |
| `facial_recognition.py` | détection + reconnaissance, logique pure |
| `base_donnees.py` | SQLite : personnes, empreintes, événements, captures |
| `dashboard.py` | console de supervision tkinter |
| `test_ia.py` | banc d'essai IA |
| `test_base_donnees.py` | tests unitaires de la base |

Le contrat entre le pipeline et l'IA tient en deux appels :

```python
import facial_recognition as ia

ia.initialize()                     # une fois, avant la boucle vidéo
faces = ia.analyze_frame(frame)     # à chaque trame
# [{"box": (x, y, w, h), "identity": "isaac" | None,
#   "similarity": 0.71, "obstructed": False, "confidence": 0.0}]
```

`facial_recognition.py` ne touche ni à la webcam, ni à l'affichage, ni au réseau,
ni au stockage : `main.py` lui fournit les références lues en base via
`ia.set_references(...)`.

### Données non versionnées

| Dossier | Contenu | Pourquoi |
|---|---|---|
| `modeles/` | les deux `.onnx` | volumineux |
| `donnees/` | base SQLite | **empreintes = données biométriques** |
| `autorises/` | photos de référence du banc d'essai | **données biométriques** |
| `captures/` | photos d'intrusion | données personnelles |
| `journaux/` | journal du dashboard | — |

La base stocke **l'empreinte, jamais la photo du visage** : une fuite n'expose
aucune image exploitable.

---

## Résultats mesurés

### Performance

| | Temps médian |
|---|---|
| Total par trame, machine dédiée | **29,7 ms** (p95 44,8) |
| Total par trame, poste de travail chargé | 48 – 103 ms |
| Coût de 200 personnes autorisées | **+1 ms** par rapport à une seule |

Le budget de 100 ms est respecté, mais sans marge si le PC fait autre chose.
`main.py` se met en priorité Windows « au-dessus de la normale », ce qui ramène
le pire cas de 226 ms à 61 ms. Avant une démonstration : fermer les navigateurs
et l'IDE, mode d'alimentation « Performances élevées », PC branché.

### Reconnaissance

Validation **leave-one-out** sur 11 photos de 2 personnes :

| | Valeur |
|---|---|
| Personne autorisée | **0,62 – 0,80** — 0 rejet sur 11 |
| Maximum entre deux personnes différentes | **0,366** |
| Seuil retenu | **0,50** |

Le seuil n'est pas celui par défaut d'OpenCV (0,363) : il a été placé au point
d'égale erreur mesuré entre 0,366 et 0,652. Un score de 0,70 est la plage normale
de SFace, pas un mauvais résultat.

---

## Limites connues

| | |
|---|---|
| **Pas de détection de vivacité** | une photo du visage sur un téléphone passerait le contrôle. C'est la faiblesse la plus sérieuse pour un vrai système. |
| **Webcam masquée = « personne devant »** | choix du groupe : personne devant la caméra ne déclenche pas d'alerte, donc un objectif couvert non plus. |
| **Détection d'obstruction non implémentée** | les champs `obstructed` / `confidence` sont réservés mais inertes. Un visage masqué n'est de toute façon pas identifiable, donc il déclenche l'alerte. |
| **Seuil calibré sur 2 personnes** | statistiquement mince, à remesurer avec plus de visages. |
| **Le journal n'enregistre pas les accès réussis** | seuls les changements d'état vont en base ; une personne reconnue devant une caméra déjà VERT ne produit aucun événement. |
| **Publication réseau** | `send_alert()` affiche le JSON ; le vrai envoi MQTTS relève de l'équipe cyber. |
| **Dashboard sur données simulées** | les données capteurs et réseau du groupe ne sont pas encore disponibles. Le badge « DONNEES SIMULEES » est affiché. |

---

## Pour aller plus loin

| Document | Contenu |
|---|---|
| [docs/EXPLICATION_CODE.md](docs/EXPLICATION_CODE.md) | fonctionnement détaillé de l'IA, fonction par fonction, et les pièges |
