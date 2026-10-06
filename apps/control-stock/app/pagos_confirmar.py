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

from .datos import _db, ahora_iso
from . import venta_estado

# La evidencia que se vio, con su texto de pantalla. "reporte_operaciones"
# es el reporte de quien carga el deber de operaciones y banco.
EVIDENCIAS = {
    "tarjeta": "Pago con tarjeta (el voucher ya es evidencia)",
    "yappy": "Voucher de Yappy",
    "transferencia": "Confirmación de la transferencia",
    "reporte_operaciones": "Reporte de operaciones (llegó al banco)",
}

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


def cola():
    """(pendientes, huecos): las ventas del informe con plata que nadie
    confirmó que llegó — pagado real en Odoo sin confirmación humana, o
    clase F (pago informado fuera de Odoo, a verificar). Nada se
    inventa: si el informe trae huecos, viajan tal cual y la pantalla
    los dice.

    **Una orden ya confirmada RE-ENTRA cuando llega plata nueva** (fix
    del review, 5/10): el `orden_id in ya` viejo la excluía para
    siempre, así que el saldo de un evento con depósito confirmado
    nunca volvía a la cola y el pago_completo era imposible. La regla:
    re-entra cuando lo pagado según Odoo SUPERA la suma de montos ya
    confirmados; la fila lo dice (`aviso_reentrada`) y trae en
    `monto_nuevo` solo la plata nueva — confirmar registra OTRO hecho,
    nunca pisa el anterior."""
    informe = _informe()
    ya = sumas_confirmadas()
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
            aviso = (f"abono previo confirmado: ${previo:,.2f} — llegó "
                     f"plata nueva (${nuevo:,.2f} por confirmar)")
        debe = float(venta.get("debe") or 0)
        pendientes.append({
            "orden_id": venta.get("orden_id"),
            "orden": venta.get("nombre") or "",
            "cliente": venta.get("cliente") or "",
            "pagado": round(pagado, 2),
            "debe": round(debe, 2),
            "total": round(float(venta.get("total") or 0), 2),
            "clase": clase,
            "motivo": venta.get("motivo") or "",
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
              por_usuario, por_nombre=None, completo=False):
    """Registra la confirmación humana: quién marcó, cuándo y qué vio
    (la evidencia del selector) + la nota. Solo el system manager.
    Devuelve el código de error o None. Deja además el hecho del pago en
    venta_estado si la orden es una venta local (completo solo con el
    saldo en 0 — jamás convierte un depósito en cierre)."""
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
            print(f"pagos_confirmar: el hecho del pago de {origen} {n} no "
                  f"quedó anotado: {error!r}", flush=True)
    return None
