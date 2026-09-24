"""
Croise les sorties scrapées avec la bibliothèque Jellyfin (films ET
séries — un coffret Blu-ray/4K peut tout aussi bien être une série ou un
anime qu'un film), pour repérer ce que tu possèdes déjà (utile pour
savoir si une nouvelle sortie est une réédition/upgrade d'un titre que tu
as déjà, par ex. un Steelbook 4K d'un film que tu as en DVD).

Configuration (variables d'environnement) :
- JELLYFIN_URL      : ex. http://192.168.1.10:8096 (laisser vide pour désactiver)
- JELLYFIN_API_KEY  : clé générée dans Jellyfin (Tableau de bord > Clés API)
- JELLYFIN_USER_ID  : facultatif, identifiant d'utilisateur à utiliser pour
                      les requêtes (par défaut, le premier administrateur)

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
JELLYFIN_USER_ID = os.environ.get("JELLYFIN_USER_ID", "")
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
# énorme d'un coup n'est pas fiable, et rien n'indiquait si la réponse
# avait été tronquée. On lit TotalRecordCount et on boucle.
PAGE_SIZE = 500

# Seuls ces types nous intéressent. Surtout, les BoxSet sont exclus : ce
# sont les collections que Jellyfin crée tout seul ("saga X", "trilogie
# Y"). Elles étaient comptées comme des films ET leurs noms entraient dans
# la liste des titres possédés, ce qui pouvait faire passer une sortie pour
# déjà possédée alors qu'on n'a que d'autres films de la même collection.
WANTED_TYPES = ("Movie", "Series")
IGNORED_TYPES = ("BoxSet", "Collection", "Folder", "CollectionFolder",
                 "Playlist", "MusicAlbum", "Audio", "Book")

# Bibliothèques à ne pas parcourir : celle des collections n'apporte rien.
IGNORED_COLLECTION_TYPES = ("boxsets", "playlists", "music", "books", "photos")


def _get_json(path, params=None, timeout=20):
    resp = requests.get(f"{JELLYFIN_URL}{path}", params=params or {},
                        headers=_auth_headers(), timeout=timeout)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        logger.error("Jellyfin : %s", _explain_http_error(exc, resp))
        raise
    return resp.json()


def get_user_id(timeout=20):
    """Identifiant d'utilisateur à employer pour les requêtes.

    C'est le point clé : interrogé sans utilisateur, /Items ne renvoie pas
    forcément l'ensemble des bibliothèques, alors que l'interface web, elle,
    interroge toujours au nom d'un utilisateur. On reproduit donc ce que
    l'interface fait, en prenant le premier administrateur — ou celui
    imposé par JELLYFIN_USER_ID."""
    if JELLYFIN_USER_ID:
        return JELLYFIN_USER_ID
    cached = getattr(get_user_id, "_cached", None)
    if cached:
        return cached
    try:
        users = _get_json("/Users", timeout=timeout)
    except Exception:  # noqa: BLE001
        logger.warning("Jellyfin : impossible de lister les utilisateurs, "
                       "les requêtes se feront sans identifiant")
        return ""
    admins = [u for u in users if (u.get("Policy") or {}).get("IsAdministrator")]
    chosen = (admins or users or [{}])[0].get("Id", "")
    get_user_id._cached = chosen
    return chosen


def _fetch_items(parent_id=None, item_types=WANTED_TYPES, timeout=20):
    """Tous les éléments d'un type donné, page par page.
    Renvoie (items, total annoncé par le serveur)."""
    user_id = get_user_id(timeout=timeout)
    items = []
    total = None
    start_index = 0
    while True:
        params = {
            "IncludeItemTypes": ",".join(item_types),
            "ExcludeItemTypes": ",".join(IGNORED_TYPES),
            "Recursive": "true",
            "Fields": "OriginalTitle",
            "StartIndex": start_index,
            "Limit": PAGE_SIZE,
        }
        if user_id:
            params["userId"] = user_id
        if parent_id:
            params["ParentId"] = parent_id
        data = _get_json("/Items", params, timeout=timeout)
        page = data.get("Items", [])
        if total is None:
            total = data.get("TotalRecordCount")
        items.extend(page)
        start_index += len(page)
        if not page or len(page) < PAGE_SIZE:
            break
        if total is not None and start_index >= total:
            break
    return items, total


def get_library_folders(timeout=20):
    """Bibliothèques de premier niveau, celles de collections exclues."""
    user_id = get_user_id(timeout=timeout)
    params = {"IncludeItemTypes": "CollectionFolder", "Recursive": "false"}
    if user_id:
        params["userId"] = user_id
    folders = _get_json("/Items", params, timeout=timeout).get("Items", [])
    return [f for f in folders
            if (f.get("CollectionType") or "").lower() not in IGNORED_COLLECTION_TYPES]


def fetch_library_titles(timeout=20):
    """Titres normalisés de tous les films et séries de la bibliothèque.

    Le parcours se fait **bibliothèque par bibliothèque**, comme le fait
    l'interface web : c'est le seul moyen fiable quand les films sont
    répartis dans plusieurs dossiers (films, anime...), une requête globale
    pouvant n'en couvrir qu'une partie. Une requête globale sert tout de
    même de filet, pour rattraper ce qu'un parcours par dossier raterait.
    """
    if not is_configured():
        return set()

    seen_ids = set()
    items = []
    per_library = {}

    try:
        folders = get_library_folders(timeout=timeout)
    except Exception:  # noqa: BLE001
        logger.warning("Jellyfin : impossible de lister les bibliothèques, "
                       "parcours global uniquement")
        folders = []

    for folder in folders:
        found, announced = _fetch_items(parent_id=folder.get("Id"), timeout=timeout)
        kept = 0
        for item in found:
            if item.get("Type") in IGNORED_TYPES:
                continue
            if item.get("Id") in seen_ids:
                continue
            seen_ids.add(item.get("Id"))
            items.append(item)
            kept += 1
        per_library[folder.get("Name") or "?"] = {
            "collection_type": folder.get("CollectionType"),
            "kept": kept,
            "announced": announced,
        }

    # Filet : une requête globale, pour les éléments hors bibliothèque ou
    # si le parcours par dossier n'a rien donné.
    global_items, global_total = _fetch_items(timeout=timeout)
    added_by_global = 0
    for item in global_items:
        if item.get("Type") in IGNORED_TYPES or item.get("Id") in seen_ids:
            continue
        seen_ids.add(item.get("Id"))
        items.append(item)
        added_by_global += 1

    titles = set()
    by_type = {}
    for item in items:
        by_type[item.get("Type", "?")] = by_type.get(item.get("Type", "?"), 0) + 1
        for key in ("Name", "OriginalTitle"):
            val = item.get(key)
            if val:
                norm = _normalize(val)
                if norm:
                    titles.add(norm)

    movies = sum(v for k, v in by_type.items() if k == "Movie")
    series = sum(v for k, v in by_type.items() if k == "Series")
    logger.info("Jellyfin : %d films + %d séries (détail par type : %s ; "
                "par bibliothèque : %s ; +%d via la requête globale)",
                movies, series, by_type,
                {k: v["kept"] for k, v in per_library.items()}, added_by_global)

    fetch_library_titles.last_counts = {
        "movies": movies,
        "series": series,
        "by_type": by_type,
        "per_library": per_library,
        "added_by_global_sweep": added_by_global,
        "global_announced": global_total,
        "user_id_used": bool(get_user_id(timeout=timeout)),
    }
    return titles


def get_libraries(timeout=20):
    """Détail par bibliothèque : type de contenu et répartition par type
    d'élément. Sert à comprendre un écart de comptage."""
    if not is_configured():
        return []
    libraries = []
    for folder in get_library_folders(timeout=timeout):
        found, announced = _fetch_items(parent_id=folder.get("Id"), timeout=timeout)
        counts = {}
        for item in found:
            counts[item.get("Type", "?")] = counts.get(item.get("Type", "?"), 0) + 1
        libraries.append({
            "name": folder.get("Name"),
            "collection_type": folder.get("CollectionType"),
            "announced": announced,
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
