"""Vender con el diseño Orquesta (Korto, 2/10/2026): el rediseño es SOLO
de aspecto, así que estas pruebas amarran lo que no puede perderse —
cada acción que la pantalla tenía antes sigue en el HTML nuevo, con su
misma ruta; la lista única se agrupa en columnas sin cambiar el orden; y
la tarjeta abierta (?abrir=) muestra las mismas acciones sin estrenar
rutas.

El Odoo mínimo es el mismo de test_vender_lista.py: solo search_read de
sale.order (estados) y action_cancel."""

from datetime import datetime

import pytest

from app import cotizaciones, ventas
from app.datos import ZONA_PANAMA, _db


class OdooMinimo:
    def __init__(self):
        self.ordenes = {}

    def ejecutar(self, modelo, metodo, args, kw=None):
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
                    estado="cotizacion", celular=None, factura_id=None,
                    factura=None, metodo=None):
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " celular, orden_id, orden, total, estado, factura_id,"
            " factura, metodo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", cliente,
             celular, orden_id, orden, total, estado, factura_id,
             factura, metodo))
        return cursor.lastrowid


def _insertar_servicio(orden_id, orden, cliente="Beto", total=200.0,
                       tipo="renta"):
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", tipo,
             cliente, None, orden_id, orden, total))
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Ninguna acción de hoy desaparece (límite duro del rediseño)
# ---------------------------------------------------------------------------

def test_cotizacion_de_planta_conserva_sus_tres_acciones(cliente, odoo):
    n = _insertar_venta(501, "S00050")
    pagina = cliente.get("/venta").text
    assert f'href="/venta/pago/{n}"' in pagina          # Facturar / Pagado
    assert "Facturar / Pagado" in pagina
    assert f"/venta/{n}/cotizacion.pdf" in pagina          # Descargar / Compartir
    assert "Descargar / Compartir" in pagina
    assert f'action="/venta/cancelar/{n}"' in pagina       # Cancelar (POST igual)
    assert ">Cancelar</button>" in pagina


def test_cotizacion_de_servicio_conserva_sus_tres_acciones(cliente, odoo):
    n = _insertar_servicio(601, "S00049")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get("/venta").text
    assert f"/venta/servicio/{n}/propuesta.pdf" in pagina
    assert "Descargar / Compartir" in pagina               # el nombre del botón
    assert f'href="/venta/servicio/{n}/editar"' in pagina  # Editar
    assert f'action="/venta/servicio/{n}/cancelar"' in pagina  # Quitar
    assert ">Quitar</button>" in pagina


def test_venta_pagada_conserva_factura_y_mandar_factura(cliente, odoo):
    n = _insertar_venta(502, "S00051", cliente="Carla", estado="pagado",
                        celular="61234567", factura_id=9, factura="F00009",
                        metodo="yappy")
    pagina = cliente.get("/venta").text
    assert f"/venta/{n}/factura.pdf" in pagina
    assert "Descargar / Compartir factura" in pagina
    assert "Mandar factura" in pagina and "wa.me/50761234567" in pagina
    assert "F00009" in pagina and "Yappy" in pagina


def test_venta_atorada_conserva_reintentar(cliente, odoo):
    n = _insertar_venta(503, "S00052", cliente="Atorada", estado="facturada")
    pagina = cliente.get("/venta").text
    assert f'href="/venta/pago/{n}"' in pagina
    assert "Reintentar" in pagina


def test_vendida_conserva_descargar_con_download_y_data_pdf(cliente, odoo):
    _insertar_venta(504, "S00053", cliente="Vendida", estado="vendida")
    pagina = cliente.get("/venta").text
    # La regla del 28/09: descarga Y pestaña nueva; y data-pdf para la
    # hoja nativa del iPhone (compartir.js).
    assert 'target="_blank"' in pagina and "data-pdf" in pagina
    assert 'download="' in pagina


def test_nueva_venta_y_cotizar_servicio_siguen(cliente, odoo, monkeypatch):
    # El botón negro y los chips de cotizar solo se pintan con Vender
    # prendido (ventas.configurado()); aquí se prende sin Odoo real.
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    pagina = cliente.get("/venta").text
    assert 'href="/venta/nueva"' in pagina                 # el botón negro
    assert "/venta/servicio/renta" in pagina               # chips de cotizar
    assert "/venta/servicio-personalizada" in pagina       # + Personalizado


# ---------------------------------------------------------------------------
# Las columnas: agrupación por el estado de hoy, orden intacto
# ---------------------------------------------------------------------------

def test_columnas_cotizado_confirmado_pagado(cliente, odoo):
    _insertar_venta(505, "S00060", cliente="EnCotizado")
    _insertar_venta(506, "S00061", cliente="EnConfirmado", estado="vendida")
    _insertar_venta(507, "S00062", cliente="EnPagado", estado="pagado")
    n_serv = _insertar_servicio(602, "S00063", cliente="ServicioFacturado")
    odoo.ordenes[602] = {"state": "sale", "invoice_ids": [77]}
    pagina = cliente.get("/venta").text
    assert pagina.count('class="vd-col"') == 3
    assert "Cotizado" in pagina and "Confirmado" in pagina and "Pagado" in pagina
    # Un servicio facturado cae en Confirmado (su cobro vive en Odoo); el
    # orden DENTRO de una columna sigue siendo por número descendente.
    pos_conf = pagina.index("Confirmado")
    assert pagina.index("ServicioFacturado") > pos_conf
    assert pos_conf < pagina.index("EnConfirmado")
    assert n_serv


def test_las_anclas_no_pierden_el_lugar(cliente, odoo):
    n_v = _insertar_venta(508, "S00064")
    n_s = _insertar_servicio(603, "S00065")
    odoo.ordenes[603] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get("/venta").text
    assert f'id="v-{n_v}"' in pagina
    # El ancla cot-N se conserva: /venta/servicio/N/editar redirige a
    # /venta#cot-N y ese enganche no puede romperse.
    assert f'id="cot-{n_s}"' in pagina


# ---------------------------------------------------------------------------
# La tarjeta abierta (?abrir=): panel sin rutas nuevas
# ---------------------------------------------------------------------------

def test_abrir_cotizacion_de_servicio_muestra_el_panel(cliente, odoo):
    n = _insertar_servicio(604, "S00066", cliente="PanelServicio")
    odoo.ordenes[604] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get(f"/venta?abrir=s{n}").text
    assert 'class="vd-panel"' in pagina
    # Las mismas acciones de la tarjeta, dentro del panel.
    assert pagina.count(f'href="/venta/servicio/{n}/editar"') == 2  # tarjeta + panel
    assert pagina.count(f'action="/venta/servicio/{n}/cancelar"') == 2


def test_abrir_venta_muestra_el_panel_con_facturar(cliente, odoo):
    n = _insertar_venta(509, "S00067", cliente="PanelVenta")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert 'class="vd-panel"' in pagina
    assert pagina.count(f'href="/venta/pago/{n}"') == 2  # tarjeta + panel
    # Cerrar es un enlace que vuelve al ancla (no perder el lugar).
    assert f'href="/venta#v-{n}"' in pagina


def test_abrir_invalido_no_rompe_ni_abre_nada(cliente, odoo):
    _insertar_venta(510, "S00068")
    for malo in ("x9", "v", "v99999", "s1particular", ""):
        r = cliente.get(f"/venta?abrir={malo}")
        assert r.status_code == 200
        assert 'class="vd-panel"' not in r.text


def test_sin_abrir_no_hay_panel(cliente, odoo):
    _insertar_venta(511, "S00069")
    assert 'class="vd-panel"' not in cliente.get("/venta").text


# ---------------------------------------------------------------------------
# El menú nuevo: solo las pestañas que existen hoy
# ---------------------------------------------------------------------------

def test_menu_sin_pestanas_inventadas(cliente, odoo):
    pagina = cliente.get("/venta").text
    nav = pagina.split("<nav>")[1].split("</nav>")[0]
    for pestana in ("Calendario", "Stock", "Vender", "Control", "Compras",
                    "Ajustes"):
        assert pestana in nav
    for inventada in ("Pedidos", "CRM", "Analytics"):
        assert inventada not in nav


def test_el_cajon_movil_tiene_su_x_de_cerrar(cliente, odoo):
    pagina = cliente.get("/venta").text
    assert 'class="nav-cerrar"' in pagina
