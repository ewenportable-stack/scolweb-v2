import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from login import get_planning
from crypto_utils import decrypt_password
from supabase_client import fetch_all_users, upsert_events, mark_sync_result

MAX_WORKERS = int(os.environ.get("SYNC_MAX_WORKERS", "5"))


def sync_single_user(username: str, password: str) -> None:
    """Synchronise un seul utilisateur immédiatement (ex: juste après inscription).
    Prend le mot de passe en clair directement (pas besoin de repasser par Supabase)."""
    try:
        raw = get_planning(username, password, datetime.date.today())
        events = json.loads(raw)
        upsert_events(username, events)
        mark_sync_result(username, error=None)
        print(f"[{username}] OK (sync immédiate) - {len(events)} événements")
    except Exception as e:
        print(f"[{username}] ÉCHEC (sync immédiate) - {e}")
        try:
            mark_sync_result(username, error=str(e))
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
