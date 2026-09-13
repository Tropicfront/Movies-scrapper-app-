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
l'embarquement en iFrame**, présentée comme une vraie **grille de
calendrier mensuel** (jours de la semaine en colonnes, un point coloré
par format sur chaque jour où il y a une sortie — 🟣 4K, 🔵 Blu-ray, 🔴
DVD), avec navigation mois précédent/suivant. Cliquer sur un jour qui a
des sorties ouvre la liste des titres de ce jour (avec affiche, lien vers
le site source, boutons Amazon/Fnac) :
```
http://<ton-serveur>:8080/widget/upcoming
```

La grille reprend l'allure du widget calendrier de Homarr : pas de cadre
autour des cases, numéros de jour seuls, week-ends en rouge et mois voisins
estompés. Le jour courant est entouré d'un **contour doré**.

Chaque jour porte une **pastille par format** présent ce jour-là :

| Pastille | Signification |
|---|---|
| point violet | 4K Ultra HD |
| point bleu | Blu-ray |
| point rouge | DVD |
| point gris | format non reconnu |
| anneau vert | au moins une édition **steelbook** ce jour-là |

La pastille steelbook est un anneau et non un point plein, pour ne pas la
confondre avec un format. Elle se déclenche sur « steelbook » ou « steel
book » trouvé dans le titre, le descriptif ou le format, et le badge
correspondant réapparaît dans la fenêtre du jour pour identifier de quelle
sortie il s'agit. Le détail (nombre de sorties et liste des formats)
s'affiche aussi au survol de la case.

La grille **s'étire pour occuper toute la hauteur** de la tuile : les
lignes se partagent la place disponible, donc pas de vide en bas, et pas
de débordement sur les mois qui comptent six semaines.

La grille affiche **tous les jours du mois**, y compris ceux déjà passés,
et complète chaque mois avec **les jours débordant sur les mois voisins**
(ex. le 31 août devant le lundi 1er septembre, le 1er au 4 octobre après le
30 septembre), affichés en grisé mais tout aussi cliquables. Les flèches de
l'en-tête font défiler les mois un par un, sans jamais en sauter un (les
touches ← / → fonctionnent aussi).

Le détail d'une journée est chargé **à la demande** depuis
`/widget/day/<date>` au moment du clic : la grille elle-même ne contient
que les dates et le nombre de sorties par jour, ce qui garde la page du
widget légère (~100 Ko pour 9 mois, contre ~350 Ko quand le détail de
chaque jour y était embarqué) et évite de télécharger des centaines
d'affiches TMDB à chaque rechargement du dashboard.

Cliquer sur un jour ouvre une **fenêtre large** listant ses sorties avec
l'affiche TMDB, le format, la source, le badge Jellyfin et les boutons
Amazon/Fnac — sans horaire, une sortie étant un événement de journée
entière. On la referme avec la croix, un clic à côté, ou `Échap`.

Paramètres optionnels :
- `?limit=` — **accepté mais ignoré**, volontairement : les sorties étant triées par date, toute troncature supprimait les derniers mois du calendrier (un `?limit=8` hérité d'une vieille config ne laissait qu'un seul jour affiché, flèches de navigation grisées). Une grille de calendrier n'a pas besoin d'être plafonnée : son poids dépend du nombre de mois, pas du nombre de sorties
- `?scope=all` — `all` (défaut : toutes les sorties datées, passées comprises), `upcoming` (à partir d'aujourd'hui), `today`, `tomorrow`
- `?jellyfin=only` — uniquement les films/séries déjà présents dans Jellyfin
- `?category=4k` ou `?category=bluray` — filtrer par format
- `?theme=dark` (défaut) ou `?theme=light`

#### Configuration Homepage (widget iFrame natif)
```yaml
- Calendrier des sorties:
    widget:
      type: iframe
      src: http://<ton-serveur>:8080/widget/upcoming
      classes: h-[32rem] sm:h-[32rem] md:h-[34rem] lg:h-[34rem] xl:h-[34rem]
```
Prévoir une tuile plutôt haute : la fenêtre qui s'ouvre au clic sur un jour
s'affiche à l'intérieur de l'iFrame, et une tuile trop basse la rendrait
étroite (elle reste défilable dans tous les cas).

#### Configuration Homarr (widget iFrame natif)
Ajoute une tuile → **Widgets** → **iFrame**, colle l'URL
`http://<ton-serveur>:8080/widget/upcoming`, puis ajuste la
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

| Source | Où ces liens sont cherchés |
|---|---|
| 4k-ultra-hd.fr | sur la page individuelle de chaque film (`/film/<slug>`) |
| edition-limitee.fr | sur la fiche de chaque film ou série — l'article mensuel ne contient que les titres et les formats |

Dans les deux cas il faut donc **une requête HTTP par titre**, mise en
cache (`affiliate_links.json` et `affiliate_links_el.json` dans le volume
de données) pour ne pas la refaire à chaque rafraîchissement. Côté
edition-limitee.fr, au maximum 150 fiches sont visitées par
rafraîchissement (`MAX_LOOKUPS_PER_RUN`) : avec ~200 sorties au catalogue,
les boutons finissent donc de se remplir au deuxième rafraîchissement. Une
fiche qui n'a donné aucun lien est retentée au bout de 7 jours.

La reconnaissance du marchand (`date_utils.classify_purchase_link`) se fait
d'abord sur le **libellé** du lien — son texte (« ici sur Amazon »), ou
l'`alt`/`title` du logo (« FNAC France ») — et seulement ensuite sur l'URL.
C'est nécessaire parce que les raccourcisseurs d'affiliation (`tidd.ly`,
`awin1.com`) servent indifféremment à la Fnac, à Cultura ou à E.Leclerc :
l'URL seule ne dit pas de quel marchand il s'agit. Les marchands autres
qu'Amazon et Fnac sont explicitement écartés, et les liens Amazon directs
(`amazon.fr/dp/...?tag=...`) sont reconnus en plus des `amzn.to`.

Le **titre**, lui, pointe toujours vers la fiche du site source
(4k-ultra-hd.fr ou edition-limitee.fr) — jamais vers un lien affilié —
même quand le site source utilise directement un lien affilié comme lien
principal (dans ce cas on retombe sur la page/l'article du site source).

> Cette détection dépend de la structure HTML de chaque site au moment du
> scraping (calibrée à partir d'exemples réels) ; si un site change sa
> mise en page, les boutons peuvent temporairement ne plus apparaître sans
> que le reste du scraping en soit affecté.

**Pour vérifier toi-même si la détection fonctionne**, sans avoir à
inspecter les logs du conteneur : `GET /api/affiliate-links/status`
retourne, par source, combien de sorties ont un lien Amazon/Fnac détecté
(quelques exemples de titres qui n'en ont pas, et le nombre de fiches déjà
visitées par cache), par exemple :
```bash
curl http://<ton-serveur>:8080/api/affiliate-links/status
```

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

- `GET /` — page web (planning à venir avec affiches, dates à préciser, sorties récentes) — les blocs « Aujourd'hui » et « Demain » occupent toute la largeur de la page et répartissent leurs fiches sur 1 à 4 colonnes selon l'écran
- `GET /widget/upcoming` — page compacte pour widget iFrame (voir section dédiée)
- `GET /api/releases` — JSON (`?jellyfin=only`, `?category=4k|bluray`)
- `GET /calendar.ics` — flux iCalendar complet, trié par date (recommandé)
- `GET /calendar-4k.ics` / `GET /calendar-bluray.ics` — flux scindés par format
- `GET /api/jellyfin/status` — vérifie la connexion à Jellyfin
- `GET /api/affiliate-links/status` — diagnostic des liens Amazon/Fnac (nombre de sorties avec/sans lien, par source)
- `POST /api/refresh` — force un rafraîchissement immédiat
- `GET /widget/day/<AAAA-MM-JJ>` — fragment HTML des sorties d'une journée, utilisé par le widget au clic sur une case (accepte les mêmes `?category=` et `?jellyfin=`)
- `GET /api/debug/calendar` — diagnostic du calendrier : contenu du cache mois par mois et jour par jour, date vue par le conteneur, plage de dates couverte
- `GET /health` — healthcheck (renvoie aussi `build`, le marqueur de version du code en cours d'exécution)

## Vérifier quelle version du code tourne

Le `docker-compose.yml` d'exemple utilise l'image publiée
`tropicfront/movies_scrapper:latest` : **modifier les fichiers en local ne
change rien au conteneur** tant que cette ligne est active. Pour faire
tourner ton code, commente `image:`, décommente `build: .` et relance avec
`docker compose up -d --build`.

Pour confirmer ce qui tourne réellement :
```bash
curl http://<ton-serveur>:8080/health     # -> {"status":"ok","build":"..."}
```
Le même marqueur est affiché en pied de page du site.

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
├── templates/widget_day.html        # Fragment : sorties d'une journée, chargé au clic
├── static/style.css                 # Style
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
