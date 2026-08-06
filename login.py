import requests
import re

HOSTNAME = "scolweb.ecam-ldb.fr"
LOGIN_URL = f"https://{HOSTNAME}/login"


def login(username: str, password: str):
    """
    Se connecte à scolweb et renvoie (session, status_code).
    'session' est un objet requests.Session déjà authentifié, réutilisable
    pour toutes les requêtes suivantes (planning, notes, etc.).
    Lève une exception si le login échoue.
    """
    payload = {
        "username": username,
        "password": password,
        "j_idt27": "",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Connection": "keep-alive",
        "Origin": f"https://{HOSTNAME}",
        "Referer": f"https://{HOSTNAME}/faces/Login.xhtml",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    session = requests.Session()

    # 1) GET préalable sur la page de login pour récupérer un premier JSESSIONID
    session.get(f"https://{HOSTNAME}/faces/Login.xhtml")

    # 2) POST avec ce même cookie de session
    response = session.post(LOGIN_URL, data=payload, headers=headers, allow_redirects=False)

    if response.status_code != 302 or "Login ou mot de passe invalide" in response.text:
        raise ValueError("Login échoué : identifiant ou mot de passe invalide.")

    return session, response.status_code


MENU_ID_PLANNING = "3_0"  # id du lien "Mon planning" dans le menu, fixe pour ce site


def _parse_view_state(body: str) -> str:
    match = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', body)
    if not match:
        raise ValueError("ViewState introuvable dans la réponse")
    return match.group(1)


def _parse_id_init(body: str) -> str:
    match = re.search(r'name="form:idInit"[^>]*value="([^"]+)"', body)
    if not match:
        idx = body.find("idInit")
        if idx != -1:
            raise ValueError(
                "form:idInit introuvable avec le pattern attendu. "
                f"Contexte : ...{body[max(0,idx-100):idx+150]}..."
            )
        raise ValueError("form:idInit introuvable dans la réponse")
    return match.group(1)


def _parse_planning_form_id(body: str) -> str:
    # L'id peut contenir des ':' supplémentaires (ex: form:j_idt757:j_idt760)
    match = re.search(r'<div id="([^"]+)" class="schedule">', body)
    if not match:
        raise ValueError("Composant schedule (class=\"schedule\") introuvable dans la réponse")
    return match.group(1)


def _post_sidebar_click(session, view_state: str, id_init: str) -> str:
    """Simule le clic sur le lien 'Mon planning' du menu (vrai submit de formulaire, pas AJAX).
    Le serveur répond normalement par une redirection 302 vers Planning.xhtml, que
    `requests` suit automatiquement."""
    payload = {
        "form": "form",
        "form:largeurDivCenter": "659",
        "form:idInit": id_init,
        "form:sidebar": "form:sidebar",
        "form:sidebar_menuid": MENU_ID_PLANNING,
        "javax.faces.ViewState": view_state,
    }
    resp = session.post(
        f"https://{HOSTNAME}/faces/MainMenuPage.xhtml",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return resp.text


def _post_planning(session, view_state: str, id_init: str, planning_form_id: str,
                    start_ms: int, end_ms: int, today_str: str, week_year: str) -> str:
    payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": planning_form_id,
        "javax.faces.partial.execute": planning_form_id,
        "javax.faces.partial.render": planning_form_id,
        planning_form_id: planning_form_id,
        f"{planning_form_id}_start": str(start_ms),
        f"{planning_form_id}_end": str(end_ms),
        "form": "form",
        "form:largeurDivCenter": "659",
        "form:idInit": id_init,
        "form:date_input": today_str,
        "form:week": week_year,
        f"{planning_form_id}_view": "agendaWeek",
        "form:offsetFuseauNavigateur": "-7200000",
        "form:onglets_activeIndex": "0",
        "form:onglets_scrollState": "0",
        "javax.faces.ViewState": view_state,
    }
    resp = session.post(
        f"https://{HOSTNAME}/faces/Planning.xhtml",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return resp.text


def _extract_events_json(raw: str) -> str:
    """Extrait le tableau JSON des événements en comptant les crochets/accolades
    (plus robuste qu'une regex qui suppose une fin de type '}]]')."""
    start = raw.find('[{"id"')
    if start == -1:
        # Pas d'événement sur la période (semaine vide) : on considère que c'est un tableau vide.
        if "events" in raw or "schedule" in raw.lower():
            return "[]"
        raise ValueError(
            "Impossible de localiser le tableau d'événements dans la réponse "
            "(structure inattendue)."
        )

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]

    raise ValueError("Tableau d'événements mal formé (crochets non équilibrés).")


def _iso_week(date):
    return date.isocalendar()[1]


def get_planning(username: str, password: str, start_date):
    """
    start_date : objet datetime.date ou datetime.datetime représentant le début de la période voulue.
    Renvoie le texte brut contenant les événements JSON (à parser ensuite).
    """
    import datetime

    session, status_code = login(username, password)
    if status_code != 302:
        raise ValueError("Login échoué")

    # 1) Page d'accueil : viewState + idInit
    body = session.get(f"https://{HOSTNAME}/").text
    view_state = _parse_view_state(body)
    id_init = _parse_id_init(body)

    # 2) Clic sur "Mon planning" : submit de formulaire classique, suit la redirection
    #    vers Planning.xhtml automatiquement (requests gère ça par défaut).
    body = _post_sidebar_click(session, view_state, id_init)
    view_state = _parse_view_state(body)
    id_init = _parse_id_init(body)
    planning_form_id = _parse_planning_form_id(body)

    # 4) Prépare les dates
    if isinstance(start_date, datetime.datetime):
        start_date = start_date.date()
    end_date = start_date + datetime.timedelta(days=60)  # ~2 mois

    today_str = start_date.strftime("%d/%m/%Y")
    week_year = f"{_iso_week(start_date)}-{start_date.year}"

    start_ms = int(datetime.datetime.combine(start_date, datetime.time()).timestamp() * 1000) - 604800000
    end_ms = int(datetime.datetime.combine(end_date, datetime.time()).timestamp() * 1000)

    # 5) Requête finale : récupère les événements
    raw = _post_planning(session, view_state, id_init, planning_form_id,
                          start_ms, end_ms, today_str, week_year)

    return _extract_events_json(raw)


if __name__ == "__main__":
    import datetime

    # --- Zone de test ---
    username = "prenom.nom"       # remplace par ton identifiant
    password = "ton_mot_de_passe"  # remplace par ton mot de passe

    try:
        session, status_code = login(username, password)
        print("Status code:", status_code)
        print("Login OK, session établie.")

        raw_planning = get_planning(username, password, datetime.date.today())
        print("Planning brut (extrait):", raw_planning[:1000])
    except Exception as e:
        print("Erreur:", e)
