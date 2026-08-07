import base64
import hashlib
import hmac
import os
import time
from typing import Optional

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 jours


def _sign(payload: str) -> str:
    if not SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET manquant. Génère-en un avec : "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session_token(username: str) -> str:
    issued_at = str(int(time.time()))
    payload = f"{username}|{issued_at}"
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    signature = _sign(payload_b64)
    return f"{payload_b64}.{signature}"


def verify_session_token(token: Optional[str]) -> Optional[str]:
    """Renvoie le username si le token est valide et pas expiré, sinon None."""
    if not token or "." not in token:
        return None
    payload_b64, signature = token.rsplit(".", 1)
    try:
        expected = _sign(payload_b64)
    except RuntimeError:
        return None
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = base64.urlsafe_b64decode(payload_b64.encode()).decode()
        username, issued_at = payload.split("|")
        if time.time() - int(issued_at) > SESSION_MAX_AGE:
            return None
        return username
    except Exception:
        return None
