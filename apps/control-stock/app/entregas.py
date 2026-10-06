"""La entrega es una obligación NOMBRADA (item 7 de Jay, 5/10/2026).

Del decision record (§Delivery is a named obligation): un evento o una
entrega tiene una DIRECCIÓN y un ASIGNADO puesto durante la venta —
empleada activa del login o texto libre (mensajero, tercero, contratista).
Ese asignado es el responsable. El system manager la marca entregada.

Reglas:

- La dirección y el asignado son EDITABLES hasta que la venta cierra
  (estado 3); después quedan como historia.
- Todo cambio deja su fila en `entrega_cambio`: qué campo, qué decía,
  qué dice, quién y cuándo.
- «Marcar entregada» EXIGE que haya asignado — y si falta, lo dice con
  un aviso visible, nunca un bloqueo mudo.
- El candado de QUIÉN marca vive en venta_estado.marcar_entregada (el
  system manager, por deber); aquí se orquesta: primero el candado y el
  asignado, después los dos pasos en Odoo —la salida (validar_salida: es
  ESTE el momento en que Odoo valida, no antes, porque el pago nunca la
  tocó) y la factura de la entrega (ventas.facturar_entrega: la que trae
  los renglones, con el anticipo del cobro descontado)— y al final el
  hecho local con su fecha REAL de entrega. Los dos pasos de Odoo son
  idempotentes y el hecho local va último: ver marcar_entregada().
- La recogida (plantas que vuelven, alquiler) NO es un paso de la venta
  de plantas y no vive aquí.
- **«Marcar entregada» NO escribe en Linear ni en Twenty** (régimen del
  BLOQUE 12: hacia los sistemas reales, solo lectura). El embudo del
  lead (pago → Por agendar, «Hecha» → Entregado, Ganado = entregado +
  saldo 0) sigue moviéndose por sus caminos de siempre; esta máquina de
  estados es PARALELA (ver venta_estado.py). Las únicas escrituras
  externas son en Odoo —la salida de stock y la factura de la entrega—,
  que son stock y dinero: el trabajo de Odoo. La reconciliación
  estado↔embudo es pregunta abierta para Jay.
"""

from .datos import _db, ahora_iso
from . import venta_estado, ventas

CAMPOS = ("direccion", "asignado")
LARGO_CAMPO = 200


def iniciar_tablas():
    with _db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS entrega_obligacion (
            origen TEXT NOT NULL,
            venta INTEGER NOT NULL,
            direccion TEXT NOT NULL DEFAULT '',
            asignado TEXT NOT NULL DEFAULT '',
            actualizado_en TEXT,
            PRIMARY KEY (origen, venta)
        );
        -- Historial de la obligación: qué cambió, quién, cuándo.
        CREATE TABLE IF NOT EXISTS entrega_cambio (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            origen TEXT NOT NULL,
            venta INTEGER NOT NULL,
            campo TEXT NOT NULL,
            antes TEXT NOT NULL DEFAULT '',
            despues TEXT NOT NULL DEFAULT '',
            por TEXT NOT NULL DEFAULT '',
            en TEXT NOT NULL
        );
        """)


def obligacion_de(origen, venta):
    with _db() as con:
        fila = con.execute(
            "SELECT * FROM entrega_obligacion WHERE origen=? AND venta=?",
            (origen, int(venta))).fetchone()
    if fila is None:
        return {"origen": origen, "venta": int(venta),
                "direccion": "", "asignado": "", "actualizado_en": None}
    return dict(fila)


def historial_de(origen, venta):
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM entrega_cambio WHERE origen=? AND venta=?"
            " ORDER BY n", (origen, int(venta))).fetchall()
    return [dict(f) for f in filas]


def guardar(origen, venta, direccion, asignado, por):
    """Guarda la dirección y el asignado, anotando en el historial SOLO
    lo que de verdad cambió. Una venta cerrada (estado 3) ya no se
    edita: devuelve 'cerrada'. None si quedó."""
    if venta_estado.estado_de(origen, venta)["estado"] == 3:
        return "cerrada"
    nuevos = {"direccion": (direccion or "").strip()[:LARGO_CAMPO],
              "asignado": (asignado or "").strip()[:LARGO_CAMPO]}
    actual = obligacion_de(origen, venta)
    ahora = ahora_iso()
    with _db() as con:
        con.execute(
            "INSERT INTO entrega_obligacion (origen, venta, direccion,"
            " asignado, actualizado_en) VALUES (?,?,?,?,?)"
            " ON CONFLICT (origen, venta) DO UPDATE SET direccion=?,"
            " asignado=?, actualizado_en=?",
            (origen, int(venta), nuevos["direccion"], nuevos["asignado"],
             ahora, nuevos["direccion"], nuevos["asignado"], ahora))
        for campo in CAMPOS:
            if (actual.get(campo) or "") != nuevos[campo]:
                con.execute(
                    "INSERT INTO entrega_cambio (origen, venta, campo,"
                    " antes, despues, por, en) VALUES (?,?,?,?,?,?,?)",
                    (origen, int(venta), campo, actual.get(campo) or "",
                     nuevos[campo], por, ahora))
    return None


def _registro_de(origen, venta):
    """El registro local de esta venta (ventas_locales o
    cotizaciones_servicio), o None."""
    if origen == "venta":
        return ventas.obtener_venta(venta)
    from . import cotizaciones  # diferido: cotizaciones importa ventas
    return cotizaciones.obtener(venta)


def _orden_id_de(origen, venta):
    """El sale.order de Odoo detrás de esta venta local, o None."""
    return (_registro_de(origen, venta) or {}).get("orden_id")


def marcar_entregada(origen, venta, por_usuario, por_nombre=None, fecha=None):
    """«Marcar entregada», completo: candado del system manager, el
    asignado obligatorio (con aviso, no mudo), los DOS pasos en Odoo —la
    salida validada AHÍ (y no antes: el pago nunca la tocó) y la factura
    de la entrega, con los renglones y el anticipo descontado— y al final
    el hecho local con su fecha REAL. Devuelve (error, fila de
    venta_estado); errores: 'solo_system_manager' · 'falta_asignado' ·
    'odoo' · 'odoo_factura'.

    **La atomicidad, que es el bulto de F3.** El contrato de siempre era
    «si Odoo no acepta la salida, NADA quedó marcado». Con un SEGUNDO
    paso en Odoo aparece un hueco nuevo —salida validada y factura
    fallida— y ahí no se puede prometer lo mismo: la salida ya se escribió
    y deshacerla no es una opción. Se cierra como lo cierra `registrar_pago`
    (ventas.py): **por pasos sellados, cada uno idempotente, con el sello
    donde el paso escribió**, así que volver a tocar el botón retoma desde
    el paso que faltó sin repetir el anterior.

    - Paso 1, la salida: su sello es el estado del picking en Odoo
      (`_salidas_pendientes` viene vacío cuando ya está validada, y
      `validar_salida` no escribe nada).
    - Paso 2, la factura: su sello es la factura misma en Odoo (y
      `factura_final_id` en la base); un borrador que quedó sin publicar
      se publica en la pasada siguiente.
    - El hecho local va AL FINAL, y solo si los dos pasos salieron. Un
      intento que validó la salida pero no logró facturar deja
      `entrega_marcada` en 0 a propósito: la entrega no está cerrada
      mientras falte la factura, y el aviso dice exactamente dónde se
      quedó (en 'odoo_factura' la salida SÍ quedó validada — decirle al
      empleado «nada quedó marcado» ahí sería mentirle).

    El segundo toque del botón, cuando el primero sí terminó, no vuelve a
    facturar: ese POST ya no entra aquí — `main.py` lo trata como
    corrección de la fecha."""
    if not venta_estado.es_system_manager(por_usuario):
        return "solo_system_manager", venta_estado.estado_de(origen, venta)
    if not obligacion_de(origen, venta)["asignado"]:
        return "falta_asignado", venta_estado.estado_de(origen, venta)
    registro = _registro_de(origen, venta)
    orden_id = (registro or {}).get("orden_id")
    monto = None
    if orden_id and ventas.configurado():
        try:
            ventas.validar_salida(orden_id)
        except Exception as error:
            # Odoo dijo que no: NO se marca nada local — el hecho debe
            # reflejar la realidad, no el deseo. El error llega visible.
            return "odoo", {"detalle": ventas._mensaje_de_error(error),
                            **venta_estado.estado_de(origen, venta)}
        # La factura de la entrega, solo para las ventas de plantas: una
        # cotización de servicio no se factura desde la app (eso sigue en
        # Odoo) y su tabla local no guarda factura.
        if origen == "venta":
            try:
                ventas.facturar_entrega(registro)
            except Exception as error:
                return "odoo_factura", {
                    "detalle": ventas._mensaje_de_error(error),
                    **venta_estado.estado_de(origen, venta)}
        monto = ventas.monto_sin_impuesto(orden_id)
    return venta_estado.marcar_entregada(
        origen, venta, por_usuario, por_nombre, fecha=fecha, monto=monto)
