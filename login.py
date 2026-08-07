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
    if match:
        return match.group(1)
    # Format des réponses AJAX partielles (partial-response / <update>)
    match = re.search(r'id="j_id1:javax\.faces\.ViewState:0"><!\[CDATA\[([^\]]+)\]\]>', body)
    if match:
        return match.group(1)
    raise ValueError("ViewState introuvable dans la réponse")


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


def _post_sidebar_click(session, view_state: str, id_init: str, menu_id: str) -> str:
    """Simule le clic sur un lien du menu (vrai submit de formulaire, pas AJAX).
    Le serveur répond normalement par une redirection 302 vers la bonne page, que
    `requests` suit automatiquement."""
    payload = {
        "form": "form",
        "form:largeurDivCenter": "659",
        "form:idInit": id_init,
        "form:sidebar": "form:sidebar",
        "form:sidebar_menuid": menu_id,
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


MENU_ID_NOTES = "1_0"  # fallback, mais on cherche dynamiquement par libellé d'abord
NOTES_ROWS_PER_PAGE = 20
NOTES_COLUMNS = ["date", "cours", "epreuve", "note", "note_max", "plus_haute", "plus_basse", "moyenne"]


def _find_menu_id_by_label(body: str, label: str) -> str:
    """Cherche dans le HTML du menu le lien dont le texte est exactement `label`,
    et renvoie l'id de menu (ex: '1_0') associé à son onclick. Plus robuste qu'un
    id codé en dur, qui peut varier selon les menus disponibles pour le compte.
    Le motif '(?:(?!</a>).)*?' empêche de déborder sur un autre lien du menu."""
    pattern = re.compile(
        r"'form:sidebar_menuid':'([^']+)'\}\)\.submit\('form'\);return false;\"[^>]*>"
        r"(?:(?!</a>).)*?<span class=\"ui-menuitem-text\">" + re.escape(label) + r"</span>",
        re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        raise ValueError(f"Impossible de trouver l'id de menu pour le lien '{label}' dans la sidebar")
    return match.group(1)


def _parse_sidebar_ajax_form_id(body: str) -> str:
    """Id du composant utilisé par chargerSousMenu() pour déplier un sous-menu via AJAX."""
    match = re.search(r'chargerSousMenu = function\(\) \{PrimeFaces\.ab\(\{s:"([^"]+)"', body)
    if not match:
        raise ValueError("Id du composant sidebar (chargerSousMenu) introuvable")
    return match.group(1)


def _find_submenu_class_by_label(body: str, label: str) -> str:
    """Trouve la classe CSS submenu_XXXXX du sous-menu parent portant ce libellé
    (ex: 'Scolarité'), utilisée pour demander son dépliage via AJAX.
    Le motif '(?:(?!</li>).)*?' empêche de déborder sur le <li> suivant."""
    pattern = re.compile(
        r'ui-menu-parent (submenu_\d+)[^"]*"[^>]*>(?:(?!</li>).)*?<span class="ui-menuitem-text">'
        + re.escape(label) + r"</span>",
        re.DOTALL,
    )
    match = pattern.search(body)
    if not match:
        raise ValueError(f"Sous-menu parent '{label}' introuvable dans la sidebar")
    return match.group(1)


def _post_expand_submenu(session, view_state: str, id_init: str, sidebar_form_id: str, submenu_class: str) -> str:
    """Déplie un sous-menu paresseux via AJAX (équivalent du clic sur la rubrique parente),
    et renvoie le HTML mis à jour de la sidebar contenant maintenant ses enfants."""
    payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": sidebar_form_id,
        "javax.faces.partial.execute": sidebar_form_id,
        "javax.faces.partial.render": "form:sidebar",
        sidebar_form_id: sidebar_form_id,
        "webscolaapp.Sidebar.ID_SUBMENU": submenu_class,
        "form": "form",
        "form:largeurDivCenter": "659",
        "form:idInit": id_init,
        "javax.faces.ViewState": view_state,
    }
    resp = session.post(
        f"https://{HOSTNAME}/faces/MainMenuPage.xhtml",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return resp.text


def _find_menu_id_with_expand(session, home_body: str, view_state: str, id_init: str,
                               parent_label: str, item_label: str) -> str:
    """Déplie toujours le sous-menu parent via AJAX avant de chercher le lien enfant
    (plus simple et fiable que d'essayer d'abord sans, quitte à faire une requête
    en plus si jamais les enfants étaient déjà chargés)."""
    sidebar_form_id = _parse_sidebar_ajax_form_id(home_body)
    submenu_class = _find_submenu_class_by_label(home_body, parent_label)
    expanded_body = _post_expand_submenu(session, view_state, id_init, sidebar_form_id, submenu_class)
    return _find_menu_id_by_label(expanded_body, item_label)


def _parse_notes_table_id(body: str) -> str:
    match = re.search(r'<div id="([^"]+)" class="ui-datatable', body)
    if not match:
        raise ValueError('Tableau de notes ("ui-datatable") introuvable dans la réponse')
    return match.group(1)


def _parse_row_count(body: str) -> int:
    match = re.search(r"rowCount:(\d+)", body)
    return int(match.group(1)) if match else 0


def _parse_table_rows(html: str):
    """Extrait chaque ligne <tr data-ri="N">...</tr> et ses cellules <span class="preformatted">."""
    rows = []
    for tr_match in re.finditer(r'<tr[^>]*data-ri="\d+"[^>]*>(.*?)</tr>', html, re.DOTALL):
        row_html = tr_match.group(1)
        cells = re.findall(r'<span class="preformatted ?">(.*?)</span>', row_html, re.DOTALL)
        rows.append([c.strip() for c in cells])
    return rows


def _post_all_notes(session, view_state: str, id_init: str, table_id: str) -> str:
    """Demande TOUTES les lignes en une seule requête (rows=20000) plutôt que de
    paginer par tranches de 20 — plus simple et plus rapide."""
    payload = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": table_id,
        "javax.faces.partial.execute": table_id,
        "javax.faces.partial.render": table_id,
        table_id: table_id,
        f"{table_id}_pagination": "true",
        f"{table_id}_first": "0",
        f"{table_id}_rows": "20000",
        f"{table_id}_skipChildren": "true",
        f"{table_id}_encodeFeature": "true",
        "form": "form",
        "form:largeurDivCenter": "659",
        "form:idInit": id_init,
        f"{table_id}_reflowDD": "0_0",
        "javax.faces.ViewState": view_state,
    }
    resp = session.post(
        f"https://{HOSTNAME}/faces/ChoixDonnee.xhtml",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return resp.text


def get_notes(username: str, password: str):
    """Renvoie la liste des notes (chaque note = dict avec les clés NOTES_COLUMNS)."""
def get_notes_from_session(session) -> list:
    """Comme get_notes, mais réutilise une session déjà authentifiée
    (pour éviter un login redondant si on récupère aussi le planning)."""
    # 1) Page d'accueil : viewState + idInit
    home_body = session.get(f"https://{HOSTNAME}/").text
    view_state = _parse_view_state(home_body)
    id_init = _parse_id_init(home_body)

    # 2) Clic sur "Mes notes" : le sous-menu "Scolarité" doit d'abord être déplié via AJAX
    menu_id = _find_menu_id_with_expand(session, home_body, view_state, id_init, "Scolarité", "Mes notes")
    body = _post_sidebar_click(session, view_state, id_init, menu_id)
    view_state = _parse_view_state(body)
    id_init = _parse_id_init(body)
    table_id = _parse_notes_table_id(body)

    # 3) Demande toutes les lignes d'un coup (rows=20000) plutôt que de paginer
    all_body = _post_all_notes(session, view_state, id_init, table_id)
    all_rows = _parse_table_rows(all_body)

    return [dict(zip(NOTES_COLUMNS, row)) for row in all_rows]


def get_notes(username: str, password: str) -> list:
    """Renvoie la liste des notes (chaque note = dict avec les clés NOTES_COLUMNS).
    Se connecte elle-même (pratique pour un usage isolé/tests) — si tu récupères
    aussi le planning dans le même appel, utilise plutôt login() une seule fois
    puis get_notes_from_session()."""
    session, status_code = login(username, password)
    if status_code != 302:
        raise ValueError("Login échoué")
    return get_notes_from_session(session)


def get_planning_from_session(session, start_date) -> str:
    """Comme get_planning, mais réutilise une session déjà authentifiée."""
    import datetime

    # 1) Page d'accueil : viewState + idInit
    body = session.get(f"https://{HOSTNAME}/").text
    view_state = _parse_view_state(body)
    id_init = _parse_id_init(body)

    # 2) Clic sur "Mon planning" : submit de formulaire classique, suit la redirection
    #    vers Planning.xhtml automatiquement (requests gère ça par défaut).
    body = _post_sidebar_click(session, view_state, id_init, MENU_ID_PLANNING)
    view_state = _parse_view_state(body)
    id_init = _parse_id_init(body)
    planning_form_id = _parse_planning_form_id(body)

    # 3) Prépare les dates
    if isinstance(start_date, datetime.datetime):
        start_date = start_date.date()
    end_date = start_date + datetime.timedelta(days=60)  # ~2 mois

    today_str = start_date.strftime("%d/%m/%Y")
    week_year = f"{_iso_week(start_date)}-{start_date.year}"

    start_ms = int(datetime.datetime.combine(start_date, datetime.time()).timestamp() * 1000) - 604800000
    end_ms = int(datetime.datetime.combine(end_date, datetime.time()).timestamp() * 1000)

    # 4) Requête finale : récupère les événements
    raw = _post_planning(session, view_state, id_init, planning_form_id,
                          start_ms, end_ms, today_str, week_year)

    return _extract_events_json(raw)


def get_planning(username: str, password: str, start_date) -> str:
    """
    start_date : objet datetime.date ou datetime.datetime représentant le début de la période voulue.
    Renvoie le texte brut contenant les événements JSON (à parser ensuite).
    Se connecte elle-même (pratique pour un usage isolé/tests) — si tu récupères
    aussi les notes dans le même appel, utilise plutôt login() une seule fois
    puis get_planning_from_session()."""
    session, status_code = login(username, password)
    if status_code != 302:
        raise ValueError("Login échoué")
    return get_planning_from_session(session, start_date)


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
