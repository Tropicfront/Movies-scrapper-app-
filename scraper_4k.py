"""
Scraper pour 4k-ultra-hd.fr

Parcourt les pages "Prochaines sorties 4K" (paginées) et "Date en attente"
(éditions annoncées sans date précise). Pour chaque film, la page contient
un lien vers la fiche produit (/film/<slug>) suivi d'une ligne
"Sortie <date> : <édition> <format> (<année d'origine>)".

Le parsing se fait en parcourant le document dans l'ordre (comme à la
lecture) : on retient le dernier lien de fiche produit rencontré, puis on
rattache la prochaine ligne "Sortie ..." trouvée à ce film. Cette méthode
ne dépend pas de classes CSS précises, donc elle résiste mieux aux petites
évolutions de la mise en page du site.

Les boutons d'achat affiliés (logos Amazon / Fnac) ne sont PAS présents
sur ces pages de listing : ils n'apparaissent que sur la page individuelle
de chaque film (/film/<slug>, dans un bloc "afi5-logos-row"). Il faut donc
une requête HTTP supplémentaire par film pour les récupérer — coûteux sur
~140 titres, d'où un cache persistant (voir enrich_with_affiliate_links)
qui ne re-télécharge que les fiches pas encore vues.
"""

import re
import logging
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup, NavigableString, Tag

from date_utils import (
    classify_purchase_link,
    extract_purchase_links,
    make_release,
    polite_get,
)
from json_cache import load_json_cache, save_json_cache

logger = logging.getLogger(__name__)

BASE = "https://4k-ultra-hd.fr"
SOURCE_NAME = "4K-Ultra-HD.fr"

LISTING_URLS = [
    f"{BASE}/prochaines-sorties-blu-ray-4k-ultra-hd",
    f"{BASE}/sorties-4k/date-en-attente",
]

FILM_LINK_RE = re.compile(r"^https?://4k-ultra-hd\.fr/film/[^/?#]+/?$")
SORTIE_LINE_RE = re.compile(r"^Sortie\s+(?P<date>[^:]+?)\s*:\s*(?P<details>.+)$", re.IGNORECASE)
PAGE_LINK_RE = re.compile(r"/prochaines-sorties-blu-ray-4k-ultra-hd/page/(\d+)")

# La reconnaissance des boutons d'achat (Amazon / Fnac, et les marchands à
# ignorer) est centralisée dans date_utils.classify_purchase_link : elle est
# partagée avec le scraper edition-limitee.fr, qui rencontre les mêmes
# raccourcisseurs d'affiliation.

RETRY_NOT_FOUND_AFTER_DAYS = 7


def _normalize_url(href):
    if href.startswith("http"):
        return href.split("?")[0]
    return BASE + href.split("?")[0]


def _extract_page(html):
    """Extrait les releases d'une page (retourne aussi le nombre max de pages détecté)."""
    soup = BeautifulSoup(html, "html.parser")
    releases = []
    current_title = None
    current_url = None
    current_amazon_url = None
    current_fnac_url = None
    max_page = 1

    for node in soup.descendants:
        if isinstance(node, Tag):
            if node.name == "a":
                href = node.get("href", "") or ""
                full_href = _normalize_url(href) if href.startswith("/") or href.startswith("http") else ""
                if full_href and FILM_LINK_RE.match(full_href):
                    text = node.get_text(strip=True)
                    if text:  # ignore le lien-image sans texte
                        current_title = text
                        current_url = full_href
                        current_amazon_url = None
                        current_fnac_url = None
                elif current_title is not None:
                    # Liens affiliés potentiellement présents dans le même
                    # bloc que la fiche en cours (boutons "Acheter sur...")
                    kind, link_href = classify_purchase_link(node)
                    if kind == "amazon" and not current_amazon_url:
                        current_amazon_url = link_href
                    elif kind == "fnac" and not current_fnac_url:
                        current_fnac_url = link_href
                m = PAGE_LINK_RE.search(href)
                if m:
                    max_page = max(max_page, int(m.group(1)))
            continue

        if isinstance(node, NavigableString):
            text = str(node).strip()
            if not text or not current_title:
                continue
            m = SORTIE_LINE_RE.match(text)
            if m:
                releases.append(make_release(
                    title=current_title,
                    url=current_url,
                    source=SOURCE_NAME,
                    date_text=m.group("date"),
                    details=m.group("details"),
                    amazon_url=current_amazon_url,
                    fnac_url=current_fnac_url,
                ))
                current_title, current_url = None, None
                current_amazon_url, current_fnac_url = None, None
            elif text.startswith("Sortie") and node.parent is not None:
                # La date/les infos sont parfois réparties sur plusieurs balises
                # (ex: <strong>) dans le même bloc : on relit le texte complet du parent.
                parent_text = node.parent.get_text(" ", strip=True)
                m2 = SORTIE_LINE_RE.match(parent_text)
                if m2:
                    releases.append(make_release(
                        title=current_title,
                        url=current_url,
                        source=SOURCE_NAME,
                        date_text=m2.group("date"),
                        details=m2.group("details"),
                        amazon_url=current_amazon_url,
                        fnac_url=current_fnac_url,
                    ))
                    current_title, current_url = None, None
                    current_amazon_url, current_fnac_url = None, None

    return releases, max_page


def get_releases(max_pages=6):
    all_releases = []
    seen = set()

    for base_url in LISTING_URLS:
        try:
            html = polite_get(base_url)
        except Exception:
            logger.exception("Échec du chargement de %s", base_url)
            continue

        releases, max_page = _extract_page(html)
        for r in releases:
            key = (r["url"], r["date_text"])
            if key not in seen:
                seen.add(key)
                all_releases.append(r)

        # pagination uniquement pour la liste "prochaines sorties" (les pages
        # suivantes utilisent le même motif /page/N)
        if "prochaines-sorties" in base_url:
            page = 2
            while page <= min(max_page, max_pages):
                page_url = f"{base_url}/page/{page}"
                try:
                    html = polite_get(page_url)
                except Exception:
                    logger.exception("Échec du chargement de %s", page_url)
                    break
                releases, _ = _extract_page(html)
                if not releases:
                    break
                for r in releases:
                    key = (r["url"], r["date_text"])
                    if key not in seen:
                        seen.add(key)
                        all_releases.append(r)
                page += 1

    logger.info("[%s] %d sorties trouvées", SOURCE_NAME, len(all_releases))
    return all_releases


def _fetch_affiliate_links_from_film_page(url, timeout=15):
    """Va chercher les liens Amazon / Fnac sur la page individuelle du film.
    Les boutons sont dans un bloc "afi5-logos-row" au moment de l'écriture,
    mais on ne dépend pas de cette classe : on scanne tous les liens de la
    page et on reconnaît le marchand via le libellé de son logo."""
    try:
        html = polite_get(url, timeout=timeout)
    except Exception:
        logger.exception("Échec du chargement de la fiche film %s", url)
        return None, None

    soup = BeautifulSoup(html, "html.parser")
    return extract_purchase_links(soup)


def enrich_with_affiliate_links(releases, cache_file, request_delay=0.3):
    """Complète amazon_url/fnac_url pour les sorties 4K-Ultra-HD.fr qui ne
    les ont pas encore (une requête HTTP par fiche film manquante, mise en
    cache pour ne pas la refaire à chaque rafraîchissement)."""
    cache = load_json_cache(cache_file)
    now = datetime.now(timezone.utc)
    changed = False

    for r in releases:
        if r.get("source") != SOURCE_NAME:
            continue
        # On ne saute la fiche que si les DEUX liens sont déjà connus.
        # Avant, un seul lien trouvé sur la page de listing (en pratique
        # souvent celui de la Fnac) suffisait à empêcher la visite de la
        # fiche film, donc à ne jamais récupérer le lien Amazon.
        if r.get("amazon_url") and r.get("fnac_url"):
            continue
        url = r.get("url")
        if not url:
            continue

        entry = cache.get(url)
        needs_lookup = entry is None
        # Une entrée en cache qui n'a trouvé qu'un seul des deux liens est
        # réessayée comme une entrée vide (le site peut ajouter le second).
        incomplete = bool(entry) and not (entry.get("amazon_url") and entry.get("fnac_url"))
        if entry and incomplete and entry.get("checked_at"):
            try:
                checked_at = datetime.fromisoformat(entry["checked_at"])
                if now - checked_at > timedelta(days=RETRY_NOT_FOUND_AFTER_DAYS):
                    needs_lookup = True
            except ValueError:
                needs_lookup = True

        if needs_lookup:
            amazon_url, fnac_url = _fetch_affiliate_links_from_film_page(url)
            found = bool(amazon_url or fnac_url)
            cache[url] = {
                "amazon_url": amazon_url,
                "fnac_url": fnac_url,
                "found": found,
                "checked_at": now.isoformat(),
            }
            changed = True
            time.sleep(request_delay)
            entry = cache[url]

        if entry.get("amazon_url"):
            r["amazon_url"] = entry["amazon_url"]
        if entry.get("fnac_url"):
            r["fnac_url"] = entry["fnac_url"]

    if changed:
        save_json_cache(cache_file, cache)

    matched = sum(1 for r in releases if r.get("source") == SOURCE_NAME and (r.get("amazon_url") or r.get("fnac_url")))
    logger.info("[%s] Liens affiliés : %d sorties avec au moins un lien Amazon/Fnac", SOURCE_NAME, matched)
    return releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    releases = get_releases()[:20]
    releases = enrich_with_affiliate_links(releases, "/tmp/4k_affiliate_cache_test.json")
    for r in releases:
        print(r["date_text"], "|", r["title"], "|", r["details"], "| amazon:", r["amazon_url"], "| fnac:", r["fnac_url"])
