# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Annexe technique. Constantes publiques du reglement.

Ce fichier fait partie de l'enonce : il ne contient aucune information sur un
flux particulier, seulement les conventions du parc et les seuils cites par
le reglement R1 a R10. Importez ces constantes plutot que de les recopier.

Ne pas modifier : app.py et la suite de tests s'appuient dessus.
"""

# --- Parc
MACHINES = ["M-%02d" % i for i in range(1, 13)]
INDEX_MACHINE = {m: i for i, m in enumerate(MACHINES)}   # configuration, hors budget
FREQUENCE_HZ = 10              # echantillons par machine et par seconde simulee
CHAMPS_MESURE = ["temperature", "vibration", "pressure", "rpm", "power"]

# --- R1, R2 : fenetre de livraison. Un evenement arrive au plus
#              RETARD_MAX_S secondes simulees apres son horodatage.
RETARD_MAX_S = 5.0

# --- R6 : versions de micrologiciel dont la temperature est en degres
#          Fahrenheit (version majeure, premier nombre de `fw`).
FW_FAHRENHEIT_DES = 2

# --- R7 : intervalles physiquement admissibles, bornes comprises, unites
#          de l'evenement nettoye (temperature en degres Celsius).
BORNES = {
    "temperature": (5.0, 150.0),
    "vibration": (0.1, 50.0),
    "pressure": (0.5, 50.0),
    "rpm": (-50.0, 3000.0),
    "power": (0.0, 500.0),
}

# --- R8 : capteurs figes. Champs concernes, et longueur de serie a partir
#          de laquelle une valeur repetee est un artefact.
CHAMPS_GEL = ["temperature", "vibration", "pressure"]
GEL_N = 10

# --- R9 : prediction. Une coupure survient-elle dans les HORIZON_S secondes
#          simulees qui suivent l'horodatage de l'evenement ?
HORIZON_S = 10.0
SEUIL_DECISION = 0.5

# --- R10 : memoire
BUDGET_OCTETS = 32_768         # budget de la couche de donnees (src/memoire.py)
PLAFOND_HORS_MEMOIRE = 8_192   # etat tolere hors couche, configuration comprise

# --- R9 : plancher de performance, F1 sur la seconde moitie du flux au
#     seuil SEUIL_DECISION. Une regle de seuil fixe sur la temperature,
#     meme choisie apres coup, reste en dessous.
PLANCHER_F1 = 0.78
