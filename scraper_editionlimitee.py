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

Le lien "ici en <formats>" pointe parfois vers la fiche du site
edition-limitee.fr, parfois directement vers un lien affilié (Amazon,
Fnac via awin1.com). On distingue les deux : l'URL principale ("url",
utilisée pour le titre) pointe toujours vers edition-limitee.fr — soit la
fiche dédiée si elle existe, soit à défaut l'article mensuel lui-même —
tandis que les liens affiliés Amazon/Fnac repérés dans le même bloc sont
conservés séparément pour être proposés comme boutons d'achat.
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

# Liens affiliés : amzn.to (Amazon, commun aux deux sites source) et
# awin1.com (réseau d'affiliation utilisé par ce site pour ses liens Fnac).
AMAZON_LINK_RE = re.compile(r"amzn\.to", re.IGNORECASE)
FNAC_LINK_RE = re.compile(r"awin1\.com", re.IGNORECASE)


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


def _find_purchase_links(entry_block, max_following=3):
    """Cherche les liens Amazon/Fnac à partir du bloc de l'entrée, puis dans
    les quelques éléments frères suivants : sur edition-limitee.fr, ces
    liens ("ici sur Amazon", "ici sur la fnac") sont souvent dans un
    paragraphe de description séparé, juste après la ligne "Titre ici en
    Formats. Sorti le Date.", et non dans le même bloc qu'elle. On arrête
    dès qu'on croise la ligne "ici en ..." de l'entrée suivante, pour ne
    pas lui voler ses propres liens."""
    amazon_url = None
    fnac_url = None
    host_url = None
    node = entry_block
    for i in range(max_following + 1):
        if node is None or not hasattr(node, "find_all"):
            break
        if i > 0 and node.find(string=re.compile(r"ici en\s", re.IGNORECASE)):
            break  # on a atteint l'entrée suivante, on s'arrête là
        for link in node.find_all("a"):
            h = link.get("href", "") or ""
            if not h:
                continue
            if AMAZON_LINK_RE.search(h):
                amazon_url = amazon_url or h
            elif FNAC_LINK_RE.search(h):
                fnac_url = fnac_url or h
            elif "edition-limitee.fr" in h or h.startswith("/"):
                host_url = host_url or (h if h.startswith("http") else BASE + h)
        node = node.find_next_sibling()
    return host_url, amazon_url, fnac_url


def _parse_month_article(html, article_url):
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

        entry_block = a.find_parent(["p", "li", "div"]) or parent
        host_url, amazon_url, fnac_url = _find_purchase_links(entry_block)

        # Si aucun lien vers le site hôte n'a été trouvé (le lien "ici en
        # ..." pointe directement vers un site affilié), on retombe sur
        # l'article mensuel lui-même : ça reste un lien vers
        # edition-limitee.fr, contrairement à un lien affilié.
        if not host_url:
            host_url = article_url

        releases.append(make_release(
            title=title,
            url=host_url,
            source=SOURCE_NAME,
            date_text=date_text,
            details=f"Disponible en {formats}",
            format_hint=formats,
            amazon_url=amazon_url,
            fnac_url=fnac_url,
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
        for r in _parse_month_article(html, url):
            key = (r["title"], r["date_text"])
            if key not in seen:
                seen.add(key)
                all_releases.append(r)

    logger.info("[%s] %d sorties trouvées", SOURCE_NAME, len(all_releases))
    return all_releases


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in get_releases()[:30]:
        print(r["date_text"], "|", r["title"], "|", r["details"], "| amazon:", r["amazon_url"], "| fnac:", r["fnac_url"])
