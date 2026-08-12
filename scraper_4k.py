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
"""

import re
import logging
from bs4 import BeautifulSoup, NavigableString, Tag

from date_utils import polite_get, make_release

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
                ))
                current_title, current_url = None, None
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
                    ))
                    current_title, current_url = None, None

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in get_releases()[:20]:
        print(r["date_text"], "|", r["title"], "|", r["details"])
