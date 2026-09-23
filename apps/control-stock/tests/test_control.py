"""La pestaña Control (kanban de chats) y la pestaña CRM, en modo muestra."""

import pytest

from app import control, crm_flujo


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch):
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    control.refrescar()
    crm_flujo.refrescar()
    # En muestra las escrituras (motivo, drag de estado) mutan las filas:
    # cada caso arranca con el tablero de fábrica (solo Monica inactiva).
    de_fabrica = {"L1": "NUEVO", "L2": "EN_CONVERSACION", "L3": "CONTACTADO",
                  "L4": "GANADO", "L5": "CONTACTADO"}
    for fila in crm_flujo._MUESTRA:
        fila["motivoNoAvance"] = "SOLO_PREGUNTABA" if fila["id"] == "L5" else ""
        fila["estado"] = de_fabrica[fila["id"]]


def test_el_semaforo_reparte_las_columnas(cliente):
    cuerpo = cliente.get("/control").text
    assert "En curso" in cuerpo and "Esperando respuesta" in cuerpo
    assert "Inactivo" in cuerpo and "Terminado" in cuerpo
    # Terminado va ANTES de Inactivo (pedido del dueño, 23/09/2026).
    assert cuerpo.index("Terminado") < cuerpo.index("Inactivo")
    # Corrección del dueño (23/09/2026): Tamara escribió de último ->
    # ESPERA la respuesta del vivero; a Kev le contestaron -> En curso;
    # Soledad está Ganada -> Terminado.
    _columnas, por_chat = control.tablero()
    assert por_chat["m1"]["columna"] == "esperando" and por_chat["m1"]["debe"]
    assert por_chat["m2"]["columna"] == "en_curso"
    assert por_chat["m4"]["columna"] == "terminado"


def test_la_tarjeta_trae_mensaje_y_responder(cliente):
    cuerpo = cliente.get("/control").text
    assert "¿Tienen calatheas grandes?" in cuerpo       # el último mensaje
    assert "wa.me/50765520966" in cuerpo                # botón Responder
    assert "6552-0966" in cuerpo                        # número al lado del nombre


def test_la_ficha_de_control_es_la_misma_del_crm(cliente):
    # Tamara (chat m1, lead L1): su ficha trae la conversación completa en
    # burbujas, las notas y los datos del lead — como la ficha del CRM.
    cuerpo = cliente.get("/control?abrir=m1").text
    assert "Conversación · WhatsApp" in cuerpo
    assert "msj-cliente" in cuerpo                     # burbuja del cliente
    assert "Guardar nota" in cuerpo
    assert "Estado CRM" in cuerpo
    # Un chat sin lead (Diana, m3) conserva la ficha corta de siempre.
    corta = cliente.get("/control?abrir=m3").text
    assert "Guardar nota" not in corta
    assert "eq-burbuja" in corta


def test_mover_a_inactivo_pide_motivo_y_persiste(cliente):
    # El drag manda a Inactivo sin motivo: el servidor redirige al modal.
    r = cliente.post("/control/mover", data={"chat": "m3", "columna": "inactivo"},
                     follow_redirects=False)
    assert r.status_code == 303 and "motivo=m3" in r.headers["location"]
    # El modal vuelve con el motivo y ahí sí se guarda.
    r = cliente.post("/control/mover",
                     data={"chat": "m3", "columna": "inactivo",
                           "motivo": "solo_preguntaba"},
                     follow_redirects=False)
    assert r.status_code == 303
    _columnas, por_chat = control.tablero()
    assert por_chat["m3"]["columna"] == "inactivo"
    assert por_chat["m3"]["motivo"] == "Solo preguntaba"


def test_un_mensaje_nuevo_le_gana_a_la_mano(cliente):
    cliente.post("/control/mover",
                 data={"chat": "m3", "columna": "inactivo",
                       "motivo": "no_contesto"})
    # El cliente escribe DESPUÉS de la marca: el semáforo vuelve a decidir
    # y el chat revive en Esperando respuesta (le deben una).
    control._MUESTRA[2]["fecha"] = "2099-01-01T12:00:00+00:00"
    try:
        _columnas, por_chat = control.tablero()
        assert por_chat["m3"]["columna"] == "esperando"
    finally:
        control._MUESTRA[2]["fecha"] = "2026-09-22T21:10:00+00:00"


def test_la_mano_le_gana_al_semaforo_mientras_no_haya_mensaje(cliente):
    # Kev está en "En curso" (el vivero contestó); el empleado lo pasa a
    # Esperando respuesta a mano y ahí se queda aunque el semáforo diga
    # otra cosa.
    cliente.post("/control/mover", data={"chat": "m2", "columna": "esperando"})
    _columnas, por_chat = control.tablero()
    assert por_chat["m2"]["columna"] == "esperando"


def test_equipo_redirige_a_control(cliente):
    r = cliente.get("/equipo", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/control"


def test_el_crm_pinta_el_tablero_del_admin(cliente):
    cuerpo = cliente.get("/crm").text
    assert "Nuevo" in cuerpo and "Ganado" in cuerpo and "Perdido" in cuerpo
    assert "PP-70211 · Tamara" in cuerpo
    # Un lead con motivo no va en columnas: va en Inactivos.
    assert "Inactivos (1)" in cuerpo
    assert "PP-70190 · Monica Gama" in cuerpo


def test_la_tarjeta_del_crm_dice_donde_esta_en_retail(cliente):
    # Kev (L2, issue LEAD-45) vive en el kanban Retail en "Por cotizar":
    # su tarjeta del CRM lo dice en un label chiquito.
    cuerpo = cliente.get("/crm").text
    assert "Retail · por cotizar" in cuerpo
    # Y su ficha trae el salto directo a esa tarjeta de Retail.
    ficha = cliente.get("/crm?abrir=L2").text
    assert "/retail?abrir=LEAD-45" in ficha


def test_la_ficha_del_crm_trae_lo_del_panel(cliente):
    cuerpo = cliente.get("/crm?abrir=L1").text
    assert "PP-70211 · Tamara" in cuerpo
    assert "¿Tienen calatheas grandes?" in cuerpo   # la conversación
    assert "Registra el porqué" in cuerpo           # el bloque del motivo
    assert "Guardar nota" in cuerpo                 # las notas se escriben


def test_registrar_motivo_sincroniza_los_dos_tableros(cliente):
    # Desde la ficha del CRM se registra el porqué, como en el panel.
    r = cliente.post("/crm/motivo", data={"lead": "L3", "motivo": "DIJO_QUE_NO"},
                     follow_redirects=False)
    assert r.status_code == 303 and "aviso=" in r.headers["location"]
    cuerpo = cliente.get("/crm").text
    assert "Inactivos (2)" in cuerpo  # Monica + NC Renovando
    # Quitarlo lo revive en su columna de siempre.
    cliente.post("/crm/motivo", data={"lead": "L3", "motivo": ""})
    assert "Inactivos (1)" in cliente.get("/crm").text


def test_control_inactivo_se_espeja_en_el_crm(cliente):
    # Tamara (chat m1, lead L1): mandarla a Inactivo en Control registra el
    # motivo en su lead — el admin y la pestaña CRM lo ven igual.
    r = cliente.post("/control/mover",
                     data={"chat": "m1", "columna": "inactivo",
                           "motivo": "solo_preguntaba"},
                     follow_redirects=False)
    assert r.status_code == 303
    _columnas, por_chat = control.tablero()
    assert por_chat["m1"]["columna"] == "inactivo"
    assert "PP-70211 · Tamara" in [t["titulo"] for t in crm_flujo.tablero()[1]]
    # Sacarla de Inactivo en Control quita el motivo del lead: revive en
    # los dos tableros.
    cliente.post("/control/mover", data={"chat": "m1", "columna": "en_curso"})
    assert crm_flujo.tablero()[1] == [t for t in crm_flujo.tablero()[1]
                                      if t["titulo"] != "PP-70211 · Tamara"]


def test_el_drag_del_crm_solo_acepta_los_tres_destinos(cliente):
    # Nuevo y Contactado no son destino del drag: los pone el sistema.
    r = cliente.post("/crm/mover", data={"lead": "L1", "estado": "NUEVO"},
                     follow_redirects=False)
    assert "error=" in r.headers["location"]
    r = cliente.post("/crm/mover", data={"lead": "L1", "estado": "CONTACTADO"},
                     follow_redirects=False)
    assert "error=" in r.headers["location"]
    # A En conversación sí, y el tablero lo refleja.
    r = cliente.post("/crm/mover", data={"lead": "L1", "estado": "EN_CONVERSACION"},
                     follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    columnas, _inactivos = crm_flujo.tablero()
    conversacion = next(c for c in columnas if c["clave"] == "EN_CONVERSACION")
    assert "PP-70211 · Tamara" in [t["titulo"] for t in conversacion["tarjetas"]]
    # Y las tres listas de destino son las únicas que aceptan soltar.
    cuerpo = cliente.get("/crm").text
    assert cuerpo.count("data-estado=") == 3


def test_ganado_por_drag_llega_a_terminado_en_control(cliente):
    # Arrastrar el lead de Kev a Ganado en el CRM manda su chat a
    # Terminado en Control: mismo espejo, mismo dato.
    cliente.post("/crm/mover", data={"lead": "L2", "estado": "GANADO"})
    _columnas, por_chat = control.tablero()
    assert por_chat["m2"]["columna"] == "terminado"


def test_el_motivo_del_admin_llega_a_control(cliente):
    # El admin registra un motivo sobre el lead de Kev (L2): su chat cae
    # solo en Inactivo dentro de Control, sin que nadie lo arrastre aquí.
    crm_flujo.registrar_motivo("L2", "NO_CONTESTO")
    _columnas, por_chat = control.tablero()
    assert por_chat["m2"]["columna"] == "inactivo"
    assert por_chat["m2"]["motivo"] == "No contestó"
    # Y el Ganado del CRM ya mandaba a Soledad a Terminado (mismo espejo).
    assert por_chat["m4"]["columna"] == "terminado"
