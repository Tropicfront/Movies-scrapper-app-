# Sorties Films — Blu-ray / DVD / 4K Ultra HD

Application Docker qui récupère automatiquement le planning des sorties
Blu-ray / DVD / 4K Ultra HD depuis **4k-ultra-hd.fr** et **edition-limitee.fr**,
les enrichit avec les **affiches TMDB**, les croise avec **Jellyfin**, et
expose le tout via une page web, un **flux calendrier (.ics)** et un
**widget iFrame** — les deux prêts à brancher sur un dashboard type
**Homarr** ou **Homepage**.

## Sources de sorties

| Site | Ce qui est scrapé |
|---|---|
| **4k-ultra-hd.fr** | Page "Prochaines sorties 4K" (paginée, ~140 titres) + page "Dates en attente" |
| **edition-limitee.fr** | Articles mensuels du calendrier, repérés automatiquement depuis la page hub `/blu-ray-dvd/sortie-blu-ray-dvd/` |

### Dédoublonnage
Quand le même titre sort à la même date sur les deux sites, une seule
entrée est conservée — celle de **4k-ultra-hd.fr** en priorité — grâce à
une comparaison de titres normalisée (accents/mentions de format retirés).

## Calendrier pour dashboard (Homarr / Homepage)

Deux flux, un par format :
```
http://<ton-serveur>:8080/calendar-4k.ics       (sorties 4K Ultra HD)
http://<ton-serveur>:8080/calendar-bluray.ics   (sorties Blu-ray / DVD)
```

**Pour avoir les sorties jour par jour avec une couleur différente par
format**, il faut ajouter **les deux flux comme deux intégrations d'un
seul et même widget "Calendar"** — Homarr et Homepage supportent tous les
deux plusieurs intégrations sur un même widget calendrier, qui sont alors
fusionnées et affichées ensemble, jour par jour, chacune gardant sa
couleur propre. C'est la seule façon d'obtenir des couleurs différentes
par format : ces widgets colorent par intégration, pas événement par
événement (un flux `.ics` unique ne peut pas transporter une couleur
différente par sortie).

Un flux fusionné existe aussi si tu ne te préoccupes pas des couleurs :
```
http://<ton-serveur>:8080/calendar.ics
```
(4K et Blu-ray/DVD mélangés dans un seul flux, chaque titre préfixé d'un
pictogramme 🟣 4K / 🔵 Blu-ray / 🔴 DVD à défaut de vraie couleur.)

Paramètres optionnels (sur les 3 flux) : `?scope=all` (inclut l'historique
récent), `?jellyfin=only` (uniquement ce que tu as déjà).

### Configuration Homepage
Deux intégrations sur le **même** widget `calendar` :
```yaml
- Sorties Films:
    widget:
      type: calendar
      maxEvents: 15
      showTime: false
      view: monthly
      firstDayInWeek: monday
      integrations:
        - type: ical
          url: http://<ton-serveur>:8080/calendar-4k.ics
          name: Sorties 4K
          color: purple
          params:
            showName: true
        - type: ical
          url: http://<ton-serveur>:8080/calendar-bluray.ics
          name: Sorties Blu-ray/DVD
          color: blue
          params:
            showName: true
```

### Configuration Homarr
Menu **Intégrations** → Ajouter → **iCal**, une fois pour chaque flux
(`calendar-4k.ics` avec une couleur, `calendar-bluray.ics` avec une
autre), puis ajoute **un seul** widget **Calendar** sur ton board et
sélectionne les **deux** intégrations dedans — elles s'affichent alors
fusionnées, jour par jour, avec leurs couleurs respectives.

## Affiches sur le dashboard : le widget iFrame

**Aucun widget calendrier (Homarr, Homepage, Google/Apple/Outlook inclus)
n'affiche d'affiche/poster à partir d'un flux `.ics`** — ce n'est pas
prévu par le format, quel que soit le contournement technique.

Pour contourner cette limite, l'app expose une **page compacte dédiée à
l'embarquement en iFrame**, présentée façon calendrier (les sorties sont
regroupées par jour, chacun avec un repère visuel jour/mois), avec
affiches, boutons Amazon/Fnac et bordure colorée par format (🟣 4K, 🔵
Blu-ray, 🔴 DVD) — puisqu'il s'agit d'une vraie page HTML et non d'un
flux calendrier, tout ça s'affiche normalement (cliquer sur une affiche
ouvre sa fiche TMDB) :
```
http://<ton-serveur>:8080/widget/upcoming
```

Paramètres optionnels :
- `?limit=30` — nombre de sorties affichées (défaut : 30, max : 50)
- `?scope=upcoming` — `upcoming` (défaut), `today`, `tomorrow`, ou `all` (à venir + récentes)
- `?jellyfin=only` — uniquement les films/séries déjà présents dans Jellyfin
- `?category=4k` ou `?category=bluray` — filtrer par format
- `?theme=dark` (défaut) ou `?theme=light`

#### Configuration Homepage (widget iFrame natif)
```yaml
- Prochaines sorties:
    widget:
      type: iframe
      src: http://<ton-serveur>:8080/widget/upcoming?limit=30
      classes: h-96 sm:h-96 md:h-[32rem] lg:h-[32rem] xl:h-[32rem]
```

#### Configuration Homarr (widget iFrame natif)
Ajoute une tuile → **Widgets** → **iFrame**, colle l'URL
`http://<ton-serveur>:8080/widget/upcoming?limit=30`, puis ajuste la
hauteur de la tuile selon le nombre de sorties affichées.

## Affiches (TMDB)

Renseigne `TMDB_API_KEY` (clé gratuite sur
[themoviedb.org](https://www.themoviedb.org) → Paramètres → API) pour que
chaque sortie récupère son affiche — cliquer dessus ouvre sa fiche TMDB
complète (pas besoin de bouton séparé). Utilisé à la fois sur la page web
et sur le widget iFrame. Cache persistant (`posters.json`), retenté tous
les 7 jours pour les titres non trouvés.

## Boutons Amazon / Fnac

Chaque sortie affiche, quand disponibles, des boutons **Amazon** et
**Fnac** repris directement des liens d'achat présents sur le site
source :

| Source | Amazon | Fnac | Où ces liens sont cherchés |
|---|---|---|---|
| 4k-ultra-hd.fr | liens `amzn.to` | liens `tidd.ly` | sur la page individuelle de chaque film (`/film/<slug>`) — une requête HTTP supplémentaire par titre, mise en cache pour ne pas la refaire à chaque rafraîchissement |
| edition-limitee.fr | liens `amzn.to` | liens `awin1.com` | dans le paragraphe de description qui suit l'entrée du titre dans l'article mensuel |

Le **titre**, lui, pointe toujours vers la fiche du site source
(4k-ultra-hd.fr ou edition-limitee.fr) — jamais vers un lien affilié —
même quand le site source utilise directement un lien affilié comme lien
principal (dans ce cas on retombe sur la page/l'article du site source).

> Cette détection dépend de la structure HTML de chaque site au moment du
> scraping (calibrée à partir d'exemples réels) ; si un site change sa
> mise en page, les boutons peuvent temporairement ne plus apparaître sans
> que le reste du scraping en soit affecté.

## Intégration Jellyfin

Renseigne `JELLYFIN_URL` et `JELLYFIN_API_KEY` (Jellyfin → Tableau de
bord → Paramètres avancés → Clés API) pour que chaque sortie déjà
présente dans ta bibliothèque affiche un badge **📀 Déjà dans Jellyfin**
(comparaison de titres normalisée + tolérance aux petites variations via
`difflib`). La comparaison porte à la fois sur les **films et les
séries** de ta bibliothèque Jellyfin, puisque les séries/animes sortent
aussi en coffrets physiques (Blu-ray/4K). Laisser les variables vides
désactive l'intégration sans impact sur le reste de l'app.

> Cette intégration ne fait que **lire** ta bibliothèque pour comparer
> les titres — elle n'ajoute, ne modifie ni ne supprime rien dans Jellyfin.

## docker-compose.yml complet

Ce fichier utilise directement l'image publiée plutôt que de builder
depuis les sources :

```yaml
services:
  sorties-films:
    image: tropicfront/movies_scrapper:latest
    container_name: sorties-films
    ports:
      - "8080:5000"
    environment:
      - REFRESH_HOURS=6   # fréquence de rafraîchissement automatique (en heures)
      - APP_TIMEZONE=Europe/Paris                # fuseau horaire d'affichage (ex: America/New_York, Europe/Paris)
      - JELLYFIN_URL=http://192.168.1.X:8096   # URL de ton serveur Jellyfin (laisser vide pour désactiver)
      - JELLYFIN_API_KEY=                       # clé API Jellyfin (Tableau de bord > Clés API)
      - TMDB_API_KEY=                           # clé API TMDB v3 (gratuite) pour les affiches (laisser vide pour désactiver)
      - TMDB_LANGUAGE=fr-FR                     # langue des fiches/affiches TMDB
    volumes:
      - sorties-data:/app/data
    restart: unless-stopped

volumes:
  sorties-data:
```

## Configuration (détail des variables)

| Variable | Description | Valeur par défaut |
|---|---|---|
| `PORT` | Port d'écoute interne de l'application (le `8080` à gauche dans `ports:` est celui accessible depuis l'extérieur) | `5000` |
| `REFRESH_HOURS` | Fréquence de rafraîchissement automatique (en heures) | `6` |
| `APP_TIMEZONE` | Fuseau horaire IANA pour l'heure de dernière mise à jour (ex. `America/New_York`, `Asia/Tokyo`) | `Europe/Paris` |
| `EDITION_LIMITEE_MONTH_ARTICLES` | Nombre d'articles mensuels scrapés sur edition-limitee.fr | `3` |
| `JELLYFIN_URL` | URL de ton serveur Jellyfin (vide = désactivé) | *(vide)* |
| `JELLYFIN_API_KEY` | Clé API Jellyfin | *(vide)* |
| `JELLYFIN_FUZZY_CUTOFF` | Tolérance de comparaison approximative des titres (0 à 1) | `0.88` |
| `TMDB_API_KEY` | Clé API TMDB pour les affiches (vide = désactivé) | *(vide)* |
| `TMDB_LANGUAGE` | Langue des fiches/affiches TMDB | `fr-FR` |
| `DATA_DIR` | Dossier de persistance des données dans le conteneur | `/app/data` |

## Endpoints

- `GET /` — page web (planning à venir avec affiches, dates à préciser, sorties récentes)
- `GET /widget/upcoming` — page compacte pour widget iFrame (voir section dédiée)
- `GET /api/releases` — JSON (`?jellyfin=only`, `?category=4k|bluray`)
- `GET /calendar.ics` — flux iCalendar complet, trié par date (recommandé)
- `GET /calendar-4k.ics` / `GET /calendar-bluray.ics` — flux scindés par format
- `GET /api/jellyfin/status` — vérifie la connexion à Jellyfin
- `POST /api/refresh` — force un rafraîchissement immédiat
- `GET /health` — healthcheck

## Persistance

`releases.json` et `posters.json` sont sauvegardés dans le volume Docker
`sorties-data`. En cas d'échec d'une source (site, Jellyfin, TMDB
indisponible), l'ancien cache est conservé et l'erreur s'affiche en haut
de la page.

## Structure du projet

```
.
├── app.py                       # Flask + planificateur + fusion des sources
├── date_utils.py                 # Dates FR, normalisation titres, classification format
├── json_cache.py                  # Utilitaire partagé de cache JSON sur disque
├── scraper_4k.py                   # Scraper 4k-ultra-hd.fr (+ liens affiliés par fiche film)
├── scraper_editionlimitee.py       # Scraper edition-limitee.fr (+ liens affiliés)
├── jellyfin_client.py              # Croisement Jellyfin (films + séries)
├── poster_lookup.py                 # Affiches via TMDB
├── calendar_feed.py                 # Génération des flux iCalendar (.ics)
├── templates/index.html             # Page web
├── templates/widget.html            # Page compacte façon calendrier pour widget iFrame
├── static/style.css                 # Style
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
