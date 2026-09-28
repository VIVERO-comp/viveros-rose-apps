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

import time
from datetime import datetime

import pytest

from app import control, linear_leads


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()
    # Las pruebas de este archivo son de ANTES de que "Por empleado" se
    # apagara una semana (28/09/2026): por defecto corren como si ya
    # hubiera vuelto. Las pruebas de la ventana apagada la prenden a mano.
    monkeypatch.setattr(control, "vista_empleado_apagada", lambda: False)


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas es el dueño (ve las dos vistas y todo).

    `genesis` es la empleada con la que entra el TestClient (conftest).
    """
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


ADMIN = {"vistas": ["empleado", "estado"], "solo_resp": "", "admin": True}
EMPLEADO = {"vistas": ["estado"], "solo_resp": "Ruben", "admin": False}

# La función real, capturada ANTES de que el autouse de arriba la tape con
# un lambda: la necesitan las pruebas que congelan el reloj para probar la
# comparación de fechas de verdad, no el atajo que usa el resto del archivo.
_VISTA_EMPLEADO_APAGADA_REAL = control.vista_empleado_apagada


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

def test_sin_las_variables_el_enganche_esta_apagado(monkeypatch):
    monkeypatch.delenv("SINCRO_URL", raising=False)
    monkeypatch.delenv("SINCRO_SECRET", raising=False)
    assert control.waha_activo() is False
    assert control.etiquetar_en_whatsapp("LEAD-91") is False


def test_hace_falta_la_url_Y_el_secreto(monkeypatch):
    monkeypatch.setenv("SINCRO_URL", "http://10.116.0.3:3002/sincro/lead")
    monkeypatch.delenv("SINCRO_SECRET", raising=False)
    assert control.waha_activo() is False, "con la URL sola no alcanza"
    monkeypatch.setenv("SINCRO_SECRET", "el-secreto")
    assert control.waha_activo() is True


def test_el_enganche_no_bloquea_ni_cuando_el_endpoint_revienta(monkeypatch):
    """La regla del dueño: si el endpoint falla o tarda, Control sigue
    igual. Sale en un hilo y el error solo queda en el log."""
    monkeypatch.setenv("SINCRO_URL", "http://10.116.0.3:3002/sincro/lead")
    monkeypatch.setenv("SINCRO_SECRET", "el-secreto")
    avisos_log = []
    monkeypatch.setattr(control, "registro_aviso", avisos_log.append)

    def revienta(*_a, **_k):
        raise OSError("no hay ruta al host")

    monkeypatch.setattr(control.httpx, "post", revienta)
    # Despachado: vuelve True aunque el endpoint no exista.
    assert control.etiquetar_en_whatsapp("LEAD-91") is True
    import time
    for _ in range(40):
        if avisos_log:
            break
        time.sleep(0.05)
    assert avisos_log and "no contestó" in avisos_log[0]


def test_con_el_endpoint_puesto_el_aviso_manual_se_apaga(monkeypatch):
    """Cuando el sincronizador se encarga, la pantalla deja de pedir el
    aviso manual: ya no hay nada que poner a mano."""
    monkeypatch.setattr(control, "waha_activo", lambda: True)
    monkeypatch.setattr(control, "etiquetar_en_whatsapp", lambda ref: True)
    aviso, error = control.mover_a_empleado("LEAD-91", "Mary")
    assert error == ""
    assert "Pon en WhatsApp" not in aviso
    assert "se está poniendo sola" in aviso


def test_el_lead_que_se_manda_a_sincronizar_es_el_que_se_movio(monkeypatch):
    monkeypatch.setattr(control, "waha_activo", lambda: True)
    pedidos = []
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda ref: pedidos.append(ref) or True)
    control.mover_a_empleado("LEAD-90", "Mary")
    control.mover_a_estado("LEAD-86", "COTIZADO", nota="le pasé el precio")
    assert pedidos == ["LEAD-90", "LEAD-86"]


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
    # UN solo comentario, con la nota y el motivo adentro: dos seguidos
    # diciendo lo mismo solo ensucian el issue.
    notas = linear_leads.comentarios(lead["id"])
    assert len(notas) == 1
    assert "dijo que estaba caro" in notas[0]["texto"]
    assert "motivo: Precio" in notas[0]["texto"]
    assert "Mary" in notas[0]["texto"]


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


def test_la_primera_corrida_solo_toma_nota(con_avisos):
    """Si no, el día del deploy al encargado le suena el celular una vez por
    cada conversación que tenga «Te toca» acumulado."""
    # LEAD-87 y LEAD-86 ya esperan respuesta desde antes.
    assert control.avisar_a_quien_le_toca() == []
    assert con_avisos == []


def test_despues_del_estreno_el_aviso_suena_una_sola_vez(con_avisos):
    control.avisar_a_quien_le_toca()          # el estreno: solo anota
    lead = linear_leads.uno("LEAD-91")         # este no tenía «Te toca»
    linear_leads.poner_te_toca(lead["id"], True)

    sonaron = control.avisar_a_quien_le_toca()
    assert [l["ref"] for l in sonaron] == ["LEAD-91"]
    assert con_avisos == ["Te toca · Tamara"]
    # La segunda pintada de la pantalla no vuelve a sonar.
    assert control.avisar_a_quien_le_toca() == []
    assert len(con_avisos) == 1


def test_cuando_contestamos_el_aviso_se_olvida_y_puede_volver(con_avisos):
    control.avisar_a_quien_le_toca()          # el estreno
    lead = linear_leads.uno("LEAD-87")
    # Nuestra respuesta quita «Te toca»: el acuse se olvida.
    linear_leads.poner_te_toca(lead["id"], False)
    assert control.avisar_a_quien_le_toca() == []
    # El cliente vuelve a escribir: suena de nuevo.
    linear_leads.poner_te_toca(lead["id"], True)
    assert [l["ref"] for l in control.avisar_a_quien_le_toca()] == ["LEAD-87"]
    assert con_avisos == ["Te toca · Ximena Dávila"]


def test_vaciar_la_tabla_no_vuelve_a_parecer_un_estreno(con_avisos):
    """Cuando todos contestan, los acuses se borran — pero el centinela del
    estreno se queda, así que el siguiente que espere sí suena."""
    control.avisar_a_quien_le_toca()          # el estreno
    for ref in ("LEAD-87", "LEAD-86"):
        linear_leads.poner_te_toca(linear_leads.uno(ref)["id"], False)
    control.avisar_a_quien_le_toca()          # se olvidan los dos acuses
    linear_leads.poner_te_toca(linear_leads.uno("LEAD-86")["id"], True)
    assert [l["ref"] for l in control.avisar_a_quien_le_toca()] == ["LEAD-86"]


def test_sin_claves_vapid_no_suena_nada():
    assert control.avisar_a_quien_le_toca() == []


# ---------------------------------------------------------------------------
# El interruptor «🔴 Responder» (25/09/2026) — `prender` viaja explícito
# desde el formulario (28/09/2026): nunca se recalcula acá.
# ---------------------------------------------------------------------------

def test_responder_prende_te_toca_lo_anota_y_sincroniza(monkeypatch):
    pedidos = []
    monkeypatch.setattr(control, "waha_activo", lambda: True)
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda ref: pedidos.append(ref) or True)
    aviso, error = control.alternar_responder("LEAD-90", True, autor="Ruben")
    assert error == ""
    assert "prendido" in aviso
    lead = linear_leads.uno("LEAD-90")
    assert lead["te_toca"] is True
    nota = linear_leads.comentarios(lead["id"])[0]["texto"]
    assert "Responder" in nota and "prendido" in nota and "Ruben" in nota
    assert pedidos == ["LEAD-90"]


def test_responder_se_apaga_y_tambien_queda_anotado(monkeypatch):
    monkeypatch.setattr(control, "waha_activo", lambda: False)
    lead = linear_leads.uno("LEAD-87")  # ya tiene Te toca en la muestra
    assert lead["te_toca"] is True
    aviso, error = control.alternar_responder("LEAD-87", False, autor="Mary")
    assert error == ""
    assert "apagado" in aviso
    lead = linear_leads.uno("LEAD-87")
    assert lead["te_toca"] is False
    nota = linear_leads.comentarios(lead["id"])[-1]["texto"]
    assert "apagado" in nota and "Mary" in nota


def test_responder_sin_waha_no_intenta_sincronizar(monkeypatch):
    monkeypatch.delenv("SINCRO_URL", raising=False)
    monkeypatch.delenv("SINCRO_SECRET", raising=False)
    llamado = []
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda ref: llamado.append(ref) or True)
    control.alternar_responder("LEAD-90", True)
    assert llamado == []


def test_responder_de_un_lead_que_no_existe():
    aviso, error = control.alternar_responder("LEAD-999", True)
    assert aviso == ""
    assert "ya no está en Linear" in error


def test_responder_fija_el_estado_que_pide_el_boton_aunque_ya_este_asi(monkeypatch):
    """El bug de "hay que apretar dos veces": antes se negaba una lectura
    (`not lead["te_toca"]`), así que con una lectura vieja el primer clic
    podía terminar pidiendo lo mismo que ya había. Fijar el estado del
    formulario es idempotente — pedirlo dos veces con la misma intención
    dos veces no rompe nada."""
    linear_leads.poner_te_toca(linear_leads.uno("LEAD-90")["id"], True)
    aviso, error = control.alternar_responder("LEAD-90", True, autor="Ruben")
    assert error == ""
    assert linear_leads.uno("LEAD-90")["te_toca"] is True
    aviso, error = control.alternar_responder("LEAD-90", True, autor="Ruben")
    assert error == ""
    assert linear_leads.uno("LEAD-90")["te_toca"] is True


def test_responder_desde_la_pantalla(cliente, de_dueno):
    respuesta = cliente.post(
        "/control/responder", params={"vista": "estado"},
        data={"ref": "LEAD-90", "prender": "1"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-90")["te_toca"] is True


def test_responder_no_lo_puede_un_empleado_de_otro(cliente):
    respuesta = cliente.post(
        "/control/responder",
        data={"ref": "LEAD-89", "prender": "1"},  # Resp: Mary
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-89")["te_toca"] is False


# ---------------------------------------------------------------------------
# «Responder a mano» (28/09/2026): la segunda etiqueta que Responder
# prende junto a «Te toca», para que nuestra respuesta no lo apague solo.
# ---------------------------------------------------------------------------

def test_prender_pone_te_toca_y_responder_a_mano():
    lead = linear_leads.uno("LEAD-90")
    aviso, error = control.alternar_responder("LEAD-90", True, autor="Ruben")
    assert error == ""
    etiquetas = linear_leads.uno("LEAD-90")["etiquetas"]
    assert "Te toca" in etiquetas
    assert "Responder a mano" in etiquetas


def test_apagar_quita_las_dos():
    lead = linear_leads.uno("LEAD-90")
    control.alternar_responder("LEAD-90", True, autor="Ruben")
    aviso, error = control.alternar_responder("LEAD-90", False, autor="Ruben")
    assert error == ""
    etiquetas = linear_leads.uno("LEAD-90")["etiquetas"]
    assert "Te toca" not in etiquetas
    assert "Responder a mano" not in etiquetas


def test_sin_la_etiqueta_en_el_catalogo_prende_igual_y_lo_avisa(monkeypatch):
    monkeypatch.setattr(linear_leads, "responder_a_mano_disponible", lambda: False)
    aviso, error = control.alternar_responder("LEAD-90", True, autor="Ruben")
    assert error == ""
    lead = linear_leads.uno("LEAD-90")
    assert lead["te_toca"] is True
    assert "Responder a mano" not in lead["etiquetas"]
    # No se intentó crear ni poner: no hay ningún aviso de "no existe" en
    # el log (eso solo lo escribe `_label_id` si de verdad se llamó).
    assert "nuestra respuesta lo va a apagar" in aviso


def test_el_orden_es_te_toca_primero_al_prender_y_al_reves_al_apagar(monkeypatch):
    orden = []
    original_suelta = linear_leads.poner_etiqueta_suelta

    def espia(id_issue, nombre, prendida):
        orden.append((nombre, prendida))
        return original_suelta(id_issue, nombre, prendida)

    monkeypatch.setattr(linear_leads, "poner_etiqueta_suelta", espia)
    control.alternar_responder("LEAD-90", True, autor="Ruben")
    control.alternar_responder("LEAD-90", False, autor="Ruben")
    assert orden == [
        ("Te toca", True), ("Responder a mano", True),
        ("Responder a mano", False), ("Te toca", False),
    ]


# ---------------------------------------------------------------------------
# Las señales sueltas: el mismo interruptor, sin comentario y sin WhatsApp
# ---------------------------------------------------------------------------

def test_senal_disponible_se_prende_y_se_apaga():
    aviso, error = control.alternar_senal("LEAD-90", "Seguimiento", True, autor="Mary")
    assert error == ""
    assert "puesta" in aviso
    assert "Seguimiento" in linear_leads.uno("LEAD-90")["etiquetas"]
    aviso, error = control.alternar_senal("LEAD-90", "Seguimiento", False, autor="Mary")
    assert error == ""
    assert "quitada" in aviso
    assert "Seguimiento" not in linear_leads.uno("LEAD-90")["etiquetas"]


def test_una_senal_sin_etiqueta_en_linear_no_hace_nada():
    # "Cliente potencial" no existe en el catálogo de muestra a propósito:
    # el botón no debería haber aparecido, y si el POST llega igual, acá
    # no se crea nada ni se avisa un error.
    aviso, error = control.alternar_senal(
        "LEAD-90", "Cliente potencial", True, autor="Mary")
    assert aviso == "" and error == ""
    assert "Cliente potencial" not in linear_leads.uno("LEAD-90")["etiquetas"]


def test_las_senales_no_piden_sincronizacion_de_whatsapp(monkeypatch):
    monkeypatch.setattr(control, "waha_activo", lambda: True)
    pedidos = []
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda ref: pedidos.append(ref) or True)
    control.alternar_senal("LEAD-90", "Seguimiento", True, autor="Mary")
    control.alternar_senal("LEAD-90", "Importante", True, autor="Mary")
    assert pedidos == []


def test_las_senales_no_dejan_comentario_en_el_issue():
    lead = linear_leads.uno("LEAD-90")
    antes = len(linear_leads.comentarios(lead["id"]))
    control.alternar_senal("LEAD-90", "Seguimiento", True, autor="Mary")
    despues = len(linear_leads.comentarios(lead["id"]))
    assert despues == antes


def test_senal_fija_el_estado_que_pide_el_boton():
    linear_leads.poner_etiqueta_suelta(
        linear_leads.uno("LEAD-90")["id"], "Seguimiento", True)
    aviso, error = control.alternar_senal("LEAD-90", "Seguimiento", True, autor="Mary")
    assert error == ""
    assert "puesta" in aviso
    assert "Seguimiento" in linear_leads.uno("LEAD-90")["etiquetas"]


def test_la_ficha_trae_solo_las_senales_disponibles_con_su_estado():
    linear_leads.poner_etiqueta_suelta(
        linear_leads.uno("LEAD-90")["id"], "Importante", True)
    ficha = control.ficha("LEAD-90")
    nombres = [s["nombre"] for s in ficha["senales"]]
    # "Cliente potencial" no existe en la muestra: no aparece, y no revienta.
    assert nombres == ["Seguimiento", "Importante"]
    por_nombre = {s["nombre"]: s["prendida"] for s in ficha["senales"]}
    assert por_nombre == {"Seguimiento": False, "Importante": True}


def test_senal_desde_la_pantalla(cliente, de_dueno):
    respuesta = cliente.post(
        "/control/senal", params={"vista": "estado"},
        data={"ref": "LEAD-90", "nombre": "Seguimiento", "prender": "1"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "Seguimiento" in linear_leads.uno("LEAD-90")["etiquetas"]


def test_una_senal_a_mano_por_una_etiqueta_inexistente_no_crea_nada(cliente, de_dueno):
    # El candado no es solo del navegador: un POST a mano con un nombre que
    # no está en el catálogo tampoco toca el issue.
    respuesta = cliente.post(
        "/control/senal", params={"vista": "estado"},
        data={"ref": "LEAD-90", "nombre": "Cliente potencial", "prender": "1"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "Cliente potencial" not in linear_leads.uno("LEAD-90")["etiquetas"]


# ---------------------------------------------------------------------------
# El orden dentro de cada columna: Te toca primero, y el que más espera
# arriba (25/09/2026)
# ---------------------------------------------------------------------------

def _columna(clave):
    columnas = control.tablero_por_estado()
    return next(c for c in columnas if c["clave"] == clave)


def test_te_toca_va_primero_aunque_el_resto_sea_mas_viejo():
    # LEAD-90 (2 días) y LEAD-91 (1 día) caen los dos en Por agendar; sin
    # tocar nada, LEAD-90 iría primero (es el más viejo). Con Te toca en
    # LEAD-91, este pasa adelante igual.
    linear_leads.poner_te_toca(linear_leads.uno("LEAD-91")["id"], True)
    refs = [l["ref"] for l in _columna("POR_AGENDAR")["leads"]]
    assert refs == ["LEAD-91", "LEAD-90"]


def test_entre_dos_te_toca_el_que_mas_espera_manda():
    for ref in ("LEAD-91", "LEAD-90"):
        linear_leads.poner_te_toca(linear_leads.uno(ref)["id"], True)
    ahora = time.time()
    control._guardar_espera("LEAD-91", ahora - 60)     # hace 1 minuto
    control._guardar_espera("LEAD-90", ahora - 3600)   # hace 1 hora: manda
    refs = [l["ref"] for l in _columna("POR_AGENDAR")["leads"]]
    assert refs == ["LEAD-90", "LEAD-91"]


def test_sin_cache_el_desempate_es_createdat_no_updatedat():
    # Con la caché fría (sin filas en control_espera), el desempate cae en
    # el mismo `dias` que ya usa el resto de la pantalla — nunca un orden
    # que baile de una recarga a otra.
    for ref in ("LEAD-91", "LEAD-90"):
        linear_leads.poner_te_toca(linear_leads.uno(ref)["id"], True)
    refs = [l["ref"] for l in _columna("POR_AGENDAR")["leads"]]
    assert refs == ["LEAD-90", "LEAD-91"]  # LEAD-90 (2 días) es el más viejo


def test_los_sin_te_toca_mantienen_su_orden_de_siempre():
    # Ninguno de los dos tiene Te toca: el orden no cambia por esta feature.
    refs = [l["ref"] for l in _columna("POR_AGENDAR")["leads"]]
    assert refs == ["LEAD-91", "LEAD-90"]


def test_la_pantalla_se_pinta_igual_con_la_cache_vacia(cliente, de_dueno):
    # No se sembró ninguna fila en control_espera: el fallback por createdAt
    # tiene que alcanzar solo, sin que la pantalla reviente.
    cuerpo = cliente.get("/control", params={"vista": "estado"}).text
    assert "Tamara" in cuerpo and "Juan Carlos Lopez" in cuerpo


# ---------------------------------------------------------------------------
# «Por empleado» apagada una semana (28/09/2026, pedido de Abraham): el
# candado va en el servidor (`alcance()`/`vista_pedida()`), no solo en el
# botón que no se pinta.
# ---------------------------------------------------------------------------

def test_apagada_vista_pedida_cae_a_estado_incluso_para_el_dueno(monkeypatch):
    monkeypatch.setattr(control, "vista_empleado_apagada", lambda: True)
    alc = control.alcance({"id": "abraham"}, es_admin=True)
    assert alc["vistas"] == ["estado"]
    assert control.vista_pedida("empleado", alc) == "estado"


def test_llegada_la_fecha_vuelve_a_funcionar_sola(monkeypatch):
    """Congela "ahora" en la fecha en que vuelve — no depende del reloj
    real, así esta prueba sigue significando lo mismo después del 5 de
    octubre de 2026."""
    class _RelojFijo(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 9, 0, tzinfo=tz)

    monkeypatch.setattr(control, "vista_empleado_apagada", _VISTA_EMPLEADO_APAGADA_REAL)
    monkeypatch.setattr(control, "datetime", _RelojFijo)
    assert control.vista_empleado_apagada() is False
    alc = control.alcance({"id": "abraham"}, es_admin=True)
    assert alc["vistas"] == ["empleado", "estado"]


def test_todavia_apagada_un_dia_antes(monkeypatch):
    class _RelojFijo(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 4, 23, 59, tzinfo=tz)

    monkeypatch.setattr(control, "vista_empleado_apagada", _VISTA_EMPLEADO_APAGADA_REAL)
    monkeypatch.setattr(control, "datetime", _RelojFijo)
    assert control.vista_empleado_apagada() is True


def test_el_boton_de_por_empleado_no_se_pinta_mientras_esta_apagada(
        cliente, de_dueno, monkeypatch):
    monkeypatch.setattr(control, "vista_empleado_apagada", lambda: True)
    cuerpo = cliente.get("/control").text
    assert "Por empleado" not in cuerpo


def test_el_reparto_por_detras_sigue_andando_aunque_la_vista_este_apagada(
        monkeypatch):
    """Se apaga LA VISTA, no el reparto: `mover_a_empleado` (la etiqueta
    `Resp:`) no se toca."""
    monkeypatch.setattr(control, "vista_empleado_apagada", lambda: True)
    aviso, error = control.mover_a_empleado("LEAD-90", "Mary")
    assert error == ""
    assert linear_leads.uno("LEAD-90")["resp"] == "Mary"
