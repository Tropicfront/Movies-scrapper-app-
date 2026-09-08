# Sorties Films — Blu-ray / DVD / 4K Ultra HD

Application Docker qui récupère automatiquement le planning des sorties
Blu-ray / DVD / 4K Ultra HD depuis **4k-ultra-hd.fr** et **edition-limitee.fr**,
les enrichit avec les **affiches TMDB**, les croise avec ta bibliothèque
**Jellyfin**, et expose le tout via une page web et des **flux calendrier
(.ics)** prêts à brancher sur un dashboard type **Homarr** ou **Homepage**.

## Sources de sorties

| Site | Ce qui est scrapé |
|---|---|
| **4k-ultra-hd.fr** | Page "Prochaines sorties 4K" (paginée, ~140 titres) + page "Dates en attente" (éditions annoncées sans date précise) |
| **edition-limitee.fr** | Articles mensuels du calendrier ("Août 2026", "Juillet 2026"...), repérés automatiquement depuis la page hub `/blu-ray-dvd/sortie-blu-ray-dvd/`. Le site publie généralement le mois en cours + 1-2 mois à l'avance. |

> bluray-mania.com a été testé dans une première version mais écarté :
> les informations de sa page "planning des sorties" manquaient de précision.

### Dédoublonnage

Quand le même film/la même série sort à la même date sur les deux sites
(ex. "Ghost in the Shell 4K Steelbook" sur 4k-ultra-hd.fr et "Ghost in the
Shell" sur edition-limitee.fr), une seule entrée est conservée — celle de
**4k-ultra-hd.fr** en priorité. La comparaison se fait sur des titres
normalisés (minuscules, sans accents, sans mentions de format/édition)
pour détecter ces doublons même quand les intitulés diffèrent légèrement
d'un site à l'autre.

### Pourquoi le parsing est robuste
Aucun des deux scrapers ne dépend de classes CSS précises (qui changent
facilement lors d'une mise à jour de thème). Ils repèrent les informations
par motif de texte :
- **4k-ultra-hd.fr** : un lien vers une fiche `/film/<slug>` suivi d'une
  ligne "Sortie **DATE** : **Édition** Format *(Année)*"
- **edition-limitee.fr** : un lien dont le texte commence par "ici en"
  (ex: "ici en Blu-ray et DVD"), dans un bloc du type
  "**Titre** ici en Formats. Sorti le Date."

Les dates non précises ("(prochainement)", "3e trimestre 2026"...) sont
classées à part dans une section "Annoncées, date à préciser" plutôt que
d'être ignorées.

## Affiches (TMDB)

Si tu renseignes `TMDB_API_KEY`, chaque sortie est enrichie avec :
- son affiche (récupérée via l'API de recherche multi TMDB, films + séries)
- un lien vers sa fiche TMDB (utilisé comme lien principal de l'affiche et
  comme URL de l'événement dans le calendrier)

**Pourquoi TMDB plutôt que TVDB ou IMDb** : API gratuite avec simple
inscription, couvre films *et* séries en une seule recherche, fiches en
français. IMDb n'a pas d'API publique officielle (le scraping violerait
leurs CGU) ; TVDB est plus orienté séries et limité sans compte payant.

**Comment récupérer ta clé API TMDB** : crée un compte gratuit sur
[themoviedb.org](https://www.themoviedb.org), puis Paramètres → API →
"Créer" → clé API (v3 auth). Renseigne-la dans `TMDB_API_KEY`.

Le titre scrapé est nettoyé avant recherche (retrait des mentions "4K",
"Steelbook", "Blu-ray", "Saison X"...) pour maximiser les chances de
trouver la bonne fiche. Les résultats sont mis en cache indéfiniment une
fois trouvés (fichier `posters.json` dans le volume de données) ; les
titres non trouvés sont retentés tous les 7 jours. Si un titre ne
correspond à aucune affiche, un pictogramme 🎬 est affiché à la place.

> Si `TMDB_API_KEY` n'est pas configuré, l'app fonctionne normalement,
> simplement sans affiches.

## Intégration Jellyfin

Si tu renseignes `JELLYFIN_URL` et `JELLYFIN_API_KEY`, l'app interroge ta
bibliothèque Jellyfin (films) à chaque rafraîchissement et marque chaque
sortie avec un badge **📀 Déjà dans Jellyfin** quand elle correspond à un
film que tu possèdes déjà (utile pour repérer les rééditions/upgrades —
ex. un Steelbook 4K d'un film que tu as pour l'instant en DVD).

**Comment récupérer ta clé API Jellyfin** : Tableau de bord Jellyfin →
Paramètres avancés → Clés API → "+" pour en créer une nouvelle.

Sur la page web, un bouton "📀 Voir seulement ce que j'ai déjà" permet de
filtrer l'affichage. Côté API/calendrier, ajoute `?jellyfin=only`.

> Si Jellyfin n'est pas configuré, l'app fonctionne normalement, simplement
> sans les badges/filtre.

## Calendrier pour dashboard (Homarr / Homepage)

L'app expose des flux **iCalendar (.ics)**, le format standard reconnu
nativement par les widgets "Calendar" de Homarr et Homepage (et par
Google/Apple/Outlook Calendar) :

| Flux | Contenu |
|---|---|
| `/calendar.ics` | Toutes les sorties |
| `/calendar-4k.ics` | Uniquement les sorties 4K Ultra HD |
| `/calendar-bluray.ics` | Uniquement les sorties Blu-ray / DVD (non-4K) |

**Sur la couleur par format** : les widgets calendrier de Homarr/Homepage
appliquent une couleur *par flux/intégration*, pas événement par événement.
Il n'existe pas de mécanisme pour colorer différemment deux événements
d'un même flux ICS dans ces dashboards. La solution est donc d'ajouter
**les deux flux `/calendar-4k.ics` et `/calendar-bluray.ics` comme deux
intégrations distinctes**, chacune avec sa propre couleur — c'est ce que
montrent les exemples ci-dessous. (Chaque événement porte tout de même une
propriété `COLOR` standard RFC 7986 dans le flux complet `/calendar.ics`,
pour les clients qui la supportent, comme Apple Calendar.)

Paramètres optionnels (cumulables, sur les 3 flux) :
- `?scope=all` — inclut aussi les 30 dernières sorties passées (par défaut,
  seules les sorties à venir sont incluses)
- `?jellyfin=only` — uniquement les sorties correspondant à un film déjà
  présent dans ta bibliothèque Jellyfin

### Configuration Homepage

Dans `services.yaml`, une entrée par flux :
```yaml
- Sorties 4K:
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
(`.../calendar-4k.ics` et `.../calendar-bluray.ics`), en leur donnant une
couleur différente, puis ajoute un widget **Calendar** sur ton board et
sélectionne les deux intégrations.

## Démarrage rapide

```bash
docker compose up -d --build
```

Puis ouvre : http://localhost:8080

Le premier scraping se lance automatiquement au démarrage, puis se répète
toutes les `REFRESH_HOURS` heures (6h par défaut). Un scraping complet
prend en général 30s à 2min (scraping des deux sites + recherches TMDB
pour les nouveaux titres, avec pauses courtes entre les requêtes).

## Configuration

Variables d'environnement (dans `docker-compose.yml`) :

- `REFRESH_HOURS` : fréquence de rafraîchissement automatique (défaut : 6)
- `EDITION_LIMITEE_MONTH_ARTICLES` : nombre d'articles mensuels à scraper
  sur edition-limitee.fr (défaut : 3)
- `JELLYFIN_URL` / `JELLYFIN_API_KEY` : intégration Jellyfin (laisser vide
  pour désactiver)
- `JELLYFIN_FUZZY_CUTOFF` : seuil de tolérance pour la comparaison
  approximative des titres Jellyfin (0 à 1, défaut : 0.88)
- `TMDB_API_KEY` : intégration TMDB pour les affiches (laisser vide pour
  désactiver)
- `TMDB_LANGUAGE` : langue des fiches/affiches TMDB (défaut : fr-FR)
- `PORT` : port interne du serveur (défaut : 5000, exposé en 8080 côté hôte)

## Endpoints

- `GET /` — page web (prochaine sortie, planning à venir avec affiches,
  dates à préciser, sorties récentes)
- `GET /api/releases` — données JSON (`?jellyfin=only`, `?category=4k|bluray`)
- `GET /calendar.ics` (alias `/api/calendar.ics`) — flux iCalendar complet
- `GET /calendar-4k.ics` — flux iCalendar, sorties 4K uniquement
- `GET /calendar-bluray.ics` — flux iCalendar, sorties Blu-ray/DVD uniquement
- `GET /api/jellyfin/status` — vérifie la connexion à Jellyfin et le
  nombre de films détectés dans la bibliothèque
- `POST /api/refresh` — force un rafraîchissement immédiat
- `GET /health` — healthcheck

## Persistance

Les données scrapées (`releases.json`) et le cache d'affiches TMDB
(`posters.json`) sont sauvegardés dans un volume Docker (`sorties-data`).
Si un scraping échoue (site indisponible, changement de structure,
Jellyfin/TMDB injoignable...), l'ancien cache est conservé et l'erreur est
affichée en haut de la page plutôt que de vider les données.

## Sans Docker Compose

```bash
docker build -t sorties-films .
docker run -d -p 8080:5000 \
  -e JELLYFIN_URL=http://192.168.1.10:8096 \
  -e JELLYFIN_API_KEY=xxxxx \
  -e TMDB_API_KEY=xxxxx \
  -v sorties-data:/app/data \
  sorties-films
```

## Développement local (sans Docker)

```bash
pip install -r requirements.txt
python app.py
```

Pour tester un module isolément :

```bash
python scraper_4k.py
python scraper_editionlimitee.py
python calendar_feed.py
JELLYFIN_URL=http://... JELLYFIN_API_KEY=... python jellyfin_client.py
TMDB_API_KEY=... python poster_lookup.py
```

## Structure du projet

```
.
├── app.py                       # Application Flask + planificateur + fusion des sources
├── date_utils.py                 # Dates FR, normalisation de titres, classification de format
├── scraper_4k.py                  # Scraper 4k-ultra-hd.fr
├── scraper_editionlimitee.py      # Scraper edition-limitee.fr
├── jellyfin_client.py             # Croisement avec la bibliothèque Jellyfin
├── poster_lookup.py                # Récupération des affiches via TMDB
├── calendar_feed.py                # Génération des flux iCalendar (.ics)
├── templates/index.html            # Page web
├── static/style.css                # Style
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
