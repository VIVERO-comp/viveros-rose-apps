"""Finanzas (/finanzas) — esqueleto navegable de los BLOQUES 20 y 22.

Lo que se prueba, por regla:

- Nace cerrada (BLOQUE 22.7): sin sesión al login; sin admin ni deber de
  la cola, 403 — por request directa.
- Los números salen de los MISMOS motores (informe + cola + tabla de
  confirmaciones): con datos cuadran, y si no cierran el descuadre SE
  DICE en pantalla.
- Con huecos (Odoo caído) los números dependientes salen «sin dato» —
  jamás un $0 fingido.
- El botón «Confirmar» va DISABLED con «Todavía no — falta el sí de
  Jay» (BLOQUE 22.1); reportes y «Ver», apagados.
- CERO rutas POST bajo /finanzas y ni un <form>.

Datos QA solamente.
"""

import re

import pytest

from app import finanzas, pagos_confirmar


VENTAS_QA = [
    {"orden_id": 9001, "nombre": "S09001", "cliente": "Cliente QA Uno",
     "total": 100.0, "pagado": 40.0, "debe": 60.0, "clase": "B",
     "motivo": ""},
    {"orden_id": 9002, "nombre": "S09002", "cliente": "Cliente QA Dos",
     "total": 50.0, "pagado": 0.0, "debe": 50.0, "clase": "CANCELADA",
     "motivo": "cancelada"},  # fuera de todo, como en la cola
]


@pytest.fixture
def admin(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    return cliente


@pytest.fixture
def con_informe_qa(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [dict(v) for v in VENTAS_QA], "huecos": []})


@pytest.fixture
def con_odoo_caido(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [], "huecos": ["Odoo no contestó (QA)"]})


# ---------------------------------------------------------------------------
# La puerta: nace cerrada, server-side
# ---------------------------------------------------------------------------

def test_sin_sesion_redirige_al_login(db_limpia):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/finanzas", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_sin_admin_ni_deber_recibe_403(cliente, monkeypatch):
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    r = cliente.get("/finanzas")
    assert r.status_code == 403


def test_el_admin_entra(admin, con_informe_qa):
    r = admin.get("/finanzas")
    assert r.status_code == 200
    assert "Finanzas" in r.text


# ---------------------------------------------------------------------------
# Los números: mismos motores, y cuadran (o el descuadre se dice)
# ---------------------------------------------------------------------------

def test_los_cuatro_numeros_salen_de_los_motores(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    assert "$100.00" in texto      # vendido (la cancelada queda fuera)
    assert "$40.00" in texto       # por confirmar (monto_nuevo de la cola)
    assert "$60.00" in texto       # por cobrar
    assert "S09001" in texto       # la fila de la cola, la MISMA cola
    assert "S09002" not in texto   # la cancelada no existe para nadie
    # Con 100 = 0 + 40 + 60 no hay descuadre que avisar.
    assert "no cierran entre sí" not in texto


def test_el_descuadre_se_dice_no_se_esconde(admin, con_informe_qa,
                                            monkeypatch):
    # Una confirmación vieja de una orden que el informe ya no trae:
    # 100 ≠ 50 + 40 + 60 → el renglón honesto aparece.
    monkeypatch.setattr(pagos_confirmar, "sumas_confirmadas",
                        lambda: {8888: 50.0})
    texto = admin.get("/finanzas").text
    assert "no cierran entre sí por $50.00" in texto


def test_con_odoo_caido_sin_dato_jamas_cero(admin, con_odoo_caido):
    texto = admin.get("/finanzas").text
    assert "Odoo no contestó (QA)" in texto
    # Vendido, por confirmar y por cobrar: sin dato, nunca $0 fingido.
    assert texto.count(">sin dato<") >= 3
    datos = finanzas.resumen()
    assert datos["con_datos"] is False
    assert [t["monto"] for t in datos["tarjetas"]][0] is None


def test_las_cifras_de_cifras_py_viajan_con_su_rotulo(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    assert "Plata confirmada sin entregar" in texto
    assert "provisional — hechos locales" in texto


# ---------------------------------------------------------------------------
# Botones apagados y cero escritura
# ---------------------------------------------------------------------------

def test_confirmar_va_apagado_con_el_texto_de_jay(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    assert "Todavía no — falta el sí de Jay" in texto
    for boton in re.findall(r"<button[^>]*>[^<]*Todavía no[^<]*</button>",
                            texto):
        assert "disabled" in boton, boton


def test_los_reportes_van_apagados(admin, con_informe_qa):
    texto = admin.get("/finanzas").text
    for nombre in finanzas.REPORTES:
        assert f"{nombre} — Todavía no" in texto
    assert "Descargar reporte — Todavía no" in texto


def test_ni_un_form_en_la_pantalla(admin, con_informe_qa):
    assert "<form" not in admin.get("/finanzas").text


def test_cero_rutas_post_bajo_finanzas():
    from app.main import app
    for ruta in app.routes:
        if str(getattr(ruta, "path", "")).startswith("/finanzas"):
            assert "POST" not in (getattr(ruta, "methods", None) or set())
