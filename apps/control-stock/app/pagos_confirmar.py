"""La cola «Pagos por confirmar» (item 6 de Jay, 5/10/2026).

Del decision record (§Money, for now, is a human confirmation): no hay
bank feed en este alcance. Quien carga OPERACIONES reporta lo que llegó
al banco; quien carga SYSTEM MANAGER marca la venta. Una tarjeta ya es
evidencia; un Yappy se marca desde el voucher; una transferencia, cuando
alguien manda la confirmación. Se registra QUIÉN marcó, CUÁNDO y QUÉ VIO.

Qué es esta pantalla — y qué no:

- Es LA COLA de los pagos que nadie ha confirmado que llegaron: otra
  cara del MISMO motor de /revisar (reconciliacion.informe_datos, solo
  lectura). NO es conciliación bancaria ni contabilidad, y /revisar
  queda exactamente como está.
- Entra a la cola toda venta con plata en Odoo sin confirmación humana
  registrada, y toda clase F (pago informado fuera de Odoo). Los viejos
  enredos se limpian trabajando la cola, no con otro producto.
- La ven los TRES deberes (system manager · operaciones · owner view —
  por deber, nunca por nombre); CONFIRMA solo el system manager (él es
  el dueño de la cola y quien marca la venta).
- Confirmar escribe la tabla local `pago_confirmado` y, si la orden es
  una venta local, deja el HECHO del pago en venta_estado (estado 2;
  completo solo si el saldo quedó en 0 — un depósito de evento nunca
  cierra). JAMÁS escribe en Odoo, Linear ni Twenty.
"""

import logging

from .datos import _db, ahora_iso
from . import calculos, venta_estado

# La evidencia que se vio, con su texto de pantalla. "reporte_operaciones"
# es el reporte de quien carga el deber de operaciones y banco.
EVIDENCIAS = {
    "tarjeta": "Pago con tarjeta (el voucher ya es evidencia)",
    "yappy": "Voucher de Yappy",
    "transferencia": "Confirmación de la transferencia",
    "reporte_operaciones": "Reporte de operaciones (llegó al banco)",
}

# Una evidencia que no está en EVIDENCIAS (una fila vieja, o una que
# alguien agregue sin pasar por acá): se MUESTRA con su propio renglón,
# nunca se reparte entre las conocidas ni se descuenta del total.
TEXTO_EVIDENCIA_DESCONOCIDA = "Sin clasificar: la evidencia no se reconoce"

# Centavos de tolerancia al decidir si el saldo quedó en 0.
_CENTAVO = 0.009


def iniciar_tablas():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pago_confirmado (
                n INTEGER PRIMARY KEY AUTOINCREMENT,
                orden_id INTEGER NOT NULL,
                orden TEXT NOT NULL DEFAULT '',
                cliente TEXT NOT NULL DEFAULT '',
                monto REAL,
                evidencia TEXT NOT NULL,
                nota TEXT NOT NULL DEFAULT '',
                por TEXT NOT NULL DEFAULT '',
                en TEXT NOT NULL
            )
        """)


# ---------------------------------------------------------------------------
# Quién ve y quién confirma (por deber, nunca por nombre)
# ---------------------------------------------------------------------------

DEBERES_QUE_VEN = ("system_manager", "operations", "owner_view")


def puede_ver(usuario):
    """¿Este usuario carga alguno de los tres deberes? La pantalla es de
    ellos: el system manager (dueño de la cola), operaciones (reporta el
    banco) y el owner view (mira todas las capas)."""
    from . import datos_roles
    for deber in DEBERES_QUE_VEN:
        ocupante = datos_roles.quien_ocupa(deber)
        if ocupante and any(p["usuario"] == usuario
                            for p in ocupante["personas"]):
            return True
    return False


def puede_confirmar(usuario):
    """Confirmar marca la venta: eso es del system manager (el decision
    record lo dice con nombre de deber, no de persona)."""
    return venta_estado.es_system_manager(usuario)


# ---------------------------------------------------------------------------
# La cola
# ---------------------------------------------------------------------------

def _informe():
    """El MISMO motor de /revisar, en import perezoso (las pruebas lo
    suplantan limpio y este módulo no se cae si aquel está a medias)."""
    from . import reconciliacion
    return reconciliacion.informe_datos()


def confirmados():
    """{orden_id: fila} de lo ya confirmado (la última confirmación de
    cada orden gana para la pantalla)."""
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM pago_confirmado ORDER BY n").fetchall()
    return {int(f["orden_id"]): dict(f) for f in filas}


def historial():
    with _db() as con:
        filas = con.execute(
            "SELECT * FROM pago_confirmado ORDER BY n DESC").fetchall()
    return [dict(f) for f in filas]


def sumas_confirmadas():
    """{orden_id: suma de los montos ya confirmados de esa orden}. Un
    monto NULL suma 0 a propósito — el lado conservador: ante la duda la
    orden puede VOLVER a la cola, nunca desaparecer para siempre."""
    with _db() as con:
        filas = con.execute(
            "SELECT orden_id, COALESCE(SUM(monto), 0) AS suma"
            " FROM pago_confirmado GROUP BY orden_id").fetchall()
    return {int(f["orden_id"]): round(float(f["suma"]), 2) for f in filas}


def confirmado_por_metodo(ids=None):
    """«Cómo pagaron»: lo ya dado por bueno, partido por la evidencia que
    vio quien confirmó. Lista de dicts {clave, texto, n, monto}, de mayor
    a menor.

    `ids` acota a un conjunto de órdenes (Finanzas pasa las CONFIRMADAS,
    para que la suma del desglose sea EXACTAMENTE su «Cobrado y
    confirmado» — BLOQUE 59.4: si el desglose sumara otra cosa, sería una
    verdad más justo cuando estamos matando otra). Sin `ids`, todo.

    Una evidencia que la casa no conozca NO se reparte ni se esconde: sale
    con su propia clave y el texto lo dice. Un monto NULL suma 0 (igual
    que `sumas_confirmadas`) pero su fila SÍ se cuenta: el hecho existe
    aunque el monto no se sepa."""
    with _db() as con:
        filas = con.execute(
            "SELECT orden_id, evidencia, COALESCE(SUM(monto), 0) AS suma,"
            " COUNT(*) AS n FROM pago_confirmado"
            " GROUP BY orden_id, evidencia").fetchall()
    acumulado = {}
    for f in filas:
        if ids is not None and int(f["orden_id"]) not in ids:
            continue
        clave = f["evidencia"] or ""
        celda = acumulado.setdefault(clave, {"n": 0, "monto": 0.0})
        celda["n"] += int(f["n"])
        celda["monto"] += float(f["suma"])
    salida = [{"clave": clave,
               "texto": EVIDENCIAS.get(clave, TEXTO_EVIDENCIA_DESCONOCIDA),
               "n": celda["n"],
               "monto": round(celda["monto"], 2)}
              for clave, celda in acumulado.items()]
    salida.sort(key=lambda d: (-d["monto"], d["texto"]))
    return salida


# ---------------------------------------------------------------------------
# Las tres palabras de contexto de una fila de la cola (7/10/2026)
#
# «Falta la fecha de la venta» era el reclamo; el método y el quién vinieron
# con él. La regla de la casa manda sobre las ganas de llenar la fila: lo
# que no se sabe SE DICE. Por eso cada una de estas tres tiene su frase de
# «todavía no» y ninguna devuelve un guion mudo ni una fecha de hoy.
# ---------------------------------------------------------------------------

SIN_FECHA = "sin fecha en Odoo"
SIN_METODO = "todavía sin método: se elige al confirmar"
SIN_MARCA = "nadie la ha marcado todavía"


def fecha_de_venta(crudo):
    """`2026-10-02 15:04:33` → `02/10/2026`. Lo que no se entienda sale
    como «sin fecha en Odoo»: una fecha a medias se lee como un dato."""
    texto = str(crudo or "").strip()[:10]
    partes = texto.split("-")
    if len(partes) != 3 or not all(p.isdigit() for p in partes):
        return SIN_FECHA
    return f"{partes[2]}/{partes[1]}/{partes[0]}"


def metodo_de(confirmacion):
    """Cómo llegó la plata, en palabras: la evidencia que vio quien
    confirmó. Sin confirmación todavía no hay método que contar."""
    if not confirmacion:
        return SIN_METODO
    return EVIDENCIAS.get(confirmacion.get("evidencia"), SIN_METODO)


def marco_de(confirmacion):
    """Quién marcó esta plata y cuándo, del libro de confirmaciones. Una
    fila que nadie tocó lo dice con todas las letras: la plata está en
    Odoo y ninguna persona la ha revisado."""
    if not confirmacion:
        return SIN_MARCA
    quien = (confirmacion.get("por") or "").strip()
    cuando = fecha_de_venta(confirmacion.get("en"))
    if not quien:
        return f"marcada el {cuando}, sin nombre de quién"
    return (f"{quien}" if cuando == SIN_FECHA
            else f"{quien}, el {cuando}")


def cola(informe=None):
    """(pendientes, huecos): las ventas del informe con plata que nadie
    confirmó que llegó — pagado real en Odoo sin confirmación humana, o
    clase F (pago informado fuera de Odoo, a verificar). Nada se
    inventa: si el informe trae huecos, viajan tal cual y la pantalla
    los dice.

    **`informe` deja PASARLE la foto ya leída** (7/10/2026). Sin él la
    cola sigue siendo dueña de su propia lectura, exactamente como
    siempre — /pagos-por-confirmar no cambia en nada. Lo pide Finanzas,
    que necesita la MISMA foto para las tarjetas: medido en el 8095,
    leía el motor DOS VECES por pintada (24 viajes a Odoo y 2 lecturas
    de Linear, el doble que /revisar), y pasarle la foto lo baja a 12 y
    1. No es una caché: es no pedir dos veces lo mismo en la misma
    pintada, así que no hay dato viejo posible y las dos mitades de la
    pantalla quedan además CONSISTENTES entre sí — antes podían salir de
    dos lecturas distintas de Odoo.

    **Una orden ya confirmada RE-ENTRA cuando llega plata nueva** (fix
    del review, 5/10): el `orden_id in ya` viejo la excluía para
    siempre, así que el saldo de un evento con depósito confirmado
    nunca volvía a la cola y el pago_completo era imposible. La regla:
    re-entra cuando lo pagado según Odoo SUPERA la suma de montos ya
    confirmados; la fila lo dice (`aviso_reentrada`) y trae en
    `monto_nuevo` solo la plata nueva — confirmar registra OTRO hecho,
    nunca pisa el anterior.

    **Cada fila dice DE CUÁNDO es, CÓMO llegó la plata y QUIÉN la marcó**
    (7/10/2026). Los tres se arman acá, en palabras, y los tres saben
    callarse: lo que la casa no sabe se DICE («todavía sin método»), no se
    rellena. De dónde sale cada uno:

    - **cuándo** → `fecha` de la venta en Odoo (`date_order`, que el
      informe ahora trae). Sin ella: «sin fecha en Odoo».
    - **cómo** → la evidencia de la ÚLTIMA confirmación de esa orden
      (`pago_confirmado.evidencia`: tarjeta, Yappy, transferencia o el
      reporte de operaciones). Una fila que nadie confirmó todavía no
      tiene método: se elige AL confirmar, y hasta entonces se dice.
    - **quién** → `pago_confirmado.por` de esa misma confirmación. Una
      fila nueva no la marcó nadie: la plata está en Odoo y nadie la ha
      revisado, y eso es lo que se escribe."""
    if informe is None:
        informe = _informe()
    ya = sumas_confirmadas()
    ultimas = confirmados()
    pendientes = []
    for venta in informe.get("ventas") or []:
        clase = (venta.get("clase") or "").strip().upper()
        if clase == "CANCELADA":
            continue
        pagado = float(venta.get("pagado") or 0)
        previo = ya.get(venta.get("orden_id"))
        if previo is None:
            if pagado <= _CENTAVO and clase != "F":
                continue
            nuevo, aviso = pagado, ""
        else:
            if pagado <= previo + _CENTAVO:
                continue  # nada nuevo que confirmar
            nuevo = pagado - previo
            aviso = (f"abono previo confirmado: {calculos.dinero(previo)} — "
                     f"llegó plata nueva "
                     f"({calculos.dinero(nuevo)} por confirmar)")
        debe = float(venta.get("debe") or 0)
        ultima = ultimas.get(venta.get("orden_id"))
        pendientes.append({
            "orden_id": venta.get("orden_id"),
            "orden": venta.get("nombre") or "",
            "cliente": venta.get("cliente") or "",
            # Los tres renglones de contexto de la fila, ya en palabras
            # (regla 10: la plantilla no decide ni traduce nada).
            "fecha": venta.get("fecha") or "",
            "fecha_texto": fecha_de_venta(venta.get("fecha")),
            "metodo_texto": metodo_de(ultima),
            "marco_texto": marco_de(ultima),
            "pagado": round(pagado, 2),
            "debe": round(debe, 2),
            "total": round(float(venta.get("total") or 0), 2),
            "clase": clase,
            "motivo": venta.get("motivo") or "",
            # ¿La venta está confirmada en Odoo? Viaja TAL CUAL desde el
            # informe (BLOQUE 59.2): la cola sigue mostrando TODO lo que
            # tiene plata o señal de plata — un pago sobre una cotización
            # sin confirmar es justo lo que hay que ver — pero Finanzas
            # necesita poder sumar solo las confirmadas.
            # **Sin `bool()` a propósito**: un None (una foto vieja que no
            # trae el campo) tiene que llegar como None y no como False,
            # para que quien decida pueda distinguir «no está confirmada»
            # de «no se sabe» y caer en su candado. Un `bool()` acá
            # borraba esa diferencia y mandaba una venta pagada al montón
            # de las cotizaciones.
            "confirmada": venta.get("confirmada"),
            # F = el dinero NO está en Odoo: la fila lo dice para que la
            # evidencia que se pida sea la de verdad.
            "fuera_de_odoo": clase == "F",
            # ¿El saldo quedó en 0? Decide si la confirmación deja la
            # venta lista para cerrar (completo) o es un abono.
            "completo": debe <= _CENTAVO and pagado > _CENTAVO,
            # La plata de ESTE hecho (lo nuevo): es lo que se confirma.
            "monto_nuevo": round(nuevo, 2),
            "confirmado_previo": round(previo or 0.0, 2),
            "reentrada": previo is not None,
            "aviso_reentrada": aviso,
        })
    return pendientes, list(informe.get("huecos") or [])


# ---------------------------------------------------------------------------
# Confirmar
# ---------------------------------------------------------------------------

def _venta_local_de(orden_id):
    """(origen, n) de la venta local amarrada a esta orden de Odoo, o
    (None, None): la confirmación también deja el hecho del pago en el
    estado local cuando hay dónde."""
    with _db() as con:
        fila = con.execute(
            "SELECT n FROM ventas_locales WHERE orden_id=?",
            (orden_id,)).fetchone()
        if fila:
            return "venta", int(fila["n"])
        fila = con.execute(
            "SELECT n FROM cotizaciones_servicio WHERE orden_id=?",
            (orden_id,)).fetchone()
        if fila:
            return "servicio", int(fila["n"])
    return None, None


def confirmar(orden_id, orden, cliente, monto, evidencia, nota,
              por_usuario, por_nombre=None, completo=False,
              pagado_total=None):
    """Registra la confirmación humana: quién marcó, cuándo y qué vio
    (la evidencia del selector) + la nota. Solo el system manager.
    Devuelve el código de error o None. Deja además el hecho del pago en
    venta_estado si la orden es una venta local (completo solo con el
    saldo en 0 — jamás convierte un depósito en cierre).

    **Idempotencia (review, 5/10): el MISMO pago repetido es no-op**
    (devuelve 'ya_confirmado', sin fila nueva ni hecho en venta_estado).
    Es repetido cuando la ÚLTIMA confirmación de la orden tiene el mismo
    monto y evidencia Y no hay plata nueva que lo justifique: con
    `pagado_total` (lo pagado según Odoo, lo pasa la ruta desde la fila
    de la cola), plata nueva = pagado_total supera la suma confirmada —
    la frontera del Requerido 1: dos abonos iguales con plata nueva SÍ
    son dos hechos. Sin `pagado_total`, el calce de monto+evidencia
    basta (lado conservador: no duplicar)."""
    if not puede_confirmar(por_usuario):
        return "solo_system_manager"
    if evidencia not in EVIDENCIAS:
        return "evidencia_invalida"
    try:
        orden_id = int(orden_id)
    except (TypeError, ValueError):
        return "orden_invalida"
    por = por_nombre or por_usuario
    try:
        monto = round(float(monto), 2)
    except (TypeError, ValueError):
        monto = None
    with _db() as con:
        ultima = con.execute(
            "SELECT monto, evidencia FROM pago_confirmado WHERE orden_id=?"
            " ORDER BY n DESC LIMIT 1", (orden_id,)).fetchone()
    if ultima is not None and ultima["evidencia"] == evidencia \
            and ultima["monto"] == monto:
        suma = sumas_confirmadas().get(orden_id, 0.0)
        try:
            hay_plata_nueva = (pagado_total is not None
                               and float(pagado_total) > suma + _CENTAVO)
        except (TypeError, ValueError):
            hay_plata_nueva = False
        if not hay_plata_nueva:
            return "ya_confirmado"
    with _db() as con:
        con.execute(
            "INSERT INTO pago_confirmado (orden_id, orden, cliente, monto,"
            " evidencia, nota, por, en) VALUES (?,?,?,?,?,?,?,?)",
            (orden_id, (orden or "").strip()[:40],
             (cliente or "").strip()[:120], monto, evidencia,
             (nota or "").strip()[:500], por, ahora_iso()))
    origen, n = _venta_local_de(orden_id)
    if origen is not None:
        try:
            venta_estado.registrar_pago(
                origen, n, por, monto=monto, completo=bool(completo),
                detalle=f"confirmado en la cola ({evidencia})")
        except Exception as error:
            logging.getLogger("control_stock").warning(
                f"pagos_confirmar: el hecho del pago de {origen} {n} no "
                f"quedó anotado: {error!r}")
    return None
