import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# 1. Configurer l'objet d'options (On pointe vers le fichier .tflite téléchargé)
base_options = python.BaseOptions(model_asset_path='blaze_face_short_range.tflite')
options = vision.FaceDetectorOptions(
    base_options=base_options, 
    min_detection_confidence=0.5
)

# 2. Créer le détecteur avec le Context Manager (with)
with vision.FaceDetector.create_from_options(options) as detector:
    
    cap = cv2.VideoCapture(0)
    
    while cap.isOpened():
        success, image = cap.read()
        if not success:
            break

        # Conversion BGR(OpenCV) vers RGB(MediaPipe)
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # La nouvelle API exige que l'image soit "emballée" dans un objet mp.Image
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)

        # L'inférence
        detection_result = detector.detect(mp_image)

        # Affichage des données
        # S'il y a au moins une détection, on imprime la première
        if len(detection_result.detections) > 0:
            # Récupèration de la bounding box du premier visage trouvé
            bbox = detection_result.detections[0].bounding_box
                
            # Extraction des valeurs (MediaPipe donne des float, OpenCV veut des int)
            x = int(bbox.origin_x)
            y = int(bbox.origin_y)
            w = int(bbox.width)
            h = int(bbox.height)
                
            # Calcule les coordonnées de fin du carré
            x_end = x + w
            y_end = y + h
                
            # Découpe l'image d'origine en utilisant le slicing d'OpenCV
            face_sliced = image[y:y_end, x:x_end]
                
            # Affichage du visage découpé dans une nouvelle fenêtre
            cv2.imshow('Visage Isole', face_sliced)

            # print(detection_result.detections[0].bounding_box)
            print(bbox)

        window_name = 'Test IA - Nouvelle API'
        cv2.imshow(window_name, image)

        # On quitte si on appuie sur la touche Echap (Code 27)
        if cv2.waitKey(5) & 0xFF == 27: 
            break

        # On quitte si l'utilisateur clique sur la croix de la fenêtre
        # getWindowProperty renvoie -1 si la fenêtre a été fermée manuellement
        if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
            break
            
    cap.release()
    cv2.destroyAllWindows()