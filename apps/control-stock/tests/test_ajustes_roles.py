"""Item 1 de Jay (5/10/2026): Settings de roles, marcas, tipos de venta,
llegadas y términos por defecto.

Lo que se prueba, del decision record y del diseño corto:
- la migración al vuelo es idempotente y las semillas entran SOLO en tablas
  vírgenes (lo editado por pantalla no se re-siembra nunca);
- la semilla de personas se casa contra empleadas reales del login y no
  inventa a nadie;
- renombrar, duplicar (mismas personas, sin deber) y varias personas por
  rol; quitar persona;
- los 3 deberes se reasignan y NUNCA quedan vacíos en silencio (el código
  de aviso sube y deberes_estado() lo pinta);
- los catálogos se DESACTIVAN, jamás se borran, y las funciones para los
  consumidores (items 3 y 5) devuelven solo activos;
- el candado admin: cada POST nuevo rechaza con 403 a quien no es admin.
"""

import pytest
from fastapi.testclient import TestClient

from app import control, datos, datos_roles, linear_leads, seguridad
from app.main import app


@pytest.fixture(autouse=True)
def sin_linear(monkeypatch):
    """Como en las pruebas de admins: sin Linear ni Twenty, con el tablero
    de muestra, para que la pestaña Ajustes se pinte sin salir a la red."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()


@pytest.fixture(autouse=True)
def tablas_control(db_limpia):
    control.iniciar_tablas()


def _tablas():
    with datos._db() as con:
        return {f["name"] for f in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


def _entrar(usuario, nombre):
    seguridad.crear_empleada(usuario, nombre, "clave-de-prueba")
    c = TestClient(app)
    r = c.post("/login",
               data={"usuario": usuario, "contrasena": "clave-de-prueba"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _rol_por_nombre(nombre, solo_activos=False):
    return next(r for r in datos_roles.listar_roles(solo_activos)
                if r["nombre"] == nombre)


# ---------------------------------------------------------------------------
# Migración y semillas
# ---------------------------------------------------------------------------

def test_migracion_idempotente_y_semillas_completas(db_limpia):
    esperadas = {"roles", "rol_persona", "marcas", "tipos_venta", "llegadas"}
    assert esperadas <= _tablas()
    # Correr la migración de nuevo no truena ni duplica semillas.
    datos_roles.iniciar_tablas()
    datos_roles.iniciar_tablas()
    roles = datos_roles.listar_roles(solo_activos=False)
    assert [r["nombre"] for r in roles] == [
        "Eventos", "PH y proyectos grandes",
        "Ventas Plantas Panamá / Vivero Rose", "System manager",
        "Operaciones y banco", "Owner view"]
    # Los 3 deberes vienen marcados en sus roles de la semilla.
    assert {r["nombre"]: r["deber"] for r in roles if r["deber"]} == {
        "System manager": "system_manager",
        "Operaciones y banco": "operations",
        "Owner view": "owner_view"}
    assert [m["nombre"] for m in datos_roles.marcas_activas()] == [
        "Plantas Panamá", "Vivero Rose"]
    tipos = datos_roles.tipos_venta_activos()
    assert [t["nombre"] for t in tipos] == [
        "plant retail", "garden", "maintenance", "PH",
        "commercial project", "rental event", "other"]
    # Los términos por defecto del decision record, con su bandera: el
    # retail de plantas es 100% antes y SIN override visible (nada de
    # crédito por accidente); el evento es 50/50 y el resto a medida.
    por_nombre = {t["nombre"]: t for t in tipos}
    assert por_nombre["plant retail"]["termino_default"] == "100% antes de proceder"
    assert por_nombre["plant retail"]["override_visible"] == 0
    assert por_nombre["rental event"]["termino_default"] == "50% depósito, 50% al cumplir"
    assert por_nombre["rental event"]["override_visible"] == 1
    assert por_nombre["PH"]["termino_default"] == "A medida"
    assert [l["nombre"] for l in datos_roles.llegadas_activas()] == [
        "WhatsApp", "Teléfono", "Referido", "Prospección fría", "Email", "Otro"]


def test_las_semillas_no_se_resiembran_sobre_lo_editado(db_limpia):
    # Abraham renombra una marca y desactiva una llegada...
    marca = datos_roles.marcas_activas()[0]
    assert datos_roles.catalogo_renombrar("marcas", marca["n"], "PP") is None
    llegada = datos_roles.llegadas_activas()[0]
    assert datos_roles.catalogo_activar("llegadas", llegada["n"], False) is None
    # ...y un arranque nuevo (deploy, reinicio) respeta todo tal cual.
    datos_roles.iniciar_tablas()
    assert [m["nombre"] for m in datos_roles.marcas_activas()] == ["PP", "Vivero Rose"]
    assert len(datos_roles.catalogo_completo("marcas")) == 2
    assert len(datos_roles.llegadas_activas()) == 5
    assert len(datos_roles.catalogo_completo("llegadas")) == 6


def test_la_semilla_casa_personas_contra_empleadas_reales(tmp_path, monkeypatch):
    # Una base nueva donde las empleadas del login YA existen al sembrar
    # (producción): la semilla las encuentra por usuario, email o nombre.
    monkeypatch.setenv("CONTROL_STOCK_DB", str(tmp_path / "semilla.db"))
    datos.iniciar_db()
    seguridad.crear_empleada("mary@viverorose.com", "Mary", "x")
    seguridad.crear_empleada("ruben@viverorose.com", "Rubén", "x")
    seguridad.crear_empleada("admin@viverorose.com", "Abraham", "x")
    seguridad.crear_empleada("info@viverorose.com", "Salomón", "x")
    datos_roles.iniciar_tablas()
    assert [p["nombre"] for p in _rol_por_nombre("Eventos")["personas"]] == ["Mary"]
    assert [p["nombre"] for p in _rol_por_nombre("PH y proyectos grandes")["personas"]] == ["Rubén"]
    assert [p["nombre"] for p in _rol_por_nombre("System manager")["personas"]] == ["Abraham"]
    # Salomón entra por la pista del email (info@).
    assert [p["nombre"] for p in _rol_por_nombre("Operaciones y banco")["personas"]] == ["Salomón"]
    # Jordan no tiene login todavía: NO se inventa a nadie, y el estado de
    # los deberes lo dice en voz alta en vez de callárselo.
    assert _rol_por_nombre("Owner view")["personas"] == []
    owner = next(d for d in datos_roles.deberes_estado()
                 if d["clave"] == "owner_view")
    assert owner["aviso"] == "sin_persona"
    assert owner["rol"]["nombre"] == "Owner view"


def test_sin_empleadas_nadie_se_siembra_y_los_deberes_avisan(db_limpia):
    # db_limpia nace sin empleadas: cero personas inventadas.
    assert all(r["personas"] == [] for r in datos_roles.listar_roles(False))
    assert all(d["aviso"] == "sin_persona" for d in datos_roles.deberes_estado())


# ---------------------------------------------------------------------------
# Roles: renombrar, duplicar, personas
# ---------------------------------------------------------------------------

def test_renombrar_duplicar_y_varias_personas(db_limpia):
    seguridad.crear_empleada("ana", "Ana", "x")
    seguridad.crear_empleada("beto", "Beto", "x")
    eventos = _rol_por_nombre("Eventos")
    # Varias personas en el mismo rol (y ponerla dos veces no duplica).
    assert datos_roles.poner_persona(eventos["n"], "ana", "Abraham") is None
    assert datos_roles.poner_persona(eventos["n"], "beto", "Abraham") is None
    assert datos_roles.poner_persona(eventos["n"], "ana", "Abraham") is None
    assert [p["usuario"] for p in _rol_por_nombre("Eventos")["personas"]] == ["ana", "beto"]
    # Solo empleadas activas entran a un rol.
    assert datos_roles.poner_persona(eventos["n"], "fantasma", "Abraham") == "empleada_invalida"
    # Renombrar: el nombre cambia, las personas quedan.
    assert datos_roles.renombrar_rol(eventos["n"], "Eventos y bodas") is None
    assert len(_rol_por_nombre("Eventos y bodas")["personas"]) == 2
    assert datos_roles.renombrar_rol(eventos["n"], "  ") == "vacio"
    assert datos_roles.renombrar_rol(eventos["n"], "owner VIEW") == "repetido"
    # Duplicar (los pods): mismas personas, sin deber, nombre propio.
    error, nuevo = datos_roles.duplicar_rol(eventos["n"], "Eventos pod B")
    assert error is None
    copia = _rol_por_nombre("Eventos pod B")
    assert copia["n"] == nuevo and copia["deber"] is None
    assert [p["usuario"] for p in copia["personas"]] == ["ana", "beto"]
    # Sin nombre, la copia se llama sola y no choca.
    error, _ = datos_roles.duplicar_rol(eventos["n"])
    assert error is None
    assert _rol_por_nombre("Copia de Eventos y bodas")
    error, _ = datos_roles.duplicar_rol(eventos["n"], "eventos POD b")
    assert error == "repetido"


def test_quitar_persona_y_el_deber_nunca_queda_vacio_en_silencio(db_limpia):
    seguridad.crear_empleada("ana", "Ana", "x")
    manager = _rol_por_nombre("System manager")
    eventos = _rol_por_nombre("Eventos")
    datos_roles.poner_persona(manager["n"], "ana", "Abraham")
    datos_roles.poner_persona(eventos["n"], "ana", "Abraham")
    # Quitarla de un rol sin deber: silencio normal.
    assert datos_roles.quitar_persona(eventos["n"], "ana") is None
    # Quitar a la ÚLTIMA persona de un rol con deber: se aplica (reasignar
    # es el punto) pero el código lo grita, y deberes_estado() también.
    assert datos_roles.quitar_persona(manager["n"], "ana") == "deber_sin_persona"
    estado = next(d for d in datos_roles.deberes_estado()
                  if d["clave"] == "system_manager")
    assert estado["aviso"] == "sin_persona"
    assert datos_roles.quien_ocupa("system_manager")["personas"] == []


def test_reasignar_deber_a_otro_rol(db_limpia):
    seguridad.crear_empleada("ana", "Ana", "x")
    eventos = _rol_por_nombre("Eventos")
    datos_roles.poner_persona(eventos["n"], "ana", "Abraham")
    # El deber se muda: el rol viejo queda normal, el nuevo lo carga.
    assert datos_roles.asignar_deber("system_manager", eventos["n"]) is None
    assert _rol_por_nombre("System manager")["deber"] is None
    ocupa = datos_roles.quien_ocupa("system_manager")
    assert ocupa["rol"]["nombre"] == "Eventos"
    assert [p["usuario"] for p in ocupa["personas"]] == ["ana"]
    # Un rol carga UN deber: el que ya tiene otro se rechaza.
    assert datos_roles.asignar_deber("operations", eventos["n"]) == "rol_con_otro_deber"
    # Y un deber inventado no existe.
    assert datos_roles.asignar_deber("gerente_general", eventos["n"]) == "deber_invalido"
    # Mudarlo a un rol sin gente se aplica, pero avisa.
    vacio = _rol_por_nombre("PH y proyectos grandes")
    assert datos_roles.asignar_deber("operations", vacio["n"]) == "deber_sin_persona"


# ---------------------------------------------------------------------------
# Catálogos: desactivar, no borrar; solo activos para los consumidores
# ---------------------------------------------------------------------------

def test_catalogos_desactivan_en_vez_de_borrar(db_limpia):
    tipo = next(t for t in datos_roles.tipos_venta_activos()
                if t["nombre"] == "other")
    assert datos_roles.catalogo_activar("tipos_venta", tipo["n"], False) is None
    # La fila sigue viva en la base, apagada; los consumidores no la ven.
    completos = datos_roles.catalogo_completo("tipos_venta")
    assert len(completos) == 7
    apagada = next(t for t in completos if t["n"] == tipo["n"])
    assert apagada["activo"] == 0
    assert all(t["nombre"] != "other" for t in datos_roles.tipos_venta_activos())
    # Reactivar la repone tal cual, con su término intacto.
    assert datos_roles.catalogo_activar("tipos_venta", tipo["n"], True) is None
    repuesta = next(t for t in datos_roles.tipos_venta_activos()
                    if t["nombre"] == "other")
    assert repuesta["termino_default"] == "A medida"
    # Una tabla que no es catálogo no se toca ni por accidente.
    assert datos_roles.catalogo_activar("empleadas", 1, False) == "catalogo_invalido"
    with pytest.raises(ValueError):
        datos_roles.catalogo_completo("empleadas")


def test_agregar_renombrar_y_los_tocayos(db_limpia):
    assert datos_roles.catalogo_agregar("marcas", "Rose Garden") is None
    assert datos_roles.catalogo_agregar("marcas", "rose garden") == "repetido"
    assert datos_roles.catalogo_agregar("llegadas", "") == "vacio"
    # El tocayo cuenta aunque esté desactivado: reactivar no debe chocar.
    nueva = next(m for m in datos_roles.marcas_activas()
                 if m["nombre"] == "Rose Garden")
    datos_roles.catalogo_activar("marcas", nueva["n"], False)
    assert datos_roles.catalogo_agregar("marcas", "ROSE GARDEN") == "repetido"
    # Renombrar sin acentos tampoco pisa a nadie, y a sí misma sí puede.
    marca = datos_roles.marcas_activas()[0]
    assert datos_roles.catalogo_renombrar("marcas", marca["n"], "Plantas Panama") is None
    assert datos_roles.catalogo_renombrar("marcas", marca["n"], "vivero rosé") == "repetido"


def test_fijar_termino_y_override(db_limpia):
    tipo = next(t for t in datos_roles.tipos_venta_activos()
                if t["nombre"] == "garden")
    assert datos_roles.fijar_termino(tipo["n"], "50% y 50% al entregar", False) is None
    guardado = next(t for t in datos_roles.tipos_venta_activos()
                    if t["n"] == tipo["n"])
    assert guardado["termino_default"] == "50% y 50% al entregar"
    assert guardado["override_visible"] == 0
    assert datos_roles.fijar_termino(9999, "x", True) == "no_existe"


# ---------------------------------------------------------------------------
# El candado admin y la pantalla
# ---------------------------------------------------------------------------

RUTAS_POST = (
    ("/ajustes/roles/renombrar", {"rol": "1", "nombre": "Hackeado"}),
    ("/ajustes/roles/duplicar", {"rol": "1"}),
    ("/ajustes/roles/persona/poner", {"rol": "1", "usuario": "genesis"}),
    ("/ajustes/roles/persona/quitar", {"rol": "1", "usuario": "genesis"}),
    ("/ajustes/deberes", {"deber": "owner_view", "rol": "1"}),
    ("/ajustes/catalogo/agregar", {"tabla": "marcas", "nombre": "Pirata"}),
    ("/ajustes/catalogo/renombrar", {"tabla": "marcas", "n": "1", "nombre": "Pirata"}),
    ("/ajustes/catalogo/activar", {"tabla": "marcas", "n": "1", "activo": "0"}),
    ("/ajustes/tipos/termino", {"n": "1", "termino": "gratis", "override": "1"}),
)


def test_todos_los_post_rechazan_a_quien_no_es_admin(cliente, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    for ruta, cuerpo in RUTAS_POST:
        r = cliente.post(ruta, data=cuerpo, follow_redirects=False)
        assert r.status_code == 403, ruta
    # Y nada quedó tocado por el intento.
    assert _rol_por_nombre("Eventos")  # sigue con su nombre de semilla
    assert all(m["nombre"] != "Pirata"
               for m in datos_roles.catalogo_completo("marcas"))
    # La pestaña de un no-admin ni pinta la sección.
    pagina = cliente.get("/?tab=ajustes").text
    assert "Roles y catálogos de venta" not in pagina
    assert "/ajustes/roles/renombrar" not in pagina


def test_el_admin_opera_todo_por_pantalla(cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    _entrar("marta", "Marta")

    # La sección existe y trae las semillas pintadas.
    pagina = cliente.get("/?tab=ajustes").text
    assert "Roles y catálogos de venta" in pagina
    assert "PH y proyectos grandes" in pagina
    assert "100% antes de proceder" in pagina
    assert "Prospección fría" in pagina

    # Renombrar un rol.
    eventos = _rol_por_nombre("Eventos")
    r = cliente.post("/ajustes/roles/renombrar",
                     data={"rol": eventos["n"], "nombre": "Eventos y bodas"},
                     follow_redirects=False)
    assert "aviso=rol-renombrado" in r.headers["location"]
    # Poner y quitar personas (Marta entra al rol).
    r = cliente.post("/ajustes/roles/persona/poner",
                     data={"rol": eventos["n"], "usuario": "marta"},
                     follow_redirects=False)
    assert "aviso=persona-puesta" in r.headers["location"]
    assert [p["usuario"] for p in _rol_por_nombre("Eventos y bodas")["personas"]] == ["marta"]
    # Duplicar desde la pantalla.
    r = cliente.post("/ajustes/roles/duplicar", data={"rol": eventos["n"]},
                     follow_redirects=False)
    assert "aviso=rol-duplicado" in r.headers["location"]
    assert _rol_por_nombre("Copia de Eventos y bodas")["personas"][0]["usuario"] == "marta"
    # Reasignar un deber por pantalla.
    r = cliente.post("/ajustes/deberes",
                     data={"deber": "owner_view", "rol": eventos["n"]},
                     follow_redirects=False)
    assert "aviso=deber-asignado" in r.headers["location"]
    assert datos_roles.quien_ocupa("owner_view")["rol"]["nombre"] == "Eventos y bodas"
    # Quitar a la última persona de ese rol con deber: el aviso lo dice.
    r = cliente.post("/ajustes/roles/persona/quitar",
                     data={"rol": eventos["n"], "usuario": "marta"},
                     follow_redirects=False)
    assert "aviso=deber-sin-persona" in r.headers["location"]
    pagina = cliente.get("/?tab=ajustes&aviso=deber-sin-persona").text
    assert "sin persona" in pagina
    # Catálogos: desactivar por pantalla apaga, no borra.
    marca = datos_roles.marcas_activas()[0]
    r = cliente.post("/ajustes/catalogo/activar",
                     data={"tabla": "marcas", "n": marca["n"], "activo": "0"},
                     follow_redirects=False)
    assert "aviso=catalogo-apagado" in r.headers["location"]
    assert len(datos_roles.catalogo_completo("marcas")) == 2
    assert len(datos_roles.marcas_activas()) == 1
    # El término de un tipo, con su casilla de override.
    tipo = datos_roles.tipos_venta_activos()[0]
    r = cliente.post("/ajustes/tipos/termino",
                     data={"n": tipo["n"], "termino": "100% al confirmar"},
                     follow_redirects=False)
    assert "aviso=termino-guardado" in r.headers["location"]
    guardado = next(t for t in datos_roles.tipos_venta_activos()
                    if t["n"] == tipo["n"])
    assert guardado["termino_default"] == "100% al confirmar"
    assert guardado["override_visible"] == 0  # sin la casilla, queda en no
    # Un nombre repetido rebota con su aviso y sin guardar.
    r = cliente.post("/ajustes/catalogo/agregar",
                     data={"tabla": "llegadas", "nombre": "whatsapp"},
                     follow_redirects=False)
    assert "aviso=nombre-repetido" in r.headers["location"]
    assert len(datos_roles.catalogo_completo("llegadas")) == 6


def test_consumidores_para_items_3_y_5_devuelven_solo_activos(db_limpia):
    """El contrato que van a leer el lead a mano (item 3) y los términos de
    la cotización (item 5): listas de activos, con el término y la bandera
    ya adentro, y quien_ocupa(deber) para saber a quién le toca."""
    llegada = datos_roles.llegadas_activas()[0]
    datos_roles.catalogo_activar("llegadas", llegada["n"], False)
    assert all(l["n"] != llegada["n"] for l in datos_roles.llegadas_activas())
    assert {"n", "nombre", "activo"} <= set(datos_roles.marcas_activas()[0])
    assert {"n", "nombre", "termino_default", "override_visible"} <= set(
        datos_roles.tipos_venta_activos()[0])
    # quien_ocupa de un deber con rol pero sin gente: rol sí, personas [].
    ocupa = datos_roles.quien_ocupa("operations")
    assert ocupa["rol"]["nombre"] == "Operaciones y banco"
    assert ocupa["personas"] == []
