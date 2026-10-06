"""Contactos (/contactos) — pantalla SOLO LECTURA del BLOQUE 37 (item 2
de Jay; diseño corto docs/DISENO-ITEM2-contactos.md del repo
plantaspanama).

Lo que se prueba, por regla:

- Renderiza CON y SIN datos, y cada hueco se DICE (Twenty no conectado,
  Odoo sin configurar o caído) — nunca una lista vacía que mienta.
- El casamiento es por teléfono NORMALIZADO (solo dígitos, sin 507) y EN
  LECTURA: ninguna llamada de escritura sale hacia Odoo (el doble de
  `_ejecutar` revienta con cualquier método que no sea search_read).
- El buscador (?q=) filtra nombre y teléfono; los filtros GET filtran
  bien y uno inválido cae a «Todos»; «Sin responsable» va APAGADO.
- Los botones que escribirían (Nuevo contacto, Nuevo lead) van disabled
  con «Todavía no»; NINGUNA ruta POST nueva bajo /contactos.
- El nombre plano solo SUGIERE «posible mismo»: dos apariciones con el
  mismo nombre y sin teléfono NO se amarran en un solo contacto.

Datos QA solamente — acá no entra ningún nombre de cliente real.
"""

import re

import pytest

from app import contactos, datos, ventas

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
    """Como el 8095: sin Twenty y, por defecto, sin Odoo."""
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
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


def test_con_twenty_configurado_dice_que_llega_despues(cliente, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    texto = cliente.get("/contactos").text
    assert contactos.AVISO_TWENTY_LUEGO in texto
    assert contactos.AVISO_TWENTY_PRUEBAS not in texto


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
    v = contactos.lista()
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
    f = contactos.ficha(f"lv{n1}")
    assert [p["id"] for p in f["posibles"]] == [f"ls{n2}"]
    texto = cliente.get(f"/contactos/lv{n1}").text
    assert "Posible mismo contacto" in texto


def test_tipo_empresa_sale_de_odoo(con_odoo_qa):
    v = contactos.lista()
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
    v = contactos.lista()
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
    f = contactos.ficha("t60000030")
    ordenes = [t["orden"] for t in f["tratos"]]
    assert ordenes.count("S00111") == 1
    assert "S00110" in ordenes  # la confirmada de Odoo también sale
    local = next(t for t in f["tratos"] if t["orden"] == "S00111")
    assert local["href"] == f"/venta/estado/servicio/{n}"
    texto = cliente.get("/contactos/t60000030").text
    assert "S00110" in texto and "S00111" in texto
    assert f'href="/venta/estado/servicio/{n}"' in texto


def test_ficha_kpis_con_odoo(con_odoo_qa):
    f = contactos.ficha("t60000030")
    assert f["contacto"]["ventas_n"] == 1
    assert f["contacto"]["ventas_total"] == 480.0
    assert f["contacto"]["cotiz_total"] == 1150.0


def test_ficha_solo_local_sin_odoo(cliente):
    n = _venta_local(cliente="Cliente QA Lirio", celular="60000002",
                     orden="S00120", orden_id=120, total=12.0)
    f = contactos.ficha("t60000002")
    assert f["contacto"]["ventas_n"] == 1
    assert f["contacto"]["ventas_total"] == 12.0
    assert f["tratos"][0]["href"] == f"/venta/estado/venta/{n}"
    texto = cliente.get("/contactos/t60000002").text
    assert "Cliente QA Lirio" in texto


def test_ficha_dice_sus_huecos(cliente, con_odoo_qa):
    texto = cliente.get("/contactos/t60000030").text
    assert contactos.AVISO_LEADS_LUEGO in texto
    assert contactos.AVISO_CITAS_LUEGO in texto
    assert contactos.AVISO_TWENTY_PRUEBAS in texto


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
