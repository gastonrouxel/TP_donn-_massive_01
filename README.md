# 8INF919 : Travail pratique 1

**Une chaîne de filtres sous budget mémoire, et une coupure annoncée avant de survenir**

Sujet distribué le 1er octobre 2026. Remise le 22 octobre 2026 à 23 h 59.
Pondération : 20 % de la note finale. Travail individuel, environ douze heures.

Ce fichier est l'énoncé complet du travail. Il n'y en a pas d'autre.

---

## 1. Contexte et objectif

Un exploitant industriel instrumente un parc de douze machines. Chacune publie
dix fois par seconde sa température, son niveau de vibration, une pression, sa
vitesse de rotation et sa puissance consommée. Les messages transitent par un
bus jusqu'à votre service, qui doit les traiter au fil de l'eau : chaque
évènement est vu une seule fois, la mémoire disponible est fixée d'avance, et
le procédé qui produit les données ne reste pas identique à lui-même au cours
du temps.

Les machines surchauffent. Lorsqu'une machine atteint sa température de
coupure, elle s'arrête quelques secondes, refroidit, puis redémarre. Chaque
coupure interrompt la production ; l'exploitant veut être prévenu avant
qu'elle survienne.

Le travail comporte deux parties, et une contrainte qui les traverse toutes
les deux.

| Partie | Ce que vous construisez | Règles | Charge indicative |
|---|---|---|---:|
| A. Chaîne de filtres | un nettoyage en ligne qui rend chaque évènement exploitable : unicité, ordre d'arrivée, pertes, forme, arrêts, unités, bornes, capteurs figés | R1 à R8 | 6 h |
| B. Prédiction | une régression logistique en ligne qui annonce, pour chaque évènement, la probabilité d'une coupure dans les dix secondes | R9 | 5 h |
| Contrainte | tout l'état de votre traitement tient dans une couche de données de 32 Ko, imposée par le contrôleur | R10 | partout |

**L'essentiel du travail est la conception sous contrainte de mémoire.** Une
solution qui garde tout ce qu'elle voit est facile à écrire et ne passe pas :
le flux est sans fin, et le budget ne l'est pas. Pour chaque filtre, il vous
faut choisir ce qu'il retient, combien de temps, et sur combien d'octets. Les
notions des cours 2 à 5 y servent toutes : les trois clauses d'un algorithme
en ligne, l'oubli et ses fenêtres, les structures de résumé et leur
dimensionnement, l'étiquette différée, la descente de gradient et
l'évaluation progressive.

---

## 2. Le flux

Le flux est diffusé par le service `generateur` en
[Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
sur `http://generateur:8000/stream`, adresse joignable depuis le conteneur du
consommateur. Le module fourni `src/flux.py` s'y connecte et vous remet les évènements un par un, sous
forme de dictionnaires.

| Champ | Type | Signification |
|---|---|---|
| `machine_id` | texte | identifiant de la machine |
| `seq` | entier | compteur de messages de la machine |
| `ts` | entier | horodatage de la machine, en millisecondes depuis l'époque Unix, en temps simulé |
| `fw` | texte | version du micrologiciel de la machine |
| `temperature` | flottant | température, en °C |
| `vibration` | flottant | vibration efficace, en mm/s |
| `pressure` | flottant | pression, en bar |
| `rpm` | entier | vitesse de rotation, en tr/min |
| `power` | flottant | puissance consommée, en kW |

Une mesure vaut `null` lorsque la machine ne la produit pas.

**Temps simulé.** Chaque machine est échantillonnée dix fois par seconde de
temps simulé ; une heure de procédé représente donc toujours 432 000
évènements pour le parc, quel que soit le débit. Le débit `GEN_DEBIT` fixe la
vitesse à laquelle ce temps s'écoule :

| `GEN_DEBIT` (évt/s) | Accélération | Une heure de procédé dure |
|---:|---:|---:|
| 120 | ×1 | 1 h |
| 1 200 (défaut) | ×10 | 6 min |
| 6 000 | ×50 | 1 min 12 s |
| 12 000 | ×100 | 36 s |

Le champ `ts` part de l'heure du lancement du générateur et avance à cette
vitesse : à ×10, il prend dix secondes d'avance par seconde d'horloge. Toute
durée exprimée dans cet énoncé ou dans `.env` est une durée simulée, à lire
sur `ts`. Monter le débit raccourcit l'attente des phénomènes lents (section
7), au prix d'un traitement qui doit tenir une cadence plus élevée.

**Diffusion.** Le flux avance que vous soyez connecté ou non, et vous le
recevez à partir du moment où vous vous connectez. Deux lancements successifs
ne voient donc pas les mêmes données ; seul un traitement exécuté assez
longtemps converge vers des résultats comparables d'un lancement à l'autre. Un
consommateur trop lent pour suivre le débit prend du retard, puis perd des
évènements au transport ; `src/flux.py` les compte dans `perdus_transport`.

---

## 3. Ce que vous devez rendre

Un dépôt Git contenant votre implémentation de `src/pipeline.py`. Ce fichier
est le point d'entrée : c'est lui que `app.py` et la suite de tests importent.
Vous pouvez ajouter les dossiers et les fichiers que vous jugez utiles
(modules auxiliaires, notes, essais), à condition qu'ils soient versionnés
dans le dépôt et importés depuis la racine (`from src.outils import ...`).

La classe `Traitement` constitue le contrat d'interface. Son nom, ses
méthodes et la forme de ce qu'elles renvoient sont imposés ; `src/pipeline.py`
en contient le squelette, dont chaque méthode lève pour l'instant
`NotImplementedError`.

```python
Traitement(memoire)             # alloue tout l'état dans memoire (R10)
Traitement.traiter(evenement)   # -> None (doublon) ou l'évènement nettoyé
Traitement.resume()             # -> dict, avec les compteurs de R1 à R3
```

`traiter` est appelée une fois par évènement, dans l'ordre d'arrivée. Elle
renvoie `None` si l'évènement est un doublon (R1), et sinon l'évènement
nettoyé, sous la forme suivante :

```python
{"machine_id": "M-03", "seq": 18342, "ts": 1798761600123,
 "retard": False,                 # R2
 "arret": False,                  # R5
 "temperature": 81.4,             # degrés Celsius (R6), ou None
 "vibration": 2.31, "pressure": 4.12, "rpm": 1502, "power": 37.8,
 "p": 0.12}                       # probabilité de coupure (R9), ou None
```

`resume` renvoie un dict sérialisable en JSON qui contient au moins les
entiers `"doublons"`, `"retards"` et `"pertes"` (R1 à R3) ; vous pouvez y
ajouter tout ce qui vous aide à suivre votre traitement.

**La couche de données.** Le contrôleur (`app.py` à l'exécution, la suite de
tests à la correction) crée une couche de données de `BUDGET_OCTETS` octets
(`src/memoire.py`), la passe au constructeur de `Traitement`, puis la
verrouille. Vous y allouez des tableaux de taille fixe, dans `__init__`, une
fois pour toutes :

```python
def __init__(self, memoire):
    self.bits = memoire.allouer("doublons", 4_000, "bit")
    self.cms = memoire.allouer("count_min", (4, 256), "i32")
    self.poids = memoire.allouer("poids", 6, "f64")
```

Les types disponibles sont `bit` (un bit), `u8` (un octet), `i32` (quatre
octets), `i64` et `f64` (huit octets). Les tableaux numériques sont des
`numpy.ndarray` de forme fixe : on lit et on écrit leurs cases, on ne les
agrandit pas. Une allocation qui dépasse le budget lève `MemoireEpuisee` ; une
allocation après la construction lève `MemoireVerrouillee`. Ce que vous allouez
et la façon dont vous l'utilisez sont libres ; le budget est imposé.

`app.py`, `src/flux.py`, `src/memoire.py`, `src/annexe.py`, `generateur/` et
`tests/` sont fournis et ne doivent pas être modifiés. Ces éléments sont
réécrits à l'identique avant la correction, `tests/` étant supprimé puis
restauré en entier : une modification ou un ajout de votre part serait sans
effet sur votre note. Placez vos propres essais ailleurs, dans un dossier
`essais/` par exemple. Le fichier `requirements.txt` est fixe : `numpy` et
`pytest`, rien d'autre. La régression logistique est donc à écrire vous-même.

---

## 4. Règlement de traitement

Ce qu'il faut corriger n'est pas indiqué ligne par ligne ; **comment** le
corriger l'est intégralement. Le règlement fixe une politique unique pour
chaque règle, de sorte que deux implémentations correctes produisent la même
sortie. Les constantes citées sont dans `src/annexe.py`.

**L'ordre d'application des règles n'est pas imposé ; il n'est pas
indifférent.** Certaines règles supposent que d'autres ont déjà été
appliquées, et un ordre qui paraît naturel peut produire une sortie fausse
sans rien signaler. C'est à vous de trouver un ordre qui respecte chacune des
règles telles qu'elles sont écrites.

### Partie A : la chaîne de filtres

**R1. Unicité.** Un évènement dont le couple `(machine_id, seq)` a déjà été
reçu est un doublon : `traiter` renvoie `None`, et `resume()["doublons"]`
compte les doublons rejetés. Un évènement arrive au plus `RETARD_MAX_S`
secondes simulées après son horodatage.

**R2. Ordre d'arrivée.** Un évènement dont le `seq` est inférieur au plus
grand `seq` déjà reçu de sa machine est en retard : il est nettoyé et transmis
avec `"retard": True`, et `resume()["retards"]` les compte. Un évènement en
retard ne fait pas avancer l'état de sa machine : il n'entre ni dans les
séries de R8 ni dans les variables de R9.

**R3. Pertes.** `resume()["pertes"]` vaut, à tout instant, le nombre de `seq`
jamais reçus compris entre 1 et le plus grand `seq` reçu, sommé sur les douze
machines. Un `seq` qui arrive en retard cesse d'être une perte.

**R4. Forme.** Chaque évènement transmis porte ses cinq champs de mesure. Un
champ absent du message vaut `None` ; il n'est jamais reconstitué.

**R5. Arrêts.** Un évènement dont toutes les mesures sont nulles ou absentes
signale une machine à l'arrêt : il est transmis avec `"arret": True` et toutes
ses mesures à `None`. Une coupure commence au premier évènement d'arrêt d'une
série ; c'est elle que R9 cherche à annoncer.

**R6. Unités.** La température s'exprime en degrés Celsius. Une machine dont
la version majeure de micrologiciel (le premier nombre de `fw`) est au moins
`FW_FAHRENHEIT_DES` rapporte en degrés Fahrenheit ; ses températures sont
converties et arrondies au dixième.

**R7. Bornes physiques.** Toute mesure hors de l'intervalle `BORNES` de son
champ, bornes comprises, devient `None`. Les autres mesures de l'évènement
sont conservées.

**R8. Capteurs figés.** Pour chaque machine et chaque champ de `CHAMPS_GEL`,
une valeur identique à celle des `GEL_N − 1` mesures précédentes du même champ
est un artefact de capteur : elle devient `None`, ainsi que les valeurs
identiques qui la suivent. Les évènements d'arrêt, les mesures manquantes et
les évènements en retard n'interrompent pas une série et n'y comptent pas.

### Partie B : la prédiction

**R9. Coupure annoncée.** Pour chaque évènement transmis d'une machine en
marche, `p` est la probabilité qu'une coupure de cette machine commence dans
les `HORIZON_S` secondes simulées qui suivent son horodatage. Le modèle est
une **régression logistique apprise en ligne**, écrite à la main avec numpy :
il prédit chaque évènement avant d'en apprendre quoi que ce soit, et il
apprend à partir des étiquettes que votre traitement construit lui-même. Une
étiquette n'est connue que plus tard : positive si une coupure de la machine
commence dans les `HORIZON_S` secondes, négative si ce délai s'écoule sans
coupure. Le choix des variables, du pas d'apprentissage et de ce que vous
conservez en attendant les étiquettes vous appartient. Pour un évènement à
l'arrêt, `p` peut valoir `None`.

### Partout : la mémoire

**R10. Mémoire bornée.** Tout état de votre traitement qui dépend du flux vit
dans la couche de données. Hors de la couche, seule la configuration est
tolérée : constantes, paramètres, et la correspondance `INDEX_MACHINE` de
l'annexe, dans la limite de `PLAFOND_HORS_MEMOIRE` octets et sans croissance au
fil du flux. Une variable de module qui grandit avec le flux viole la règle au
même titre qu'un attribut. **Un traitement qui viole R10 ne marque aucun point
sur les tests de nettoyage et de prédiction (tests 04 à 14).**

---

## 5. Annexe technique

Toutes les constantes du règlement sont dans `src/annexe.py`, qui fait partie
de l'énoncé : liste des machines et correspondance `INDEX_MACHINE`, fréquence
d'échantillonnage, retard maximal de livraison, version de micrologiciel en
Fahrenheit, bornes physiques, champs et longueur de série des capteurs figés,
horizon et seuil de décision, budget de la couche, plafond hors couche,
plancher de performance. Importez-les plutôt que de les recopier.

---

## 6. Mise en route

Les deux services tournent dans des conteneurs Docker : le générateur de flux
et le consommateur, qui contient Python 3.14, les versions exactes des
bibliothèques (`requirements.txt`) et les éléments imposés. Votre dépôt est
monté en lecture seule dans le consommateur ; vous continuez donc à éditer vos
fichiers avec votre éditeur habituel, et rien n'est à installer sur votre
poste en dehors de Docker Desktop (Windows, macOS) ou de Docker Engine avec le
plugin Compose (Linux).

```bash
docker compose up --build
```

Cette commande construit les deux images, démarre le générateur, attend qu'il
soit prêt, puis lance votre traitement. Toutes les cinq secondes, le
consommateur affiche le débit reçu, les pertes de transport et ce que renvoie
`resume`. Ce mode sert à observer votre traitement sur le flux en direct ; il
ne calcule pas de note. `Ctrl+C` arrête les deux services ; la relancer après une
modification de votre code repart d'un parc neuf.

Pour itérer sans redémarrer le procédé, détachez le générateur, puis relancez
le consommateur seul autant de fois que nécessaire :

```bash
docker compose up -d --build generateur   # une fois
docker compose up --build consommateur    # à chaque modification de votre code
```

L'option `-d` détache le générateur : il continue en arrière-plan, et `Ctrl+C`
n'arrête plus que le consommateur. Vos essais successifs observent alors la
suite du même procédé, usure accumulée comprise. C'est la situation normale en
production : un consommateur qui redémarre retrouve un flux qui ne l'a pas
attendu. `docker compose logs -f generateur` affiche le journal du générateur
(débit réel, accélération, clients connectés) ; `docker compose down` arrête
tout.

Les paramètres à calibrer se règlent dans le fichier `.env` (section 7). Après une
modification, relancez `docker compose up --build`, ou
`docker compose up -d generateur` si le générateur est détaché : il redémarre
avec un parc neuf, et le consommateur s'y reconnecte de lui-même. Dans
`docker-compose.yml`, `TP_DUREE` (secondes d'horloge, 0 pour sans fin) et `TP_RAPPORT_S`
(période d'affichage) règlent le consommateur.

Sous Linux, si le conteneur signale que le dépôt n'est pas lisible, faites
précéder les commandes de `TP_UID=$(id -u) TP_GID=$(id -g)`.

Le conteneur du consommateur est celui de la correction : votre dépôt y est
monté en lecture seule, les fichiers fournis y sont remplacés par les versions
de l'image, et seul ce qui y roule compte. La suite de tests et la note
s'obtiennent par une autre commande, qui n'a pas besoin du générateur en
direct (section 8) :

```bash
docker compose run --rm --no-deps --build consommateur tests
```

---

## 7. Régler le générateur

Les paramètres utiles à calibrer sont regroupés dans le fichier `.env` à la
racine du dépôt, une ligne `NOM=valeur` par paramètre : débit, seed,
dynamique du procédé et défauts de données. `docker compose` le lit de
lui-même ; `docker-compose.yml` n'est pas à modifier, et les autres réglages
du générateur y sont fixes. Les durées et les taux sont en temps simulé ; les
colonnes « à ×10 » et « à ×100 » donnent l'attente correspondante en temps d'horloge pour
`GEN_DEBIT=1200` et `GEN_DEBIT=12000`. Les fréquences ci-dessous ont été
mesurées sur quatre heures simulées avec les réglages par défaut.

**Comportement du procédé.**

| Phénomène | Variable (défaut) | Fréquence pour le parc | À ×10 | À ×100 |
|---|---|---|---:|---:|
| Surchauffe : coupure, mesures nulles pendant l'arrêt, redémarrage | aucune (arrêt de 3 à 8 s) | une coupure toutes les 21 à 33 s par machine en charge haute ; arrêt médian de 5,4 s ; environ 16 coupures par minute pour le parc | 2,7 par seconde | 27 par seconde |
| Changement de régime de charge | `GEN_DUREE_REGIME_S` (180) | toutes les 3 min en moyenne par machine, soit toutes les 15 s pour le parc | 1,5 s | 0,15 s |
| Cycle de température ambiante | aucune | période de 20 min | 2 min | 12 s |
| Usure : les régimes modérés se mettent à couper | `GEN_USURE_PAR_HEURE` (0,10) | après 1 à 3 h selon la machine ; les coupures doublent sur certaines machines entre la première et la quatrième heure | 6 à 18 min | 36 s à 1 min 48 |
| Épisode de vibration anormale, 20 à 60 s | `GEN_TAUX_PANNE_PAR_HEURE` (1) | une par heure et par machine, soit un épisode toutes les 5 min pour le parc | 30 s | 3 s |

**Défauts de données.** Une probabilité s'applique à chaque évènement ; un
taux horaire s'applique à chaque machine. La valeur 0 désactive le défaut.

| Défaut | Variable (défaut) | Fréquence | À ×10 | À ×100 |
|---|---|---|---:|---:|
| Évènement perdu à la source | `GEN_P_PERTE` (0,002) | 0,2 % des évènements | 2,4 par seconde | 24 par seconde |
| Évènement livré deux fois | `GEN_P_DOUBLON` (0,002) | 0,2 % | 2,4 par seconde | 24 par seconde |
| Évènement livré en retard, jusqu'à `GEN_RETARD_MAX_S` (5 s) | `GEN_P_RETARD` (0,003) | 0,3 % ; 0,5 % des évènements arrivent hors d'ordre, doublons compris | 3,6 par seconde | 36 par seconde |
| Valeur physiquement impossible | `GEN_P_ABERRANT` (0,001) | 0,1 % | 1,2 par seconde | 12 par seconde |
| Champ de mesure absent | `GEN_P_CHAMP_ABSENT` (0,001) | 0,1 % | 1,2 par seconde | 12 par seconde |
| Capteur figé, 5 à 30 s | `GEN_TAUX_GEL_PAR_HEURE` (2) | deux par heure et par machine, soit un toutes les 2 min 30 pour le parc | 15 s | 1,5 s |
| Mise à jour du micrologiciel | `GEN_TAUX_FIRMWARE_PAR_HEURE` (0,2) | première du parc après 25 min en moyenne ; la moitié du parc après 4 h | 2 min 30 | 15 s |

**Recettes.**

| Pour | Écrire dans `.env` |
|---|---|
| un flux propre, pour mettre au point la logique de base | les sept variables de défaut à `0` |
| isoler un défaut | ce défaut à sa valeur par défaut ou plus haute, les six autres à `0` |
| observer l'usure et la mise à jour du micrologiciel sans attendre | `GEN_DEBIT=12000` |
| provoquer davantage de mises à jour du micrologiciel | `GEN_TAUX_FIRMWARE_PAR_HEURE=5` |
| plus de surchauffes, plus vite | `GEN_DUREE_REGIME_S=60` et `GEN_USURE_PAR_HEURE=0.5` |
| éprouver la tenue en débit de votre traitement | `GEN_DEBIT=20000`, en surveillant `perdus_transport` |
| rejouer les mêmes tirages | `GEN_SEED=42` ; les horodatages et la phase de l'ambiante dépendent encore de l'heure du lancement |
| revenir aux conditions de la correction | les valeurs d'origine de `.env` |

Ces réglages ne concernent que le flux en direct. La suite de tests ignore
`.env` : elle rejoue toujours les valeurs d'origine du tableau ci-dessus, au
débit qui lui convient, et la correction fait de même pour toutes ses seeds.

---

## 8. Comment vous êtes évalué

### Suite de tests

Votre note est le résultat de la suite de tests exécutée sur votre dépôt à
l'échéance ; il n'existe pas d'autre suite.

```bash
docker compose run --rm --no-deps --build consommateur tests
```

L'option `--build` reconstruit l'image lorsque les fichiers fournis ont
changé, par exemple après une mise à jour de l'énoncé ; sans elle, Docker
réutilise l'image existante et rejoue d'anciens tests. L'option `--no-deps`
évite de démarrer le générateur en direct, inutile ici.

La commande prend une à deux minutes et s'arrête sur votre note, par exemple
`Note : 85 / 100`. Elle ne se branche pas sur le flux en direct : elle rejoue
une heure simulée produite par le même générateur en mode hors ligne, avec
une seed et une origine de temps fixées, et passe les évènements directement
à `Traitement.traiter`, sans réseau. Deux exécutions voient donc exactement
les mêmes évènements, et la vérité terrain du générateur (coupures, défauts
injectés, valeurs d'origine) sert de référence.

La suite publique rejoue la seed `20261001`. **La correction rejoue le même
code sur plusieurs autres seeds, qui ne sont pas communiquées** ; un test ne
rapporte ses points que s'il passe sur toutes. Un traitement réglé sur un flux
particulier ne passera donc pas la correction ; un traitement qui encode les
règles passera les deux.

Chaque test nomme **la propriété qu'il vérifie**, jamais le défaut qui la met
en défaut. Aucun test ne compare votre sortie à une sortie de référence :
tous vérifient des propriétés, calculées sur votre sortie et sur la vérité
terrain. Les tolérances absorbent les cas limites, pas les règles omises.

| Test | Propriété vérifiée | Règle | Points |
|---|---|---|---:|
| `test_01_interface` | le traitement se construit dans le budget et rend des sorties de la forme imposée | contrat | 5 |
| `test_02_etat_hors_couche` | l'état tenu hors de la couche est borné et constant | R10 | 5 |
| `test_03_memoire_python_stable` | aucune structure cachée ne grandit avec le flux | R10 | 5 |
| `test_04_unicite` | chaque prélèvement est transmis une fois et une seule | R1 | 8 |
| `test_05_ordre_d_arrivee` | un évènement arrivé après un plus récent de sa machine est signalé | R2 | 6 |
| `test_06_pertes` | le nombre d'évènements jamais reçus est connu | R1, R3 | 6 |
| `test_07_forme_complete` | une mesure absente du message n'est jamais inventée | R4 | 4 |
| `test_08_une_grandeur_une_unite` | toutes les températures transmises sont en Celsius | R6 | 8 |
| `test_09_plausibilite_physique` | aucune mesure transmise n'est hors de son domaine | R7 | 8 |
| `test_10_capteurs_figes` | une valeur figée n'est pas transmise comme une mesure | R8 | 8 |
| `test_11_arrets` | une machine à l'arrêt est signalée comme telle, sans mesure | R5 | 5 |
| `test_12_mesures_legitimes_preservees` | aucune mesure légitime n'est perdue ni modifiée | R4 à R8 | 8 |
| `test_13_couverture_des_predictions` | une prédiction est émise pour chaque machine en marche | R9 | 4 |
| `test_14_detection_des_coupures` | les coupures sont annoncées avant de survenir | R9 | 12 |
| `test_15_determinisme` | deux exécutions sur le même flux donnent les mêmes sorties | contrat | 8 |

Le test 12 mérite un avertissement. Il est l'envers des tests 07 à 10 : un
filtre trop zélé, qui efface une mesure à la moindre ressemblance avec un
défaut, échoue ici. Distinguer l'artefact de la mesure est le cœur de la
partie A.

Le test 14 calcule le score F1 de vos prédictions au seuil `SEUIL_DECISION`,
sur la seconde moitié du flux, sur les évènements d'une machine en marche qui
ne sont ni des doublons ni en retard ; une prédiction `None` y compte comme un
« non ». Le plancher `PLANCHER_F1` est au-dessus de ce qu'obtient la meilleure
règle de seuil fixe sur la température, même choisie après coup : il faut
apprendre.

### Règles, pas réponses

Votre code doit encoder des règles, pas des réponses : une implémentation qui
s'appuie sur des valeurs précises du flux public, et que vous ne savez pas
expliquer, est traitée comme telle (section 9).

### Conditions d'exécution

La correction utilise le conteneur décrit à la section 6, construit à partir
du dépôt de référence et non de votre copie : votre dépôt est cloné tel qu'il
se trouve sur `main` et monté en lecture seule, et les fichiers fournis y sont
remplacés par les versions de l'image. Le code s'exécute avec deux
processeurs, 2 Go de mémoire et quinze minutes au plus. Un module qui
importerait une bibliothèque absente de `requirements.txt` fait échouer
l'importation de `src.pipeline` ; toute rupture du contrat d'interface produit
zéro sur les tests concernés.

### Pondération

La note est sur 100 ; un test est réussi en entier ou ne rapporte rien. La
suite affiche votre note en dernière ligne, après le bilan de pytest, sous la
forme `Note : 85 / 100`, précédée de la liste des tests à zéro. Aucun rapport
n'est demandé.

---

## 9. Règles

Travail strictement individuel. Les remises sont passées à un outil de
détection de similarité de code. L'écriture de code assistée est autorisée,
mais vous répondez de chaque ligne que vous remettez : une implémentation que
vous ne savez pas expliquer sera traitée comme telle.

L'accès en écriture à votre dépôt est coupé automatiquement à l'échéance. Tout
travail en retard sans motif valable préalablement discuté se voit attribuer la
note zéro. Le dernier état poussé sur `main` avant la coupure fait foi.

Les questions d'intérêt général vont dans le canal `#discussion` du Discord du
cours. Une réponse vous parvient sous 48 heures.
