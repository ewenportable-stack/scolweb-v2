import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from login import login as scolweb_login
from crypto_utils import encrypt_password
from supabase_client import (
    upsert_credentials,
    user_exists,
    fetch_planning_for_user,
    fetch_notes_for_user,
)
from sync import sync_all_users, sync_single_user
from session_utils import create_session_token, verify_session_token
from view_helpers import enrich_notes, enrich_planning

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scolweb-sync")

scheduler = BackgroundScheduler()
templates = Jinja2Templates(directory="templates")

SESSION_COOKIE_NAME = "scolweb_session"


def _run_sync_job():
    logger.info("Démarrage de la synchro planifiée")
    try:
        result = sync_all_users()
        logger.info(f"Synchro terminée : {result}")
    except Exception as e:
        logger.exception(f"La synchro a échoué : {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(_run_sync_job, "interval", hours=1, id="hourly_sync")
    scheduler.start()
    logger.info("Scheduler démarré (synchro toutes les heures)")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Scolweb Sync API", version="1.0.0", lifespan=lifespan)


class RegisterRequest(BaseModel):
    username: str
    password: str


# --- API (utilisée pour les tests / usages programmatiques) ---

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/register")
def register(payload: RegisterRequest, background_tasks: BackgroundTasks):
    try:
        scolweb_login(payload.username, payload.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    encrypted = encrypt_password(payload.password)
    try:
        upsert_credentials(payload.username, encrypted)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    background_tasks.add_task(sync_single_user, payload.username, payload.password)
    return {"status": "ok", "message": "Identifiants enregistrés, synchro en cours."}


@app.post("/sync-now")
def sync_now():
    result = sync_all_users()
    return result


# --- Site web (login par cookie de session) ---

def _get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return verify_session_token(token)


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    username = _get_current_user(request)
    if username:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    username = _get_current_user(request)
    if username:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        scolweb_login(username, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "login.html", {"error": str(e)}, status_code=401
        )

    is_new_user = not user_exists(username)

    encrypted = encrypt_password(password)
    upsert_credentials(username, encrypted)

    if is_new_user:
        # Première fois : synchro immédiate et bloquante, pour que le dashboard
        # soit déjà rempli quand l'utilisateur arrive dessus.
        sync_single_user(username, password)

    token = create_session_token(username)
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    username = _get_current_user(request)
    if not username:
        return RedirectResponse(url="/login")

    events = fetch_planning_for_user(username)
    notes = fetch_notes_for_user(username)

    notes_data = enrich_notes(notes)
    planning_data = enrich_planning(events)

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "weeks": planning_data["weeks"],
            "months": planning_data["months"],
            "upcoming_important": planning_data["upcoming_important"],
            "hours_breakdown": planning_data["hours_breakdown"],
            "notes": notes_data["notes"],
            "course_stats": notes_data["course_stats"],
            "global_average": notes_data["global_average"],
        }
    )
