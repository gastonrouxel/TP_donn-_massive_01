# -*- coding: utf-8 -*-
"""Fixtures de la suite publique.

La suite rejoue des flux produits par le generateur en mode hors ligne, avec
une seed et une origine de temps fixees : deux executions voient exactement
les memes evenements. Elle passe les evenements directement a
`Traitement.traiter`, sans reseau, donc sans perte de transport, et compare
ce qui en sort a la verite terrain du generateur.

Les tests ne comparent jamais votre sortie a une sortie de reference : ils
verifient des proprietes, calculees sur votre sortie et sur la verite.

La suite publique rejoue la seed 20261001 ; la correction rejoue le meme code
sur d'autres seeds, inconnues (variable TP_SEEDS).
"""
import bisect
import importlib.util
import io
import json
import os
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import pytest

from src.annexe import (BUDGET_OCTETS, CHAMPS_MESURE, FW_FAHRENHEIT_DES,
                        HORIZON_S, INDEX_MACHINE, PLAFOND_HORS_MEMOIRE)
from src.memoire import Memoire

RACINE = Path(__file__).resolve().parent.parent
SEEDS = [int(s) for s in os.environ.get("TP_SEEDS", "20261001").split(",") if s.strip()]
DUREE_S = float(os.environ.get("TP_DUREE_REJEU_S", "3600"))   # secondes simulees
POINTS_CONTROLE = (0.25, 0.95)                                # parts du flux

# Un traitement qui ne respecte pas le contrat de memoire (tests 02 et 03)
# ne marque aucun point sur les tests de nettoyage et de prediction.
CONTRAT_MEMOIRE_BLOQUANT = True
CROISSANCE_HORS_COUCHE_MAX = 256          # octets, entre les deux controles
CROISSANCE_BLOCS_MAX = 20_000             # blocs alloues par Python, idem

C = {c: k for k, c in enumerate(CHAMPS_MESURE)}
CLES_SORTIE = {"machine_id", "seq", "ts", "retard", "arret", "p", *CHAMPS_MESURE}


# --------------------------------------------------------------- generateur

def charger_generateur():
    spec = importlib.util.spec_from_file_location(
        "generateur_tp", RACINE / "generateur" / "generateur.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = charger_generateur()

DEFAUTS_GENERATEUR = {
    "machines": 12, "frequence": 10.0, "periode_ambiante": 1200.0,
    "duree_regime": 180.0, "arret_min": 3.0, "arret_max": 8.0,
    "usure_par_heure": 0.10, "taux_panne": 1.0, "p_perte": 0.002,
    "p_doublon": 0.002, "p_retard": 0.003, "retard_max": 5.0,
    "p_aberrant": 0.001, "p_champ_absent": 0.001, "taux_gel": 2.0,
    "taux_firmware": 0.2, "gigue_ms": 2.0, "decalage_horloge_ms": 0.0,
}


def config(seed):
    """Configuration par defaut du generateur, seed imposee, independante
    des variables d'environnement GEN_*.
    """
    cfg = dict(GEN.CONFIG)
    cfg.update(DEFAUTS_GENERATEUR)
    cfg["seed"] = seed
    return cfg


# ------------------------------------------------------------------ mesures

def taille_hors_memoire(objet, memoire):
    """Octets de l'etat de `objet` qui ne vit pas dans la couche de donnees."""
    exclus = {id(memoire)} | {id(t) for t in memoire.tableaux()}

    class Pickler(pickle.Pickler):
        def persistent_id(self, obj):
            return id(obj) if id(obj) in exclus else None

    tampon = io.BytesIO()
    Pickler(tampon, protocol=pickle.HIGHEST_PROTOCOL).dump(objet)
    return tampon.tell()


def construire(budget=BUDGET_OCTETS):
    from src.pipeline import Traitement
    memoire = Memoire(budget)
    traitement = Traitement(memoire)
    memoire.verrouiller()
    return traitement, memoire


def fahrenheit(fw):
    try:
        return int(str(fw).split(".")[0]) >= FW_FAHRENHEIT_DES
    except ValueError:
        return False


def nombre(x):
    if x is None or isinstance(x, bool):
        return np.nan
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


# -------------------------------------------------------------------- rejeu

class Rejeu:
    """Un flux rejoue en entier : une ligne par evenement livre, la sortie du
    traitement d'un cote, la verite terrain de l'autre.
    """

    def __init__(self, seed, duree=DUREE_S, budget=BUDGET_OCTETS):
        cfg = config(seed)
        n_max = int(duree * cfg["machines"] * cfg["frequence"] * 1.02) + 1000
        nc = len(CHAMPS_MESURE)
        # sortie du traitement
        self.rejete = np.zeros(n_max, dtype=bool)
        self.sortie = np.full((n_max, nc), np.nan)
        self.s_retard = np.zeros(n_max, dtype=bool)
        self.s_arret = np.zeros(n_max, dtype=bool)
        self.p = np.full(n_max, np.nan)
        self.anomalies = []                  # ecarts de forme, au plus vingt
        self.n_anomalies = 0
        # verite terrain
        self.machine = np.full(n_max, -1, dtype=np.int16)
        self.seq = np.zeros(n_max, dtype=np.int64)
        self.t_ms = np.zeros(n_max, dtype=np.int64)
        self.doublon = np.zeros(n_max, dtype=bool)
        self.retard_obs = np.zeros(n_max, dtype=bool)
        self.arret = np.zeros(n_max, dtype=bool)
        self.fahr = np.zeros(n_max, dtype=bool)
        self.brut = np.full((n_max, nc), np.nan)      # valeur livree, en degC
        self.propre = np.full((n_max, nc), np.nan)    # valeur avant defaut
        self.defaut = np.zeros((n_max, nc), dtype=np.int8)   # 1 aberrant, 2 absent, 3 gel
        self.hors_memoire = []
        self.blocs = []
        self.resume = None

        traitement, memoire = construire(budget)
        self.memoire = memoire
        attendu = min(int(duree * cfg["machines"] * cfg["frequence"]), n_max)
        controles = [int(f * attendu) for f in POINTS_CONTROLE]
        seq_max = {}
        gen = GEN.iterer(cfg, duree, verite=True)
        n = 0
        self.sim = None
        while n < n_max:
            try:
                texte, v = next(gen)
            except StopIteration as fin:
                self.sim = fin.value
                break
            evenement = json.loads(texte)
            self._verite(n, evenement, v, seq_max)
            self._sortie(n, traitement.traiter(evenement))
            n += 1
            if n in controles:
                self.hors_memoire.append((n, taille_hors_memoire(traitement, memoire)))
                self.blocs.append(sys.getallocatedblocks())
        self.n = n
        for nom, val in list(vars(self).items()):
            if isinstance(val, np.ndarray) and val.shape[:1] == (n_max,):
                setattr(self, nom, val[:n])
        self.resume = traitement.resume()
        # seq jamais recus, entre 1 et le plus grand seq recu de chaque machine
        self.vraies_pertes = sum(mx - recus for mx, recus in seq_max.values())
        self.etiquette = self._etiquettes()

    def _verite(self, n, e, v, seq_max):
        i = INDEX_MACHINE[v["machine_id"]]
        s = v["seq"]
        self.machine[n], self.seq[n], self.t_ms[n] = i, s, v["t_ms"]
        self.doublon[n], self.arret[n] = v["doublon"], v["arret"]
        self.fahr[n] = fahrenheit(e.get("fw"))
        if not v["doublon"]:
            mx, recus = seq_max.setdefault(i, [0, 0])
            self.retard_obs[n] = s < mx
            seq_max[i] = [max(mx, s), recus + 1]
        for c, k in C.items():
            x = nombre(e.get(c))
            if c == "temperature" and self.fahr[n] and not np.isnan(x):
                x = (x - 32.0) * 5.0 / 9.0
            self.brut[n, k] = x
            if v["propre"] is not None:
                self.propre[n, k] = v["propre"][c]
        for code, cle in ((1, "aberrant"), (2, "absent"), (3, "gel")):
            if v[cle] is not None:
                self.defaut[n, C[v[cle]]] = code

    def _anomalie(self, n, texte):
        self.n_anomalies += 1
        if len(self.anomalies) < 20:
            self.anomalies.append("evenement %d : %s" % (n, texte))

    def _sortie(self, n, s):
        if s is None:
            self.rejete[n] = True
            return
        if not isinstance(s, dict):
            self._anomalie(n, "sortie de type %s" % type(s).__name__)
            return
        manquantes = CLES_SORTIE - set(s)
        if manquantes:
            self._anomalie(n, "cles manquantes %s" % sorted(manquantes))
        for c, k in C.items():
            x = s.get(c)
            if x is not None and not isinstance(x, (int, float, np.integer, np.floating)):
                self._anomalie(n, "%s n'est ni un nombre ni None" % c)
            else:
                self.sortie[n, k] = nombre(x)
        self.s_retard[n] = bool(s.get("retard"))
        self.s_arret[n] = bool(s.get("arret"))
        p = s.get("p")
        if p is not None:
            if isinstance(p, (int, float, np.floating)) and not isinstance(p, bool) \
                    and 0.0 <= float(p) <= 1.0:
                self.p[n] = float(p)
            else:
                self._anomalie(n, "p = %r n'est pas une probabilite" % (p,))

    def _etiquettes(self):
        """Vrai si une coupure de la machine debute dans les HORIZON_S
        secondes qui suivent l'evenement. Calculee apres coup, sur la verite.
        """
        debuts = {}
        if self.sim is not None:
            for m, liste in self.sim.coupures().items():
                debuts[INDEX_MACHINE[m]] = [d for d, _ in liste]
        h = int(HORIZON_S * 1000)
        y = np.zeros(self.n, dtype=bool)
        for i in range(self.n):
            d = debuts.get(int(self.machine[i]), [])
            j = bisect.bisect_right(d, int(self.t_ms[i]))
            y[i] = j < len(d) and d[j] <= self.t_ms[i] + h
        return y

    # ------------------------------------------------ contrat de memoire
    def contrat_memoire(self):
        """Liste des manquements au contrat de memoire (vide si respecte)."""
        fautes = []
        pire = max(o for _, o in self.hors_memoire)
        if pire > PLAFOND_HORS_MEMOIRE:
            fautes.append("%d octets d'etat hors de la couche de donnees (plafond %d)"
                          % (pire, PLAFOND_HORS_MEMOIRE))
        (n1, o1), (n2, o2) = self.hors_memoire
        if o2 - o1 > CROISSANCE_HORS_COUCHE_MAX:
            fautes.append("l'etat hors couche passe de %d a %d octets entre %d et %d "
                          "evenements" % (o1, o2, n1, n2))
        if self.blocs[1] - self.blocs[0] > CROISSANCE_BLOCS_MAX:
            fautes.append("%d objets Python de plus entre %d et %d evenements"
                          % (self.blocs[1] - self.blocs[0], n1, n2))
        return fautes


@pytest.fixture(scope="session", params=SEEDS, ids=lambda s: "seed%d" % s)
def rejeu(request):
    return Rejeu(request.param)


def exiger_contrat(rejeu):
    """A appeler en tete des tests de nettoyage et de prediction."""
    if CONTRAT_MEMOIRE_BLOQUANT:
        fautes = rejeu.contrat_memoire()
        if fautes:
            pytest.fail("contrat de memoire non respecte : " + " ; ".join(fautes),
                        pytrace=False)


# ---------------------------------------------------------------------- note

# Points par test, sur 100. Un test vaut ses points s'il passe sur toutes les
# seeds rejouees, zero sinon. Un test non execute vaut zero.
POIDS = {
    "test_01": 5, "test_02": 5, "test_03": 5,
    "test_04": 8, "test_05": 6, "test_06": 6, "test_07": 4, "test_08": 8,
    "test_09": 8, "test_10": 8, "test_11": 5, "test_12": 8,
    "test_13": 4, "test_14": 12, "test_15": 8,
}


def _numero(rapport):
    m = re.search(r"::(test_\d\d)_", rapport.nodeid)
    return m.group(1) if m else None


def _afficher_note(config):
    tr = config.pluginmanager.get_plugin("terminalreporter")
    reussis, rates = set(), set()
    for rep in tr.stats.get("passed", []):
        if rep.when == "call":
            reussis.add(_numero(rep))
    for cle in ("failed", "error"):
        for rep in tr.stats.get(cle, []):
            rates.add(_numero(rep))
    reussis = (reussis - rates) & set(POIDS)
    note = sum(POIDS[t] for t in reussis)
    total = sum(POIDS.values())
    tr.write_sep("=", "NOTE", bold=True)
    for t in sorted(set(POIDS) - reussis):
        tr.write_line("  %s : 0 / %d" % (t, POIDS[t]))
    tr.write_line("Tests reussis : %d / %d" % (len(reussis), len(POIDS)))
    tr.write_line("Note : %d / %d" % (note, total), bold=True)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Imprime la note apres le bilan de pytest, donc en toute derniere ligne."""
    resultat = yield
    try:
        _afficher_note(session.config)
    except Exception as e:  # la note ne doit jamais masquer le resultat des tests
        print("Note non calculee : %r" % (e,))
    return resultat
