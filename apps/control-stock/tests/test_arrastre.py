"""El arrastre como GESTO DE NAVEGACIÓN en Vender y Pedidos (BLOQUE 40).

LA REGLA DE ORO que estas pruebas clavan: SOLTAR NUNCA ESCRIBE. El drop
solo navega a la pantalla que ya existe (el panel de cobro de Vender,
el editor de fecha de la ficha en Pedidos); no hay POST nuevo, no hay
fetch, no hay estado en el cliente. Qué tarjeta se arrastra, a qué
columnas se puede soltar y a qué URL navega el drop lo decide PYTHON
(main._arrastre_vender, pedidos.gesto_arrastre) y viaja en
data-atributos — arrastre.js solo ejecuta el gesto.

El retroceso (Pagado/Confirmado → Cotizado) no tiene destino en el
cliente (no-drop), pero el candado REAL es el del motor que ya existía:
venta_estado.bloqueo_manual detrás de POST /venta/estado — acá se clava
con una request directa armada, desde el ángulo del drag.
"""

import re
from datetime import datetime

from app import (crm_leads, datos, entregas, main, pedidos, venta_estado,
                 ventas)
from app.datos import ZONA_PANAMA


def _venta_local(orden="S00081", orden_id=81, cliente="Ana",
                 estado="cotizacion", total=18.0):
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " orden_id, orden, total, estado) VALUES (?,?,?,?,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", cliente,
             orden_id, orden, total, estado))
        return cursor.lastrowid


def _servicio_local(orden="S00090", orden_id=90, cliente="Hotel Sol",
                    total=310.0, tipo="evento"):
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total)"
            " VALUES (?,?,?,?,NULL,?,?,?)",
            (datetime.now(ZONA_PANAMA).isoformat(), "Génesis", tipo,
             cliente, orden_id, orden, total))
        return cursor.lastrowid


def _plata_vacia(monkeypatch):
    pedidos.reiniciar_cache_plata()
    monkeypatch.setattr(pedidos, "_informe",
                        lambda: {"ventas": [], "huecos": []})


# ---------------------------------------------------------------------------
# Vender: los destinos por estado los decide Python (unit)
# ---------------------------------------------------------------------------

def test_destinos_de_vender_los_decide_python():
    """La tabla completa del gesto: cotización → Confirmado o Pagado
    (los dos navegan al panel de cobro); confirmada → solo Pagado;
    pagada y servicio no se arrastran. Hacia atrás jamás hay destino."""
    cotizacion = main._arrastre_vender(
        {"tipo": "venta", "estado": "cotizacion", "n": 5})
    assert cotizacion == {"url": "/venta/pago/5",
                          "destinos": ["confirmado", "pagado"]}
    for estado in ("vendida", "facturada", "entregada"):
        gesto = main._arrastre_vender(
            {"tipo": "venta", "estado": estado, "n": 7})
        assert gesto == {"url": "/venta/pago/7", "destinos": ["pagado"]}
    assert main._arrastre_vender(
        {"tipo": "venta", "estado": "pagado", "n": 9}) is None
    # Un servicio no se arrastra: su cobro vive en el kanban de Odoo.
    assert main._arrastre_vender(
        {"tipo": "servicio", "facturada": False, "n": 3}) is None
    assert main._arrastre_vender(
        {"tipo": "servicio", "facturada": True, "n": 3}) is None


def test_ningun_destino_de_vender_mira_hacia_atras():
    """Por construcción: los destinos de cada tarjeta quedan SIEMPRE
    adelante de su columna — un drop hacia atrás no existe en el dato
    que Python manda a la plantilla."""
    orden_columnas = [clave for clave, _t, _p in main.COLUMNAS_VENDER]
    for estado in ("cotizacion", "vendida", "facturada", "pagado"):
        fila = {"tipo": "venta", "estado": estado, "n": 1}
        gesto = main._arrastre_vender(fila)
        if gesto is None:
            continue
        propia = orden_columnas.index(main._columna_vender(fila))
        for destino in gesto["destinos"]:
            assert orden_columnas.index(destino) > propia


def test_mover_a_de_vender_no_repite_la_puerta_del_boton_negro():
    """El «Mover a» del celular es el mismo gesto, pero NO duplica lo
    que el panel ya ofrece. En una cotización el botón negro ES
    «Facturar / Pagado» → el mismo /venta/pago/<n> del arrastre, así
    que el bloque queda vacío: una sola puerta, no tres."""
    fila = {"tipo": "venta", "estado": "cotizacion", "n": 5}
    fila["arrastre"] = main._arrastre_vender(fila)
    panel = {"boton": {"texto": "Facturar / Pagado", "href": "/venta/pago/5"}}
    assert main._mover_a_vender(fila, panel) == []
    assert main._mover_a_vender({"tipo": "venta", "estado": "pagado",
                                 "n": 9, "arrastre": None}, panel) == []


def test_mover_a_aparece_donde_el_panel_no_tiene_puerta_al_cobro():
    """En una confirmada el botón negro es el PDF y el panel no lleva
    al cobro por ningún lado: ahí «Mover a → Pagado» es el ÚNICO camino
    del celular, donde no hay arrastre. Eso es lo que el bloque aporta."""
    fila = {"tipo": "venta", "estado": "vendida", "n": 7}
    fila["arrastre"] = main._arrastre_vender(fila)
    panel = {"boton": {"texto": "Descargar / Compartir PDF",
                       "href": "/venta/7/cotizacion.pdf"}}
    assert main._mover_a_vender(fila, panel) == [
        {"texto": "Pagado", "href": "/venta/pago/7"}]


# ---------------------------------------------------------------------------
# Vender por HTTP: los data-atributos en la plantilla
# ---------------------------------------------------------------------------

def test_tablero_de_vender_lleva_los_data_atributos(cliente):
    n = _venta_local()                                   # cotización
    _venta_local(orden="S00082", orden_id=82, cliente="Pagada",
                 estado="pagado")
    _servicio_local()
    pagina = cliente.get("/venta").text
    # Las columnas son zonas de drop, con su clave.
    for clave in ("cotizado", "confirmado", "pagado"):
        assert f'data-arrastre-destino="{clave}"' in pagina
    # La cotización se arrastra, con la URL y los destinos de Python.
    assert f'data-arrastre-url="/venta/pago/{n}"' in pagina
    assert 'data-arrastre-destinos="confirmado pagado"' in pagina
    # La pagada y el servicio NO: una sola tarjeta arrastrable.
    assert pagina.count("data-arrastre-url=") == 1
    # La trampa del 29/09: el <a> interno no se arrastra.
    assert 'class="vd-abrir" draggable="false"' in pagina
    assert "/static/arrastre.js" in pagina


def test_una_confirmada_solo_ofrece_pagado(cliente):
    _venta_local(estado="vendida")
    pagina = cliente.get("/venta").text
    assert 'data-arrastre-destinos="pagado"' in pagina
    assert 'data-arrastre-destinos="confirmado pagado"' not in pagina


def test_panel_de_confirmada_trae_mover_a_decidido_en_servidor(cliente):
    """La confirmada es el caso que lo necesita: su botón negro es el
    PDF, así que el único enlace al cobro de toda la pantalla es el
    «Mover a → Pagado» que armó Python."""
    n = _venta_local(estado="vendida")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert "Mover a" in pagina
    assert pagina.count(f'class="mover-a-ln" href="/venta/pago/{n}"') == 1
    assert "Pagado →" in pagina


def test_panel_de_cotizacion_no_repite_el_boton_negro(cliente):
    """La cotización ya tiene su puerta («Facturar / Pagado»): el panel
    no estrena un «Mover a» que lleve al mismo lado, y en toda la
    pantalla sigue habiendo UN solo enlace a /venta/pago/<n>."""
    n = _venta_local()
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert 'class="mover-a"' not in pagina
    assert pagina.count(f'href="/venta/pago/{n}"') == 1


def test_panel_de_pagada_no_ofrece_mover_a(cliente):
    n = _venta_local(estado="pagado")
    pagina = cliente.get(f"/venta?abrir=v{n}").text
    assert 'class="mover-a"' not in pagina


# ---------------------------------------------------------------------------
# El drop no escribe: su destino es un GET que solo pinta, y el
# retroceso lo corta el candado EXISTENTE del motor (ángulo del drag)
# ---------------------------------------------------------------------------

def test_el_destino_del_drop_no_escribe_nada(cliente):
    """Soltar navega a GET /venta/pago/<n> (el panel de cobro). Abrirlo
    y cerrarlo sin pagar no mueve NADA: ni el estado local de la venta
    ni el motor de los 3 estados — no hay nada que revertir."""
    n = _venta_local()
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    respuesta = cliente.get(f"/venta/pago/{n}")
    assert respuesta.status_code == 200
    assert ventas.obtener_venta(n)["estado"] == "cotizacion"
    assert venta_estado.estado_de("venta", n)["estado"] == 1


def test_un_post_armado_de_reversa_lo_corta_el_candado_del_motor(cliente):
    """El no-drop del cliente es cortesía: si alguien ARMA el POST que
    el drag jamás manda (bajar 2 → 1 sin ser system manager), el candado
    que ya existía (venta_estado.bloqueo_manual detrás de
    POST /venta/estado) lo rechaza y el estado no se mueve."""
    n = _venta_local()
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    venta_estado.registrar_pago("venta", n, "Génesis")
    assert venta_estado.estado_de("venta", n)["estado"] == 2
    respuesta = cliente.post(f"/venta/estado/venta/{n}",
                             data={"estado": "1"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=solo_system_manager" in respuesta.headers["location"]
    assert venta_estado.estado_de("venta", n)["estado"] == 2


def test_un_post_armado_hacia_pagado_sin_pago_tambien_se_corta(cliente):
    """El otro POST que un drag manoseado podría armar: subir a 2 sin
    pago registrado. El mismo candado lo rechaza — el gesto navega al
    panel de cobro justamente porque el chip no escribe plata."""
    n = _venta_local()
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    respuesta = cliente.post(f"/venta/estado/venta/{n}",
                             data={"estado": "2"}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=falta_pago" in respuesta.headers["location"]
    assert venta_estado.estado_de("venta", n)["estado"] == 1


# ---------------------------------------------------------------------------
# Pedidos: la única movida es Por programar → Programado, y navega al
# editor de fecha
# ---------------------------------------------------------------------------

def test_gesto_de_pedidos_lo_decide_python():
    gesto = pedidos.gesto_arrastre("por_programar", "/venta/estado/venta/4")
    assert gesto == {"url": "/venta/estado/venta/4#vt-fecha-prog",
                     "destinos": ["programado"]}
    # Programado no se arrastra (volver atrás es borrar la fecha en la
    # ficha) y Entregado reciente ni arrastra ni es destino.
    assert pedidos.gesto_arrastre("programado", "/x") is None
    assert pedidos.gesto_arrastre("entregado", "/x") is None


def test_tablero_de_pedidos_lleva_los_data_atributos(cliente, monkeypatch):
    _plata_vacia(monkeypatch)
    n = _venta_local(estado="pagado")
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    venta_estado.registrar_pago("venta", n, "Génesis")   # → Por programar
    n2 = _venta_local(orden="S00082", orden_id=82, cliente="ConFecha",
                      estado="pagado")
    venta_estado.abrir("venta", n2, "plant retail", "Génesis")
    venta_estado.registrar_pago("venta", n2, "Génesis")
    entregas.guardar("venta", n2, "Calle 50", "Sam", "Génesis",
                     fecha_programada="2026-12-01")      # → Programado
    pagina = cliente.get("/pedidos").text
    for clave in ("por_programar", "programado", "entregado"):
        assert f'data-arrastre-destino="{clave}"' in pagina
    # Solo la de «Por programar» se arrastra, y su drop navega DERECHO
    # al editor de fecha de la ficha existente.
    assert (f'data-arrastre-url="/venta/estado/venta/{n}#vt-fecha-prog"'
            in pagina)
    assert 'data-arrastre-destinos="programado"' in pagina
    assert pagina.count("data-arrastre-url=") == 1
    # El gesto del celular: Mover a → Programado, mismo destino.
    assert "Mover a" in pagina and "Programado →" in pagina
    assert (f'class="mover-a-ln" href="/venta/estado/venta/{n}'
            '#vt-fecha-prog"') in pagina
    # La trampa del 29/09 en los <a> internos de la tarjeta.
    assert 'class="pd-abrir" draggable="false"' in pagina
    assert 'class="pd-chip" draggable="false"' in pagina
    assert "/static/arrastre.js" in pagina


def test_entregado_reciente_jamas_es_destino_de_una_tarjeta(cliente,
                                                            monkeypatch):
    """La columna lleva su data-arrastre-destino (para pintarse gris
    durante un drag), pero NINGUNA tarjeta la lista en sus destinos:
    el dato que decide es el de la tarjeta, y lo arma Python. Se miran
    TODAS las tarjetas de la pantalla, no la primera."""
    _plata_vacia(monkeypatch)
    n = _venta_local(estado="pagado")
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    venta_estado.registrar_pago("venta", n, "Génesis")
    n2 = _venta_local(orden="S00082", orden_id=82, cliente="Entregada",
                      estado="pagado")
    venta_estado.abrir("venta", n2, "plant retail", "Génesis")
    venta_estado.registrar_pago("venta", n2, "Génesis")
    entregas.guardar("venta", n2, "Calle 50", "Sam", "Génesis",
                     fecha_programada="2026-12-01")
    pagina = cliente.get("/pedidos").text
    destinos = re.findall(r'data-arrastre-destinos="([^"]*)"', pagina)
    assert destinos                      # si no hay nada, no probó nada
    for valor in destinos:
        assert "entregado" not in valor.split()


def test_el_drop_de_pedidos_cae_en_un_editor_de_fecha_que_existe(
        cliente, monkeypatch):
    """El ancla del drop no puede ser una promesa: se sigue la URL que
    Python puso en la tarjeta y se comprueba que al otro lado está de
    verdad el input `fecha_programada` con ese id. Si alguien le cambia
    el id al campo de la ficha, el gesto caería en una página sin ancla
    y nadie se enteraría."""
    _plata_vacia(monkeypatch)
    n = _venta_local(estado="pagado")
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    venta_estado.registrar_pago("venta", n, "Génesis")
    gesto = pedidos.gesto_arrastre("por_programar", f"/venta/estado/venta/{n}")
    ruta, _, ancla = gesto["url"].partition("#")
    destino = cliente.get(ruta)
    assert destino.status_code == 200
    assert f'id="{ancla}"' in destino.text
    assert 'name="fecha_programada"' in destino.text


# ---------------------------------------------------------------------------
# Punto 6 del bloque: el tablero lee el MISMO motor que Vender — mover
# allá se refleja acá sin ningún espejo de por medio
# ---------------------------------------------------------------------------

def test_pedidos_lee_el_mismo_motor_que_vender_sin_espejo(cliente,
                                                          monkeypatch):
    """Registrar el pago en Vender (ventas.registrar_pago llama a
    venta_estado.registrar_pago — el MISMO motor) hace aparecer la
    tarjeta en Pedidos sin que ningún espejo/puente viaje: acá el espejo
    del CRM está parcheado para REVENTAR si alguien lo llama, y la
    pestaña igual refleja el movimiento, porque los dos lados leen
    venta_estado. (El tablero de LEADS de /control es una máquina
    PARALELA a propósito — frontera del Arquitecto, 5/10, en el
    docstring de venta_estado.py — y conserva su propio arrastre.)"""
    def revienta(*_args, **_kw):
        raise AssertionError("el reflejo no puede depender del espejo")

    monkeypatch.setattr(crm_leads, "espejar_venta", revienta)
    _plata_vacia(monkeypatch)
    n = _venta_local(cliente="SinEspejo", estado="pagado")
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    antes = cliente.get("/pedidos").text
    assert "SinEspejo" not in antes                 # estado 1: no es pedido
    venta_estado.registrar_pago("venta", n, "Génesis")  # el paso de Vender
    despues = cliente.get("/pedidos").text
    assert "SinEspejo" in despues                   # reflejado, sin espejo
