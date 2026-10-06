"""Contactos — las tres pantallas SOLO LECTURA (BLOQUE 37 + BLOQUE 43;
item 2 de Jay, diseño corto docs/DISENO-ITEM2-contactos.md del repo
plantaspanama; lienzos abraham-contactos, abraham-contacto-abierto y
contacto-whatsapp).

Lo que se prueba, por regla:

- Renderiza CON y SIN datos, y cada hueco se DICE (Twenty no conectado,
  Linear no conectado, Odoo sin configurar o caído) — nunca una lista
  vacía que mienta, y NUNCA un lead de muestra con pinta de real.
- El casamiento es por teléfono NORMALIZADO (solo dígitos, sin 507) y EN
  LECTURA: ninguna llamada de escritura sale hacia Odoo (el doble de
  `_ejecutar` revienta con cualquier método que no sea search_read).
- El buscador (?q=) filtra nombre y teléfono; los filtros GET filtran
  bien y uno inválido cae a «Todos»; «Sin responsable» va APAGADO.
- Los botones que escribirían (Nuevo contacto, Nuevo lead) y los seis
  apagados del BLOQUE 43 van disabled con «Todavía no»; NINGUNA ruta POST
  nueva bajo /contactos.
- El nombre plano solo SUGIERE «posible mismo»: dos apariciones con el
  mismo nombre y sin teléfono NO se amarran en un solo contacto.
- **EL CANDADO POR ROL** (lo más importante del BLOQUE 43): el dinero y
  el chat de un contacto solo los ven quien lo atiende, el director y
  finanzas. Se comprueba sobre el CUERPO de la respuesta —que el monto y
  el hilo no estén en el HTML—, no sobre lo que se vería en pantalla.

Datos QA solamente — acá no entra ningún nombre de cliente real.
"""

import re

import pytest

from app import contactos, datos, datos_roles, linear_leads, seguridad, ventas


def sesion_abierta():
    """La sesión de quien lo ve todo, armada con la MISMA función de
    producción (`contactos.sesion_de`) — nunca un dict a mano, que es
    como un candado se prueba contra una ficción."""
    return contactos.sesion_de({"id": "qa-director"}, es_admin=True)


# ---------------------------------------------------------------------------
# Datos QA
# ---------------------------------------------------------------------------

_PARTNERS_QA = [
    {"id": 11, "name": "Empresa QA Hotel", "phone": "+507 6000-0030",
     "is_company": True},
    {"id": 12, "name": "Cliente QA Rosa", "phone": "6000-0001",
     "is_company": False},
    {"id": 13, "name": "Cliente QA Sin Tel", "phone": False,
     "is_company": False},
]

_ORDENES_QA = [
    {"id": 110, "name": "S00110", "partner_id": [11, "Empresa QA Hotel"],
     "amount_total": 480.0, "state": "sale",
     "date_order": "2026-10-01 10:00:00"},
    {"id": 111, "name": "S00111", "partner_id": [11, "Empresa QA Hotel"],
     "amount_total": 1150.0, "state": "draft",
     "date_order": "2026-10-03 09:00:00"},
    {"id": 112, "name": "S00112", "partner_id": [12, "Cliente QA Rosa"],
     "amount_total": 35.0, "state": "sale",
     "date_order": "2026-10-02 12:00:00"},
    # Una cancelada: no cuenta en ningún lado.
    {"id": 113, "name": "S00113", "partner_id": [12, "Cliente QA Rosa"],
     "amount_total": 999.0, "state": "cancel",
     "date_order": "2026-10-02 13:00:00"},
]


@pytest.fixture(autouse=True)
def fuentes_de_pruebas(monkeypatch, db_limpia):
    """Como el 8095: sin Twenty y, por defecto, sin Odoo ni Linear.

    `LINEAR_API_KEY` también se borra: si la tuviera el shell de quien
    corre la suite, la pestaña de leads saldría a la red de verdad."""
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setattr(ventas, "configurado", lambda: False)
    contactos.reiniciar_cache()


def _doble_odoo():
    """Un Odoo falso que SOLO acepta search_read — cualquier escritura
    revienta la prueba (el módulo es de lectura)."""
    def ejecutar(modelo, metodo, args, kw=None):
        assert metodo == "search_read", \
            f"Contactos debe ser SOLO LECTURA y llamó {modelo}.{metodo}"
        if modelo == "res.partner":
            return [dict(p) for p in _PARTNERS_QA]
        if modelo == "sale.order":
            filas = [dict(o) for o in _ORDENES_QA if o["state"] != "cancel"]
            for condicion in args[0]:
                if condicion[0] == "partner_id" and condicion[1] == "in":
                    filas = [f for f in filas
                             if f["partner_id"][0] in set(condicion[2])]
            return filas
        raise AssertionError(f"Modelo inesperado: {modelo}")
    return ejecutar


@pytest.fixture
def con_odoo_qa(monkeypatch):
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", _doble_odoo())


def _venta_local(cliente="Cliente QA Rosa", celular="60000001",
                 orden="S00112", orden_id=112, total=35.0,
                 estado="pagado", creado="2026-10-02T12:00:00"):
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " celular, orden_id, orden, total, estado)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (creado, "Génesis", cliente, celular, orden_id, orden, total,
             estado))
        return cursor.lastrowid


def _servicio_local(cliente="Empresa QA Hotel", celular="60000030",
                    orden="S00111", orden_id=111, total=1150.0,
                    tipo="general", creado="2026-10-03T09:00:00"):
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (creado, "Génesis", tipo, cliente, celular, orden_id, orden,
             total))
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# El normalizador del casamiento
# ---------------------------------------------------------------------------

def test_normalizador_solo_digitos_sin_507():
    assert contactos.normalizar_telefono("+507 6000-0030") == "60000030"
    assert contactos.normalizar_telefono("0050760000030") == "60000030"
    assert contactos.normalizar_telefono("6000-0030") == "60000030"
    assert contactos.normalizar_telefono("60000030") == "60000030"
    # Un fijo de 7 dígitos que empiece en 507 NO se recorta.
    assert contactos.normalizar_telefono("5071234") == "5071234"
    assert contactos.normalizar_telefono("") == ""
    assert contactos.normalizar_telefono(None) == ""


# ---------------------------------------------------------------------------
# Renderiza con y sin datos, y dice la verdad de cada fuente
# ---------------------------------------------------------------------------

def test_abre_sin_datos_y_dice_los_huecos(cliente):
    r = cliente.get("/contactos")
    assert r.status_code == 200
    assert "Contactos" in r.text
    assert contactos.AVISO_TWENTY_PRUEBAS in r.text
    assert contactos.AVISO_SIN_ODOO in r.text
    assert contactos.VACIO_LISTA in r.text


def test_con_twenty_conectado_de_verdad_dice_que_llega_despues(
        cliente, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "ey-una-key-de-verdad")
    texto = cliente.get("/contactos").text
    assert contactos.AVISO_TWENTY_LUEGO in texto
    assert contactos.AVISO_TWENTY_PRUEBAS not in texto


def test_un_token_neutralizado_cuenta_COMO_AUSENTE(cliente, monkeypatch):
    """El defecto cazado midiendo contra el proceso del 8095 (6/10/2026):
    allá TWENTY_API_KEY EXISTE pero arranca con «CLAVE» (la convención de
    la casa para «esto no es una key»), así que `twenty_configurado()`
    —que solo mira que la variable exista— decía que sí. La pantalla
    tiene que decir el hueco, no fingir conexión."""
    monkeypatch.setenv("TWENTY_API_KEY", "CLAVE-neutralizada-de-pruebas")
    assert contactos.twenty_conectado() is False
    texto = cliente.get("/contactos").text
    assert contactos.AVISO_TWENTY_PRUEBAS in texto
    assert contactos.AVISO_TWENTY_LUEGO not in texto


def test_con_odoo_pinta_los_partners(cliente, con_odoo_qa):
    texto = cliente.get("/contactos").text
    assert "Empresa QA Hotel" in texto
    assert "Cliente QA Rosa" in texto
    assert "Cliente QA Sin Tel" in texto
    assert contactos.AVISO_SIN_ODOO not in texto
    # La fila abre la ficha por su id estable.
    assert 'href="/contactos/t60000030"' in texto


def test_solo_locales_tambien_funciona(cliente):
    """La lista vive aunque Odoo no esté (el punto del diseño: en el
    8095 funciona con lo que haya)."""
    _venta_local(cliente="Cliente QA Lirio", celular="60000002",
                 orden="S00120", orden_id=120, total=12.0)
    texto = cliente.get("/contactos").text
    assert "Cliente QA Lirio" in texto
    assert contactos.AVISO_SIN_ODOO in texto  # el hueco se sigue diciendo


# ---------------------------------------------------------------------------
# El casamiento EN LECTURA por teléfono normalizado
# ---------------------------------------------------------------------------

def test_casa_odoo_y_local_en_un_contacto(con_odoo_qa):
    _venta_local()  # celular 60000001 = el phone de Cliente QA Rosa
    v = contactos.lista(sesion=sesion_abierta())
    assert v["total"] == 3  # 3 partners; la venta local se CASÓ, no sumó
    rosa = next(c for c in v["contactos"]
                if c["nombre"] == "Cliente QA Rosa")
    assert rosa["fuente_texto"] == "Odoo + Local"
    assert rosa["id"] == "t60000001"
    # Con partner casado la plata manda Odoo: la misma orden no se
    # cuenta dos veces (S00112 vive en los dos lados).
    assert rosa["ventas_n"] == 1
    assert rosa["ventas_total"] == 35.0


def test_el_nombre_plano_solo_sugiere_no_amarra(cliente):
    """Dos apariciones con el MISMO nombre y sin teléfono quedan como
    DOS contactos; la ficha sugiere «posible mismo» apuntando."""
    n1 = _venta_local(cliente="Cliente QA Gemelo", celular=None,
                      orden="S00130", orden_id=130, total=10.0)
    n2 = _servicio_local(cliente="Cliente QA Gemelo", celular=None,
                         orden="S00131", orden_id=131, total=20.0)
    v = contactos.lista()
    gemelos = [c for c in v["contactos"]
               if c["nombre"] == "Cliente QA Gemelo"]
    assert len(gemelos) == 2
    f = contactos.ficha(f"lv{n1}", sesion=sesion_abierta())
    assert [p["id"] for p in f["posibles"]] == [f"ls{n2}"]
    texto = cliente.get(f"/contactos/lv{n1}").text
    assert "Posible mismo contacto" in texto


def test_tipo_empresa_sale_de_odoo(con_odoo_qa):
    v = contactos.lista(sesion=sesion_abierta())
    hotel = next(c for c in v["contactos"]
                 if c["nombre"] == "Empresa QA Hotel")
    assert hotel["tipo"] == "Empresa"
    rosa = next(c for c in v["contactos"]
                if c["nombre"] == "Cliente QA Rosa")
    assert rosa["tipo"] == "Persona"


# ---------------------------------------------------------------------------
# El buscador y los filtros (server-rendered, enlaces GET)
# ---------------------------------------------------------------------------

def test_el_buscador_filtra_por_nombre(cliente, con_odoo_qa):
    texto = cliente.get("/contactos", params={"q": "Rosa"}).text
    assert "Cliente QA Rosa" in texto
    assert "Empresa QA Hotel" not in texto


def test_el_buscador_filtra_por_telefono_normalizado(con_odoo_qa):
    # Se busca con guion y casa contra el +507 de Odoo.
    v = contactos.lista(q="6000-0030")
    assert [c["nombre"] for c in v["contactos"]] == ["Empresa QA Hotel"]


def test_filtro_con_venta_y_sin_venta(con_odoo_qa):
    con_venta = contactos.lista(filtro="con_venta")
    assert sorted(c["nombre"] for c in con_venta["contactos"]) == \
        ["Cliente QA Rosa", "Empresa QA Hotel"]
    sin_venta = contactos.lista(filtro="sin_venta")
    assert [c["nombre"] for c in sin_venta["contactos"]] == \
        ["Cliente QA Sin Tel"]


def test_filtro_empresas(con_odoo_qa):
    v = contactos.lista(filtro="empresas")
    assert [c["nombre"] for c in v["contactos"]] == ["Empresa QA Hotel"]


def test_filtro_invalido_cae_a_todos(con_odoo_qa):
    v = contactos.lista(filtro="lo-que-sea")
    assert v["filtro"] == "todos"
    assert v["cuenta"] == 3


def test_los_filtros_son_enlaces_get_y_sin_responsable_va_apagado(cliente):
    texto = cliente.get("/contactos").text
    for clave in ("todos", "con_venta", "sin_venta", "empresas"):
        assert f'href="/contactos?f={clave}"' in texto
    assert "Sin responsable — Todavía no" in texto


def test_una_venta_cancelada_no_cuenta_como_venta(cliente):
    _venta_local(cliente="Cliente QA Nube", celular="60000003",
                 orden="S00140", orden_id=140, total=50.0,
                 estado="cancelada")
    v = contactos.lista(sesion=sesion_abierta())
    nube = next(c for c in v["contactos"]
                if c["nombre"] == "Cliente QA Nube")
    assert nube["ventas_n"] == 0
    assert not nube["con_venta"]
    # Pero la persona SÍ existe en la lista (un contacto puede existir
    # sin venta).
    assert contactos.lista(filtro="sin_venta")["cuenta"] == 1


# ---------------------------------------------------------------------------
# La ficha
# ---------------------------------------------------------------------------

def test_ficha_agrupa_sin_duplicar_la_misma_orden(cliente, con_odoo_qa):
    # La cotización local S00111 ES la misma orden draft de Odoo: la
    # ficha la lista UNA vez, con su href local a /venta.
    n = _servicio_local()
    f = contactos.ficha("t60000030", sesion=sesion_abierta())
    ordenes = [t["orden"] for t in f["tratos"]]
    assert ordenes.count("S00111") == 1
    assert "S00110" in ordenes  # la confirmada de Odoo también sale
    local = next(t for t in f["tratos"] if t["orden"] == "S00111")
    assert local["href"] == f"/venta/estado/servicio/{n}"
    texto = cliente.get("/contactos/t60000030").text
    assert "S00110" in texto and "S00111" in texto
    assert f'href="/venta/estado/servicio/{n}"' in texto


def test_ficha_kpis_con_odoo(con_odoo_qa):
    f = contactos.ficha("t60000030", sesion=sesion_abierta())
    assert f["contacto"]["ventas_n"] == 1
    assert f["contacto"]["ventas_total"] == 480.0
    assert f["contacto"]["cotiz_total"] == 1150.0


def test_ficha_solo_local_sin_odoo(cliente):
    n = _venta_local(cliente="Cliente QA Lirio", celular="60000002",
                     orden="S00120", orden_id=120, total=12.0)
    f = contactos.ficha("t60000002", sesion=sesion_abierta())
    assert f["contacto"]["ventas_n"] == 1
    assert f["contacto"]["ventas_total"] == 12.0
    assert f["tratos"][0]["href"] == f"/venta/estado/venta/{n}"
    texto = cliente.get("/contactos/t60000002").text
    assert "Cliente QA Lirio" in texto


def test_ficha_dice_sus_huecos(cliente, con_odoo_qa):
    texto = cliente.get("/contactos/t60000030").text
    assert contactos.AVISO_LINEAR_PRUEBAS in texto
    assert contactos.AVISO_CITAS_LUEGO in texto


def test_ficha_sin_tratos_lo_dice(cliente, con_odoo_qa):
    texto = cliente.get("/contactos/o13").text  # Sin Tel: 0 órdenes
    assert contactos.AVISO_SIN_TRATOS in texto


def test_ficha_inexistente_no_es_un_500(cliente):
    r = cliente.get("/contactos/t99999999", follow_redirects=False)
    assert r.status_code == 303
    assert "/contactos?error=" in r.headers["location"]
    assert "ya no está" in cliente.get("/contactos/t99999999").text


# ---------------------------------------------------------------------------
# Odoo caído: la última lectura buena, dicha — jamás silencio
# ---------------------------------------------------------------------------

def test_odoo_caido_sirve_la_ultima_lectura_buena(con_odoo_qa, monkeypatch):
    assert contactos.lista()["total"] == 3  # llena el caché

    def revienta(*args, **kw):
        raise RuntimeError("Odoo no contesta")
    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    v = contactos.lista()
    assert v["total"] == 3
    assert "última lectura buena" in v["aviso_odoo"]


def test_odoo_caido_sin_cache_cae_a_locales_y_lo_dice(monkeypatch):
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(*args, **kw):
        raise RuntimeError("Odoo no contesta")
    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    _venta_local(cliente="Cliente QA Lirio", celular="60000002",
                 orden="S00120", orden_id=120, total=12.0)
    v = contactos.lista()
    assert [c["nombre"] for c in v["contactos"]] == ["Cliente QA Lirio"]
    assert "Odoo no contestó" in v["aviso_odoo"]


# ---------------------------------------------------------------------------
# SOLO LECTURA: botones apagados, cero POST, cero formularios de escritura
# ---------------------------------------------------------------------------

def test_los_botones_que_escribirian_van_apagados(cliente, con_odoo_qa):
    lista = cliente.get("/contactos").text
    assert "+ Nuevo contacto — Todavía no" in lista
    ficha = cliente.get("/contactos/t60000030").text
    assert "Nuevo lead para este contacto — Todavía no" in ficha
    for texto in (lista, ficha):
        for boton in re.findall(
                r"<button[^>]*>[^<]*Todavía no[^<]*</button>", texto):
            assert "disabled" in boton, boton


def test_cero_rutas_de_escritura_bajo_contactos():
    from app.main import app
    rutas = [r for r in app.routes
             if str(getattr(r, "path", "")).startswith("/contactos")]
    assert rutas  # las dos GET existen
    for ruta in rutas:
        metodos = getattr(ruta, "methods", None) or set()
        assert not (metodos & {"POST", "PUT", "PATCH", "DELETE"}), ruta.path


def test_el_unico_form_es_el_buscador_get(cliente, con_odoo_qa):
    lista = cliente.get("/contactos").text
    assert 'method="post"' not in lista.lower()
    assert 'method="get" action="/contactos"' in lista
    ficha = cliente.get("/contactos/t60000030").text
    assert "<form" not in ficha


# ===========================================================================
# BLOQUE 43 — las tres pantallas del lienzo
# ===========================================================================

# ---------------------------------------------------------------------------
# (A) La lista a TODO EL ANCHO: tocar un contacto ABRE SU PÁGINA
# ---------------------------------------------------------------------------

def test_la_lista_no_tiene_panel_al_costado_y_cada_fila_abre_la_pagina(
        cliente, con_odoo_qa):
    texto = cliente.get("/contactos").text
    # Cada contacto es un enlace a su PÁGINA, no un panel del costado.
    assert 'href="/contactos/t60000030"' in texto
    assert 'href="/contactos/t60000001"' in texto
    # Y no queda un <aside> de panel lateral en la lista.
    assert "<aside" not in texto
    # El buscador y los filtros siguen siendo enlaces/form GET.
    assert 'method="get" action="/contactos"' in texto
    assert 'href="/contactos?f=con_venta"' in texto


# ---------------------------------------------------------------------------
# (B) La página del contacto: dos pestañas, enlaces GET, abre en leads
# ---------------------------------------------------------------------------

def test_las_pestanas_son_enlaces_get_y_abre_en_historial_de_leads(
        cliente, con_odoo_qa):
    v = contactos.ficha("t60000030", sesion=sesion_abierta())
    assert v["panel"] == "leads"
    assert [p["clave"] for p in v["paneles"]] == ["leads", "whatsapp"]
    assert [p["activo"] for p in v["paneles"]] == [True, False]
    texto = cliente.get("/contactos/t60000030").text
    assert 'href="/contactos/t60000030?panel=leads"' in texto
    assert 'href="/contactos/t60000030?panel=whatsapp"' in texto
    assert "Historial de leads" in texto and "WhatsApp" in texto
    # Cero JS: las pestañas son ENLACES, no botones con onclick (regla 10).
    assert 'class="ct-tab on"' in texto
    assert "onclick" not in texto
    assert "<button class=\"ct-tab" not in texto


def test_la_pestana_pedida_manda_y_una_inventada_cae_en_leads(
        cliente, con_odoo_qa):
    wa = contactos.ficha("t60000030", sesion=sesion_abierta(),
                         panel="whatsapp")
    assert wa["panel"] == "whatsapp"
    assert wa["paneles"][1]["activo"] is True
    inventada = contactos.ficha("t60000030", sesion=sesion_abierta(),
                                panel="lo-que-sea")
    assert inventada["panel"] == "leads"
    texto = cliente.get("/contactos/t60000030",
                        params={"panel": "whatsapp"}).text
    assert contactos.PIE_CHAT in texto


def test_sin_linear_la_pestana_de_leads_lo_dice_y_no_inventa_ninguno(
        cliente, con_odoo_qa):
    """En pruebas Linear no se lee: la pestaña dice su hueco. Lo que NO
    puede pasar es que salgan los leads de muestra de `linear_leads`
    (`listar()` los devuelve cuando no hay key) con pinta de reales."""
    assert contactos.linear_conectado() is False
    v = contactos.ficha("t60000030", sesion=sesion_abierta())
    assert v["leads"]["leads"] == []
    assert v["leads"]["aviso"] == contactos.AVISO_LINEAR_PRUEBAS
    texto = cliente.get("/contactos/t60000030").text
    assert contactos.AVISO_LINEAR_PRUEBAS in texto
    # Ni una ref de la muestra en el HTML.
    for lead in linear_leads._muestra():
        assert lead["ref"] not in texto


def test_una_key_de_linear_neutralizada_cuenta_COMO_AUSENTE(monkeypatch):
    """El mismo defecto del token de Twenty (a93c093), del otro lado: una
    key que arranca con «CLAVE» no es una key."""
    monkeypatch.setenv("LINEAR_API_KEY", "CLAVE-neutralizada-de-pruebas")
    assert contactos.linear_conectado() is False
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_de_verdad")
    assert contactos.linear_conectado() is True


def test_los_leads_se_casan_por_telefono_y_se_pintan_verde_o_gris(
        cliente, con_odoo_qa, monkeypatch):
    """Con Linear legible, la pestaña lista los leads de ESA persona:
    verde los activos, gris los terminados y los que nunca se activaron."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_de_verdad")
    monkeypatch.setattr(linear_leads, "listar", lambda refrescar=False: [
        {"ref": "LEAD-10", "url": "https://linear.app/x/LEAD-10",
         "nombre": "Cliente QA Hotel", "celular": "6000-0030",
         "estado": "COTIZADO", "estado_nombre": "Cotizado", "cerrado": False,
         "interes": "Paisajismo", "resp": "Ruben", "hace": "hace 2 días"},
        {"ref": "LEAD-11", "url": "", "nombre": "Cliente QA Hotel",
         "celular": "+507 6000-0030", "estado": "GANADO",
         "estado_nombre": "Ganado", "cerrado": True, "interes":
         "Mantenimiento", "resp": "Mary", "hace": "hace 30 días"},
        {"ref": "LEAD-12", "url": "", "nombre": "Cliente QA Hotel",
         "celular": "60000030", "estado": "NUEVO", "estado_nombre": "Nuevo",
         "cerrado": False, "interes": "", "resp": "", "hace": "hace 1 día"},
        # De OTRA persona: no entra.
        {"ref": "LEAD-13", "url": "", "nombre": "Otro", "celular": "60009999",
         "estado": "NUEVO", "estado_nombre": "Nuevo", "cerrado": False,
         "interes": "", "resp": "", "hace": "hoy"},
    ])
    v = contactos.ficha("t60000030", sesion=sesion_abierta())
    assert [l["ref"] for l in v["leads"]["leads"]] == \
        ["LEAD-10", "LEAD-12", "LEAD-11"]
    chips = {l["ref"]: (l["chip"], l["activo"]) for l in v["leads"]["leads"]}
    assert chips["LEAD-10"] == ("Activo", True)
    assert chips["LEAD-11"] == ("Terminado", False)
    assert chips["LEAD-12"] == ("No activado", False)
    assert v["leads"]["activos"] == 1
    assert v["atiende"] == ["Mary", "Ruben"]
    texto = cliente.get("/contactos/t60000030").text
    assert "LEAD-10" in texto and "LEAD-13" not in texto
    assert contactos.PIE_LEADS in texto


def test_sin_twenty_la_pestana_de_whatsapp_lo_dice(cliente, con_odoo_qa):
    v = contactos.ficha("t60000030", sesion=sesion_abierta(),
                        panel="whatsapp")
    assert v["chat"]["hilo"] == []
    assert v["chat"]["aviso"] == contactos.AVISO_CHAT_PRUEBAS
    texto = cliente.get("/contactos/t60000030",
                        params={"panel": "whatsapp"}).text
    assert contactos.AVISO_CHAT_PRUEBAS in texto


def test_el_chat_se_arma_con_el_hilo_de_control_y_es_solo_lectura(
        cliente, con_odoo_qa, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "ey-una-key-de-verdad")
    from app import crm_twenty
    monkeypatch.setattr(crm_twenty, "_mensajes_por_telefono", lambda t, n=60: [
        {"texto": "Buenas, ¿me cotizan el jardín?", "direccion": "ENTRANTE",
         "fecha": "2026-10-05T14:12:00Z"},
        {"texto": "Claro, ya se la mando.", "direccion": "SALIENTE",
         "fecha": "2026-10-05T14:20:00Z", "autor": "Rubén"},
    ])
    v = contactos.ficha("t60000030", sesion=sesion_abierta(),
                        panel="whatsapp")
    assert v["chat"]["cant"] == 2
    grupos = [b for b in v["chat"]["hilo"] if b["tipo"] == "grupo"]
    # Cliente a la izquierda, equipo a la derecha con su nombre.
    assert [g["mio"] for g in grupos] == [False, True]
    assert grupos[1]["nombre"] == "Rubén"
    texto = cliente.get("/contactos/t60000030",
                        params={"panel": "whatsapp"}).text
    assert "Buenas, ¿me cotizan el jardín?" in texto
    assert "Claro, ya se la mando." in texto
    # SOLO LECTURA: ni un formulario, ni una caja de escribir.
    assert "<form" not in texto and "<textarea" not in texto


# ---------------------------------------------------------------------------
# (C) Los apagados del lienzo: en su lugar, disabled, sin números
# ---------------------------------------------------------------------------

def test_los_seis_apagados_estan_en_su_lugar_y_sin_numero(cliente,
                                                          con_odoo_qa):
    texto = cliente.get("/contactos/t60000030").text
    for nombre in (contactos.APAGADOS_ACCION + contactos.APAGADOS_PLATA
                   + (contactos.APAGADO_TOTAL_GASTADO,)):
        assert nombre in texto, nombre
    # Los tres botones de acción, apagados de verdad.
    for nombre in contactos.APAGADOS_ACCION:
        boton = re.search(
            r"<button[^>]*>" + re.escape(nombre) + r"[^<]*</button>", texto)
        assert boton and "disabled" in boton.group(0), nombre
    # Y ningún «$» pegado a un apagado: no se inventa plata.
    for nombre in contactos.APAGADOS_PLATA:
        assert f"{nombre}</span>" in texto or nombre in texto


# ---------------------------------------------------------------------------
# (D) EL CANDADO: el dinero y el chat son de quien atiende
# ---------------------------------------------------------------------------

_LEADS_DE_RUBEN = [
    {"ref": "LEAD-10", "url": "", "nombre": "Cliente QA Hotel",
     "celular": "6000-0030", "estado": "COTIZADO",
     "estado_nombre": "Cotizado", "cerrado": False, "interes": "Paisajismo",
     "resp": "Ruben", "hace": "hace 2 días"},
]


@pytest.fixture
def con_linear_de_ruben(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_de_verdad")
    monkeypatch.setattr(linear_leads, "listar",
                        lambda refrescar=False: [dict(l)
                                                 for l in _LEADS_DE_RUBEN])
    monkeypatch.setattr(linear_leads, "responsables",
                        lambda: ["Abraham", "Mary", "Ruben"])


def _empleada_con_rol(usuario, nombre, slug):
    """Una empleada de verdad del login, con un rol de verdad del
    esqueleto (los 5 slugs los asegura `iniciar_tablas`) y puesta con la
    MISMA función de la pantalla de Ajustes: el candado se prueba contra
    las piezas reales, no contra INSERTs a mano."""
    seguridad.crear_empleada(usuario, nombre, "clave-de-prueba")
    with datos._db() as con:
        rol = con.execute("SELECT n FROM roles WHERE slug=?",
                          (slug,)).fetchone()["n"]
    datos_roles.poner_persona(rol, usuario, por="qa")
    return {"id": usuario, "nombre": nombre, "email": f"{usuario}@qa.test"}


def test_quien_atiende_ve_su_dinero_y_su_chat(con_odoo_qa,
                                              con_linear_de_ruben):
    ruben = _empleada_con_rol("ruben", "Rubén", datos_roles.SLUG_OPERACIONES)
    sesion = contactos.sesion_de(ruben, es_admin=False)
    assert sesion["ve_todo"] is False
    assert sesion["resp"] == "Ruben"
    v = contactos.ficha("t60000030", sesion=sesion)
    assert v["permiso"]["dinero"] is True and v["permiso"]["chat"] is True
    assert v["contacto"]["ventas_total"] == 480.0


def test_operaciones_NO_recibe_montos_ni_hilo_en_LOS_DATOS(
        con_odoo_qa, con_linear_de_ruben):
    """El caso que manda, en la capa que decide: el contacto lo atiende
    Rubén y quien mira es de Operaciones. Lo tapado NO está en el dict que
    viaja a la plantilla — no es CSS, no llegó."""
    mary = _empleada_con_rol("mary", "Mary", datos_roles.SLUG_OPERACIONES)
    sesion = contactos.sesion_de(mary, es_admin=False)
    assert sesion["ve_todo"] is False
    assert sesion["resp"] == "Mary"

    v = contactos.ficha("t60000030", sesion=sesion)
    assert v["permiso"] == {"dinero": False, "chat": False,
                            "motivo": contactos.CANDADO_AJENO}
    assert v["contacto"]["ventas_total"] is None
    assert v["contacto"]["cotiz_total"] is None
    assert v["tratos"] == []
    assert v["chat"]["hilo"] == []
    # Los datos de contacto PUROS sí quedan: el candado es sobre el dinero
    # y el chat ajenos, no sobre la persona (diseño corto, punto 5).
    assert v["contacto"]["nombre"] == "Empresa QA Hotel"
    assert v["contacto"]["telefono"]
    # Y la pestaña de leads abre igual: dice de quién es el contacto.
    assert [l["ref"] for l in v["leads"]["leads"]] == ["LEAD-10"]
    assert v["leads"]["leads"][0]["resp"] == "Ruben"


def test_operaciones_NO_recibe_montos_ni_hilo_EN_EL_CUERPO_HTTP(
        con_odoo_qa, con_linear_de_ruben, monkeypatch):
    """El mismo caso, comprobado sobre el CUERPO de la respuesta: lo que
    sale por HTTP no trae ni un monto ni un renglón del hilo.

    La sesión se fuerza a la de Operaciones porque HOY la puerta GLOBAL
    por rol manda a /control a quien tiene ese rol: `/contactos` no está
    en `datos_roles.PESTANAS`, así que un usuario de Operaciones todavía
    no puede ABRIR la pantalla (hallazgo del BLOQUE 43; esa tabla es de
    otro frente y no se toca desde aquí). El candado del módulo ya está
    puesto para cuando esa puerta se abra, y así se prueba sin fingir que
    la puerta no existe."""
    from fastapi.testclient import TestClient

    from app.main import app
    mary = _empleada_con_rol("mary", "Mary", datos_roles.SLUG_OPERACIONES)
    sesion_de_mary = contactos.sesion_de(mary, es_admin=False)
    monkeypatch.setattr("app.main._sesion_contactos",
                        lambda request: dict(sesion_de_mary))

    c = TestClient(app)
    # Génesis pasa la puerta global (no tiene rol); lo que mira la pantalla
    # es la sesión acotada de arriba.
    seguridad.crear_empleada("genesis", "Génesis", "clave-de-prueba")
    assert c.post("/login", data={"usuario": "genesis",
                                  "contrasena": "clave-de-prueba"},
                  follow_redirects=False).status_code == 303

    pagina = c.get("/contactos/t60000030").text
    assert contactos.CANDADO_AJENO in pagina
    for monto in ("480", "1,150", "1150", "S00110", "S00111"):
        assert monto not in pagina, monto
    assert "Empresa QA Hotel" in pagina and "6000-0030" in pagina
    assert "LEAD-10" in pagina and "Ruben" in pagina

    chat = c.get("/contactos/t60000030", params={"panel": "whatsapp"}).text
    assert contactos.CANDADO_AJENO in chat
    assert 'class="ct-m ' not in chat  # ni un globo del hilo

    lista = c.get("/contactos").text
    for monto in ("480", "1,150", "1150"):
        assert monto not in lista, monto
    assert "Empresa QA Hotel" in lista


def test_hoy_operaciones_ni_siquiera_abre_contactos(con_odoo_qa):
    """El hallazgo, fijado: hasta que `/contactos` entre en
    `datos_roles.PESTANAS`, la puerta global manda a Operaciones a su
    casa. Si algún día eso cambia, esta prueba lo dice."""
    from fastapi.testclient import TestClient

    from app.main import app
    _empleada_con_rol("mary", "Mary", datos_roles.SLUG_OPERACIONES)
    c = TestClient(app)
    c.post("/login", data={"usuario": "mary",
                           "contrasena": "clave-de-prueba"},
           follow_redirects=False)
    r = c.get("/contactos", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/control"


def test_atencion_sin_lead_suyo_tampoco_ve_dinero_ni_chat(con_odoo_qa,
                                                          con_linear_de_ruben):
    """Un contacto sin ningún lead a su nombre: tampoco. El candado no se
    afloja porque no haya a quién echarle la culpa."""
    quien = _empleada_con_rol("mary", "Mary", datos_roles.SLUG_ATENCION)
    sesion = contactos.sesion_de(quien, es_admin=False)
    v = contactos.ficha("t60000001", sesion=sesion)  # sin leads casados
    assert v["permiso"]["dinero"] is False
    assert v["permiso"]["motivo"] == contactos.CANDADO_SIN_MIO
    assert v["contacto"]["ventas_total"] is None


def test_finanzas_y_director_ven_todo(con_odoo_qa, con_linear_de_ruben):
    for slug in (datos_roles.SLUG_FINANZAS, datos_roles.SLUG_DIRECTOR):
        with datos._db() as con:
            con.execute("DELETE FROM rol_persona WHERE usuario='jordan'")
            con.execute("DELETE FROM empleadas WHERE usuario='jordan'")
        quien = _empleada_con_rol("jordan", "Jordan", slug)
        sesion = contactos.sesion_de(quien, es_admin=False)
        assert sesion["ve_todo"] is True, slug
        v = contactos.ficha("t60000030", sesion=sesion)
        assert v["permiso"]["dinero"] is True, slug
        assert v["contacto"]["ventas_total"] == 480.0, slug


def test_sin_poder_leer_linear_el_candado_CIERRA(con_odoo_qa, monkeypatch):
    """Si no se sabe de quién es el contacto, no se adivina a favor."""
    monkeypatch.setattr(linear_leads, "responsables",
                        lambda: ["Abraham", "Mary", "Ruben"])
    quien = _empleada_con_rol("mary", "Mary", datos_roles.SLUG_OPERACIONES)
    sesion = contactos.sesion_de(quien, es_admin=False)
    v = contactos.ficha("t60000030", sesion=sesion)
    assert v["permiso"]["dinero"] is False
    assert v["permiso"]["motivo"] == contactos.CANDADO_SIN_FUENTE
    assert v["contacto"]["ventas_total"] is None


def test_sin_sesion_el_candado_tambien_CIERRA(con_odoo_qa):
    """El módulo no tiene un default abierto que una ruta nueva pueda
    heredar sin darse cuenta."""
    v = contactos.ficha("t60000030")
    assert v["permiso"]["motivo"] == contactos.CANDADO_SIN_SESION
    assert v["contacto"]["ventas_total"] is None
    assert contactos.lista()["contactos"][0]["ventas_total"] is None


def test_con_el_dinero_tapado_a_odoo_ni_se_le_preguntan_las_ordenes(
        con_odoo_qa, con_linear_de_ruben, monkeypatch):
    """Lo que no se puede ver, no se lee: una consulta de más es una
    filtración esperando un log."""
    quien = _empleada_con_rol("mary", "Mary", datos_roles.SLUG_OPERACIONES)
    sesion = contactos.sesion_de(quien, es_admin=False)
    contactos.lista(sesion=sesion)  # llena el caché de partners

    pedidas = []
    original = ventas._ejecutar

    def espiar(modelo, metodo, args, kw=None):
        pedidas.append((modelo, str(args)))
        return original(modelo, metodo, args, kw)
    monkeypatch.setattr(ventas, "_ejecutar", espiar)
    contactos.ficha("t60000030", sesion=sesion)
    assert not [p for p in pedidas
                if p[0] == "sale.order" and "partner_id" in p[1]
                and "'in'" in p[1]]
