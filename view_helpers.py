"""Transforme les données brutes (Supabase) en structures prêtes pour les templates."""
import re
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")


def _to_float(val):
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return None


# Types d'évaluation reconnus, dans l'ordre de préférence de correspondance
_EVAL_TYPES = ["DS", "TP", "TD", "CC", "SOUT", "BE", "Participation", "Evaluation"]


def _extract_badge(epreuve: str) -> str:
    if not epreuve:
        return ""
    upper = epreuve.upper()
    for t in _EVAL_TYPES:
        if upper.startswith(t.upper()):
            return t
    return ""


def enrich_notes(raw_notes: list[dict]) -> dict:
    """
    Prend la liste brute de notes (depuis Supabase) et renvoie :
      - notes : liste enrichie pour l'affichage (badge, couleur, % position)
      - stats : moyenne générale + moyenne par cours
    """
    enriched = []
    by_course = defaultdict(list)

    for n in raw_notes:
        note = _to_float(n.get("note"))
        note_max = _to_float(n.get("note_max"))
        moyenne = _to_float(n.get("moyenne"))
        plus_haute = _to_float(n.get("plus_haute"))
        plus_basse = _to_float(n.get("plus_basse"))

        # Certaines évaluations ont note_max=0 dans scolweb (bug/convention de leur
        # côté) — on suppose alors une échelle standard sur 20 pour l'affichage.
        scale = note_max if note_max and note_max > 0 else 20

        is_above_avg = (
            note is not None and moyenne is not None and note >= moyenne
        )

        position_pct = None
        if note is not None and scale:
            position_pct = max(0, min(100, round((note / scale) * 100, 1)))

        avg_position_pct = None
        if moyenne is not None and scale:
            avg_position_pct = max(0, min(100, round((moyenne / scale) * 100, 1)))

        min_position_pct = None
        if plus_basse is not None and scale:
            min_position_pct = max(0, min(100, round((plus_basse / scale) * 100, 1)))

        max_position_pct = None
        if plus_haute is not None and scale:
            max_position_pct = max(0, min(100, round((plus_haute / scale) * 100, 1)))

        item = {
            **n,
            "note_num": note,
            "scale": scale,
            "badge": _extract_badge(n.get("epreuve", "")),
            "is_above_avg": is_above_avg,
            "position_pct": position_pct,
            "avg_position_pct": avg_position_pct,
            "min_position_pct": min_position_pct,
            "max_position_pct": max_position_pct,
        }
        enriched.append(item)

        # Pour les stats par cours : on exclut les évals avec note_max=0
        # (semblent être des évaluations non comptabilisées, ex: TD non noté officiellement)
        if note is not None and note_max and note_max > 0:
            by_course[n.get("cours", "?")].append(note)

    # Tri par date décroissante (format DD/MM/YYYY)
    def _parse_date(d):
        try:
            return datetime.strptime(d, "%d/%m/%Y")
        except (ValueError, TypeError):
            return datetime.min

    enriched.sort(key=lambda x: _parse_date(x.get("date", "")), reverse=True)

    course_stats = []
    all_notes_for_global_avg = []
    for course, notes in by_course.items():
        avg = sum(notes) / len(notes)
        course_stats.append({"cours": course, "average": round(avg, 2), "count": len(notes)})
        all_notes_for_global_avg.extend(notes)

    course_stats.sort(key=lambda x: x["average"])

    global_average = (
        round(sum(all_notes_for_global_avg) / len(all_notes_for_global_avg), 2)
        if all_notes_for_global_avg
        else None
    )

    return {
        "notes": enriched,
        "course_stats": course_stats,
        "global_average": global_average,
    }


_JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def _format_date_fr(dt: datetime) -> str:
    return f"{_JOURS_FR[dt.weekday()]} {dt.day} {_MOIS_FR[dt.month - 1]}"


GRID_START_HOUR = 7   # début de la grille (7h)
GRID_END_HOUR = 20    # fin de la grille (20h)
GRID_TOTAL_MIN = (GRID_END_HOUR - GRID_START_HOUR) * 60

_JOURS_COURT_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


def _minutes_from_grid_start(dt: datetime) -> float:
    return (dt.hour * 60 + dt.minute) - GRID_START_HOUR * 60


def enrich_planning(raw_events: list[dict]) -> list[dict]:
    """Regroupe les événements par semaine, avec position (%) dans une grille
    horaire pour un rendu en vrai calendrier (colonnes = jours, lignes = heures)."""
    parsed = []
    for e in raw_events:
        start = e.get("start_at")
        end = e.get("end_at")
        if not start:
            continue
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(PARIS_TZ)
            end_dt = (
                datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(PARIS_TZ)
                if end else start_dt
            )
        except ValueError:
            continue

        title_lines = (e.get("title") or "").split("\n")
        title_lines = [t.strip(" -") for t in title_lines if t.strip(" -")]

        is_exam = any(
            kw in (e.get("title") or "").upper() for kw in ["DS", "EXAMEN", "EPREUVE"]
        )

        parsed.append({
            "start_dt": start_dt,
            "end_dt": end_dt,
            "title": title_lines[0] if title_lines else "(sans titre)",
            "subtitle": title_lines[1] if len(title_lines) > 1 else "",
            "room": title_lines[2] if len(title_lines) > 2 else "",
            "start_time": start_dt.strftime("%H:%M"),
            "end_time": end_dt.strftime("%H:%M"),
            "is_exam": is_exam,
        })

    parsed.sort(key=lambda x: x["start_dt"])

    # Regroupe par semaine (année ISO, numéro de semaine)
    weeks = {}
    for ev in parsed:
        iso_year, iso_week, _ = ev["start_dt"].isocalendar()
        key = (iso_year, iso_week)
        weeks.setdefault(key, []).append(ev)

    result = []
    for (iso_year, iso_week), events in sorted(weeks.items()):
        monday = datetime.fromisocalendar(iso_year, iso_week, 1)
        days = []
        for i in range(7):
            day_date = (monday + timedelta(days=i)).replace(tzinfo=PARIS_TZ)
            day_events = [
                ev for ev in events
                if ev["start_dt"].date() == day_date.date()
            ]
            positioned = []
            for ev in day_events:
                top_min = max(0, _minutes_from_grid_start(ev["start_dt"]))
                end_min = min(GRID_TOTAL_MIN, _minutes_from_grid_start(ev["end_dt"]))
                duration_min = max(20, end_min - top_min)  # hauteur minimale visible
                positioned.append({
                    **ev,
                    "top_pct": round((top_min / GRID_TOTAL_MIN) * 100, 2),
                    "height_pct": round((duration_min / GRID_TOTAL_MIN) * 100, 2),
                })
            days.append({
                "label": _JOURS_COURT_FR[i],
                "day_num": day_date.day,
                "iso_date": day_date.strftime("%Y-%m-%d"),
                "events": positioned,
            })

        result.append({
            "week_key": f"{iso_year}-W{iso_week:02d}",
            "label": f"{monday.day} {_MOIS_FR[monday.month - 1]} – {(monday + timedelta(days=6)).day} {_MOIS_FR[(monday + timedelta(days=6)).month - 1]}",
            "days": days,
            "hour_labels": [f"{h}h" for h in range(GRID_START_HOUR, GRID_END_HOUR)],
        })

    return result
