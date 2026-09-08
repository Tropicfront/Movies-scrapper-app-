"""
Génère un flux iCalendar (.ics) à partir des sorties récupérées.

Le format iCal (RFC 5545) est le standard universel supporté nativement
par les widgets "Calendar" de Homepage et Homarr (intégration "ical"),
ainsi que par Google Calendar, Apple Calendar, Outlook, etc.

Notes d'implémentation :
- Les événements sont "journée entière" (VALUE=DATE, sans heure) et
  n'ont volontairement PAS de DTEND : selon la RFC 5545, un événement
  DATE sans DTEND ni DURATION dure implicitement 1 jour. Certains
  parseurs ICS plus permissifs affichent en revanche un DTEND explicite
  comme si l'événement débordait sur le jour suivant (ce qui pouvait
  ressembler à un "timer"/une durée dans certains dashboards) — on
  l'évite en s'appuyant sur la durée implicite.
- Chaque événement porte une propriété COLOR (RFC 7986, nom de couleur
  CSS3) qui dépend du format (4K / Blu-ray / DVD). Peu de clients
  l'exploitent, donc pour Homarr/Homepage, il vaut mieux utiliser les
  flux séparés /calendar-4k.ics et /calendar-bluray.ics, chacun ajouté
  comme une intégration différente avec sa propre couleur dans le
  dashboard (ces widgets colorent par flux, pas par événement).
"""

import hashlib
from datetime import date as date_cls, datetime, timezone

from icalendar import Calendar, Event

CALENDAR_NAME = "Sorties Blu-ray / DVD / 4K"

# Couleurs CSS3 (RFC 7986) par catégorie de format
CATEGORY_COLORS = {
    "4k": "blueviolet",
    "bluray": "dodgerblue",
    "dvd": "crimson",
    "autre": "gray",
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

        summary = r.get("title", "Sortie")
        if r.get("in_jellyfin"):
            summary = f"📀 {summary}"  # déjà dans la bibliothèque Jellyfin
        event.add("summary", summary)

        event.add("dtstart", start)   # date seule, sans dtend -> durée implicite de 1 jour (pas de "timer")
        event.add("dtstamp", now_utc)

        category = r.get("format_category", "autre")
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
            event.add("attach", r["poster_url"])  # affiche en pièce jointe (supporté par certains clients, ex. Apple Calendar)

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
