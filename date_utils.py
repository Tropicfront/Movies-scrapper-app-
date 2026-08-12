"""Utilitaires partagés : parsing de dates en français, requêtes HTTP polies."""

import re
import time
import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

MOIS_FR = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12, "décembre": 12,
}

# Ex: "22 juillet 2026", "1er Juillet 2026", "4 Aout 2026", "1 août 2026"
DATE_FULL_RE = re.compile(
    r"(\d{1,2})\s*(?:er)?\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})",
    re.IGNORECASE,
)


def parse_french_date(text):
    """Essaie d'extraire une date complète (jour + mois + année) d'un texte français.
    Retourne un objet date ou None si le texte ne contient pas de date précise
    (ex: '(prochainement)', '3e trimestre 2026', 'T4 2026')."""
    if not text:
        return None
    m = DATE_FULL_RE.search(text)
    if not m:
        return None
    day, month_name, year = m.groups()
    month = MOIS_FR.get(month_name.lower())
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day)).date()
    except ValueError:
        return None


def polite_get(url, timeout=20, retries=2, delay=0.5):
    """GET avec retries légers et pause entre les tentatives."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("Échec requête %s (essai %d/%d) : %s", url, attempt + 1, retries + 1, exc)
            if attempt < retries:
                time.sleep(delay)
    raise last_exc


def make_release(title, url, source, date_text=None, details="", format_hint=""):
    date_iso = None
    d = parse_french_date(date_text or "")
    if d:
        date_iso = d.isoformat()
    return {
        "title": title.strip(),
        "url": url,
        "source": source,
        "date_text": (date_text or "").strip() or "Date à préciser",
        "date_iso": date_iso,
        "details": details.strip(),
        "format": format_hint.strip(),
    }
