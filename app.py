import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, jsonify, render_template, request

import calendar_feed
import jellyfin_client
import poster_lookup
import scraper_4k
import purchase_links
import scraper_editionlimitee
import title_parser
from date_utils import MOIS_FR_NAMES, format_date_label, normalize_title
from urllib.parse import urlencode

from json_cache import load_json_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
CACHE_FILE = os.path.join(DATA_DIR, "releases.json")
POSTER_CACHE_FILE = os.path.join(DATA_DIR, "posters.json")
AFFILIATE_CACHE_FILE = os.path.join(DATA_DIR, "affiliate_links.json")
EL_AFFILIATE_CACHE_FILE = os.path.join(DATA_DIR, "affiliate_links_el.json")
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "6"))
EL_MONTH_ARTICLES = int(os.environ.get("EDITION_LIMITEE_MONTH_ARTICLES", "3"))
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Paris")

# Marqueur de version du code, renvoyé par /health et /api/debug/calendar et
# affiché en pied de page : permet de vérifier que le conteneur tourne bien
# avec les fichiers à jour.
APP_BUILD = "2026-09-22.1"

# Durée maximale d'un rafraîchissement. Au-delà, ce qui reste à récupérer
# est repris par un passage de rattrapage programmé peu après, plutôt que
# d'attendre le rafraîchissement périodique suivant. Objectif : tout avoir
# dès le premier démarrage, sans jamais marteler les sites.
REFRESH_BUDGET_MINUTES = float(os.environ.get("REFRESH_BUDGET_MINUTES", "15"))
FOLLOWUP_DELAY_MINUTES = float(os.environ.get("FOLLOWUP_DELAY_MINUTES", "10"))

# Quand une même sortie (titre normalisé + date) apparaît sur plusieurs
# sources, on ne garde que celle de la source la mieux classée ici.
# En cas de doublon (même titre, même date), la source de plus petit rang
# est conservée. Une source absente de ce tableau reçoit le rang 99, donc
# ne l'emporte jamais : penser à y ajouter toute nouvelle source.
SOURCE_PRIORITY = {
    scraper_4k.SOURCE_NAME: 0,          # 4K-Ultra-HD.fr : gardé en priorité
    scraper_editionlimitee.SOURCE_NAME: 1,
}

# Un seul rafraîchissement à la fois : le job périodique et le bouton
# « Rafraîchir maintenant » peuvent tomber en même temps, et deux passages
# concurrents se marcheraient dessus en écrivant le même cache.
_refresh_lock = threading.Lock()
_scheduler = None
_refresh_state = {"running": False, "started_at": None, "finished_at": None,
                  "pending": 0, "steps": {}}

app = Flask(__name__)
os.makedirs(DATA_DIR, exist_ok=True)


def _get_app_timezone():
    try:
        return ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("APP_TIMEZONE %r invalide, repli sur UTC", APP_TIMEZONE)
        return timezone.utc


def format_updated_at(iso_str):
    """Formate un horodatage ISO (stocké en UTC) en heure locale, au format
    jour/mois/année à heure:minutes:seconde, dans le fuseau APP_TIMEZONE."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    local_dt = dt.astimezone(_get_app_timezone())
    return local_dt.strftime("%d/%m/%Y à %H:%M:%S")


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Impossible de lire le cache, il sera recréé")
    return {"updated_at": None, "releases": [], "errors": [], "jellyfin_matched": None}


def save_cache(releases, errors, jellyfin_matched):
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "releases": releases,
        "errors": errors,
        "jellyfin_matched": jellyfin_matched,
    }
    # Écriture atomique : voir json_cache.save_json_cache
    tmp = f"{CACHE_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_FILE)
    return payload


def _dedupe(releases):
    """Déduplique par (titre normalisé, date). En cas de doublon entre
    sources, ne garde que l'entrée de la source prioritaire (4K-Ultra-HD.fr)
    plutôt que de fusionner les deux."""
    seen = {}
    for r in releases:
        key = (normalize_title(r.get("title", "")), r.get("date_iso") or r.get("date_text"))
        existing = seen.get(key)
        if existing is None:
            seen[key] = r
            continue
        current_rank = SOURCE_PRIORITY.get(r.get("source"), 99)
        existing_rank = SOURCE_PRIORITY.get(existing.get("source"), 99)
        if current_rank < existing_rank:
            seen[key] = r
    return list(seen.values())


# Sources de sorties, dans l'ordre d'interrogation. Ajouter une source =
# ajouter une entrée ici, rien d'autre côté app.py.
#   name           : nom affiché, doit correspondre au SOURCE_NAME du module
#   module         : module de scraping
#   fetch          : appelable renvoyant la liste des sorties
#   enrich         : appelable (releases, cache_file) complétant les liens
#                    d'achat, ou None si la source les donne directement
#   affiliate_cache: fichier de cache des liens d'achat de cette source
SOURCES = [
    {
        "name": scraper_4k.SOURCE_NAME,
        "fetch": lambda: scraper_4k.get_releases(),
        "enrich": lambda releases, cache, deadline: scraper_4k.enrich_with_affiliate_links(
            releases, cache, deadline=deadline),
        "affiliate_cache": AFFILIATE_CACHE_FILE,
    },
    {
        "name": scraper_editionlimitee.SOURCE_NAME,
        "fetch": lambda: scraper_editionlimitee.get_releases(
            month_articles_limit=EL_MONTH_ARTICLES),
        "enrich": lambda releases, cache, deadline: scraper_editionlimitee.enrich_with_affiliate_links(
            releases, cache, deadline=deadline),
        "affiliate_cache": EL_AFFILIATE_CACHE_FILE,
    },
]


def refresh_data():
    """Rafraîchissement complet. Un seul à la fois ; si le budget de temps
    est épuisé avant d'avoir tout récupéré, un passage de rattrapage est
    programmé quelques minutes plus tard."""
    if not _refresh_lock.acquire(blocking=False):
        logger.info("Rafraîchissement déjà en cours, celui-ci est ignoré")
        return
    try:
        _do_refresh()
    finally:
        _refresh_lock.release()


def _do_refresh():
    logger.info("Rafraîchissement des données (%s)",
                " + ".join(src["name"] for src in SOURCES))
    deadline = time.monotonic() + REFRESH_BUDGET_MINUTES * 60
    _refresh_state.update(running=True, finished_at=None, steps={},
                          started_at=datetime.now(timezone.utc).isoformat())
    errors = []
    releases = []

    for src in SOURCES:
        try:
            found = src["fetch"]()
            releases.extend(found)
            logger.info("[%s] %d sorties récupérées", src["name"], len(found))
        except Exception as exc:
            logger.exception("Échec du scraping %s", src["name"])
            errors.append(f"{src['name']} : {exc}")

    if not releases:
        logger.warning("Aucune sortie récupérée sur aucune source, le cache n'est pas modifié")
        _refresh_state.update(running=False, pending=0,
                              finished_at=datetime.now(timezone.utc).isoformat())
        # Les sources sont peut-être seulement indisponibles : on retente
        # sans attendre le prochain rafraîchissement périodique.
        _schedule_followup("aucune source n'a répondu")
        return

    before = len(releases)
    releases = _dedupe(releases)
    logger.info("Dédoublonnage : %d -> %d sorties", before, len(releases))

    # Les boutons d'achat ne sont, sur les deux sites, que sur la fiche de
    # chaque film : une requête HTTP par fiche, mise en cache par source.
    pending = 0
    for src in SOURCES:
        if not src.get("enrich"):
            continue
        try:
            releases = src["enrich"](releases, src["affiliate_cache"], deadline)
            stats = getattr(purchase_links.enrich_releases, "last_stats", {})
            _refresh_state["steps"][src["name"]] = stats
            pending += stats.get("pending", 0)
        except Exception as exc:
            logger.exception("Échec de la récupération des liens d'achat (%s)", src["name"])
            errors.append(f"Liens affiliés {src['name']} : {exc}")

    if poster_lookup.is_configured():
        try:
            releases = poster_lookup.enrich_with_posters(
                releases, POSTER_CACHE_FILE, deadline=deadline)
            stats = getattr(poster_lookup.enrich_with_posters, "last_stats", {})
            _refresh_state["steps"]["TMDB"] = stats
            pending += stats.get("pending", 0)
        except Exception as exc:
            logger.exception("Échec de la récupération des affiches TMDB")
            errors.append(f"TMDB : {exc}")

    jellyfin_matched = None
    if jellyfin_client.is_configured():
        try:
            library_titles = jellyfin_client.fetch_library_titles()
            releases = jellyfin_client.annotate_with_library(releases, library_titles)
            jellyfin_matched = sum(1 for r in releases if r.get("in_jellyfin"))
        except Exception as exc:
            logger.exception("Échec de la connexion à Jellyfin")
            errors.append(f"Jellyfin : {exc}")
            for r in releases:
                r.setdefault("in_jellyfin", False)
    else:
        for r in releases:
            r["in_jellyfin"] = False

    save_cache(releases, errors, jellyfin_matched)
    _refresh_state.update(running=False, pending=pending,
                          finished_at=datetime.now(timezone.utc).isoformat())
    logger.info(
        "Cache mis à jour : %d sorties (%d erreurs)%s",
        len(releases), len(errors),
        f", {jellyfin_matched} déjà dans Jellyfin" if jellyfin_matched is not None else "",
    )
    if pending:
        _schedule_followup(f"{pending} éléments encore à récupérer")


def _schedule_followup(reason):
    """Programme un passage de rattrapage dans quelques minutes. Remplace le
    précédent s'il y en avait un, pour ne jamais empiler les passages."""
    if _scheduler is None:
        logger.info("Rattrapage non programmé (pas d'ordonnanceur) : %s", reason)
        return
    when = datetime.now(timezone.utc) + timedelta(minutes=FOLLOWUP_DELAY_MINUTES)
    try:
        _scheduler.add_job(refresh_data, "date", run_date=when,
                           id="refresh-followup", replace_existing=True)
        logger.info("Rattrapage programmé dans %.0f min (%s)",
                    FOLLOWUP_DELAY_MINUTES, reason)
    except Exception:
        logger.exception("Impossible de programmer le rattrapage")


def get_sorted_releases():
    cache = load_cache()
    releases = cache.get("releases", [])

    def sort_key(r):
        # Tri uniquement par date : les sorties 4K et Blu-ray/DVD d'un même
        # jour se retrouvent donc mélangées ensemble, dans l'ordre du jour,
        # peu importe le format ou la source.
        return r.get("date_iso") or "9999-12-31"

    dated = sorted([r for r in releases if r.get("date_iso")], key=sort_key)
    undated = [r for r in releases if not r.get("date_iso")]

    # Normalise le libellé de date affiché même pour les données déjà en
    # cache (scrapées avant ce correctif) : sans ça, "9 septembre 2026" et
    # "9 Septembre 2026" (une par site) créent deux sections au lieu d'une
    # seule dans la liste groupée par jour.
    for r in dated:
        label = format_date_label(r.get("date_iso"))
        if label:
            r["date_text"] = label

    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

    upcoming = [r for r in dated if r["date_iso"] >= today_str]
    past = list(reversed([r for r in dated if r["date_iso"] < today_str]))[:30]
    today_releases = [r for r in dated if r["date_iso"] == today_str]
    tomorrow_releases = [r for r in dated if r["date_iso"] == tomorrow_str]

    # La liste "Toutes les prochaines sorties" exclut les sorties déjà
    # affichées juste au-dessus, dans "Aujourd'hui" ET dans "Demain" :
    # seule la date du jour était écartée, si bien que les sorties du
    # lendemain apparaissaient deux fois.
    upcoming_rest = [r for r in upcoming
                     if r["date_iso"] not in (today_str, tomorrow_str)]

    return {
        "updated_at": cache.get("updated_at"),
        "updated_at_display": format_updated_at(cache.get("updated_at")),
        "errors": cache.get("errors", []),
        "jellyfin_enabled": jellyfin_client.is_configured(),
        "jellyfin_matched": cache.get("jellyfin_matched"),
        "tmdb_enabled": poster_lookup.is_configured(),
        # Toutes les sorties ayant une date précise, passées comprises et sans
        # troncature (contrairement à "past", limité à 30) : c'est ce que la
        # grille du calendrier consomme, pour que les jours déjà écoulés du
        # mois en cours affichent eux aussi leurs sorties.
        "dated": dated,
        "today": today_releases,
        "tomorrow": tomorrow_releases,
        "upcoming": upcoming,
        "upcoming_rest": upcoming_rest,
        "undated": undated,
        "past": past,
    }


def _is_initial_loading(data):
    """Vrai tant que le tout premier scraping n'a rien donné à afficher :
    un rafraîchissement tourne et le cache est encore vide. Sert à montrer
    un indicateur de chargement plutôt qu'une page vide, au démarrage."""
    empty = not data["dated"] and not data["undated"]
    return bool(empty and _refresh_state.get("running"))


def _filter_releases(releases, only_jellyfin=False, category=None):
    if only_jellyfin:
        releases = [r for r in releases if r.get("in_jellyfin")]
    if category == "4k":
        releases = [r for r in releases if r.get("format_category") == "4k"]
    elif category == "bluray_dvd":
        releases = [r for r in releases if r.get("format_category") != "4k"]
    return releases


@app.route("/")
def index():
    data = get_sorted_releases()
    # Étiquettes d'édition calculées une fois ici : toutes les listes de la
    # page pointent sur les mêmes dicts que `dated`, une seule passe suffit
    # donc. Elles servent aux badges et au système de tri/filtre du site.
    for r in data["dated"] + data["undated"]:
        r["edition_tags"] = sorted(_edition_tags(r))
    return render_template(
        "index.html",
        today=data["today"],
        tomorrow=data["tomorrow"],
        upcoming=data["upcoming"],
        upcoming_rest=data["upcoming_rest"],
        undated=data["undated"],
        past=data["past"],
        updated_at=data["updated_at"],
        updated_at_display=data["updated_at_display"],
        errors=data["errors"],
        jellyfin_enabled=data["jellyfin_enabled"],
        jellyfin_matched=data["jellyfin_matched"],
        tmdb_enabled=data["tmdb_enabled"],
        loading=_is_initial_loading(data),
        refreshing=bool(_refresh_state.get("running")),
        sources=[
            ("4K-Ultra-HD.fr", "https://4k-ultra-hd.fr/prochaines-sorties-blu-ray-4k-ultra-hd"),
            ("Édition-Limitée.fr", "https://edition-limitee.fr/blu-ray-dvd/sortie-blu-ray-dvd/"),
        ],
        app_build=APP_BUILD,
    )


@app.route("/api/releases")
def api_releases():
    data = get_sorted_releases()
    only_jellyfin = request.args.get("jellyfin") == "only"
    category = request.args.get("category")  # '4k' ou 'bluray'
    if category == "bluray":
        category = "bluray_dvd"

    for key in ("today", "tomorrow", "upcoming", "undated", "past"):
        data[key] = _filter_releases(data[key], only_jellyfin, category)

    return jsonify(data)


def _calendar_response(scope, only_jellyfin, category):
    data = get_sorted_releases()
    releases = list(data["upcoming"])
    if scope == "all":
        releases += data["past"]
    releases = _filter_releases(releases, only_jellyfin, category)
    # déjà trié par date via get_sorted_releases() -> 4K et Blu-ray/DVD
    # apparaissent mélangés, dans l'ordre chronologique, le même jour.

    ics_bytes = calendar_feed.build_ics(releases)
    return Response(
        ics_bytes,
        mimetype="text/calendar",
        headers={
            "Content-Disposition": 'inline; filename="sorties-films.ics"',
            "Cache-Control": "public, max-age=1800",
        },
    )


@app.route("/api/calendar.ics")
@app.route("/calendar.ics")  # alias court, pratique à coller dans Homarr/Homepage
def calendar_ics():
    """Flux iCalendar (.ics) UNIQUE et fusionné (4K + Blu-ray/DVD, trié par
    date) à donner à un widget calendrier (Homarr, Homepage, Google/Apple/
    Outlook Calendar...). C'est le flux à utiliser par défaut : les sorties
    apparaissent groupées par jour quel que soit leur format (chaque titre
    est préfixé d'un pictogramme de couleur — 🟣 4K, 🔵 Blu-ray, 🔴 DVD —
    pour les distinguer visuellement dans une même liste/vue).

    Paramètres optionnels :
      ?scope=upcoming     -> uniquement les sorties à venir (défaut)
      ?scope=all          -> sorties à venir + historique récent (30 dernières)
      ?jellyfin=only      -> uniquement les films déjà présents dans ta bibliothèque Jellyfin
      ?category=4k        -> uniquement les sorties 4K Ultra HD
      ?category=bluray    -> uniquement les sorties Blu-ray / DVD (non-4K)
    """
    scope = request.args.get("scope", "upcoming")
    only_jellyfin = request.args.get("jellyfin") == "only"
    category = request.args.get("category")
    if category == "bluray":
        category = "bluray_dvd"
    return _calendar_response(scope, only_jellyfin, category)


@app.route("/calendar-4k.ics")
def calendar_ics_4k():
    """Flux iCalendar limité aux sorties 4K Ultra HD. À réserver aux cas où
    tu veux vraiment deux panneaux séparés dans ton dashboard (au prix de
    ne plus voir 4K et Blu-ray mélangés au même jour) — sinon utilise
    /calendar.ics qui contient tout, trié par date."""
    scope = request.args.get("scope", "upcoming")
    only_jellyfin = request.args.get("jellyfin") == "only"
    return _calendar_response(scope, only_jellyfin, "4k")


@app.route("/calendar-bluray.ics")
def calendar_ics_bluray():
    """Pendant du flux ci-dessus, pour les sorties Blu-ray / DVD (non-4K)."""
    scope = request.args.get("scope", "upcoming")
    only_jellyfin = request.args.get("jellyfin") == "only"
    return _calendar_response(scope, only_jellyfin, "bluray_dvd")


@app.route("/widget/upcoming")
def widget_upcoming():
    """Mini page compacte, pensée pour être embarquée via un widget iFrame
    (Homarr, Homepage, ou tout autre dashboard qui supporte l'embarquement
    d'une URL) — présentée comme une vraie grille de calendrier mensuel
    (jours de la semaine en colonnes, points colorés par format sur les
    jours où il y a une sortie), plutôt qu'une simple liste. Contrairement
    au flux .ics, cette page peut afficher les affiches au survol/clic,
    puisque ce n'est pas un format calendrier mais une vraie page HTML.

    Paramètres optionnels :
      ?scope=all           -> all (défaut : toutes les sorties datées, passées comprises)
                              / upcoming (à partir d'aujourd'hui) / today / tomorrow
      ?jellyfin=only       -> uniquement les films déjà présents dans Jellyfin
      ?category=4k|bluray  -> filtrer par format
      ?theme=dark|light    -> thème visuel (défaut : dark)

    `?limit=` est accepté mais IGNORÉ, et c'est volontaire : les sorties
    étant triées par date, toute troncature supprimait les derniers mois du
    calendrier. Un `?limit=8` hérité d'une ancienne configuration de
    dashboard ne laissait ainsi qu'un seul jour affiché et désactivait les
    flèches de navigation. Une grille de calendrier n'a de toute façon pas
    besoin d'être plafonnée : son poids dépend du nombre de mois, pas du
    nombre de sorties, puisque le détail de chaque jour est chargé à la
    demande par /widget/day/<date>.
    """
    data = get_sorted_releases()
    scope = request.args.get("scope", "all")
    only_jellyfin = request.args.get("jellyfin") == "only"
    category = request.args.get("category")
    if category == "bluray":
        category = "bluray_dvd"
    theme = "light" if request.args.get("theme") == "light" else "dark"

    if request.args.get("limit"):
        logger.info("Paramètre ?limit ignoré sur /widget/upcoming (voir docstring)")

    releases = _releases_for_scope(data, scope)
    today_iso = date.today().isoformat()
    releases = _filter_releases(releases, only_jellyfin, category)

    months, active_index = _build_calendar_months(releases, today_iso)

    return render_template(
        "widget.html",
        months=months,
        active_index=active_index,
        today_iso=today_iso,
        theme=theme,
        loading=_is_initial_loading(data),
        # Rejoués tels quels par le fetch du détail d'un jour, pour que la
        # fenêtre applique les mêmes filtres que la grille.
        day_query=urlencode({k: v for k, v in (
            ("category", request.args.get("category")),
            ("jellyfin", request.args.get("jellyfin")),
        ) if v}),
    )


@app.route("/widget/day/<day_iso>")
def widget_day(day_iso):
    """Fragment HTML des sorties d'une journée, chargé par le widget au clic
    sur une case du calendrier (voir templates/widget_day.html)."""
    try:
        datetime.strptime(day_iso, "%Y-%m-%d")
    except ValueError:
        return Response("Date invalide", status=400, mimetype="text/plain")

    only_jellyfin = request.args.get("jellyfin") == "only"
    category = request.args.get("category")
    if category == "bluray":
        category = "bluray_dvd"

    data = get_sorted_releases()
    releases = [r for r in data["dated"] if r.get("date_iso") == day_iso]
    releases = _filter_releases(releases, only_jellyfin, category)
    releases.sort(key=lambda r: (
        _CATEGORY_ORDER.get(r.get("format_category", "autre"), 9),
        (r.get("title") or "").lower(),
    ))
    # Signalé aussi dans la fenêtre, pour qu'on sache à quelle sortie
    # correspond la pastille steelbook de la case du calendrier.
    for r in releases:
        tags = _edition_tags(r)
        r["is_boxset"] = "coffret" in tags
        r["is_steelbook"] = "steelbook" in tags
        r["is_fnac_exclusive"] = "fnac" in tags

    return render_template("widget_day.html", releases=releases)


def _releases_for_scope(data, scope):
    if scope == "today":
        return data["today"]
    if scope == "tomorrow":
        return data["tomorrow"]
    if scope == "upcoming":
        return data["upcoming"]
    # Défaut : toute la période couverte par les données, y compris les
    # jours déjà passés du mois en cours (un calendrier qui n'affiche rien
    # avant aujourd'hui donne l'impression d'être vide).
    return data["dated"]


# Ordre d'affichage des sorties à l'intérieur d'une même journée
_CATEGORY_ORDER = {"4k": 0, "bluray": 1, "dvd": 2, "autre": 3}

# Caractéristiques d'édition, lues dans le titre par title_parser : ni les
# coffrets, ni les steelbooks, ni les exclusivités Fnac n'ont de champ dédié
# sur les sites source. Chacune a sa pastille, ce sont souvent les critères
# d'achat.
_MARKER_LABELS = {
    "4k": "4K Ultra HD",
    "bluray": "Blu-ray",
    "dvd": "DVD",
    "autre": "Autre format",
    "coffret": "Coffret",
    "steelbook": "Steelbook",
    "fnac": "Exclusivité Fnac",
}

# Étiquettes d'édition, dans leur ordre d'affichage après les formats
_EDITION_MARKERS = ("coffret", "steelbook", "fnac")


def _edition_tags(r):
    """Étiquettes d'édition d'une sortie : coffret / steelbook / fnac."""
    return set(title_parser.analyze_title(
        r.get("title", ""), r.get("details", ""))["tags"])


def _day_markers(releases):
    """Pastilles à afficher sur une case du calendrier : un point par format
    présent ce jour-là, plus un point « steelbook » si au moins une des
    sorties en est une."""
    markers = sorted(
        {r.get("format_category", "autre") for r in releases},
        key=lambda c: _CATEGORY_ORDER.get(c, 9),
    )
    # Une seule analyse de titre par sortie, les trois étiquettes en sortant
    # ensemble (la grille appelle cette fonction pour chaque case du mois).
    tags = set()
    for r in releases:
        tags |= _edition_tags(r)
    markers.extend(m for m in _EDITION_MARKERS if m in tags)
    return markers


def _day_hint(releases, markers):
    if not releases:
        return ""
    count = len(releases)
    label = f"{count} sortie{'s' if count > 1 else ''}"
    formats = ", ".join(_MARKER_LABELS.get(m, m) for m in markers)
    hint = f"{label} — {formats}" if formats else label
    owned = sum(1 for r in releases if r.get("in_jellyfin"))
    if owned:
        hint += f" — {owned} déjà dans Jellyfin"
    return hint


def _build_calendar_months(releases, today_iso=None):
    """Construit une grille de calendrier mensuel et l'index du mois à
    afficher par défaut (celui d'aujourd'hui si possible).

    - Les mois sont générés en série continue du premier au dernier mois
      couvert par les données (mois d'aujourd'hui inclus même s'il est
      vide), pour que les flèches de navigation ne sautent jamais un mois.
    - Chaque semaine compte toujours 7 cases : les jours qui débordent sur
      le mois précédent/suivant (ex. 31 août devant le 1er septembre, ou
      1er au 4 octobre après le 30 septembre) sont affichés en grisé,
      avec leurs éventuelles sorties, via le drapeau `in_month`.
    """
    import calendar as calendar_module

    by_day = {}
    for r in releases:
        date_iso = r.get("date_iso")
        if not date_iso:
            continue
        by_day.setdefault(date_iso, []).append(r)

    for items in by_day.values():
        items.sort(key=lambda r: (
            _CATEGORY_ORDER.get(r.get("format_category", "autre"), 9),
            (r.get("title") or "").lower(),
        ))

    def _year_month(date_iso):
        y, m, _d = (int(part) for part in date_iso.split("-"))
        return y, m

    bounds = [_year_month(d) for d in (min(by_day), max(by_day))] if by_day else []
    if today_iso:
        bounds.append(_year_month(today_iso))
    if not bounds:
        return [], 0

    first, last = min(bounds), max(bounds)

    # Garde-fou : une seule date mal parsée (année aberrante) suffirait sinon
    # à générer des centaines de mois vides et à rendre la navigation
    # inutilisable. On borne la plage autour du mois en cours.
    if today_iso:
        ty, tm = _year_month(today_iso)
        floor = (ty - 1, tm)
        ceiling = (ty + 3, tm)
        first = max(first, floor)
        last = min(last, ceiling)
        if first > last:
            first = last = (ty, tm)

    # Garde-fou : une date aberrante dans les données scrapées (2099...)
    # ne doit pas faire générer des centaines de mois.
    MAX_MONTHS = 36
    ordered_months = []
    y, m = first
    while (y, m) <= last and len(ordered_months) < MAX_MONTHS:
        ordered_months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    cal = calendar_module.Calendar(firstweekday=0)  # semaine commence le lundi
    months = []
    active_index = 0

    for index, (y, m) in enumerate(ordered_months):
        if today_iso and (y, m) == _year_month(today_iso):
            active_index = index

        weeks = []
        for week in cal.monthdatescalendar(y, m):
            week_cells = []
            for day in week:
                day_iso = day.isoformat()
                items = by_day.get(day_iso, [])
                markers = _day_markers(items)
                week_cells.append({
                    # Au moins une sortie du jour est déjà dans Jellyfin
                    "in_jellyfin": any(r.get("in_jellyfin") for r in items),
                    "day_num": day.day,
                    "date_iso": day_iso,
                    "in_month": day.month == m,
                    "label": format_date_label(day_iso),
                    "releases": items,
                    "markers": markers,
                    # Infobulle de la case : nombre de sorties + formats
                    "hint": _day_hint(items, markers),
                })
            weeks.append(week_cells)

        months.append({
            "year": y,
            "month": m,
            "label": f"{MOIS_FR_NAMES[m - 1].capitalize()} {y}",
            "weeks": weeks,
        })

    return months, active_index


@app.route("/api/jellyfin/status")
def jellyfin_status():
    if not jellyfin_client.is_configured():
        return jsonify({"configured": False, "message": "JELLYFIN_URL / JELLYFIN_API_KEY non définis"})
    try:
        info = jellyfin_client.get_server_info()
        titles = jellyfin_client.fetch_library_titles()
        counts = getattr(jellyfin_client.fetch_library_titles, "last_counts", {})
        return jsonify({
            "configured": True,
            "reachable": True,
            "server_name": info.get("server_name"),
            "server_version": info.get("version"),
            "movies": counts.get("movies"),
            "series": counts.get("series"),
            # Totaux annoncés par le serveur : un écart avec les nombres
            # ci-dessus signale une pagination incomplète.
            "movies_announced": counts.get("movies_announced"),
            "series_announced": counts.get("series_announced"),
            "by_type": counts.get("by_type"),
            "libraries": jellyfin_client.get_libraries(),
            # Nombre de titres normalisés servant à la comparaison : supérieur
            # au nombre d'éléments, chaque titre original comptant en plus.
            "titles_indexed": len(titles),
        })
    except Exception as exc:
        return jsonify({"configured": True, "reachable": False, "error": str(exc)}), 502


@app.route("/api/affiliate-links/status")
def affiliate_links_status():
    """Diagnostic pour vérifier si les liens Amazon/Fnac sont bien
    détectés, sans avoir à inspecter les logs du conteneur. Répartit les
    sorties par source et indique combien ont au moins un lien affilié."""
    cache = load_cache()
    releases = cache.get("releases", [])

    by_source = {}
    for r in releases:
        source = r.get("source", "inconnu")
        stats = by_source.setdefault(source, {"total": 0, "with_amazon": 0, "with_fnac": 0, "examples_without": []})
        stats["total"] += 1
        if r.get("amazon_url"):
            stats["with_amazon"] += 1
        if r.get("fnac_url"):
            stats["with_fnac"] += 1
        if not r.get("amazon_url") and not r.get("fnac_url") and len(stats["examples_without"]) < 3:
            stats["examples_without"].append({"title": r.get("title"), "url": r.get("url")})

    return jsonify({
        "build": APP_BUILD,
        "total_releases": len(releases),
        "by_source": by_source,
        "caches": {
            src["name"]: len(load_json_cache(src["affiliate_cache"]))
            for src in SOURCES if src.get("affiliate_cache")
        },
    })


@app.route("/api/calendar/debug")
def calendar_debug():
    """Diagnostic du calendrier : ce que contient réellement le cache, mois
    par mois. À appeler quand la grille du widget paraît vide ou s'arrête
    trop tôt, pour distinguer un problème d'affichage d'un problème de
    scraping (mois réellement absent des données).

        curl http://<ton-serveur>:8080/api/calendar/debug
    """
    data = get_sorted_releases()
    dated = data["dated"]
    undated = data["undated"]

    by_month = {}
    for r in dated:
        key = r["date_iso"][:7]
        stats = by_month.setdefault(key, {"total": 0, "by_source": {}, "by_format": {}})
        stats["total"] += 1
        source = r.get("source", "inconnu")
        stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        cat = r.get("format_category", "autre")
        stats["by_format"][cat] = stats["by_format"].get(cat, 0) + 1

    return jsonify({
        "today": date.today().isoformat(),
        "updated_at": data["updated_at"],
        "errors": data["errors"],
        "dated_releases": len(dated),
        "undated_releases": len(undated),
        "first_date": dated[0]["date_iso"] if dated else None,
        "last_date": dated[-1]["date_iso"] if dated else None,
        "months": {k: by_month[k] for k in sorted(by_month)},
        "undated_examples": [r.get("title") for r in undated[:10]],
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if _refresh_state.get("running"):
        return jsonify({
            "status": "busy",
            "message": "Un rafraîchissement est déjà en cours",
            "started_at": _refresh_state.get("started_at"),
        }), 409
    refresh_data()
    cache = load_cache()
    return jsonify({
        "status": "ok",
        "updated_at": cache.get("updated_at"),
        "errors": cache.get("errors", []),
        "jellyfin_matched": cache.get("jellyfin_matched"),
        "pending": _refresh_state.get("pending"),
    })


@app.route("/api/debug/calendar")
def debug_calendar():
    """Diagnostic du calendrier : dit exactement ce que contient le cache,
    mois par mois. Sert à distinguer « le scraping n'a rien trouvé pour ce
    mois-là » de « les données sont là mais la grille ne les affiche pas ».

        curl http://<ton-serveur>:8080/api/debug/calendar
    """
    cache = load_cache()
    releases = cache.get("releases", [])
    today_iso = date.today().isoformat()

    by_month = {}
    for r in releases:
        date_iso = r.get("date_iso")
        key = date_iso[:7] if date_iso else "sans-date"
        stats = by_month.setdefault(key, {"total": 0, "par_source": {}, "jours": {}})
        stats["total"] += 1
        source = r.get("source", "inconnu")
        stats["par_source"][source] = stats["par_source"].get(source, 0) + 1
        if date_iso:
            stats["jours"][date_iso[8:]] = stats["jours"].get(date_iso[8:], 0) + 1

    for stats in by_month.values():
        stats["jours"] = dict(sorted(stats["jours"].items()))

    dated = [r for r in releases if r.get("date_iso")]
    return jsonify({
        "build": APP_BUILD,
        "updated_at": cache.get("updated_at"),
        "errors": cache.get("errors", []),
        # Où en est la collecte : utile juste après un démarrage, le temps
        # que toutes les fiches et toutes les affiches soient récupérées.
        "refresh": _refresh_state,
        # Date vue par le CONTENEUR : si elle ne correspond pas à ta date
        # locale, le découpage aujourd'hui/demain et le mois ouvert par
        # défaut seront décalés (voir la variable TZ du conteneur).
        "today_in_container": today_iso,
        "total": len(releases),
        "avec_date": len(dated),
        "sans_date": len(releases) - len(dated),
        "premiere_date": min((r["date_iso"] for r in dated), default=None),
        "derniere_date": max((r["date_iso"] for r in dated), default=None),
        "par_mois": dict(sorted(by_month.items())),
    })


@app.route("/health")
def health():
    # `build` permet de vérifier d'un coup d'œil quelle version du code
    # tourne réellement dans le conteneur (utile car le docker-compose
    # d'exemple utilise une image publiée : sans rebuild, modifier les
    # fichiers en local ne change rien).
    return jsonify({"status": "ok", "build": APP_BUILD})


def start_scheduler():
    global _scheduler
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(refresh_data, "interval", hours=REFRESH_HOURS, next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    _scheduler = scheduler
    return scheduler


if __name__ == "__main__":
    start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
else:
    start_scheduler()
