"""La pestaña Pedidos: el trabajo ya pagado, en tiquetes (30/09/2026).

Corren en modo muestra (sin `LINEAR_API_KEY`) y **sin red**: los leads y las
actividades se le pasan a `tiquetes.tablero()` ya armados — ese es el motivo
de que los acepte como parámetros— y la plata se le pone un doble a
`cot_lead.plata_de_las_reales`, que es la ÚNICA puerta de este módulo a
Odoo.

Lo que se cuida acá es lo que duele si se rompe:

- el **agrupado**: falta agendar · atrasado · hoy · mañana · el día, en ese
  orden, con los atrasados ARRIBA de hoy (una entrega que se pasó de fecha
  no puede esconderse al fondo);
- que **solo** salgan Por agendar y Agendado: un Cotizado todavía no pagó y
  un Entregado ya no está por hacer;
- que un lead **sin cotización conectada** no pinte plata, y que «no se pudo
  leer Odoo» nunca se confunda con «no debe nada»;
- que la fecha vaya por el **camino del calendario** (`agenda.agendar` /
  `agenda.reprogramar`) y por ningún otro — si esto escribiera la fecha por
  su cuenta habría dos verdades;
- que el **candado** deje VER el tiquete ajeno y no editarlo, verificado en
  el servidor;
- y que el **responsable** salga de la etiqueta `Resp:`, nunca del assignee.
"""

from datetime import timedelta

import pytest

from app import agenda, calendario, cot_lead, linear_leads, tiquetes, ventas


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    calendario.reiniciar_muestra()
    tiquetes.reiniciar_cache()


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas de pantalla es el dueño: le pone fecha a
    cualquier tiquete. `genesis` (la empleada del conftest) no casa con
    ninguna etiqueta `Resp:`, así que sin esto es un empleado sin etiqueta —
    que ve todo y no toca nada."""
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


DUENO = {"vistas": ["estado"], "resp_propio": "", "admin": True}
DE_MARY = {"vistas": ["estado"], "resp_propio": "Mary", "admin": False}
SIN_ETIQUETA = {"vistas": ["estado"], "resp_propio": "", "admin": False}


def _dia(desplazamiento):
    return (calendario.hoy() + timedelta(days=desplazamiento)).isoformat()


def _lead(ref, estado, nombre=None, pp=None, resp="", interes="Plantas",
          pago="", te_toca=False, dias=1, celular="6200-0000"):
    """Un lead del equipo LEAD con la forma que devuelve
    `linear_leads._normalizar` — lo que el tablero consume."""
    ficha = linear_leads.POR_CLAVE[estado]
    return {
        "id": "muestra-" + ref, "ref": ref,
        "nombre": nombre or ref.replace("LEAD-", "Cliente "),
        "pp": pp or ("PP-" + ref.split("-")[1]),
        "celular": celular,
        "wa": "https://wa.me/507" + celular.replace("-", ""),
        "estado": estado, "estado_nombre": ficha["nombre"],
        "estado_ficha": ficha, "cerrado": estado in linear_leads.CERRADOS,
        "etiquetas": [], "origen": "WhatsApp", "interes": interes,
        "pago": pago, "motivo": "", "motivo_clave": "", "resp": resp,
        "te_toca": te_toca, "creado": "", "dias": dias,
        "hace": linear_leads.hace_bonito(dias),
    }


def _actividad(ref_lead, fecha, tipo="entrega", estado="pend", hora="09:00",
               lugar="", nota="", resp_lead="", id_act=None):
    return {
        "id": id_act or ("act-" + ref_lead + "-" + fecha), "ref": "VIV-900",
        "url": "", "tipo": tipo, "cliente": ref_lead,
        "titulo": "x", "lugar": lugar, "nota": nota, "hora": hora, "dur": 60,
        "fecha": fecha, "estado": estado, "prioridad": 3, "resp": "Sin asignar",
        "resp_id": "", "lead": ref_lead, "resp_lead": resp_lead,
    }


@pytest.fixture
def sin_odoo(monkeypatch):
    """Odoo sin configurar: `plata_de` ni lo intenta y los tiquetes dicen
    «sin cotización conectada», que en modo muestra es la verdad."""
    monkeypatch.setattr(ventas, "configurado", lambda: False)


def _con_plata(monkeypatch, plata, ok=True, error=None, viajes=None):
    """Le pone un doble a la única puerta a Odoo de este módulo."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def falso():
        if viajes is not None:
            viajes.append(1)
        return {"ok": ok, "error": error, "plata": plata}

    monkeypatch.setattr(cot_lead, "plata_de_las_reales", falso)


# ---------------------------------------------------------------------------
# El agrupado
# ---------------------------------------------------------------------------

def test_los_grupos_en_orden_con_los_atrasados_arriba_de_hoy(sin_odoo):
    leads = [
        _lead("LEAD-1", "POR_AGENDAR"),
        _lead("LEAD-2", "AGENDADO"),
        _lead("LEAD-3", "AGENDADO"),
        _lead("LEAD-4", "AGENDADO"),
        _lead("LEAD-5", "AGENDADO"),
    ]
    actividades = [
        _actividad("LEAD-2", _dia(-3)),
        _actividad("LEAD-3", _dia(0)),
        _actividad("LEAD-4", _dia(1)),
        _actividad("LEAD-5", _dia(6)),
    ]
    grupos = tiquetes.tablero(DUENO, leads=leads, actividades=actividades)["grupos"]

    assert [g["clave"] for g in grupos] == [
        tiquetes.GRUPO_FALTA, tiquetes.GRUPO_ATRASADO,
        _dia(0), _dia(1), _dia(6)]
    assert [[t["ref"] for t in g["tiquetes"]] for g in grupos] == [
        ["LEAD-1"], ["LEAD-2"], ["LEAD-3"], ["LEAD-4"], ["LEAD-5"]]
    assert grupos[0]["titulo"] == "⚠ Falta agendar"
    assert grupos[0]["nota"] == "1 · ya pagaron"
    assert grupos[0]["urge"] is True
    assert grupos[1]["titulo"] == "⚠ Atrasado"
    assert grupos[1]["urge"] is True
    assert grupos[2]["titulo"].startswith("Hoy · ")
    assert grupos[2]["hoy"] is True
    assert grupos[2]["nota"] == "1 trabajo"
    assert grupos[3]["titulo"].startswith("Mañana · ")
    # El último es una fecha a secas: «Mié 7 oct».
    assert not grupos[4]["titulo"].startswith(("Hoy", "Mañana"))
    assert grupos[4]["hoy"] is False


def test_un_grupo_vacio_no_se_pinta(sin_odoo):
    """Sin nada que falte agendar, el rótulo ámbar no aparece: un grupo
    vacío con su título haría ruido todos los días."""
    leads = [_lead("LEAD-2", "AGENDADO")]
    grupos = tiquetes.tablero(
        DUENO, leads=leads,
        actividades=[_actividad("LEAD-2", _dia(0))])["grupos"]
    assert [g["clave"] for g in grupos] == [_dia(0)]


def test_un_agendado_sin_actividad_viva_cae_en_falta_agendar(sin_odoo):
    """La actividad se canceló: el lead quedó en Agendado pero no tiene
    fecha de verdad, así que se muestra donde se le puede poner una. Una
    cancelada no es trabajo que hacer (misma regla que `contar_vivas`)."""
    leads = [_lead("LEAD-2", "AGENDADO")]
    actividades = [_actividad("LEAD-2", _dia(2), estado="cancel")]
    grupos = tiquetes.tablero(DUENO, leads=leads, actividades=actividades)["grupos"]
    assert [g["clave"] for g in grupos] == [tiquetes.GRUPO_FALTA]
    assert grupos[0]["tiquetes"][0]["falta_fecha"] is True
    assert grupos[0]["tiquetes"][0]["fecha_texto"] == "Sin fecha"


def test_una_actividad_hecha_no_le_da_fecha_al_tiquete(sin_odoo):
    leads = [_lead("LEAD-2", "AGENDADO")]
    actividades = [_actividad("LEAD-2", _dia(-1), estado="hecha")]
    grupos = tiquetes.tablero(DUENO, leads=leads, actividades=actividades)["grupos"]
    assert [g["clave"] for g in grupos] == [tiquetes.GRUPO_FALTA]


def test_la_entrega_le_gana_a_la_recogida_por_la_fecha(sin_odoo):
    """El alquiler que se entrega el viernes y se recoge el domingo se lee
    como «viernes»: la Recogida es una actividad de apoyo y no puede
    robarle la fecha al tiquete."""
    leads = [_lead("LEAD-5", "AGENDADO", interes="Eventos")]
    actividades = [
        _actividad("LEAD-5", _dia(5), tipo="recogida", id_act="la-recogida"),
        _actividad("LEAD-5", _dia(3), tipo="entrega", id_act="la-entrega"),
    ]
    tablero = tiquetes.tablero(DUENO, leads=leads, actividades=actividades)
    tiquete = tablero["grupos"][0]["tiquetes"][0]
    assert tiquete["fecha"] == _dia(3)
    assert tiquete["actividad_id"] == "la-entrega"


# ---------------------------------------------------------------------------
# Qué entra al tablero y qué no
# ---------------------------------------------------------------------------

def test_solo_por_agendar_y_agendado_salen(sin_odoo):
    leads = [_lead("LEAD-" + str(i), estado)
             for i, estado in enumerate(linear_leads.POR_CLAVE, start=1)]
    tablero = tiquetes.tablero(DUENO, leads=leads, actividades=[])
    salieron = {t["estado"] for g in tablero["grupos"] for t in g["tiquetes"]}
    assert salieron == {"POR_AGENDAR", "AGENDADO"}
    assert tablero["cuantos"] == 2


@pytest.mark.parametrize("estado", ["NUEVO", "HABLANDO", "COTIZADO",
                                    "ENTREGADO", "GANADO", "PERDIDO",
                                    "RECORDATORIO"])
def test_los_demas_estados_no_son_un_pedido(sin_odoo, estado):
    leads = [_lead("LEAD-7", estado)]
    tablero = tiquetes.tablero(DUENO, leads=leads, actividades=[])
    assert tablero["grupos"] == []
    assert tablero["cuantos"] == 0


def test_una_actividad_sin_lead_no_es_de_nadie(sin_odoo):
    """Una compra o una reunión del calendario no amarra a ningún lead: no
    puede darle fecha a un tiquete por casualidad."""
    actividades = [dict(_actividad("LEAD-2", _dia(0)), lead="")]
    leads = [_lead("LEAD-2", "AGENDADO")]
    grupos = tiquetes.tablero(DUENO, leads=leads, actividades=actividades)["grupos"]
    assert [g["clave"] for g in grupos] == [tiquetes.GRUPO_FALTA]


# ---------------------------------------------------------------------------
# La plata: de Odoo, y nunca inventada
# ---------------------------------------------------------------------------

def test_sin_cotizacion_conectada_el_tiquete_no_pinta_plata(monkeypatch):
    _con_plata(monkeypatch, {})
    leads = [_lead("LEAD-1", "POR_AGENDAR", pp="PP-70211")]
    tiquete = tiquetes.tablero(
        DUENO, leads=leads, actividades=[])["grupos"][0]["tiquetes"][0]
    assert tiquete["plata"] is None
    assert tiquete["plata_aviso"] == "Sin cotización conectada"


def test_con_cotizacion_conectada_el_tiquete_trae_total_y_saldo(monkeypatch):
    _con_plata(monkeypatch, {"PP-70211": {
        "ok": True, "error": None, "hay_real": True, "orden_id": 79,
        "orden": "S00079", "total": 787.0, "pide_abono": True,
        "abono_50": 393.5, "pagado": 393.5, "saldo": 393.5,
        "etapa_cobro": "abono"}})
    leads = [_lead("LEAD-1", "POR_AGENDAR", pp="PP-70211", pago="Abono 50%")]
    tiquete = tiquetes.tablero(
        DUENO, leads=leads, actividades=[])["grupos"][0]["tiquetes"][0]
    assert tiquete["plata"]["total"] == 787.0
    assert tiquete["plata"]["saldo"] == 393.5
    assert tiquete["plata"]["orden"] == "S00079"
    assert tiquete["plata_aviso"] == ""
    assert tiquete["pago"] == "Abono 50%"


def test_odoo_caido_lo_dice_y_no_se_confunde_con_no_deber_nada(monkeypatch):
    _con_plata(monkeypatch, {}, ok=False, error="Odoo no contesta")
    leads = [_lead("LEAD-1", "POR_AGENDAR", pp="PP-70211")]
    tablero = tiquetes.tablero(DUENO, leads=leads, actividades=[])
    assert "Odoo no contestó" in tablero["error_plata"]
    tiquete = tablero["grupos"][0]["tiquetes"][0]
    assert tiquete["plata"] is None
    # El aviso del tiquete dice la verdad: no sé, no "no debe nada".
    assert "Odoo no contestó" in tiquete["plata_aviso"]
    assert tiquete["plata_aviso"] != "Sin cotización conectada"


def test_la_plata_se_lee_una_sola_vez_para_todo_el_tablero(monkeypatch):
    viajes = []
    _con_plata(monkeypatch, {}, viajes=viajes)
    leads = [_lead("LEAD-%d" % i, "POR_AGENDAR") for i in range(1, 7)]
    tiquetes.tablero(DUENO, leads=leads, actividades=[])
    assert len(viajes) == 1, "una consulta a Odoo por pintada, no una por tiquete"


def test_sin_tiquetes_no_se_le_pregunta_nada_a_odoo(monkeypatch):
    viajes = []
    _con_plata(monkeypatch, {}, viajes=viajes)
    tiquetes.tablero(DUENO, leads=[_lead("LEAD-9", "NUEVO")], actividades=[])
    assert viajes == []


def test_el_calendario_caido_manda_todo_a_falta_agendar_y_lo_dice(monkeypatch,
                                                                  sin_odoo):
    def revienta(desde, hasta, refrescar=False):
        raise calendario.ErrorCalendario("Linear no contesta")

    monkeypatch.setattr(calendario, "listar", revienta)
    leads = [_lead("LEAD-2", "AGENDADO")]
    tablero = tiquetes.tablero(DUENO, leads=leads)
    assert tablero["error_calendario"] == "Linear no contesta"
    assert [g["clave"] for g in tablero["grupos"]] == [tiquetes.GRUPO_FALTA]


# ---------------------------------------------------------------------------
# Lo que dice el tiquete
# ---------------------------------------------------------------------------

def test_el_responsable_sale_de_la_etiqueta_resp(sin_odoo):
    leads = [_lead("LEAD-1", "POR_AGENDAR", resp="Mary"),
             _lead("LEAD-2", "POR_AGENDAR", resp="")]
    por_ref = {t["ref"]: t for g in tiquetes.tablero(
        DUENO, leads=leads, actividades=[])["grupos"] for t in g["tiquetes"]}
    assert por_ref["LEAD-1"]["resp"] == "Mary"
    assert por_ref["LEAD-1"]["resp_titulo"] == "Mary"
    assert por_ref["LEAD-1"]["inicial"] == "M"
    # Sin etiqueta no se inventa un dueño.
    assert por_ref["LEAD-2"]["resp"] == ""
    assert por_ref["LEAD-2"]["resp_titulo"] == "Sin dueño"
    assert por_ref["LEAD-2"]["inicial"] == "?"


def test_el_chip_es_el_interes_del_lead_no_un_vehiculo(sin_odoo):
    """La maqueta traía chips de vehículo (🛵/🚗/🚚); un lead del CRM no
    tiene vehículo —ese dato es de los pedidos de la tienda en línea— y en
    su lugar va el interés. Nada se inventa."""
    leads = [_lead("LEAD-1", "POR_AGENDAR", interes="Paisajismo")]
    tiquete = tiquetes.tablero(
        DUENO, leads=leads, actividades=[])["grupos"][0]["tiquetes"][0]
    assert tiquete["interes"] == "Paisajismo"
    assert "vehiculo" not in tiquete


def test_la_hora_solo_se_nombra_si_no_es_la_de_siempre(sin_odoo):
    leads = [_lead("LEAD-2", "AGENDADO"), _lead("LEAD-3", "AGENDADO")]
    actividades = [
        _actividad("LEAD-2", _dia(0), hora=calendario.HORA_POR_DEFECTO),
        _actividad("LEAD-3", _dia(0), hora="14:00"),
    ]
    por_ref = {t["ref"]: t for g in tiquetes.tablero(
        DUENO, leads=leads, actividades=actividades)["grupos"]
        for t in g["tiquetes"]}
    assert por_ref["LEAD-2"]["fecha_texto"] == "Hoy"
    assert por_ref["LEAD-3"]["fecha_texto"] == "Hoy · 2 pm"


def test_que_se_hace_dice_el_tipo_y_la_nota(sin_odoo):
    leads = [_lead("LEAD-2", "AGENDADO"), _lead("LEAD-1", "POR_AGENDAR")]
    actividades = [_actividad("LEAD-2", _dia(0), tipo="instalacion",
                              nota="entrar por el sótano", lugar="Obarrio")]
    por_ref = {t["ref"]: t for g in tiquetes.tablero(
        DUENO, leads=leads, actividades=actividades)["grupos"]
        for t in g["tiquetes"]}
    assert por_ref["LEAD-2"]["que"] == "Instalación · entrar por el sótano"
    assert por_ref["LEAD-2"]["zona"] == "Obarrio"
    # Un tiquete se lee suelto: sin fecha tiene que decir qué le falta.
    assert por_ref["LEAD-1"]["que"] == "Falta ponerle fecha"


def test_la_franja_de_un_agendado_es_el_color_de_su_tipo(sin_odoo):
    leads = [_lead("LEAD-2", "AGENDADO"), _lead("LEAD-1", "POR_AGENDAR")]
    actividades = [_actividad("LEAD-2", _dia(0), tipo="mantenimiento")]
    por_ref = {t["ref"]: t for g in tiquetes.tablero(
        DUENO, leads=leads, actividades=actividades)["grupos"]
        for t in g["tiquetes"]}
    assert por_ref["LEAD-2"]["franja"] == calendario.color_de("mantenimiento")
    # Sin fecha la franja la decide el CSS (ámbar): no es un tipo.
    assert por_ref["LEAD-1"]["franja"] == ""


def test_falta_agendar_pone_arriba_al_que_lleva_mas_esperando(sin_odoo):
    leads = [_lead("LEAD-1", "POR_AGENDAR", dias=1),
             _lead("LEAD-2", "POR_AGENDAR", dias=9),
             _lead("LEAD-3", "POR_AGENDAR", dias=4)]
    grupo = tiquetes.tablero(DUENO, leads=leads, actividades=[])["grupos"][0]
    assert [t["ref"] for t in grupo["tiquetes"]] == ["LEAD-2", "LEAD-3", "LEAD-1"]


# ---------------------------------------------------------------------------
# El candado: todos ven, cada quien mueve lo suyo
# ---------------------------------------------------------------------------

def test_un_empleado_ve_el_tablero_completo(sin_odoo):
    leads = [_lead("LEAD-1", "POR_AGENDAR", resp="Mary"),
             _lead("LEAD-2", "POR_AGENDAR", resp="Ruben")]
    tablero = tiquetes.tablero(DE_MARY, leads=leads, actividades=[])
    assert tablero["cuantos"] == 2


def test_el_lapiz_del_tiquete_ajeno_esta_apagado(sin_odoo):
    leads = [_lead("LEAD-1", "POR_AGENDAR", resp="Mary"),
             _lead("LEAD-2", "POR_AGENDAR", resp="Ruben")]
    por_ref = {t["ref"]: t for g in tiquetes.tablero(
        DE_MARY, leads=leads, actividades=[])["grupos"] for t in g["tiquetes"]}
    assert por_ref["LEAD-1"]["puede_editar"] is True
    assert por_ref["LEAD-2"]["puede_editar"] is False


def test_el_dueno_le_pone_fecha_a_todos(sin_odoo):
    leads = [_lead("LEAD-2", "POR_AGENDAR", resp="Ruben")]
    tiquete = tiquetes.tablero(
        DUENO, leads=leads, actividades=[])["grupos"][0]["tiquetes"][0]
    assert tiquete["puede_editar"] is True


def test_en_solo_lectura_nadie_edita(sin_odoo):
    leads = [_lead("LEAD-1", "POR_AGENDAR", resp="Mary")]
    tiquete = tiquetes.tablero(
        DUENO, leads=leads, actividades=[], puede_escribir=False
    )["grupos"][0]["tiquetes"][0]
    assert tiquete["puede_editar"] is False


def test_para_editar_rebota_el_tiquete_ajeno(sin_odoo):
    """El candado es del SERVIDOR: un POST se puede mandar a mano, así que
    no basta con no pintar el ✎."""
    assert tiquetes.para_editar("LEAD-91", DUENO) is not None     # Tamara, suya
    assert tiquetes.para_editar("LEAD-91", SIN_ETIQUETA) is None
    # LEAD-91 es de Ruben en el tablero de muestra: Mary no le pone fecha.
    assert tiquetes.para_editar("LEAD-91", DE_MARY) is None


def test_para_editar_rebota_lo_que_no_es_un_pedido(sin_odoo):
    # LEAD-85 está en Nuevo y LEAD-84 en Ganado: ninguno es un pedido.
    assert tiquetes.para_editar("LEAD-85", DUENO) is None
    assert tiquetes.para_editar("LEAD-84", DUENO) is None
    assert tiquetes.para_editar("LEAD-999", DUENO) is None
    assert tiquetes.para_editar("", DUENO) is None


# ---------------------------------------------------------------------------
# La fecha: SIEMPRE por el camino del calendario
# ---------------------------------------------------------------------------

def test_poner_fecha_la_primera_vez_llama_a_agendar(monkeypatch, sin_odoo):
    """Sin actividad todavía: `agenda.agendar`, que es el único camino que
    crea la actividad amarrada Y manda el lead a Agendado."""
    llamadas = {}
    monkeypatch.setattr(agenda, "agendar",
                        lambda **kw: llamadas.update(kw) or "listo")
    monkeypatch.setattr(agenda, "reprogramar",
                        lambda *a, **k: pytest.fail("no se reprograma lo que no existe"))

    aviso, error = tiquetes.poner_fecha("LEAD-91", _dia(2), resp="Ruben",
                                        autor="Génesis")

    assert (aviso, error) == ("listo", "")
    assert llamadas["ref_lead"] == "LEAD-91"
    assert llamadas["fecha"] == _dia(2)
    assert llamadas["resp"] == "Ruben"
    assert llamadas["autor"] == "Génesis"


def test_poner_fecha_de_verdad_agenda_y_mueve_el_lead_a_agendado(sin_odoo):
    """Punta a punta en modo muestra: la actividad nace amarrada y el lead
    pasa a Agendado por la regla 5 del embudo, sin que este módulo escriba
    el estado por su cuenta."""
    aviso, error = tiquetes.poner_fecha("LEAD-91", _dia(2), tipo="entrega",
                                        resp="Ruben", autor="Génesis")
    assert error == ""
    assert "Agendado" in aviso
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"
    actividades = calendario.listar(_dia(0), _dia(30))
    amarradas = [a for a in actividades if a["lead"] == "LEAD-91"]
    assert len(amarradas) == 1
    assert amarradas[0]["fecha"] == _dia(2)
    assert amarradas[0]["tipo"] == "entrega"


def test_poner_fecha_con_actividad_llama_a_reprogramar_sin_hora(monkeypatch,
                                                                sin_odoo):
    """Con actividad ya creada: `agenda.reprogramar` y nada más — mueve el
    día SIN tocar el estado, y sin pasar hora para que la que hubiera puesto
    alguien en el calendario se conserve."""
    llamadas = []
    monkeypatch.setattr(agenda, "agendar",
                        lambda **kw: pytest.fail("ya tenía actividad"))
    monkeypatch.setattr(agenda, "reprogramar",
                        lambda *a, **k: llamadas.append((a, k)) or "movida")
    monkeypatch.setattr(
        tiquetes, "_actividades",
        lambda refrescar=False: ([_actividad("LEAD-89", _dia(3),
                                             id_act="la-actividad")], ""))

    aviso, error = tiquetes.poner_fecha("LEAD-89", _dia(5), autor="Génesis")

    assert (aviso, error) == ("movida", "")
    assert llamadas == [(("la-actividad", _dia(5)), {})]


def test_reprogramar_de_verdad_no_cambia_el_estado(sin_odoo):
    """LEAD-89 está Agendado con su Recogida en el tablero de muestra:
    moverle el día la deja en Agendado."""
    aviso, error = tiquetes.poner_fecha("LEAD-89", _dia(9), autor="Génesis")
    assert error == ""
    assert linear_leads.uno("LEAD-89")["estado"] == "AGENDADO"
    amarradas = [a for a in calendario.listar(_dia(0), _dia(30))
                 if a["lead"] == "LEAD-89"]
    assert [a["fecha"] for a in amarradas] == [_dia(9)]


def test_poner_fecha_rebota_un_lead_que_no_es_un_pedido(sin_odoo):
    aviso, error = tiquetes.poner_fecha("LEAD-85", _dia(2))   # Nuevo
    assert aviso == ""
    assert "no es un pedido por hacer" in error


def test_poner_fecha_rebota_un_lead_que_no_existe(sin_odoo):
    aviso, error = tiquetes.poner_fecha("LEAD-999", _dia(2))
    assert aviso == ""
    assert "LEAD-999" in error


def test_poner_fecha_no_inventa_la_etiqueta_del_responsable(sin_odoo):
    """Las etiquetas de Linear nunca se crean solas (regla 3): un nombre que
    no existe se dice, no se inventa."""
    aviso, error = tiquetes.poner_fecha("LEAD-91", _dia(2), resp="Pepito")
    assert aviso == ""
    assert "Resp: Pepito" in error
    # Y nada se movió.
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"


def test_poner_fecha_sin_calendario_no_escribe_nada(monkeypatch, sin_odoo):
    monkeypatch.setattr(tiquetes, "_actividades",
                        lambda refrescar=False: ([], "Linear no contesta"))
    monkeypatch.setattr(agenda, "agendar",
                        lambda **kw: pytest.fail("sin saber qué hay, no se agenda"))
    aviso, error = tiquetes.poner_fecha("LEAD-91", _dia(2))
    assert aviso == ""
    assert "no se pudo leer el calendario" in error.lower()


# ---------------------------------------------------------------------------
# El tipo de actividad que se le sugiere
# ---------------------------------------------------------------------------

def test_el_tipo_sugerido_sale_del_interes_del_lead():
    assert tiquetes.tipo_sugerido({"interes": "Mantenimiento"}) == "mantenimiento"
    assert tiquetes.tipo_sugerido({"interes": "Paisajismo"}) == "instalacion"
    assert tiquetes.tipo_sugerido({"interes": "Plantas"}) == "entrega"
    assert tiquetes.tipo_sugerido({"interes": "Eventos"}) == "entrega"
    assert tiquetes.tipo_sugerido({}) == "entrega"


def test_los_tres_tipos_sugeridos_cierran_la_entrega():
    """Un trabajo pagado que se agenda es trabajo que se va a ENTREGAR: los
    tres destinos del mapa tienen que ser de los que mueven el lead a
    Entregado al marcar «Hecha»."""
    destinos = set(tiquetes.TIPO_POR_INTERES.values()) | {tiquetes.TIPO_POR_DEFECTO}
    for tipo in destinos:
        assert agenda.cierra_la_entrega(tipo), tipo


def test_para_editar_sugiere_el_tipo_que_ya_tiene_la_actividad(monkeypatch,
                                                               sin_odoo):
    monkeypatch.setattr(
        tiquetes, "_actividades",
        lambda refrescar=False: ([_actividad("LEAD-89", _dia(3),
                                             tipo="mantenimiento")], ""))
    datos = tiquetes.para_editar("LEAD-89", DUENO)
    assert datos["tipo_sugerido"] == "mantenimiento"
    assert datos["fecha_sugerida"] == _dia(3)


def test_para_editar_sin_fecha_arranca_en_hoy(sin_odoo):
    datos = tiquetes.para_editar("LEAD-91", DUENO)
    assert datos["tiquete"]["falta_fecha"] is True
    assert datos["fecha_sugerida"] == _dia(0)
    assert datos["resp_sugerido"] == "Ruben"
    assert [t["clave"] for t in datos["tipos"]] == [
        t["clave"] for t in agenda.TIPOS]


# ---------------------------------------------------------------------------
# Los rótulos de fecha
# ---------------------------------------------------------------------------

def test_fecha_corta_dice_hoy_manana_y_ayer():
    assert tiquetes.fecha_corta(_dia(0)) == "Hoy"
    assert tiquetes.fecha_corta(_dia(1)) == "Mañana"
    assert tiquetes.fecha_corta(_dia(-1)) == "Ayer"
    assert tiquetes.fecha_corta("") == ""


def test_fecha_corta_de_otro_dia_lleva_dia_numero_y_mes():
    assert tiquetes.fecha_corta("2026-10-03", "2026-10-01") == "Sáb 3 oct"


def test_titulo_de_dia_de_otro_dia_no_dice_hoy_ni_manana():
    assert tiquetes.titulo_de_dia("2026-10-01", "2026-10-01") == "Hoy · jueves 1"
    assert tiquetes.titulo_de_dia("2026-10-02", "2026-10-01") == "Mañana · viernes 2"
    assert tiquetes.titulo_de_dia("2026-10-05", "2026-10-01") == "Lun 5 oct"


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def test_la_pantalla_pinta_los_tiquetes(cliente, de_dueno, sin_odoo):
    respuesta = cliente.get("/pedidos")
    assert respuesta.status_code == 200
    html = respuesta.text
    assert "Falta agendar" in html
    # Los dos Por agendar del tablero de muestra, y el Agendado.
    assert "Tamara" in html and "Juan Carlos Lopez" in html
    assert "Boda Las Nubes" in html
    # Y ninguno de los que no son pedidos.
    assert "Diego Armando" not in html    # Nuevo
    assert "Hotel Bristol" not in html    # Entregado
    assert "Monica Gama" not in html      # Perdido
    # El refresco de cada minuto es el único JS propio.
    assert "/static/tiquetes.js" in html


def test_la_pantalla_sale_en_el_menu(cliente, de_dueno, sin_odoo):
    for ruta in ("/pedidos", "/control", "/compras", "/calendario"):
        assert 'href="/pedidos"' in cliente.get(ruta).text, ruta


def test_el_lapiz_solo_aparece_en_lo_propio(cliente, sin_odoo):
    """Sin ser admin ni tener etiqueta `Resp:`, la empleada ve los tiquetes
    y ningún ✎ vivo."""
    respuesta = cliente.get("/pedidos")
    assert respuesta.status_code == 200
    assert "Tamara" in respuesta.text
    assert "/pedidos?fecha=" not in respuesta.text


def test_el_modal_de_la_fecha_pide_dia_y_responsable(cliente, de_dueno, sin_odoo):
    html = cliente.get("/pedidos?fecha=LEAD-91").text
    assert 'action="/pedidos/fecha"' in html
    assert 'name="fecha"' in html and 'type="date"' in html
    assert 'name="resp"' in html
    # Sin selector de hora: el sistema agenda a las 9:00 y eso no cambia.
    assert 'type="time"' not in html


def test_el_post_de_la_fecha_agenda_y_vuelve_a_pedidos(cliente, de_dueno,
                                                       sin_odoo):
    respuesta = cliente.post("/pedidos/fecha",
                             data={"ref": "LEAD-91", "fecha": _dia(2),
                                   "tipo": "entrega", "resp": "Ruben"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/pedidos?aviso=")
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"


def test_el_post_de_un_tiquete_ajeno_rebota_sin_tocar_nada(cliente, sin_odoo):
    """Sin `AJUSTES_ADMINS` la empleada no es dueña y no tiene etiqueta
    `Resp:`: el POST se corta en el servidor."""
    respuesta = cliente.post("/pedidos/fecha",
                             data={"ref": "LEAD-91", "fecha": _dia(2)},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"


def test_el_post_de_un_lead_que_no_existe_lo_nombra(cliente, de_dueno, sin_odoo):
    respuesta = cliente.post("/pedidos/fecha",
                             data={"ref": "LEAD-999", "fecha": _dia(2)},
                             follow_redirects=False)
    assert "LEAD-999" in respuesta.headers["location"]


def test_un_error_al_poner_la_fecha_deja_el_modal_abierto(cliente, de_dueno,
                                                          sin_odoo):
    """Si algo falla no se pierde el formulario: se vuelve con el modal
    abierto en ESE tiquete, como hace /calendario/agendar."""
    respuesta = cliente.post("/pedidos/fecha",
                             data={"ref": "LEAD-91", "fecha": "",
                                   "tipo": "entrega"},
                             follow_redirects=False)
    destino = respuesta.headers["location"]
    assert destino.startswith("/pedidos?fecha=LEAD-91")
    assert "error=" in destino
    assert linear_leads.uno("LEAD-91")["estado"] == "POR_AGENDAR"
    # Y el modal reabierto muestra el error adentro (el telón tapa el banner
    # de atrás).
    html = cliente.get(destino).text
    assert "Ponerle fecha" in html and "Ups." in html
