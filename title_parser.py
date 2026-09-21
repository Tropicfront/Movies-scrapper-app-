"""
Analyse des titres de sorties tels que les écrivent les sites source, pour
en tirer deux choses :

0. **Les étiquettes**, inutiles à TMDB mais utiles à l'affichage : format
   (4K / Blu-ray / DVD), steelbook, exclusivité Fnac, coffret. Elles sont
   retirées de la requête TMDB et conservées à part. « Steelcase » est une
   autre façon d'écrire « steelbook » et compte comme telle ; « Fnac » dans
   un titre signale une édition exclusive à cette enseigne.

0 bis. **L'année**, quand le titre en porte une (« Scary Movie 2026 4K »,
   « Running Man 1987 4K Steelbook »). Elle n'est pas envoyée dans le texte
   de la requête — TMDB la chercherait dans le titre et ne trouverait rien —
   mais sert à départager les résultats, ce qui est décisif pour les remakes
   et les titres très courants.

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
    r"steel\s*-?\s*book|steel\s*-?\s*case|combo|digipack|mediabook|digibook|"
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

# --- Étiquettes d'édition, retirées de la requête TMDB et conservées à part.
# « Steelcase » est une graphie fréquente de « steelbook ».
_STEELBOOK_RE = re.compile(r"steel\s*-?\s*(?:book|case)|bo[iî]tier m[ée]tal", re.IGNORECASE)
# « Fnac » dans un titre désigne une édition exclusive à l'enseigne.
_FNAC_RE = re.compile(r"\bfnac\b", re.IGNORECASE)

# --- Année d'édition ou de sortie présente dans le titre.
# Bornée à 20[0-2]\d pour ne pas confondre avec un titre comme
# « Blade Runner 2049 », et ignorée quand elle ouvre le titre (« 1917 »,
# « 2001 : L'Odyssée de l'espace »), où elle EST le titre.
_YEAR_RE = re.compile(r"(?<=\S)\s+\(?((?:19[2-9]\d)|(?:20[0-2]\d))\)?(?=\s|$)")

# --- Coffret sans séparateur explicite : « Monte-Cristo Les Trois
# Mousquetaires ». On tente une coupure devant un déterminant français
# capitalisé rencontré en cours de titre. Uniquement pour les titres déjà
# reconnus comme coffrets, et seulement en candidats supplémentaires : le
# titre entier reste essayé en premier.
_IMPLICIT_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=(?:Les|Le|La|L['’]|Un|Une|Des)\s*\S)")

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
    text = _FNAC_RE.sub(" ", text)
    text = _DESCRIPTOR_RE.sub(" ", text)
    text = _BOXSET_WORD_RE.sub(" ", text)
    text = _strip_year(text)[0]
    return _squeeze(text)


def _strip_year(text):
    """Retire l'année du titre et la renvoie. Rien n'est retiré si l'année
    ouvre le titre (c'est alors le titre lui-même) ou si le titre n'est
    constitué que d'elle."""
    match = _YEAR_RE.search(text)
    if not match:
        return text, None
    remainder = _squeeze(text[: match.start()] + " " + text[match.end():])
    if not remainder:
        return text, None
    return remainder, int(match.group(1))


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
    """Analyse un titre de sortie.

    Retourne : is_boxset, is_steelbook, is_fnac_exclusive, year,
    search_titles (ordonnés, sans étiquette ni année), base_title, tags.
    """
    raw = (title or "").translate(_DASHES)
    if not raw.strip():
        return {"is_boxset": False, "is_steelbook": False,
                "is_fnac_exclusive": False, "year": None,
                "search_titles": [], "base_title": "", "tags": []}

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

    # Coffret dont les films sont collés sans séparateur : on propose en
    # plus chaque morceau, après le titre entier.
    if is_boxset and len(parts) == 1 and search_titles:
        pieces = [_squeeze(p) for p in _IMPLICIT_SPLIT_RE.split(search_titles[0])]
        if len(pieces) > 1:
            for piece in pieces:
                if piece and piece not in search_titles:
                    search_titles.append(piece)

    # Dernier recours : le titre nettoyé de son seul format, au cas où le
    # nettoyage aurait été trop agressif sur un titre atypique.
    fallback = _strip_noise(raw) or without_format or raw.strip()
    if fallback and fallback not in search_titles:
        search_titles.append(fallback)

    is_steelbook = bool(_STEELBOOK_RE.search(haystack))
    # L'exclusivité Fnac n'est cherchée que dans le TITRE : le descriptif
    # mentionne parfois l'enseigne comme simple point de vente.
    is_fnac = bool(_FNAC_RE.search(raw))
    _, year = _strip_year(_squeeze(_FORMAT_RE.sub(" ", raw)))

    tags = []
    if is_boxset:
        tags.append("coffret")
    if is_steelbook:
        tags.append("steelbook")
    if is_fnac:
        tags.append("fnac")

    return {
        "is_boxset": is_boxset,
        "is_steelbook": is_steelbook,
        "is_fnac_exclusive": is_fnac,
        "year": year,
        "search_titles": search_titles,
        "base_title": search_titles[0] if search_titles else raw.strip(),
        "tags": tags,
    }


def is_boxset(title, details=""):
    return analyze_title(title, details)["is_boxset"]


def is_steelbook(title, details=""):
    return analyze_title(title, details)["is_steelbook"]


def is_fnac_exclusive(title, details=""):
    return analyze_title(title, details)["is_fnac_exclusive"]


if __name__ == "__main__":
    # (titre, détails, coffret, année, étiquettes, candidats attendus —
    #  le premier doit être exactement celui indiqué, les autres présents)
    CASES = [
        ("Coffret The Eye 1 et 2 4K", "Coffret 2 films 2× 4K UHD", True, None, ["coffret"],
         ["The Eye", "The Eye 1", "The Eye 2"]),
        ("Mortal Kombat 1 et 2 4k", "", True, None, ["coffret"],
         ["Mortal Kombat", "Mortal Kombat 1", "Mortal Kombat 2"]),
        ("John Wayne : Rio Bravo + La Prisonnière du désert", "", True, None, ["coffret"],
         ["Rio Bravo", "La Prisonnière du désert"]),
        ("Freddy – L'intégrale 1 à 7", "", True, None, ["coffret"], ["Freddy"]),
        ("Bleach : Thousand-Year Blood War - Partie 3", "", False, None, [],
         ["Bleach : Thousand-Year Blood War", "Bleach"]),
        ("Star Trek : La série Originale", "", False, None, [], ["Star Trek"]),

        # --- année dans le titre : retirée de la requête, gardée pour trier
        ("Scary Movie 2026 4K", "", False, 2026, [], ["Scary Movie"]),
        ("Running Man 1987 4K Steelbook", "", False, 1987, ["steelbook"], ["Running Man"]),
        ("Godzilla 1954", "", False, 1954, [], ["Godzilla"]),

        # --- étiquettes d'édition
        ("Obsession 4K Steelcase", "", False, None, ["steelbook"], ["Obsession"]),
        ("Obsession 4K Steelcase Fnac", "", False, None, ["steelbook", "fnac"], ["Obsession"]),
        ("Inside Llewyn Davis 4K Fnac", "", False, None, ["fnac"], ["Inside Llewyn Davis"]),

        # --- coffret sans séparateur : morceaux proposés après le titre entier
        ("Coffret Monte-Cristo Les Trois Mousquetaires 4k", "", True, None, ["coffret"],
         ["Monte-Cristo Les Trois Mousquetaires", "Monte-Cristo", "Les Trois Mousquetaires"]),

        # --- non-régression : ne rien abîmer
        ("Marama", "Disponible en Blu-ray et DVD", False, None, [], ["Marama"]),
        ("City On Fire", "", False, None, [], ["City On Fire"]),
        ("Ghost in the Shell 4K Steelbook", "", False, None, ["steelbook"],
         ["Ghost in the Shell"]),
        ("Sailor Suit and the Machine Gun 4K", "", False, None, [],
         ["Sailor Suit and the Machine Gun"]),
        # une année qui EST le titre ne doit pas être retirée
        ("1917", "", False, None, [], ["1917"]),
        ("2001 : L'Odyssée de l'espace", "", False, None, [],
         ["2001 : L'Odyssée de l'espace"]),
        # 2049 est hors de la plage des années d'édition plausibles
        ("Blade Runner 2049", "", False, None, [], ["Blade Runner 2049"]),
        ("Fast & Furious", "", False, None, [], ["Fast & Furious"]),
        # « la » et « le » en minuscules ne déclenchent pas de coupure
        ("Le Bon, la Brute et le Truand", "", False, None, [],
         ["Le Bon, la Brute et le Truand"]),
        ("Blue Exorcist - Saison 4", "", False, None, [], ["Blue Exorcist"]),
        ("Trilogie Le Parrain", "", True, None, ["coffret"], ["Le Parrain"]),
    ]

    failures = 0
    for title, details, expect_box, expect_year, expect_tags, expect_head in CASES:
        result = analyze_title(title, details)
        got = result["search_titles"]
        checks = {
            "coffret": result["is_boxset"] == expect_box,
            "année": result["year"] == expect_year,
            "étiquettes": sorted(result["tags"]) == sorted(expect_tags),
            "candidats": bool(got) and got[0] == expect_head[0]
                         and all(t in got for t in expect_head),
        }
        bad = [name for name, ok in checks.items() if not ok]
        if bad:
            failures += 1
            print(f"ÉCHEC {title!r} -> {', '.join(bad)}")
            print(f"      coffret={result['is_boxset']} année={result['year']} "
                  f"étiquettes={result['tags']}\n      candidats={got}")
        else:
            print(f"ok    {title!r}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} cas conformes")
