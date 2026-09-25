"""
Récupération des boutons d'achat (Amazon / Fnac) sur les fiches
individuelles, partagée par les deux scrapers : ni 4k-ultra-hd.fr ni
edition-limitee.fr ne donnent ces liens dans leurs pages de liste, il faut
une requête par titre. La logique étant strictement la même de part et
d'autre, elle est écrite une seule fois ici.

Deux garde-fous importants :

- **Aucun résultat négatif n'est mis en cache après un échec de
  chargement.** Une entrée « rien trouvé » n'est écrite que si la page a
  réellement été lue. Sinon, une coupure réseau ou un blocage en milieu de
  parcours gravait « pas de liens » pour des centaines de fiches, qui
  n'étaient alors plus réessayées avant l'expiration du délai.

- **La boucle s'arrête d'elle-même** quand l'hôte nous refuse
  (HostUnavailable), après plusieurs échecs consécutifs, ou quand le budget
  de temps du rafraîchissement est écoulé. Ce qui reste à faire est compté
  et renvoyé, pour que l'appelant puisse programmer un nouveau passage.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from date_utils import HostUnavailable
from json_cache import load_json_cache, save_json_cache

logger = logging.getLogger(__name__)

# Une fiche qui n'a donné aucun lien (ou un seul) est réessayée après ce délai
RETRY_INCOMPLETE_AFTER_DAYS = 7
# Nombre d'échecs de chargement consécutifs au-delà duquel on abandonne la
# source pour ce tour : au-delà, ce n'est plus un incident isolé.
MAX_CONSECUTIVE_FAILURES = 3


def _needs_lookup(entry, now):
    if entry is None:
        return True
    if entry.get("amazon_url") and entry.get("fnac_url"):
        return False  # complet, rien à refaire
    checked_at = entry.get("checked_at")
    if not checked_at:
        return True
    try:
        seen = datetime.fromisoformat(checked_at)
    except ValueError:
        return True
    return now - seen > timedelta(days=RETRY_INCOMPLETE_AFTER_DAYS)


def enrich_releases(releases, cache_file, source_name, fetch_fn,
                    url_field="url", deadline=None):
    """Complète amazon_url/fnac_url pour les sorties de `source_name`.

    `fetch_fn(url)` doit renvoyer un dict ({"amazon_url", "fnac_url", et
    éventuellement "year"}) et LEVER une exception si la page n'a pas pu
    être chargée — c'est ce qui permet de distinguer « lu, rien trouvé » de
    « pas lu ». Les clés reconnues sont recopiées sur la sortie quand elle
    ne les porte pas déjà ; « year » sert à identifier la bonne fiche TMDB.
    `deadline` est un instant `time.monotonic()` au-delà duquel on s'arrête.

    Renvoie `releases`. Les compteurs du tour sont exposés dans
    `enrich_releases.last_stats`.
    """
    cache = load_json_cache(cache_file)
    now = datetime.now(timezone.utc)
    changed = False
    looked_up = 0
    pending = 0
    consecutive_failures = 0
    stopped_reason = None

    concerned = [r for r in releases if r.get("source") == source_name]

    for r in concerned:
        if r.get("amazon_url") and r.get("fnac_url"):
            continue
        url = r.get(url_field)
        if not url:
            continue

        entry = cache.get(url)
        if _needs_lookup(entry, now):
            if stopped_reason:
                pending += 1
                continue
            if deadline is not None and time.monotonic() > deadline:
                stopped_reason = "budget de temps du rafraîchissement épuisé"
                pending += 1
                continue

            try:
                found = fetch_fn(url)
            except HostUnavailable as exc:
                stopped_reason = str(exc)
                pending += 1
                continue
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                pending += 1
                logger.warning("[%s] Fiche illisible (%d/%d avant abandon) %s : %s",
                               source_name, consecutive_failures,
                               MAX_CONSECUTIVE_FAILURES, url, exc)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    stopped_reason = "trop d'échecs de chargement consécutifs"
                continue

            consecutive_failures = 0
            looked_up += 1
            entry = {
                "amazon_url": found.get("amazon_url"),
                "fnac_url": found.get("fnac_url"),
                "year": found.get("year"),
                "found": bool(found.get("amazon_url") or found.get("fnac_url")),
                "checked_at": now.isoformat(),
            }
            cache[url] = entry
            changed = True

        if entry:
            for field in ("amazon_url", "fnac_url", "year"):
                if entry.get(field) and not r.get(field):
                    r[field] = entry[field]

    if changed:
        save_json_cache(cache_file, cache)

    matched = sum(1 for r in concerned if r.get("amazon_url") or r.get("fnac_url"))
    enrich_releases.last_stats = {
        "source": source_name,
        "looked_up": looked_up,
        "pending": pending,
        "matched": matched,
        "total": len(concerned),
        "stopped_reason": stopped_reason,
    }
    logger.info("[%s] Liens d'achat : %d/%d sorties pourvues "
                "(%d fiches visitées, %d en attente)%s",
                source_name, matched, len(concerned), looked_up, pending,
                f" — arrêt : {stopped_reason}" if stopped_reason else "")
    return releases
