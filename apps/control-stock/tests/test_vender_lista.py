"""La pantalla Vender en UNA sola lista (dueño, 30/09/2026): "ponlo en
orden de número, y no, si es de servicio o planta no importa, pon todo en
una fila". Antes había dos listas separadas — "Ventas y cotizaciones
locales" (plantas) y "Cotizaciones de servicios" — y esta pantalla las
unifica, ordenadas por número de orden (S000xx) de mayor a menor.

Un Odoo mínimo propio (no el de test_ventas.py ni el de
test_cotizaciones.py): solo necesita responder sale.order.search_read (lo
que usa cotizaciones.estados_en_odoo para saber facturada/cancelada) y
sale.order.action_cancel (lo que usa ventas._cancelar_en_odoo, el único
camino que cancela algo en Odoo)."""

from datetime import datetime

import pytest

from app import cotizaciones, ventas
from app.datos import ZONA_PANAMA, _db


class OdooMinimo:
    """Solo lo que estas pruebas necesitan de Odoo: leer estado/facturas
    de una orden y cancelarla. `self.ordenes` es {orden_id: {"state":
    "sale"|"cancel", "invoice_ids": [...]}}."""

    def __init__(self):
        self.ordenes = {}

    def ejecutar(self, modelo, metodo, args, kw=None):
        kw = kw or {}
        if modelo == "sale.order" and metodo == "search_read":
            ids = args[0][0][2]
            return [{"id": i, **self.ordenes[i]} for i in ids if i in self.ordenes]
        if modelo == "sale.order" and metodo == "action_cancel":
            for orden_id in args[0]:
                self.ordenes[orden_id]["state"] = "cancel"
            return True
        raise NotImplementedError(f"{modelo}.{metodo} no está simulado aquí")


@pytest.fixture
def odoo(monkeypatch):
    falso = OdooMinimo()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


def _insertar_venta(orden_id, orden, cliente="Ana", total=100.0,
                    estado="cotizacion", celular=None):
    """Una fila directa en ventas_locales, sin pasar por Odoo real — el
    foco de estas pruebas es la lista, no cómo se crea cada registro."""
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " celular, orden_id, orden, total, estado)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", cliente,
             celular, orden_id, orden, total, estado))
        return cursor.lastrowid


def _insertar_servicio(orden_id, orden, cliente="Beto", total=200.0,
                       tipo="renta"):
    """Una fila directa en cotizaciones_servicio."""
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", tipo,
             cliente, None, orden_id, orden, total))
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# main._numero_de_orden: la clave de orden, en Python
# ---------------------------------------------------------------------------

def test_numero_de_orden_extrae_el_digito_final():
    from app.main import _numero_de_orden
    assert _numero_de_orden("S00099") == 99
    assert _numero_de_orden("S00001") == 1


def test_numero_de_orden_sin_digitos_es_none():
    from app.main import _numero_de_orden
    assert _numero_de_orden(None) is None
    assert _numero_de_orden("") is None


# ---------------------------------------------------------------------------
# Un solo título, una sola lista
# ---------------------------------------------------------------------------

def test_un_solo_tablero_de_lista(cliente, odoo):
    """El FONDO que ya verificaba el título único: ventas de plantas y
    cotizaciones de servicio viven en UNA sola lista, sin una sección
    "Cotizaciones de servicios" aparte. Con el diseño Orquesta el título
    se volvió un tablero único (.vd-tablero) — uno solo."""
    _insertar_venta(501, "S00050")
    n_serv = _insertar_servicio(601, "S00049")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get("/venta").text
    assert pagina.count('class="vd-tablero"') == 1
    assert "Cotizaciones de servicios" not in pagina
    assert "Ana" in pagina and "Beto" in pagina  # los dos, en el mismo tablero
    assert n_serv  # la fila sí se creó (evita el "insertado pero no usado")


# ---------------------------------------------------------------------------
# Orden descendente por número, mezclando venta y servicio de verdad
# ---------------------------------------------------------------------------

def test_orden_descendente_por_numero(cliente, odoo):
    _insertar_venta(501, "S00050", cliente="Cincuenta")
    _insertar_servicio(601, "S00052", cliente="CincuentaYDos")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    _insertar_venta(502, "S00048", cliente="CuarentaYOcho")
    pagina = cliente.get("/venta").text
    posiciones = [pagina.index(n) for n in
                 ("CincuentaYDos", "Cincuenta", "CuarentaYOcho")]
    assert posiciones == sorted(posiciones)


def test_venta_y_servicio_se_intercalan_por_numero(cliente, odoo):
    """No "toda la lista de plantas y después toda la de servicios": el
    número manda, sin importar el tipo. Venta 50, servicio 48, venta 46
    tiene que salir en ESE orden — no venta-venta-servicio."""
    _insertar_venta(501, "S00050", cliente="VentaArriba")
    _insertar_servicio(601, "S00048", cliente="ServicioMedio")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    _insertar_venta(502, "S00046", cliente="VentaAbajo")
    pagina = cliente.get("/venta").text
    pos_arriba = pagina.index("VentaArriba")
    pos_medio = pagina.index("ServicioMedio")
    pos_abajo = pagina.index("VentaAbajo")
    assert pos_arriba < pos_medio < pos_abajo


def test_renglon_sin_numero_va_al_fondo_sin_romper_el_orden(cliente, odoo):
    """Decisión: un renglón sin número (todavía no llegó a Odoo) cae al
    FONDO de la lista, nunca intercalado entre los que sí tienen número."""
    _insertar_venta(501, "S00050", cliente="ConNumeroArriba")
    _insertar_venta(502, None, cliente="SinNumeroAlFondo")
    _insertar_venta(503, "S00040", cliente="ConNumeroAbajo")
    pagina = cliente.get("/venta").text
    pos_arriba = pagina.index("ConNumeroArriba")
    pos_abajo = pagina.index("ConNumeroAbajo")
    pos_sin = pagina.index("SinNumeroAlFondo")
    assert pos_arriba < pos_abajo < pos_sin


# ---------------------------------------------------------------------------
# Cada tarjeta conserva sus propias acciones y chips
# ---------------------------------------------------------------------------

def test_cada_tarjeta_conserva_sus_propias_acciones(cliente, odoo):
    """Con la tarjeta simple (BLOQUE 32) las acciones viven en el PANEL
    de cada tarjeta — y cada panel sigue ofreciendo SOLO las suyas."""
    n_venta = _insertar_venta(501, "S00050", cliente="AnaVenta")
    n_serv = _insertar_servicio(601, "S00049", cliente="BetoServicio")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    # La venta de plantas: Facturar/Pagado y Cancelar, nunca Editar.
    panel_venta = cliente.get(f"/venta?abrir=v{n_venta}").text
    assert "Facturar / Pagado" in panel_venta
    assert f'action="/venta/cancelar/{n_venta}"' in panel_venta
    assert ">Editar</a>" not in panel_venta
    # La cotización de servicio: Editar y Quitar, nunca Facturar/Pagado.
    panel_servicio = cliente.get(f"/venta?abrir=s{n_serv}").text
    assert f'href="/venta/servicio/{n_serv}/editar"' in panel_servicio
    assert ">Quitar</button>" in panel_servicio
    assert "Facturar / Pagado" not in panel_servicio


# ---------------------------------------------------------------------------
# Una cancelada no se pinta, pero el dato no se toca (dueño, 30/09/2026)
# ---------------------------------------------------------------------------

def test_venta_cancelada_no_se_pinta_pero_el_dato_sigue(cliente, odoo):
    n_venta = _insertar_venta(501, "S00060", cliente="VentaCancelada",
                              estado="cancelada")
    pagina = cliente.get("/venta").text
    assert "VentaCancelada" not in pagina
    # El registro local no se borró.
    assert ventas.obtener_venta(n_venta)["estado"] == "cancelada"


def test_servicio_cancelado_en_odoo_no_se_pinta_pero_el_dato_sigue(cliente, odoo):
    n_serv = _insertar_servicio(602, "S00061", cliente="ServicioCancelado")
    odoo.ordenes[602] = {"state": "cancel", "invoice_ids": []}
    pagina = cliente.get("/venta").text
    assert "ServicioCancelado" not in pagina
    # El registro local no se borró (cotizaciones_servicio no guarda
    # "cancelada": el estado real vive en Odoo).
    assert cotizaciones.obtener(n_serv) is not None


# ---------------------------------------------------------------------------
# "Quitar" en una cotización de servicio = "Cancelar" en una venta
# ---------------------------------------------------------------------------

def test_quitar_cancela_en_odoo_y_desaparece_solo(cliente, odoo):
    n = _insertar_servicio(701, "S00070", cliente="Duplicado")
    odoo.ordenes[701] = {"state": "sale", "invoice_ids": []}
    r = cliente.post(f"/venta/servicio/{n}/cancelar", follow_redirects=False)
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    assert odoo.ordenes[701]["state"] == "cancel"
    # Desaparece solo de la lista unificada por el filtro de canceladas
    # (punto de arriba) — no hace falta lógica nueva para esto.
    pagina = cliente.get("/venta").text
    assert "Duplicado" not in pagina


def test_quitar_una_ya_facturada_no_se_puede(cliente, odoo):
    n = _insertar_servicio(702, "S00071", cliente="YaFacturado")
    odoo.ordenes[702] = {"state": "sale", "invoice_ids": [55]}
    r = cliente.post(f"/venta/servicio/{n}/cancelar", follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert odoo.ordenes[702]["state"] == "sale"


def test_quitar_reusa_el_mismo_camino_que_cancelar(monkeypatch):
    """La prueba que pidió Abraham: "Quitar" y "Cancelar" tienen que
    llamar a la MISMA función de Odoo — nunca dos lógicas separadas para
    lo mismo."""
    llamadas = []
    monkeypatch.setattr(ventas, "_cancelar_en_odoo", llamadas.append)

    monkeypatch.setattr(cotizaciones, "obtener", lambda n: {"orden_id": 999})
    monkeypatch.setattr(cotizaciones, "estados_en_odoo", lambda ids: {})
    cotizaciones.cancelar(1)
    assert llamadas == [999]

    monkeypatch.setattr(
        ventas, "obtener_venta",
        lambda n: {"orden_id": 999, "estado": "cotizacion"})
    monkeypatch.setattr(ventas, "_actualizar_venta", lambda n, **kw: None)
    ventas.cancelar(1)
    assert llamadas == [999, 999]


# ---------------------------------------------------------------------------
# Nº4 (2/10/2026): Odoo caído al armar la lista NO deja mudo al empleado
# ---------------------------------------------------------------------------

def test_odoo_caido_avisa_que_editar_y_quitar_no_estan(cliente, monkeypatch):
    """Antes el `except Exception: pass` dejaba todas las cotizaciones de
    servicio sin Editar/Quitar sin una palabra: el empleado creía que la
    cotización «estaba rara». Ahora la lista sale igual (sin los botones,
    mejor sin botón que un botón que rompe) pero CON el aviso honesto."""
    _insertar_servicio(601, "S00049", cliente="ServicioSinBotones")

    def revienta(_ids):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(cotizaciones, "estados_en_odoo", revienta)
    pagina = cliente.get("/venta").text
    assert "ServicioSinBotones" in pagina          # la lista no se rompe
    assert "Odoo no contesta en este momento" in pagina
    assert "Editar y Quitar" in pagina


def test_con_odoo_sano_no_sale_el_aviso(cliente, odoo):
    _insertar_servicio(601, "S00049")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get("/venta").text
    assert "Odoo no contesta en este momento" not in pagina


def test_sin_cotizaciones_no_hay_aviso_aunque_odoo_este_caido(cliente,
                                                              monkeypatch):
    """Sin ni una cotización con orden no hay botones que perder: el aviso
    sería ruido."""
    def revienta(_ids):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(cotizaciones, "estados_en_odoo", revienta)
    pagina = cliente.get("/venta").text
    assert "Odoo no contesta en este momento" not in pagina
