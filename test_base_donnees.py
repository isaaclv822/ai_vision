"""
Tests de base_donnees.py — lancer avec : python -m unittest test_base_donnees -v
Chaque test travaille sur une base temporaire, jamais sur la vraie.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from base_donnees import BaseDonnees

MODELE_SFACE = Path(__file__).parent / "modeles" / "face_recognition_sface_2021dec.onnx"


def fausse_empreinte(graine):
    """Empreinte au format exact de SFace : numpy float32 de forme (1, 128)."""
    return np.random.default_rng(graine).standard_normal((1, 128)).astype(np.float32)


class TestBaseDonnees(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.base = BaseDonnees(Path(self.dossier) / "test.db")

    def tearDown(self):
        self.base.fermer()
        shutil.rmtree(self.dossier)

    # --- Personnes et empreintes ----------------------------------------------
    def test_une_ligne_par_photo(self):
        self.base.ajouter_personne("joakim")
        e1, e2 = fausse_empreinte(1), fausse_empreinte(2)
        self.base.ajouter_empreinte("joakim", e1)
        self.base.ajouter_empreinte("joakim", e2)

        references = self.base.charger_empreintes()
        self.assertEqual([nom for nom, _ in references], ["joakim", "joakim"])
        self.assertEqual(references[0][1].shape, (1, 128))
        self.assertEqual(references[0][1].dtype, np.float32)
        np.testing.assert_array_equal(references[0][1], e1)
        np.testing.assert_array_equal(references[1][1], e2)

    def test_personne_revoquee_n_est_plus_chargee(self):
        for prenom in ("joakim", "isaac"):
            self.base.ajouter_personne(prenom)
            self.base.ajouter_empreinte(prenom, fausse_empreinte(len(prenom)))
        self.assertTrue(self.base.revoquer("isaac"))
        self.assertFalse(self.base.revoquer("isaac"))          # Déjà révoqué
        self.assertEqual([nom for nom, _ in self.base.charger_empreintes()], ["joakim"])
        # La personne reste en base, avec sa date de révocation
        isaac = [p for p in self.base.personnes() if p["prenom"] == "isaac"][0]
        self.assertEqual(isaac["actif"], 0)
        self.assertIsNotNone(isaac["revoque_le"])

    def test_empreinte_d_un_autre_modele_ignoree_et_comptee(self):
        self.base.ajouter_personne("joakim")
        self.base.ajouter_empreinte("joakim", fausse_empreinte(1))
        self.base.ajouter_empreinte("joakim", fausse_empreinte(2), modele="ancien_modele")
        self.assertEqual(len(self.base.charger_empreintes()), 1)
        self.assertEqual(self.base.compter_empreintes_autre_modele(), 1)

    def test_prenoms_refuses(self):
        self.base.ajouter_personne("Jean-Éric")                  # Accents et tiret acceptés
        for prenom in ("", "a" * 51, "x'); DROP TABLE personnes;--", "jo\nakim", "<script>"):
            with self.assertRaises(ValueError, msg=repr(prenom)):
                self.base.ajouter_personne(prenom)
        with self.assertRaises(ValueError):
            self.base.ajouter_personne("Jean-Éric")              # Doublon

    def test_empreintes_refusees(self):
        self.base.ajouter_personne("joakim")
        mauvaises = [np.zeros(127), np.full(128, np.nan), np.full(128, np.inf)]
        for empreinte in mauvaises:
            with self.assertRaises(ValueError):
                self.base.ajouter_empreinte("joakim", empreinte)
        with self.assertRaises(ValueError):
            self.base.ajouter_empreinte("inconnu", fausse_empreinte(1))

    # --- Événements et captures -----------------------------------------------
    def test_evenements_du_plus_recent_au_plus_ancien(self):
        details = {"etat": "ROUGE", "cause": "visage_inconnu", "identites": [],
                   "similarite": None, "traitement_ms": 31.4, "duree_menace_s": 3.0}
        self.base.enregistrer_evenement("vision", "CHANGEMENT_ETAT", {"etat": "ORANGE"})
        id_alerte = self.base.enregistrer_evenement("vision", "ALERTE_INTRUSION", details)
        self.base.enregistrer_capture("captures/intrusion_20261007_104213.jpg", id_alerte)

        evenements = self.base.derniers_evenements()
        self.assertEqual([e["type"] for e in evenements], ["ALERTE_INTRUSION", "CHANGEMENT_ETAT"])
        self.assertEqual(evenements[0]["details"], details)         # JSON relu à l'identique
        self.assertEqual(evenements[0]["cause"], "visage_inconnu")
        self.assertEqual(evenements[0]["capture"], "captures/intrusion_20261007_104213.jpg")
        self.assertIsNone(evenements[1]["capture"])

    def test_texte_malveillant_stocke_tel_quel(self):
        """Requêtes paramétrées : une tentative d'injection est stockée comme du texte."""
        attaque = "'); DROP TABLE evenements; --"
        self.base.enregistrer_evenement("vision", attaque, {"cause": attaque})
        self.assertEqual(self.base.derniers_evenements()[0]["type"], attaque)
        self.assertEqual(len(self.base.derniers_evenements()), 1)    # La table existe toujours

    def test_capture_liee_a_un_evenement_inexistant(self):
        with self.assertRaises(sqlite3.IntegrityError):              # Clés étrangères actives
            self.base.enregistrer_capture("captures/x.jpg", evenement_id=999)

    # --- État courant (lu par le dashboard) -------------------------------------
    def test_etat_courant_une_seule_ligne_par_source(self):
        self.assertIsNone(self.base.lire_etat_courant("vision"))
        self.base.mettre_a_jour_etat_courant("vision", {"etat": "VERT", "identites": ["joakim"]})
        self.base.mettre_a_jour_etat_courant("vision", {"etat": "ORANGE", "identites": []})
        horodatage, details = self.base.lire_etat_courant("vision")
        self.assertEqual(details, {"etat": "ORANGE", "identites": []})   # Le dernier écrase
        self.assertTrue(horodatage.endswith("+00:00"))                    # UTC
        nb_lignes = self.base.connexion.execute("SELECT COUNT(*) FROM etat_courant").fetchone()[0]
        self.assertEqual(nb_lignes, 1)                                    # La table ne grossit pas

    # --- Compatibilité avec SFace ---------------------------------------------
    @unittest.skipUnless(MODELE_SFACE.exists(), "modèle SFace absent")
    def test_empreinte_relue_compatible_avec_sface(self):
        reconnaisseur = cv2.FaceRecognizerSF.create(str(MODELE_SFACE), "")
        originale = fausse_empreinte(7)
        self.base.ajouter_personne("joakim")
        self.base.ajouter_empreinte("joakim", originale)
        _, relue = self.base.charger_empreintes()[0]
        similarite = reconnaisseur.match(originale, relue, cv2.FaceRecognizerSF_FR_COSINE)
        self.assertAlmostEqual(similarite, 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
