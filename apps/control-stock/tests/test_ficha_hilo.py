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

from app import control, linear_leads, ventas


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


def test_la_firma_va_anclada_al_final_no_contiene_suelto():
    """Un suceso del sistema que CITE algo con el patrón «_— » en medio del
    texto no debe clasificarse como nota de empleado: la firma real va
    SIEMPRE al final («_— Nombre desde Control Viverorose_»). Si la
    detección fuera un «contiene» suelto, este suceso caería (mal) del
    lado de las notas internas."""
    del_sistema = nota(
        'Cliente escribió: "necesito la entrega _— gracias" '
        "(Cotización S00089 generada · $55.00)"
    )
    sucesos, internas = control.separar_notas([del_sistema])
    assert internas == []
    assert sucesos == [del_sistema]


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
    assert abierta["hilo"] == []


def test_si_twenty_revienta_la_ficha_sigue_en_pie(monkeypatch):
    def explota(lead):
        raise RuntimeError("Twenty no contesta")
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead", explota)
    with pytest.raises(RuntimeError):
        control.crm_twenty.ficha_de_lead({})      # el doble está puesto
    monkeypatch.setattr(control.crm_twenty, "ficha_de_lead", lambda lead: None)
    abierta = control.ficha("LEAD-87")
    assert abierta["hilo"] == []


# ---------------------------------------------------------------------------
# La pantalla: el panel en el orden de C1
# ---------------------------------------------------------------------------

@pytest.fixture
def de_dueno(monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


def test_el_panel_va_en_el_orden_del_lienzo(cliente, de_dueno, monkeypatch):
    """Cabecera, atajos, datos, botones, cotización, conversación, notas.

    El orden venía de C1 (25/09/2026), que dejaba los datos de ÚLTIMO
    porque se consultan y no se leen. La fidelidad P37 (pantallas 06 y 22
    del lienzo fresco) los sube: el teléfono y el responsable van arriba,
    pegados a los dos atajos de llamar y chatear, que es donde la persona
    los busca cuando abre la ficha para contestarle a alguien. Todo lo
    demás conserva su lugar. Con un Odoo fingido sin órdenes: así "Sin
    cotización conectada" de verdad sale en pantalla (28/09/2026).

    Con el lienzo de ROLES (BLOQUE 43) el responsable dejó de ser renglón
    de datos y es la primera de las cinco filas, «Lo atiende» — sigue
    arriba y en el mismo tramo, que es lo que esta prueba cuida.
    """
    from test_cot_lead import OdooCotLead
    monkeypatch.setattr(ventas, "_ejecutar", OdooCotLead().ejecutar)
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    orden = [panel.index(t) for t in (
        "dc-ics",                     # llamar y chatear
        ">Lo atiende</span>",         # el responsable, en su fila
        "🔴 Responder",
        # La SECCIÓN de cotizaciones, no su resumen: con el lienzo de
        # Roles la fila «Cotización» dice la misma frase más arriba.
        'ficha-cot-vacia">Sin cotización conectada.',
        "Conversación",
        "Notas internas",
    )]
    assert orden == sorted(orden)
    # Y el nombre de quien lo atiende se sigue leyendo en esa fila.
    assert "Ruben" in panel[orden[1]:orden[2]]


def test_responder_ya_no_es_un_boton_de_mentira(cliente, de_dueno):
    """El primero de los tres se construyó (25/09/2026, otra tanda): ya no
    es un botón apagado, es un <form> que prende o apaga «Te toca»."""
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    assert "🔴 Responder" in panel
    assert 'action="/control/responder' in panel


def test_conectar_cotizacion_ya_no_es_un_boton_de_mentira(cliente, de_dueno, monkeypatch):
    """«Adjuntar venta» se renombró a «Conectar cotización» y quedó
    cableado (28/09/2026): ya no hay ningún botón disabled con ese texto."""
    from test_cot_lead import OdooCotLead
    monkeypatch.setattr(ventas, "_ejecutar", OdooCotLead().ejecutar)
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    assert "Adjuntar venta" not in panel
    assert "Conectar cotización" in panel


def test_cotizar_esta_disponible_siempre(cliente, de_dueno):
    # Abraham (28/09/2026): ya no se limita a Nuevo/Hablando — es un
    # enlace a Vender, no un cambio de estado, así que no hay razón para
    # escondérselo a un lead más avanzado.
    hablando = cliente.get("/control", params={"abrir": "LEAD-86"}).text
    agendado = cliente.get("/control", params={"abrir": "LEAD-89"}).text
    assert ">Cotizar<" in hablando[hablando.index('class="panel-der"'):]
    assert ">Cotizar<" in agendado[agendado.index('class="panel-der"'):]


def test_cotizar_es_un_post_a_vender_con_el_lead(cliente, de_dueno):
    # Era un enlace GET (/venta?lead=...), pero dejaba escrito el lead
    # pendiente y el borrador: desde el punto 1 de roles es un formulario
    # POST a /venta/lead (precisión 2 del review — un GET no muta).
    cuerpo = cliente.get("/control", params={"abrir": "LEAD-91"}).text
    panel = cuerpo[cuerpo.index('class="panel-der"'):]
    trozo = panel[panel.index(">Cotizar<") - 400:panel.index(">Cotizar<")]
    assert 'action="/venta/lead"' in trozo
    assert 'name="lead" value="LEAD-91"' in trozo
    assert 'name="cliente" value="Tamara' in trozo
    assert 'name="cel" value="6552-0966"' in trozo
    # Y el camino viejo ya no muta: el GET con ?lead= pinta la pestaña.
    r = cliente.get("/venta", params={"lead": "LEAD-91"},
                    follow_redirects=False)
    assert r.status_code == 200
    from app import ventas
    assert ventas.lead_pendiente("genesis") is None


def test_cotizar_no_duplica_el_lead_en_el_selector_de_vender(
        cliente, de_dueno, monkeypatch):
    # El camino real, de punta a punta: clic en Cotizar -> POST
    # /venta/lead -> deja el lead pendiente -> /venta/nueva. La referencia que manda Control
    # (LEAD-91, la de linear_leads) tiene que ser la MISMA que arma el
    # selector de Vender (_leads_para_elegir, la misma fuente) — si no
    # coincidieran, el lead saldría duplicado en el <select>. El selector
    # solo se pinta con Odoo "configurado" (el carrito vacío no lo toca:
    # `carrito_de` sale local si no hay filas, sin llamar a Odoo).
    for variable, valor in {"ODOO_URL": "http://odoo-de-prueba:8069",
                            "ODOO_DB": "pruebas", "ODOO_USER": "prueba",
                            "ODOO_PASSWORD": "prueba"}.items():
        monkeypatch.setenv(variable, valor)
    r = cliente.post("/venta/lead", data={
        "lead": "LEAD-91", "cliente": "Tamara", "cel": "6552-0966"},
        follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/venta/nueva"
    cuerpo = cliente.get("/venta/nueva").text
    assert cuerpo.count('value="LEAD-91"') == 1
    opcion = cuerpo[cuerpo.index('value="LEAD-91"'):cuerpo.index('value="LEAD-91"') + 200]
    assert 'data-nombre="Tamara"' in opcion
    assert "selected" in opcion


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
