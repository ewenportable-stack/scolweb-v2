import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from login import login, get_planning_from_session, get_notes_from_session
from crypto_utils import decrypt_password
from supabase_client import fetch_all_users, upsert_events, upsert_notes, mark_sync_result

MAX_WORKERS = int(os.environ.get("SYNC_MAX_WORKERS", "5"))


def sync_single_user(username: str, password: str) -> None:
    """Synchronise le planning ET les notes d'un seul utilisateur immédiatement
    (ex: juste après inscription). Un seul login, réutilisé pour les deux."""
    errors = []

    try:
        session, status_code = login(username, password)
        if status_code != 302:
            raise ValueError("Login échoué")
    except Exception as e:
        print(f"[{username}] Login ÉCHEC - {e}")
        try:
            mark_sync_result(username, error=f"login: {e}")
        except Exception:
            pass
        return

    try:
        raw = get_planning_from_session(session, datetime.date.today())
        events = json.loads(raw)
        upsert_events(username, events)
        print(f"[{username}] Planning OK - {len(events)} événements")
    except Exception as e:
        errors.append(f"planning: {e}")
        print(f"[{username}] Planning ÉCHEC - {e}")

    try:
        notes = get_notes_from_session(session)
        upsert_notes(username, notes)
        print(f"[{username}] Notes OK - {len(notes)} notes")
    except Exception as e:
        errors.append(f"notes: {e}")
        print(f"[{username}] Notes ÉCHEC - {e}")

    try:
        mark_sync_result(username, error="; ".join(errors) if errors else None)
    except Exception:
        pass


def _sync_one_user(user: dict) -> None:
    username = user["username"]
    try:
        password = decrypt_password(user["encrypted_password"])
        sync_single_user(username, password)
    except Exception as e:
        print(f"[{username}] ÉCHEC - {e}")


def sync_all_users() -> dict:
    """Synchronise le planning de tous les utilisateurs enregistrés.
    Renvoie un petit résumé (nombre d'utilisateurs traités)."""
    users = fetch_all_users()
    print(f"[sync] {len(users)} utilisateur(s) à synchroniser")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_sync_one_user, u) for u in users]
        for _ in as_completed(futures):
            pass

    return {"users_processed": len(users)}
