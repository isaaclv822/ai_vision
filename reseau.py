"""
SENTINEL-X — Envoi des alertes vision vers l'API du serveur Kali (HTTPS).

Rien que la bibliothèque standard (urllib + ssl) : aucune dépendance à installer.

Sécurité :
  - TLS vérifié avec le ca.crt de la PKI Sentinel-X (certs/ca.crt) :
    un faux serveur sans certificat signé par cette autorité est refusé
  - jeton lu dans la variable d'environnement SENTINEL_API_TOKEN, jamais dans le code
  - aucun flux vidéo : seulement le JSON des changements d'état, et UNE photo par intrusion

Performance : les envois partent dans un fil d'exécution séparé, la boucle vidéo
n'attend jamais le réseau (budget des 100 ms par trame préservé).
"""

import json
import os
import queue
import ssl
import threading
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("SENTINEL_API", "https://192.168.10.1/api/v1")
CA_FILE = Path(os.environ.get("SENTINEL_CA", Path(__file__).parent / "certs" / "ca.crt"))
TIMEOUT_S = 3
TAILLE_FILE = 100                   # Au-delà (serveur injoignable longtemps), on jette

# L'état périodique (toutes les 2 s) ne part pas vers l'API : il remplirait la
# base de bruit. Seuls les vrais changements y vont.
TYPES_ENVOYES = {"CHANGEMENT_ETAT", "ALERTE_INTRUSION"}


class ClientAPI:

    def __init__(self):
        self.token = os.environ.get("SENTINEL_API_TOKEN", "")
        self.actif = bool(self.token) and CA_FILE.is_file()
        self.dernier_id_intrusion = None
        if not self.actif:
            manque = "SENTINEL_API_TOKEN" if not self.token else f"le certificat {CA_FILE}"
            print(f"[reseau] Envoi vers le serveur DESACTIVE : il manque {manque}.")
            return
        # Vérifie la chaîne de certificats ET l'adresse du serveur (192.168.10.1 est dans le SAN)
        self.contexte = ssl.create_default_context(cafile=str(CA_FILE))
        # Python >= 3.13 active le mode X509 "strict", qui refuse notre CA : le script
        # make-pki.sh (Phase C) la crée sans extension keyUsage. On retire UNIQUEMENT ce
        # contrôle de conformité RFC 5280 ; la chaîne et le nom du serveur restent vérifiés.
        # Correctif propre (après la démo) : régénérer la CA avec keyUsage=keyCertSign,cRLSign.
        self.contexte.verify_flags &= ~ssl.VERIFY_X509_STRICT
        self.file = queue.Queue(maxsize=TAILLE_FILE)
        threading.Thread(target=self._boucle, daemon=True).start()
        print(f"[reseau] Envoi des alertes vers {API_BASE} (TLS verifie).")

    # --- Appelé par main.py ---------------------------------------------------
    def envoyer_alerte(self, message):
        if self.actif and message.get("type") in TYPES_ENVOYES:
            self._deposer(("alerte", message))

    def envoyer_capture(self, image_jpeg):
        """
        Photo de l'intrusion (octets JPEG déjà encodés), rattachée à la dernière
        ALERTE_INTRUSION envoyée. Rien n'est écrit sur le disque local : la photo
        n'existe que sur le Kali.
        """
        if self.actif:
            self._deposer(("capture", image_jpeg))

    # --- Interne --------------------------------------------------------------
    def _deposer(self, tache):
        try:
            self.file.put_nowait(tache)
        except queue.Full:
            print("[reseau] File d'envoi pleine : serveur injoignable ? Element ignore.")

    def _boucle(self):
        # Un seul fil, traité dans l'ordre : l'alerte part toujours avant sa photo
        while True:
            genre, contenu = self.file.get()
            try:
                if genre == "alerte":
                    reponse = self._post("/alerts", json.dumps(contenu).encode("utf-8"),
                                         "application/json")
                    if contenu["type"] == "ALERTE_INTRUSION":
                        self.dernier_id_intrusion = reponse.get("id")
                elif self.dernier_id_intrusion is not None:
                    self._post(f"/alerts/{self.dernier_id_intrusion}/capture",
                               contenu, "image/jpeg")
                    print(f"[reseau] Photo envoyee pour l'alerte {self.dernier_id_intrusion}.")
                    self.dernier_id_intrusion = None
            except Exception as erreur:
                print(f"[reseau] Envoi echoue ({genre}) : {erreur}")

    def _post(self, chemin, corps, type_contenu):
        requete = urllib.request.Request(
            API_BASE + chemin, data=corps, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": type_contenu})
        with urllib.request.urlopen(requete, timeout=TIMEOUT_S, context=self.contexte) as r:
            return json.loads(r.read() or b"{}")

    def verifier_connexion(self):
        """Lecture seule : prouve que TLS, jeton et réseau fonctionnent. Renvoie un message."""
        if not self.actif:
            return "ECHEC : client desactive (voir le message ci-dessus)."
        requete = urllib.request.Request(API_BASE + "/alerts",
                                         headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(requete, timeout=TIMEOUT_S, context=self.contexte) as r:
                return f"CONNEXION OK : le serveur repond ({len(json.loads(r.read()))} alerte(s) en base)."
        except Exception as erreur:
            return f"ECHEC : {erreur}"


if __name__ == "__main__":
    # python reseau.py  -> test de connexion au serveur Kali, avant de lancer main.py
    print(ClientAPI().verifier_connexion())
