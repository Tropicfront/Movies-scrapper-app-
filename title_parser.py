"""
Analyse des titres de sorties tels que les écrivent les sites source, pour
en tirer deux choses :

1. **Est-ce un coffret ?** L'information n'est jamais dans un champ dédié :
   elle est dans la formulation du titre. Trois signaux, cumulables :
     - un mot-clé : « Coffret », « L'intégrale », « Collection », « Trilogie »…
     - une énumération ou une plage de numéros en fin de titre : « 1 et 2 »,
       « 1 à 7 » — plusieurs numéros = plusieurs films dans la boîte ;
     - plusieurs titres réunis par « + » : « Rio Bravo + La Prisonnière du
       désert ».

2. **Quoi demander à TMDB.** Un titre de coffret n'existe pas dans TMDB :
   « Coffret The Eye 1 et 2 4K » n'y donnera jamais rien, alors que « The
   Eye » oui. On produit donc une liste de titres candidats, du plus
   probable au moins probable, que poster_lookup essaie dans l'ordre
   jusqu'à trouver une affiche.

Exemples (voir les tests en bas de fichier) :

    Coffret The Eye 1 et 2 4K            -> coffret, ["The Eye", "The Eye 2", …]
    Mortal Kombat 1 et 2 4k              -> coffret, ["Mortal Kombat", "Mortal Kombat 2", …]
    John Wayne : Rio Bravo + La Prisonnière du désert
                                         -> coffret, ["Rio Bravo", "La Prisonnière du désert", …]
    Freddy - L'intégrale 1 à 7           -> coffret, ["Freddy"]
    Bleach : Thousand-Year Blood War - Partie 3
                                         -> pas un coffret, ["Bleach : Thousand-Year Blood War", "Bleach"]
    Star Trek : La série Originale       -> pas un coffret, ["Star Trek"]
"""

import re
import unicodedata

# --- Tirets et séparateurs de toutes sortes ramenés à un tiret simple
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")

# --- Mentions de format/édition : jamais dans le titre d'un film
_FORMAT_RE = re.compile(
    r"\b(4k ultra hd|ultra hd|4k uhd|uhd 4k|4k|uhd|blu-?\s?ray|bd|dvd|"
    r"steel\s*-?\s*book|combo|digipack|mediabook|digibook|"
    r"bo[iî]tier m[ée]tal|[ée]dition (?:limit[ée]e|collector|sp[ée]ciale|prestige)|"
    r"collector|limit[ée]e?|remasteris[ée]e?|version longue|vostfr|vf)\b",
    re.IGNORECASE,
)

# --- Mots-clés de coffret
_BOXSET_WORD_RE = re.compile(
    r"\b(coffret|int[ée]grale|collection|compilation|anthologie|saga|"
    r"duologie|trilogie|t[ée]tralogie|quadrilogie|pentalogie|hexalogie|"
    r"box\s?set|\d+\s*films?)\b",
    re.IGNORECASE,
)

# --- Descriptifs d'édition à retirer : ce qui reste est le vrai titre.
# C'est ce qui distingue « Star Trek : La série Originale » (descriptif, le
# titre est « Star Trek ») de « Bleach : Thousand-Year Blood War » (vrai
# sous-titre, à garder).
_DESCRIPTOR_RE = re.compile(
    r"\b(l['’]\s*int[ée]grale(?:\s+de\s+la\s+s[ée]rie)?|int[ée]grale|"
    r"la\s+s[ée]rie\s+(?:originale|compl[èe]te)|s[ée]rie\s+(?:originale|compl[èe]te)|"
    r"partie\s+\d+|part\s+\d+|chapitre\s+\d+|"
    r"saisons?\s+\d+(?:\s*(?:[àa]|-|et)\s*\d+)?|"
    r"vol\.?\s*\d+|volume\s+\d+|"
    r"films?\s+\d+\s*(?:[àa]|-)\s*\d+|"
    r"[ée]dition\s+\d+\s*ans|\d+\s*[eè]me?\s+anniversaire)\b",
    re.IGNORECASE,
)

# --- Énumération / plage de numéros en fin de titre : « 1 et 2 », « 1 à 7 ».
# Ancrée sur la fin pour ne pas se déclencher sur « Blade Runner 2049 ».
_RANGE_RE = re.compile(
    r"\s*(\d{1,2})\s*(?:et|[àa]|-|,|\+|&)\s*(\d{1,2})\s*$",
    re.IGNORECASE,
)

# --- Titres réunis dans une même boîte. Séparateurs entourés d'espaces
# obligatoires, pour ne pas découper « Fast & Furious » ou « Face/Off ».
_MULTI_SPLIT_RE = re.compile(r"\s+(?:\+|/)\s+")


def _squeeze(text):
    """Nettoie les espaces et la ponctuation résiduelle en bord de chaîne,
    y compris un article élidé resté seul après un retrait (« Freddy - L' »)."""
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s[ldnmts]['’]\s*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*([:,-])\s*$", "", text)
    text = re.sub(r"^\s*([:,-])\s*", "", text)
    return text.strip(" \t-–:,;.&+/")


def _strip_noise(text):
    """Retire les mentions de format, de coffret et les descriptifs
    d'édition, sans toucher au reste du titre.

    L'ordre compte : les descriptifs d'abord, car « L'intégrale » contient
    « intégrale », qui est aussi un mot-clé de coffret — l'inverse
    laisserait traîner un « L' » orphelin."""
    text = _FORMAT_RE.sub(" ", text)
    text = _DESCRIPTOR_RE.sub(" ", text)
    text = _BOXSET_WORD_RE.sub(" ", text)
    return _squeeze(text)


def _candidates_from_part(part, drop_prefix):
    """Titres candidats pour un morceau (un film) du titre d'origine.

    `drop_prefix` est vrai quand le titre réunit plusieurs films : le
    préfixe avant « : » est alors le nom du coffret et pas celui du film
    (« John Wayne : Rio Bravo » -> « Rio Bravo »).
    """
    cleaned = _strip_noise(part)
    if not cleaned:
        return []

    if drop_prefix and ":" in cleaned:
        cleaned = _squeeze(cleaned.split(":")[-1])

    candidates = []
    match = _RANGE_RE.search(cleaned)
    if match:
        # « The Eye 1 et 2 » : le premier film porte souvent le titre nu,
        # les suivants un numéro. On propose les deux formes.
        base = _squeeze(cleaned[: match.start()])
        if base:
            first, last = int(match.group(1)), int(match.group(2))
            candidates.append(base)
            for n in range(first, min(last, first + 4) + 1):
                candidates.append(f"{base} {n}")
    else:
        candidates.append(cleaned)
        # « Bleach : Thousand-Year Blood War » d'abord, « Bleach » en
        # repli si TMDB ne connaît pas le sous-titre.
        if ":" in cleaned:
            prefix = _squeeze(cleaned.split(":")[0])
            if prefix and prefix.lower() != cleaned.lower():
                candidates.append(prefix)

    return candidates


def analyze_title(title, details=""):
    """Retourne un dict : is_boxset, search_titles (ordonnés), base_title."""
    raw = (title or "").translate(_DASHES)
    if not raw.strip():
        return {"is_boxset": False, "search_titles": [], "base_title": ""}

    haystack = f"{raw} {details or ''}".translate(_DASHES)

    # Le titre débarrassé de son format sert à chercher une plage de
    # numéros : « Mortal Kombat 1 et 2 4k » -> « Mortal Kombat 1 et 2 ».
    without_format = _squeeze(_FORMAT_RE.sub(" ", raw))

    parts = _MULTI_SPLIT_RE.split(without_format)
    is_boxset = bool(
        _BOXSET_WORD_RE.search(haystack)
        or _RANGE_RE.search(_squeeze(_DESCRIPTOR_RE.sub(" ", without_format)))
        or len(parts) > 1
    )

    search_titles = []
    for part in parts:
        for candidate in _candidates_from_part(part, drop_prefix=len(parts) > 1):
            if candidate not in search_titles:
                search_titles.append(candidate)

    # Dernier recours : le titre nettoyé de son seul format, au cas où le
    # nettoyage aurait été trop agressif sur un titre atypique.
    fallback = _strip_noise(raw) or without_format or raw.strip()
    if fallback and fallback not in search_titles:
        search_titles.append(fallback)

    return {
        "is_boxset": is_boxset,
        "search_titles": search_titles,
        "base_title": search_titles[0] if search_titles else raw.strip(),
    }


def is_boxset(title, details=""):
    return analyze_title(title, details)["is_boxset"]


if __name__ == "__main__":
    CASES = [
        # (titre, détails, coffret attendu, premiers candidats attendus)
        ("Coffret The Eye 1 et 2 4K", "Coffret 2 films 2× 4K UHD", True,
         ["The Eye", "The Eye 1", "The Eye 2"]),
        ("Mortal Kombat 1 et 2 4k", "", True,
         ["Mortal Kombat", "Mortal Kombat 1", "Mortal Kombat 2"]),
        ("John Wayne : Rio Bravo + La Prisonnière du désert", "", True,
         ["Rio Bravo", "La Prisonnière du désert"]),
        ("Freddy – L'intégrale 1 à 7", "", True, ["Freddy"]),
        ("Bleach : Thousand-Year Blood War - Partie 3", "", False,
         ["Bleach : Thousand-Year Blood War", "Bleach"]),
        ("Star Trek : La série Originale", "", False, ["Star Trek"]),
        # non-régression : titres simples, à ne pas abîmer
        ("Marama", "Disponible en Blu-ray et DVD", False, ["Marama"]),
        ("City On Fire", "", False, ["City On Fire"]),
        ("Ghost in the Shell 4K Steelbook", "", False, ["Ghost in the Shell"]),
        ("Blade Runner 2049", "", False, ["Blade Runner 2049"]),
        ("Fast & Furious", "", False, ["Fast & Furious"]),
        ("Le Bon, la Brute et le Truand", "", False, ["Le Bon, la Brute et le Truand"]),
        ("Blue Exorcist - Saison 4", "", False, ["Blue Exorcist"]),
        ("Trilogie Le Parrain", "", True, ["Le Parrain"]),
    ]

    failures = 0
    for title, details, expect_box, expect_head in CASES:
        result = analyze_title(title, details)
        ok_box = result["is_boxset"] == expect_box
        got = result["search_titles"]
        ok_titles = all(t in got for t in expect_head) and got[0] == expect_head[0]
        flag = "ok  " if (ok_box and ok_titles) else "ÉCHEC"
        if flag != "ok  ":
            failures += 1
        print(f"{flag} {title!r}\n      coffret={result['is_boxset']} "
              f"(attendu {expect_box})\n      candidats={got}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} cas conformes")
