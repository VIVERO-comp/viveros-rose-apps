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
  asignado, después la salida en Odoo (validar_salida — es ESTE el
  momento en que Odoo valida, no antes: el pago nunca la tocó), y al
  final el hecho local con su fecha REAL de entrega.
- La recogida (plantas que vuelven, alquiler) NO es un paso de la venta
  de plantas y no vive aquí.
- **«Marcar entregada» NO escribe en Linear ni en Twenty** (régimen del
  BLOQUE 12: hacia los sistemas reales, solo lectura). El embudo del
  lead (pago → Por agendar, «Hecha» → Entregado, Ganado = entregado +
  saldo 0) sigue moviéndose por sus caminos de siempre; esta máquina de
  estados es PARALELA (ver venta_estado.py). La única escritura externa
  es la salida de Odoo (validar_salida), que es stock — el trabajo de
  Odoo. La reconciliación estado↔embudo es pregunta abierta para Jay.
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


def _orden_id_de(origen, venta):
    """El sale.order de Odoo detrás de esta venta local, o None."""
    if origen == "venta":
        registro = ventas.obtener_venta(venta)
    else:
        from . import cotizaciones  # diferido: cotizaciones importa ventas
        registro = cotizaciones.obtener(venta)
    return (registro or {}).get("orden_id")


def marcar_entregada(origen, venta, por_usuario, por_nombre=None, fecha=None):
    """«Marcar entregada», completo: candado del system manager, el
    asignado obligatorio (con aviso, no mudo), la salida validada en
    Odoo AHÍ (y no antes — el pago nunca la tocó) y el hecho local con
    su fecha REAL. Devuelve (error, fila de venta_estado); errores:
    'solo_system_manager' · 'falta_asignado' · 'odoo'."""
    if not venta_estado.es_system_manager(por_usuario):
        return "solo_system_manager", venta_estado.estado_de(origen, venta)
    if not obligacion_de(origen, venta)["asignado"]:
        return "falta_asignado", venta_estado.estado_de(origen, venta)
    orden_id = _orden_id_de(origen, venta)
    monto = None
    if orden_id and ventas.configurado():
        try:
            ventas.validar_salida(orden_id)
        except Exception as error:
            # Odoo dijo que no: NO se marca nada local — el hecho debe
            # reflejar la realidad, no el deseo. El error llega visible.
            return "odoo", {"detalle": ventas._mensaje_de_error(error),
                            **venta_estado.estado_de(origen, venta)}
        monto = ventas.monto_sin_impuesto(orden_id)
    return venta_estado.marcar_entregada(
        origen, venta, por_usuario, por_nombre, fecha=fecha, monto=monto)
