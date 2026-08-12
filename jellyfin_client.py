"""
Croise les sorties scrapées avec la bibliothèque de films Jellyfin, pour
repérer les films que tu possèdes déjà (utile pour savoir si une nouvelle
sortie est une réédition/upgrade d'un film que tu as déjà, par ex. un
Steelbook 4K d'un film que tu as en DVD).

Configuration (variables d'environnement) :
- JELLYFIN_URL      : ex. http://192.168.1.10:8096 (laisser vide pour désactiver)
- JELLYFIN_API_KEY  : clé générée dans Jellyfin (Tableau de bord > Clés API)

Le titre de chaque sortie est normalisé (minuscules, sans accents, sans
mentions d'édition/format comme "Blu-ray", "4K", "Steelbook"...) puis comparé
au titre normalisé de chaque film Jellyfin. Une comparaison approximative
(difflib) sert de filet pour les petites variations de formulation.
"""

import difflib
import logging
import os
import re
import unicodedata

import requests

logger = logging.getLogger(__name__)

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
FUZZY_CUTOFF = float(os.environ.get("JELLYFIN_FUZZY_CUTOFF", "0.88"))

_JUNK_WORDS = [
    "edition collector", "édition collector", "collector",
    "boitier steelbook", "boîtier steelbook", "steelbook",
    "4k ultra hd", "ultra hd", "4k uhd", "4k",
    "blu-ray", "bluray", "dvd",
    "combo", "coffret", "limite", "limitee", "limitée", "limité",
    "version longue", "director's cut", "sortie",
]


def is_configured():
    return bool(JELLYFIN_URL and JELLYFIN_API_KEY)


def _normalize(title):
    if not title:
        return ""
    t = title.lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\[[^\]]*\]", " ", t)   # retire [Blu-ray], [4K Ultra HD - Steelbound]...
    t = re.sub(r"\([^)]*\)", " ", t)    # retire (1995), (Amores perros)...
    for junk in _JUNK_WORDS:
        t = t.replace(junk, " ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def fetch_library_titles(timeout=20):
    """Récupère l'ensemble des titres (normalisés) des films de la bibliothèque Jellyfin."""
    if not is_configured():
        return set()

    url = f"{JELLYFIN_URL}/Items"
    params = {
        "IncludeItemTypes": "Movie",
        "Recursive": "true",
        "Fields": "OriginalTitle",
        "Limit": 10000,
    }
    headers = {"X-Emby-Token": JELLYFIN_API_KEY}

    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    items = resp.json().get("Items", [])

    titles = set()
    for item in items:
        for key in ("Name", "OriginalTitle"):
            val = item.get(key)
            if val:
                norm = _normalize(val)
                if norm:
                    titles.add(norm)

    logger.info("Jellyfin : %d films récupérés dans la bibliothèque", len(items))
    return titles


def annotate_with_library(releases, library_titles):
    """Ajoute un champ 'in_jellyfin' (bool) à chaque release."""
    if not library_titles:
        for r in releases:
            r["in_jellyfin"] = False
        return releases

    for r in releases:
        norm = _normalize(r.get("title", ""))
        found = norm in library_titles
        if not found and norm:
            close = difflib.get_close_matches(norm, library_titles, n=1, cutoff=FUZZY_CUTOFF)
            found = bool(close)
        r["in_jellyfin"] = found

    matched = sum(1 for r in releases if r["in_jellyfin"])
    logger.info("Jellyfin : %d/%d sorties correspondent à un film déjà possédé", matched, len(releases))
    return releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not is_configured():
        print("JELLYFIN_URL / JELLYFIN_API_KEY non définis.")
    else:
        titles = fetch_library_titles()
        print(f"{len(titles)} titres récupérés.")
        for t in sorted(titles)[:20]:
            print(" -", t)
