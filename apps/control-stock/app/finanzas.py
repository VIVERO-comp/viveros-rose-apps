"""Finanzas — la pantalla de quien lleva la plata (BLOQUES 20 y 22, esqueleto).

ESQUELETO NAVEGABLE (BLOQUE 25, 6/10/2026): números REALES en solo
lectura; lo que todavía no funciona es un botón APAGADO con «Todavía
no». Regla dura de Abraham: nada que parezca funcionar y no guarde —
acá no hay ni un POST.

Qué es — y qué no:

- **Los cuatro números de arriba** (vendido · cobrado y confirmado ·
  pagos por confirmar · por cobrar) salen de los MOTORES de la casa, tal
  cual: el informe de /revisar (pagos_confirmar._informe →
  reconciliacion.informe_datos), la cola real (pagos_confirmar.cola) y
  las confirmaciones humanas (pagos_confirmar.sumas_confirmadas). CERO
  cálculo paralelo: aquí solo se SUMA lo que esos motores ya dicen, así
  los números cuadran con /revisar y con la cola por construcción. Si
  aún así no cierran entre sí, el descuadre SE DICE — nunca se esconde.
- **La cola de «Pagos por confirmar» es LA MISMA** de
  /pagos-por-confirmar, listada en solo lectura: el botón «Confirmar»
  va APAGADO con «Todavía no — falta el sí de Jay» (BLOQUE 22.1).
  Confirmar de verdad sigue viviendo en /pagos-por-confirmar (system
  manager).
- **«Cifras a revisar»** son las dos cifras a propósito de cifras.py
  (pending_revenue / delivered_revenue), con su rótulo provisional tal
  cual — nada se re-etiqueta ni se redondea distinto.
- **Nada se inventa**: si el informe trae huecos (Odoo caído o sin
  configurar), los números dependientes salen «sin dato» y el hueco se
  dice — jamás un $0 fingido (la regla del resumen de las 7 p.m.).
- **Reportes**: botones APAGADOS, «Todavía no». Gastos y ganancias no
  van (faltan costos confiables, BLOQUE 20).
- SOLO LECTURA hacia Odoo/Linear/Twenty y hacia lo local.

Nota de costo: resumen() llama el informe UNA vez para las tarjetas y
otra DENTRO de cola() (que es dueña de su propia lectura) — dos lecturas
del mismo motor por pintada, el precio de no duplicar ni un renglón de
la lógica de la cola. Si algún día pesa, el lugar del arreglo es un
caché en reconciliacion.informe_datos, no una copia de la cola aquí.
"""

from datetime import datetime

from . import calculos, cifras, pagos_confirmar
from .datos import ZONA_PANAMA

# La misma frontera de centavos de la cola y de Pedidos.
_CENTAVO = 0.009

# Los reportes del diseño (pantalla jordan-finanzas): hoy solo nombre y
# botón apagado. Cuando existan, cada uno tendrá su motor.
REPORTES = ("Ventas del mes", "Cobros por método", "Ventas por persona",
            "Cuentas por cobrar")

TEXTO_BOTON_CONFIRMAR = "Todavía no — falta el sí de Jay"


def _tarjeta(titulo, monto, hint, n=None, rojo=False):
    """Una tarjeta de arriba, ya decidida: monto None = «sin dato» (el
    hueco viaja aparte y se pinta en la franja)."""
    return {
        "titulo": titulo,
        "monto": monto,
        "texto": (calculos.dinero(monto) if monto is not None else "sin dato"),
        "hint": hint,
        "n": n,
        "rojo": bool(rojo) and monto is not None and monto > _CENTAVO,
    }


def resumen():
    """Todo lo que la plantilla pinta, ya decidido (regla 10)."""
    # LA cola real (mismo motor y mismas reglas que /pagos-por-confirmar).
    try:
        pendientes, huecos = pagos_confirmar.cola()
    except Exception as fallo:
        pendientes, huecos = [], [f"La cola de pagos no contestó: {fallo}"]

    # El informe de /revisar para los totales (vendido / por cobrar).
    try:
        informe = pagos_confirmar._informe()
    except Exception as fallo:
        informe = {"ventas": [],
                   "huecos": [f"El informe de plata no contestó: {fallo}"]}
    for hueco in informe.get("huecos") or []:
        if hueco not in huecos:
            huecos.append(hueco)

    ventas = [v for v in (informe.get("ventas") or [])
              if (v.get("clase") or "").strip().upper() != "CANCELADA"]
    # Con huecos y sin ventas no hay foto: los números del informe salen
    # «sin dato», nunca $0 (un cero fingido se celebra o se cobra mal).
    con_datos = bool(ventas) or not huecos

    vendido = round(sum(float(v.get("total") or 0) for v in ventas), 2) \
        if con_datos else None
    por_cobrar = round(sum(float(v.get("debe") or 0) for v in ventas), 2) \
        if con_datos else None
    n_deben = sum(1 for v in ventas
                  if float(v.get("debe") or 0) > _CENTAVO)

    # Lo confirmado por humanos: tabla local, siempre legible.
    confirmado = round(sum(pagos_confirmar.sumas_confirmadas().values()), 2)

    # Lo por confirmar: la suma de la plata NUEVA de cada fila de la cola
    # (monto_nuevo — la misma cifra que confirma el system manager).
    por_confirmar = round(sum(float(p.get("monto_nuevo") or 0)
                              for p in pendientes), 2) if con_datos else None

    tarjetas = [
        _tarjeta("Vendido", vendido,
                 "Total de las ventas del informe de /revisar "
                 "(canceladas fuera)."),
        _tarjeta("Cobrado y confirmado", confirmado,
                 "Confirmaciones humanas registradas en la cola "
                 "(quién revisó, cuándo y qué vio)."),
        _tarjeta("Pagos por confirmar", por_confirmar,
                 (f"{len(pendientes)} en la cola: plata que alguien marcó "
                  "y nadie revisó.") if con_datos else
                 "La cola no se pudo leer.", n=len(pendientes)),
        _tarjeta("Por cobrar", por_cobrar,
                 f"{n_deben} ventas que todavía deben, según el informe.",
                 n=n_deben, rojo=True),
    ]

    # La honestidad del cuadre: mismas fuentes, y si no cierran se dice.
    descuadre = None
    if con_datos and vendido is not None:
        resto = round(vendido - (confirmado + (por_confirmar or 0)
                                 + (por_cobrar or 0)), 2)
        if abs(resto) > _CENTAVO:
            descuadre = resto

    hoy = datetime.now(ZONA_PANAMA).date()
    entregado_mes = cifras.delivered_revenue(hoy.replace(day=1), hoy)
    pendiente = cifras.pending_revenue()
    cifras_revisar = [
        {"texto": (f"Plata confirmada sin entregar: "
                   f"${pendiente['monto']:,.2f} en {pendiente['n']} ventas"
                   + (f" ({pendiente['sin_monto']} sin monto conocido)"
                      if pendiente["sin_monto"] else "")),
         "rotulo": pendiente["rotulo"]},
        {"texto": (f"Entregado del 1 al {hoy.day} de este mes: "
                   f"${entregado_mes['monto']:,.2f} en "
                   f"{entregado_mes['n']} entregas"
                   + (f" ({entregado_mes['sin_monto']} sin monto conocido)"
                      if entregado_mes["sin_monto"] else "")),
         "rotulo": entregado_mes["rotulo"]},
    ]

    return {
        "tarjetas": tarjetas,
        "pendientes": pendientes,
        "huecos": huecos,
        "con_datos": con_datos,
        "descuadre": descuadre,
        "cifras": cifras_revisar,
        "reportes": list(REPORTES),
        "boton_confirmar": TEXTO_BOTON_CONFIRMAR,
    }
