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
from hashlib import blake2b
import math

UNITE_TS = 1000
USE_GAP = True

LR = 0.05
POIDS_POS = 2.0
INCR_MOYENNE = 1
PERIODE_ECHANTILLONAGE = 4

CHAMPS_STOCKE = ["ts", "temperature", "rpm", "pressure"]
CHAMPS_STOCKE2 = ["ts", "temperature", "vibration", "pressure", "rpm", "power", "temperature_gap", "vibration_gap", "pressure_gap", "rpm_gap", "power_gap"]

N_MODELE = len(CHAMPS_STOCKE) - 1 
NB_FEATURES = 1 + N_MODELE * (2 if USE_GAP else 1)


def _sigmoide(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))

class Traitement:

    class Boom:
        def __init__(self, memoire, nom, m, k):
            self.m = m
            self.k = k
            self.bits = memoire.allouer(nom, m, "bit")

        def h(self, cle, i):
            d = blake2b(cle.encode(), digest_size=8, key=bytes([i])).digest()
            return int.from_bytes(d, "big") % self.m
    
        def ajouter(self, cle):
            for i in range(self.k):
                self.bits[self.h(cle, i)] = True

        def peut_contenir(self, cle):
            return all(self.bits[self.h(cle, i)] for i in range(self.k))

        def reinitialiser(self):
            for i in range(len(self.bits)):
                self.bits[i] = False

    def __init__(self, memoire):
        """R10. Alloue dans `memoire` tout l'etat qui depend du flux.

        Les allocations se font ici, une fois pour toutes : la couche est
        verrouillee apres la construction. Exemple :

            self.seq_max = memoire.allouer("seq_max", len(MACHINES), "i64")
        """
        self.periode = PERIODE_ECHANTILLONAGE
        self.queue_size = int(HORIZON_S * FREQUENCE_HZ / self.periode) + 1


        self.nb_doublons = 0    # doublons
        self.nb_retards = 0     # retards
        self.nb_pertes = 0      # pertes

        self.bloom1 = self.Boom(memoire, "bloom1", 30000, 6)
        self.bloom2 = self.Boom(memoire, "bloom2", 30000, 6)
        self.curent_bloom = self.bloom1
        self.swap_sequence = 50

        self.gel_val = memoire.allouer("gel_val", len(MACHINES) * len(CHAMPS_GEL), "f64")
        self.gel_nb  = memoire.allouer("gel_nb",  len(MACHINES) * len(CHAMPS_GEL), "i64")

        self.seq_max = memoire.allouer("seq_max", len(MACHINES), "i64")

        self.mean   = memoire.allouer("means", (len(MACHINES), len(CHAMPS_MESURE), 2), "f64")

        self.queue  = memoire.allouer("queue", (len(MACHINES), self.queue_size, NB_FEATURES), "f64")
        self.nb_elt = memoire.allouer("nb_elt", len(MACHINES), "i64")
        self.head   = memoire.allouer("head", len(MACHINES), "i64")
        self.count  = memoire.allouer("count", len(MACHINES), "i64")
        self.weight = memoire.allouer("weight", NB_FEATURES, "f64")

        for i in range(len(MACHINES)):
            self.seq_max[i] = 0
            self.head[i] = 0
            self.nb_elt[i] = 0
            self.count[i] = self.periode + 1

        for i in range(NB_FEATURES):
            self.weight[i] = 0.0


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
        sortie = {
                "machine_id": evenement["machine_id"],
                "seq": evenement["seq"],
                "ts": evenement["ts"],

                "retard": False, 
                "arret": False,

                "p": 0.0
            }

        if (sortie["seq"] > self.seq_max[INDEX_MACHINE[evenement["machine_id"]]]):
            # R3
            self.nb_pertes += sortie["seq"] - self.seq_max[INDEX_MACHINE[evenement["machine_id"]]] - 1
            self.seq_max[INDEX_MACHINE[evenement["machine_id"]]] = sortie["seq"]

        # R1
        if (max(self.seq_max) >= self.swap_sequence):
            if self.curent_bloom == self.bloom1:
                self.curent_bloom = self.bloom2
            else:
                self.curent_bloom = self.bloom1
            self.curent_bloom.reinitialiser()
            self.swap_sequence += 50

        cle = f"{evenement['machine_id']}:{evenement['seq']}"
        if self.bloom1.peut_contenir(cle) or self.bloom2.peut_contenir(cle):
            self.nb_doublons += 1
            return None
        self.curent_bloom.ajouter(cle)

        # R2
        if (sortie["seq"] < self.seq_max[INDEX_MACHINE[evenement["machine_id"]]]):
            sortie["retard"] = True
            self.nb_retards += 1
            # R3
            self.nb_pertes -= 1

        # R4
        for champ in CHAMPS_MESURE:
            sortie[champ] = evenement.get(champ)

        # R5
        arret = True
        for champ in CHAMPS_MESURE:
            if sortie[champ] is not None:
                arret = False
                break        
        sortie["arret"] = arret

        # R6        
        fw_majeur = int(evenement["fw"].split(".")[0])

        if sortie["temperature"] is not None and fw_majeur >= FW_FAHRENHEIT_DES:
            sortie["temperature"] = round(float((sortie["temperature"]-32)*5/9), 1)

        # R7
        for champ in CHAMPS_MESURE:
            if (sortie[champ] is not None and (sortie[champ] < BORNES[champ][0] or sortie[champ] > BORNES[champ][1])):
                sortie[champ] = None

        # R8
        if not sortie["retard"]:
            m = INDEX_MACHINE[evenement["machine_id"]]
            for j, champ in enumerate(CHAMPS_GEL):
                v = sortie[champ]
                if v is None:
                    continue

                c = m * len(CHAMPS_GEL) + j
                if self.gel_nb[c] > 0 and v == self.gel_val[c]:
                    self.gel_nb[c] += 1
                else:
                    self.gel_val[c] = v
                    self.gel_nb[c] = 1
                    
                if self.gel_nb[c] >= GEL_N:
                    sortie[champ] = None

        # R9
        m = INDEX_MACHINE[evenement["machine_id"]]

        ecarts = {}
        if USE_GAP:
            for i, champ in enumerate(CHAMPS_MESURE):
                v = sortie[champ]
                if v is None:
                    ecarts[champ] = None
                    continue
                mu, n = self.mean[m][i][0], self.mean[m][i][1]
                ecarts[champ] = (v - mu) if n > 0 else 0.0
                if not sortie["retard"]:
                    self.mean[m][i][0] = (mu * n + v) / (n + 1)
                    self.mean[m][i][1] = n + INCR_MOYENNE

        valeurs = [sortie[c] for c in CHAMPS_STOCKE[1:]]
        if USE_GAP:
            valeurs += [ecarts[c] for c in CHAMPS_STOCKE[1:]]


        if sortie["arret"]:
            sortie["p"] = None
        else:
            sortie["p"] = self.predire(self._features(valeurs))

        self.purger(m, sortie["ts"])

        if not sortie["retard"]:
            if sortie["arret"]:
                self.vider_positifs(m)
            else:
                self.count[m] += 1
                if self.count[m] >= self.periode and all(v is not None for v in valeurs):
                    self.enqueue(m, sortie["ts"], valeurs)

        return sortie
        


    def enqueue(self, m, ts, valeurs):
        if self.nb_elt[m] >= self.queue_size:
            return
        
        k = (self.head[m] + self.nb_elt[m]) % self.queue_size
        self.queue[m][k][0] = ts

        for i, v in enumerate(valeurs):
            self.queue[m][k][i + 1] = v

        self.nb_elt[m] += 1
        self.count[m] = 0

        # if (m == 1):
        #     self.display_queue(m)


    def dequeue(self, m):
        h = self.head[m]

        for i in range(NB_FEATURES):
            self.queue[m][h][i] = 0.0

        self.head[m] = (h + 1) % self.queue_size
        self.nb_elt[m] -= 1


    def purger(self, m, ts):
        while (self.nb_elt[m] > 0 and ts >= self.queue[m][self.head[m]][0] + HORIZON_S * UNITE_TS):
            self.apprendre(self._features(self.queue[m][self.head[m]][1:]), 0)
            self.dequeue(m)


    def display_queue (self, id_machine):
        n=len(CHAMPS_STOCKE)
        if USE_GAP:
            n=2*n-1
        string = ""
        for absulute_index in range(self.queue_size):
            relative_index = (absulute_index + self.head[id_machine]) % self.queue_size
            string += "("
            for i in range(n):
                string += f"{self.queue[id_machine][relative_index][i]}"
                if i != n-1:
                    string += ", "
                else:
                    string += ") "
            if absulute_index != self.queue_size -1:
                string += "--> "
        print(string)


    def _features(self, valeurs):
        n = len(CHAMPS_STOCKE[1:])
        x = [1.0]

        for i, champ in enumerate(CHAMPS_STOCKE[1:]):
            lo, hi = BORNES[champ]
            v = valeurs[i]
            x.append(0.5 if v is None else (v - lo) / (hi - lo))
            if USE_GAP:
                g = valeurs[n + i]
                x.append(0.0 if g is None else g / (hi - lo))

        return x

    def predire(self, x):
        z = 0.0
        for i in range(len(self.weight)):
            z += self.weight[i] * x[i]
        return float(_sigmoide(z))

    def apprendre(self, x, y):
        p = self.predire(x)
        g = LR * (POIDS_POS if y else 1.0) * (p - y)
        for i in range(len(self.weight)):
            self.weight[i] -= g * x[i]

    def vider_positifs(self, m):
        while self.nb_elt[m] > 0:
            self.apprendre(self._features(self.queue[m][self.head[m]][1:]), 1)
            self.dequeue(m)


    def resume(self):
        """Renvoie un dict serialisable en JSON, avec au moins les compteurs
        entiers "doublons" (R1), "retards" (R2) et "pertes" (R3). Vous pouvez
        y ajouter tout ce qui vous aide a suivre votre traitement.
        """
        return {"doublons": int(self.nb_doublons),
                "retards": int(self.nb_retards),
                "pertes": int(self.nb_pertes)}