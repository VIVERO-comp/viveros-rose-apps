"""El rol «Inventario» (BLOQUE 13 + spec de Omar, 5/10/2026).

Lo que se prueba, del plan aprobado con review del Arquitecto
(docs/DISENO-rol-inventario.md de plantaspanama):

- la semilla: el rol existe siempre, con slug inmutable 'inventario' y SIN
  persona (a Omar lo invita Korto por la pantalla del item 1); idempotente,
  y un «Inventario» creado a mano ANTES del slug se adopta, no se duplica;
- el predicado único `solo_inventario()` con su matriz completa: admin /
  solo-inventario / inventario+otro rol / sin roles;
- renombrar la fila NO suelta los candados (se compara por slug, jamás por
  nombre — la trampa es_maceta);
- el redirect global a /stock con sus excepciones (logout, estáticos) y sin
  loop, y el menú de la vista plana = [Stock];
- los endpoints prohibidos, ENUMERADOS y probados por request directa con
  la sesión del rol: cada POST rechaza con 403 sin efecto secundario.
"""

import pytest
from fastapi.testclient import TestClient

from app import datos, datos_roles, seguridad
from app.main import app


def _rol_inventario_n():
    with datos._db() as con:
        return con.execute("SELECT n FROM roles WHERE slug=?",
                           (datos_roles.SLUG_INVENTARIO,)).fetchone()["n"]


def _empleada(usuario):
    """El dict de sesión como lo arma seguridad (sin pasar por HTTP)."""
    return {"id": usuario, "nombre": usuario.capitalize(),
            "email": None, "email_verificado": 0}


@pytest.fixture
def omar(db_limpia):
    """Omar: empleada activa con el rol Inventario y NADA más."""
    seguridad.crear_empleada("omar", "Omar", "clave-de-prueba")
    assert datos_roles.poner_persona(_rol_inventario_n(), "omar", "korto") is None
    return "omar"


@pytest.fixture
def cliente_omar(omar):
    """Un TestClient con la sesión de Omar (solo-inventario)."""
    c = TestClient(app)
    r = c.post("/login",
               data={"usuario": "omar", "contrasena": "clave-de-prueba"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


# ---------------------------------------------------------------------------
# La semilla y el slug
# ---------------------------------------------------------------------------

def test_semilla_crea_el_rol_sin_persona(db_limpia):
    with datos._db() as con:
        fila = con.execute("SELECT n, nombre, deber, activo FROM roles "
                           "WHERE slug=?",
                           (datos_roles.SLUG_INVENTARIO,)).fetchone()
        assert fila is not None
        assert fila["nombre"] == "Inventario"
        assert fila["deber"] is None and fila["activo"] == 1
        assert con.execute("SELECT 1 FROM rol_persona WHERE rol=?",
                           (fila["n"],)).fetchone() is None


def test_semilla_es_idempotente(db_limpia):
    datos_roles.iniciar_tablas()
    datos_roles.iniciar_tablas()
    with datos._db() as con:
        assert con.execute("SELECT count(*) c FROM roles WHERE slug=?",
                           (datos_roles.SLUG_INVENTARIO,)).fetchone()["c"] == 1


def test_un_inventario_creado_a_mano_se_adopta_no_se_duplica(db_limpia):
    """Base vieja: alguien creó el rol «Inventario» por pantalla antes de
    existir el slug. La migración le estampa el slug a ESA fila."""
    with datos._db() as con:
        con.execute("UPDATE roles SET slug=NULL WHERE slug=?",
                    (datos_roles.SLUG_INVENTARIO,))
    datos_roles.iniciar_tablas()
    with datos._db() as con:
        filas = con.execute(
            "SELECT nombre FROM roles WHERE slug=?",
            (datos_roles.SLUG_INVENTARIO,)).fetchall()
    assert len(filas) == 1 and filas[0]["nombre"] == "Inventario"


def test_duplicar_el_rol_no_copia_el_slug(db_limpia):
    error, nuevo = datos_roles.duplicar_rol(_rol_inventario_n(), "Conteo B")
    assert error is None
    with datos._db() as con:
        assert con.execute("SELECT slug FROM roles WHERE n=?",
                           (nuevo,)).fetchone()["slug"] is None


# ---------------------------------------------------------------------------
# El predicado único: la matriz completa
# ---------------------------------------------------------------------------

def test_matriz_solo_inventario(db_limpia, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "jefa")
    for usuario in ("jefa", "omar", "mixta", "nueva"):
        seguridad.crear_empleada(usuario, usuario.capitalize(), "clave-de-prueba")
    rol_inv = _rol_inventario_n()
    with datos._db() as con:
        otro = con.execute("SELECT n FROM roles WHERE slug IS NULL "
                           "AND activo=1 LIMIT 1").fetchone()["n"]
    # jefa: admin fijada en el servidor, AUNQUE tenga el rol.
    assert datos_roles.poner_persona(rol_inv, "jefa", "x") is None
    # omar: solo el rol Inventario.
    assert datos_roles.poner_persona(rol_inv, "omar", "x") is None
    # mixta: Inventario + otro rol.
    assert datos_roles.poner_persona(rol_inv, "mixta", "x") is None
    assert datos_roles.poner_persona(otro, "mixta", "x") is None
    # nueva: sin roles.
    assert datos_roles.solo_inventario(_empleada("jefa")) is False
    assert datos_roles.solo_inventario(_empleada("omar")) is True
    assert datos_roles.solo_inventario(_empleada("mixta")) is False
    assert datos_roles.solo_inventario(_empleada("nueva")) is False


def test_admin_dada_por_pantalla_tampoco_queda_presa(db_limpia):
    seguridad.crear_empleada("ruth", "Ruth", "clave-de-prueba")
    assert datos_roles.poner_persona(_rol_inventario_n(), "ruth", "x") is None
    assert datos_roles.solo_inventario(_empleada("ruth")) is True
    seguridad.fijar_admin("ruth", True, "la jefa")
    assert datos_roles.solo_inventario(_empleada("ruth")) is False


def test_renombrar_el_rol_no_suelta_los_candados(omar):
    """La fila se renombra desde la pantalla; el slug (y con él el
    predicado) no se mueve — se compara por slug, nunca por nombre."""
    assert datos_roles.solo_inventario(_empleada("omar")) is True
    assert datos_roles.renombrar_rol(_rol_inventario_n(), "Conteo del vivero") is None
    with datos._db() as con:
        fila = con.execute("SELECT nombre, slug FROM roles WHERE n=?",
                           (_rol_inventario_n(),)).fetchone()
    assert fila["nombre"] == "Conteo del vivero"
    assert fila["slug"] == datos_roles.SLUG_INVENTARIO
    assert datos_roles.solo_inventario(_empleada("omar")) is True


# ---------------------------------------------------------------------------
# La puerta global: menú=[Stock], redirect a /stock, candados POST
# ---------------------------------------------------------------------------

def test_redirect_global_a_stock_sin_loop(cliente_omar, con_inventario):
    for ruta in ("/", "/?tab=stock", "/?tab=ajustes", "/venta", "/compras",
                 "/control", "/calendario", "/productos/crear", "/revisar",
                 "/conversaciones", "/resumen", "/equipo"):
        r = cliente_omar.get(ruta, follow_redirects=False)
        assert (r.status_code, r.headers.get("location")) == (303, "/stock"), ruta
    # Sin loop: /stock se pinta (200), no redirige a sí misma.
    assert cliente_omar.get("/stock", follow_redirects=False).status_code == 200


def test_logout_y_estaticos_quedan_fuera_del_redirect(cliente_omar):
    assert cliente_omar.get("/static/styles.css",
                            follow_redirects=False).status_code == 200
    r = cliente_omar.post("/logout", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")


def test_menu_del_rol_es_solo_stock(cliente_omar, con_inventario):
    pantalla = cliente_omar.get("/stock").text
    assert "Salir" in pantalla
    for enlace in ('href="/venta"', 'href="/compras"', 'href="/control"',
                   'href="/calendario"', 'href="/?tab=ajustes"'):
        assert enlace not in pantalla, enlace
    # Ni siquiera el pre-render ofrece esas rutas (bloque vaciado).
    assert "speculationrules" not in pantalla


# Los endpoints PROHIBIDOS para el rol, enumerados (review del Arquitecto):
# todo POST fuera de /stock/* rechaza con 403 en el servidor, botones
# aparte. La lista nombra los que tocan productos (viejos y bulk
# incluidos) y una muestra del resto de la app; la regla del middleware
# cubre a TODOS los que no empiecen por /stock.
PROHIBIDOS = [
    ("/ajustar", {"json": {"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}}),
    ("/productos/nuevo", {"json": {"nombre": "X", "sku": "PL-X",
                                   "categoria": "Exterior",
                                   "precioCentavos": 100}}),
    ("/productos/crear", {"data": {"tipo": "maceta", "nombre": "M"}}),
    ("/productos/PL-ROMERO/publicacion", {"json": {"publicado": False}}),
    ("/fotos/PL-ROMERO", {"files": {"archivo": ("x.jpg", b"123", "image/jpeg")}}),
    ("/fichas/PL-ROMERO", {"json": {"descripcion": "x"}}),
    ("/conteos/importar", {"files": {"archivo": ("c.xlsx", b"123")}}),
    ("/conteos/1/confirmar", {}),
    ("/conteos/1/descartar", {}),
    ("/conteos/pdf", {}),
    ("/revisiones", {}),
    ("/alertas/atender", {"data": {"sku": "PL-ROMERO"}}),
    ("/umbral", {"data": {"umbral": "9"}}),
    ("/venta/carrito/agregar", {"data": {"producto_id": 1, "cantidad": 1}}),
    ("/venta/carrito/precio", {"data": {"producto_id": 1, "precio": "9"}}),
    ("/venta/vender", {"data": {}}),
    ("/venta/cotizar", {"data": {}}),
    ("/compras/borrador", {"data": {}}),
    ("/compras/nueva", {"data": {}}),
    ("/compras/recibir", {"data": {"ref": "C-1"}}),
    ("/compras/proveedores/producto", {"data": {}}),
    ("/ajustes/envio", {"data": {}}),
    ("/ajustes/invitar", {"data": {"email": "a@b.co"}}),
    ("/ajustes/admin", {"data": {"usuario": "omar", "dar": "1"}}),
    ("/ajustes/roles/renombrar", {"data": {"rol": "1", "nombre": "Z"}}),
    ("/control/estado", {"data": {}}),
    ("/control/responsable", {"data": {}}),
    ("/calendario/actividad", {"data": {}}),
    ("/fichas/PL-ROMERO", {"json": {}}),
]


def test_endpoints_prohibidos_rechazan_403_sin_efecto(
        cliente_omar, con_inventario, ajustes_registrados):
    for ruta, kwargs in PROHIBIDOS:
        r = cliente_omar.post(ruta, **kwargs)
        assert r.status_code == 403, ruta
        assert "solo de inventario" in r.text, ruta
    # Y de verdad no pasó nada: ni un ajuste viajó al order-api.
    assert ajustes_registrados == []


def test_el_candado_sobrevive_el_renombre_del_rol(cliente_omar, con_inventario):
    assert datos_roles.renombrar_rol(_rol_inventario_n(), "Conteo") is None
    r = cliente_omar.get("/venta", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/stock")
    assert cliente_omar.post("/ajustar", json={
        "sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}).status_code == 403


def test_con_otro_rol_ademas_no_hay_puerta(db_limpia, con_inventario):
    seguridad.crear_empleada("mixta", "Mixta", "clave-de-prueba")
    with datos._db() as con:
        otro = con.execute("SELECT n FROM roles WHERE slug IS NULL "
                           "AND activo=1 LIMIT 1").fetchone()["n"]
    datos_roles.poner_persona(_rol_inventario_n(), "mixta", "x")
    datos_roles.poner_persona(otro, "mixta", "x")
    c = TestClient(app)
    r = c.post("/login", data={"usuario": "mixta",
                               "contrasena": "clave-de-prueba"},
               follow_redirects=False)
    assert r.status_code == 303
    # Navega como cualquiera: /venta no redirige a /stock.
    assert c.get("/venta", follow_redirects=False).status_code == 200
    # Y /stock la manda a su pestaña de siempre (la vista plana es del rol).
    r = c.get("/stock", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?tab=stock")


def test_la_bitacora_es_de_admins_tambien_dentro_de_stock(cliente_omar):
    # /stock/cambios vive bajo el prefijo permitido, pero se defiende
    # sola: el rol no es admin y recibe su 403.
    assert cliente_omar.get("/stock/cambios").status_code == 403
