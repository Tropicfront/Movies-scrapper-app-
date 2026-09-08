"""
Récupère les affiches (posters) des films/séries via l'API TMDB
(The Movie Database) et fournit un lien vers la fiche TMDB correspondante.

Pourquoi TMDB plutôt que TVDB ou IMDb :
- API gratuite avec simple inscription (contrairement à IMDb, qui n'a pas
  d'API publique officielle — le scraping de leurs pages violerait leurs CGU)
- Couvre à la fois films ET séries en une seule recherche ("multi search"),
  utile car le planning contient aussi bien des films que des coffrets de
  séries/animes, contrairement à TVDB plutôt orienté séries et dont l'accès
  à l'API est limité sans compte payant
- Fiches disponibles en français, avec des images optimisées et servies
  via CDN (image.tmdb.org)

Configuration (variables d'environnement) :
- TMDB_API_KEY   : clé API TMDB v3, gratuite -> https://www.themoviedb.org/settings/api
- TMDB_LANGUAGE  : langue des résultats (défaut : fr-FR)

Un cache persistant (posters.json, dans le volume de données) évite de
refaire une recherche à chaque rafraîchissement pour un titre déjà résolu.
Les titres non trouvés sont retentés périodiquement (au cas où TMDB
référencerait le titre plus tard) plutôt qu'indéfiniment ignorés.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_LANGUAGE = os.environ.get("TMDB_LANGUAGE", "fr-FR")
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/multi"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
TMDB_SITE_BASE = "https://www.themoviedb.org"

RETRY_NOT_FOUND_AFTER_DAYS = 7

# Nettoyage du titre avant recherche : on retire tout ce qui parle de
# l'édition/du format (qui ne fait pas partie du titre du film/de la série)
_FORMAT_JUNK_RE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|"
    r"\b(4k ultra hd|ultra hd|4k uhd|4k|blu-?ray|dvd|steelbook|boitier|boîtier|"
    r"edition collector|édition collector|collector|combo|coffret|"
    r"limit[ée]e?|version longue)\b",
    re.IGNORECASE,
)
_SEASON_SUFFIX_RE = re.compile(r"\s*[-:]\s*saison\s*\d+.*$", re.IGNORECASE)


def is_configured():
    return bool(TMDB_API_KEY)


def _clean_query(title):
    t = _FORMAT_JUNK_RE.sub(" ", title)
    t = _SEASON_SUFFIX_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -:")
    return t or title


def _search_tmdb(title, timeout=10):
    query = _clean_query(title)
    if not query:
        return None
    try:
        resp = requests.get(
            TMDB_SEARCH_URL,
            params={
                "api_key": TMDB_API_KEY,
                "query": query,
                "language": TMDB_LANGUAGE,
                "include_adult": "false",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        logger.exception("Échec recherche TMDB pour %r (requête : %r)", title, query)
        return None

    candidates = [
        r for r in results
        if r.get("media_type") in ("movie", "tv") and r.get("poster_path")
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda r: r.get("popularity", 0), reverse=True)
    best = candidates[0]
    return {
        "poster_url": TMDB_IMAGE_BASE + best["poster_path"],
        "page_url": f"{TMDB_SITE_BASE}/{best['media_type']}/{best['id']}",
        "tmdb_id": best["id"],
        "media_type": best["media_type"],
    }


def _load_cache(cache_file):
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Cache posters illisible, il sera recréé")
    return {}


def _save_cache(cache_file, cache):
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def enrich_with_posters(releases, cache_file, request_delay=0.25, search_fn=_search_tmdb):
    """Ajoute poster_url / poster_page_url à chaque release (dict), avec
    cache persistant sur disque. `search_fn` est injectable pour les tests."""
    if not is_configured():
        return releases

    cache = _load_cache(cache_file)
    now = datetime.now(timezone.utc)
    changed = False

    for r in releases:
        title = r.get("title", "")
        if not title:
            continue
        key = title.strip().lower()
        entry = cache.get(key)

        needs_lookup = entry is None
        if entry and not entry.get("found") and entry.get("checked_at"):
            try:
                checked_at = datetime.fromisoformat(entry["checked_at"])
                if now - checked_at > timedelta(days=RETRY_NOT_FOUND_AFTER_DAYS):
                    needs_lookup = True
            except ValueError:
                needs_lookup = True

        if needs_lookup:
            result = search_fn(title)
            changed = True
            if result:
                cache[key] = {**result, "found": True, "checked_at": now.isoformat()}
            else:
                cache[key] = {"found": False, "checked_at": now.isoformat()}
            time.sleep(request_delay)
            entry = cache[key]

        if entry and entry.get("found"):
            r["poster_url"] = entry["poster_url"]
            r["poster_page_url"] = entry["page_url"]

    if changed:
        _save_cache(cache_file, cache)

    matched = sum(1 for r in releases if r.get("poster_url"))
    logger.info("TMDB : %d/%d sorties avec affiche trouvée", matched, len(releases))
    return releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not is_configured():
        print("TMDB_API_KEY non défini.")
    else:
        sample = [
            {"title": "Ghost in the Shell 4K Steelbook"},
            {"title": "DOLLY"},
            {"title": "Blue Exorcist - Saison 4"},
        ]
        enrich_with_posters(sample, "/tmp/posters_test.json")
        for s in sample:
            print(s)
