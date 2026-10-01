# -*- coding: utf-8 -*-
"""8INF919 - TP1 - Client du flux. Fourni, ne pas modifier.

Se connecte au generateur en Server-Sent Events et produit les evenements un
par un, sous forme de dictionnaires, dans l'ordre ou ils arrivent :

    from src.flux import Flux

    flux = Flux("http://generateur:8000/stream")
    for evenement in flux:
        ...

Le client tient deux compteurs de transport, consultables a tout moment :
`flux.recus` (evenements lus) et `flux.perdus_transport` (evenements que le
generateur a diffuses mais que ce client n'a pas recus parce qu'il lisait
trop lentement, deduits des trous dans les identifiants SSE). Ces compteurs
ne disent rien de la qualite des donnees elles-memes.

En cas de coupure, le client se reconnecte apres `attente` secondes ; le flux
reprend au present, sans rattrapage.
"""
import http.client
import json
import time
from urllib.parse import urlsplit


class Flux:
    def __init__(self, url, attente=2.0, delai=30.0):
        p = urlsplit(url)
        self.hote = p.hostname
        self.port = p.port or 80
        self.chemin = p.path or "/stream"
        self.attente = attente
        self.delai = delai
        self.recus = 0
        self.perdus_transport = 0
        self.connexions = 0
        self._dernier_id = None

    def __iter__(self):
        while True:
            try:
                yield from self._session()
            except (OSError, http.client.HTTPException) as e:
                print("flux : connexion interrompue (%s), nouvelle tentative "
                      "dans %.0f s" % (e.__class__.__name__, self.attente),
                      flush=True)
            self._dernier_id = None
            time.sleep(self.attente)

    def _session(self):
        conn = http.client.HTTPConnection(self.hote, self.port, timeout=self.delai)
        try:
            conn.request("GET", self.chemin, headers={"Accept": "text/event-stream"})
            rep = conn.getresponse()
            if rep.status != 200:
                raise http.client.HTTPException("statut %d" % rep.status)
            self.connexions += 1
            ident, donnees = None, None
            for ligne in rep:
                if ligne.startswith(b"data:"):
                    donnees = ligne[5:].strip()
                elif ligne.startswith(b"id:"):
                    ident = int(ligne[3:])
                elif ligne in (b"\n", b"\r\n"):
                    if donnees is not None:
                        self._compter(ident)
                        yield json.loads(donnees)
                    ident, donnees = None, None
            raise http.client.HTTPException("flux termine par le serveur")
        finally:
            conn.close()

    def _compter(self, ident):
        self.recus += 1
        if ident is None:
            return
        if self._dernier_id is not None and ident > self._dernier_id + 1:
            self.perdus_transport += ident - self._dernier_id - 1
        self._dernier_id = ident
