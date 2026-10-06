"""Las dos cifras, a propósito (items 5-7 de Jay; las consume el item 10).

Del decision record (§Two numbers, on purpose):

- **pending_revenue()** — facturado o depositado, TODAVÍA SIN ENTREGAR.
  El depósito de un evento vive aquí: existe para que un depósito no se
  celebre como trabajo terminado. No es un asiento contable.
- **delivered_revenue(desde, hasta)** — por FECHA DE ENTREGA. Esa es la
  fecha de venta del top line cerrado; la fecha de la factura es LA
  FECHA EQUIVOCADA para este número y no se usa jamás.

**La fuente de la fecha de entrega (revisión del Arquitecto, 5/10): la
fija «Marcar entregada»** — una fecha de calendario (AAAA-MM-DD) que
elige el system manager en el acto de marcar, y que él mismo puede
CORREGIR después si la entrega real fue otro día
(venta_estado.corregir_fecha_entrega, con su fila en el historial). Si
existe una actividad del calendario ligada al trabajo, esa es una
REFERENCIA para la persona; la cifra usa siempre la fecha del acto de
marcar (venta_estado.fecha_entrega), nunca la de la actividad ni la de
la factura. Al ser una fecha elegida por una persona —no un reloj— no
hay zonas que comparar; los INSTANTES de los hechos sí van en epoch
(ver venta_estado.py).

**Montos sin ITBMS**, cuando el que registró el hecho lo supo
(amount_untaxed de Odoo en los flujos; el monto confirmado en la cola).
**Todo rotulado «provisional»** (ETIQUETA): sale de los hechos locales
registrados desde el 5/10/2026 — las ventas de antes no tienen hechos y
no están aquí; la foto completa contra Odoo sigue siendo /revisar.

SOLO funciones, sin pantalla: el item 10 (leads to close / top line)
las consumirá. Leen el SQLite local y nada más — ni Odoo, ni Linear.
"""

from .datos import _db

ETIQUETA = "provisional — hechos locales desde el 5/10/2026, montos sin ITBMS"


def _como_fecha(valor):
    """'AAAA-MM-DD' desde un str o date. Las dos cifras comparan FECHAS
    de calendario (texto ISO ordena igual que la fecha), nunca relojes."""
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat()[:10]
    return str(valor)[:10]


def pending_revenue():
    """Plata confirmada en ventas AÚN SIN ENTREGAR: la suma de los
    montos del pago (pago_monto — que ya es el ACUMULADO de los hechos,
    depósito + saldo: venta_estado.registrar_pago lo suma) donde
    pago_confirmado=1 y entrega_marcada=0. {'monto', 'n', 'sin_monto', 'rotulo'} —
    `sin_monto` cuenta los hechos sin monto conocido: esos NO suman cero
    en silencio, se dicen."""
    with _db() as con:
        filas = con.execute(
            "SELECT pago_monto FROM venta_estado"
            " WHERE pago_confirmado=1 AND entrega_marcada=0").fetchall()
    montos = [f["pago_monto"] for f in filas]
    return {
        "monto": round(sum(m for m in montos if m is not None), 2),
        "n": len(montos),
        "sin_monto": sum(1 for m in montos if m is None),
        "rotulo": ETIQUETA,
    }


def delivered_revenue(desde, hasta):
    """El top line cerrado del rango, POR FECHA DE ENTREGA (inclusive
    ambos lados): ventas con entrega_marcada=1 y fecha_entrega dentro de
    [desde, hasta]. El monto de cada una es el del hecho de la entrega
    (entrega_monto, sin ITBMS) o, si no se supo entonces, el del pago.
    `desde`/`hasta` aceptan date o 'AAAA-MM-DD'."""
    desde, hasta = _como_fecha(desde), _como_fecha(hasta)
    with _db() as con:
        filas = con.execute(
            "SELECT entrega_monto, pago_monto FROM venta_estado"
            " WHERE entrega_marcada=1 AND fecha_entrega IS NOT NULL"
            " AND fecha_entrega >= ? AND fecha_entrega <= ?",
            (desde, hasta)).fetchall()
    montos = [(f["entrega_monto"] if f["entrega_monto"] is not None
               else f["pago_monto"]) for f in filas]
    return {
        "monto": round(sum(m for m in montos if m is not None), 2),
        "n": len(montos),
        "sin_monto": sum(1 for m in montos if m is None),
        "rotulo": ETIQUETA,
    }
