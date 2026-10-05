"""Admin desde la pantalla de Ajustes (5/10/2026).

La regla vive en UN solo lugar, `seguridad.es_admin()`: correo fijado en
AJUSTES_ADMINS (la semilla del .env, que no se puede quitar por pantalla)
O empleada activa con `es_admin=1`, dado con el botón de Ajustes. Acá se
prueba la migración al vuelo, que los candados existentes (/revisar,
/conversaciones) aceptan al admin de pantalla y lo rechazan al quitarle,
que un no-admin ni ve ni puede usar los botones, que nadie se auto-quita,
que el fijado en el servidor no se quita por pantalla, y que el rastro
quién/cuándo se escribe en cada cambio.
"""

import pytest
from fastapi.testclient import TestClient

from app import control, datos, linear_leads, seguridad
from app.main import app


@pytest.fixture(autouse=True)
def tablero_de_muestra(monkeypatch, db_limpia):
    """Sin Linear ni Twenty (como en las pruebas de /conversaciones): el
    tablero de muestra y las tablas de Control listas."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    linear_leads.reiniciar_muestra()
    control.iniciar_tablas()


def _entrar(usuario, nombre):
    """Una empleada nueva, con su TestClient ya autenticado."""
    seguridad.crear_empleada(usuario, nombre, "clave-de-prueba")
    c = TestClient(app)
    r = c.post("/login",
               data={"usuario": usuario, "contrasena": "clave-de-prueba"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _columnas():
    with datos._db() as con:
        return {f["name"] for f in con.execute("PRAGMA table_info(empleadas)")}


def _fila(usuario):
    return next(e for e in seguridad.listar() if e["usuario"] == usuario)


NUEVAS = {"es_admin", "admin_cambiado_por", "admin_cambiado_en"}


def test_migracion_al_vuelo_agrega_columnas_y_es_idempotente():
    assert NUEVAS <= _columnas()
    # Correr la migración de nuevo no truena ni duplica nada.
    datos.iniciar_db()
    assert NUEVAS <= _columnas()
    # Una empleada nueva nace sin admin y sin rastro.
    seguridad.crear_empleada("ana", "Ana", "x")
    fila = _fila("ana")
    assert fila["es_admin"] == 0
    assert fila["admin_cambiado_por"] is None
    assert fila["admin_cambiado_en"] is None


def test_admin_de_pantalla_entra_a_las_pantallas_y_al_quitarle_ya_no(
        cliente, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    # /revisar con su informe suplantado: acá se prueba el candado, no M1.
    from app import revisar as revisar_apoyo
    monkeypatch.setattr(revisar_apoyo, "informe", lambda: {
        "generado_en": "2026-10-05T08:00:00", "contadores": {},
        "ventas": [], "fuera_de_alcance": None, "huecos": []})
    assert cliente.get("/revisar").status_code == 403
    assert cliente.get("/conversaciones").status_code == 403
    # Hecha admin por pantalla: los candados existentes la aceptan.
    assert seguridad.fijar_admin("genesis", True, "Abraham") is None
    assert cliente.get("/revisar").status_code == 200
    assert cliente.get("/conversaciones").status_code == 200
    # Y al quitarle, deja de verlas en la misma sesión.
    assert seguridad.fijar_admin("genesis", False, "Abraham") is None
    assert cliente.get("/revisar").status_code == 403
    assert cliente.get("/conversaciones").status_code == 403


def test_no_admin_ni_ve_los_botones_ni_puede_usar_el_post(
        cliente, con_inventario, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    pagina = cliente.get("/?tab=ajustes").text
    assert "Hacer admin" not in pagina
    assert "/ajustes/admin" not in pagina
    r = cliente.post("/ajustes/admin", data={"usuario": "genesis", "dar": "1"})
    assert r.status_code == 403
    assert _fila("genesis")["es_admin"] == 0


def test_nadie_se_quita_el_admin_a_si_mismo(cliente, con_inventario, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    seguridad.fijar_admin("genesis", True, "Abraham")
    # Su propio botón sale deshabilitado, con la nota.
    pagina = cliente.get("/?tab=ajustes").text
    assert "nadie se quita el admin a sí mismo" in pagina
    # Y el POST lo rechaza igual, aunque alguien arme la petición a mano.
    r = cliente.post("/ajustes/admin", data={"usuario": "genesis", "dar": "0"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "aviso=admin-propio" in r.headers["location"]
    assert seguridad.es_admin({"id": "genesis"})


def test_el_fijado_en_el_servidor_no_se_quita_por_pantalla(
        cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    marta = _entrar("marta", "Marta")
    seguridad.fijar_admin("marta", True, "Génesis")
    # Marta (admin de pantalla) intenta quitarle el admin a la fijada.
    r = marta.post("/ajustes/admin", data={"usuario": "genesis", "dar": "0"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "aviso=admin-fijado" in r.headers["location"]
    assert seguridad.es_admin({"id": "genesis"})
    # La fila de la fijada sale con el botón apagado y su nota.
    pagina = marta.get("/?tab=ajustes").text
    assert "fijado en el servidor" in pagina


def test_el_rastro_quien_cuando_y_el_chip_de_origen(
        cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    _entrar("marta", "Marta")
    r = cliente.post("/ajustes/admin", data={"usuario": "marta", "dar": "1"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "aviso=admin-dado" in r.headers["location"]
    fila = _fila("marta")
    assert fila["es_admin"] == 1
    assert fila["admin_cambiado_por"] == "Génesis"
    assert fila["admin_cambiado_en"]
    # El candado real la acepta ya, sin tocar el .env.
    assert seguridad.es_admin({"id": "marta"})
    pagina = cliente.get("/?tab=ajustes").text
    assert "Admin · servidor" in pagina        # genesis, de AJUSTES_ADMINS
    assert "Admin · pantalla" in pagina        # marta, del botón
    assert "admin desde" in pagina and "por Génesis" in pagina
    # Quitárselo también deja rastro (quién y cuándo, en CADA cambio).
    r2 = cliente.post("/ajustes/admin", data={"usuario": "marta", "dar": "0"},
                      follow_redirects=False)
    assert "aviso=admin-quitado" in r2.headers["location"]
    fila = _fila("marta")
    assert fila["es_admin"] == 0
    assert fila["admin_cambiado_por"] == "Génesis"
    assert fila["admin_cambiado_en"] >= "2026"
    assert not seguridad.es_admin({"id": "marta"})


def test_una_empleada_que_ya_no_esta_no_se_vuelve_admin(
        cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    r = cliente.post("/ajustes/admin", data={"usuario": "fantasma", "dar": "1"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "aviso=admin-no-existe" in r.headers["location"]
