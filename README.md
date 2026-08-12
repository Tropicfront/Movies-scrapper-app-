# Sorties Films — Blu-ray / DVD / 4K Ultra HD

Application Docker qui récupère automatiquement le planning des sorties
Blu-ray / DVD / 4K Ultra HD depuis **4k-ultra-hd.fr** et **edition-limitee.fr**,
les croise avec ta bibliothèque **Jellyfin** (pour repérer ce que tu as déjà),
et expose le tout via une page web et un **flux calendrier (.ics)** prêt à
brancher sur un dashboard type **Homarr** ou **Homepage**.

## Sources de sorties

| Site | Ce qui est scrapé |
|---|---|
| **4k-ultra-hd.fr** | Page "Prochaines sorties 4K" (paginée, ~140 titres) + page "Dates en attente" (éditions annoncées sans date précise) |
| **edition-limitee.fr** | Articles mensuels du calendrier ("Août 2026", "Juillet 2026"...), repérés automatiquement depuis la page hub `/blu-ray-dvd/sortie-blu-ray-dvd/`. Le site publie généralement le mois en cours + 1-2 mois à l'avance. |

> bluray-mania.com a été testé dans une première version mais écarté :
> les informations de sa page "planning des sorties" manquaient de précision.

Chaque film récupéré porte une étiquette indiquant sa source (ou les deux,
si le même titre/date est trouvé sur les deux sites — dans ce cas les
entrées sont fusionnées). Les dates non précises ("(prochainement)", "3e
trimestre 2026"...) sont classées à part plutôt que d'être ignorées.

### Pourquoi le parsing est robuste
Aucun des deux scrapers ne dépend de classes CSS précises (qui changent
facilement lors d'une mise à jour de thème). Ils repèrent les informations
par motif de texte :
- **4k-ultra-hd.fr** : un lien vers une fiche `/film/<slug>` suivi d'une
  ligne "Sortie **DATE** : **Édition** Format *(Année)*"
- **edition-limitee.fr** : un lien dont le texte commence par "ici en"
  (ex: "ici en Blu-ray et DVD"), dans un bloc du type
  "**Titre** ici en Formats. Sorti le Date."

## Intégration Jellyfin

Si tu renseignes `JELLYFIN_URL` et `JELLYFIN_API_KEY`, l'app interroge ta
bibliothèque Jellyfin (films) à chaque rafraîchissement et marque chaque
sortie avec un badge **📀 Déjà dans Jellyfin** quand elle correspond à un
film que tu possèdes déjà (utile pour repérer les rééditions/upgrades —
ex. un Steelbook 4K d'un film que tu as pour l'instant en DVD).

La comparaison se fait sur les titres normalisés (minuscules, sans accents,
sans mentions de format/édition comme "Blu-ray", "4K", "Steelbook"...),
avec un filet de comparaison approximative pour les petites variations de
formulation.

**Comment récupérer ta clé API Jellyfin** : Tableau de bord Jellyfin →
Paramètres avancés → Clés API → "+" pour en créer une nouvelle.

Sur la page web, un bouton "📀 Voir seulement ce que j'ai déjà" permet de
filtrer l'affichage. Côté API, ajoute `?jellyfin=only` à `/api/releases`
ou `/calendar.ics`.

> Si Jellyfin n'est pas configuré, l'app fonctionne normalement, simplement
> sans les badges/filtre (aucune erreur, aucun impact sur le scraping).

## Calendrier pour dashboard (Homarr / Homepage)

L'app expose un flux **iCalendar (.ics)**, le format standard reconnu
nativement par les widgets "Calendar" de Homarr et Homepage (et par
Google/Apple/Outlook Calendar si tu veux t'en servir ailleurs) :

```
http://<ton-serveur>:8080/calendar.ics
```

Paramètres optionnels (à ajouter dans l'URL) :
- `?scope=all` — inclut aussi les 30 dernières sorties passées (par défaut,
  seules les sorties à venir sont incluses)
- `?jellyfin=only` — uniquement les sorties correspondant à un film déjà
  présent dans ta bibliothèque Jellyfin

Exemple combiné : `http://<ton-serveur>:8080/calendar.ics?scope=all&jellyfin=only`

### Configuration Homepage

Dans `services.yaml` :
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

Menu **Intégrations** → Ajouter → **iCal**, avec comme URL
`http://<ton-serveur>:8080/calendar.ics`, puis ajoute un widget
**Calendar** sur ton board et sélectionne cette intégration.

## Démarrage rapide

```bash
docker compose up -d --build
```

Puis ouvre : http://localhost:8080

Le premier scraping se lance automatiquement au démarrage, puis se répète
toutes les `REFRESH_HOURS` heures (6h par défaut). Un scraping complet
prend en général 30s à 1min.

## Configuration

Variables d'environnement (dans `docker-compose.yml`) :

- `REFRESH_HOURS` : fréquence de rafraîchissement automatique (défaut : 6)
- `EDITION_LIMITEE_MONTH_ARTICLES` : nombre d'articles mensuels à scraper
  sur edition-limitee.fr (défaut : 3)
- `JELLYFIN_URL` : URL de ton serveur Jellyfin, ex. `http://192.168.1.10:8096`
  (laisser vide pour désactiver l'intégration)
- `JELLYFIN_API_KEY` : ta clé API Jellyfin
- `JELLYFIN_FUZZY_CUTOFF` : seuil de tolérance pour la comparaison
  approximative des titres (0 à 1, défaut : 0.88 — plus bas = plus permissif,
  au risque de faux positifs)
- `PORT` : port interne du serveur (défaut : 5000, exposé en 8080 côté hôte)

## Endpoints

- `GET /` — page web (prochaine sortie, planning à venir, dates à
  préciser, sorties récentes)
- `GET /api/releases` — données JSON (`?jellyfin=only` pour filtrer)
- `GET /calendar.ics` (alias `/api/calendar.ics`) — flux iCalendar
  (`?scope=all`, `?jellyfin=only`)
- `GET /api/jellyfin/status` — vérifie la connexion à Jellyfin et le
  nombre de films détectés dans la bibliothèque
- `POST /api/refresh` — force un rafraîchissement immédiat
- `GET /health` — healthcheck

## Persistance

Les données scrapées sont sauvegardées dans un volume Docker
(`sorties-data`) sous forme de fichier JSON. Si un scraping échoue (site
indisponible, changement de structure, Jellyfin injoignable...), l'ancien
cache est conservé et l'erreur est affichée en haut de la page plutôt que
de vider les données.

## Sans Docker Compose

```bash
docker build -t sorties-films .
docker run -d -p 8080:5000 \
  -e JELLYFIN_URL=http://192.168.1.10:8096 \
  -e JELLYFIN_API_KEY=xxxxx \
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
```

## Structure du projet

```
.
├── app.py                       # Application Flask + planificateur + fusion des sources
├── date_utils.py                 # Parsing des dates françaises, requêtes HTTP
├── scraper_4k.py                  # Scraper 4k-ultra-hd.fr
├── scraper_editionlimitee.py      # Scraper edition-limitee.fr
├── jellyfin_client.py             # Croisement avec la bibliothèque Jellyfin
├── calendar_feed.py                # Génération du flux iCalendar (.ics)
├── templates/index.html            # Page web
├── static/style.css                # Style
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
