"""La pestaña PEDIDOS: una VISTA sobre el motor de los 3 estados.

Punto 4 del BLOQUE 12 de Korto, con el diseño corto aprobado y el ACK del
Arquitecto (6/10/2026 — docs/DISENO-PEDIDOS-Y-RESTO.md §A del repo
plantaspanama). NO es la pestaña Pedidos descartada del 30/09: esta nace
del modelo de Jay — «el pedido nace cuando el cliente paga o abona».

Qué es — y qué no:

- **CERO lógica nueva**: este módulo consume las MISMAS funciones del
  motor que la ficha Estado/Entrega (venta_estado.estados_de, el estado
  1|2|3 ya decidido allá; entregas.obligaciones_de, la obligación
  nombrada con su fecha_programada) y el MISMO motor de plata de la cola
  de pagos (pagos_confirmar._informe → reconciliacion.informe_datos).
  Aquí solo se AGRUPA y se PINTA; ninguna regla se recalcula.
- **El criterio de selección (el dominio exacto), CONGELADO**: una
  tarjeta es una venta LOCAL de Vender — ventas_locales ('venta') o
  cotizaciones_servicio ('servicio') — con fila en venta_estado. **Los
  pedidos VR- de la tienda en línea NO entran** (precisión (iii) del
  review): viven en el equipo VIV con su propia fecha_entrega de la
  Fase 2 y tendrán su diseño propio; acá además se excluye por el
  prefijo de la orden, por si algún día una VR- aterrizara en las
  tablas locales. Una venta de plantas cancelada tampoco entra (igual
  que en la lista de Vender).
- **Las columnas, CONGELADAS y excluyentes POR CONSTRUCCIÓN** (nota 2
  del ACK — un if/elif sobre el estado, imposible caer en dos):
      Por programar       = estado 2 y fecha_programada vacía
      Programado          = estado 2 y fecha_programada puesta
      Entregado reciente  = estado 3 con fecha_entrega dentro de la
                            ventana (solo lectura)
  La ventana son FECHAS de calendario de Panamá comparadas como fecha
  (texto ISO ordena igual), default 7 días, configurable en la tabla
  config (clave pedidos.ventana_dias) sin desplegar.
- **La plata** («debe $X» en rojo solo si debe) sale del informe real.
  Con Odoo caído la pestaña se pinta SIEMPRE desde el estado local y
  DICE el hueco — jamás $0 ni silencio (la regla del resumen): si hay
  una lectura buena anterior se usa esa y se muestra su edad
  («actualizado hace X min»); si no la hay, cada tarjeta dice «plata
  sin dato».
- Linear/Twenty/Odoo: SOLO LECTURA (régimen del BLOQUE 12). Este módulo
  no escribe en NADA — ni siquiera local: es una vista.
- «Marcar entregada» NO vive acá: sigue solo en la ficha. Abrir una
  tarjeta lleva a la ficha existente (/venta/estado/...) — no se
  duplica pantalla.
"""

import time
from datetime import datetime, timedelta

from . import cotizaciones, datos, datos_roles, entregas, venta_estado, ventas
from .datos import ZONA_PANAMA
from .datos_roles import _plano

# La ventana de «Entregado reciente»: días hacia atrás, default 7
# (pregunta del diseño: 7 con config fácil). Se cambia con
# datos.fijar_config("pedidos.ventana_dias", "14") — sin desplegar.
CLAVE_VENTANA = "pedidos.ventana_dias"
VENTANA_DEFAULT = 7

# Centavos de tolerancia al decidir si una venta debe (la misma frontera
# que usa la cola de pagos).
_CENTAVO = 0.009

COLUMNAS = (
    ("por_programar", "Por programar",
     "Plata confirmada, sin fecha. La fecha se pone en la ficha."),
    ("programado", "Programado",
     "Con fecha programada de entrega."),
    ("entregado", "Entregado reciente",
     "Cerradas (estado 3). Solo lectura."),
)


def ventana_dias():
    """Los días de la ventana de «Entregado reciente», desde config. Un
    valor roto o ≤0 cae al default — la pantalla nunca truena por una
    clave mal escrita."""
    crudo = datos.config_valores("pedidos.").get("ventana_dias")
    try:
        dias = int(str(crudo).strip())
    except (TypeError, ValueError):
        return VENTANA_DEFAULT
    return dias if dias > 0 else VENTANA_DEFAULT


def hoy_panama():
    """HOY como fecha de calendario de Panamá — nunca el reloj de otra
    máquina (la trampa de zonas ya mordió)."""
    return datetime.now(ZONA_PANAMA).date()


def columna_de(estado, fecha_programada, fecha_entrega, hoy, ventana):
    """EN QUÉ COLUMNA cae una venta, o None si no va al tablero. Criterio
    CONGELADO (nota 2 del ACK) y excluyente POR CONSTRUCCIÓN: el if/elif
    sobre el estado hace imposible que una tarjeta caiga en dos.

    `hoy` es date; `ventana` días. «Dentro de la ventana» = fecha_entrega
    en [hoy − ventana, hoy], inclusive ambos lados, comparando FECHAS de
    calendario (texto ISO) — nunca relojes."""
    estado = int(estado)
    if estado == 2:
        return "programado" if (fecha_programada or "").strip() \
            else "por_programar"
    if estado == 3:
        fecha = (fecha_entrega or "").strip()
        if not fecha:
            return None
        desde = (hoy - timedelta(days=ventana)).isoformat()
        hasta = hoy.isoformat()
        return "entregado" if desde <= fecha <= hasta else None
    return None  # estado 1: todavía no es un pedido (no hay plata)


# ---------------------------------------------------------------------------
# La plata: el MISMO motor de la cola, con la última lectura buena
# ---------------------------------------------------------------------------

def _informe():
    """El MISMO motor que la cola de pagos (pagos_confirmar._informe →
    reconciliacion.informe_datos). Import perezoso, como allá."""
    from . import pagos_confirmar
    return pagos_confirmar._informe()


# La última lectura BUENA del informe: {en: epoch, por_orden: {...}}.
# Vive en el proceso; con Odoo caído la pestaña la sirve diciendo su
# edad, en vez de inventar un $0 o callarse.
_cache_plata = {"en": None, "por_orden": {}}


def reiniciar_cache_plata():
    _cache_plata["en"] = None
    _cache_plata["por_orden"] = {}


def plata():
    """{'por_orden': {orden_id: {pagado, debe, total}}, 'huecos': [...],
    'edad_min': None|int, 'con_datos': bool}.

    Nada se inventa: si el informe trae huecos viajan tal cual; si Odoo
    no dio ventas y hay una lectura buena anterior, se sirve ESA con su
    edad en minutos (edad_min); sin nada, con_datos=False y las
    tarjetas dicen «sin dato»."""
    try:
        informe = _informe()
    except Exception as fallo:
        informe = {"ventas": [], "huecos": [f"El informe de plata no "
                                            f"contestó: {fallo}"]}
    huecos = list(informe.get("huecos") or [])
    filas = informe.get("ventas") or []
    if filas:
        _cache_plata["por_orden"] = {
            v["orden_id"]: {"pagado": round(float(v.get("pagado") or 0), 2),
                            "debe": round(float(v.get("debe") or 0), 2),
                            "total": round(float(v.get("total") or 0), 2)}
            for v in filas if v.get("orden_id")}
        _cache_plata["en"] = time.time()
        return {"por_orden": _cache_plata["por_orden"], "huecos": huecos,
                "edad_min": None, "con_datos": True}
    if _cache_plata["en"] is not None:
        edad = max(0, int((time.time() - _cache_plata["en"]) // 60))
        return {"por_orden": _cache_plata["por_orden"], "huecos": huecos,
                "edad_min": edad, "con_datos": True}
    return {"por_orden": {}, "huecos": huecos, "edad_min": None,
            "con_datos": False}


def _plata_de(tarjeta, plata_info):
    """El renglón de plata de UNA tarjeta, ya decidido en Python (regla
    10): 'debe' en rojo solo si debe, 'pagado' si el saldo está en 0,
    'sin_dato' cuando el informe no la trae o no hay informe."""
    fila = (plata_info["por_orden"].get(tarjeta["orden_id"])
            if plata_info["con_datos"] and tarjeta["orden_id"] else None)
    if fila is None:
        return {"clase": "sin_dato", "texto": "Plata sin dato", "debe": None}
    if fila["debe"] > _CENTAVO:
        return {"clase": "debe", "texto": f"Debe ${fila['debe']:,.2f}",
                "debe": fila["debe"]}
    return {"clase": "pagado", "texto": "Pagado", "debe": 0.0}


# ---------------------------------------------------------------------------
# Las tarjetas y el tablero
# ---------------------------------------------------------------------------

def _registros_locales():
    """{(origen, n): registro} de las dos tablas locales de Vender — el
    DOMINIO de la vista. Una venta de plantas cancelada no entra (igual
    que en la lista de Vender)."""
    registros = {}
    for v in ventas.ventas_todas():
        if v.get("estado") == "cancelada":
            continue
        registros[("venta", v["n"])] = v
    for c in cotizaciones.cotizaciones_todas():
        registros[("servicio", c["n"])] = c
    return registros


def tarjetas(hoy=None):
    """{clave de columna: [tarjetas]} del tablero, SIN la plata (esa la
    agrega tablero(), que es quien habla con el informe). Orden estable
    dentro de cada columna: fecha_programada, luego creación (y el par
    origen/n como desempate total)."""
    hoy = hoy or hoy_panama()
    ventana = ventana_dias()
    estados = venta_estado.estados_de()
    obligaciones = entregas.obligaciones_de()
    registros = _registros_locales()
    columnas = {clave: [] for clave, _t, _p in COLUMNAS}
    for (origen, n), fila in estados.items():
        registro = registros.get((origen, n))
        if registro is None:
            continue  # sin venta local detrás no hay qué pintar
        orden = (registro.get("orden") or "").strip()
        # Los pedidos VR- de la tienda NO entran (precisión (iii)): su
        # tablero es otro pendiente, con diseño propio.
        if orden.upper().startswith("VR-"):
            continue
        obligacion = obligaciones.get((origen, n)) or \
            {"direccion": "", "asignado": "", "fecha_programada": ""}
        fecha_programada = obligacion.get("fecha_programada") or ""
        columna = columna_de(fila["estado"], fecha_programada,
                             fila.get("fecha_entrega"), hoy, ventana)
        if columna is None:
            continue
        columnas[columna].append({
            "origen": origen, "n": n,
            "cliente": registro.get("cliente") or "—",
            "orden": orden or "—",            # el S00xxx visible
            "orden_id": registro.get("orden_id"),
            "total": registro.get("total"),
            "tipo_venta": fila.get("tipo_venta") or "",
            "direccion": obligacion.get("direccion") or "",
            "asignado": obligacion.get("asignado") or "",
            "fecha_programada": fecha_programada,
            "fecha_entrega": fila.get("fecha_entrega") or "",
            "creado_en": registro.get("creado_en") or "",
            "href": f"/venta/estado/{origen}/{n}",  # la ficha EXISTENTE
        })
    for lista in columnas.values():
        lista.sort(key=lambda t: (t["fecha_programada"], t["creado_en"],
                                  t["origen"], t["n"]))
    return columnas


def _filtros(columnas, tipo_activo):
    """Las capas por tipo de venta (los del item 1 — el catálogo vivo de
    datos_roles.tipos_venta_activos), con su cuenta sobre el tablero SIN
    filtrar. Solo salen los tipos con al menos una tarjeta."""
    cuentas = {}
    for lista in columnas.values():
        for t in lista:
            clave = _plano(t["tipo_venta"])
            cuentas[clave] = cuentas.get(clave, 0) + 1
    filtros = []
    for tipo in datos_roles.tipos_venta_activos():
        clave = _plano(tipo["nombre"])
        if not cuentas.get(clave):
            continue
        filtros.append({"nombre": tipo["nombre"], "cuenta": cuentas[clave],
                        "activo": clave == _plano(tipo_activo or "")})
    return filtros


def tablero(tipo=None, hoy=None):
    """Todo lo que la plantilla pinta, ya decidido (regla 10): las tres
    columnas con sus tarjetas (plata incluida), los filtros por tipo y
    el aviso honesto de la plata."""
    columnas = tarjetas(hoy=hoy)
    filtros = _filtros(columnas, tipo)
    tipo_valido = any(f["activo"] for f in filtros)
    plata_info = plata()
    armadas = []
    for clave, titulo, pista in COLUMNAS:
        lista = columnas[clave]
        if tipo_valido:
            lista = [t for t in lista
                     if _plano(t["tipo_venta"]) == _plano(tipo)]
        for t in lista:
            t["plata"] = _plata_de(t, plata_info)
        armadas.append({"clave": clave, "titulo": titulo, "pista": pista,
                        "tarjetas": lista, "cuenta": len(lista),
                        "solo_lectura": clave == "entregado"})
    aviso = None
    if plata_info["huecos"]:
        base = "La plata tiene huecos — el tablero se pinta desde el " \
               "estado local: " + " · ".join(plata_info["huecos"])
        if plata_info["edad_min"] is not None:
            base += (f" (mostrando la última lectura buena, actualizada "
                     f"hace {plata_info['edad_min']} min)")
        aviso = base
    return {"columnas": armadas, "filtros": filtros,
            "tipo": tipo if tipo_valido else None,
            "aviso_plata": aviso, "ventana": ventana_dias()}
