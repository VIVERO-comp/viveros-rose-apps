"""Pruebas de la pestaña PEDIDOS (punto 4 del BLOQUE 12; diseño con ACK
del Arquitecto, 6/10/2026).

Lo que el ACK exige con prueba: columnas CONGELADAS y excluyentes por
construcción (jamás una tarjeta en dos — con los tres casos frontera),
la ventana de «Entregado reciente» como fechas de calendario de Panamá
configurable, el orden estable, los VR- de la tienda fuera del dominio,
la plata sin inventos con Odoo caído (ni $0 ni silencio: el hueco se
dice, y una lectura vieja muestra su edad), y el rol Inventario cortado
por request directa en las rutas nuevas (el patrón de los 27+).
"""

from datetime import date, timedelta

import pytest

from app import (datos, datos_roles, entregas, pedidos, seguridad,
                 venta_estado)

HOY = date(2026, 10, 6)


@pytest.fixture
def base(db_limpia, monkeypatch):
    """Base limpia + la caché de plata reiniciada + un informe doble
    vacío (cada prueba pone el suyo si le importa la plata)."""
    pedidos.reiniciar_cache_plata()
    monkeypatch.setattr(pedidos, "_informe",
                        lambda: {"ventas": [], "huecos": []})
    return db_limpia


@pytest.fixture
def manager(base):
    seguridad.crear_empleada("sam", "Sam", "clave")
    rol = next(r for r in datos_roles.listar_roles()
               if r["deber"] == "system_manager")
    datos_roles.poner_persona(rol["n"], "sam", "prueba")
    return "sam"


def _venta_local(orden="S00081", orden_id=81, cliente="Ana",
                 creado="2026-10-01T10:00:00", total=18.0):
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " orden_id, orden, total, estado) VALUES (?,?,?,?,?,?,'pagado')",
            (creado, "Génesis", cliente, orden_id, orden, total))
        return cursor.lastrowid


def _servicio_local(orden="S00090", orden_id=90, cliente="Hotel Sol",
                    creado="2026-10-02T10:00:00", total=310.0,
                    tipo="evento"):
    with datos._db() as con:
        cursor = con.execute(
            "INSERT INTO cotizaciones_servicio (creado_en, empleada, tipo,"
            " cliente, celular, orden_id, orden, total)"
            " VALUES (?,?,?,?,NULL,?,?,?)",
            (creado, "Génesis", tipo, cliente, orden_id, orden, total))
        return cursor.lastrowid


def _en_estado_2(n, origen="venta", tipo="plant retail"):
    venta_estado.abrir(origen, n, tipo, "Génesis")
    venta_estado.registrar_pago(origen, n, "Génesis")


def _en_estado_3(n, manager, fecha, origen="venta"):
    _en_estado_2(n, origen)
    entregas.guardar(origen, n, "Calle 50", "Sam", "Génesis")
    error, fila = entregas.marcar_entregada(origen, n, manager, "Sam",
                                            fecha=fecha)
    assert error is None and fila["estado"] == 3


def _donde_cae(origen, n, columnas):
    """Las columnas donde aparece la tarjeta (para clavar «jamás en
    dos» mirando el tablero entero, no solo el criterio)."""
    return [clave for clave, lista in columnas.items()
            if any(t["origen"] == origen and t["n"] == n for t in lista)]


# ---------------------------------------------------------------------------
# El criterio congelado: excluyente por construcción, con los casos
# frontera
# ---------------------------------------------------------------------------

def test_columnas_excluyentes_los_tres_casos_frontera(manager):
    """Los tres casos frontera del criterio congelado, cada uno en UNA
    columna y solo una, mirando el tablero completo."""
    # 1 · estado 2 sin fecha programada → Por programar.
    a = _venta_local(orden="S00081", orden_id=81)
    _en_estado_2(a)
    # 2 · estado 2 con fecha programada → Programado.
    b = _venta_local(orden="S00082", orden_id=82)
    _en_estado_2(b)
    entregas.guardar("venta", b, "", "Sam", "Génesis",
                     fecha_programada="2026-10-15")
    # 3 · estado 3 con fecha_entrega dentro de la ventana → Entregado.
    c = _venta_local(orden="S00083", orden_id=83)
    _en_estado_3(c, manager, HOY.isoformat())
    columnas = pedidos.tarjetas(hoy=HOY)
    assert _donde_cae("venta", a, columnas) == ["por_programar"]
    assert _donde_cae("venta", b, columnas) == ["programado"]
    assert _donde_cae("venta", c, columnas) == ["entregado"]
    # Y NADIE está en dos: la suma de tarjetas = las ventas pintadas.
    todas = [(t["origen"], t["n"])
             for lista in columnas.values() for t in lista]
    assert len(todas) == len(set(todas)) == 3


def test_una_cerrada_con_fecha_programada_no_cae_en_programado(manager):
    """La tarjeta ambigua a propósito: estado 3 que ADEMÁS tiene
    fecha_programada. El if/elif por estado la deja solo en Entregado —
    jamás en dos."""
    n = _venta_local()
    _en_estado_2(n)
    entregas.guardar("venta", n, "", "Sam", "Génesis",
                     fecha_programada=HOY.isoformat())
    _en_estado_3(n, manager, HOY.isoformat())
    assert _donde_cae("venta", n, pedidos.tarjetas(hoy=HOY)) == ["entregado"]


def test_estado_1_no_es_un_pedido(base):
    """Sin plata confirmada no hay pedido (el modelo de Jay: el pedido
    nace cuando el cliente paga o abona)."""
    n = _venta_local()
    venta_estado.abrir("venta", n, "plant retail", "Génesis")
    assert _donde_cae("venta", n, pedidos.tarjetas(hoy=HOY)) == []


def test_la_ventana_de_entregado_es_de_fechas_de_calendario(manager):
    """Frontera de la ventana (default 7): hoy−7 entra, hoy−8 ya no, y
    una fecha corregida al futuro tampoco (fuera de [hoy−7, hoy])."""
    al_borde = _venta_local(orden="S00084", orden_id=84)
    _en_estado_3(al_borde, manager, (HOY - timedelta(days=7)).isoformat())
    pasada = _venta_local(orden="S00085", orden_id=85)
    _en_estado_3(pasada, manager, (HOY - timedelta(days=8)).isoformat())
    futura = _venta_local(orden="S00086", orden_id=86)
    _en_estado_3(futura, manager, (HOY + timedelta(days=1)).isoformat())
    columnas = pedidos.tarjetas(hoy=HOY)
    assert _donde_cae("venta", al_borde, columnas) == ["entregado"]
    assert _donde_cae("venta", pasada, columnas) == []
    assert _donde_cae("venta", futura, columnas) == []


def test_la_ventana_se_configura_sin_desplegar(manager):
    n = _venta_local()
    _en_estado_3(n, manager, (HOY - timedelta(days=10)).isoformat())
    assert _donde_cae("venta", n, pedidos.tarjetas(hoy=HOY)) == []
    datos.fijar_config(pedidos.CLAVE_VENTANA, "14")
    assert pedidos.ventana_dias() == 14
    assert _donde_cae("venta", n, pedidos.tarjetas(hoy=HOY)) == ["entregado"]
    # Una clave rota cae al default, nunca truena.
    datos.fijar_config(pedidos.CLAVE_VENTANA, "catorce")
    assert pedidos.ventana_dias() == pedidos.VENTANA_DEFAULT


# ---------------------------------------------------------------------------
# El dominio: ventas locales de Vender; los VR- de la tienda NO entran
# ---------------------------------------------------------------------------

def test_los_vr_de_la_tienda_no_entran(base):
    n = _venta_local(orden="VR-549312", orden_id=549312)
    _en_estado_2(n)
    columnas = pedidos.tarjetas(hoy=HOY)
    assert _donde_cae("venta", n, columnas) == []
    assert all(not lista for lista in columnas.values())


def test_una_cancelada_no_entra(base):
    n = _venta_local()
    _en_estado_2(n)
    with datos._db() as con:
        con.execute("UPDATE ventas_locales SET estado='cancelada'"
                    " WHERE n=?", (n,))
    assert _donde_cae("venta", n, pedidos.tarjetas(hoy=HOY)) == []


def test_un_servicio_local_tambien_es_tarjeta(base):
    n = _servicio_local()
    _en_estado_2(n, origen="servicio", tipo="rental event")
    columnas = pedidos.tarjetas(hoy=HOY)
    assert _donde_cae("servicio", n, columnas) == ["por_programar"]
    tarjeta = columnas["por_programar"][0]
    assert tarjeta["orden"] == "S00090"     # el identificador visible
    assert tarjeta["href"] == f"/venta/estado/servicio/{n}"


# ---------------------------------------------------------------------------
# Orden estable: fecha programada, luego creación
# ---------------------------------------------------------------------------

def test_orden_estable_fecha_programada_luego_creacion(base):
    tarde = _venta_local(orden="S00087", orden_id=87,
                         creado="2026-10-03T10:00:00")
    temprano = _venta_local(orden="S00088", orden_id=88,
                            creado="2026-10-01T10:00:00")
    cercana = _venta_local(orden="S00089", orden_id=89,
                           creado="2026-10-05T10:00:00")
    for n, fecha in ((tarde, "2026-10-20"), (temprano, "2026-10-20"),
                     (cercana, "2026-10-10")):
        _en_estado_2(n)
        entregas.guardar("venta", n, "", "Sam", "Génesis",
                         fecha_programada=fecha)
    lista = pedidos.tarjetas(hoy=HOY)["programado"]
    assert [t["n"] for t in lista] == [cercana, temprano, tarde]
    # Y es ESTABLE: dos pasadas, el mismo orden.
    assert [t["n"] for t in pedidos.tarjetas(hoy=HOY)["programado"]] \
        == [cercana, temprano, tarde]


# ---------------------------------------------------------------------------
# La plata: el mismo motor de la cola, sin inventos
# ---------------------------------------------------------------------------

def _informe_con(ventas_filas, huecos=()):
    return {"ventas": ventas_filas, "huecos": list(huecos)}


def test_debe_en_rojo_solo_si_debe(base, monkeypatch):
    con_saldo = _venta_local(orden="S00091", orden_id=91, total=500.0)
    _en_estado_2(con_saldo)
    pagada = _venta_local(orden="S00092", orden_id=92)
    _en_estado_2(pagada)
    monkeypatch.setattr(pedidos, "_informe", lambda: _informe_con([
        {"orden_id": 91, "pagado": 190.0, "debe": 310.0, "total": 500.0},
        {"orden_id": 92, "pagado": 18.0, "debe": 0.0, "total": 18.0},
    ]))
    tablero = pedidos.tablero(hoy=HOY)
    por_n = {t["n"]: t for c in tablero["columnas"] for t in c["tarjetas"]}
    assert por_n[con_saldo]["plata"] == {"clase": "debe",
                                         "texto": "Debe $310.00",
                                         "debe": 310.0}
    assert por_n[pagada]["plata"]["clase"] == "pagado"
    assert tablero["aviso_plata"] is None


def test_odoo_caido_sin_lectura_previa_dice_el_hueco(base, monkeypatch):
    """La pantalla VIVE desde el estado local y lo dice: jamás $0 ni
    silencio."""
    n = _venta_local()
    _en_estado_2(n)
    monkeypatch.setattr(pedidos, "_informe", lambda: _informe_con(
        [], huecos=["Odoo no contestó: apagado"]))
    tablero = pedidos.tablero(hoy=HOY)
    tarjeta = tablero["columnas"][0]["tarjetas"][0]
    assert tarjeta["plata"]["clase"] == "sin_dato"
    assert "$0" not in tarjeta["plata"]["texto"]
    assert "Odoo no contestó" in tablero["aviso_plata"]


def test_odoo_caido_sirve_la_ultima_lectura_buena_con_su_edad(
        base, monkeypatch):
    n = _venta_local(orden="S00091", orden_id=91, total=500.0)
    _en_estado_2(n)
    monkeypatch.setattr(pedidos, "_informe", lambda: _informe_con([
        {"orden_id": 91, "pagado": 190.0, "debe": 310.0, "total": 500.0}]))
    pedidos.tablero(hoy=HOY)  # la lectura buena queda en caché
    # Se cae Odoo; la caché tiene 30 minutos.
    monkeypatch.setattr(pedidos, "_informe", lambda: _informe_con(
        [], huecos=["Odoo no contestó: apagado"]))
    pedidos._cache_plata["en"] -= 30 * 60
    tablero = pedidos.tablero(hoy=HOY)
    tarjeta = tablero["columnas"][0]["tarjetas"][0]
    assert tarjeta["plata"]["clase"] == "debe"      # la lectura buena
    assert "hace 30 min" in tablero["aviso_plata"]  # con su edad
    assert "Odoo no contestó" in tablero["aviso_plata"]


def test_el_informe_que_revienta_no_tumba_la_pestana(base, monkeypatch):
    n = _venta_local()
    _en_estado_2(n)

    def revienta():
        raise RuntimeError("se cayó entero")

    monkeypatch.setattr(pedidos, "_informe", revienta)
    tablero = pedidos.tablero(hoy=HOY)
    assert tablero["columnas"][0]["cuenta"] == 1
    assert "se cayó entero" in tablero["aviso_plata"]


# ---------------------------------------------------------------------------
# Filtros por tipo de venta (las capas del item 1)
# ---------------------------------------------------------------------------

def test_filtro_por_tipo_de_venta(base):
    planta = _venta_local(orden="S00093", orden_id=93)
    _en_estado_2(planta)
    evento = _servicio_local(orden="S00094", orden_id=94)
    _en_estado_2(evento, origen="servicio", tipo="rental event")
    tablero = pedidos.tablero(tipo="rental event", hoy=HOY)
    assert tablero["tipo"] == "rental event"
    todas = [(t["origen"], t["n"]) for c in tablero["columnas"]
             for t in c["tarjetas"]]
    assert todas == [("servicio", evento)]
    # Las cuentas de los chips salen del tablero SIN filtrar.
    filtros = {f["nombre"]: f["cuenta"] for f in tablero["filtros"]}
    assert filtros == {"plant retail": 1, "rental event": 1}
    # Un tipo inventado no filtra nada (se ignora, no se truena).
    assert pedidos.tablero(tipo="no-existe", hoy=HOY)["tipo"] is None
