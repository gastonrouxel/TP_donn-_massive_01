# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Suite publique.

Chaque test nomme la propriete qu'il verifie, jamais le defaut qui la met en
defaut. Un echec vous indique donc ou regarder, pas quoi corriger.

La note est le resultat de cette suite, rejouee a l'echeance sur des seeds
que vous ne connaissez pas.
"""
import numpy as np
import pytest

from src.annexe import (BORNES, BUDGET_OCTETS, CHAMPS_GEL, CHAMPS_MESURE,
                        PLANCHER_F1, SEUIL_DECISION)
from src.memoire import MemoireVerrouillee
from tests.conftest import (C, CROISSANCE_BLOCS_MAX,
                            CROISSANCE_HORS_COUCHE_MAX, PLAFOND_HORS_MEMOIRE,
                            SEEDS, Rejeu, construire, exiger_contrat)

# Tolerances : un traitement qui applique le reglement les tient toutes avec
# une large marge ; elles absorbent les cas limites, pas les regles omises.
DOUBLONS_MANQUES_MAX = 0.01       # part des doublons conserves
FAUX_REJETS_MAX = 0.0005          # part des evenements uniques rejetes
RETARDS_ECARTS_MAX = 0.0005       # part des evenements mal marques
PERTES_ECART_MAX = 0.02           # ecart relatif sur le nombre de pertes
ECARTS_MAX = 0.001                # part des cellules fautives, tests 08, 11, 12
GELS_NEUTRALISES_MIN = 0.75       # part des cellules figees rendues manquantes
COUVERTURE_MIN = 0.99             # part des evenements evaluables predits


# ----------------------------------------------------------- contrat

def test_01_interface(rejeu):
    """Traitement se construit dans le budget, traite tout le flux et rend
    des sorties de la forme imposee.
    """
    _, memoire = construire()
    assert memoire.utilise <= BUDGET_OCTETS
    with pytest.raises(MemoireVerrouillee):
        memoire.allouer("apres_coup", 1, "u8")
    assert rejeu.n_anomalies == 0, \
        "%d sorties mal formees, dont : %s" % (rejeu.n_anomalies, rejeu.anomalies[:3])
    assert isinstance(rejeu.resume, dict), "resume doit renvoyer un dict"
    for cle in ("doublons", "retards", "pertes"):
        assert isinstance(rejeu.resume.get(cle), int), "resume()['%s'] doit etre un entier" % cle


def test_02_etat_hors_couche(rejeu):
    """L'etat tenu hors de la couche de donnees est borne et constant."""
    pire = max(o for _, o in rejeu.hors_memoire)
    assert pire <= PLAFOND_HORS_MEMOIRE, \
        "%d octets d'etat hors de la couche de donnees (plafond %d)" % (pire, PLAFOND_HORS_MEMOIRE)
    (n1, o1), (n2, o2) = rejeu.hors_memoire
    assert o2 - o1 <= CROISSANCE_HORS_COUCHE_MAX, \
        "l'etat hors couche passe de %d a %d octets entre %d et %d evenements" % (o1, o2, n1, n2)


def test_03_memoire_python_stable(rejeu):
    """Aucune structure cachee ne grandit avec le flux."""
    croissance = rejeu.blocs[1] - rejeu.blocs[0]
    assert croissance <= CROISSANCE_BLOCS_MAX, \
        "%d objets Python de plus entre 25 %% et 95 %% du flux" % croissance


# --------------------------------------------------------- nettoyage

def _garde(rejeu):
    """Evenements conserves par le traitement."""
    return ~rejeu.rejete


def test_04_unicite(rejeu):
    """Chaque prelevement est transmis une fois et une seule."""
    exiger_contrat(rejeu)
    d = rejeu.doublon
    manques = int((d & ~rejeu.rejete).sum())
    faux = int((~d & rejeu.rejete).sum())
    assert manques <= DOUBLONS_MANQUES_MAX * max(int(d.sum()), 1), \
        "%d doublons transmis sur %d" % (manques, int(d.sum()))
    assert faux <= FAUX_REJETS_MAX * int((~d).sum()), \
        "%d evenements uniques rejetes" % faux


def test_05_ordre_d_arrivee(rejeu):
    """Un evenement arrive apres un plus recent de sa machine est signale."""
    exiger_contrat(rejeu)
    g = _garde(rejeu) & ~rejeu.doublon
    ecarts = int((rejeu.s_retard[g] != rejeu.retard_obs[g]).sum())
    assert ecarts <= RETARDS_ECARTS_MAX * int(g.sum()), \
        "%d evenements mal marques sur %d (%d en retard)" % (
            ecarts, int(g.sum()), int(rejeu.retard_obs[g].sum()))
    compte = rejeu.resume.get("retards")
    vrai = int(rejeu.retard_obs[~rejeu.doublon].sum())
    assert compte is not None and abs(compte - vrai) <= max(3, 0.02 * vrai), \
        "resume()['retards'] vaut %r pour %d" % (compte, vrai)


def test_06_pertes(rejeu):
    """Le nombre d'evenements jamais recus est connu."""
    exiger_contrat(rejeu)
    vrai = rejeu.vraies_pertes
    compte = rejeu.resume.get("pertes")
    assert compte is not None and abs(compte - vrai) <= max(3, PERTES_ECART_MAX * vrai), \
        "resume()['pertes'] vaut %r pour %d evenements jamais recus" % (compte, vrai)
    compte = rejeu.resume.get("doublons")
    vrai = int(rejeu.doublon.sum())
    assert compte is not None and abs(compte - vrai) <= max(3, 0.02 * vrai), \
        "resume()['doublons'] vaut %r pour %d" % (compte, vrai)


def test_07_forme_complete(rejeu):
    """Chaque evenement transmis porte toutes ses mesures, manquantes comprises."""
    exiger_contrat(rejeu)
    g = _garde(rejeu)
    absents = (rejeu.defaut == 2) & g[:, None]
    # une mesure absente du message doit ressortir manquante, jamais inventee
    inventees = int((absents & ~np.isnan(rejeu.sortie)).sum())
    assert inventees == 0, "%d mesures absentes du message ressortent avec une valeur" % inventees


def test_08_une_grandeur_une_unite(rejeu):
    """Toutes les temperatures transmises sont en degres Celsius."""
    exiger_contrat(rejeu)
    k = C["temperature"]
    g = _garde(rejeu) & ~rejeu.arret & (rejeu.defaut[:, k] == 0) & ~np.isnan(rejeu.sortie[:, k])
    ecart = np.abs(rejeu.sortie[g, k] - rejeu.propre[g, k])
    fautes = int((ecart > 0.11).sum())
    assert fautes <= ECARTS_MAX * int(g.sum()), \
        "%d temperatures s'ecartent de plus de 0,1 degre de la valeur en Celsius" % fautes
    f = g & rejeu.fahr
    assert int(f.sum()) == 0 or int((np.abs(rejeu.sortie[f, k] - rejeu.propre[f, k]) > 0.11).sum()) == 0, \
        "des temperatures restent dans l'unite d'origine"


def test_09_plausibilite_physique(rejeu):
    """Aucune mesure transmise n'est hors de son domaine physique."""
    exiger_contrat(rejeu)
    g = _garde(rejeu)
    for c, (lo, hi) in BORNES.items():
        v = rejeu.sortie[g, C[c]]
        v = v[~np.isnan(v)]
        hors = int(((v < lo) | (v > hi)).sum())
        assert hors == 0, "%d valeurs de %s hors de [%g, %g]" % (hors, c, lo, hi)


def test_10_capteurs_figes(rejeu):
    """Une valeur figee par le capteur n'est pas transmise comme une mesure."""
    exiger_contrat(rejeu)
    g = _garde(rejeu) & ~rejeu.arret
    figees = neutralisees = 0
    for c in CHAMPS_GEL:
        k = C[c]
        m = g & (rejeu.defaut[:, k] == 3)
        figees += int(m.sum())
        neutralisees += int(np.isnan(rejeu.sortie[m, k]).sum())
    assert figees > 0
    assert neutralisees >= GELS_NEUTRALISES_MIN * figees, \
        "%d valeurs figees neutralisees sur %d" % (neutralisees, figees)


def test_11_arrets(rejeu):
    """Une machine a l'arret est signalee comme telle, sans mesure."""
    exiger_contrat(rejeu)
    g = _garde(rejeu) & ~rejeu.doublon
    ecarts = int((rejeu.s_arret[g] != rejeu.arret[g]).sum())
    assert ecarts <= ECARTS_MAX * int(g.sum()), \
        "%d evenements dont l'etat de marche est mal signale" % ecarts
    a = g & rejeu.arret
    assert int((~np.isnan(rejeu.sortie[a])).sum()) == 0, \
        "des evenements a l'arret portent des mesures"


def test_12_mesures_legitimes_preservees(rejeu):
    """Aucune mesure legitime n'est perdue ni modifiee."""
    exiger_contrat(rejeu)
    g = _garde(rejeu) & ~rejeu.arret
    total = fautes = 0
    for c, k in C.items():
        m = g & (rejeu.defaut[:, k] == 0)
        total += int(m.sum())
        tol = 0.11 if c == "temperature" else 1e-6
        ok = np.abs(rejeu.sortie[m, k] - rejeu.propre[m, k]) <= tol
        fautes += int((~ok).sum())
    assert fautes <= ECARTS_MAX * total, \
        "%d mesures legitimes perdues ou modifiees sur %d" % (fautes, total)


# -------------------------------------------------------- prediction

def _evaluables(rejeu):
    return _garde(rejeu) & ~rejeu.doublon & ~rejeu.arret & ~rejeu.retard_obs


def test_13_couverture_des_predictions(rejeu):
    """Une prediction est emise pour chaque machine en marche."""
    exiger_contrat(rejeu)
    e = _evaluables(rejeu)
    emises = int((~np.isnan(rejeu.p[e])).sum())
    assert emises >= COUVERTURE_MIN * int(e.sum()), \
        "%d predictions emises pour %d evenements en marche" % (emises, int(e.sum()))


def score_f1(rejeu):
    e = _evaluables(rejeu)
    e[: rejeu.n // 2] = False                       # seconde moitie du flux
    y = rejeu.etiquette[e]
    p = np.nan_to_num(rejeu.p[e], nan=0.0) >= SEUIL_DECISION
    vp = int((p & y).sum())
    precision = vp / max(int(p.sum()), 1)
    rappel = vp / max(int(y.sum()), 1)
    f1 = 2 * precision * rappel / max(precision + rappel, 1e-12)
    return f1, precision, rappel


def test_14_detection_des_coupures(rejeu):
    """Les coupures sont annoncees avant de survenir."""
    exiger_contrat(rejeu)
    f1, precision, rappel = score_f1(rejeu)
    assert f1 >= PLANCHER_F1, \
        "F1 = %.3f (precision %.3f, rappel %.3f), plancher %.2f" % (f1, precision, rappel, PLANCHER_F1)


def test_15_determinisme():
    """Deux executions sur le meme flux donnent les memes sorties."""
    a = Rejeu(SEEDS[0], duree=300)
    b = Rejeu(SEEDS[0], duree=300)
    assert np.array_equal(a.rejete, b.rejete)
    assert np.array_equal(a.sortie, b.sortie, equal_nan=True)
    assert np.array_equal(a.p, b.p, equal_nan=True), \
        "%d predictions different" % int((~np.isclose(a.p, b.p, equal_nan=True)).sum())
