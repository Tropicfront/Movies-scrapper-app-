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

Le lien "ici en <formats>" pointe vers la fiche du film/série sur
edition-limitee.fr. C'est UNIQUEMENT sur cette fiche que se trouvent les
boutons d'achat Amazon/Fnac : l'article mensuel, lui, ne contient que les
titres et les formats. Il faut donc, comme pour 4k-ultra-hd.fr, une requête
HTTP supplémentaire par fiche — d'où le cache persistant de
enrich_with_affiliate_links, qui ne re-télécharge pas les fiches déjà vues.

L'URL de la fiche est conservée dans "film_page_url" (absente si l'article
ne pointait vers aucune fiche), tandis que "url" — celle du titre affiché —
retombe sur l'article mensuel dans ce cas.
"""

import re
import logging
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

from date_utils import extract_purchase_links, make_release, polite_get
from json_cache import load_json_cache, save_json_cache

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

# La reconnaissance des boutons d'achat est centralisée dans
# date_utils.classify_purchase_link (libellé du lien d'abord, URL en filet).

# Une fiche dont la visite n'a rien donné est retentée au bout de ce délai
RETRY_NOT_FOUND_AFTER_DAYS = 7
# Nombre maximum de fiches visitées par rafraîchissement : le site publie
# ~200 sorties, les visiter toutes d'un coup rendrait le premier
# rafraîchissement très long. Le reste est récupéré au rafraîchissement
# suivant, le cache conservant ce qui a déjà été trouvé.
MAX_LOOKUPS_PER_RUN = 150


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
    """Repère, à partir du bloc de l'entrée, le lien vers la fiche du film
    sur edition-limitee.fr, et — par filet, car en pratique l'article
    mensuel n'en contient pas — d'éventuels liens Amazon/Fnac déjà présents.

    On explore le bloc puis quelques éléments frères suivants, en s'arrêtant
    dès qu'on croise la ligne "ici en ..." de l'entrée suivante, pour ne pas
    lui voler ses propres liens."""
    amazon_url = None
    fnac_url = None
    host_url = None
    node = entry_block
    for i in range(max_following + 1):
        if node is None or not hasattr(node, "find_all"):
            break
        if i > 0 and node.find(string=re.compile(r"ici en\s", re.IGNORECASE)):
            break  # on a atteint l'entrée suivante, on s'arrête là
        amazon_url, fnac_url = extract_purchase_links(node, amazon_url, fnac_url)
        for link in node.find_all("a"):
            h = link.get("href", "") or ""
            if not h:
                continue
            if "edition-limitee.fr" in h or h.startswith("/"):
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
        film_page_url = host_url
        if not host_url:
            host_url = article_url

        release = make_release(
            title=title,
            url=host_url,
            source=SOURCE_NAME,
            date_text=date_text,
            details=f"Disponible en {formats}",
            format_hint=formats,
            amazon_url=amazon_url,
            fnac_url=fnac_url,
        )
        # URL de la fiche à visiter pour trouver les boutons d'achat. Vide si
        # l'entrée ne pointait vers aucune fiche : on ne visitera alors pas
        # l'article mensuel, qui ne contient pas de boutons et dont les liens
        # appartiendraient de toute façon à d'autres films.
        release["film_page_url"] = film_page_url
        releases.append(release)

    return releases


def _fetch_purchase_links_from_film_page(url):
    """Va chercher les boutons Amazon / Fnac sur la fiche d'un film ou d'une
    série. On scanne tous les liens de la page plutôt que de dépendre d'un
    conteneur CSS précis, le marchand étant reconnu via le libellé du lien
    ou de son logo."""
    try:
        html = polite_get(url)
    except Exception:
        logger.warning("Fiche inaccessible, liens d'achat ignorés : %s", url)
        return None, None
    soup = BeautifulSoup(html, "html.parser")
    return extract_purchase_links(soup)


def enrich_with_affiliate_links(releases, cache_file, request_delay=0.35,
                                max_lookups=MAX_LOOKUPS_PER_RUN):
    """Complète amazon_url/fnac_url pour les sorties Édition-Limitée.fr en
    visitant la fiche de chaque film (les boutons d'achat ne figurent pas
    dans l'article mensuel). Une requête HTTP par fiche manquante, mise en
    cache pour ne pas la refaire à chaque rafraîchissement."""
    cache = load_json_cache(cache_file)
    now = datetime.now(timezone.utc)
    changed = False
    lookups = 0

    for r in releases:
        if r.get("source") != SOURCE_NAME:
            continue
        if r.get("amazon_url") and r.get("fnac_url"):
            continue
        url = r.get("film_page_url")
        if not url:
            continue

        entry = cache.get(url)
        needs_lookup = entry is None
        # Une entrée qui n'a trouvé qu'un des deux liens (ou aucun) est
        # retentée passé le délai : le site complète parfois ses fiches.
        incomplete = bool(entry) and not (entry.get("amazon_url") and entry.get("fnac_url"))
        if entry and incomplete and entry.get("checked_at"):
            try:
                checked_at = datetime.fromisoformat(entry["checked_at"])
                if now - checked_at > timedelta(days=RETRY_NOT_FOUND_AFTER_DAYS):
                    needs_lookup = True
            except ValueError:
                needs_lookup = True

        if needs_lookup:
            if lookups >= max_lookups:
                continue  # la suite sera récupérée au prochain rafraîchissement
            amazon_url, fnac_url = _fetch_purchase_links_from_film_page(url)
            cache[url] = {
                "amazon_url": amazon_url,
                "fnac_url": fnac_url,
                "found": bool(amazon_url or fnac_url),
                "checked_at": now.isoformat(),
            }
            changed = True
            lookups += 1
            time.sleep(request_delay)
            entry = cache[url]

        if entry.get("amazon_url") and not r.get("amazon_url"):
            r["amazon_url"] = entry["amazon_url"]
        if entry.get("fnac_url") and not r.get("fnac_url"):
            r["fnac_url"] = entry["fnac_url"]

    if changed:
        save_json_cache(cache_file, cache)

    concerned = [r for r in releases if r.get("source") == SOURCE_NAME]
    matched = sum(1 for r in concerned if r.get("amazon_url") or r.get("fnac_url"))
    logger.info(
        "[%s] Liens d'achat : %d/%d sorties pourvues (%d fiches visitées ce tour)",
        SOURCE_NAME, matched, len(concerned), lookups,
    )
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
