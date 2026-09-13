import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, jsonify, render_template, request

import calendar_feed
import jellyfin_client
import poster_lookup
import scraper_4k
import scraper_editionlimitee
from date_utils import MOIS_FR_NAMES, format_date_label, normalize_title
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
APP_BUILD = "2026-09-12.1"

# Quand une même sortie (titre normalisé + date) apparaît sur plusieurs
# sources, on ne garde que celle de la source la mieux classée ici.
SOURCE_PRIORITY = {
    scraper_4k.SOURCE_NAME: 0,          # 4K-Ultra-HD.fr : gardé en priorité
    scraper_editionlimitee.SOURCE_NAME: 1,
}

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
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
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


def refresh_data():
    logger.info("Rafraîchissement des données (4K-Ultra-HD.fr + Édition-Limitée.fr)")
    errors = []
    releases = []

    try:
        releases.extend(scraper_4k.get_releases())
    except Exception as exc:
        logger.exception("Échec du scraping 4k-ultra-hd.fr")
        errors.append(f"4K-Ultra-HD.fr : {exc}")

    try:
        releases.extend(scraper_editionlimitee.get_releases(month_articles_limit=EL_MONTH_ARTICLES))
    except Exception as exc:
        logger.exception("Échec du scraping edition-limitee.fr")
        errors.append(f"Édition-Limitée.fr : {exc}")

    if not releases:
        logger.warning("Aucune sortie récupérée sur aucune source, le cache n'est pas modifié")
        return

    before = len(releases)
    releases = _dedupe(releases)
    logger.info("Dédoublonnage : %d -> %d sorties", before, len(releases))

    try:
        releases = scraper_4k.enrich_with_affiliate_links(releases, AFFILIATE_CACHE_FILE)
    except Exception as exc:
        logger.exception("Échec de la récupération des liens Amazon/Fnac (4K-Ultra-HD.fr)")
        errors.append(f"Liens affiliés 4K-Ultra-HD.fr : {exc}")

    # Sur edition-limitee.fr aussi, les boutons d'achat ne sont que sur la
    # fiche de chaque film : une requête par fiche, mise en cache.
    try:
        releases = scraper_editionlimitee.enrich_with_affiliate_links(
            releases, EL_AFFILIATE_CACHE_FILE)
    except Exception as exc:
        logger.exception("Échec de la récupération des liens Amazon/Fnac (Édition-Limitée.fr)")
        errors.append(f"Liens affiliés Édition-Limitée.fr : {exc}")

    if poster_lookup.is_configured():
        try:
            releases = poster_lookup.enrich_with_posters(releases, POSTER_CACHE_FILE)
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
    logger.info(
        "Cache mis à jour : %d sorties (%d erreurs)%s",
        len(releases), len(errors),
        f", {jellyfin_matched} déjà dans Jellyfin" if jellyfin_matched is not None else "",
    )


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

    # La liste "Toutes les prochaines sorties" exclut les sorties du jour
    # même (déjà affichées dans la section "Aujourd'hui" juste au-dessus,
    # pas besoin de les montrer deux fois).
    upcoming_rest = [r for r in upcoming if r["date_iso"] != today_str]

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
      ?limit=800           -> nombre de sorties incluses dans la grille (défaut : 800, max : 2000)
      ?scope=all           -> all (défaut : toutes les sorties datées, passées comprises)
                              / upcoming (à partir d'aujourd'hui) / today / tomorrow
      ?jellyfin=only       -> uniquement les films déjà présents dans Jellyfin
      ?category=4k|bluray  -> filtrer par format
      ?theme=dark|light    -> thème visuel (défaut : dark)
    """
    data = get_sorted_releases()
    scope = request.args.get("scope", "all")
    only_jellyfin = request.args.get("jellyfin") == "only"
    category = request.args.get("category")
    if category == "bluray":
        category = "bluray_dvd"
    theme = "light" if request.args.get("theme") == "light" else "dark"
    try:
        limit = max(1, min(2000, int(request.args.get("limit", 800))))
    except ValueError:
        limit = 800

    if scope == "today":
        releases = data["today"]
    elif scope == "tomorrow":
        releases = data["tomorrow"]
    elif scope == "upcoming":
        releases = data["upcoming"]
    else:
        # Défaut : toute la période couverte par les données, y compris les
        # jours déjà passés du mois en cours (un calendrier qui n'affiche
        # rien avant aujourd'hui donne l'impression d'être vide).
        releases = data["dated"]

    today_iso = date.today().isoformat()
    releases = _filter_releases(releases, only_jellyfin, category)
    releases = _cap_releases(releases, limit, today_iso)

    months, active_index = _build_calendar_months(releases, today_iso)

    return render_template(
        "widget.html",
        months=months,
        active_index=active_index,
        today_iso=today_iso,
        theme=theme,
    )


# Ordre d'affichage des sorties à l'intérieur d'une même journée
_CATEGORY_ORDER = {"4k": 0, "bluray": 1, "dvd": 2, "autre": 3}


def _cap_releases(releases, limit, today_iso):
    """Ramène la liste à `limit` entrées SANS sacrifier les sorties à venir.

    Les sorties arrivent triées du plus ancien au plus récent : une simple
    troncature `[:limit]` couperait donc tous les mois futurs (symptôme :
    un calendrier qui s'arrête au mois en cours, avec la flèche « mois
    suivant » grisée alors qu'il reste des sorties après). On garde d'abord
    tout ce qui est à venir, puis on complète avec le passé le plus récent.
    """
    if len(releases) <= limit:
        return releases

    upcoming = [r for r in releases if (r.get("date_iso") or "") >= today_iso]
    past = [r for r in releases if (r.get("date_iso") or "") < today_iso]

    kept = upcoming[:limit]
    room_left = limit - len(kept)
    if room_left > 0:
        kept = past[-room_left:] + kept
    return kept


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

    ordered_months = []
    y, m = first
    while (y, m) <= last:
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
                week_cells.append({
                    "day_num": day.day,
                    "date_iso": day_iso,
                    "in_month": day.month == m,
                    "label": format_date_label(day_iso),
                    "releases": items,
                    "categories": sorted(
                        {r.get("format_category", "autre") for r in items},
                        key=lambda c: _CATEGORY_ORDER.get(c, 9),
                    ),
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
        titles = jellyfin_client.fetch_library_titles()
        return jsonify({"configured": True, "reachable": True, "movies_in_library": len(titles)})
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
            "4K-Ultra-HD.fr": len(load_json_cache(AFFILIATE_CACHE_FILE)),
            "Édition-Limitée.fr": len(load_json_cache(EL_AFFILIATE_CACHE_FILE)),
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
    refresh_data()
    cache = load_cache()
    return jsonify({
        "status": "ok",
        "updated_at": cache.get("updated_at"),
        "errors": cache.get("errors", []),
        "jellyfin_matched": cache.get("jellyfin_matched"),
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
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(refresh_data, "interval", hours=REFRESH_HOURS, next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    return scheduler


if __name__ == "__main__":
    start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
else:
    start_scheduler()
