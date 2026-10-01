# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Chaine de traitement a completer.

Ce fichier est le point d'entree de la correction. Vous pouvez y ecrire tout
votre code ou le repartir dans d'autres modules de votre depot (importes
depuis la racine, par exemple `from src.outils import ...`), pourvu que la
suite de tests s'execute sans dependance absente de `requirements.txt`.

La classe `Traitement` constitue le contrat d'interface (README, section 3).
Son nom, ses methodes et la forme de ce qu'elles renvoient sont imposes.

Ce qui est demande (README, section 4) :

    Partie A, chaine de filtres     R1 a R8
    Partie B, prediction            R9, regression logistique en ligne
    Partout, memoire bornee         R10, tout l'etat dans `memoire`

L'ordre dans lequel vous appliquez R1 a R8 n'est pas impose ; il n'est pas
indifferent pour autant.
"""
from src.annexe import (BORNES, CHAMPS_GEL, CHAMPS_MESURE, FREQUENCE_HZ,
                        FW_FAHRENHEIT_DES, GEL_N, HORIZON_S, INDEX_MACHINE,
                        MACHINES, RETARD_MAX_S)


class Traitement:

    def __init__(self, memoire):
        """R10. Alloue dans `memoire` tout l'etat qui depend du flux.

        Les allocations se font ici, une fois pour toutes : la couche est
        verrouillee apres la construction. Exemple :

            self.seq_max = memoire.allouer("seq_max", len(MACHINES), "i64")
        """
        raise NotImplementedError("Traitement.__init__ : allocations (R10)")

    def traiter(self, evenement):
        """Recoit un evenement brut (dict) et renvoie :

        - None si l'evenement est un doublon (R1) ;
        - sinon un dict, l'evenement nettoye :

            {"machine_id": str, "seq": int, "ts": int,
             "retard": bool,                     # R2
             "arret": bool,                      # R5
             "temperature": float ou None,       # degres Celsius (R6)
             "vibration": float ou None,
             "pressure": float ou None,
             "rpm": float ou None,
             "power": float ou None,
             "p": float dans [0, 1] ou None}     # R9

        La prediction `p` est calculee avant toute mise a jour du modele par
        cet evenement.
        """
        raise NotImplementedError("Traitement.traiter : R1 a R9")

    def resume(self):
        """Renvoie un dict serialisable en JSON, avec au moins les compteurs
        entiers "doublons" (R1), "retards" (R2) et "pertes" (R3). Vous pouvez
        y ajouter tout ce qui vous aide a suivre votre traitement.
        """
        raise NotImplementedError("Traitement.resume : compteurs R1 a R3")
