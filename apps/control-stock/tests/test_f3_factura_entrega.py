"""F3 — la factura de la ENTREGA, con renglones (6/10/2026).

Qué protege este archivo, en una línea: **que al cliente le siga llegando
una factura que dice qué compró.**

El problema. Hasta los items 5-7 el botón único validaba la salida de
stock ANTES de facturar, así que la única factura de la venta salía con
sus plantas renglón por renglón. Partido el botón (cobrar ≠ entregar), el
COBRO ya no tiene nada entregado que facturar y cae en un ANTICIPO de una
sola línea. Esta pieza devuelve el detalle: al marcar entregada sale la
factura final, con todos los renglones y el anticipo descontado — que es
el down payment estándar de Odoo, nada programado allá.

Las tres cosas que se clavan acá:

1. **La factura final sale con renglones, y la ATOMICIDAD.** Con un
   SEGUNDO paso en Odoo dentro de «Marcar entregada» aparece un hueco
   nuevo —salida validada y factura fallida— y el contrato de siempre
   («si Odoo no acepta, NADA quedó marcado») no se puede prometer igual.
   Se cierra como lo cierra `registrar_pago`: pasos sellados, cada uno
   idempotente, y el hecho local al final.
2. **El total de la venta NO se vuelve $0.00.** La final queda en $0
   cuando el anticipo ya cubrió todo, y `_facturar_orden` escribe `total`
   desde la factura leída: sin condicionarlo, cada venta entregada habría
   pasado a mostrar $0.00 en la lista de Vender.
3. **El renglón fantasma del anticipo, fuera del documento público /f/.**
"""

import pytest

from app import datos_roles, entregas, seguridad, venta_estado, ventas
from test_ventas import _agregar, odoo  # noqa: F401


@pytest.fixture
def jefa(db_limpia, odoo):  # noqa: F811
    """Un TestClient logueado como Génesis, que ADEMÁS carga el deber de
    system manager: así el mismo caso cobra por HTTP (como Mary) y marca
    la entrega (como quien tiene el asiento)."""
    from fastapi.testclient import TestClient

    from app.main import app

    seguridad.crear_empleada("genesis", "Génesis", "clave-de-prueba")
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    datos_roles.poner_persona(rol["n"], "genesis", "prueba")
    c = TestClient(app)
    assert c.post("/login", data={"usuario": "genesis",
                                  "contrasena": "clave-de-prueba"},
                  follow_redirects=False).status_code == 303
    return c


def _cobrar(cliente_web, producto_id=501, veces=1):
    """Una venta de plantas cobrada por el flujo real: carrito → cotizar →
    «Facturar / Pagado». Devuelve el número local."""
    _agregar(cliente_web, producto_id, veces=veces)
    r = cliente_web.post("/venta/pagar",
                         data={"cliente": "María", "celular": "6123-4567"},
                         follow_redirects=False)
    n = int(r.headers["location"].rsplit("/", 1)[1])
    assert cliente_web.post(f"/venta/cobrar/{n}",
                            data={"metodo": "yappy"}).status_code == 200
    assert ventas.obtener_venta(n)["estado"] == "pagado"
    return n


def _entregable(n):
    """La entrega lista para marcarse: la obligación con su asignado y el
    estado con el pago sellado, como lo deja el cobro."""
    venta_estado.abrir("venta", n, venta_estado.TIPO_PLANTAS, "Génesis")
    venta_estado.registrar_pago("venta", n, "Génesis", completo=True)
    assert entregas.guardar("venta", n, "Calle 50", "Mensajero Juan",
                            "Génesis") is None


def _marcar(n, fecha="2026-10-09"):
    return entregas.marcar_entregada("venta", n, "genesis", "Génesis",
                                     fecha=fecha)


# ---------------------------------------------------------------------------
# 1 · El punto de partida: sin esta pieza el cliente NO ve qué compró
# ---------------------------------------------------------------------------

def test_el_cobro_sale_como_anticipo_de_una_sola_linea(jefa, odoo):  # noqa: F811
    """El defecto que F3 viene a tapar, medido: cobrar antes de entregar
    factura un anticipo, y ese documento no lista las plantas."""
    n = _cobrar(jefa, veces=2)
    venta = ventas.obtener_venta(n)
    factura = odoo.facturas[venta["factura_id"]]
    assert [l["name"] for l in factura["lineas"]] == ["Anticipo"]
    assert factura["amount_total"] == 7.0  # 2 × ROMERO $3.50
    # Y la salida sigue abierta: el pago no entrega (items 5-7).
    assert ventas._salidas_pendientes(venta["orden_id"])


# ---------------------------------------------------------------------------
# 2 · Marcar entregada emite la factura final CON renglones
# ---------------------------------------------------------------------------

def test_marcar_entregada_emite_la_factura_con_renglones(jefa, odoo):  # noqa: F811
    n = _cobrar(jefa, veces=2)
    _entregable(n)
    error, fila = _marcar(n)
    assert error is None and fila["entrega_marcada"] == 1 and fila["estado"] == 3
    venta = ventas.obtener_venta(n)
    # Dos facturas: el anticipo del cobro y la final de la entrega.
    assert venta["factura_final_id"]
    assert venta["factura_final_id"] != venta["factura_id"]
    final = odoo.facturas[venta["factura_final_id"]]
    assert final["state"] == "posted"
    # LOS RENGLONES, que es todo el punto: la planta con su nombre y su
    # cantidad, MÁS la línea del anticipo en negativo.
    assert [(l["name"], l["quantity"]) for l in final["lineas"]] == [
        ("ROMERO", 2.0), ("Anticipo", -1.0)]
    # Neteada: el anticipo del 100% la deja en cero.
    assert final["amount_total"] == 0.0


def test_el_asistente_va_en_delivered_y_deduciendo(jefa, odoo):  # noqa: F811
    """La regla que pidió Abraham ES el down payment estándar de Odoo: lo
    único que hace la app es pedirlo bien. `deduct_down_payments` va
    EXPLÍCITO (su default es True, pero de él depende que la final netee:
    un default que cambie no debe cambiar la factura en silencio)."""
    n = _cobrar(jefa)
    _entregable(n)
    _marcar(n)
    modos = [v["advance_payment_method"]
             for v in odoo.asistentes_factura.values()]
    assert modos == ["fixed", "delivered"]
    final = [v for v in odoo.asistentes_factura.values()
             if v["advance_payment_method"] == "delivered"][0]
    assert final["deduct_down_payments"] is True


def test_el_total_de_la_venta_no_se_vuelve_cero(jefa, odoo):  # noqa: F811
    """El riesgo del punto 2, clavado: `_facturar_orden` escribe `total`
    desde la factura que leyó, y la final queda en $0. Si el sello de la
    final no estuviera condicionado, la lista de Vender mostraría $0.00 en
    cada venta entregada."""
    n = _cobrar(jefa, veces=2)
    assert ventas.obtener_venta(n)["total"] == 7.0
    _entregable(n)
    _marcar(n)
    venta = ventas.obtener_venta(n)
    # La final está en $0 …
    assert odoo.facturas[venta["factura_final_id"]]["amount_total"] == 0.0
    # … y el total del trato sigue intacto.
    assert venta["total"] == 7.0
    # Y lo que la lista de Vender pinta, también.
    assert "$7.00" in jefa.get("/venta").text
    # El estado y la factura del COBRO tampoco se movieron: ahí vive la
    # plata (_pagar_factura paga ESE id).
    assert venta["estado"] == "pagado"
    assert venta["factura"] == odoo.facturas[venta["factura_id"]]["name"]


# ---------------------------------------------------------------------------
# 3 · La atomicidad: el hueco «salida validada + factura fallida»
# ---------------------------------------------------------------------------

def test_si_la_factura_falla_la_entrega_no_queda_marcada(jefa, odoo):  # noqa: F811
    """El contrato nuevo: un intento que validó la salida pero no logró
    facturar NO deja `entrega_marcada` puesto. La entrega no está cerrada
    mientras falte la factura."""
    n = _cobrar(jefa)
    _entregable(n)
    odoo.fallar_una_vez = ("sale.advance.payment.inv", "create_invoices")
    error, resultado = _marcar(n)
    assert error == "odoo_factura"
    assert "odoo dijo que no" in resultado["detalle"]
    assert venta_estado.estado_de("venta", n)["entrega_marcada"] == 0
    assert ventas.obtener_venta(n)["factura_final_id"] is None
    # Pero la salida SÍ se escribió: por eso el aviso no puede decir «nada
    # quedó marcado» — y por eso el reintento tiene que retomar.
    assert not ventas._salidas_pendientes(ventas.obtener_venta(n)["orden_id"])


def test_el_reintento_retoma_desde_la_factura(jefa, odoo):  # noqa: F811
    """Volver a tocar el botón después del fallo: la salida ya está
    validada (paso idempotente, no se repite) y la factura sale. Una sola
    factura final, no dos."""
    n = _cobrar(jefa)
    _entregable(n)
    odoo.fallar_una_vez = ("sale.advance.payment.inv", "create_invoices")
    assert _marcar(n)[0] == "odoo_factura"
    error, fila = _marcar(n)
    assert error is None and fila["entrega_marcada"] == 1
    venta = ventas.obtener_venta(n)
    assert len(odoo.ordenes[venta["orden_id"]]["invoice_ids"]) == 2
    final = odoo.facturas[venta["factura_final_id"]]
    assert final["state"] == "posted"
    assert [l["name"] for l in final["lineas"]] == ["ROMERO", "Anticipo"]


def test_el_reintento_publica_el_borrador_que_quedo_colgado(jefa, odoo):  # noqa: F811
    """El otro medio camino: la factura se CREÓ y el action_post falló. El
    reintento no crea una segunda — reutiliza el borrador y lo publica.
    Y el orden de las comprobaciones importa: para Odoo un borrador ya
    cuenta como facturado (`invoice_status` pasa a 'invoiced'), así que si
    se preguntara «¿falta algo por facturar?» ANTES de buscar lo
    reutilizable, ese borrador se quedaría sin publicar para siempre."""
    n = _cobrar(jefa)
    _entregable(n)
    odoo.fallar_una_vez = ("account.move", "action_post")
    assert _marcar(n)[0] == "odoo_factura"
    orden_id = ventas.obtener_venta(n)["orden_id"]
    assert len(odoo.ordenes[orden_id]["invoice_ids"]) == 2
    colgada = odoo.ordenes[orden_id]["invoice_ids"][-1]
    assert odoo.facturas[colgada]["state"] == "draft"
    # El reintento la publica, sin crear otra.
    assert _marcar(n)[0] is None
    assert len(odoo.ordenes[orden_id]["invoice_ids"]) == 2
    assert odoo.facturas[colgada]["state"] == "posted"
    assert ventas.obtener_venta(n)["factura_final_id"] == colgada


def test_marcar_dos_veces_no_emite_una_segunda_factura(jefa, odoo):  # noqa: F811
    """El segundo toque del botón, cuando el primero sí terminó, es una
    corrección de fecha: no vuelve a facturar. Lo prueba la ruta real,
    que es donde vive esa bifurcación."""
    n = _cobrar(jefa)
    _entregable(n)
    assert _marcar(n)[0] is None
    orden_id = ventas.obtener_venta(n)["orden_id"]
    assert len(odoo.ordenes[orden_id]["invoice_ids"]) == 2
    r = jefa.post(f"/venta/estado/venta/{n}/entregada",
                  data={"fecha": "2026-10-11"}, follow_redirects=False)
    assert r.status_code == 303
    assert len(odoo.ordenes[orden_id]["invoice_ids"]) == 2
    assert venta_estado.estado_de("venta", n)["fecha_entrega"] == "2026-10-11"


def test_el_aviso_dice_que_la_salida_si_quedo_validada(jefa, odoo):  # noqa: F811
    """El error no puede mentir: en este hueco la salida se escribió. El
    aviso de pantalla lo dice y manda a reintentar."""
    n = _cobrar(jefa)
    _entregable(n)
    odoo.fallar_una_vez = ("sale.advance.payment.inv", "create_invoices")
    r = jefa.post(f"/venta/estado/venta/{n}/entregada",
                  data={"fecha": "2026-10-09"}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import parse_qs, urlparse

    aviso = parse_qs(urlparse(r.headers["location"]).query)["error"][0]
    assert "salida qued" in aviso and "validada en Odoo" in aviso
    assert "NO qued" in aviso and "Marcar" in aviso
    assert "nada qued" not in aviso


# ---------------------------------------------------------------------------
# 4 · Los casos en que NO se emite nada
# ---------------------------------------------------------------------------

def test_entregar_sin_cobrar_no_factura_nada(jefa, odoo):  # noqa: F811
    """Sin factura de cobro no hay anticipo que netear, así que no se
    emite nada: emitir una factura acá sería una que nadie pidió.

    Y un apunte honesto de este camino: marcar entregada una cotización
    que Odoo todavía tiene en borrador NO valida ninguna salida (no hay
    picking que validar hasta que la orden se confirma, y eso lo hace el
    pago). Cuando el pago llega después, la salida está abierta y el cobro
    cae en anticipo — con la entrega ya marcada, la final nunca sale. El
    detalle le llega al cliente igual por el enlace /f/. Está apuntado en
    docs/PAQUETE-PRODUCCION.md §5: entregar antes de cobrar Y antes de
    confirmar es el único camino que se queda sin factura con renglones."""
    _agregar(jefa, 501)
    r = jefa.post("/venta/pagar", data={"cliente": "Ana"},
                  follow_redirects=False)
    n = int(r.headers["location"].rsplit("/", 1)[1])
    venta_estado.abrir("venta", n, venta_estado.TIPO_PLANTAS, "Génesis")
    entregas.guardar("venta", n, "Calle 50", "Mensajero Juan", "Génesis")
    assert _marcar(n)[0] is None
    venta = ventas.obtener_venta(n)
    assert venta["factura_final_id"] is None
    assert odoo.ordenes[venta["orden_id"]]["invoice_ids"] == []
    assert odoo.facturas == {}
    # El total del trato, intacto: nadie le escribió una factura encima.
    assert venta["total"] == 3.5


def test_una_venta_entregada_de_verdad_antes_de_cobrar_no_cae_en_anticipo(
        jefa, odoo):  # noqa: F811
    """La otra mitad del caso anterior: cuando la salida SÍ está validada
    (la orden ya estaba confirmada), el cobro factura 'delivered' y sale
    con sus renglones — el camino viejo, sin anticipo y sin final. Y
    entonces facturar_entrega no estrena una segunda factura: no queda
    nada por facturar."""
    _agregar(jefa, 501)
    r = jefa.post("/venta/pagar", data={"cliente": "Ana"},
                  follow_redirects=False)
    n = int(r.headers["location"].rsplit("/", 1)[1])
    venta = ventas.obtener_venta(n)
    odoo.ejecutar("sale.order", "action_confirm", [[venta["orden_id"]]])
    ventas.validar_salida(venta["orden_id"])       # la entrega, de verdad
    jefa.post(f"/venta/cobrar/{n}", data={"metodo": "efectivo"})
    venta = ventas.obtener_venta(n)
    assert [l["name"] for l in odoo.facturas[venta["factura_id"]]["lineas"]] \
        == ["ROMERO"]
    assert venta["total"] == 3.5
    # Y la guarda: nada por facturar, ninguna segunda factura.
    assert ventas.facturar_entrega(venta) is None
    assert len(odoo.ordenes[venta["orden_id"]]["invoice_ids"]) == 1
    assert ventas.obtener_venta(n)["factura_final_id"] is None


def test_una_cotizacion_de_servicio_no_se_factura_desde_la_app(odoo):  # noqa: F811
    """Un servicio se factura en Odoo, no acá: su tabla local ni guarda
    factura, y `_facturar_orden` escribe en ventas_locales (que es de otra
    lista). «Marcar entregada» de un servicio valida la salida y nada
    más."""
    seguridad.crear_empleada("sam", "Sam", "clave")
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    datos_roles.poner_persona(rol["n"], "sam", "prueba")
    from app import cotizaciones
    orden_id = odoo.ejecutar("sale.order", "create", [{
        "partner_id": 74,
        "order_line": [[0, 0, {"product_id": 501, "product_uom_qty": 1}]]}])
    odoo.ejecutar("sale.order", "action_confirm", [[orden_id]])
    n = cotizaciones._guardar_local(
        {"usuario": "sam", "nombre": "Sam"}, "evento", "Ana", "",
        orden_id, "S99", 100.0)["n"]
    entregas.guardar("servicio", n, "Calle 50", "Mensajero", "Sam")
    error, fila = entregas.marcar_entregada("servicio", n, "sam", "Sam")
    assert error is None and fila["entrega_marcada"] == 1
    # La salida sí se validó (eso es stock, y es de siempre) …
    assert not ventas._salidas_pendientes(orden_id)
    # … y ninguna factura se emitió.
    assert odoo.facturas == {}


# ---------------------------------------------------------------------------
# 5 · El renglón fantasma del anticipo, fuera del documento público
# ---------------------------------------------------------------------------

def test_el_documento_publico_no_muestra_el_renglon_del_anticipo(jefa, odoo):  # noqa: F811
    """Al cobrar por anticipo, Odoo cuelga de la ORDEN una línea de
    anticipo con cantidad 0 que NO es display_type, así que pasaba el
    filtro de /f/ y el cliente veía «Anticipo · 0 × $3.50 · $0.00».
    `is_downpayment` la deja fuera."""
    n = _cobrar(jefa)
    venta = ventas.obtener_venta(n)
    # La línea existe en la orden (es Odoo quien la pone) …
    lineas_orden = odoo.ordenes[venta["orden_id"]]["lineas"]
    assert any(l.get("is_downpayment") for l in lineas_orden)
    # … y NO llega al documento del cliente.
    nombres = [l["nombre"] for l in ventas.lineas_de_cotizacion(venta)]
    assert nombres == ["ROMERO"]
    jefa.get("/venta")                 # genera el token del enlace público
    token = ventas.obtener_venta(n)["token"]
    jefa.cookies.clear()               # el cliente final no tiene sesión
    documento = jefa.get(f"/f/{token}")
    assert documento.status_code == 200
    assert "ROMERO" in documento.text
    assert "Anticipo" not in documento.text
    # El documento público sigue mostrando el total del trato, no el $0 de
    # la factura final.
    assert "$3.50" in documento.text


def test_el_dominio_de_la_consulta_pide_is_downpayment_falso(jefa, odoo):  # noqa: F811
    """La consulta misma, porque es UNA línea fácil de perder en un
    merge: si el filtro se cae, el renglón fantasma vuelve."""
    vistas = []
    original = ventas._ejecutar

    def espia(modelo, metodo, args, kw=None):
        if modelo == "sale.order.line":
            vistas.append(args[0])
        return original(modelo, metodo, args, kw)

    n = _cobrar(jefa)
    ventas._ejecutar = espia
    try:
        ventas.lineas_de_cotizacion(ventas.obtener_venta(n))
    finally:
        ventas._ejecutar = original
    assert ["is_downpayment", "=", False] in vistas[0]


# ---------------------------------------------------------------------------
# 6 · Qué factura se le MANDA al cliente (decisión de redacción (b))
# ---------------------------------------------------------------------------

def test_el_pdf_compartible_apunta_a_la_que_trae_los_renglones(jefa, odoo):  # noqa: F811
    """El botón dice «Compartir factura»: no puede entregarle al cliente
    el anticipo de una sola línea. Mientras no haya entrega marcada baja
    la del cobro, como siempre; marcada la entrega, baja la final."""
    pedidos = []
    import app.main as main

    def falso_pdf(reporte, res_id, nombre):
        pedidos.append((reporte, res_id, nombre))
        from starlette.responses import Response
        return Response(b"%PDF-falso", media_type="application/pdf")

    n = _cobrar(jefa)
    venta = ventas.obtener_venta(n)
    original = main._respuesta_pdf
    main._respuesta_pdf = falso_pdf
    try:
        jefa.get(f"/venta/{n}/factura.pdf")
        assert pedidos[-1][1] == venta["factura_id"]
        _entregable(n)
        _marcar(n)
        final_id = ventas.obtener_venta(n)["factura_final_id"]
        jefa.get(f"/venta/{n}/factura.pdf")
        assert pedidos[-1][1] == final_id
        # El nombre del archivo lleva el número de ESA factura.
        assert odoo.facturas[final_id]["name"].replace("/", "-") in pedidos[-1][2]
    finally:
        main._respuesta_pdf = original
    # Y el nombre que la lista de Vender le pone al enlace, también: si
    # quedara el del cobro, el archivo que el cliente recibe diría un
    # número de factura y traería otra.
    esperado = ventas.nombre_de_pdf(
        odoo.facturas[final_id]["name"].replace("/", "-"), "María")
    assert f'download="{esperado}"' in jefa.get("/venta").text


def test_mandar_factura_sigue_mandando_el_enlace_publico(jefa, odoo):  # noqa: F811
    """«Mandar factura» no cambia: manda el /f/, que es el documento con
    el detalle Y el total del trato. Nombra la factura del COBRO, que es
    la que tiene la plata — igual que el total que muestra."""
    n = _cobrar(jefa)
    _entregable(n)
    _marcar(n)
    pagina = jefa.get("/venta")
    token = ventas.obtener_venta(n)["token"]
    assert "Mandar factura" in pagina.text
    assert f"/f/{token}" in pagina.text
    jefa.cookies.clear()
    documento = jefa.get(f"/f/{token}")
    assert ventas.obtener_venta(n)["factura"] in documento.text
    assert "$3.50" in documento.text
