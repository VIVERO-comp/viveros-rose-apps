"""La pestaña Control, Fase 5: reparte el trabajo del equipo.

Corren en modo muestra (sin LINEAR_API_KEY), así que el tablero del equipo
LEAD vive en memoria. Lo que se cuida aquí es lo que duele si se rompe:
que Control no guarde nada propio, que repartir cambie la etiqueta `Resp:`
y nunca el assignee, que un empleado vea y mueva SOLO lo suyo (verificado
en el servidor), y que una corrección de estado a mano no pase sin motivo.

Los leads de muestra (app/linear_leads.py):

    LEAD-91  Tamara              Por agendar  Resp: Ruben
    LEAD-90  Juan Carlos Lopez   Por agendar  sin Resp:
    LEAD-89  Boda Las Nubes      Agendado     Resp: Mary
    LEAD-88  Hotel Bristol       Entregado    Resp: Ruben
    LEAD-87  Ximena Dávila       Cotizado     sin Resp:, Te toca
    LEAD-86  Nedjaira            Hablando     Resp: Salomón, Te toca
    LEAD-85  Diego Armando       Nuevo        sin Resp:
    LEAD-84  Soledad             Ganado       Resp: Abraham
    LEAD-83  Monica Gama         Perdido      sin Resp:
"""

import pytest

from app import control, linear_leads


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas es el dueño (ve las dos vistas y todo).

    `genesis` es la empleada con la que entra el TestClient (conftest).
    """
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


ADMIN = {"vistas": ["empleado", "estado"], "solo_resp": "", "admin": True}
EMPLEADO = {"vistas": ["estado"], "solo_resp": "Ruben", "admin": False}


# ---------------------------------------------------------------------------
# Control no guarda nada propio
# ---------------------------------------------------------------------------

def test_lo_unico_que_guarda_es_el_acuse_de_los_avisos():
    from app.datos import _db
    with _db() as con:
        tablas = {f[0] for f in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "control_acuse" in tablas
    # Las tablas del kanban viejo ya no se crean: el estado vive en Linear.
    assert "control_tablero" not in tablas
    assert "control_visto" not in tablas


# ---------------------------------------------------------------------------
# Vista por empleado
# ---------------------------------------------------------------------------

def test_las_columnas_son_las_etiquetas_de_responsable():
    columnas = control.tablero_por_empleado()
    assert [c["titulo"] for c in columnas] == [
        "Sin asignar", "Abraham", "Mary", "Ruben", "Salomón"]


def test_cada_lead_cae_en_la_columna_de_su_responsable():
    por_titulo = {c["titulo"]: {l["ref"] for l in c["leads"]}
                  for c in control.tablero_por_empleado()}
    assert por_titulo["Ruben"] == {"LEAD-91", "LEAD-88"}
    assert por_titulo["Mary"] == {"LEAD-89"}
    assert por_titulo["Sin asignar"] == {"LEAD-90", "LEAD-87", "LEAD-85"}


def test_los_cerrados_no_se_reparten():
    # Soledad (Ganado) y Monica (Perdido) no tienen trabajo que hacerles.
    todos = {l["ref"] for c in control.tablero_por_empleado() for l in c["leads"]}
    assert "LEAD-84" not in todos and "LEAD-83" not in todos


# ---------------------------------------------------------------------------
# Vista por estado
# ---------------------------------------------------------------------------

def test_la_vista_por_estado_trae_las_8_columnas_del_embudo():
    columnas = control.tablero_por_estado()
    assert [c["titulo"] for c in columnas] == [
        "Nuevo", "Hablando", "Cotizado", "Por agendar",
        "Agendado", "Entregado", "Ganado", "Perdido"]


def test_un_empleado_solo_ve_lo_suyo():
    columnas = control.tablero_por_estado(solo_resp="Ruben")
    refs = {l["ref"] for c in columnas for l in c["leads"]}
    assert refs == {"LEAD-91", "LEAD-88"}


def test_un_empleado_sin_etiqueta_resp_no_ve_nada():
    # Correcto: todavía no le toca ningún lead.
    assert control.alcance({"id": "nadie"}, es_admin=False)["solo_resp"] == ""


# ---------------------------------------------------------------------------
# El alcance: quién ve y quién toca qué (decidido en el servidor)
# ---------------------------------------------------------------------------

def test_el_dueno_ve_las_dos_vistas_y_todo():
    alc = control.alcance({"id": "abraham"}, es_admin=True)
    assert alc == ADMIN


def test_el_empleado_solo_tiene_la_vista_por_estado():
    alc = control.alcance(
        {"id": "ruben", "nombre": "Rubén", "email": "ruben@viverorose.com",
         "email_verificado": True}, es_admin=False)
    assert alc["vistas"] == ["estado"]
    assert alc["solo_resp"] == "Ruben"
    assert alc["admin"] is False


def test_una_vista_que_no_le_toca_cae_en_la_suya():
    assert control.vista_pedida("empleado", EMPLEADO) == "estado"
    assert control.vista_pedida("", ADMIN) == "empleado"
    assert control.vista_pedida("estado", ADMIN) == "estado"


def test_un_empleado_no_puede_tocar_un_lead_de_otro():
    mio = linear_leads.uno("LEAD-91")      # Resp: Ruben
    de_otro = linear_leads.uno("LEAD-89")  # Resp: Mary
    de_nadie = linear_leads.uno("LEAD-90")
    assert control.puede_tocar(mio, EMPLEADO) is True
    assert control.puede_tocar(de_otro, EMPLEADO) is False
    assert control.puede_tocar(de_nadie, EMPLEADO) is False
    # El dueño, todo.
    for lead in (mio, de_otro, de_nadie):
        assert control.puede_tocar(lead, ADMIN) is True


# ---------------------------------------------------------------------------
# Repartir: la etiqueta `Resp:`, nunca el assignee
# ---------------------------------------------------------------------------

def test_repartir_cambia_la_etiqueta_resp():
    aviso, error = control.mover_a_empleado("LEAD-90", "Mary", autor="Abraham")
    assert error == ""
    lead = linear_leads.uno("LEAD-90")
    assert lead["resp"] == "Mary"
    assert "Resp: Mary" in lead["etiquetas"]
    assert "Juan Carlos Lopez es de Mary" in aviso


def test_repartir_avisa_de_la_etiqueta_de_whatsapp_una_sola_vez():
    # El aviso manual existe porque OpenWA (baileys) no soporta etiquetas;
    # se da UNA vez por lead y responsable, y no en cada recarga.
    aviso, _e = control.mover_a_empleado("LEAD-91", "Mary")
    assert "Pon en WhatsApp la etiqueta: Mary" in aviso
    assert "quita la de Ruben" in aviso
    # Volver a ponerlo en Ruben y de vuelta en Mary: el acuse del par se
    # olvidó al cambiar de manos, así que el aviso vuelve a salir.
    control.mover_a_empleado("LEAD-91", "Ruben")
    aviso, _e = control.mover_a_empleado("LEAD-91", "Mary")
    assert "Pon en WhatsApp la etiqueta: Mary" in aviso


def test_el_mismo_reparto_repetido_no_repite_el_aviso():
    control.mover_a_empleado("LEAD-90", "Mary")
    # Sin cambio de manos no hay nada que avisar.
    aviso, error = control.mover_a_empleado("LEAD-90", "Mary")
    assert aviso == "" and error == ""


def test_quitar_el_responsable_lo_deja_sin_asignar():
    aviso, error = control.mover_a_empleado("LEAD-91", "")
    assert error == ""
    assert "sin asignar" in aviso
    assert linear_leads.uno("LEAD-91")["resp"] == ""


def test_un_responsable_que_no_existe_en_linear_se_rechaza():
    # Las etiquetas no se crean solas: aquí se dice en vez de inventarla.
    aviso, error = control.mover_a_empleado("LEAD-91", "Fulano")
    assert aviso == ""
    assert "No existe la etiqueta «Resp: Fulano»" in error
    assert linear_leads.uno("LEAD-91")["resp"] == "Ruben"


# ---------------------------------------------------------------------------
# El enganche para WAHA (Fase W): hoy vacío a propósito
# ---------------------------------------------------------------------------

def test_el_enganche_de_whatsapp_esta_puesto_pero_apagado():
    assert control.waha_activo() is False
    assert control.etiquetar_en_whatsapp("6552-0966", "Ruben") is False


def test_con_waha_andando_el_aviso_manual_se_apaga(monkeypatch):
    """El día que WAHA ande, se llena etiquetar_en_whatsapp() y el aviso
    manual desaparece sin tocar nada más. Esto lo demuestra."""
    monkeypatch.setattr(control, "waha_activo", lambda: True)
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda celular, etiqueta: True)
    aviso, error = control.mover_a_empleado("LEAD-91", "Mary")
    assert error == ""
    assert "Pon en WhatsApp" not in aviso
    assert "La etiqueta de WhatsApp quedó puesta" in aviso


# ---------------------------------------------------------------------------
# Corregir el estado a mano: exige motivo y queda firmado
# ---------------------------------------------------------------------------

def test_corregir_el_estado_exige_motivo():
    aviso, error = control.mover_a_estado("LEAD-86", "COTIZADO", nota="")
    assert aviso == ""
    assert "por qué la moviste" in error
    assert linear_leads.uno("LEAD-86")["estado"] == "HABLANDO"


def test_corregir_el_estado_queda_anotado_en_el_issue():
    aviso, error = control.mover_a_estado(
        "LEAD-86", "COTIZADO", nota="le pasé el precio por teléfono",
        autor="Ruben")
    assert error == ""
    assert "Hablando → Cotizado" in aviso
    lead = linear_leads.uno("LEAD-86")
    assert lead["estado"] == "COTIZADO"
    nota = linear_leads.comentarios(lead["id"])[0]["texto"]
    assert "le pasé el precio por teléfono" in nota
    assert "Ruben" in nota


def test_corregir_hacia_atras_se_puede_a_mano():
    # El automático nunca degrada; una corrección a mano sí.
    aviso, error = control.mover_a_estado(
        "LEAD-88", "AGENDADO", nota="no se entregó, me equivoqué", autor="Mary")
    assert error == ""
    assert linear_leads.uno("LEAD-88")["estado"] == "AGENDADO"


def test_perdido_a_mano_pide_su_motivo_de_perdida():
    aviso, error = control.mover_a_estado(
        "LEAD-85", "PERDIDO", nota="no volvió a escribir")
    assert aviso == ""
    assert "Falta el motivo" in error
    assert linear_leads.uno("LEAD-85")["estado"] == "NUEVO"


def test_perdido_con_motivo_pone_su_etiqueta():
    aviso, error = control.mover_a_estado(
        "LEAD-85", "PERDIDO", nota="dijo que estaba caro",
        motivo="PRECIO", autor="Mary")
    assert error == ""
    lead = linear_leads.uno("LEAD-85")
    assert lead["estado"] == "PERDIDO"
    assert lead["motivo"] == "Precio"


def test_un_estado_inventado_se_rechaza():
    aviso, error = control.mover_a_estado(
        "LEAD-86", "CONTACTADO", nota="algo")
    assert aviso == ""
    assert "no existe" in error


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def test_el_dueno_abre_en_por_empleado(cliente, de_dueno):
    cuerpo = cliente.get("/control").text
    assert "Sin asignar" in cuerpo
    assert "Por empleado" in cuerpo and "Por estado" in cuerpo
    assert "Tamara" in cuerpo
    # Las columnas de los responsables, sacadas de las etiquetas de Linear.
    for nombre in ("Abraham", "Mary", "Ruben", "Salomón"):
        assert nombre in cuerpo


def test_la_vista_por_estado_pinta_las_8_columnas(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    for titulo in ("Nuevo", "Hablando", "Cotizado", "Por agendar",
                   "Agendado", "Entregado", "Ganado", "Perdido"):
        assert titulo in cuerpo


def test_el_empleado_no_ve_el_segmento_de_vistas(cliente):
    # Sin AJUSTES_ADMINS la sesión de prueba no es admin.
    cuerpo = cliente.get("/control").text
    assert "Por empleado" not in cuerpo


def test_la_ficha_ensena_el_lead_y_sus_notas(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    assert "Tamara" in cuerpo
    assert "Ruben" in cuerpo
    assert "6552-0966" in cuerpo
    assert "Se lo doy a" in cuerpo
    assert "Corregir el estado" in cuerpo


def test_repartir_desde_la_pantalla(cliente, de_dueno):
    respuesta = cliente.post("/control/responsable",
                             params={"vista": "empleado"},
                             data={"ref": "LEAD-90", "resp": "Mary"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-90")["resp"] == "Mary"


def test_repartir_no_lo_puede_un_empleado(cliente):
    respuesta = cliente.post("/control/responsable",
                             data={"ref": "LEAD-91", "resp": "Mary"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-91")["resp"] == "Ruben"


def test_el_arrastre_entre_estados_pasa_por_el_modal_del_motivo(cliente, de_dueno):
    # El drag manda ref + estado y NADA más: el servidor lo desvía al modal.
    respuesta = cliente.post("/control/estado", params={"vista": "estado"},
                             data={"ref": "LEAD-86", "estado": "COTIZADO"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert "mover=LEAD-86" in destino and "a=COTIZADO" in destino
    assert linear_leads.uno("LEAD-86")["estado"] == "HABLANDO"
    # Y el modal pide el motivo.
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "mover": "LEAD-86", "a": "COTIZADO"}).text
    assert "¿Por qué la movés?" in cuerpo
    assert "Hablando → Cotizado" in cuerpo


def test_el_modal_de_perdido_ofrece_los_seis_motivos(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={
        "vista": "estado", "mover": "LEAD-85", "a": "PERDIDO"}).text
    for texto in ("Precio", "No respondió", "Sin stock", "Fuera de zona",
                  "Compró en otro lado", "Solo preguntaba"):
        assert texto in cuerpo


def test_mover_con_motivo_desde_la_pantalla(cliente, de_dueno):
    respuesta = cliente.post(
        "/control/estado", params={"vista": "estado"},
        data={"ref": "LEAD-86", "estado": "COTIZADO",
              "nota": "le pasé el precio por teléfono"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert linear_leads.uno("LEAD-86")["estado"] == "COTIZADO"


def test_un_empleado_no_mueve_el_lead_de_otro_desde_la_pantalla(cliente):
    respuesta = cliente.post(
        "/control/estado",
        data={"ref": "LEAD-89", "estado": "ENTREGADO", "nota": "porque sí"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-89")["estado"] == "AGENDADO"


def test_una_nota_desde_la_pantalla_cae_en_el_issue(cliente, de_dueno):
    respuesta = cliente.post("/control/nota", params={"vista": "estado"},
                             data={"ref": "LEAD-91", "texto": "llamar antes de ir"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    lead = linear_leads.uno("LEAD-91")
    assert "llamar antes de ir" in linear_leads.comentarios(lead["id"])[0]["texto"]


def test_equipo_redirige_a_control(cliente):
    respuesta = cliente.get("/equipo", follow_redirects=False)
    assert respuesta.status_code == 308
    assert respuesta.headers["location"] == "/control"


# ---------------------------------------------------------------------------
# El aviso al celular: una sola vez por «Te toca»
# ---------------------------------------------------------------------------

@pytest.fixture
def con_avisos(monkeypatch):
    mandados = []
    monkeypatch.setattr(control.avisos, "configurado", lambda: True)
    monkeypatch.setattr(
        control.avisos, "avisar",
        lambda usuario, titulo, cuerpo, ruta: mandados.append(titulo))
    return mandados


def test_el_aviso_suena_una_sola_vez_por_lead(con_avisos):
    sonaron = control.avisar_a_quien_le_toca()
    assert {l["ref"] for l in sonaron} == {"LEAD-87", "LEAD-86"}
    assert len(con_avisos) == 2
    # La segunda pintada de la pantalla no vuelve a sonar.
    assert control.avisar_a_quien_le_toca() == []
    assert len(con_avisos) == 2


def test_cuando_contestamos_el_aviso_se_olvida_y_puede_volver(con_avisos):
    control.avisar_a_quien_le_toca()
    lead = linear_leads.uno("LEAD-87")
    # Nuestra respuesta quita «Te toca»: el acuse se olvida.
    linear_leads.poner_te_toca(lead["id"], False)
    assert control.avisar_a_quien_le_toca() == []
    # El cliente vuelve a escribir: suena de nuevo.
    linear_leads.poner_te_toca(lead["id"], True)
    assert [l["ref"] for l in control.avisar_a_quien_le_toca()] == ["LEAD-87"]


def test_sin_claves_vapid_no_suena_nada():
    assert control.avisar_a_quien_le_toca() == []
