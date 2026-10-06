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
                    factura=None, metodo=None, ultimo_error=None):
    with _db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " celular, orden_id, orden, total, estado, factura_id,"
            " factura, metodo, ultimo_error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", cliente,
             celular, orden_id, orden, total, estado, factura_id,
             factura, metodo, ultimo_error))
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
# Ninguna acción de hoy desaparece (límite duro del rediseño). Con la
# tarjeta SIMPLE (BLOQUE 32) las acciones viven en el PANEL que la
# tarjeta abre (?abrir=) — la sustancia es la misma: cada función sigue
# viva, con su misma ruta.
# ---------------------------------------------------------------------------

def test_cotizacion_de_planta_conserva_sus_tres_acciones(cliente, odoo):
    n = _insertar_venta(501, "S00050")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert f'href="/venta/pago/{n}"' in pagina          # Facturar / Pagado
    assert "Facturar / Pagado" in pagina
    assert f"/venta/{n}/cotizacion.pdf" in pagina          # Descargar / Compartir
    assert "Descargar / Compartir" in pagina
    assert f'action="/venta/cancelar/{n}"' in pagina       # Cancelar (POST igual)
    assert ">Cancelar</button>" in pagina


def test_cotizacion_de_servicio_conserva_sus_tres_acciones(cliente, odoo):
    n = _insertar_servicio(601, "S00049")
    odoo.ordenes[601] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get(f"/venta?abrir=s{n}").text
    assert f"/venta/servicio/{n}/propuesta.pdf" in pagina
    assert "Descargar / Compartir" in pagina               # el nombre del botón
    assert f'href="/venta/servicio/{n}/editar"' in pagina  # Editar
    assert f'action="/venta/servicio/{n}/cancelar"' in pagina  # Quitar
    assert ">Quitar</button>" in pagina


def test_venta_pagada_conserva_factura_y_mandar_factura(cliente, odoo):
    n = _insertar_venta(502, "S00051", cliente="Carla", estado="pagado",
                        celular="61234567", factura_id=9, factura="F00009",
                        metodo="yappy")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert f"/venta/{n}/factura.pdf" in pagina
    assert "Descargar / Compartir factura" in pagina
    assert "Mandar factura" in pagina and "wa.me/50761234567" in pagina
    assert "F00009" in pagina and "Yappy" in pagina


def test_venta_atorada_conserva_reintentar(cliente, odoo):
    n = _insertar_venta(503, "S00052", cliente="Atorada", estado="facturada")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert f'href="/venta/pago/{n}"' in pagina
    assert "Reintentar" in pagina
    # Y la TARJETA avisa que algo está pendiente (su único dato
    # contextual): la etiqueta del paso sigue en el tablero.
    assert "pago pendiente" in cliente.get("/venta").text


def test_vendida_conserva_descargar_con_download_y_data_pdf(cliente, odoo):
    n = _insertar_venta(504, "S00053", cliente="Vendida", estado="vendida")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    # La regla del 28/09: descarga Y pestaña nueva; y data-pdf para la
    # hoja nativa del iPhone (compartir.js).
    assert 'target="_blank"' in pagina and "data-pdf" in pagina
    assert 'download="' in pagina


# ---------------------------------------------------------------------------
# La tarjeta SIMPLE (BLOQUE 32, pág. 09): nombre, monto, color y UN dato
# contextual — nada de acciones ni chips en el tablero.
# ---------------------------------------------------------------------------

def test_la_tarjeta_no_lleva_acciones_ni_chips(cliente, odoo):
    n = _insertar_venta(512, "S00072", cliente="TarjetaSimple")
    n_s = _insertar_servicio(605, "S00073", cliente="ServicioSimple")
    odoo.ordenes[605] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get("/venta").text  # sin ?abrir=: puro tablero
    # Lo que la tarjeta SÍ tiene: nombre (enlace al panel) y monto.
    assert "TarjetaSimple" in pagina and "ServicioSimple" in pagina
    assert f'href="/venta?abrir=v{n}#v-{n}"' in pagina
    assert f'href="/venta?abrir=s{n_s}#cot-{n_s}"' in pagina
    # Lo que se mudó al panel: acciones, chips y el chip de 3 estados.
    assert f'href="/venta/pago/{n}"' not in pagina
    assert "Facturar / Pagado" not in pagina
    assert f"/venta/{n}/cotizacion.pdf" not in pagina
    assert "forma-cancelar" not in pagina
    assert f'href="/venta/servicio/{n_s}/editar"' not in pagina
    assert '<span class="vd-chip">Cotización</span>' not in pagina
    assert "Acordada" not in pagina


def test_la_linea_contextual_la_decide_python(cliente, odoo):
    """El único dato contextual de cada tarjeta llega listo de Python
    (main._linea_tarjeta): cómo pagó la pagada, cuándo se cotizó la
    cotización."""
    _insertar_venta(513, "S00074", cliente="PagadaConYappy",
                    estado="pagado", metodo="yappy")
    _insertar_venta(514, "S00075", cliente="CotizadaConFecha")
    pagina = cliente.get("/venta").text
    assert "Yappy ·" in pagina        # la pagada dice cómo pagó
    assert "Cotizada ·" in pagina     # la cotización dice cuándo


def test_el_panel_muestra_los_chips_y_el_estado3(cliente, odoo):
    """Los chips que la tarjeta vieja mostraba («Cotización», «1 ·
    Acordada» con su enlace a /venta/estado) viven ahora en el panel."""
    n = _insertar_venta(515, "S00076", cliente="ConChips")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert '<span class="vd-chip">Cotización</span>' in pagina
    assert "1 · Acordada" in pagina
    assert f'href="/venta/estado/venta/{n}"' in pagina


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
    # Las columnas siguen siendo 3 (ahora llevan además sus clases de
    # sección celular vd-mv-*, BLOQUE 37 — la sustancia es la misma).
    assert pagina.count('<section class="vd-col') == 3
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
    # Las acciones viven SOLO en el panel (la tarjeta es simple).
    assert pagina.count(f'href="/venta/servicio/{n}/editar"') == 1
    assert pagina.count(f'action="/venta/servicio/{n}/cancelar"') == 1


def test_abrir_venta_muestra_el_panel_con_facturar(cliente, odoo):
    n = _insertar_venta(509, "S00067", cliente="PanelVenta")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert 'class="vd-panel"' in pagina
    assert pagina.count(f'href="/venta/pago/{n}"') == 1  # solo el panel
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
# El CUERPO CELULAR (BLOQUE 37, pantalla 25): píldoras Pendientes/Pagadas
# por GET (?vista=), secciones del lienzo y lo apagado en su lugar. El
# HTML es el mismo para computadora y teléfono (las caras celulares se
# prenden por CSS ≤899px): aquí se amarra la ESTRUCTURA que llega de
# Python — conteos reales, agrupación, nada inventado.
# ---------------------------------------------------------------------------

def _tres_filas(odoo):
    """1 cotizada + 1 confirmada (pendientes) y 1 pagada."""
    a = _insertar_venta(520, "S00080", cliente="MvCotizada", total=50.0)
    b = _insertar_venta(521, "S00081", cliente="MvConfirmada",
                        estado="vendida", total=200.0)
    c = _insertar_venta(522, "S00082", cliente="MvPagada",
                        estado="pagado", total=80.0, metodo="efectivo")
    return a, b, c


def test_pildoras_con_conteos_reales_y_navegacion_por_get(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    # Las dos píldoras navegan por GET, con los conteos de verdad
    # (2 pendientes = cotizada + confirmada; 1 pagada) y Pendientes
    # activa por defecto.
    assert '<a class="vd-fc on" href="/venta?vista=pendientes">Pendientes 2</a>' in pagina
    assert '<a class="vd-fc" href="/venta?vista=pagadas">Pagadas 1</a>' in pagina


def test_vista_pendientes_trae_las_dos_secciones_del_lienzo(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    # Los títulos de sección del lienzo, con su conteo inline.
    assert "Falta cobrar · 1" in pagina
    assert "Cotizado, sin pagar · 1" in pagina
    # La sección de pagadas queda fuera de esta vista (vd-mv-fuera); las
    # dos pendientes no. Y «Falta cobrar» va primero (order vd-mv-1).
    assert pagina.count("vd-mv-fuera") == 1
    assert 'vd-mv-1 vd-mv-fuera' in pagina  # la fuera es la de pagadas
    # El total de cada sección es el real.
    assert "$200.00" in pagina and "$50.00" in pagina


def test_vista_pagadas_solo_deja_su_seccion(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta?vista=pagadas").text
    # La píldora activa ahora es Pagadas.
    assert '<a class="vd-fc on" href="/venta?vista=pagadas">Pagadas 1</a>' in pagina
    assert '<a class="vd-fc" href="/venta?vista=pendientes">Pendientes 2</a>' in pagina
    # Fuera quedan las DOS secciones pendientes; la de pagadas se queda.
    assert pagina.count("vd-mv-fuera") == 2
    assert "Pagadas · 1" in pagina


def test_vista_manoseada_cae_en_pendientes(cliente, odoo):
    _tres_filas(odoo)
    for mala in ("rara", "PAGADAS", "pagadas%20", "1"):
        pagina = cliente.get(f"/venta?vista={mala}").text
        assert '<a class="vd-fc on" href="/venta?vista=pendientes"' in pagina


def test_abrir_y_cerrar_no_pierden_la_vista_pagadas(cliente, odoo):
    _, _, n = _tres_filas(odoo)
    pagina = cliente.get("/venta?vista=pagadas").text
    # La tarjeta abre arrastrando la vista (& escapado por Jinja)…
    assert f'href="/venta?vista=pagadas&amp;abrir=v{n}#v-{n}"' in pagina
    panel = cliente.get(f"/venta?vista=pagadas&abrir=v{n}").text
    assert 'class="vd-panel"' in panel
    # …y cerrar vuelve a la MISMA vista, al ancla de la tarjeta.
    assert f'href="/venta?vista=pagadas#v-{n}"' in panel
    # En la vista de siempre las URLs no cambian (lo amarra también
    # test_abrir_venta_muestra_el_panel_con_facturar).
    assert f'href="/venta?abrir=v{n}#v-{n}"' in cliente.get("/venta").text


def test_cobrar_de_la_tarjeta_va_apagado_y_sin_ruta(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    # El «Cobrar» del lienzo existe pero APAGADO (pide el flujo de
    # abonos): solo en las 2 tarjetas pendientes, nunca en la pagada, y
    # sin href ni action — no hay a dónde ir todavía.
    assert pagina.count("Cobrar — Todavía no") == 2
    assert pagina.count('<button class="vd-cobrar" type="button" disabled>') == 2


def test_aviso_de_atoradas_solo_con_dato_real(cliente, odoo):
    # Sin atoradas: ni rastro del aviso (nada se inventa — el «4 ventas
    # por revisar» del lienzo no se pinta sin dato).
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    assert "por revisar" not in pagina
    assert "vd-alerta-rev" not in pagina
    # Con una atorada de verdad (ultimo_error a medio pipeline): el
    # aviso con su conteo real y su enlace al panel (donde vive
    # Reintentar).
    n = _insertar_venta(523, "S00083", cliente="MvAtorada",
                        estado="facturada", ultimo_error="Odoo no contestó")
    pagina = cliente.get("/venta").text
    assert "1 venta atorada por revisar" in pagina
    assert f'href="/venta?abrir=v{n}#v-{n}">Revisar</a>' in pagina


def test_lo_del_lienzo_sin_dato_no_se_pinta(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    # «vence en N días» no existe como dato: la tarjeta conserva su
    # línea contextual real (Cotizada · fecha).
    assert "vence en" not in pagina
    assert "Cotizada ·" in pagina
    # El selector de Ajustes del lienzo no existe: su línea tampoco.
    assert "En Ajustes puedes elegir" not in pagina


# ---------------------------------------------------------------------------
# El menú nuevo: solo las pestañas que existen hoy
# ---------------------------------------------------------------------------

def test_menu_sin_pestanas_inventadas(cliente, odoo):
    # «Pedidos» dejó de ser inventada el 6/10/2026: la pestaña existe de
    # verdad (punto 4 del BLOQUE 12, diseño con ACK del Arquitecto). Las
    # del lienzo que siguen sin construirse siguen prohibidas.
    # Y «Control» pasó a llamarse «CRM» el 6/10/2026 (BLOQUE 35): la
    # ruta /control se queda, el nombre visible no.
    pagina = cliente.get("/venta").text
    nav = pagina.split("<nav>")[1].split("</nav>")[0]
    for pestana in ("Calendario", "Stock", "Vender", "CRM", "Compras",
                    "Pedidos", "Ajustes"):
        assert pestana in nav
    for inventada in ("Control</a>", "Analytics"):
        assert inventada not in nav


def test_el_cajon_movil_tiene_su_x_de_cerrar(cliente, odoo):
    pagina = cliente.get("/venta").text
    assert 'class="nav-cerrar"' in pagina
