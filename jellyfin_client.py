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


# Taille d'une page de résultats. Jellyfin pagine : demander une limite
# énorme d'un coup n'est pas fiable, et surtout rien n'indiquait jusqu'ici
# si la réponse avait été tronquée. On lit maintenant TotalRecordCount et
# on boucle, ce qui permet aussi de comparer le total annoncé par le
# serveur au nombre réellement reçu.
PAGE_SIZE = 500

# Types d'éléments considérés. "Video" couvre les films qu'un scan n'a pas
# réussi à identifier : Jellyfin les range sous ce type, et non sous
# "Movie", ce qui les rendait invisibles ici alors qu'ils apparaissent
# bien dans la bibliothèque côté interface.
MOVIE_TYPES = ("Movie", "Video")
SERIES_TYPES = ("Series",)


def _get_json(path, params, timeout=20):
    resp = requests.get(f"{JELLYFIN_URL}{path}", params=params,
                        headers=_auth_headers(), timeout=timeout)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        logger.error("Jellyfin : %s", _explain_http_error(exc, resp))
        raise
    return resp.json()


def _fetch_items(item_types, timeout=20):
    """Récupère tous les éléments d'un ou plusieurs types, page par page.
    Renvoie (items, total_annoncé_par_le_serveur)."""
    items = []
    total = None
    start_index = 0
    while True:
        data = _get_json("/Items", {
            "IncludeItemTypes": ",".join(item_types),
            "Recursive": "true",
            "Fields": "OriginalTitle",
            "StartIndex": start_index,
            "Limit": PAGE_SIZE,
        }, timeout=timeout)
        page = data.get("Items", [])
        if total is None:
            total = data.get("TotalRecordCount")
        items.extend(page)
        start_index += len(page)
        if not page or (total is not None and start_index >= total):
            break
        if len(page) < PAGE_SIZE:
            break
    return items, total


def fetch_library_titles(timeout=20):
    """Récupère l'ensemble des titres (normalisés) des films ET séries de
    la bibliothèque Jellyfin.

    Les films et les séries sont demandés séparément : cela permet de
    comparer, pour chaque type, le total annoncé par le serveur au nombre
    d'éléments effectivement reçus, et de repérer tout de suite un écart."""
    if not is_configured():
        return set()

    movie_items, movie_total = _fetch_items(MOVIE_TYPES, timeout)
    series_items, series_total = _fetch_items(SERIES_TYPES, timeout)

    titles = set()
    by_type = {}
    for item in movie_items + series_items:
        by_type[item.get("Type", "?")] = by_type.get(item.get("Type", "?"), 0) + 1
        for key in ("Name", "OriginalTitle"):
            val = item.get(key)
            if val:
                norm = _normalize(val)
                if norm:
                    titles.add(norm)

    logger.info("Jellyfin : %d films + %d séries récupérés (détail par type : %s)",
                len(movie_items), len(series_items), by_type)
    for label, received, announced in (("films", len(movie_items), movie_total),
                                       ("séries", len(series_items), series_total)):
        if announced is not None and received != announced:
            logger.warning("Jellyfin : %d %s reçus alors que le serveur en annonce %d",
                           received, label, announced)

    fetch_library_titles.last_counts = {
        "movies": len(movie_items),
        "series": len(series_items),
        "movies_announced": movie_total,
        "series_announced": series_total,
        "by_type": by_type,
    }
    return titles


def get_libraries(timeout=20):
    """Liste les bibliothèques et, pour chacune, le nombre d'éléments par
    type. Sert à comprendre un écart de comptage : c'est ce qui révèle
    qu'un dossier est déclaré avec un type de contenu inattendu, ou que ses
    éléments ne sont pas identifiés."""
    if not is_configured():
        return []

    folders = _get_json("/Items", {
        "IncludeItemTypes": "CollectionFolder",
        "Recursive": "false",
    }, timeout=timeout).get("Items", [])

    libraries = []
    for folder in folders:
        detail = _get_json("/Items", {
            "ParentId": folder.get("Id"),
            "Recursive": "true",
            "Limit": 0,
        }, timeout=timeout)
        counts = {}
        page = _get_json("/Items", {
            "ParentId": folder.get("Id"),
            "Recursive": "true",
            "Limit": 2000,
        }, timeout=timeout).get("Items", [])
        for item in page:
            counts[item.get("Type", "?")] = counts.get(item.get("Type", "?"), 0) + 1
        libraries.append({
            "name": folder.get("Name"),
            "collection_type": folder.get("CollectionType"),
            "total_items": detail.get("TotalRecordCount"),
            "by_type": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        })
    return libraries


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
