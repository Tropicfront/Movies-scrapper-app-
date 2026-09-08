# Sorties Films — Blu-ray / DVD / 4K Ultra HD

Application Docker qui récupère automatiquement le planning des sorties
Blu-ray / DVD / 4K Ultra HD depuis **4k-ultra-hd.fr** et **edition-limitee.fr**,
les enrichit avec les **affiches TMDB**, les croise avec **Jellyfin** /
**Radarr** / **Sonarr**, et expose le tout via une page web et un **flux
calendrier (.ics)** prêt à brancher sur un dashboard type **Homarr** ou
**Homepage**.

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

### Correctif de l'horaire fantôme ("02:00 - 23:59")

Si tu as déjà testé une version précédente, tu as peut-être vu chaque
sortie affichée avec un horaire du type "02:00 - 23:59" au lieu d'un
événement "journée entière" sans heure. **C'est corrigé.** La cause : les
événements "journée entière" (une date, sans heure) doivent porter un
`DTEND` explicite (date de fin = jour suivant) pour être reconnus comme
tels par la plupart des parseurs ICS, dont celui utilisé par Homarr — sans
lui, certains parseurs calculent une durée par défaut à partir de minuit
UTC puis la reconvertissent dans ton fuseau local, ce qui produit cet
horaire qui n'a aucun sens. Le flux ajoute maintenant :
- `DTSTART`/`DTEND` en `VALUE=DATE` (forme standard, celle utilisée par
  Google/Apple/Outlook pour les événements d'un jour)
- `TRANSP:TRANSPARENT` et `X-MICROSOFT-CDO-ALLDAYEVENT:TRUE` (marqueurs
  additionnels reconnus par de nombreux clients calendrier)

Si un ancien flux était déjà en cache côté Homarr/Homepage, un
rafraîchissement forcé du widget (ou une purge de cache) peut être
nécessaire pour voir la correction.

### Sur les affiches dans le calendrier

**Aucun widget calendrier (Homarr, Homepage, Google/Apple/Outlook inclus)
n'affiche d'affiche/poster à partir d'un flux `.ics`** — ce n'est pas
prévu par le format, quel que soit le contournement technique. La carte
avec poster + bouton IMDb que tu as vue vient du **widget natif
Radarr/Sonarr** de Homarr, qui se connecte directement à ton instance
Radarr/Sonarr (pas à un flux ICS externe) et affiche les données que
Radarr/Sonarr gèrent eux-mêmes. Ce rendu n'est donc reproductible que si
tu utilises Radarr/Sonarr pour les titres concernés, via leur propre
widget dans Homarr.

Sur la page web de cette app (`http://<ton-serveur>:8080/`), les affiches
s'affichent normalement (voir section TMDB ci-dessous) — c'est uniquement
dans un flux `.ics` que ce n'est pas possible.

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

## Affiches (TMDB)

Renseigne `TMDB_API_KEY` (clé gratuite sur
[themoviedb.org](https://www.themoviedb.org) → Paramètres → API) pour que
chaque sortie récupère son affiche et un lien vers sa fiche TMDB. Un badge
**IMDb** (jaune, à gauche du titre) est aussi affiché quand disponible —
récupéré via l'endpoint `external_ids` de TMDB (pas de clé IMDb séparée
nécessaire, IMDb n'ayant pas d'API publique). Cache persistant
(`posters.json`), retenté tous les 7 jours pour les titres non trouvés.

## Intégrations Jellyfin / Radarr / Sonarr

Chacune est indépendante et optionnelle (laisser les variables vides pour
désactiver) :

| Variables | Effet |
|---|---|
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | Badge 📀 sur les sorties déjà présentes dans ta bibliothèque Jellyfin |
| `RADARR_URL` / `RADARR_API_KEY` | Badge 🎬 sur les sorties déjà suivies dans Radarr |
| `SONARR_URL` / `SONARR_API_KEY` | Badge 📺 sur les sorties déjà suivies dans Sonarr |

Clés API : Jellyfin → Tableau de bord → Paramètres avancés → Clés API.
Radarr/Sonarr → Paramètres → Général → Sécurité → Clé API.

> Ces intégrations ne font que **lire** tes bibliothèques pour comparer
> les titres (comparaison normalisée + tolérance aux petites variations
> via `difflib`) — elles n'ajoutent, ne modifient ni ne suppriment rien
> dans Jellyfin/Radarr/Sonarr.

## Démarrage rapide

```bash
docker compose up -d --build
```

Puis ouvre : http://localhost:8080. Le premier scraping se lance
automatiquement, puis se répète toutes les `REFRESH_HOURS` heures.

## docker-compose.yml complet

Ce fichier utilise directement l'image publiée (voir section ci-dessus) plutôt que de builder depuis les sources :

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
      - RADARR_URL=http://192.168.1.X:7878      # URL de ton instance Radarr (laisser vide pour désactiver)
      - RADARR_API_KEY=                          # clé API Radarr (Paramètres > Général > Sécurité)
      - SONARR_URL=http://192.168.1.X:8989      # URL de ton instance Sonarr (laisser vide pour désactiver)
      - SONARR_API_KEY=                          # clé API Sonarr (Paramètres > Général > Sécurité)
    volumes:
      - sorties-data:/app/data
    restart: unless-stopped

volumes:
  sorties-data:
```

## Configuration (détail des variables)

- `REFRESH_HOURS` : fréquence de rafraîchissement automatique (défaut : 6)
- `APP_TIMEZONE` : fuseau horaire IANA utilisé pour afficher l'heure de dernière mise à jour (défaut : `Europe/Paris`, ex. `America/New_York`, `Asia/Tokyo`)
- `EDITION_LIMITEE_MONTH_ARTICLES` : nombre d'articles mensuels scrapés sur edition-limitee.fr (défaut : 3)
- `JELLYFIN_URL` / `JELLYFIN_API_KEY` / `JELLYFIN_FUZZY_CUTOFF` (défaut 0.88)
- `TMDB_API_KEY` / `TMDB_LANGUAGE` (défaut fr-FR)
- `RADARR_URL` / `RADARR_API_KEY`
- `SONARR_URL` / `SONARR_API_KEY`
- `ARR_FUZZY_CUTOFF` : tolérance de comparaison de titres Radarr/Sonarr (défaut : 0.88)
- `PORT` : port interne du serveur (défaut : 5000, exposé en 8080 côté hôte)

## Endpoints

- `GET /` — page web (prochaine sortie, planning avec affiches, dates à préciser, sorties récentes)
- `GET /api/releases` — JSON (`?jellyfin=only`, `?category=4k|bluray`)
- `GET /calendar.ics` — flux iCalendar complet, trié par date (recommandé)
- `GET /calendar-4k.ics` / `GET /calendar-bluray.ics` — flux scindés par format
- `GET /api/jellyfin/status`, `/api/radarr/status`, `/api/sonarr/status` — vérifient chaque intégration
- `POST /api/refresh` — force un rafraîchissement immédiat
- `GET /health` — healthcheck

## Persistance

`releases.json` et `posters.json` sont sauvegardés dans le volume Docker
`sorties-data`. En cas d'échec d'une source (site, Jellyfin, Radarr,
Sonarr, TMDB indisponible), l'ancien cache est conservé et l'erreur
s'affiche en haut de la page.

## Développement local (sans Docker)

```bash
pip install -r requirements.txt
python app.py
```

Modules testables isolément :
```bash
python scraper_4k.py
python scraper_editionlimitee.py
python calendar_feed.py
JELLYFIN_URL=... JELLYFIN_API_KEY=... python jellyfin_client.py
RADARR_URL=... RADARR_API_KEY=... python radarr_sonarr_client.py
TMDB_API_KEY=... python poster_lookup.py
```

## Structure du projet

```
.
├── app.py                       # Flask + planificateur + fusion des sources
├── date_utils.py                 # Dates FR, normalisation titres, classification format
├── scraper_4k.py                  # Scraper 4k-ultra-hd.fr
├── scraper_editionlimitee.py      # Scraper edition-limitee.fr
├── jellyfin_client.py             # Croisement Jellyfin
├── radarr_sonarr_client.py         # Croisement Radarr / Sonarr
├── poster_lookup.py                # Affiches via TMDB
├── calendar_feed.py                # Génération des flux iCalendar (.ics)
├── templates/index.html            # Page web
├── static/style.css                # Style
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
