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

from app import cotizaciones, venta_estado, ventas
from app.datos import ZONA_PANAMA, _db


class OdooMinimo:
    def __init__(self):
        self.ordenes = {}
        # Cada llamada que la pantalla le hace a Odoo, para poder afirmar
        # que una vista nueva no agrega NI UN viaje (BLOQUE 56, punto 8).
        self.llamadas = []

    def ejecutar(self, modelo, metodo, args, kw=None):
        self.llamadas.append((modelo, metodo))
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
    """Los chips que la tarjeta vieja mostraba («Cotización» y el de los
    tres estados, con su enlace a /venta/estado) viven ahora en el panel.
    El chip del estado dejó de llevar el número delante (BLOQUE 53 ·
    A10): dice qué falta, que es lo que la persona necesita."""
    n = _insertar_venta(515, "S00076", cliente="ConChips")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert '<span class="vd-chip">Cotización</span>' in pagina
    assert venta_estado.ETIQUETA_CORTA[1] in pagina
    assert "Falta el pago" in pagina        # y en palabras, no "1 · …"
    assert "1 · Acordada" not in pagina
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


# ---------------------------------------------------------------------------
# BLOQUE 59 · el ancho y el ras del tablero (7/10/2026)
# ---------------------------------------------------------------------------
#
# Lo que el dueño reportó y lo que la MEDICIÓN dijo (elemento renderizado,
# sobre el mismo CSS que corre en el 8095):
#
#   «las tarjetas no miden igual»  → FALSO: las nueve miden 78px exactas en
#       las tres columnas, de 1024 a 1920. Lo desparejo era dónde ARRANCA la
#       primera de cada columna: y=204 · 205 · 187.
#   «el título se parte en dos»    → CIERTO: esa cabecera medía 42px contra
#       24 de las otras dos. Y había una segunda causa sin reportar: el pie
#       de «Cotizado» ocupa 2 renglones (34px) contra 17.
#   «sobra ancho a 1280»           → CIERTO, con otro número: a 1280 las tres
#       columnas usaban el 87% del lienzo (852 de 984), no la mitad. La mitad
#       exacta es a 1920 — y ahí el hueco es del RESUMEN, no del tablero.
# ---------------------------------------------------------------------------

import re as _re

_CSS_VD = "app/static/diseno-vender.css"


def _apretado_vd():
    return _re.sub(r"\s+", "", open(_CSS_VD).read())


def test_la_cabecera_de_columna_tiene_altura_fija():
    """Para que las tres columnas arranquen al ras aunque un título ocupe
    dos renglones y un pie también. 42 y 34 son lo MEDIDO del caso más
    largo que ya existe, no números elegidos."""
    css = _apretado_vd()
    assert "min-height:42px" in css
    assert "min-height:34px" in css
    # Y el pie es un <span>: sin `display:block` el min-height no hace nada.
    assert "display:block;min-height:34px" in css


def test_la_altura_fija_de_la_cabecera_no_llega_al_telefono():
    """Ahí las columnas son secciones apiladas: no hay con quién alinearse
    y 42px serían 24 de aire por sección."""
    css = _apretado_vd()
    movil = css.split("@media(max-width:899px)", 1)[1]
    assert ".vd-ch{border:0;padding:0;min-height:0}" in movil


def test_las_columnas_llenan_el_carril_cuando_el_resumen_esta_debajo():
    """`1 0 276px`: crecen hasta llenar (el 1) y NUNCA bajan de 276 (el 0),
    que es lo que impide la tarjeta de una palabra por línea."""
    css = _apretado_vd()
    bloque = css.split("@containerlienzo(min-width:860px)", 1)[1].split("}", 2)
    assert "flex:10276px" in bloque[0]
    assert "max-width:340px" in bloque[0]


def test_el_bloque_de_1120_murio_con_el_panel():
    """El bloque `@container lienzo (min-width:1120px)` existía SOLO para
    devolverle a las columnas su ancho fijo (276) y hacerle sitio al
    resumen al costado. Al irse el resumen (BLOQUE 56, punto 8) se borró
    entero, y por eso las columnas crecen en TODOS los anchos: medido, el
    tablero pasó de 852 a 1044 a 1440, 1600 y 1920 de ventana.

    Queda clavado para que nadie lo reponga sin darse cuenta: con ese
    bloque de vuelta, el tablero volvería a quedarse en 852."""
    css = _apretado_vd()
    assert "@containerlienzo(min-width:1120px)" not in css
    assert "max-width:none" not in css
    assert "flex:01auto" not in css
    # Y las columnas crecen desde el ÚNICO escalón que queda.
    assert css.count("@containerlienzo(min-width:860px)") == 1


def test_la_tarjeta_sigue_midiendo_lo_mismo_en_las_tres_columnas():
    """Medido: 78px en las tres. Queda clavado para que el día que alguien
    le agregue un renglón a la tarjeta de «Pagado» —el visto, un chip— la
    suite lo diga en vez de dejar las columnas desparejas otra vez."""
    css = _apretado_vd()
    assert "min-height:78px" in css
    # el visto vive en una fila de alto fijo, así que no empuja la tarjeta
    assert ".vd-pie{align-items:center;gap:4px6px;min-height:28px}" in css


# ---------------------------------------------------------------------------
# EL RESUMEN DE LA DERECHA SE FUE, Y EN SU ANCHO VIVEN DOS VISTAS
# (punto 8 del BLOQUE 56, 7/10/2026)
#
# Abraham: «quitá el resumen de la derecha, hacé una vista». Lo que estas
# pruebas cuidan es, en este orden:
#   1. que el panel no esté — ni su HTML, ni su CSS, ni su función;
#   2. que lo COMPARTIDO siga en pie (es lo que la regla 11 midió: borrar
#      `col.total`, `col.cuenta` o `_NOMBRES_METODO` rompería el tablero,
#      las pastillas del teléfono y la línea «Yappy · vie 3»);
#   3. que las dos vistas existan, las decida Python y ninguna quede
#      inalcanzable (ni en computadora ni en el teléfono);
#   4. que la tabla no estrene NI UN viaje a Odoo ni una ruta.
# ---------------------------------------------------------------------------

def test_el_resumen_de_la_derecha_no_esta_en_ninguna_vista(cliente, odoo):
    _tres_filas(odoo)
    for ruta in ("/venta", "/venta?ver=tabla"):
        pagina = cliente.get(ruta).text
        for marca in ('class="vd-res"', "vd-tile", "vd-pvs", "vd-lienzo",
                      "Cómo pagaron", "en el tablero"):
            assert marca not in pagina, f"{marca} sigue en {ruta}"


def test_el_css_del_resumen_tampoco_esta():
    css = _apretado_vd()
    for marca in (".vd-res", ".vd-tile", ".vd-pvs", ".vd-pv-n", ".vd-lienzo"):
        assert marca + "{" not in css, f"{marca} sigue en la hoja"


def test_la_funcion_del_resumen_se_fue_con_el():
    """`_vender_pagos` y la clave `vender_pagos` eran exclusivas del panel
    (medido con la regla 11 en los cinco repos: ningún test, ninguna otra
    plantilla, ningún endpoint JSON)."""
    from app import main
    assert not hasattr(main, "_vender_pagos")


def test_lo_compartido_sigue_en_pie(cliente, odoo):
    """Las tres cosas que la regla 11 marcó como COMPARTIDAS y que un
    borrado de más se habría llevado:

    - `col.total` → el total en el encabezado de cada columna;
    - `col.cuenta` → el globito del encabezado y las pastillas del teléfono;
    - `_NOMBRES_METODO` → la línea «Efectivo · <fecha>» de una pagada,
      que la pinta `_linea_tarjeta`, no el panel que se fue.
    """
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    # el total de cada columna (los tres montos de _tres_filas)
    assert pagina.count('class="vd-tot num') == 3
    assert "$200.00" in pagina and "$50.00" in pagina and "$80.00" in pagina
    # el conteo de cada columna
    assert pagina.count('class="vd-cnt num"') == 3
    # y el método, que es lo único que leía _NOMBRES_METODO fuera del panel
    assert "Efectivo · " in pagina
    from app import main
    assert main._NOMBRES_METODO["efectivo"] == "Efectivo"


# ---------------------------------------------------------------------------
# El segmento de las dos vistas: el control de la casa, decidido en Python
# ---------------------------------------------------------------------------

def test_el_segmento_tiene_las_dos_caras_y_el_tablero_por_omision(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    assert '<a class="on" href="/venta" aria-current="page">Tablero</a>' in pagina
    assert '<a class="" href="/venta?ver=tabla">Tabla</a>' in pagina


def test_el_segmento_marca_la_tabla_cuando_es_la_que_se_mira(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta?ver=tabla").text
    assert '<a class="on" href="/venta?ver=tabla" aria-current="page">Tabla</a>' in pagina
    assert '<a class="" href="/venta">Tablero</a>' in pagina


def test_el_segmento_viene_dos_veces_para_que_el_telefono_lo_alcance(cliente, odoo):
    """Bajo 900px la cabecera entera está `display:none`: sin la copia
    `seg-movil` la tabla sería INALCANZABLE desde el celular — el defecto
    que ya apareció una vez con «Respuestas» (A17)."""
    _tres_filas(odoo)
    pagina = cliente.get("/venta").text
    assert pagina.count('class="vd-seg ') == 2
    assert 'class="vd-seg seg-movil"' in pagina
    css = _apretado_vd()
    # escondida por defecto y prendida SOLO en el bloque del teléfono
    assert ".vd-seg.seg-movil{display:none}" in css
    movil = css.split("@media(max-width:899px)", 1)[1]
    assert ".vd-seg.seg-movil{display:flex" in movil


def test_el_segmento_arrastra_la_vista_del_telefono(cliente, odoo):
    """Cambiar de cara no puede cambiar DOS cosas de un toque: si estabas
    en las pagadas, seguís en las pagadas."""
    _tres_filas(odoo)
    pagina = cliente.get("/venta?vista=pagadas").text
    assert 'href="/venta?vista=pagadas&amp;ver=tabla">Tabla</a>' in pagina
    assert ('href="/venta?vista=pagadas" aria-current="page">Tablero</a>'
            in pagina)
    # …y al revés: desde la tabla de las pagadas, «Tablero» tampoco
    # pierde la vista del teléfono.
    tabla = cliente.get("/venta?vista=pagadas&ver=tabla").text
    assert 'href="/venta?vista=pagadas">Tablero</a>' in tabla


def test_una_vista_manoseada_cae_en_el_tablero(cliente, odoo):
    _tres_filas(odoo)
    for mala in ("excel", "TABLA", "tabla%20", "1", "tablero2"):
        pagina = cliente.get(f"/venta?ver={mala}").text
        assert '<a class="on" href="/venta" aria-current="page">Tablero</a>' in pagina
        assert '<section class="vd-col' in pagina


# ---------------------------------------------------------------------------
# La vista de tabla
# ---------------------------------------------------------------------------

def test_la_tabla_tiene_las_cinco_columnas_que_pidio(cliente, odoo):
    _tres_filas(odoo)
    pagina = cliente.get("/venta?ver=tabla").text
    for titulo in ("Contacto", "Fecha", "Interés", "Etapa", "Monto"):
        assert f'<th scope="col"' in pagina and titulo in pagina
    # y el tablero NO se pinta al mismo tiempo: son dos vistas, no dos capas
    assert '<section class="vd-col' not in pagina


def test_la_tabla_trae_una_fila_por_renglon_de_la_lista_unica(cliente, odoo):
    """Las mismas filas del tablero —ventas de plantas y cotizaciones de
    servicio juntas— y en el MISMO orden (número de orden descendente):
    agrupar o tabular es presentación, nunca otro orden."""
    _tres_filas(odoo)
    n_serv = _insertar_servicio(610, "S00083", cliente="TablaServicio")
    odoo.ordenes[610] = {"state": "sale", "invoice_ids": []}
    pagina = cliente.get("/venta?ver=tabla").text
    assert pagina.count('<tr class="vd-tr"') == 4
    # S00083 > S00082 > S00081 > S00080
    assert (pagina.index("TablaServicio") < pagina.index("MvPagada")
            < pagina.index("MvConfirmada") < pagina.index("MvCotizada"))
    assert n_serv


def test_la_etapa_de_una_fila_es_la_columna_del_tablero(cliente, odoo):
    """Una fila y una tarjeta no pueden decir cosas distintas de la misma
    venta: la etapa sale de `_columna_vender`, la misma que reparte el
    kanban."""
    _tres_filas(odoo)
    pagina = cliente.get("/venta?ver=tabla").text
    from app import main
    for clave, titulo, _pista in main.COLUMNAS_VENDER:
        assert f'<td class="vd-td-etapa">{titulo}</td>' in pagina, clave


def test_el_monto_dice_si_esta_cobrado_o_por_cobrar(cliente, odoo):
    """El rótulo que le faltaba al resumen que se fue (prometía un
    cobrable y entregaba una facturación). En «Pagado» el total está
    cobrado; en las otras dos, por cobrar — y NO es un residual de Odoo,
    que en esta lista no viaja."""
    _tres_filas(odoo)
    pagina = cliente.get("/venta?ver=tabla").text
    assert pagina.count(">cobrado</span>") == 1       # la pagada
    assert pagina.count(">por cobrar</span>") == 2    # cotizada y confirmada
    # y la cifra por cobrar va en rojo, el mismo lenguaje del encabezado
    assert pagina.count('class="num vd-rojo"') == 2


def test_la_tabla_no_pinta_las_pildoras_del_telefono(cliente, odoo):
    """Las píldoras esconden SECCIONES del tablero; en la tabla no
    esconderían nada. Un control que no hace nada es peor que no tenerlo.

    OJO, medido el 7/10 y NO es de este punto: hoy ese `<nav class="vd-fl">`
    no se ve en el teléfono ni en el tablero, porque `@media (max-width:899px)
    nav{position:fixed;transform:translateX(-105%)}` (styles.css) le pega al
    ELEMENTO `nav` y se lleva también a este, que queda en x=-315. Pasa igual
    en `2335f6c`, así que esta prueba cuida la DECISIÓN de Python (si las
    píldoras se mandan o no), no que se vean. Queda reportado."""
    _tres_filas(odoo)
    assert 'class="vd-fl"' in cliente.get("/venta").text
    assert 'class="vd-fl"' not in cliente.get("/venta?ver=tabla").text


def test_desde_la_tabla_se_abre_el_MISMO_panel_y_cerrar_vuelve_a_la_tabla(
        cliente, odoo):
    """Ninguna acción se queda sin puerta por cambiar de vista: el nombre
    de la fila abre el panel de siempre (misma ruta, `data-panel-liga`) y
    cerrar devuelve a la TABLA, no al tablero."""
    _, _, n = _tres_filas(odoo)
    pagina = cliente.get("/venta?ver=tabla").text
    assert f'href="/venta?ver=tabla&amp;abrir=v{n}#v-{n}"' in pagina
    assert 'data-panel-liga' in pagina
    panel = cliente.get(f"/venta?ver=tabla&abrir=v{n}").text
    assert 'class="vd-panel"' in panel
    assert f'href="/venta?ver=tabla#v-{n}"' in panel
    # el pedazo que pide panel.js (A5) contesta lo mismo, con su vista
    pedazo = cliente.get(f"/venta/panel?ver=tabla&abrir=v{n}").text
    assert f'href="/venta?ver=tabla#v-{n}"' in pedazo


def test_en_la_vista_de_siempre_las_urls_no_cambian(cliente, odoo):
    """`ver=tablero` es el valor por omisión y NO se escribe: los enlaces
    de toda la vida siguen idénticos (los otros tests de este archivo los
    afirman letra por letra)."""
    _, _, n = _tres_filas(odoo)
    assert f'href="/venta?abrir=v{n}#v-{n}"' in cliente.get("/venta").text
    assert "ver=tablero" not in cliente.get("/venta").text


def test_la_tabla_no_cuesta_ni_un_viaje_nuevo_a_odoo(cliente, odoo):
    """Las cinco columnas salen de lo que la lista única YA trae: el
    cliente y la fecha de la tabla local, el interés de `colores`, la
    etapa de `_columna_vender` y el monto del `total` de siempre."""
    _tres_filas(odoo)
    _insertar_servicio(611, "S00084", cliente="ViajeServicio")
    odoo.ordenes[611] = {"state": "sale", "invoice_ids": []}
    cliente.get("/venta")
    del odoo.llamadas[:]
    cliente.get("/venta")
    tablero = list(odoo.llamadas)
    del odoo.llamadas[:]
    cliente.get("/venta?ver=tabla")
    assert list(odoo.llamadas) == tablero


def test_la_tabla_y_el_tablero_cuentan_LA_MISMA_plata(cliente, odoo):
    """El pecado del resumen que se fue era decir una cosa donde el resto
    de la casa decía otra. Las dos vistas tienen que cuadrar al centavo:
    lo «cobrado» de la tabla ES el total de la columna «Pagado», y lo «por
    cobrar» ES la suma de las otras dos."""
    from app import main
    _tres_filas(odoo)
    _insertar_servicio(612, "S00085", cliente="CuadreServicio", total=333.0)
    odoo.ordenes[612] = {"state": "sale", "invoice_ids": []}
    # Se piden por la MISMA puerta que usa la pantalla, no por una copia.
    pagina = cliente.get("/venta?ver=tabla")
    assert pagina.status_code == 200
    filas, _aviso = main._lista_vender(_PeticionFalsa(), "pendientes", "tabla")
    columnas = {c["clave"]: c for c in main._vender_columnas(filas)}
    tabla = main._vender_tabla(filas)
    cobrado = sum(r["total"] for r in tabla if r["cobrado"])
    por_cobrar = sum(r["total"] for r in tabla if not r["cobrado"])
    assert cobrado == columnas["pagado"]["total"]
    assert por_cobrar == (columnas["cotizado"]["total"]
                          + columnas["confirmado"]["total"])
    # y ninguna fila se queda afuera ni se cuenta dos veces
    assert len(tabla) == len(filas) == sum(c["cuenta"] for c in columnas.values())


class _PeticionFalsa:
    """`_lista_vender` solo usa `request` para armar el wa.me de una venta
    pagada con celular (_enlace_whatsapp); para el cuadre basta una base
    de URL."""
    base_url = "http://pruebas/"
