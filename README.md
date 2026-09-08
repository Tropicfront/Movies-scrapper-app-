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

**Flux principal, à utiliser par défaut :**
```
http://<ton-serveur>:8080/calendar.ics
```
Il contient **toutes** les sorties (4K et Blu-ray/DVD mélangées), triées
par date — un film 4K et un Blu-ray du même jour apparaissent donc bien
ensemble, groupés sous la même date, quel que soit leur format ou leur
site source. Chaque titre est préfixé d'un pictogramme pour distinguer le
format d'un coup d'œil : 🟣 4K Ultra HD, 🔵 Blu-ray, 🔴 DVD.

Deux flux additionnels existent si tu préfères deux panneaux séparés
(au prix de ne plus voir les deux formats mélangés au même jour) :
- `/calendar-4k.ics` — uniquement les sorties 4K
- `/calendar-bluray.ics` — uniquement les sorties Blu-ray/DVD

Paramètres optionnels (sur les 3 flux) : `?scope=all` (inclut l'historique
récent), `?jellyfin=only` (uniquement ce que tu as déjà).

### Configuration Homepage
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
          url: http://<ton-serveur>:8080/calendar.ics
          name: Sorties Films
          color: yellow
          params:
            showName: true
```

### Configuration Homarr
Menu **Intégrations** → Ajouter → **iCal**, avec l'URL
`http://<ton-serveur>:8080/calendar.ics`, puis ajoute un widget
**Calendar** et sélectionne cette intégration.

## Affiches sur le dashboard : le widget iFrame

**Aucun widget calendrier (Homarr, Homepage, Google/Apple/Outlook inclus)
n'affiche d'affiche/poster à partir d'un flux `.ics`** — ce n'est pas
prévu par le format, quel que soit le contournement technique.

Pour contourner cette limite, l'app expose une **page compacte dédiée à
l'embarquement en iFrame**, avec affiches et bouton vers la fiche TMDB —
puisqu'il s'agit d'une vraie page HTML et non d'un flux calendrier, ces
éléments s'affichent normalement :
```
http://<ton-serveur>:8080/widget/upcoming
```

Paramètres optionnels :
- `?limit=10` — nombre de sorties affichées (défaut : 10, max : 50)
- `?scope=upcoming` — `upcoming` (défaut), `today`, `tomorrow`, ou `all` (à venir + récentes)
- `?jellyfin=only` — uniquement les films déjà présents dans Jellyfin
- `?category=4k` ou `?category=bluray` — filtrer par format
- `?theme=dark` (défaut) ou `?theme=light`

#### Configuration Homepage (widget iFrame natif)
```yaml
- Prochaines sorties:
    widget:
      type: iframe
      src: http://<ton-serveur>:8080/widget/upcoming?limit=8
      classes: h-80 sm:h-80 md:h-96 lg:h-96 xl:h-96
```

#### Configuration Homarr (widget iFrame natif)
Ajoute une tuile → **Widgets** → **iFrame**, colle l'URL
`http://<ton-serveur>:8080/widget/upcoming?limit=8`, puis ajuste la
hauteur de la tuile selon le nombre de sorties affichées.

## Affiches (TMDB)

Renseigne `TMDB_API_KEY` (clé gratuite sur
[themoviedb.org](https://www.themoviedb.org) → Paramètres → API) pour que
chaque sortie récupère son affiche et un badge **TMDB** (à gauche du
titre) qui pointe vers sa fiche complète — utilisé à la fois sur la page
web et sur le widget iFrame ci-dessus. Cache persistant (`posters.json`),
retenté tous les 7 jours pour les titres non trouvés.

## Intégration Jellyfin

Renseigne `JELLYFIN_URL` et `JELLYFIN_API_KEY` (Jellyfin → Tableau de
bord → Paramètres avancés → Clés API) pour que chaque sortie déjà
présente dans ta bibliothèque affiche un badge **📀 Déjà dans Jellyfin**
(comparaison de titres normalisée + tolérance aux petites variations via
`difflib`). Laisser les variables vides désactive l'intégration sans
impact sur le reste de l'app.

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
├── scraper_4k.py                  # Scraper 4k-ultra-hd.fr
├── scraper_editionlimitee.py      # Scraper edition-limitee.fr
├── jellyfin_client.py             # Croisement Jellyfin
├── poster_lookup.py                # Affiches via TMDB
├── calendar_feed.py                # Génération des flux iCalendar (.ics)
├── templates/index.html            # Page web
├── templates/widget.html           # Page compacte pour widget iFrame
├── static/style.css                # Style
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
