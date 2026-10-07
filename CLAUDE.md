# SENTINEL-X — Module Vision Intelligente

Projet de groupe de la semaine d'intégration EPSI (octobre 2026). Démo et pentest croisé le **jeudi**.
Langue du projet : français (code commenté en français, messages affichés en français).

## Contexte

Sujet fictif : en 2050, AetherCorp surveille des micro-centrales isolées avec un boîtier autonome.
Notre groupe (2 dev, 3 cyber) a **fusionné les volets IA et Cyber** du sujet :
une IA qui détecte les menaces, sur un pipeline chiffré et durci par la cyber.

Ce dépôt contient le **module vision** : un script Python qui analyse la webcam en temps réel
et déclenche une alerte d'intrusion quand un visage est **inconnu**, **absent** (dos tourné)
ou, plus tard, **caché** (masque, cagoule, écharpe, main).
Il contient aussi le **dashboard de supervision de tout le projet** (`dashboard.py`), demandé par le groupe.

Répartition dans ce module :
- **Profil Dev (moi)** : `main.py` — pipeline vidéo, performance, machine à états, mock d'alerte ;
  `dashboard.py` — supervision ; `base_donnees.py` — BDD SQLite (tests : `test_base_donnees.py`).
- **Profil IA (binôme)** : `facial_recognition.py` — détection + reconnaissance faciale.
  Son guide de branchement : `docs/NOTES_POUR_DEV.md`. Banc d'essai : `test_ia.py`.

**Périmètre :** la partie réseau (MQTTS, broker, certificats, validation des payloads) n'est
**pas** la mienne : c'est un autre membre / l'équipe cyber. Ne rien ajouter qui n'est pas prévu
par le cadrage ou demandé par le groupe ; en cas de doute, demander avant de coder.

## Architecture retenue

- **Un seul PC = serveur + webcam intégrée.** Le script tourne directement sur la machine, **hors Docker**
  (faire voir la webcam à un conteneur est trop galère). Seul le broker Mosquitto est conteneurisé.
- Pipeline : capture OpenCV → resize 640x480 → détection YuNet (`cv2.FaceDetectorYN`)
  → alignement + empreinte SFace (`cv2.FaceRecognizerSF`) → comparaison aux photos de `autorises/`
  → machine à états → publication MQTTS. Tout est fourni par `opencv-contrib-python`, ~30 ms par trame.
- Contrat IA : `ia.initialize()` une fois avant la boucle, puis `ia.analyze_frame(frame)` qui renvoie
  `[{"box": (x, y, w, h), "identity": "isaac" | None, "similarity": 0.87, "obstructed": False, "confidence": 0.0}]`.
  Liste vide = aucun visage. `obstructed` / `confidence` sont réservés (toujours inertes pour l'instant).
  Le seuil de similarité est réglé dans `facial_recognition.py`, pas dans `main.py`.
- **Réseau : MQTTS uniquement** (`paho-mqtt` + TLS, `localhost:8883`, certificat fourni par l'équipe cyber).
  Jamais de HTTP en clair, jamais de flux vidéo sur le réseau : seuls les états et alertes JSON sortent.
- Les états sont publiés **à chaque changement** (pas seulement l'alerte rouge) : ils alimentent le
  score de menace unifié calculé par l'autre dev (Isolation Forest capteurs + réseau).
- Dashboard : programme **tkinter** séparé de `main.py` (ne ralentit pas la vision). Pour l'instant
  alimenté par `DonneesSimulees` ; les vraies données passeront par `Dashboard.recevoir(source, donnees)`.
  Source muette > 5 s → alerte. État global = le pire de score / vision / sources muettes.
  Journal horodaté aussi écrit dans `journaux/` (preuve pour le pentest).

## Contraintes du cahier des charges

- Traitement **< 100 ms par trame**, images bridées en **640x480**.
- Mesurer le **temps de traitement** (après `cap.read()` jusqu'avant l'affichage), pas seulement
  l'intervalle entre trames, qui dépend surtout de la caméra (~33 ms à 30 fps).
- Afficher la mesure en incrustation sur la vidéo pour la démo.

## Machine à états

- **VERT** : tous les visages du champ sont reconnus. Chrono à 0.
- **ORANGE** : situation suspecte, chrono démarré. Le message dépend de la cause
  (`aucun_visage`, `visage_inconnu`, `obstruction`).
- **ROUGE** : menace continue > 3 s. Alerte + affichage « ALERTE INTRUSION ».

Règles :
- Chrono avec `time.monotonic()`, pas en comptant les trames.
- **Tolérance** : retour au VERT seulement après 0,5 s de situation normale (hystérésis).
- Règle stricte : un seul visage inconnu suffit, même à côté d'une personne autorisée.
- Obstruction prise en compte seulement si `confidence >= SEUIL_CONFIANCE` (80 %).

## État actuel

Fait dans `main.py` :
- Capture webcam (`CAP_DSHOW`), 640x480, sortie avec `q` ou la croix, libération propre de la caméra.
- Sonde de performance lissée (traitement, FPS, détail détection / IA).
- IA branchée : `ia.initialize()` avant la boucle, `analyser_trame` → `ia.analyze_frame`, touche `r`
  pour recharger `autorises/`.
- `cause_menace(visages)` + `MachineEtats`. Publie `CHANGEMENT_ETAT` / `ALERTE_INTRUSION` à chaque
  changement, et `ETAT_PERIODIQUE` toutes les 2 s (sinon le dashboard déclarerait la vision muette).
  `details` = `{etat, cause, identites, similarite, traitement_ms, duree_menace_s}` (+ `etat_precedent`
  pour un changement). Le dashboard reçoit ce `details` tel quel, pas l'enveloppe.
  Prévenir l'autre dev : le trafic vision est périodique (base de référence de son Isolation Forest).
- `send_alert` : mock (`print` du JSON). Le vrai envoi réseau sera fait par celui qui gère le réseau.
- Photo locale dans `captures/` au passage en ROUGE (jamais envoyée sur le réseau).
- BDD branchée : chaque changement d'état va dans `evenements` (pas l'état périodique), la capture
  est reliée à l'alerte ROUGE (`MachineEtats.dernier_evenement_id`). ~1 ms par écriture.
- `MODE_SIMULATION = True` pour tester sans webcam ni modèle : touches `i` (inconnu), `n` (aucun visage).

Fait dans `dashboard.py` (`python dashboard.py`) : score en grand, tuiles vision / capteurs / réseau
avec courbes, signaux de vie, indicateurs cyber (canal chiffré, payloads rejetés), journal,
bouton « Alerte prise en compte », badge « DONNEES SIMULEES ».

Fait dans `base_donnees.py` (SQLite, `donnees/sentinelx.db`, non versionné, mode WAL) :
- Tables `personnes` (prénom, actif, révocation), `empreintes` (une ligne **par photo**, 128 float32,
  colonne `modele`), `evenements` (details en JSON), `captures` (chemin du JPEG, lié à l'événement).
- On stocke l'empreinte SFace, **jamais la photo du visage**. Requêtes paramétrées partout.
- `charger_empreintes()` → `[(prenom, empreinte (1, 128))]`, personnes actives et modèle courant
  uniquement : c'est ce que le binôme appellera dans `reload_references()` (son fichier, pas le mien).
- Tests : `python -m unittest test_base_donnees -v`.

Les données de la cyber ne sont pas encore disponibles : le dashboard reste sur des mocks.
Ne pas inventer de format pour les données réseau : attendre celui du groupe.

À faire :
- Donner `charger_empreintes()` au binôme pour `reload_references()`.
- Décider avec le binôme comment inscrire une personne (script d'inscription ou `test_ia.py`).
- Brancher les vraies données dans `Dashboard.recevoir` quand le groupe les fournira.
- Détection d'obstruction (binôme IA) : rien à changer dans `main.py` quand elle arrivera.
- Répétition de la démo.

## Environnement

- Windows, Python **3.11** dans un venv `.venv`.
- `python -m venv .venv` puis `.venv\Scripts\activate`, `pip install -r requirements.txt`, `python main.py`.
- Ne **pas** installer `opencv-python` en plus de `opencv-contrib-python` (même module `cv2`, conflit).
- Non versionnés (à récupérer sur chaque machine) :
  - `modeles/*.onnx` : commandes `curl` dans `docs/NOTES_POUR_DEV.md`.
  - `autorises/<prénom>/*.jpg` : photos de référence (données biométriques), créées avec `python test_ia.py` puis `s`.
  - `captures/` (photos d'intrusion), `journaux/` (journal du dashboard), `donnees/` (base SQLite).
- Le venv local a OpenCV 5.0 : ça marche (deux `WARN` dnn au démarrage, sans conséquence).

## Conventions

- **Git : une branche par fonctionnalité** (`feat/...`, comme le binôme), fusionnée ensuite dans `main`.
  Ne pas commiter le `PERSON_NAME` local de `test_ia.py`.
- Garder le code simple et lisible : c'est un projet étudiant présenté à l'oral.
- Constantes de configuration en haut de `main.py`.
- Ne pas ajouter de dépendance lourde sans vérifier l'impact sur les 100 ms.
