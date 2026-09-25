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

Les titres de coffrets ("Coffret The Eye 1 et 2 4K") n'existent pas dans
TMDB : title_parser.analyze_title en tire une liste de titres candidats
("The Eye", "The Eye 2"...), essayés dans l'ordre jusqu'à trouver une
affiche. C'est ce qui permet d'illustrer les coffrets, qui restaient sans
image tant qu'on cherchait le titre brut.

Un cache persistant (posters.json, dans le volume de données) évite de
refaire une recherche à chaque rafraîchissement pour un titre déjà résolu.
Les titres non trouvés sont retentés périodiquement (au cas où TMDB
référencerait le titre plus tard) plutôt qu'indéfiniment ignorés.
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from json_cache import load_json_cache, save_json_cache
from title_parser import analyze_title

logger = logging.getLogger(__name__)

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_LANGUAGE = os.environ.get("TMDB_LANGUAGE", "fr-FR")
TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_SEARCH_URL = f"{TMDB_API_BASE}/search/multi"
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


def _query_tmdb(query, language, timeout, endpoint="multi"):
    """Un appel à /search/<endpoint>. Volontairement sans try/except : un
    échec réseau doit remonter, pour ne pas être confondu avec « TMDB ne
    connaît pas ce titre » et gravé comme tel dans le cache.

    /search/multi est le point d'entrée par défaut, mais il est plus
    restrictif que /search/movie et /search/tv, qui trouvent des titres
    que multi ignore : d'où l'essai des trois."""
    resp = requests.get(
        f"{TMDB_API_BASE}/search/{endpoint}",
        params={
            "api_key": TMDB_API_KEY,
            "query": query,
            "language": language,
            "include_adult": "false",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if endpoint in ("movie", "tv"):
        # Ces deux points d'entrée ne renvoient pas media_type, contrairement
        # à multi : on le rétablit pour que la suite traite tout pareil.
        for item in results:
            item.setdefault("media_type", endpoint)
    return results


def _result_year(result):
    date = result.get("release_date") or result.get("first_air_date") or ""
    try:
        return int(date[:4])
    except (ValueError, TypeError):
        return None


def _rank(result, year):
    """Plus la valeur est basse, meilleur est le résultat. L'année, quand le
    titre en portait une, primait sur la popularité : c'est ce qui permet de
    distinguer un remake de l'original, ou un film très courant parmi les
    homonymes. Une année qui ne correspond à rien ne fait jamais rejeter le
    résultat, elle le fait seulement passer derrière."""
    found = _result_year(result)
    if year and found:
        if found == year:
            return 0
        if abs(found - year) <= 1:   # décalage sortie salle / fiche TMDB
            return 1
        return 3
    return 2


def _pick_best(results, year):
    candidates = [
        r for r in results
        if r.get("media_type") in ("movie", "tv") and r.get("poster_path")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (_rank(r, year), -(r.get("popularity") or 0)))
    best = candidates[0]
    return {
        "poster_url": TMDB_IMAGE_BASE + best["poster_path"],
        "page_url": f"{TMDB_SITE_BASE}/{best['media_type']}/{best['id']}",
        "tmdb_id": best["id"],
        "media_type": best["media_type"],
        "year": _result_year(best),
    }


def _search_tmdb(title, year=None, timeout=10, prefer_series=False):
    """Cherche une fiche, en élargissant progressivement :
      1. /search/multi dans la langue configurée ;
      2. /search/tv ou /search/movie — plus permissifs que multi, et c'est
         le titre lui-même qui dit lequel essayer en premier ;
      3. /search/multi sans forcer la langue, pour les catalogues sans
         fiche ni affiche en français (cinéma asiatique, éditeurs de niche).
    On s'arrête au premier résultat exploitable."""
    query = _clean_query(title)
    if not query:
        return None

    specific = ("tv", "movie") if prefer_series else ("movie", "tv")
    attempts = [("multi", TMDB_LANGUAGE)]
    attempts += [(endpoint, TMDB_LANGUAGE) for endpoint in specific]
    if not TMDB_LANGUAGE.lower().startswith("en"):
        attempts.append(("multi", "en-US"))

    for endpoint, language in attempts:
        best = _pick_best(_query_tmdb(query, language, timeout, endpoint), year)
        if best:
            return best
    return None


def _search_candidates(release):
    """(titres à essayer, année) pour une sortie. Les titres vont du plus
    probable au moins probable : un seul essai pour un titre simple,
    plusieurs pour un coffret, dont le titre tel qu'écrit par le site
    source est introuvable dans TMDB. L'année, si le titre en portait une,
    ne fait pas partie du texte cherché mais sert à trier les résultats."""
    analysis = analyze_title(release.get("title", ""), release.get("details", ""))
    candidates = list(analysis["search_titles"])
    # L'année relevée sur la fiche du site source est plus fiable que celle
    # devinée dans le titre : elle prime quand elle existe.
    year = release.get("year") or analysis["year"]
    raw = (release.get("title") or "").strip()
    if raw and raw not in candidates:
        candidates.append(raw)
    # Plafond : évite de marteler l'API sur un titre très découpé
    return candidates[:6], year, analysis["is_series"]


MAX_CONSECUTIVE_FAILURES = 3


def enrich_with_posters(releases, cache_file, request_delay=0.25,
                        search_fn=_search_tmdb, deadline=None):
    """Ajoute poster_url / poster_page_url à chaque release (dict), avec
    cache persistant sur disque. `search_fn` est injectable pour les tests.
    `deadline` est un instant time.monotonic() au-delà duquel on s'arrête,
    le reste étant repris au passage suivant.

    Compteurs du tour exposés dans `enrich_with_posters.last_stats`."""
    if not is_configured():
        return releases

    cache = load_json_cache(cache_file)
    now = datetime.now(timezone.utc)
    changed = False
    looked_up = 0
    pending = 0
    consecutive_failures = 0
    stopped_reason = None

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
            if stopped_reason:
                pending += 1
                continue
            if deadline is not None and time.monotonic() > deadline:
                stopped_reason = "budget de temps du rafraîchissement épuisé"
                pending += 1
                continue

            result = None
            tried = []
            failed = None
            candidates, year, prefer_series = _search_candidates(r)
            for candidate in candidates:
                tried.append(candidate)
                try:
                    result = search_fn(candidate, year=year, prefer_series=prefer_series)
                except Exception as exc:  # noqa: BLE001
                    failed = exc
                    break
                time.sleep(request_delay)
                if result:
                    break

            if failed is not None:
                # Échec de requête : on ne met RIEN en cache, sinon une
                # coupure passagère marquerait des centaines de titres
                # comme « sans affiche » pour plusieurs jours.
                consecutive_failures += 1
                pending += 1
                logger.warning("TMDB injoignable (%d/%d avant abandon) pour %r : %s",
                               consecutive_failures, MAX_CONSECUTIVE_FAILURES, title, failed)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    stopped_reason = "TMDB injoignable à répétition"
                continue

            consecutive_failures = 0
            looked_up += 1
            changed = True
            if result:
                cache[key] = {**result, "query": tried[-1], "found": True,
                              "checked_at": now.isoformat()}
            else:
                logger.info("TMDB : rien trouvé pour %r (essais : %s%s)",
                            title, tried, f", année {year}" if year else "")
                cache[key] = {"found": False, "checked_at": now.isoformat()}
            entry = cache[key]

        if entry and entry.get("found"):
            r["poster_url"] = entry["poster_url"]
            r["poster_page_url"] = entry["page_url"]

    if changed:
        save_json_cache(cache_file, cache)

    matched = sum(1 for r in releases if r.get("poster_url"))
    enrich_with_posters.last_stats = {
        "looked_up": looked_up,
        "pending": pending,
        "matched": matched,
        "total": len(releases),
        "stopped_reason": stopped_reason,
    }
    logger.info("TMDB : %d/%d sorties avec affiche (%d recherches, %d en attente)%s",
                matched, len(releases), looked_up, pending,
                f" — arrêt : {stopped_reason}" if stopped_reason else "")
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
