# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Generateur de flux. Fourni, ne pas modifier.

Simule un parc de machines industrielles instrumentees et diffuse leurs
mesures en Server-Sent Events (SSE) sur HTTP, bibliotheque standard seule.

    GET /stream   flux SSE, un evenement par mesure
    GET /health   sonde de disponibilite
    GET /info     configuration publique (debit, nombre de machines, champs)

Chaque evenement SSE porte un identifiant de transport (`id:`), attribue a la
diffusion, et une charge JSON (`data:`) :

    {"machine_id": "M-03", "seq": 18342, "ts": 1790000000123, "fw": "1.4.2",
     "temperature": 81.4, "vibration": 2.31, "pressure": 4.12, "rpm": 1502,
     "power": 37.8}

`seq` est le compteur de l'equipement, `ts` l'horodatage de l'equipement en
millisecondes epoch, en temps simule. Les mesures valent null lorsque la
machine est a l'arret.

Temps simule. Chaque machine est echantillonnee GEN_FREQUENCE_HZ fois par
seconde de temps simule. Le debit GEN_DEBIT fixe donc la vitesse du temps
simule : acceleration = GEN_DEBIT / (GEN_MACHINES * GEN_FREQUENCE_HZ). Avec
12 machines a 10 Hz, 120 evt/s est le temps reel et 1200 evt/s fait passer
dix secondes de procede par seconde d'horloge. `ts` part de l'heure du
lancement et avance a cette vitesse.

Le flux est une diffusion : la simulation avance qu'un client soit connecte
ou non, et chaque client recoit le flux a partir du moment ou il se connecte. Un client trop lent pour suivre le debit perd des
paquets de transport (trous dans `id:`), comme sur un vrai bus de messages.

Mode hors ligne : simule N secondes sans attendre, a partir d'une origine de
temps fixe (ORIGINE_EXPORT). Avec GEN_SEED fixe, la sortie est entierement
deterministe ; c'est le mode qu'utilise la correction.

    GEN_SEED=7 python generateur.py --exporter 3600 > flux.jsonl
    GEN_SEED=7 python generateur.py --exporter 3600 dossier/

Avec un dossier, le generateur ecrit aussi la verite terrain : verite.jsonl
(defauts injectes et valeurs propres, une ligne par evenement livre),
coupures.json (debut et fin de chaque arret) et meta.json. La fonction
iterer() produit le meme flux en memoire, sans passer par le disque.

Toute la configuration passe par des variables d'environnement (voir CONFIG).
"""
import asyncio
import heapq
import json
import math
import os
import random
import signal
import socket
import sys
import time


# ------------------------------------------------------------ configuration

def _env(nom, defaut, conv=float):
    brut = os.environ.get(nom, "")
    if brut.strip() == "":
        return defaut
    try:
        return conv(brut)
    except ValueError:
        sys.exit("Variable %s invalide : %r" % (nom, brut))


CONFIG = {
    # transport et debit
    "port": _env("GEN_PORT_INTERNE", 8000, int),
    "debit": _env("GEN_DEBIT", 1200.0),               # evenements par seconde reelle, total
    "machines": _env("GEN_MACHINES", 12, int),
    "frequence": _env("GEN_FREQUENCE_HZ", 10.0),       # echantillons par machine et par seconde simulee
    "seed": _env("GEN_SEED", None, int),           # vide : aleatoire a chaque lancement
    "tick": _env("GEN_TICK_MS", 20.0) / 1000.0,        # granularite d'emission
    "tampon_client": _env("GEN_TAMPON_CLIENT_S", 5.0), # secondes de flux en attente par client
    "journal": _env("GEN_JOURNAL_S", 10.0),            # periode du journal d'activite (0 : muet)
    # dynamique du procede
    "periode_ambiante": _env("GEN_PERIODE_AMBIANTE_S", 1200.0),
    "duree_regime": _env("GEN_DUREE_REGIME_S", 180.0),
    "arret_min": _env("GEN_ARRET_MIN_S", 3.0),
    "arret_max": _env("GEN_ARRET_MAX_S", 8.0),
    "usure_par_heure": _env("GEN_USURE_PAR_HEURE", 0.10),
    "taux_panne": _env("GEN_TAUX_PANNE_PAR_HEURE", 1.0),       # episodes de vibration anormale
    # defauts de donnees
    "p_perte": _env("GEN_P_PERTE", 0.002),             # evenement jamais emis
    "p_doublon": _env("GEN_P_DOUBLON", 0.002),         # evenement emis deux fois
    "p_retard": _env("GEN_P_RETARD", 0.003),           # evenement livre en retard
    "retard_max": _env("GEN_RETARD_MAX_S", 5.0),
    "p_aberrant": _env("GEN_P_ABERRANT", 0.001),       # valeur physiquement impossible
    "p_champ_absent": _env("GEN_P_CHAMP_ABSENT", 0.001),
    "taux_gel": _env("GEN_TAUX_GEL_PAR_HEURE", 2.0),   # capteur fige, par machine
    "taux_firmware": _env("GEN_TAUX_FIRMWARE_PAR_HEURE", 0.2),  # mise a jour, par machine
    "gigue_ms": _env("GEN_GIGUE_MS", 2.0),
    "decalage_horloge_ms": _env("GEN_DECALAGE_HORLOGE_MS", 0.0),
}

CHAMPS = ["temperature", "vibration", "pressure", "rpm", "power"]
UNITES = {"temperature": "degC", "vibration": "mm/s", "pressure": "bar",
          "rpm": "tr/min", "power": "kW"}

# Regimes de charge (fraction de la vitesse nominale) et leurs poids.
REGIMES = [0.6, 0.8, 1.0, 1.15]
POIDS_REGIMES = [0.15, 0.30, 0.35, 0.20]

ABERRANTS = {
    "temperature": [-273.2, 850.0, 0.0],
    "vibration": [-1.0, 99.99],
    "pressure": [0.0, 655.35],
    "rpm": [65535, -1],
    "power": [0.0, 6553.5],
}


def acceleration(cfg):
    """Secondes de temps simule par seconde d'horloge."""
    return cfg["debit"] / (cfg["machines"] * cfg["frequence"])


def _lisse(dt, tau):
    """Gain d'un systeme du premier ordre sur un pas dt."""
    return 1.0 - math.exp(-dt / tau)


# ----------------------------------------------------------------- machine

class Machine:
    """Une machine : thermique du premier ordre, regimes de charge, arrets
    sur surchauffe, usure lente, episodes de vibration, capteurs imparfaits.
    """

    def __init__(self, indice, rng, cfg, t0):
        self.rng = rng
        self.cfg = cfg
        self.id = "M-%02d" % (indice + 1)
        self.seq = 0
        self.t = t0
        self.t_debut = t0
        # constantes propres a la machine
        self.rpm_nom = rng.uniform(1400, 1600)
        self.k_chauffe = rng.uniform(80, 95)        # degC au-dessus de l'ambiante a pleine charge
        self.tau_th = rng.uniform(30, 50)
        self.tau_refroid = rng.uniform(12, 20)
        self.t_coupure = rng.uniform(93, 97)
        self.p_nom = rng.uniform(4.5, 5.5)
        self.w_nom = rng.uniform(35, 45)
        self.offset_amb = rng.uniform(-1.5, 1.5)
        self.usure = rng.uniform(0.0, 0.3)
        self.vitesse_usure = cfg["usure_par_heure"] * rng.uniform(0.5, 1.5)
        self.decalage = rng.uniform(-1, 1) * cfg["decalage_horloge_ms"] / 1000.0
        # etat initial dephase
        self.charge = rng.choices(REGIMES, POIDS_REGIMES)[0]
        self.t_regime = t0 + rng.expovariate(1.0 / cfg["duree_regime"])
        self.rpm = self.rpm_nom * self.charge
        amb = self.ambiante(t0)
        self.T = rng.uniform(amb + 10, self.t_coupure - 5)
        self.marche = True
        self.t_redemarrage = 0.0
        self.panne_fin = 0.0
        self.panne_amp = 0.0
        self.gel = None                           # (champ, valeur, fin)
        self.fw = rng.choice(["1.4.2", "1.4.3"])
        self.fahrenheit = False
        self.coupures = []                        # [debut, fin] en secondes simulees
        taux_fw = cfg["taux_firmware"]
        self.t_firmware = (t0 + rng.expovariate(taux_fw / 3600.0)
                           if taux_fw > 0 else math.inf)

    def ambiante(self, t):
        # Horloge murale absolue : la phase depend du moment du lancement.
        P = self.cfg["periode_ambiante"]
        return 22.0 + 6.0 * math.sin(2 * math.pi * t / P) + self.offset_amb

    def _evenements(self, t, dt):
        rng, cfg = self.rng, self.cfg
        if t >= self.t_regime:
            self.charge = rng.choices(REGIMES, POIDS_REGIMES)[0]
            self.t_regime = t + rng.expovariate(1.0 / cfg["duree_regime"])
        if t >= self.t_firmware:
            self.fw, self.fahrenheit, self.t_firmware = "2.0.1", True, math.inf
        if cfg["taux_panne"] > 0 and t >= self.panne_fin:
            self.panne_amp = 0.0
            if rng.random() < dt * cfg["taux_panne"] / 3600.0:
                self.panne_fin = t + rng.uniform(20, 60)
                self.panne_amp = rng.uniform(1.5, 4.0)
        if self.gel is not None and t >= self.gel[2]:
            self.gel = None
        if (self.gel is None and cfg["taux_gel"] > 0
                and rng.random() < dt * cfg["taux_gel"] / 3600.0):
            self.gel = (rng.choice(["temperature", "vibration", "pressure"]),
                        None, t + rng.uniform(5, 30))

    def _physique(self, t, dt):
        amb = self.ambiante(t)
        self.usure += self.vitesse_usure * dt / 3600.0
        if self.marche:
            consigne = self.rpm_nom * self.charge
            self.rpm += (consigne - self.rpm) * _lisse(dt, 2.0)
            r = self.rpm / self.rpm_nom
            T_eq = amb + self.k_chauffe * r * r * (1 + 0.5 * self.usure)
            self.T += (T_eq - self.T) * _lisse(dt, self.tau_th)
            if self.T >= self.t_coupure:
                self.marche = False
                self.t_redemarrage = t + self.rng.uniform(self.cfg["arret_min"],
                                                          self.cfg["arret_max"])
                self.coupures.append([t, None])
        else:
            self.rpm += (0.0 - self.rpm) * _lisse(dt, 1.5)
            self.T += (amb - self.T) * _lisse(dt, self.tau_refroid)
            if t >= self.t_redemarrage:
                self.marche = True
                if self.coupures and self.coupures[-1][1] is None:
                    self.coupures[-1][1] = t

    def _mesures(self):
        g = self.rng.gauss
        r = self.rpm / self.rpm_nom
        T = self.T
        temp = T + g(0, 0.3)
        self.temp_c = round(temp, 1)              # valeur propre, pour la verite terrain
        if self.fahrenheit:
            temp = temp * 9.0 / 5.0 + 32.0
        vib = ((0.8 + 1.6 * r * r) * (1 + 2 * self.usure)
               + 0.015 * max(T - 60, 0) + self.panne_amp + g(0, 0.12))
        pres = self.p_nom * (0.4 + 0.6 * r) - 0.025 * (T - 60) + g(0, 0.04)
        puis = self.w_nom * r * self.charge * (1 + 0.1 * self.usure) + g(0, 0.6)
        return {
            "temperature": round(temp, 1),
            "vibration": round(max(vib, 0.05), 2),
            "pressure": round(max(pres, 0.1), 2),
            "rpm": int(round(self.rpm + g(0, 4))),
            "power": round(max(puis, 0.0), 1),
        }

    def mesurer(self, t):
        """Avance la machine jusqu'a t et renvoie la charge de l'evenement."""
        dt = min(max(t - self.t, 0.0), 5.0)
        self.t = t
        self._evenements(t, dt)
        self._physique(t, dt)
        self.seq += 1
        self.propre = None
        if self.marche:
            m = self._mesures()
            self.propre = dict(m, temperature=self.temp_c)
            if self.gel is not None:
                champ, valeur, fin = self.gel
                if valeur is None:
                    self.gel = (champ, m[champ], fin)
                else:
                    m[champ] = valeur
        else:
            m = dict.fromkeys(CHAMPS)
        ts = t + self.decalage + self.rng.uniform(-1, 1) * self.cfg["gigue_ms"] / 1000.0
        charge = {"machine_id": self.id, "seq": self.seq,
                  "ts": int(round(ts * 1000)), "fw": self.fw}
        charge.update(m)
        return charge


# ------------------------------------------------------------- simulation

class Simulation:
    """Parc de machines echantillonne a tour de role, puis couche de livraison
    imparfaite (pertes, doublons, retards, aberrations, champs absents).
    """

    def __init__(self, cfg, t0, verite=False):
        self.cfg = cfg
        self.verite = verite
        self._livres = {}          # (machine, seq) -> livraisons, pour les doublons
        seed = cfg["seed"]
        if seed is None:
            seed = int.from_bytes(os.urandom(8), "big")
        self.seed = seed
        self.rng = random.Random(seed)
        self.machines = [Machine(i, random.Random(self.rng.getrandbits(64)), cfg, t0)
                         for i in range(cfg["machines"])]
        self.t0 = t0
        self.cadence = cfg["machines"] * cfg["frequence"]   # echantillons par seconde simulee
        self.k = 0
        self.attente = []          # tas (instant de livraison, compteur, json)
        self.compteur = 0

    def _alterer(self, charge):
        rng, cfg = self.rng, self.cfg
        aberrant = absent = None
        if charge["temperature"] is not None and rng.random() < cfg["p_aberrant"]:
            aberrant = rng.choice(CHAMPS)
            charge[aberrant] = rng.choice(ABERRANTS[aberrant])
        if rng.random() < cfg["p_champ_absent"]:
            absent = rng.choice(CHAMPS)
            del charge[absent]
        return charge, aberrant, absent

    def _verite(self, machine, t, aberrant, absent, retard):
        return {"machine_id": machine.id, "seq": machine.seq,
                "t_ms": int(round(t * 1000)), "arret": not machine.marche,
                "propre": machine.propre,
                "gel": machine.gel[0] if (machine.gel and machine.marche) else None,
                "fahrenheit": machine.fahrenheit, "aberrant": aberrant,
                "absent": absent, "retard": retard, "doublon": False}

    def _livrer(self, element, sortie):
        if self.verite:
            texte, v = element
            cle = (v["machine_id"], v["seq"])
            if cle in self._livres:
                v = dict(v, doublon=True)
                del self._livres[cle]
            elif v.get("_duplique"):
                self._livres[cle] = 1
            v.pop("_duplique", None)
            sortie.append((texte, v))
        else:
            sortie.append(element)

    def avancer(self, jusqu_a):
        """Simule tous les echantillons dus avant `jusqu_a` (secondes epoch) et
        renvoie la liste des charges JSON a livrer maintenant.
        """
        cfg, rng = self.cfg, self.rng
        cadence, M = self.cadence, len(self.machines)
        sortie = []
        while True:
            t = self.t0 + self.k / cadence
            if t > jusqu_a:
                break
            machine = self.machines[self.k % M]
            self.k += 1
            charge = machine.mesurer(t)
            if rng.random() < cfg["p_perte"]:
                continue
            charge, aberrant, absent = self._alterer(charge)
            texte = json.dumps(charge, separators=(",", ":"))
            retard = rng.random() < cfg["p_retard"]
            delai_retard = rng.uniform(0.5, cfg["retard_max"]) if retard else 0.0
            double = rng.random() < cfg["p_doublon"]
            delai_double = rng.uniform(0.0, 0.5) if double else 0.0
            element = texte
            if self.verite:
                v = self._verite(machine, t, aberrant, absent, retard)
                if double:
                    v["_duplique"] = True
                element = (texte, v)
            if retard:
                self._differer(t + delai_retard, element)
            else:
                self._livrer(element, sortie)
            if double:
                self._differer(t + delai_double, (texte, dict(v)) if self.verite else texte)
        while self.attente and self.attente[0][0] <= jusqu_a:
            self._livrer(heapq.heappop(self.attente)[2], sortie)
        return sortie

    def coupures(self):
        """{machine_id: [[debut_ms, fin_ms], ...]}, fin a None si en cours."""
        return {m.id: [[int(round(a * 1000)), None if b is None else int(round(b * 1000))]
                       for a, b in m.coupures] for m in self.machines}

    def _differer(self, instant, texte):
        self.compteur += 1
        heapq.heappush(self.attente, (instant, self.compteur, texte))

    def sauter(self, jusqu_a):
        """Rattrapage impossible : la simulation saute au present."""
        self.k = max(self.k, int((jusqu_a - self.t0) * self.cadence))


# --------------------------------------------------------------- serveur SSE

class Serveur:
    def __init__(self, cfg):
        self.cfg = cfg
        self.clients = {}            # writer -> asyncio.Queue
        self.id_transport = 0
        self.emis = 0
        self.rejetes = 0
        self.sim = None

    async def produire(self):
        cfg = self.cfg
        t0_mur, t0_mono = time.time(), time.monotonic()
        self.sim = Simulation(cfg, t0_mur)
        acc = acceleration(cfg)
        print("generateur : seed %d, %d machines a %g Hz simules, %.0f evt/s, "
              "acceleration x%g (1 h de procede en %.1f min)"
              % (self.sim.seed, cfg["machines"], cfg["frequence"], cfg["debit"],
                 acc, 60.0 / acc), flush=True)
        while True:
            maintenant = t0_mur + (time.monotonic() - t0_mono) * acc
            retard = (maintenant - (self.sim.t0 + self.sim.k / self.sim.cadence)) / acc
            if retard > 2.0:
                print("generateur : retard de %.1f s, saut au present "
                      "(debit trop eleve pour le processeur)" % retard, flush=True)
                self.sim.sauter(maintenant)
            lot = self.sim.avancer(maintenant)
            if lot:
                self._diffuser(lot)
            await asyncio.sleep(cfg["tick"])

    def _diffuser(self, lot):
        debut = self.id_transport + 1
        self.id_transport += len(lot)
        self.emis += len(lot)
        if not self.clients:
            return
        bloc = "".join("id: %d\ndata: %s\n\n" % (debut + i, s)
                       for i, s in enumerate(lot)).encode()
        for file in self.clients.values():
            try:
                file.put_nowait(bloc)
            except asyncio.QueueFull:
                self.rejetes += len(lot)

    async def journal(self):
        periode = self.cfg["journal"]
        if periode <= 0:
            return
        precedent = 0
        while True:
            await asyncio.sleep(periode)
            debit = (self.emis - precedent) / periode
            precedent = self.emis
            print("generateur : %.0f evt/s, %d client(s), %d evenement(s) "
                  "rejete(s) pour clients lents"
                  % (debit, len(self.clients), self.rejetes), flush=True)

    async def servir(self, lecteur, redacteur):
        try:
            ligne = await asyncio.wait_for(lecteur.readline(), 10)
            while (await asyncio.wait_for(lecteur.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass
            parties = ligne.decode("latin-1").split()
            chemin = parties[1].split("?")[0] if len(parties) > 1 else "/"
            if chemin == "/stream":
                await self._flux(redacteur)
            elif chemin == "/health":
                self._repondre(redacteur, 200, "text/plain", b"ok\n")
            elif chemin == "/info":
                info = {"debit": self.cfg["debit"], "machines": self.cfg["machines"],
                        "frequence_hz": self.cfg["frequence"],
                        "acceleration": acceleration(self.cfg),
                        "champs": ["machine_id", "seq", "ts", "fw"] + CHAMPS,
                        "unites": UNITES}
                self._repondre(redacteur, 200, "application/json",
                               json.dumps(info, indent=2).encode())
            else:
                self._repondre(redacteur, 404, "text/plain", b"introuvable\n")
            await redacteur.drain()
        except (asyncio.TimeoutError, ConnectionError, IndexError):
            pass
        finally:
            try:
                redacteur.close()
            except Exception:
                pass

    @staticmethod
    def _repondre(redacteur, code, type_, corps):
        raison = {200: "OK", 404: "Not Found"}[code]
        redacteur.write(("HTTP/1.1 %d %s\r\nContent-Type: %s\r\n"
                         "Content-Length: %d\r\nConnection: close\r\n\r\n"
                         % (code, raison, type_, len(corps))).encode() + corps)

    async def _flux(self, redacteur):
        taille = max(2, int(math.ceil(self.cfg["tampon_client"] / self.cfg["tick"])))
        file = asyncio.Queue(maxsize=taille)
        sock = redacteur.get_extra_info("socket")
        if sock is not None:
            # Tampon noyau reduit : le retard d'un client lent se voit dans
            # la file applicative bornee, pas dans des megaoctets de TCP.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 128 * 1024)
        redacteur.transport.set_write_buffer_limits(high=256 * 1024)
        redacteur.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        b"Cache-Control: no-cache\r\nConnection: close\r\n\r\n"
                        b"retry: 2000\n\n")
        self.clients[redacteur] = file
        pair = redacteur.get_extra_info("peername")
        print("generateur : client connecte %s" % (pair,), flush=True)
        try:
            while True:
                redacteur.write(await file.get())
                await redacteur.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.clients.pop(redacteur, None)
            print("generateur : client deconnecte %s" % (pair,), flush=True)


async def principal(cfg):
    serveur = Serveur(cfg)
    srv = await asyncio.start_server(serveur.servir, "0.0.0.0", cfg["port"])
    print("generateur : ecoute sur le port %d" % cfg["port"], flush=True)
    arret = asyncio.Event()
    boucle = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            boucle.add_signal_handler(s, arret.set)
        except (NotImplementedError, RuntimeError):
            pass
    taches = [asyncio.create_task(serveur.produire()),
              asyncio.create_task(serveur.journal())]
    async with srv:
        await arret.wait()
    for t in taches:
        t.cancel()


ORIGINE_EXPORT = 1798761600.0      # 2027-01-01 00:00:00 UTC, origine fixe du mode hors ligne


def iterer(cfg, duree, origine=ORIGINE_EXPORT, verite=False, pas=0.1):
    """Simulation hors ligne de `duree` secondes simulees, sans attente.

    Produit les charges JSON dans l'ordre de livraison, ou des couples
    (charge, verite) si `verite` est vrai. Avec une seed et une origine fixees,
    la sortie est entierement deterministe. L'objet Simulation est renvoye en
    fin d'iteration (StopIteration.value), pour lire ses coupures.
    """
    sim = Simulation(cfg, origine, verite=verite)
    t, fin = origine, origine + duree
    while t < fin:
        t = min(t + pas, fin)
        yield from sim.avancer(t)
    for _, _, element in sorted(sim.attente, key=lambda e: (e[0], e[1])):
        sortie = []
        sim._livrer(element, sortie)
        yield from sortie
    return sim


def exporter(cfg, duree, dossier=None, origine=ORIGINE_EXPORT):
    """Sans dossier : charges JSON sur la sortie standard. Avec un dossier :
    flux.jsonl, verite.jsonl (une ligne par ligne de flux.jsonl),
    coupures.json et meta.json.
    """
    if dossier is None:
        gen = iterer(cfg, duree, origine)
        for s in gen:
            sys.stdout.write(s + "\n")
        return
    os.makedirs(dossier, exist_ok=True)
    gen = iterer(cfg, duree, origine, verite=True)
    with open(os.path.join(dossier, "flux.jsonl"), "w") as f, \
            open(os.path.join(dossier, "verite.jsonl"), "w") as v:
        while True:
            try:
                texte, ver = next(gen)
            except StopIteration as fin:
                sim = fin.value
                break
            f.write(texte + "\n")
            v.write(json.dumps(ver, separators=(",", ":")) + "\n")
    with open(os.path.join(dossier, "coupures.json"), "w") as c:
        json.dump(sim.coupures(), c)
    with open(os.path.join(dossier, "meta.json"), "w") as m:
        json.dump({"seed": sim.seed, "origine": origine, "duree": duree,
                   "config": cfg}, m, indent=2)
    print("seed %d, %d evenements simules" % (sim.seed, sim.k), file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--exporter":
        exporter(CONFIG, float(sys.argv[2]),
                 dossier=sys.argv[3] if len(sys.argv) == 4 else None)
    else:
        asyncio.run(principal(CONFIG))
