"""El hilo de la ficha del lead (Fase C, 25/09/2026).

Lo que se cuida aquí es lo que se rompe callado:

- que los mensajes seguidos del mismo autor vayan bajo UNA firma (si esto
  se rompe, una tanda de diez mensajes de Mary repite su nombre diez veces
  y empuja la conversación fuera de la pantalla),
- que un saliente sin autor diga «Vivero» y nunca invente un nombre,
- que un suceso del sistema entre en su lugar por fecha, y
- que una nota de una persona NO se cuele en el hilo: el cliente no la vio.
"""

import pytest

from app import control, linear_leads


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()


def m(fecha, texto, salida=False, autor="", dia="Jueves 24 sep", hora="08:00"):
    return {"fecha": fecha, "texto": texto, "salida": salida,
            "autor": autor, "dia": dia, "hora": hora}


def nota(texto, fecha="2026-09-24T14:00:00Z", quien="Vivero rose"):
    return {"texto": texto, "fecha": fecha, "quien": quien, "cuando": fecha[:10]}


# ---------------------------------------------------------------------------
# Agrupar: una firma por tanda
# ---------------------------------------------------------------------------

def test_los_mensajes_seguidos_del_mismo_autor_van_bajo_una_firma():
    bloques = control.hilo([
        m("2026-09-24T13:24:00Z", "Buenos días", salida=True, autor="Mary", hora="08:24"),
        m("2026-09-24T13:27:00Z", "Sí tenemos", salida=True, autor="Mary", hora="08:27"),
        m("2026-09-24T13:28:00Z", "Los anturios en 16$", salida=True, autor="Mary", hora="08:28"),
    ], nombre_cliente="Jenny Londoño")
    grupos = [b for b in bloques if b["tipo"] == "grupo"]
    assert len(grupos) == 1
    assert grupos[0]["nombre"] == "Mary"
    assert len(grupos[0]["mensajes"]) == 3


def test_cambiar_de_autor_abre_otro_grupo():
    bloques = control.hilo([
        m("2026-09-24T13:24:00Z", "Buenos días", salida=True, autor="Mary"),
        m("2026-09-24T13:26:00Z", "¿Tienen anturios?"),
        m("2026-09-24T13:27:00Z", "Sí tenemos", salida=True, autor="Mary"),
    ], nombre_cliente="Jenny Londoño")
    grupos = [b for b in bloques if b["tipo"] == "grupo"]
    assert [g["nombre"] for g in grupos] == ["Mary", "Jenny Londoño", "Mary"]


def test_el_cliente_va_a_la_izquierda_y_el_equipo_a_la_derecha():
    bloques = control.hilo([
        m("2026-09-24T13:05:00Z", "Hola"),
        m("2026-09-24T13:24:00Z", "Buenos días", salida=True, autor="Mary"),
    ], nombre_cliente="Jenny Londoño")
    grupos = [b for b in bloques if b["tipo"] == "grupo"]
    assert grupos[0]["mio"] is False
    assert grupos[1]["mio"] is True


def test_cada_dia_abre_su_separador():
    bloques = control.hilo([
        m("2026-09-23T13:05:00Z", "Hola", dia="Miércoles 23 sep"),
        m("2026-09-24T13:24:00Z", "Buenos días", dia="Jueves 24 sep"),
    ], nombre_cliente="Jenny Londoño")
    dias = [b["texto"] for b in bloques if b["tipo"] == "dia"]
    assert dias == ["Miércoles 23 sep", "Jueves 24 sep"]


def test_el_mismo_autor_en_dos_dias_se_vuelve_a_firmar():
    bloques = control.hilo([
        m("2026-09-23T13:24:00Z", "Buenas", salida=True, autor="Mary", dia="Miércoles 23 sep"),
        m("2026-09-24T13:24:00Z", "Buenos días", salida=True, autor="Mary", dia="Jueves 24 sep"),
    ], nombre_cliente="Jenny Londoño")
    assert len([b for b in bloques if b["tipo"] == "grupo"]) == 2


# ---------------------------------------------------------------------------
# Nunca inventar un nombre
# ---------------------------------------------------------------------------

def test_un_saliente_sin_autor_dice_vivero_y_no_inventa_un_nombre():
    bloques = control.hilo(
        [m("2026-09-24T13:24:00Z", "Buenos días", salida=True, autor="")],
        nombre_cliente="Jenny Londoño")
    grupo = [b for b in bloques if b["tipo"] == "grupo"][0]
    assert grupo["nombre"] == "Vivero"
    assert grupo["desconocido"] is True


def test_un_dispositivo_sin_dueno_se_marca_y_su_avatar_lleva_el_numero():
    bloques = control.hilo(
        [m("2026-09-24T13:24:00Z", "Va mañana", salida=True,
           autor="Equipo · dispositivo 4")],
        nombre_cliente="Jenny Londoño")
    grupo = [b for b in bloques if b["tipo"] == "grupo"][0]
    assert grupo["desconocido"] is True
    assert grupo["iniciales"] == "?4"


def test_lo_que_manda_el_sistema_se_marca_aparte():
    bloques = control.hilo(
        [m("2026-09-24T13:24:00Z", "…", salida=True, autor="Sistema")],
        nombre_cliente="Jenny Londoño")
    grupo = [b for b in bloques if b["tipo"] == "grupo"][0]
    assert grupo["sistema"] is True
    assert grupo["desconocido"] is False


@pytest.mark.parametrize("nombre,esperado", [
    ("Jenny Londoño", "JL"),
    ("Mary", "MA"),
    ("Teléfono", "TE"),
    ("Equipo · dispositivo 4", "?4"),
    ("NC Renovando Vidas", "NR"),
    ("🤍", "?"),
])
def test_las_iniciales_del_avatar(nombre, esperado):
    assert control._iniciales(nombre) == esperado


# ---------------------------------------------------------------------------
# Los sucesos del sistema, en el hilo
# ---------------------------------------------------------------------------

def test_un_suceso_entra_en_su_lugar_por_fecha():
    bloques = control.hilo(
        mensajes=[m("2026-09-24T13:05:00Z", "Hola"),
                  m("2026-09-24T20:00:00Z", "Gracias")],
        sucesos=[nota("Cotización S00089 generada · $55.00",
                      fecha="2026-09-24T19:58:00Z")],
        nombre_cliente="Jenny Londoño")
    tipos = [b["tipo"] for b in bloques if b["tipo"] != "dia"]
    assert tipos == ["grupo", "suceso", "grupo"]
    suceso = [b for b in bloques if b["tipo"] == "suceso"][0]
    assert "S00089" in suceso["texto"]


def test_un_suceso_corta_la_tanda_y_lo_de_despues_se_vuelve_a_firmar():
    bloques = control.hilo(
        mensajes=[m("2026-09-24T13:05:00Z", "uno", salida=True, autor="Mary"),
                  m("2026-09-24T20:00:00Z", "dos", salida=True, autor="Mary")],
        sucesos=[nota("Cotización S00089 generada", fecha="2026-09-24T19:00:00Z")],
        nombre_cliente="Jenny Londoño")
    assert len([b for b in bloques if b["tipo"] == "grupo"]) == 2


# ---------------------------------------------------------------------------
# Las notas internas van APARTE
# ---------------------------------------------------------------------------

def test_la_nota_de_una_persona_no_entra_al_hilo():
    firmada = nota("Confirmar camioneta.\n\n_— Mary desde Control Viverorose_")
    sucesos, internas = control.separar_notas([firmada])
    assert sucesos == []
    assert internas == [firmada]


def test_lo_que_escribe_el_sistema_si_entra_al_hilo():
    del_sistema = nota("Cotización S00089 generada · $55.00")
    sucesos, internas = control.separar_notas([del_sistema])
    assert sucesos == [del_sistema]
    assert internas == []


def test_el_eco_de_quien_respondio_no_se_repite_en_el_hilo():
    """La Fase B anota «Mary respondió: «…»» en Linear. Es cierto, pero el
    hilo ya muestra ese mismo mensaje con el nombre de Mary encima."""
    sucesos, internas = control.separar_notas([
        nota("Mary respondió: «Buenos días»"),
        nota("Cotización S00089 generada"),
    ])
    assert [s["texto"] for s in sucesos] == ["Cotización S00089 generada"]
    assert internas == []


# ---------------------------------------------------------------------------
# El chat es un extra: sin Twenty, la ficha se pinta igual
# ---------------------------------------------------------------------------

def test_sin_twenty_la_ficha_vive_y_dice_que_no_hay_chat(monkeypatch):
    monkeypatch.delenv("TWENTY_API_URL", raising=False)
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    abierta = control.ficha("LEAD-87")
    assert abierta is not None
    assert abierta["hay_chat"] is False
    assert abierta["hilo"] == []


def test_si_twenty_revienta_la_ficha_sigue_en_pie(monkeypatch):
    def explota(lead):
        raise RuntimeError("Twenty no contesta")
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead", explota)
    with pytest.raises(RuntimeError):
        control.crm_twenty.ficha_de_lead({})      # el doble está puesto
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead", lambda lead: None)
    abierta = control.ficha("LEAD-87")
    assert abierta["hay_chat"] is False


# ---------------------------------------------------------------------------
# La pantalla: el panel en el orden de C1
# ---------------------------------------------------------------------------

@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def test_el_panel_va_en_el_orden_de_c1(cliente, de_dueno):
    """Cabecera, botones, cotización, conversación, notas y datos al final.

    El orden es la decisión de diseño del 25/09/2026 (C1): se mira de
    arriba abajo en el orden en que se usa, y los datos, que se consultan
    y no se leen, quedan de último.
    """
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    orden = [panel.index(t) for t in (
        "🔴 Responder",
        "Sin cotización conectada",
        "Conversación",
        "Notas internas",
        "Responsable",
    )]
    assert orden == sorted(orden)


def test_los_tres_botones_estan_reservados_y_apagados(cliente, de_dueno):
    """Los construye la sesión de Control: aquí solo se guarda su lugar."""
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    for boton in ("🔴 Responder", "Adjuntar venta"):
        assert boton in panel
        trozo = panel[panel.index(boton) - 90:panel.index(boton)]
        assert "disabled" in trozo


def test_cotizar_solo_asoma_en_nuevo_y_hablando(cliente, de_dueno):
    hablando = cliente.get("/control", params={"abrir": "LEAD-86"}).text
    agendado = cliente.get("/control", params={"abrir": "LEAD-89"}).text
    assert ">Cotizar<" in hablando[hablando.index('class="panel-der"'):]
    assert ">Cotizar<" not in agendado[agendado.index('class="panel-der"'):]


def test_sin_chat_lo_dice_en_vez_de_dejar_el_hueco(cliente, de_dueno):
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    assert "Sin chat disponible." in cuerpo


def test_las_notas_no_se_mezclan_con_la_conversacion(cliente, de_dueno):
    """El rótulo dice de quién son: el cliente no vio ninguna."""
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    assert "Notas internas · solo el equipo" in cuerpo


# ---------------------------------------------------------------------------
# El candado de la instancia de pruebas
#
# El .env de control-stock-pruebas TIENE SINCRO_URL y SINCRO_SECRET, así que
# en cuanto ese contenedor se reinicie, `waha_activo()` pasa a True allá.
# Lo que no puede pasar nunca es que pruebas le ponga una etiqueta al
# WhatsApp de verdad: la escritura en Linear se cierra antes
# (CALENDARIO_ESCRITURA=0) y el camino ni siquiera llega al enganche.
# ---------------------------------------------------------------------------

def _pruebas_en_lectura(monkeypatch):
    """Una instancia configurada contra Linear pero SIN permiso de escribir,
    y con el enganche de WhatsApp encendido."""
    monkeypatch.setattr(linear_leads, "configurado", lambda: True)
    monkeypatch.setattr(linear_leads, "escritura_activa", lambda: False)
    monkeypatch.setenv("SINCRO_URL", "http://10.116.0.3:3002/sincronizar")
    monkeypatch.setenv("SINCRO_SECRET", "no-importa-el-valor")


def test_en_lectura_repartir_no_toca_whatsapp(monkeypatch):
    _pruebas_en_lectura(monkeypatch)
    assert control.waha_activo() is True
    llamadas = []
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda ref: llamadas.append(ref))
    monkeypatch.setattr(linear_leads, "uno",
                        lambda ref, leads=None: {"id": "i", "ref": ref,
                                                 "nombre": "Tamara", "resp": ""})
    monkeypatch.setattr(linear_leads, "responsables", lambda: ["Mary"])
    aviso, error = control.mover_a_empleado("LEAD-91", "Mary")
    assert not aviso and "no escribe en" in error
    assert llamadas == []


def test_en_lectura_corregir_el_estado_no_toca_whatsapp(monkeypatch):
    _pruebas_en_lectura(monkeypatch)
    llamadas = []
    monkeypatch.setattr(control, "etiquetar_en_whatsapp",
                        lambda ref: llamadas.append(ref))
    monkeypatch.setattr(linear_leads, "uno",
                        lambda ref, leads=None: {"id": "i", "ref": ref,
                                                 "nombre": "Tamara",
                                                 "estado": "HABLANDO"})
    aviso, error = control.mover_a_estado("LEAD-91", "COTIZADO",
                                          nota="probando en pruebas")
    assert not aviso and "no escribe en" in error
    assert llamadas == []


# ---------------------------------------------------------------------------
# Los ecos, medidos contra el tablero real del 25/09/2026
#
# De los 100 comentarios del sistema que había ese día, 96 eran «Volvió a
# escribir por WhatsApp: «…»»: uno por cada mensaje entrante, justo debajo
# del mensaje que repetían. Los otros cuatro sí cuentan algo que la
# conversación no dice, y esos son los que el hilo existe para mostrar.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "Volvió a escribir por WhatsApp: «Sería la pink princess»",
    "Volvió a escribir por WhatsApp (PP-L4779)",
    "Mary respondió: «Buenos días»",
])
def test_los_ecos_no_entran_al_hilo(texto):
    sucesos, internas = control.separar_notas([nota(texto)])
    assert sucesos == [] and internas == []


@pytest.mark.parametrize("texto", [
    "🧾 S00089 · $55.00 — hecha en inventario (Vender) por Abraham",
    "Cerrado por el barrido: 14 días sin mensajes.",
    "Origen emparejado por hora: el mensaje llegó 8 s después del tap.",
])
def test_los_sucesos_de_verdad_si_entran(texto):
    sucesos, internas = control.separar_notas([nota(texto)])
    assert [s["texto"] for s in sucesos] == [texto]


def test_la_cotizacion_sale_como_renglon_lila_en_el_hilo():
    """Es el renglón que pidió el dueño, y sale de un comentario que Linear
    ya tiene: «🧾 S00089 · $55.00 — hecha en inventario (Vender)»."""
    bloques = control.hilo(
        mensajes=[m("2026-09-24T13:05:00Z", "Hola"),
                  m("2026-09-24T21:00:00Z", "Gracias")],
        sucesos=[nota("🧾 S00089 · $55.00 — hecha en inventario (Vender) por Abraham",
                      fecha="2026-09-24T19:58:00Z")],
        nombre_cliente="Jenny Londoño")
    lila = [b for b in bloques if b["tipo"] == "suceso"]
    assert len(lila) == 1
    assert "S00089" in lila[0]["texto"] and "$55.00" in lila[0]["texto"]
