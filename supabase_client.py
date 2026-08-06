import os
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def _headers(prefer: str | None = None) -> dict:
    h = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def upsert_credentials(username: str, encrypted_password: str) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/scolweb_credentials?on_conflict=username",
        headers=_headers(prefer="resolution=merge-duplicates"),
        json=[{
            "username": username,
            "encrypted_password": encrypted_password,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }],
        timeout=15,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Erreur Supabase (upsert_credentials): {resp.status_code} {resp.text}")


def fetch_all_users() -> list[dict]:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/scolweb_credentials?select=username,encrypted_password",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def upsert_events(username: str, events: list[dict]) -> None:
    rows = [{
        "username": username,
        "event_id": e["id"],
        "title": e.get("title"),
        "start_at": e.get("start"),
        "end_at": e.get("end"),
        "all_day": e.get("allDay"),
        "class_name": e.get("className"),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    } for e in events]

    if not rows:
        return

    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/planning_events?on_conflict=username,event_id",
        headers=_headers(prefer="resolution=merge-duplicates"),
        json=rows,
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Erreur Supabase (upsert_events): {resp.status_code} {resp.text}")


def mark_sync_result(username: str, error: str | None) -> None:
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/scolweb_credentials?username=eq.{username}",
        headers=_headers(),
        json={
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "last_sync_error": error,
        },
        timeout=15,
    )
    if resp.status_code >= 300:
        print(f"[{username}] Impossible de mettre à jour last_sync_at: {resp.status_code} {resp.text}")
