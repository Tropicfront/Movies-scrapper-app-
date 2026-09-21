"""Petit utilitaire partagé : lecture/écriture d'un cache JSON sur disque.
Utilisé par poster_lookup.py (affiches TMDB) et scraper_4k.py (liens
affiliés Amazon/Fnac) pour éviter de refaire les mêmes requêtes HTTP à
chaque rafraîchissement."""

import json
import logging
import os

logger = logging.getLogger(__name__)


def load_json_cache(cache_file):
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Cache %s illisible, il sera recréé", cache_file)
    return {}


def save_json_cache(cache_file, cache):
    """Écriture atomique : fichier temporaire puis renommage. Une requête qui
    lit le cache pendant l'écriture ne peut donc jamais tomber sur un JSON
    tronqué — ce qui devient probable dès que les rafraîchissements durent
    plusieurs minutes."""
    tmp = f"{cache_file}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_file)
