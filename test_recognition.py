import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from deepface import DeepFace

# --- 1. Configuration et Chargement en RAM ---
chemin_photo = "autorises/moi.jpg"
image_reference = cv2.imread(chemin_photo)

if image_reference is None:
    print(f"🛑 ERREUR CRITIQUE : Impossible de trouver la photo {chemin_photo}")
    print("Vérifiez que le dossier 'autorises' existe et contient 'moi.jpg'.")
    exit()

base_options = python.BaseOptions(model_asset_path='blaze_face_short_range.tflite')
options = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=0.5)

# --- 2. Démarrage ---
print("🎥 Initialisation du système AetherCorp...")
cap = cv2.VideoCapture(0)

with vision.FaceDetector.create_from_options(options) as detector:
    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            break

        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)

        detection_result = detector.detect(mp_image)

        if len(detection_result.detections) > 0:
            bbox = detection_result.detections[0].bounding_box
            x, y, w, h = int(bbox.origin_x), int(bbox.origin_y), int(bbox.width), int(bbox.height)
            
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
            
            padding = 20
            y_start = max(0, y - padding)
            y_end = min(frame.shape[0], y + h + padding)
            x_start = max(0, x - padding)
            x_end = min(frame.shape[1], x + w + padding)
            
            visage_decoupe = frame[y_start:y_end, x_start:x_end]

            if visage_decoupe.shape[0] > 50 and visage_decoupe.shape[1] > 50:
                cv2.imshow("Scanner IA", visage_decoupe)
                
                touche = cv2.waitKey(1) & 0xFF
                if touche == ord('v'):
                    print("\n⏳ Analyse biométrique en cours...")
                    try:
                        # LA SOLUTION : On crée une copie parfaite en RAM
                        visage_ram = visage_decoupe.copy()

                        # On passe les deux images directement depuis la RAM !
                        resultat = DeepFace.verify(
                            img1_path=visage_ram, 
                            img2_path=image_reference, 
                            enforce_detection=False, 
                            model_name="VGG-Face"
                        )
                        
                        if resultat["verified"]:
                            print("✅ Accès Autorisé.")
                        else:
                            print("❌ Alerte Intrus.")
                    except Exception as e:
                        print(f"⚠️️ Erreur DeepFace : {e}")
                        
        else:
            if cv2.getWindowProperty("Scanner IA", cv2.WND_PROP_VISIBLE) > 0:
                 cv2.destroyWindow("Scanner IA")

        cv2.imshow("Test IA - AetherCorp", frame)
        
        if cv2.waitKey(5) & 0xFF == 27:
            break

cap.release()
cv2.destroyAllWindows()