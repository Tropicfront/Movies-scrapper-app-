import json
import logging
import os
from datetime import date, datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, jsonify, render_template, request

import calendar_feed
import jellyfin_client
import poster_lookup
import scraper_4k
import scraper_editionlimitee
from date_utils import normalize_title

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
CACHE_FILE = os.path.join(DATA_DIR, "releases.json")
POSTER_CACHE_FILE = os.path.join(DATA_DIR, "posters.json")
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "6"))
EL_MONTH_ARTICLES = int(os.environ.get("EDITION_LIMITEE_MONTH_ARTICLES", "3"))

# Quand une même sortie (titre normalisé + date) apparaît sur plusieurs
# sources, on ne garde que celle de la source la mieux classée ici.
SOURCE_PRIORITY = {
    scraper_4k.SOURCE_NAME: 0,          # 4K-Ultra-HD.fr : gardé en priorité
    scraper_editionlimitee.SOURCE_NAME: 1,
}

app = Flask(__name__)
os.makedirs(DATA_DIR, exist_ok=True)


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
        return r.get("date_iso") or "9999-12-31"

    dated = sorted([r for r in releases if r.get("date_iso")], key=sort_key)
    undated = [r for r in releases if not r.get("date_iso")]

    today = date.today().isoformat()
    upcoming = [r for r in dated if r["date_iso"] >= today]
    past = list(reversed([r for r in dated if r["date_iso"] < today]))[:30]

    return {
        "updated_at": cache.get("updated_at"),
        "errors": cache.get("errors", []),
        "jellyfin_enabled": jellyfin_client.is_configured(),
        "jellyfin_matched": cache.get("jellyfin_matched"),
        "tmdb_enabled": poster_lookup.is_configured(),
        "upcoming": upcoming,
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
        upcoming=data["upcoming"],
        undated=data["undated"],
        past=data["past"],
        updated_at=data["updated_at"],
        errors=data["errors"],
        jellyfin_enabled=data["jellyfin_enabled"],
        jellyfin_matched=data["jellyfin_matched"],
        tmdb_enabled=data["tmdb_enabled"],
        sources=[
            ("4K-Ultra-HD.fr", "https://4k-ultra-hd.fr/prochaines-sorties-blu-ray-4k-ultra-hd"),
            ("Édition-Limitée.fr", "https://edition-limitee.fr/blu-ray-dvd/sortie-blu-ray-dvd/"),
        ],
    )


@app.route("/api/releases")
def api_releases():
    data = get_sorted_releases()
    only_jellyfin = request.args.get("jellyfin") == "only"
    category = request.args.get("category")  # '4k' ou 'bluray'
    if category == "bluray":
        category = "bluray_dvd"

    for key in ("upcoming", "undated", "past"):
        data[key] = _filter_releases(data[key], only_jellyfin, category)

    return jsonify(data)


def _calendar_response(scope, only_jellyfin, category):
    data = get_sorted_releases()
    releases = list(data["upcoming"])
    if scope == "all":
        releases += data["past"]
    releases = _filter_releases(releases, only_jellyfin, category)

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
    """Flux iCalendar (.ics) à donner à un widget calendrier (Homarr, Homepage,
    Google/Apple/Outlook Calendar...).

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
    """Flux iCalendar limité aux sorties 4K Ultra HD — à ajouter comme
    intégration séparée dans Homarr/Homepage pour lui donner sa propre
    couleur (ces widgets colorent par flux, pas par événement)."""
    scope = request.args.get("scope", "upcoming")
    only_jellyfin = request.args.get("jellyfin") == "only"
    return _calendar_response(scope, only_jellyfin, "4k")


@app.route("/calendar-bluray.ics")
def calendar_ics_bluray():
    """Flux iCalendar limité aux sorties Blu-ray / DVD (tout ce qui n'est
    pas 4K) — pendant du flux ci-dessus, à ajouter avec une autre couleur."""
    scope = request.args.get("scope", "upcoming")
    only_jellyfin = request.args.get("jellyfin") == "only"
    return _calendar_response(scope, only_jellyfin, "bluray_dvd")


@app.route("/api/jellyfin/status")
def jellyfin_status():
    if not jellyfin_client.is_configured():
        return jsonify({"configured": False, "message": "JELLYFIN_URL / JELLYFIN_API_KEY non définis"})
    try:
        titles = jellyfin_client.fetch_library_titles()
        return jsonify({"configured": True, "reachable": True, "movies_in_library": len(titles)})
    except Exception as exc:
        return jsonify({"configured": True, "reachable": False, "error": str(exc)}), 502


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


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


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
