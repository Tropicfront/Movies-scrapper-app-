"""
Génère un flux iCalendar (.ics) à partir des sorties récupérées.

Le format iCal (RFC 5545) est le standard universel supporté nativement
par les widgets "Calendar" de Homepage et Homarr (intégration "ical"),
ainsi que par Google Calendar, Apple Calendar, Outlook, etc.

Notes d'implémentation (événements "journée entière") :
- DTSTART et DTEND sont tous les deux des dates seules (VALUE=DATE, sans
  heure), avec DTEND = jour suivant. C'est la forme EXACTE utilisée par
  Google Calendar / Apple Calendar / Outlook pour un événement d'un jour.
  Un essai précédent omettait le DTEND (légal selon la RFC : durée
  implicite d'1 jour), mais plusieurs parseurs moins stricts (dont celui
  utilisé par Homarr) ne gèrent pas bien cette durée implicite et
  affichent alors une heure de début/fin calculée à partir d'UTC et
  reconvertie dans le fuseau du navigateur — d'où l'horaire du type
  "02:00 - 23:59" qui n'a aucun sens et n'est pas un vrai horaire de
  sortie. Le DTEND explicite règle ce problème dans la quasi-totalité des
  clients.
- On ajoute aussi TRANSP:TRANSPARENT et X-MICROSOFT-CDO-ALLDAYEVENT:TRUE,
  deux marqueurs (le second non-standard mais largement reconnu, issu
  d'Outlook) qui aident certains parseurs à identifier explicitement
  l'événement comme "journée entière" plutôt que de tenter de lui
  calculer un horaire.
- Chaque événement porte quand même une propriété COLOR (RFC 7986) et un
  emoji dans le titre selon le format (4K / Blu-ray / DVD), utile pour
  les clients qui les exploitent. Mais la plupart des dashboards (Homarr,
  Homepage) colorent par flux entier et pas par événement : preferer un
  flux unique (build_ics) trié par date plutôt que les flux scindés si
  tu veux que 4K et Blu-ray apparaissent ensemble au bon jour.
"""

import hashlib
from datetime import date as date_cls, datetime, timedelta, timezone

from icalendar import Calendar, Event

CALENDAR_NAME = "Sorties Blu-ray / DVD / 4K"

# Couleurs CSS3 (RFC 7986) et emoji par catégorie de format
CATEGORY_COLORS = {
    "4k": "blueviolet",
    "bluray": "dodgerblue",
    "dvd": "crimson",
    "autre": "gray",
}
CATEGORY_EMOJI = {
    "4k": "🟣",
    "bluray": "🔵",
    "dvd": "🔴",
    "autre": "",
}
CATEGORY_LABELS_FALLBACK = {
    "4k": "4K Ultra HD",
    "bluray": "Blu-ray",
    "dvd": "DVD",
    "autre": "Autre",
}


def _make_uid(release):
    raw = f"{release.get('title', '')}|{release.get('date_iso', '')}|{release.get('source', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() + "@sorties-films"


def build_ics(releases):
    """Construit un calendrier iCal à partir d'une liste de releases (dict).
    Seules les releases avec une date_iso valide produisent un événement
    (les sorties sans date précise n'ont pas leur place dans un calendrier)."""
    cal = Calendar()
    cal.add("prodid", "-//Sorties Films//sorties-films//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", CALENDAR_NAME)
    cal.add("x-wr-timezone", "Europe/Paris")

    now_utc = datetime.now(timezone.utc)

    for r in releases:
        date_iso = r.get("date_iso")
        if not date_iso:
            continue
        try:
            y, m, d = (int(part) for part in date_iso.split("-"))
            start = date_cls(y, m, d)
        except (ValueError, TypeError):
            continue

        event = Event()
        event.add("uid", _make_uid(r))

        category = r.get("format_category", "autre")
        emoji = CATEGORY_EMOJI.get(category, "")

        summary = r.get("title", "Sortie")
        if r.get("in_jellyfin"):
            summary = f"📀 {summary}"  # déjà dans la bibliothèque Jellyfin
        if emoji:
            summary = f"{emoji} {summary}"
        event.add("summary", summary)

        # Événement "journée entière" standard : DTSTART + DTEND (exclusif,
        # jour suivant), tous deux en VALUE=DATE -> aucune heure affichable.
        event.add("dtstart", start)
        event.add("dtend", start + timedelta(days=1))
        event.add("dtstamp", now_utc)
        event.add("transp", "TRANSPARENT")
        event.add("x-microsoft-cdo-alldayevent", "TRUE")
        event.add("x-microsoft-cdo-busystatus", "FREE")

        event.add("color", CATEGORY_COLORS.get(category, CATEGORY_COLORS["autre"]))

        description_parts = []
        if r.get("details"):
            description_parts.append(r["details"])
        if r.get("source"):
            description_parts.append(f"Source : {r['source']}")
        if r.get("in_jellyfin"):
            description_parts.append("Déjà présent dans ta bibliothèque Jellyfin")
        if r.get("poster_page_url"):
            description_parts.append(f"Fiche : {r['poster_page_url']}")
        if description_parts:
            event.add("description", "\n".join(description_parts))

        # L'URL de l'événement pointe vers la fiche du film (poster) si on
        # l'a trouvée, sinon vers la page du site source
        event.add("url", r.get("poster_page_url") or r.get("url", ""))

        if r.get("poster_url"):
            event.add("attach", r["poster_url"])  # rarement rendu comme vignette par les clients calendrier, ajouté à titre indicatif

        categories = [c for c in (r.get("source"), CATEGORY_LABELS_FALLBACK.get(category)) if c]
        if categories:
            event.add("categories", categories)

        cal.add_component(event)

    return cal.to_ical()


if __name__ == "__main__":
    sample = [
        {
            "title": "Ghost in the Shell 4K Steelbook",
            "date_iso": "2026-07-22",
            "details": "Steelbook 4K UHD + 2x Blu-ray (1995)",
            "format_category": "4k",
            "source": "4K-Ultra-HD.fr",
            "url": "https://4k-ultra-hd.fr/film/ghost-in-the-shell-4k-steelbook",
            "poster_url": "https://image.tmdb.org/t/p/w342/fake.jpg",
            "poster_page_url": "https://www.themoviedb.org/movie/9323",
            "in_jellyfin": True,
        }
    ]
    print(build_ics(sample).decode("utf-8"))
