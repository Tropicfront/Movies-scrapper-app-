"""
Génère un flux iCalendar (.ics) à partir des sorties récupérées.

Le format iCal (RFC 5545) est le standard universel supporté nativement
par les widgets "Calendar" de Homepage et Homarr (intégration "ical"),
ainsi que par Google Calendar, Apple Calendar, Outlook, etc.
"""

import hashlib
from datetime import date as date_cls, datetime, timedelta, timezone

from icalendar import Calendar, Event

CALENDAR_NAME = "Sorties Blu-ray / DVD / 4K"


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

        event.add("dtstart", start)                 # date seule -> événement "journée entière"
        event.add("dtend", start + timedelta(days=1))
        event.add("dtstamp", now_utc)

        description_parts = []
        if r.get("details"):
            description_parts.append(r["details"])
        if r.get("source"):
            description_parts.append(f"Source : {r['source']}")
        if r.get("in_jellyfin"):
            description_parts.append("Déjà présent dans ta bibliothèque Jellyfin")
        if description_parts:
            event.add("description", "\n".join(description_parts))

        if r.get("url"):
            event.add("url", r["url"])

        if r.get("source"):
            event.add("categories", [r["source"]])

        cal.add_component(event)

    return cal.to_ical()


if __name__ == "__main__":
    sample = [
        {
            "title": "Ghost in the Shell 4K Steelbook",
            "date_iso": "2026-07-22",
            "details": "Steelbook 4K UHD + 2x Blu-ray (1995)",
            "source": "4K-Ultra-HD.fr",
            "url": "https://4k-ultra-hd.fr/film/ghost-in-the-shell-4k-steelbook",
            "in_jellyfin": True,
        }
    ]
    print(build_ics(sample).decode("utf-8"))
