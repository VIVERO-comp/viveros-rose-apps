"""La pantalla «Ventas a revisar» (Orquesta · M1): solo admin, palabras de
pantalla, los botones de M2 desactivados y la nota que vuelve a su tarjeta.

El informe lo arma `app/reconciliacion.py` (la otra mitad de M1): acá se
suplanta ENTERO con monkeypatch — estas pruebas miran la pantalla, no la
reconciliación. Si el módulo real todavía no existe, se inyecta un stub
con el mismo nombre SOLO en la prueba (el código de producción importa el
real, perezoso, en `app/revisar.py`).
"""

import sys
import types

import pytest

from app import datos
from app import revisar as revisar_apoyo


def _informe_base():
    """Una foto chica pero con de todo: una sana (D), la clase C con
    saldo, y una diferencia (F) marcada como prueba. Los números de los
    contadores son primos distintos para poder afirmarlos sin ambigüedad."""
    return {
        "contadores": {"A": 7, "B": 11, "C": 1, "D": 13, "E": 17,
                       "F": 2, "G": 3, "H": 1, "rojo": 6},
        "ventas": [
            {"orden_id": "S00077", "nombre": "S00077",
             "cliente": "Carla Núñez", "telefono": "6000-0001",
             "total": 150.0, "pagado": 150.0, "debe": 0.0, "clase": "D",
             "motivo": "Pagada completa y al día",
             "marca_prueba": False, "historica": False,
             "entregado_odoo": False, "entregado_calendario": False,
             "fuentes_odoo": ["Orden confirmada el 12/09",
                              "Pago completo registrado"],
             "fuentes_otras": ["Chat: pidió entregar en Costa del Este"]},
            {"orden_id": "S00078", "nombre": "S00078",
             "cliente": "Luis Prado", "telefono": "6000-0002",
             "total": 320.5, "pagado": 200.0, "debe": 120.5, "clase": "C",
             "motivo": "La entrega se marcó Hecha y quedó saldo",
             "marca_prueba": False, "historica": False,
             "entregado_odoo": True, "entregado_calendario": False,
             "fuentes_odoo": ["Orden confirmada el 15/09"],
             "fuentes_otras": ["Calendario: actividad sin marcar Hecha"]},
            {"orden_id": "S00099", "nombre": "S00099",
             "cliente": "Venta de ensayo", "telefono": "",
             "total": 10.0, "pagado": 10.0, "debe": 0.0, "clase": "F",
             "motivo": "Un pago que el sistema no vio entrar",
             "marca_prueba": True, "historica": False,
             "entregado_odoo": False, "entregado_calendario": True,
             "fuentes_odoo": [],
             "fuentes_otras": ["Yappy: aviso de pago sin orden"]},
            {"orden_id": "S00109", "nombre": "S00109",
             "cliente": "Marta Ríos", "telefono": "6000-0003",
             "total": 85.0, "pagado": 85.0, "debe": 0.0, "clase": "D",
             "motivo": "Pagada completa (venta vieja registrada el 1/10); "
                       "la entrega se regulariza en M2",
             "marca_prueba": False, "historica": True,
             "entregado_odoo": False, "entregado_calendario": False,
             "fuentes_odoo": ["Orden registrada el 01/10"],
             "fuentes_otras": []},
        ],
        "fuera_de_alcance": {"n": 29, "total": 3109.85, "detalle": [],
                             "nombre": "Ventas Super Extra"},
        "huecos": [],
    }


@pytest.fixture
def informe_falso(monkeypatch):
    """Suplanta `reconciliacion.informe_datos()` entero. Devuelve el dict
    para que cada prueba lo pueda retocar (huecos, etc.)."""
    informe = _informe_base()
    try:
        from app import reconciliacion
    except ImportError:
        # La otra mitad de M1 todavía no está en el árbol: un stub con el
        # mismo nombre, solo visible dentro de la prueba.
        import app as paquete
        reconciliacion = types.ModuleType("app.reconciliacion")
        monkeypatch.setitem(sys.modules, "app.reconciliacion", reconciliacion)
        monkeypatch.setattr(paquete, "reconciliacion", reconciliacion,
                            raising=False)
    monkeypatch.setattr(reconciliacion, "informe_datos", lambda: informe,
                        raising=False)
    return informe


@pytest.fixture
def admin(cliente, monkeypatch):
    """El mismo cliente de la casa (Génesis), vuelta admin."""
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    return cliente


def _notas_guardadas():
    revisar_apoyo.iniciar_tablas()
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT orden_id, nota, quien, creada_en FROM revision_nota")]


# ---------------------------------------------------------------------------
# Quién puede entrar
# ---------------------------------------------------------------------------

def test_sin_sesion_redirige_al_login(db_limpia):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/revisar", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_sin_rol_de_supervision_recibe_403(cliente, informe_falso,
                                           monkeypatch):
    """V2 (BLOQUE 29): /revisar dejó de ser «solo admin» y pasó a los
    ROLES Director y Finanzas — el 403 lo dice con ese nombre."""
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    r = cliente.get("/revisar")
    assert r.status_code == 403
    assert "Director y Finanzas" in r.text
    # Y el POST de la nota tampoco pasa — ni escribe nada.
    r2 = cliente.post("/revisar/nota",
                      data={"orden_id": "S00077", "nota": "hola"},
                      follow_redirects=False)
    assert r2.status_code == 403
    assert _notas_guardadas() == []


# ---------------------------------------------------------------------------
# La lista: contadores, chips, marcas
# ---------------------------------------------------------------------------

def test_los_contadores_pintan_los_numeros_del_informe(admin, informe_falso):
    texto = admin.get("/revisar").text
    assert "🔴 6" in texto      # rojo = F+G+H
    assert "🟡 2" in texto      # F: pagos fuera del sistema
    assert "🟡 3" in texto      # G: entregas por confirmar
    assert "🟢 13" in texto     # D: pagadas
    assert "🟢 17" in texto     # E: entregadas
    assert "⚪ 11" in texto     # B: vencidas
    assert "Pagos fuera del sistema" in texto
    assert "Entregas por confirmar" in texto


def test_las_tarjetas_hablan_en_palabras_de_pantalla(admin, informe_falso):
    texto = admin.get("/revisar").text
    assert "Carla Núñez" in texto and "S00077" in texto
    assert "Pagado" in texto           # el chip de la clase D
    assert "Revisar" in texto          # el chip de la clase F
    # La clase C entregada con saldo lleva su marca roja con el monto.
    assert "Entregado, debe $120.50" in texto
    # Y cada tarjeta tiene su ancla para no perder el lugar.
    assert 'id="orden-S00078"' in texto


def test_la_marca_de_prueba_aparece(admin, informe_falso):
    texto = admin.get("/revisar").text
    assert "PRUEBA / REVISIÓN ADMIN" in texto


def test_el_renglon_de_fuera_de_alcance(admin, informe_falso):
    texto = admin.get("/revisar").text
    assert "29 facturas del diario «Ventas Super Extra»" in texto
    assert "$3,109.85" in texto
    assert "fuera de alcance" in texto


def test_con_huecos_el_aviso_aparece(admin, informe_falso):
    # Sin huecos, nada de huecos en la pantalla.
    assert "on huecos" not in admin.get("/revisar").text
    informe_falso["huecos"] = ["Linear no contestó", "Twenty no contestó"]
    texto = admin.get("/revisar").text
    assert "Con huecos" in texto
    assert "Linear no contestó" in texto and "Twenty no contestó" in texto


def test_ninguna_palabra_prohibida(admin, informe_falso):
    """La jerga de los sistemas de origen no llega al HTML: la pantalla
    habla en palabras del negocio."""
    paginas = [admin.get("/revisar").text,
               admin.get("/revisar?abrir=S00078").text]
    for texto in paginas:
        bajo = texto.lower()
        for palabra in ("picking", "payment_state", "backorder", "sale order"):
            assert palabra not in bajo, f"se escapó «{palabra}»"


def test_el_chip_historica_aparece_en_lista_y_detalle(admin, informe_falso):
    """Una venta histórica lleva su chip gris JUNTO al de su clase, en la
    lista y en el detalle — y el tono sigue siendo el de la clase."""
    lista = admin.get("/revisar").text
    # Solo la S00109 lo lleva: una vez en toda la lista.
    assert lista.count("HISTÓRICA · 1/10") == 1
    tarjeta = lista.split('id="orden-S00109"')[1].split("prov-abrir")[0]
    assert "HISTÓRICA · 1/10" in tarjeta
    # El tono es el de su clase: una D histórica sigue verde, con su
    # chip «Pagado» — el chip histórico es gris, nunca una advertencia.
    assert "rv-verde" in tarjeta and "Pagado" in tarjeta
    assert "rv-rojo" not in tarjeta and "rv-dorado" not in tarjeta
    # Y el motivo se pinta tal cual llega, sin texto inventado de hoy.
    assert "venta vieja registrada el 1/10" in tarjeta
    assert "se regulariza en M2" in tarjeta

    detalle = admin.get("/revisar?abrir=S00109").text
    panel = detalle.split("panel-der")[1]
    assert "HISTÓRICA · 1/10" in panel
    assert "rv-verde" in panel


def test_el_chip_historica_no_aparece_para_las_demas(admin, informe_falso):
    lista = admin.get("/revisar").text
    for orden in ("S00077", "S00078", "S00099"):
        tarjeta = lista.split(f'id="orden-{orden}"')[1].split("prov-abrir")[0]
        assert "HISTÓRICA" not in tarjeta, f"el chip se coló en {orden}"
    detalle = admin.get("/revisar?abrir=S00077").text
    assert "HISTÓRICA" not in detalle.split("panel-der")[1]


def test_preparar_sin_el_campo_historica_no_rompe():
    """Un informe viejo que todavía no trae `historica`: default False."""
    from app import revisar

    (venta,) = revisar.preparar([{"clase": "D", "debe": 0.0}])
    assert venta["historica"] is False


# ---------------------------------------------------------------------------
# El detalle: las dos columnas y los botones de M2
# ---------------------------------------------------------------------------

def test_el_detalle_trae_las_dos_columnas_y_la_entrega_por_fuente(admin, informe_falso):
    texto = admin.get("/revisar?abrir=S00078").text
    assert "Lo que dice Odoo" in texto
    assert "Lo que dicen las otras fuentes" in texto
    assert "Orden confirmada el 15/09" in texto
    assert "Calendario: actividad sin marcar Hecha" in texto
    # La entrega SIEMPRE en dos renglones separados, uno por fuente —
    # y en este caso dicen cosas distintas, que es el punto de M1.
    assert "Entregado — según Odoo" in texto
    assert "Sin entregar — según el calendario" in texto


def test_los_botones_de_m2_salen_desactivados(admin, informe_falso):
    texto = admin.get("/revisar?abrir=S00078").text
    assert "Registrar pago" in texto and "Confirmar entrega" in texto
    assert "Fase M2" in texto
    # Los dos desactivados de verdad, no solo pintados.
    pago = texto.split("Registrar pago")[0].rsplit("<button", 1)[1]
    entrega = texto.split("Confirmar entrega")[0].rsplit("<button", 1)[1]
    assert "disabled" in pago and "disabled" in entrega
    # Y el único botón negro es «Dejar nota».
    assert texto.count("btn oro") == 1
    assert "Dejar nota" in texto.split("btn oro")[1][:200]


def test_abrir_una_venta_que_ya_no_esta(admin, informe_falso):
    r = admin.get("/revisar?abrir=S99999")
    assert r.status_code == 200
    assert "ya no está en la lista" in r.text


# ---------------------------------------------------------------------------
# Dejar nota: guarda, vuelve a la tarjeta y jamás significa «pagado»
# ---------------------------------------------------------------------------

def test_dejar_nota_guarda_y_vuelve_a_la_tarjeta(admin, informe_falso):
    r = admin.post("/revisar/nota",
                   data={"orden_id": "S00078", "nota": "Llamé al cliente: paga el viernes"},
                   follow_redirects=False)
    assert r.status_code == 303
    destino = r.headers["location"]
    assert destino.startswith("/revisar?abrir=S00078")
    assert destino.endswith("#orden-S00078")   # de vuelta a SU tarjeta
    filas = _notas_guardadas()
    assert len(filas) == 1
    assert filas[0]["orden_id"] == "S00078"
    assert filas[0]["nota"] == "Llamé al cliente: paga el viernes"
    assert filas[0]["quien"] == "Génesis"
    assert filas[0]["creada_en"]
    # La tabla guarda texto y nada más: ninguna columna de pago o estado.
    assert set(filas[0]) == {"orden_id", "nota", "quien", "creada_en"}
    # Y al volver, la nota se ve en el detalle, firmada.
    texto = admin.get("/revisar?abrir=S00078").text
    assert "Llamé al cliente: paga el viernes" in texto
    assert "Génesis" in texto


def test_nota_vacia_no_guarda_y_lo_dice(admin, informe_falso):
    r = admin.post("/revisar/nota",
                   data={"orden_id": "S00078", "nota": "   "},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert r.headers["location"].endswith("#orden-S00078")
    assert _notas_guardadas() == []


def test_la_marca_roja_exige_entrega_validada():
    """Una C con saldo pero SIN entregar no puede decir «Entregado»."""
    from app import revisar

    sin_entregar, entregada = revisar.preparar([
        {"clase": "C", "debe": 500.0, "entregado_odoo": False},
        {"clase": "C", "debe": 500.0, "entregado_odoo": True},
    ])
    assert sin_entregar["marca_debe"] is None
    assert entregada["marca_debe"] == "Entregado, debe $500.00"
