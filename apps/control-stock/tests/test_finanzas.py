"""Finanzas (/finanzas) — esqueleto navegable de los BLOQUES 20 y 22.

Lo que se prueba, por regla:

- Nace cerrada (BLOQUE 22.7): sin sesión al login; sin admin ni deber de
  la cola, 403 — por request directa.
- Los números salen de los MISMOS motores (informe + cola + tabla de
  confirmaciones): con datos cuadran, y si no cierran el descuadre SE
  DICE en pantalla.
- Con huecos (Odoo caído) los números dependientes salen «sin dato» —
  jamás un $0 fingido.
- El botón «Confirmar» va DISABLED con «Todavía no» A SECAS (item 5,
  7/10/2026: confirmar es del asiento System manager, no un permiso
  pendiente); reportes y «Ver», apagados.
- Cada fila de la cola dice DE CUÁNDO es, CÓMO llegó la plata y QUIÉN
  la marcó (item 4), y lo que no se sabe lo DICE.
- Ni una palabra técnica en la cara del lector (item 7): ni rutas, ni
  nombres de archivo, ni nombres de sistemas que él no administra.
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
    assert "se van por $50.00" in texto


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
    assert "Plata cobrada de trabajos sin entregar" in texto
    assert "solo cuenta lo que la app registró desde el 5/10/2026" in texto


# ---------------------------------------------------------------------------
# Botones apagados y cero escritura
# ---------------------------------------------------------------------------

def test_confirmar_va_apagado_y_sin_el_si_de_jay(admin, con_informe_qa):
    """Item 5 (7/10/2026): el botón decía «falta el sí de Jay» y ese
    permiso ya no es el motivo — confirmar es del asiento System manager.
    Queda apagado, y nada más."""
    texto = admin.get("/finanzas").text
    assert "Confirmar — Todavía no" in texto
    assert "Jay" not in texto
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


# ---------------------------------------------------------------------------
# Item 4 · cada fila de la cola dice cuándo, cómo y quién
# ---------------------------------------------------------------------------

VENTA_CON_FECHA = [
    {"orden_id": 9101, "nombre": "S09101", "cliente": "Cliente QA Fecha",
     "fecha": "2026-10-02 15:04:33", "total": 300.0, "pagado": 300.0,
     "debe": 0.0, "clase": "D", "motivo": ""},
]


@pytest.fixture
def con_venta_fechada(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [dict(v) for v in VENTA_CON_FECHA], "huecos": []})


def test_la_fila_dice_la_fecha_de_la_venta(admin, con_venta_fechada):
    """Era el reclamo: la cola no decía de cuándo era cada fila."""
    texto = admin.get("/finanzas").text
    assert "Venta del 02/10/2026" in texto


def test_sin_confirmar_todavia_el_metodo_y_el_quien_se_DICEN(
        admin, con_venta_fechada):
    """Nada se inventa: una fila que nadie tocó no tiene método (se elige
    al confirmar) ni quién la marcó, y las dos cosas se escriben."""
    texto = admin.get("/finanzas").text
    assert pagos_confirmar.SIN_METODO in texto
    assert pagos_confirmar.SIN_MARCA in texto


def test_con_una_confirmacion_previa_sale_el_metodo_y_el_nombre(
        admin, con_venta_fechada, monkeypatch):
    """Lo que SÍ existe sale: el libro de confirmaciones guarda qué se vio
    y quién lo vio, y la fila lo cuenta."""
    monkeypatch.setattr(pagos_confirmar, "confirmados", lambda: {
        9101: {"orden_id": 9101, "evidencia": "yappy", "por": "Rubén",
               "en": "2026-10-05 09:12:00", "monto": 100.0}})
    # Con un abono previo de 100 y 300 pagados, la fila RE-ENTRA por los 200
    # nuevos — que es justo el caso en el que el método y el quién existen.
    monkeypatch.setattr(pagos_confirmar, "sumas_confirmadas",
                        lambda: {9101: 100.0})
    texto = admin.get("/finanzas").text
    assert "Voucher de Yappy" in texto
    assert "marcó: Rubén, el 05/10/2026" in texto


def test_una_fecha_que_no_se_entiende_no_se_inventa():
    assert pagos_confirmar.fecha_de_venta("") == pagos_confirmar.SIN_FECHA
    assert pagos_confirmar.fecha_de_venta(None) == pagos_confirmar.SIN_FECHA
    assert pagos_confirmar.fecha_de_venta("ayer") == pagos_confirmar.SIN_FECHA
    assert pagos_confirmar.fecha_de_venta("2026-10-02") == "02/10/2026"


# ---------------------------------------------------------------------------
# Item 7 · ni una palabra técnica en la cara del lector
# ---------------------------------------------------------------------------

# Lo que esta pantalla NO puede decir: rutas internas, nombres de archivo y
# nombres de sistemas que quien lee Finanzas no administra.
JERGA_PROHIBIDA = ("/revisar", "/pagos-por-confirmar", "cifras.py",
                   "hechos locales", "FUERA de Odoo", "informe de",
                   "confirmaciones humanas", "droplet", ".env", "BLOQUE")


@pytest.mark.parametrize("palabra", JERGA_PROHIBIDA)
def test_la_pantalla_no_habla_en_tecnico(admin, con_informe_qa, palabra):
    cuerpo = admin.get("/finanzas").text
    # Solo lo que se VE: los comentarios de Jinja no llegan al HTML, pero
    # las clases y los href sí, así que se mira el texto entre etiquetas.
    visible = " ".join(re.sub(r"<[^>]+>", " ", cuerpo).split())
    assert palabra not in visible, f"«{palabra}» se ve en la pantalla"
