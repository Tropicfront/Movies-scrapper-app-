"""
Croise les sorties scrapées avec tes bibliothèques Radarr (films) et/ou
Sonarr (séries), sur le même principe que l'intégration Jellyfin : repérer
les titres déjà suivis pour les distinguer visuellement (badge, filtre).

Radarr et Sonarr ont chacun un vrai widget "calendrier" natif dans Homarr
et Homepage (poster + bouton IMDb, comme sur ta capture d'écran) — mais ce
widget se connecte DIRECTEMENT à ton instance Radarr/Sonarr, pas à un flux
ICS externe. Ce module ne remplace donc pas ce widget natif : il sert
uniquement à savoir, pour chaque sortie physique (Blu-ray/4K) scrapée sur
4k-ultra-hd.fr / edition-limitee.fr, si le film/la série est déjà suivi(e)
côté Radarr/Sonarr.

Configuration (variables d'environnement) :
- RADARR_URL / RADARR_API_KEY   : ex. http://192.168.1.10:7878 (vide = désactivé)
- SONARR_URL / SONARR_API_KEY   : ex. http://192.168.1.10:8989 (vide = désactivé)

Comme pour Jellyfin, les titres sont normalisés puis comparés avec un
filet de comparaison approximative (difflib) pour les petites variations
de formulation.
"""

import difflib
import logging
import os

import requests

from date_utils import normalize_title

logger = logging.getLogger(__name__)

RADARR_URL = os.environ.get("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")

SONARR_URL = os.environ.get("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")

FUZZY_CUTOFF = float(os.environ.get("ARR_FUZZY_CUTOFF", "0.88"))


def radarr_configured():
    return bool(RADARR_URL and RADARR_API_KEY)


def sonarr_configured():
    return bool(SONARR_URL and SONARR_API_KEY)


def is_configured():
    return radarr_configured() or sonarr_configured()


def _fetch_titles(base_url, api_key, endpoint, timeout=20):
    resp = requests.get(
        f"{base_url}{endpoint}",
        headers={"X-Api-Key": api_key},
        timeout=timeout,
    )
    resp.raise_for_status()
    items = resp.json()

    titles = set()
    for item in items:
        for key in ("title", "originalTitle", "cleanTitle"):
            val = item.get(key)
            if val:
                norm = normalize_title(val)
                if norm:
                    titles.add(norm)
    return titles, len(items)


def fetch_radarr_titles():
    if not radarr_configured():
        return set()
    titles, count = _fetch_titles(RADARR_URL, RADARR_API_KEY, "/api/v3/movie")
    logger.info("Radarr : %d films récupérés", count)
    return titles


def fetch_sonarr_titles():
    if not sonarr_configured():
        return set()
    titles, count = _fetch_titles(SONARR_URL, SONARR_API_KEY, "/api/v3/series")
    logger.info("Sonarr : %d séries récupérées", count)
    return titles


def _matches(norm_title, library_titles):
    if not norm_title or not library_titles:
        return False
    if norm_title in library_titles:
        return True
    return bool(difflib.get_close_matches(norm_title, library_titles, n=1, cutoff=FUZZY_CUTOFF))


def annotate_with_libraries(releases, radarr_titles, sonarr_titles):
    """Ajoute in_radarr / in_sonarr (bool) à chaque release."""
    for r in releases:
        norm = normalize_title(r.get("title", ""))
        r["in_radarr"] = _matches(norm, radarr_titles) if radarr_titles else False
        r["in_sonarr"] = _matches(norm, sonarr_titles) if sonarr_titles else False

    matched_radarr = sum(1 for r in releases if r.get("in_radarr"))
    matched_sonarr = sum(1 for r in releases if r.get("in_sonarr"))
    logger.info(
        "Radarr/Sonarr : %d sorties déjà dans Radarr, %d déjà dans Sonarr (sur %d)",
        matched_radarr, matched_sonarr, len(releases),
    )
    return releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not is_configured():
        print("RADARR_URL/RADARR_API_KEY et SONARR_URL/SONARR_API_KEY non définis.")
    else:
        r_titles = fetch_radarr_titles()
        s_titles = fetch_sonarr_titles()
        print(f"Radarr : {len(r_titles)} titres. Sonarr : {len(s_titles)} titres.")
