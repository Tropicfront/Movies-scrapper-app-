"""
Croise les sorties scrapées avec la bibliothèque Jellyfin (films ET
séries — un coffret Blu-ray/4K peut tout aussi bien être une série ou un
anime qu'un film), pour repérer ce que tu possèdes déjà (utile pour
savoir si une nouvelle sortie est une réédition/upgrade d'un titre que tu
as déjà, par ex. un Steelbook 4K d'un film que tu as en DVD).

Configuration (variables d'environnement) :
- JELLYFIN_URL      : ex. http://192.168.1.10:8096 (laisser vide pour désactiver)
- JELLYFIN_API_KEY  : clé générée dans Jellyfin (Tableau de bord > Clés API)

Authentification : Jellyfin 12.0 a désactivé les méthodes historiques
(en-tête X-Emby-Token, en-tête X-MediaBrowser-Token et paramètre d'URL
?api_key=), le réglage serveur EnableLegacyAuthorization passant à false
par défaut. Elles renvoient désormais 401 même avec une clé valide. Seul
l'en-tête `Authorization: MediaBrowser Token="<clé>"` est accepté — et il
l'est aussi par les versions antérieures (10.8+), donc pas besoin de
détecter la version du serveur.

Le titre de chaque sortie est normalisé (minuscules, sans accents, sans
mentions d'édition/format comme "Blu-ray", "4K", "Steelbook"...) puis comparé
au titre normalisé de chaque film/série Jellyfin. Une comparaison approximative
(difflib) sert de filet pour les petites variations de formulation.
"""

import difflib
import logging
import os

import requests

from date_utils import normalize_title as _normalize

logger = logging.getLogger(__name__)

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
FUZZY_CUTOFF = float(os.environ.get("JELLYFIN_FUZZY_CUTOFF", "0.88"))


# Identité annoncée au serveur : sans valeur fonctionnelle pour une clé API,
# mais elle rend l'application identifiable dans les journaux Jellyfin.
CLIENT_NAME = "Sorties Films"
DEVICE_NAME = "sorties-films"


def is_configured():
    return bool(JELLYFIN_URL and JELLYFIN_API_KEY)


def _auth_headers():
    """En-têtes d'authentification. Voir la note en tête de fichier : c'est
    la seule forme acceptée par Jellyfin 12, et elle marche aussi avant."""
    return {
        "Authorization": (
            f'MediaBrowser Token="{JELLYFIN_API_KEY}", '
            f'Client="{CLIENT_NAME}", Device="{DEVICE_NAME}", '
            f'DeviceId="{DEVICE_NAME}", Version="1.0"'
        ),
        "Accept": "application/json",
    }


def _explain_http_error(exc, resp):
    """Message d'erreur exploitable plutôt qu'un 401 sec."""
    if resp is not None and resp.status_code == 401:
        return ("Jellyfin a refusé la clé API (401). Vérifie qu'elle existe "
                "toujours dans Tableau de bord > Clés API : Jellyfin 12 "
                "invalide certaines clés lors de la migration.")
    if resp is not None and resp.status_code == 404:
        return ("Jellyfin a répondu 404 : vérifie JELLYFIN_URL (elle doit "
                "pointer vers la racine du serveur, sans /web ni slash final).")
    return str(exc)


def get_server_info(timeout=10):
    """Interroge /System/Info : sert à vérifier que l'URL et la clé sont
    bonnes, et à connaître la version du serveur."""
    if not is_configured():
        return {}
    resp = requests.get(f"{JELLYFIN_URL}/System/Info",
                        headers=_auth_headers(), timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return {
        "server_name": data.get("ServerName"),
        "version": data.get("Version"),
    }


def fetch_library_titles(timeout=20):
    """Récupère l'ensemble des titres (normalisés) des films ET séries de
    la bibliothèque Jellyfin."""
    if not is_configured():
        return set()

    url = f"{JELLYFIN_URL}/Items"
    params = {
        "IncludeItemTypes": "Movie,Series",
        "Recursive": "true",
        "Fields": "OriginalTitle",
        "Limit": 10000,
    }
    resp = requests.get(url, params=params, headers=_auth_headers(), timeout=timeout)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        logger.error("Jellyfin : %s", _explain_http_error(exc, resp))
        raise
    items = resp.json().get("Items", [])

    titles = set()
    movies = series = 0
    for item in items:
        if item.get("Type") == "Series":
            series += 1
        else:
            movies += 1
        for key in ("Name", "OriginalTitle"):
            val = item.get(key)
            if val:
                norm = _normalize(val)
                if norm:
                    titles.add(norm)

    logger.info("Jellyfin : %d films + %d séries récupérés dans la bibliothèque", movies, series)
    fetch_library_titles.last_counts = {"movies": movies, "series": series,
                                        "items": len(items)}
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
    logger.info("Jellyfin : %d/%d sorties correspondent à un titre déjà possédé", matched, len(releases))
    return releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not is_configured():
        print("JELLYFIN_URL / JELLYFIN_API_KEY non définis.")
    else:
        print("Serveur :", get_server_info())
        titles = fetch_library_titles()
        print(f"{len(titles)} titres récupérés.")
        for t in sorted(titles)[:20]:
            print(" -", t)
