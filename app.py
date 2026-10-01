# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Point d'entree du consommateur. Fourni, ne pas modifier.

    docker compose up --build

Cree la couche de donnees a budget fixe, construit `Traitement`, verrouille
la couche, puis passe chaque evenement du flux a `Traitement.traiter`. Toutes
les TP_RAPPORT_S secondes, affiche le debit recu, les pertes de transport, ce
que la chaine a transmis et rejete, et le resume renvoye par
`Traitement.resume`. S'arrete apres TP_DUREE secondes (0 : sans fin, Ctrl+C
pour arreter).

Lance par docker compose (service consommateur), qui fixe FLUX_URL a
http://generateur:8000/stream. La note, elle, est calculee par la suite de
tests (README, section 8), pas par ce programme.
"""
import json
import math
import os
import signal
import sys
import time

from src.annexe import BUDGET_OCTETS
from src.flux import Flux
from src.memoire import Memoire
from src.pipeline import Traitement

URL = os.environ.get("FLUX_URL", "http://generateur:8000/stream")
DUREE = float(os.environ.get("TP_DUREE", "0") or 0)
PERIODE = float(os.environ.get("TP_RAPPORT_S", "5") or 5)


class Compteurs:
    def __init__(self):
        self.transmis = self.rejetes = self.arrets = 0
        self.predictions = self.invalides = 0
        self.somme_p = 0.0

    def compter(self, sortie):
        if sortie is None:
            self.rejetes += 1
            return
        if not isinstance(sortie, dict):
            self.invalides += 1
            return
        self.transmis += 1
        self.arrets += bool(sortie.get("arret"))
        p = sortie.get("p")
        if p is None:
            return
        if isinstance(p, (int, float)) and not math.isnan(p) and 0.0 <= p <= 1.0:
            self.predictions += 1
            self.somme_p += p
        else:
            self.invalides += 1


def rapport(flux, traitement, c, debut, precedent):
    maintenant = time.monotonic()
    ecoule = maintenant - debut
    debit = (flux.recus - precedent[1]) / max(maintenant - precedent[0], 1e-9)
    moyenne = c.somme_p / c.predictions if c.predictions else float("nan")
    print("[%7.1f s] recus %d | %.0f evt/s | perdus au transport %d | transmis %d | "
          "rejetes %d | arrets %d | predictions %d (p moyen %.3f) | sorties invalides %d"
          % (ecoule, flux.recus, debit, flux.perdus_transport, c.transmis, c.rejetes,
             c.arrets, c.predictions, moyenne, c.invalides), flush=True)
    try:
        print(json.dumps(traitement.resume(), ensure_ascii=False, default=str), flush=True)
    except NotImplementedError as e:
        print("resume non implemente :", e, flush=True)
    return maintenant, flux.recus


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    memoire = Memoire(BUDGET_OCTETS)
    try:
        traitement = Traitement(memoire)
    except NotImplementedError as e:
        print("Fonction non implementee :", e, flush=True)
        sys.exit(1)
    memoire.verrouiller()
    print("consommateur : memoire allouee %d / %d octets" % (memoire.utilise, memoire.budget),
          flush=True)
    for nom, (type_, forme, octets) in memoire.inventaire().items():
        print("  %-24s %-4s %-14s %8d octets" % (nom, type_, forme, octets), flush=True)
    flux = Flux(URL)
    compteurs = Compteurs()
    print("consommateur : connexion a %s" % URL, flush=True)
    debut = time.monotonic()
    precedent = (debut, 0)
    prochain = debut + PERIODE
    try:
        for evenement in flux:
            compteurs.compter(traitement.traiter(evenement))
            if flux.recus & 0x3F == 0:
                maintenant = time.monotonic()
                if maintenant >= prochain:
                    precedent = rapport(flux, traitement, compteurs, debut, precedent)
                    prochain = maintenant + PERIODE
                if DUREE and maintenant - debut >= DUREE:
                    break
    except NotImplementedError as e:
        print("Fonction non implementee :", e, flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    print("consommateur : arret (debit moyen sur la session)", flush=True)
    rapport(flux, traitement, compteurs, debut, (debut, 0))


if __name__ == "__main__":
    main()
