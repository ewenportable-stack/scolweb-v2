import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from login import login as scolweb_login
from crypto_utils import encrypt_password
from supabase_client import upsert_credentials
from sync import sync_all_users, sync_single_user

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scolweb-sync")

scheduler = BackgroundScheduler()


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/register")
def register(payload: RegisterRequest, background_tasks: BackgroundTasks):
    """
    Vérifie les identifiants scolweb, les stocke chiffrés dans Supabase,
    puis lance une synchro immédiate en tâche de fond (l'utilisateur n'attend pas).
    """
    try:
        scolweb_login(payload.username, payload.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    encrypted = encrypt_password(payload.password)
    try:
        upsert_credentials(payload.username, encrypted)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Synchro immédiate en tâche de fond : la réponse HTTP part tout de suite,
    # le planning arrive dans Supabase quelques secondes après.
    background_tasks.add_task(sync_single_user, payload.username, payload.password)

    return {"status": "ok", "message": "Identifiants enregistrés, synchro en cours."}


@app.post("/sync-now")
def sync_now():
    """Déclenche une synchro immédiate (pour tester manuellement, pas pour un usage fréquent)."""
    result = sync_all_users()
    return result
