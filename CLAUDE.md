# SENTINEL-X — Module Vision Intelligente

Projet de groupe de la semaine d'intégration EPSI (octobre 2026). Démo et pentest croisé le **jeudi**.
Langue du projet : français (code commenté en français, messages affichés en français).

## Contexte

Sujet fictif : en 2050, AetherCorp surveille des micro-centrales isolées avec un boîtier autonome.
Notre groupe (2 dev, 3 cyber) a **fusionné les volets IA et Cyber** du sujet :
une IA qui détecte les menaces, sur un pipeline chiffré et durci par la cyber.

Ce dépôt contient le **module vision** : un script Python qui analyse la webcam en temps réel
et détecte un visage obstrué (masque, cagoule, écharpe, main) pour déclencher une alerte d'intrusion.

Répartition dans ce module :
- **Profil Dev (moi)** : pipeline vidéo, performance, machine à états, envoi réseau.
- **Profil IA (binôme)** : détection de visage (MediaPipe) et classification masque / pas masque.

## Architecture retenue

- **Un seul PC = serveur + webcam intégrée.** Le script tourne directement sur la machine, **hors Docker**
  (faire voir la webcam à un conteneur est trop galère). Seul le broker Mosquitto est conteneurisé.
- Pipeline : capture OpenCV → resize 640x480 → MediaPipe Face Detection → recadrage du visage
  → modèle masque (MobileNetV2 Keras, ou `.tflite` / ONNX via OpenCV DNN si TensorFlow pose problème)
  → machine à états → publication MQTTS.
- **Réseau : MQTTS uniquement** (`paho-mqtt` + TLS, `localhost:8883`, certificat fourni par l'équipe cyber).
  Jamais de HTTP en clair, jamais de flux vidéo sur le réseau : seuls les états et alertes JSON sortent.
- Les états sont publiés **à chaque changement** (pas seulement l'alerte rouge) : ils alimentent le
  score de menace unifié calculé par l'autre dev (Isolation Forest capteurs + réseau).

## Contraintes du cahier des charges

- Traitement **< 100 ms par trame**, images bridées en **640x480**.
- Mesurer le **temps de traitement** (après `cap.read()` jusqu'avant l'affichage), pas seulement
  l'intervalle entre trames, qui dépend surtout de la caméra (~33 ms à 30 fps).
- Afficher la mesure en incrustation sur la vidéo pour la démo.

## Machine à états (jour 2)

- **VERT** : visage détecté, non obstrué. Timer à 0.
- **ORANGE** : visage obstrué. Message « Veuillez dégager votre visage ». Timer démarré.
- **ROUGE** : obstruction continue > 3 s. Alerte MQTTS + affichage « ALERTE INTRUSION ».

Règles :
- Timer avec `time.monotonic()`, pas en comptant les trames.
- **Tolérance aux erreurs du modèle** : quelques trames contradictoires ne doivent pas remettre
  le timer à zéro (hystérésis).
- Ne déclarer « obstrué » qu'au-delà d'un seuil de confiance (~80 %, à ajuster).
- Cas à gérer si possible : personne présente mais aucun visage visible (dos tourné) → ORANGE aussi.

## État actuel

Jour 1 = réflexion. Étape 1 du code faite (`test_ia.py` encore vide) — `main.py` :
- Capture webcam (`cv2.VideoCapture(0, cv2.CAP_DSHOW)` sous Windows), resize 640x480.
- Sonde de performance : temps de traitement (vert < 100 ms, rouge au-delà) + FPS, lissés.
- Sortie avec `q` ou la croix de la fenêtre, libération propre de la caméra.
- `analyser_trame(frame)` : point de branchement de l'IA, renvoie pour l'instant `[]`.
  Format attendu : `[{"box": (x, y, w, h), "obstrue": True, "confiance": 0.92}]`.
- `send_alert(type_alerte, details)` : mock qui affiche le JSON final
  `{"node_id", "type", "timestamp", "details"}`. Au jour 3, seul le `print` devient `client.publish(...)`.

Étape 2 faite — machine à états :
- `MachineEtats` (VERT / ORANGE / ROUGE, `time.monotonic()`, retour au VERT après 0,5 s découvert)
  et `visage_menace(visages)` (seuil de confiance, aucun visage = menace).
- Publie `CHANGEMENT_ETAT` ou `ALERTE_INTRUSION` via `send_alert` à chaque changement.
- `MODE_SIMULATION = True` : touches `m` (masque) et `n` (aucun visage) remplacent l'IA.
  Passer à `False` une fois `analyser_trame` branchée.

À faire :
- Jour 2 (avec le binôme IA) : intégrer MediaPipe + modèle masque dans `analyser_trame`,
  recadrage du visage. **En attente du modèle.**
- Jour 3 : vraie publication MQTTS (topic, certificat et format à valider avec la cyber),
  seuils de confiance, interface type « terminal de sécurité », répétition de la démo.

## Environnement

- Windows, Python **3.11** dans un venv (versions plus récentes = risques avec MediaPipe/TensorFlow).
- `python -m venv .venv` puis `.venv\Scripts\activate`, `pip install -r requirements.txt`, `python main.py`.
- **Piège** : MediaPipe installe `opencv-contrib-python`, en conflit avec `opencv-python`.
  Si `cv2` plante : désinstaller les deux, réinstaller seulement `opencv-contrib-python`.

## Conventions

- **Git : on travaille uniquement sur la branche `main`.** Pas de branche de fonctionnalité, pas de PR.
- Garder le code simple et lisible : c'est un projet étudiant présenté à l'oral.
- Constantes de configuration en haut de `main.py`.
- Ne pas ajouter de dépendance lourde sans vérifier l'impact sur les 100 ms.
