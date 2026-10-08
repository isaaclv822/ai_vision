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

Soit **~30 ms par trame** sur machine dédiée, pour un budget de 100 ms imposé par
le cahier des charges. Sur un poste de travail chargé (navigateurs, IDE), la
médiane monte à 48–103 ms : c'est de la concurrence CPU, pas un problème de code.

---

## L'API publique

Sept fonctions, regroupées en trois usages.

| Usage | Fonctions | Appelé par |
|---|---|---|
| Reconnaissance en direct | `initialize()`, `analyze_frame()` | `main.py`, `test_ia.py` |
| Source des références | `reload_references()`, `set_references()`, `authorized_people()` | `main.py`, `test_ia.py` |
| Inscription | `reference_embedding()`, `compare()` | `main.py --inscription` |

Plus `debug_aligned_face()`, réservée à la mise au point.

### Les deux sources de références, et pourquoi

Le module sait charger ses références de deux façons, et c'est volontaire :

| | Fonction | Source | Qui l'utilise |
|---|---|---|---|
| **Base de données** | `set_references(liste)` | SQLite, via `main.py` | l'application |
| **Dossier de photos** | `reload_references()` | `autorises/<prénom>/*.jpg` | le banc d'essai |

C'est ce qu'on appelle de l'**injection de dépendance** : le module ne va pas
chercher les données, on les lui donne. Conséquence recherchée —
`facial_recognition.py` n'importe ni `sqlite3` ni `base_donnees`, et reste donc
ce que sa docstring annonce : *ni webcam, ni affichage, ni réseau, ni stockage*.

Le bénéfice concret : `test_ia.py` continue de fonctionner sans base de données,
ce qui permet de régler le seuil sans toucher aux données de production.

> L'alternative aurait été que le module importe lui-même `base_donnees`. Plus
> court, mais le banc d'essai devenait inutilisable tant que la base était vide.

---

## `facial_recognition.py`

### Pourquoi l'état est au niveau du module

Les deux réseaux (`_detector`, `_recognizer`) et les empreintes de référence
(`_references`) sont des ressources **uniques et coûteuses à charger** (0,4 s).
Les garder dans des variables de module plutôt que dans une classe permet
d'exposer une fonction **sans état** : `analyze_frame(frame)`.

C'est un singleton assumé. L'avantage est que le contrat avec `main.py` tient en
une ligne, sans objet à créer, à stocker et à faire circuler.

### `initialize()`

Séparer l'initialisation de la première trame n'est pas cosmétique. Si on
chargeait les réseaux paresseusement au premier appel d'`analyze_frame()`, la
première trame mesurerait ~400 ms et l'incrustation de performance passerait au
rouge au démarrage de la démo.

`analyze_frame()`, `reference_embedding()` et `compare()` lèvent donc un
`RuntimeError` explicite si `initialize()` a été oublié, plutôt que de planter
avec un `AttributeError` sur `None`.

**Piège** : la taille déclarée à `cv2.FaceDetectorYN.create(...)` doit être
exactement celle des trames envoyées à `detect()`. Si elle ne correspond pas,
YuNet renvoie des boîtes aberrantes **sans lever aucune erreur**.

### `analyze_frame()`

**Piège** : quand aucun visage n'est trouvé, `detect()` renvoie `None` et non un
tableau vide. Un `len(rows)` lève une `TypeError`.

Les visages sont triés par surface décroissante : le plus grand est le plus
proche de la caméra, c'est celui qui intéresse un contrôle d'accès. `main.py`
s'appuie dessus pour publier la similarité du visage principal.

Une **liste vide** signifie « aucun visage dans le champ ». Le groupe a décidé
que ce n'est **pas** une menace — sinon le système passait au rouge en permanence
quand personne n'est là. Limite connue et documentée : une webcam masquée
ressemble aussi à « personne devant ».

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

### `_downscale()` — la plage d'échelle de YuNet

YuNet est entraîné pour des visages d'environ **10×10 à 300×300 px**. Au-delà, il
trouve toujours le visage, mais **son score de confiance s'effondre** :

| Taille du visage dans la photo | Score de détection |
|---|---|
| 216 × 282 px | 0,942 |
| 272 × 369 px | 0,937 |
| 372 × 504 px | 0,903 — passe |
| 375 × 496 px | 0,891 — rejeté |
| 531 × 679 px | 0,794 |
| 885 × 1256 px | 0,686 |

Mesuré en abaissant le seuil de détection à 0,05 : le visage est trouvé **à
chaque fois**. Ce qui le faisait rejeter, c'était notre propre
`DETECTION_THRESHOLD = 0.9`.

Le bug était donc à l'intersection de **deux choix corrects** : un seuil de
détection strict (bon pour éviter les faux positifs sur le fond de la webcam) et
des photos de référence non normalisées. Aucun des deux n'était faux tout seul.

`_downscale()` ramène toute image dont le plus grand côté dépasse 640 px dans la
plage où YuNet est sûr de lui. Deux détails :

- **Jamais d'agrandissement** (`if scale >= 1: return image`) : la webcam, qui
  sort du 640×480, n'est jamais touchée. Le chemin temps réel est intact.
- **`INTER_AREA`** : c'est l'interpolation correcte pour *réduire* une image, elle
  moyenne les pixels de la zone source. `INTER_LINEAR`, le défaut, produirait du
  crénelage qui dégraderait la détection.

### `_identify()`

La similarité cosinus vaut 1 pour deux vecteurs identiques et ~0 pour deux
visages sans rapport. SFace a été entraîné avec une loss à marge angulaire
(*sigmoid-constrained hypersphere loss*) : l'espace est organisé pour que deux
vues de la même identité pointent dans la même direction, indépendamment de la
pose et de la lumière. C'est donc la **direction** qui porte l'identité, pas la
norme — le vecteur n'est d'ailleurs pas normalisé (norme ≈ 9,9), c'est `match()`
qui normalise en interne.

**On garde le maximum sur toutes les références**, méthode du plus proche voisin.
Deux conséquences utiles :

- Ajouter des photos ne peut **jamais** baisser un score.
- Le nombre de personnes est quasi gratuit : une comparaison prend **4,2 µs**, et
  200 références coûtent 1 ms de plus qu'une seule par trame.

`best_similarity` est initialisé à **−1 et non à 0** : la similarité cosinus peut
être négative, et on veut pouvoir lire la vraie valeur pour régler le seuil. Avec
une initialisation à 0, un visage à −0,1 s'afficherait à 0,00.

### `_clamp_box()`

YuNet peut renvoyer une boîte qui dépasse légèrement les bords de l'image.
OpenCV s'en moque pour dessiner, mais ces coordonnées partent ensuite dans le
message JSON vers l'équipe cyber : autant qu'elles soient toujours valides et
converties en `int` (les 15 valeurs de YuNet sont des `float`).

### `reference_embedding()` — l'inscription

```python
empreinte, probleme = ia.reference_embedding(image)
```

Cette fonction existe pour une raison précise : **une empreinte n'est comparable
que si le prétraitement est identique à celui d'`analyze_frame()`**. Si la partie
Dev avait recalculé l'empreinte elle-même en appelant SFace sans `alignCrop`,
elle aurait obtenu un vecteur inutilisable **sans aucune erreur visible** — tout
aurait fonctionné jusqu'au moment où plus personne n'est reconnu.

Les contrôles sont **plus stricts qu'à l'exécution**, et c'est volontaire : une
mauvaise trame en direct est oubliée à la trame suivante, une mauvaise référence
pollue la base durablement.

| Refus | Message |
|---|---|
| image non chargée | `image illisible` |
| aucun visage | `aucun visage detecte` |
| plusieurs visages | `2 visages detectes, une seule personne attendue` |
| visage < 80 px | `visage trop petit (...) : rapprochez-vous` |

Le refus sur **plusieurs visages** est délibéré : avec deux personnes sur la
photo, on ne sait pas laquelle inscrire. Une erreur vaut mieux qu'un choix
arbitraire — `analyze_frame()`, lui, prend le plus grand, mais c'est un autre
métier.

Le retour `(valeur, probleme)` plutôt qu'une exception rend le message
directement affichable dans le bandeau de l'inscription.

### `compare()`

Expose la similarité cosinus pour les garde-fous d'inscription de `main.py` :
vérifier qu'une nouvelle photo ressemble assez aux photos déjà enregistrées de la
personne (cohérence ≥ 0,50) et pas trop à celles des autres (collision < 0,45).
Ces deux seuils ont été validés sur 11 photos réelles : **0 refus à tort**.

### `MODEL_NAME`

`"sface_2021dec"`, identique au `MODELE_EMPREINTE` de `base_donnees.py`.

Une empreinte n'a de sens que pour le modèle qui l'a produite. Si on changeait de
modèle de reconnaissance, toutes les empreintes stockées deviendraient
**silencieusement invalides** : aucune erreur, juste plus personne de reconnu.
La base enregistre donc le nom du modèle avec chaque empreinte, et
`compter_empreintes_autre_modele()` détecte le décalage.

### `debug_aligned_face()`

Renvoie le 112×112 que SFace reçoit réellement, pour la touche `a` du banc
d'essai. Elle refait une détection complète, donc elle n'a rien à faire dans la
boucle de production — d'où le préfixe `debug_` et la mention explicite dans sa
docstring.

---

## `test_ia.py`

### Pourquoi il importe le module au lieu de le dupliquer

Aux premières étapes, le banc d'essai contenait sa propre copie de la logique.
Après extraction, il **importe** `facial_recognition`. C'est le point le plus
important de l'architecture : sans ça, on règle un seuil sur du code qui n'est
pas celui qui tourne pendant la démo, et les deux versions divergent au premier
correctif.

Le banc d'essai garde uniquement ce qui ne doit pas se trouver en production :
la webcam, la fenêtre, les touches, le lissage des mesures.

Il lit ses références dans `autorises/` via `reload_references()`, pas dans la
base. Il fonctionne donc sans base de données.

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

## Mesures et calibration

Toutes les mesures ont été faites sur le PC client, trame 640×480 avec un visage.

### Temps de traitement

| Étape | Temps médian |
|---|---|
| Détection (YuNet) | 15,0 ms |
| Alignement + empreinte (SFace) | 13,1 ms |
| Comparaison (cosinus) | 0,004 ms |
| **Total** | **29,7 ms** — p95 44,8 — max 52,1 |

Montée en charge : 1 référence → 28,5 ms, 10 → 29,0 ms, 200 → 29,6 ms.

### Seuil de reconnaissance

Protocole : **validation leave-one-out**. Chaque photo de référence joue à son
tour le rôle de l'image en direct — retirée de la base, comparée aux autres, en
gardant le maximum, exactement comme `_identify()` en production. C'est ce qui
évite de mesurer la ressemblance d'une photo avec elle-même.

| | Plage | Médiane | Rejets |
|---|---|---|---|
| Personne autorisée (11 photos, 2 personnes) | **0,62 – 0,80** | ~0,71 | **0/11** |
| **Maximum entre deux personnes différentes** | | **0,366** | |
| Image sans visage (bruit, uni) | −0,11 – 0,17 | | |
| Seuil documenté par OpenCV | 0,363 | | |
| **Seuil retenu** | **0,50** | | |

La pire correspondance légitime est à 0,652 et le meilleur score entre deux
personnes différentes à 0,366 : le point d'égale erreur est à **0,509**. On
retient 0,50.

> **Un score de 0,70 est normal pour SFace**, pas médiocre. Ses propres auteurs
> documentent un seuil à 0,363 : ils s'attendent à des correspondances légitimes
> bien en dessous de 0,9.

Relever le seuil de 0,363 à 0,50 est un arbitrage **FAR / FRR** assumé : on
accepte un peu plus de faux rejets pour réduire les faux positifs, parce qu'un
intrus accepté coûte plus cher qu'une personne autorisée qui doit se
représenter.

**Réserve** : deux personnes ne constituent pas une distribution. Plus de visages
non autorisés testés feraient remonter le maximum observé, et le seuil devrait
être remesuré.

### Conditions d'enrôlement

L'empreinte garde la trace des conditions de prise de vue : optique, capteur,
rendu des couleurs, compression, éclairage. Une référence prise au téléphone et
comparée à une image de webcam donne un score plus bas qu'une référence prise
avec la webcam elle-même.

L'enrôlement se fait donc **dans les conditions d'exploitation** — c'est la
pratique en biométrie, un portique d'aéroport enrôle avec sa propre caméra.
