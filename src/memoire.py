# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Couche de donnees a budget fixe. Fourni, ne pas modifier.

Tout etat de `Traitement` qui depend du flux doit vivre dans des tableaux
alloues par cette couche. Le controleur (app.py, la suite de tests) cree la
couche avec un budget en octets, la passe au constructeur de `Traitement`,
puis la verrouille : toutes les allocations ont donc lieu dans `__init__`,
une fois pour toutes, avant le premier evenement.

    def __init__(self, memoire):
        self.vus = memoire.allouer("doublons", 400_000, "bit")
        self.cms = memoire.allouer("count_min", (5, 2048), "i32")
        self.w = memoire.allouer("poids", 7, "f64")

Types disponibles et cout par cellule :

    "bit"  1 bit      (objet Bits, voir plus bas)
    "u8"   1 octet    entier de 0 a 255
    "i32"  4 octets   entier signe
    "i64"  8 octets   entier signe
    "f64"  8 octets   flottant

Les tableaux numeriques sont des numpy.ndarray de forme fixe, initialises a
zero ; on lit et on ecrit leurs cases, on ne les agrandit pas. Une allocation
qui depasse le budget leve MemoireEpuisee ; une allocation apres verrouillage
leve MemoireVerrouillee.
"""
import math

import numpy as np

TAILLES_BITS = {"bit": 1, "u8": 8, "i32": 32, "i64": 64, "f64": 64}
_DTYPES = {"u8": np.uint8, "i32": np.int32, "i64": np.int64, "f64": np.float64}


class MemoireEpuisee(MemoryError):
    pass


class MemoireVerrouillee(RuntimeError):
    pass


class Bits:
    """Tableau de bits de taille fixe, initialise a zero."""

    __slots__ = ("_n", "_octets")

    def __init__(self, n):
        self._n = n
        self._octets = np.zeros((n + 7) // 8, dtype=np.uint8)

    def __len__(self):
        return self._n

    def _verifier(self, i):
        if not 0 <= i < self._n:
            raise IndexError("bit %d hors du tableau de %d bits" % (i, self._n))

    def __getitem__(self, i):
        self._verifier(i)
        return bool(self._octets[i >> 3] & (1 << (i & 7)))

    def __setitem__(self, i, valeur):
        self._verifier(i)
        if valeur:
            self._octets[i >> 3] |= np.uint8(1 << (i & 7))
        else:
            self._octets[i >> 3] &= np.uint8(~(1 << (i & 7)) & 0xFF)

    def compter(self):
        """Nombre de bits a un."""
        return int(np.unpackbits(self._octets)[: self._n].sum())

    def effacer(self):
        """Remet tous les bits a zero."""
        self._octets[:] = 0


class Memoire:
    def __init__(self, budget_octets):
        self.budget = int(budget_octets)
        self._bits_utilises = 0
        self._tableaux = {}
        self._verrouillee = False

    @property
    def utilise(self):
        """Octets alloues, arrondis a l'octet superieur."""
        return int(math.ceil(self._bits_utilises / 8))

    @property
    def restant(self):
        return self.budget - self.utilise

    def allouer(self, nom, forme, type_="f64"):
        """Alloue un tableau nomme de forme fixe (entier ou tuple d'entiers)."""
        if self._verrouillee:
            raise MemoireVerrouillee(
                "allocation de %r apres le premier evenement : toutes les "
                "allocations se font dans Traitement.__init__" % (nom,))
        if nom in self._tableaux:
            raise ValueError("le tableau %r existe deja" % (nom,))
        if type_ not in TAILLES_BITS:
            raise ValueError("type %r inconnu ; types possibles : %s"
                             % (type_, ", ".join(TAILLES_BITS)))
        forme = (forme,) if isinstance(forme, int) else tuple(forme)
        if not forme or any((not isinstance(k, int)) or k <= 0 for k in forme):
            raise ValueError("forme invalide pour %r : %r" % (nom, forme))
        if type_ == "bit" and len(forme) != 1:
            raise ValueError("un tableau de bits est a une dimension")
        cellules = math.prod(forme)
        cout = cellules * TAILLES_BITS[type_]
        if self._bits_utilises + cout > 8 * self.budget:
            raise MemoireEpuisee(
                "%r demande %d octets ; il en reste %d sur un budget de %d"
                % (nom, math.ceil(cout / 8), self.restant, self.budget))
        self._bits_utilises += cout
        tableau = Bits(cellules) if type_ == "bit" else np.zeros(forme, dtype=_DTYPES[type_])
        self._tableaux[nom] = (tableau, type_, forme, int(math.ceil(cout / 8)))
        return tableau

    def verrouiller(self):
        """Appele par le controleur apres la construction de Traitement."""
        self._verrouillee = True

    def inventaire(self):
        """{nom: (type, forme, octets)} pour chaque tableau alloue."""
        return {nom: (t, f, o) for nom, (_, t, f, o) in self._tableaux.items()}

    def tableaux(self):
        """Les objets alloues, pour les controles de la suite de tests."""
        return [t for t, _, _, _ in self._tableaux.values()]
