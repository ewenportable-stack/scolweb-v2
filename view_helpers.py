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

# Fenêtre par défaut autour d'aujourd'hui si peu/pas d'événements
WEEKS_BEFORE_TODAY = 2
WEEKS_AFTER_TODAY = 10


def _minutes_from_grid_start(dt: datetime) -> float:
    return (dt.hour * 60 + dt.minute) - GRID_START_HOUR * 60


# Couleurs par type d'événement (repris de la classification réelle scolweb/Aurion)
TYPE_COLORS = {
    "CM": "#2F3EA8", "TD": "#3FB6C4", "TP": "#E8B23A", "BE": "#6B5B7B",
    "CTRL": "#C23B32", "PE": "#2E8B4E", "PNE": "#7FCBA0", "SOUT": "#D9784F",
    "INFO": "#8B3A62", "INFO_OBL": "#8B3A62", "ENTREP": "#B34700",
    "PROVISOIRE": "#9AA0A6", "RATT": "#6B6B6B", "EVEN": "#4A6FA5",
}


def _color_for_type(t: str) -> str:
    return TYPE_COLORS.get(t, "#6B6B6B")


def _parse_events(raw_events: list[dict]) -> list[dict]:
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

        # Le vrai type d'événement scolweb (CM/TD/TP/CTRL/SOUT/...) est stocké
        # tel quel dans class_name — bien plus fiable que deviner via le titre.
        ev_type = (e.get("class_name") or "").strip().upper()
        is_exam = ev_type in ("CTRL", "DS")
        is_soutenance = ev_type == "SOUT"

        subtitle = title_lines[1] if len(title_lines) > 1 else ""
        # La matière est la partie avant le premier " - " du sous-titre
        # (le reste étant les groupes/promos, ex: "Asservissement - ECAM4N_PROMO_2627")
        matiere = subtitle.split(" - ")[0].strip() if subtitle else ""

        duration_hours = max(0, (end_dt - start_dt).total_seconds() / 3600)

        parsed.append({
            "start_dt": start_dt,
            "end_dt": end_dt,
            "title": title_lines[0] if title_lines else "(sans titre)",
            "subtitle": subtitle,
            "room": title_lines[2] if len(title_lines) > 2 else "",
            "start_time": start_dt.strftime("%H:%M"),
            "end_time": end_dt.strftime("%H:%M"),
            "type": ev_type,
            "matiere": matiere or "(non précisé)",
            "duration_hours": duration_hours,
            "is_exam": is_exam,
            "is_soutenance": is_soutenance,
        })

    parsed.sort(key=lambda x: x["start_dt"])
    return parsed


def build_upcoming_important(parsed: list[dict], limit: int = 25) -> list[dict]:
    """Liste des prochains DS/contrôles/soutenances à venir, triés chronologiquement."""
    now = datetime.now(PARIS_TZ)
    upcoming = [
        ev for ev in parsed
        if (ev["is_exam"] or ev["is_soutenance"]) and ev["start_dt"] >= now
    ]
    upcoming.sort(key=lambda x: x["start_dt"])

    result = []
    for ev in upcoming[:limit]:
        result.append({
            "date_label": _format_date_fr(ev["start_dt"]),
            "start_time": ev["start_time"],
            "title": ev["title"],
            "matiere": ev["matiere"],
            "kind": "Soutenance" if ev["is_soutenance"] else "Contrôle",
        })
    return result


def build_hours_breakdown(parsed: list[dict]) -> dict:
    """Répartition des heures : par semaine/type (pour le graphique en barres)
    et par matière (pour le classement), + quelques indicateurs clés."""
    # Ne compte que les créneaux "normaux" (exclut les blocs de plusieurs jours
    # type ENTREP qui fausseraient les totaux horaires hebdomadaires)
    real_sessions = [ev for ev in parsed if 0 < ev["duration_hours"] <= 8]

    by_week = defaultdict(lambda: defaultdict(float))
    week_labels = {}
    for ev in real_sessions:
        monday = _monday_of(ev["start_dt"])
        key = monday.strftime("%Y-%m-%d")
        week_labels[key] = f"{monday.day}/{monday.month}"
        by_week[key][ev["type"] or "?"] += ev["duration_hours"]

    all_types = sorted({t for wk in by_week.values() for t in wk.keys()})

    chart_weeks = []
    for key in sorted(by_week.keys()):
        by_type = by_week[key]
        total = sum(by_type.values())
        chart_weeks.append({
            "week_label": week_labels[key],
            "total_hours": round(total, 1),
            "by_type": [
                {"type": t, "hours": round(by_type.get(t, 0), 2), "color": _color_for_type(t)}
                for t in all_types if by_type.get(t, 0) > 0
            ],
        })

    by_matiere = defaultdict(float)
    for ev in real_sessions:
        by_matiere[ev["matiere"]] += ev["duration_hours"]
    matiere_stats = sorted(
        [{"matiere": m, "hours": round(h, 1)} for m, h in by_matiere.items()],
        key=lambda x: x["hours"], reverse=True
    )

    total_hours = sum(ev["duration_hours"] for ev in real_sessions)
    nb_weeks = len(chart_weeks) or 1
    busiest = max(chart_weeks, key=lambda w: w["total_hours"], default=None)

    return {
        "chart_weeks": chart_weeks,
        "matiere_stats": matiere_stats,
        "legend_types": [{"type": t, "color": _color_for_type(t)} for t in all_types],
        "total_hours": round(total_hours, 1),
        "avg_hours_per_week": round(total_hours / nb_weeks, 1),
        "busiest_week_label": busiest["week_label"] if busiest else "—",
        "busiest_week_hours": busiest["total_hours"] if busiest else 0,
        "sessions_count": len(real_sessions),
    }


def _monday_of(dt: datetime) -> datetime:
    d = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


def build_weeks(parsed: list[dict]) -> list[dict]:
    """Construit une plage CONTINUE de semaines (y compris sans événement),
    pour que la navigation fonctionne même sur une semaine vide."""
    today_monday = _monday_of(datetime.now(PARIS_TZ))

    if parsed:
        range_start = min(_monday_of(parsed[0]["start_dt"]), today_monday - timedelta(weeks=WEEKS_BEFORE_TODAY))
        range_end = max(_monday_of(parsed[-1]["start_dt"]), today_monday + timedelta(weeks=WEEKS_AFTER_TODAY))
    else:
        range_start = today_monday - timedelta(weeks=WEEKS_BEFORE_TODAY)
        range_end = today_monday + timedelta(weeks=WEEKS_AFTER_TODAY)

    by_day = defaultdict(list)
    for ev in parsed:
        by_day[ev["start_dt"].date()].append(ev)

    result = []
    monday = range_start
    while monday <= range_end:
        days = []
        for i in range(7):
            day_date = monday + timedelta(days=i)
            day_events = by_day.get(day_date.date(), [])
            positioned = []
            for ev in day_events:
                top_min = max(0, _minutes_from_grid_start(ev["start_dt"]))
                end_min = min(GRID_TOTAL_MIN, _minutes_from_grid_start(ev["end_dt"]))
                duration_min = max(20, end_min - top_min)
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

        iso_year, iso_week, _ = monday.isocalendar()
        week_end = monday + timedelta(days=6)
        result.append({
            "week_key": f"{iso_year}-W{iso_week:02d}",
            "label": f"{monday.day} {_MOIS_FR[monday.month - 1]} – {week_end.day} {_MOIS_FR[week_end.month - 1]}",
            "days": days,
            "hour_labels": [f"{h}h" for h in range(GRID_START_HOUR, GRID_END_HOUR)],
        })
        monday += timedelta(weeks=1)

    return result


def build_months(parsed: list[dict]) -> list[dict]:
    """Construit une plage continue de mois, chaque mois = grille de semaines
    (jours avec juste un résumé du nombre d'événements, pas d'horaires détaillés)."""
    today = datetime.now(PARIS_TZ)
    today_month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if parsed:
        first_month = parsed[0]["start_dt"].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = parsed[-1]["start_dt"].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        range_start = min(first_month, today_month_start - timedelta(days=60))
        range_end = max(last_month, today_month_start + timedelta(days=300))
    else:
        range_start = today_month_start - timedelta(days=60)
        range_end = today_month_start + timedelta(days=300)

    by_day = defaultdict(list)
    for ev in parsed:
        by_day[ev["start_dt"].date()].append(ev)

    def _add_month(dt, n):
        month = dt.month - 1 + n
        year = dt.year + month // 12
        month = month % 12 + 1
        return dt.replace(year=year, month=month, day=1)

    months = []
    cursor = range_start.replace(day=1)
    end_marker = range_end.replace(day=1)
    while cursor <= end_marker:
        first_of_month = cursor
        # premier jour affiché = lundi de la semaine contenant le 1er du mois
        grid_start = _monday_of(first_of_month)
        next_month = _add_month(first_of_month, 1)

        weeks_grid = []
        day_cursor = grid_start
        while True:
            week_days = []
            for i in range(7):
                d = day_cursor + timedelta(days=i)
                day_events = by_day.get(d.date(), [])
                week_days.append({
                    "day_num": d.day,
                    "iso_date": d.strftime("%Y-%m-%d"),
                    "in_month": d.month == first_of_month.month,
                    "event_count": len(day_events),
                    "titles": [ev["title"] for ev in day_events[:2]],
                })
            weeks_grid.append(week_days)
            day_cursor += timedelta(days=7)
            if day_cursor >= next_month:
                break

        months.append({
            "month_key": first_of_month.strftime("%Y-%m"),
            "label": f"{_MOIS_FR[first_of_month.month - 1].capitalize()} {first_of_month.year}",
            "weeks_grid": weeks_grid,
        })
        cursor = next_month

    return months


def enrich_planning(raw_events: list[dict]) -> dict:
    """Renvoie {'weeks', 'months', 'upcoming_important', 'hours_breakdown'}
    pour alimenter les vues calendrier, le bandeau latéral et l'onglet stats horaires."""
    parsed = _parse_events(raw_events)
    return {
        "weeks": build_weeks(parsed),
        "months": build_months(parsed),
        "upcoming_important": build_upcoming_important(parsed),
        "hours_breakdown": build_hours_breakdown(parsed),
    }
