"""Utilitaires partagés : parsing de dates en français, requêtes HTTP polies,
normalisation de titres, classification de format (4K / Blu-ray / DVD)."""

import re
import time
import logging
import unicodedata
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

# Forme canonique (pour l'affichage), utilisée quel que soit le site source
MOIS_FR_NAMES = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# Ex: "22 juillet 2026", "1er Juillet 2026", "4 Aout 2026", "1 août 2026"
DATE_FULL_RE = re.compile(
    r"(\d{1,2})\s*(?:er)?\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})",
    re.IGNORECASE,
)

# Mots à retirer pour comparer deux titres (éditions/formats/mentions qui
# varient d'un site à l'autre mais ne changent pas le film/la série)
_JUNK_WORDS = [
    "edition collector", "édition collector", "collector",
    "boitier steelbook", "boîtier steelbook", "steelbook",
    "4k ultra hd", "ultra hd", "4k uhd", "4k",
    "blu-ray", "bluray", "dvd",
    "combo", "coffret", "limite", "limitee", "limitée", "limité",
    "version longue", "director's cut", "sortie",
]

CATEGORY_LABELS = {
    "4k": "4K Ultra HD",
    "bluray": "Blu-ray",
    "dvd": "DVD",
    "autre": "Autre",
}


def format_date_label(date_iso):
    """Formate une date ISO ('2026-09-09') en libellé français canonique
    ('9 septembre 2026'). Utilisé pour afficher/regrouper les sorties de
    façon uniforme, quel que soit le formatage d'origine du site source
    (ex: 4k-ultra-hd.fr écrit '9 septembre 2026', edition-limitee.fr écrit
    '9 Septembre 2026' avec une majuscule — sans cette normalisation, les
    deux formulations créent deux sections séparées pour le même jour)."""
    if not date_iso:
        return None
    try:
        y, m, d = (int(part) for part in date_iso.split("-"))
        return f"{d} {MOIS_FR_NAMES[m - 1]} {y}"
    except (ValueError, IndexError):
        return None


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


def normalize_title(title):
    """Normalise un titre pour comparaison (dédoublonnage entre sources,
    correspondance avec la bibliothèque Jellyfin) : minuscules, sans accents,
    sans mentions d'édition/format, sans ponctuation."""
    if not title:
        return ""
    t = title.lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\[[^\]]*\]", " ", t)   # retire [Blu-ray], [4K Ultra HD - Steelbook]...
    t = re.sub(r"\([^)]*\)", " ", t)    # retire (1995), (Amores perros)...
    for junk in _JUNK_WORDS:
        t = t.replace(junk, " ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def classify_format(details="", format_hint=""):
    """Classe une sortie en '4k', 'bluray', 'dvd' ou 'autre' à partir du
    texte de détail/format scrapé. Utilisé pour l'affichage (badge coloré)
    et pour scinder le flux calendrier en deux (4K / Blu-ray-DVD)."""
    text = f"{details} {format_hint}".lower()
    if "4k" in text:
        return "4k"
    if "blu-ray" in text or "bluray" in text:
        return "bluray"
    if "dvd" in text:
        return "dvd"
    return "autre"


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
    # Une fois la date extraite, on affiche un libellé canonique plutôt que
    # le texte brut du site (qui varie en casse/formulation d'un site à
    # l'autre) : ça évite que "9 Septembre 2026" et "9 septembre 2026"
    # soient traités comme deux dates différentes à l'affichage.
    display_date = format_date_label(date_iso) if date_iso else ((date_text or "").strip() or "Date à préciser")
    return {
        "title": title.strip(),
        "url": url,
        "source": source,
        "date_text": display_date,
        "date_iso": date_iso,
        "details": details.strip(),
        "format": format_hint.strip(),
        "format_category": classify_format(details, format_hint),
    }
