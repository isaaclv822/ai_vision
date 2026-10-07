# Explication du code — partie IA

Ce document contient les explications retirées de `facial_recognition.py` et
`test_ia.py` pour garder le code court et lisible. Les fichiers ne conservent
que les commentaires signalant un **piège** réel.

---

## Le pipeline en 4 étapes

```
trame BGR 640x480
      │
      ├─ 1. detect()      YuNet  : où sont les visages ?        15,0 ms
      │        └─► tableau N×15 : boîte + 5 points clés + score
      │
      ├─ 2. alignCrop()   SFace  : redresse sur un 112x112        ┐
      │                                                           ├ 13,1 ms
      ├─ 3. feature()     SFace  : visage → vecteur de 128       ┘
      │
      └─ 4. match()       SFace  : similarité cosinus          0,004 ms
                                   contre chaque référence
```

---

## `facial_recognition.py`

### Pourquoi l'état est au niveau du module

Les deux réseaux (`_detector`, `_recognizer`) et les empreintes de référence
(`_references`) sont des ressources **uniques et coûteuses à charger** (0,41 s).
Les garder dans des variables de module plutôt que dans une classe permet
d'exposer à la partie Dev une fonction **sans état** : `analyze_frame(frame)`.

C'est un singleton assumé. L'avantage est que le contrat avec `main.py` tient
en une ligne, sans objet à créer, à stocker et à faire circuler.

### `initialize()`

Séparer l'initialisation de la première trame n'est pas cosmétique. Si on
chargeait les réseaux paresseusement au premier appel d'`analyze_frame()`, la
première trame mesurerait ~400 ms et l'incrustation de performance passerait au
rouge au démarrage de la démo.

`analyze_frame()` lève donc un `RuntimeError` explicite si `initialize()` a été
oublié, plutôt que de planter avec un `AttributeError` sur `None`.

**Piège** : la taille déclarée à `cv2.FaceDetectorYN.create(...)` doit être
exactement celle des trames envoyées à `detect()`. Si elle ne correspond pas,
YuNet renvoie des boîtes aberrantes **sans lever aucune erreur**.

### `reload_references()`

Deux arborescences sont acceptées :

```
autorises/isaac/photo1.jpg   -> personne "isaac"
autorises/isaac/photo2.jpg   -> idem (plusieurs photos = plus robuste)
autorises/binome.jpg         -> personne "binome" (photo à plat)
```

**Une empreinte par photo, pas une moyenne par personne.** Moyenner des poses
différentes produit un vecteur qui ne ressemble à aucune des poses réelles. On
fait du 1 plus proche voisin : on garde le meilleur score parmi toutes les
références. Le coût est négligeable — 4,2 µs par comparaison, soit 1 ms de plus
pour 200 références que pour une seule.

**Piège** : les photos de référence n'ont pas forcément la résolution de la
webcam, d'où l'appel à `setInputSize()` avant chaque `detect()`. Et il faut
**remettre la taille à 640×480 à la fin**, sinon la boucle vidéo détecte des
boîtes fausses.

### `analyze_frame()`

**Piège** : quand aucun visage n'est trouvé, `detect()` renvoie `None` et non un
tableau vide. Un `len(rows)` lève une `TypeError`.

Les visages sont triés par surface décroissante : le plus grand est le plus
proche de la caméra, c'est celui qui intéresse un contrôle d'accès.

Une **liste vide** signifie « aucun visage dans le champ ». C'est une situation
suspecte (dos tourné, visage hors cadre), pas une situation normale — à la
machine à états de `main.py` de la traiter.

Les champs `obstructed` / `confidence` valent toujours `False` / `0.0`. Ils sont
dans le dictionnaire dès maintenant pour que l'ajout de la détection
d'obstruction ne force **aucune modification** côté Dev.

### `_encode()` — le rôle d'`alignCrop`

```python
_recognizer.feature(_recognizer.alignCrop(image, row))
```

C'est la ligne la plus importante du module.

SFace a été entraîné sur des visages **alignés** : une transformation de
similarité recale les 5 points clés (œil droit, œil gauche, nez, coins de la
bouche) sur un gabarit canonique de 112×112. `alignCrop()` applique exactement
cette transformation, et il prend en entrée **la ligne YuNet telle quelle**.

L'ordre des 5 points est donc un contrat implicite entre les deux modèles —
c'est la raison principale du choix YuNet + SFace plutôt que MediaPipe + SFace :
MediaPipe ne fournit pas les coins de la bouche.

Sans cet alignement, l'écart entre deux photos de la même personne la tête
inclinée différemment devient **comparable à l'écart entre deux personnes
différentes**. C'est la brique que la plupart des tutoriels sautent, et la
raison pour laquelle leur reconnaissance est instable.

### `_identify()`

La similarité cosinus vaut 1 pour deux vecteurs identiques et ~0 pour deux
visages sans rapport. SFace a été entraîné avec une loss à marge angulaire :
l'espace est organisé pour que deux vues de la même identité pointent dans la
même direction, indépendamment de la pose et de la lumière. C'est donc la
**direction** qui porte l'identité, pas la norme — le vecteur n'est d'ailleurs
pas normalisé (norme ≈ 9,9), c'est `match()` qui normalise en interne.

`best_similarity` est initialisé à **−1 et non à 0** : la similarité cosinus
peut être négative, et on veut pouvoir lire la vraie valeur pour régler le
seuil. Avec une initialisation à 0, un visage à −0,1 s'afficherait à 0,00.

### `_clamp_box()`

YuNet peut renvoyer une boîte qui dépasse légèrement les bords de l'image.
OpenCV s'en moque pour dessiner, mais ces coordonnées partent ensuite dans le
message JSON vers l'équipe cyber : autant qu'elles soient toujours valides et
converties en `int` (les 15 valeurs de YuNet sont des `float`).

### `debug_aligned_face()`

Renvoie le 112×112 que SFace reçoit réellement, pour la touche `a` du banc
d'essai. Elle refait une détection complète, donc elle n'a rien à faire dans la
boucle de production — d'où le préfixe `debug_` et la mention explicite dans sa
docstring.

---

## `test_ia.py`

### Pourquoi il importe le module au lieu de le dupliquer

Aux blocs 1 et 2, le banc d'essai contenait sa propre copie de la logique. Après
extraction, il **importe** `facial_recognition`. C'est le point le plus important
de l'architecture : sans ça, on règle un seuil sur du code qui n'est pas celui
qui tourne pendant la démo, et les deux versions divergent au premier correctif.

Le banc d'essai garde uniquement ce qui ne doit pas se trouver en production :
la webcam, la fenêtre, les touches, le lissage des mesures.

### `smooth()`

Moyenne glissante exponentielle, facteur 0,9. Sans elle, les temps affichés
sautent entre 12 et 38 ms et sont illisibles à l'œil. C'est un défaut
d'affichage, pas un défaut de mesure.

### La copie `clean_frame`

Les photos de référence doivent être **vierges de rectangles et de texte**,
sinon on encode les annotations en même temps que le visage. D'où la copie
faite avant le dessin.

### `detection_ms` / `recognition_ms`

Le module expose ces deux variables, mises à jour à chaque `analyze_frame()`.
Le banc d'essai les lit pour afficher le détail, et mesure le total de son côté.
Les trois valeurs ne coïncident pas exactement (le total inclut la construction
des dictionnaires), ce qui est normal.

---

## Mesures

Toutes les mesures citées ici ont été faites sur le PC client, trame 640×480
avec un visage, médiane sur 100 à 2000 appels selon le cas. Elles sont
détaillées dans `AVANCEMENT_IA.md`.
