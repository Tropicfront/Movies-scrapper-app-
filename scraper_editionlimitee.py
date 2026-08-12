"""
Scraper pour edition-limitee.fr

Ce site n'a pas de page "calendrier" unique et stable : les sorties sont
publiées dans des articles de blog mensuels ("Août 2026", "Juillet 2026", ...)
dont l'URL change chaque mois et ne suit pas un format prévisible.

Stratégie :
1. On part de la page hub "/blu-ray-dvd/sortie-blu-ray-dvd/" qui liste les
   articles mensuels du plus récent au plus ancien.
2. On récupère les N articles les plus récents (le site publie généralement
   le mois en cours + le(s) mois suivant(s) à l'avance).
3. Dans chaque article, chaque sortie suit toujours le même motif :
   "<Titre> [ici en <formats>](<lien>). Sorti le <date>."
   repéré ici en cherchant tous les liens dont le texte commence par
   "ici en", puis en relisant le texte complet de leur bloc parent.
"""

import re
import logging
from bs4 import BeautifulSoup

from date_utils import polite_get, make_release

logger = logging.getLogger(__name__)

BASE = "https://edition-limitee.fr"
SOURCE_NAME = "Édition-Limitée.fr"
HUB_URL = f"{BASE}/blu-ray-dvd/sortie-blu-ray-dvd/"

# Repère les liens vers les articles mensuels du calendrier
MONTH_LINK_TEXT_RE = re.compile(
    r"la page sur les sorties|sorties bluray|sorties de|récapitulatif",
    re.IGNORECASE,
)

ENTRY_RE = re.compile(
    r"(?P<title>.+?)\s*ici en\s*(?P<formats>.+?)\.\s*Sorti le\s*(?P<date>[^.]+?)\.",
    re.IGNORECASE,
)


def _get_month_article_urls(limit=3):
    """Récupère les URLs des N articles mensuels les plus récents depuis la page hub."""
    html = polite_get(HUB_URL)
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    seen = set()

    for a in soup.find_all("a"):
        href = a.get("href", "") or ""
        text = a.get_text(strip=True)
        if not href.startswith(BASE) and not href.startswith("/"):
            continue
        if "/blu-ray-4k/" not in href and "/blu-ray-dvd/" not in href:
            continue
        if href.rstrip("/") == HUB_URL.rstrip("/"):
            continue
        # Les liens vers les articles mensuels contiennent typiquement
        # "la page sur les sorties ..." comme texte de lien
        if not text or "page sur les sorties" not in text.lower():
            continue
        full = href if href.startswith("http") else BASE + href
        if full not in seen:
            seen.add(full)
            urls.append(full)
        if len(urls) >= limit:
            break

    return urls


def _parse_month_article(html):
    soup = BeautifulSoup(html, "html.parser")
    releases = []

    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        if not text.lower().startswith("ici en"):
            continue
        href = a.get("href", "") or ""
        parent = a.parent
        if parent is None:
            continue
        full_text = parent.get_text(" ", strip=True)
        # Il arrive que le tag parent direct soit très large (contienne
        # plusieurs sorties) ; dans ce cas on retombe sur une reconstruction
        # locale : texte avant le lien (dans le même parent) + texte du lien
        # + un peu du texte qui suit, borné par "Sorti le ... ."
        m = ENTRY_RE.search(full_text)
        if not m:
            continue

        title = m.group("title").strip(" \u2013-*")
        formats = m.group("formats").strip()
        date_text = m.group("date").strip()

        # Le lien produit interne (fiche film) est plus utile que les liens
        # d'affiliation Amazon ; on garde ce qu'on a
        product_url = href if href.startswith("http") else (BASE + href if href.startswith("/") else href)

        releases.append(make_release(
            title=title,
            url=product_url or None,
            source=SOURCE_NAME,
            date_text=date_text,
            details=f"Disponible en {formats}",
            format_hint=formats,
        ))

    return releases


def get_releases(month_articles_limit=3):
    all_releases = []
    seen = set()

    try:
        month_urls = _get_month_article_urls(limit=month_articles_limit)
    except Exception:
        logger.exception("Impossible de récupérer la liste des articles mensuels")
        return []

    if not month_urls:
        logger.warning("Aucun article mensuel trouvé sur la page hub %s", HUB_URL)

    for url in month_urls:
        try:
            html = polite_get(url)
        except Exception:
            logger.exception("Échec du chargement de %s", url)
            continue
        for r in _parse_month_article(html):
            key = (r["title"], r["date_text"])
            if key not in seen:
                seen.add(key)
                all_releases.append(r)

    logger.info("[%s] %d sorties trouvées", SOURCE_NAME, len(all_releases))
    return all_releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in get_releases()[:30]:
        print(r["date_text"], "|", r["title"], "|", r["details"])
