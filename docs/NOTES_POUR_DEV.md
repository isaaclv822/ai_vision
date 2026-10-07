# Notes pour la partie Dev — branchement de la reconnaissance faciale

La brique IA est terminée et testée. Elle attend d'être appelée depuis
`main.py`. Ce document contient tout ce qu'il faut pour le branchement : rien
d'autre à lire.

---

## 1. Avant tout : récupérer les deux modèles

Les fichiers `.onnx` ne sont pas dans Git (37 Mo, et `.gitignore` exclut
`*.onnx`). Sans eux, `main.py` lèvera une `FileNotFoundError` explicite.

```bash
mkdir modeles
curl -L -o modeles/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -L -o modeles/face_recognition_sface_2021dec.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
```

Les photos des personnes autorisées vivent dans `autorises/<prénom>/*.jpg` et
ne sont pas versionnées non plus (données biométriques). Pour en créer :
`python test_ia.py` puis touche `s`.

---

## 2. Les dépendances ont changé

`requirements.txt` ne contient plus qu'une ligne : **`opencv-contrib-python`**.

MediaPipe, DeepFace, TensorFlow et tf-keras ont été retirés — ils ne sont plus
importés nulle part. La détection **et** la reconnaissance faciale sont fournies
par OpenCV lui-même (`cv2.FaceDetectorYN` et `cv2.FaceRecognizerSF`).

Conséquence agréable : le conflit `opencv-python` / `opencv-contrib-python`
signalé dans le `CLAUDE.md` disparaît.

> Si ton venv a les deux paquets installés, ça fonctionne quand même. Ne le
> nettoie pas à deux jours de la démo.

Pense à décommenter `paho-mqtt` quand tu attaqueras la publication MQTTS.

---

## 3. Le contrat

```python
import facial_recognition as ia

ia.initialize()                     # UNE FOIS, avant la boucle vidéo
faces = ia.analyze_frame(frame)     # à chaque trame
```

`analyze_frame()` renvoie une liste de dictionnaires :

```python
[
    {
        "box": (x, y, w, h),    # position dans la trame, int, toujours valide
        "identity": "isaac",    # None si la personne n'est pas reconnue
        "similarity": 0.87,     # similarité cosinus avec la référence
        "obstructed": False,    # toujours False pour l'instant
        "confidence": 0.0,      # toujours 0.0 pour l'instant
    }
]
```

Points importants :

- **Liste vide = aucun visage dans le champ.** C'est une situation *suspecte*
  (dos tourné, hors cadre), pas une situation normale. Ta `visage_menace()`
  renvoyait déjà `True` dans ce cas, c'est le bon comportement.
- **`identity is None` = intrus.** C'est le signal principal désormais.
- **Les visages sont triés par taille décroissante**, le plus proche de la
  caméra en premier.
- **`obstructed` / `confidence` sont des places réservées.** Elles existent
  déjà pour que l'ajout de la détection d'obstruction ne te force aucune
  modification plus tard.

Autres fonctions disponibles :

| Appel | Usage |
|---|---|
| `ia.reload_references()` | recharge `autorises/` à chaud (touche `r`) |
| `ia.authorized_people()` | `['isaac', 'binome']`, pour l'affichage |
| `ia.detection_ms` | durée de la détection sur la dernière trame |
| `ia.recognition_ms` | durée de l'identification sur la dernière trame |
| `ia.SIMILARITY_THRESHOLD` | le seuil, si tu veux l'afficher |

Le seuil de similarité est réglé **dans le module IA**, pas dans `main.py` : ne
le duplique pas. `SEUIL_CONFIANCE` dans `main.py` reste réservé au futur modèle
d'obstruction.

---

## 4. Les modifications à faire dans `main.py`

Cinq endroits. Rien de structurel : ta machine à états, ta sonde de performance
et ton `send_alert()` ne changent pas.

### 4.1 — Import et mode simulation

```python
import cv2

import facial_recognition as ia          # AJOUT
```

```python
MODE_SIMULATION = False                  # était True
```

### 4.2 — Initialisation avant la boucle

Dans `main()`, **avant** `cv2.VideoCapture(...)` :

```python
if not MODE_SIMULATION:
    print(ia.initialize())               # ~0,4 s : charge les réseaux et les photos
```

À ne surtout pas faire dans la boucle, ni paresseusement au premier appel : la
première trame mesurerait ~400 ms et ton incrustation passerait au rouge
pendant la démo.

### 4.3 — `analyser_trame()`

Remplace tout le corps de la fonction :

```python
def analyser_trame(frame):
    """Délègue au module IA. Voir docs/NOTES_POUR_DEV.md pour le format."""
    return ia.analyze_frame(frame)
```

⚠️ **Appelle-la avant de dessiner sur la trame.** L'alignement du visage lit
les pixels de l'image : si tu as déjà tracé les rectangles, ils sont encodés
avec le visage. Dans ta boucle actuelle l'ordre est déjà le bon.

### 4.4 — `visage_menace()`

C'est le vrai changement de logique : la menace vient maintenant de
l'**identité**, plus de l'obstruction.

```python
def visage_menace(visages):
    """
    Décide si la trame est suspecte :
    - aucun visage visible (dos tourné, hors champ) -> suspect
    - au moins un visage non reconnu                -> suspect
    """
    if not visages:
        return True
    for visage in visages:
        if visage["identity"] is None:
            return True
    return False
```

Règle volontairement stricte : un seul visage non autorisé dans le champ suffit
à déclencher, même si une personne autorisée est présente à côté. C'est le bon
réflexe pour un contrôle d'accès, et facile à défendre à l'oral.

Quand la détection d'obstruction arrivera, il suffira d'ajouter une ligne :

```python
        if visage["obstructed"] and visage["confidence"] >= SEUIL_CONFIANCE:
            return True
```

### 4.5 — `dessiner_visages()` et `simuler_visages()`

Les clés du dictionnaire sont en anglais. Deux fonctions à adapter :

```python
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
```

```python
def simuler_visages(inconnu, aucun_visage):
    """Faux résultat de l'IA, piloté au clavier."""
    if aucun_visage:
        return []
    box = (LARGEUR // 2 - 80, HAUTEUR // 2 - 100, 160, 200)
    return [{
        "box": box,
        "identity": None if inconnu else "simulation",
        "similarity": 0.21 if inconnu else 0.88,
        "obstructed": False,
        "confidence": 0.0,
    }]
```

Les valeurs `0.21` et `0.88` ne sont pas arbitraires : ce sont les similarités
réellement mesurées pour un inconnu et pour une personne autorisée.

La touche de simulation `m` (masque) n'a plus d'objet pour l'instant — tu peux
la renommer en `i` (inconnu) ou la laisser pour l'étape obstruction.

---

## 5. Ce que ça change pour ta sonde de performance

Mesures réelles d'`analyze_frame()` sur une trame 640×480 avec un visage,
médiane sur 100 appels :

| Étape | Temps médian |
|---|---|
| Détection (YuNet) | 15,0 ms |
| Alignement + empreinte (SFace) | 13,1 ms |
| Comparaison (cosinus) | 0,004 ms |
| **Total** | **29,7 ms** — p95 44,8 — max 52,1 |

**100 trames sur 100 sous les 100 ms.** Aucun thread, aucun traitement d'une
trame sur deux : le pipeline tient dans le budget en direct.

Deux choses utiles à savoir :

- **Le nombre de personnes autorisées est quasi gratuit** : 1 référence =
  28,5 ms, 200 références = 29,6 ms. Le coût est dans les deux réseaux, pas
  dans la comparaison.
- **Sans visage dans le champ, c'est plus rapide** (~15 ms) : seule la
  détection tourne.

Si tu veux afficher le détail dans ton incrustation :

```python
cv2.putText(frame, f"detection {ia.detection_ms:.1f} ms | IA {ia.recognition_ms:.1f} ms",
            (15, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COULEUR_BLANC, 1)
```

---

## 6. Suggestion pour le message réseau vers la cyber

L'identité est l'information qui a de la valeur pour le score de menace unifié.
Si tu enrichis le `details` de `send_alert()` :

```python
send_alert(type_alerte, {
    "etat": self.etat,
    "etat_precedent": ancien_etat,
    "cause": "visage_inconnu",      # ou "aucun_visage", "obstruction"
    "identites": ["isaac"],         # personnes reconnues dans le champ
    "duree_obstruction_s": round(self.duree_obstruction(), 1),
})
```

Aucune image ne sort du PC client : uniquement des états, des noms et des
durées. C'est un bon point à souligner côté cyber.

À valider avec eux : le topic MQTTS, le format exact, et si les noms des
personnes peuvent circuler en clair sur le bus.

---

## 7. Ce qui n'est pas fait

- **La détection d'obstruction** (masque, cagoule, écharpe). Les champs
  `obstructed` / `confidence` existent mais valent toujours `False` / `0.0`.
  Rien ne sera à changer dans `main.py` quand elle arrivera.
- Le seuil de similarité a été calibré sur **une seule** personne non
  autorisée. Il reste sûr (0,21 contre 0,84–0,90 pour une personne autorisée),
  mais plus de visages testés le rendraient plus solide.
