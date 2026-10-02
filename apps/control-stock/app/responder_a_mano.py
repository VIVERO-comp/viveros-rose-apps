"""Cuándo se prendió «Responder a mano» (2/10/2026, spec B2).

Linear NO guarda cuándo se puso una etiqueta, así que el aviso de «lleva
más de 48 horas marcado» necesita que ALGUIEN anote el momento. Ese
alguien es el botón 🔴 Responder de la ficha (`control.alternar_responder`),
que escribe la fila al prender y la borra al apagar.

El dueño de la verdad sigue siendo Linear: la tabla
`responder_a_mano_desde` es APOYO de pantalla, como `control_espera` o
`recordatorio_motivo` — guarda solo el momento, nunca el estado. Por eso
la **reconciliación**: si alguien quita la etiqueta directo en Linear (no
por el botón), la fila se borra sola la próxima vez que algo recorra los
leads — el resumen de las 7 p.m. (`resumen._leads`) y el refresco de fondo
del tablero (`control.avisar_en_fondo`) llaman `reconciliar(leads)` con la
lista que ya tenían en la mano: cero consultas extra.

**La siembra** (decisión de Korto, 2/10/2026): las «Responder a mano» que
ya estaban prendidas ANTES de este cambio no tienen momento anotado. Al
estrenarse la tabla (vacía y sin la marca de config), se les siembra la
fecha de HOY una sola vez — así las viejas empiezan a contar y salen a
las 48 horas, en vez de no salir nunca. La marca `responder_a_mano_
sembrada` en `config` garantiza que no se re-siembra en cada arranque.

**El umbral es editable sin desplegar**: la clave de `config`
`RESPONDER_A_MANO_HORAS` (48 si nadie la cambió), como los precios de
envío de Vender.
"""

from datetime import datetime

from . import linear_leads
from .datos import ZONA_PANAMA, _db, fijar_config, leer_config

# La clave del umbral en la tabla config, y su valor si nadie lo editó.
CLAVE_UMBRAL = "RESPONDER_A_MANO_HORAS"
UMBRAL_POR_DEFECTO = 48

# La marca de «la siembra ya se hizo»: con ella puesta, nunca más.
CLAVE_SEMBRADA = "responder_a_mano_sembrada"


def iniciar_tablas():
    with _db() as con:
        # Una fila por lead con «Responder a mano» prendida desde el botón:
        # solo el MOMENTO (ISO con zona de Panamá). El estado vive en
        # Linear; esto es el reloj que Linear no tiene.
        con.execute("""
            CREATE TABLE IF NOT EXISTS responder_a_mano_desde (
                lead_ref TEXT PRIMARY KEY,
                puesta_en TEXT NOT NULL
            )
        """)


def registro_aviso(texto):
    """Un aviso al log, propio del módulo (mismo criterio que
    mantenimiento.py: un NameError prestado solo se ve el día que algo
    falla de verdad)."""
    import logging
    logging.getLogger("control_stock").warning(texto)


def _ahora_iso():
    return datetime.now(ZONA_PANAMA).isoformat()


def umbral_horas():
    """Las horas a partir de las cuales una «Responder a mano» sale en el
    resumen. Editable en config sin desplegar; un valor roto no revienta
    el resumen: vuelve al 48 con aviso en el log."""
    crudo = (leer_config(CLAVE_UMBRAL, "") or "").strip()
    if not crudo:
        return UMBRAL_POR_DEFECTO
    try:
        valor = int(crudo)
        if valor <= 0:
            raise ValueError(crudo)
        return valor
    except (ValueError, TypeError):
        registro_aviso(
            f"El umbral {CLAVE_UMBRAL}={crudo!r} de config no es un número "
            f"de horas: se usa el de siempre ({UMBRAL_POR_DEFECTO}).")
        return UMBRAL_POR_DEFECTO


def anotar(ref):
    """El botón prendió «Responder a mano»: desde AHORA corre el reloj.
    Volver a prender reinicia el momento — es una decisión nueva."""
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO responder_a_mano_desde (lead_ref, puesta_en) "
            "VALUES (?, ?) "
            "ON CONFLICT(lead_ref) DO UPDATE SET puesta_en = excluded.puesta_en",
            (ref, _ahora_iso()))


def borrar(ref):
    """El botón la apagó (o la reconciliación vio que ya no está)."""
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "DELETE FROM responder_a_mano_desde WHERE lead_ref = ?", (ref,))


def _filas():
    iniciar_tablas()
    with _db() as con:
        return con.execute(
            "SELECT lead_ref, puesta_en FROM responder_a_mano_desde").fetchall()


def sembrar(leads):
    """La siembra de estreno: fecha de HOY para los leads que YA tienen la
    etiqueta prendida en Linear. Una sola vez en la vida de la base — la
    marca de config manda, y además solo siembra sobre tabla VACÍA (una
    base restaurada con filas no se pisa aunque la marca se haya perdido).
    """
    iniciar_tablas()
    if leer_config(CLAVE_SEMBRADA, ""):
        return []
    if _filas():
        fijar_config(CLAVE_SEMBRADA, _ahora_iso())
        return []
    sembrados = []
    ahora = _ahora_iso()
    with _db() as con:
        for lead in leads:
            if linear_leads.LABEL_RESPONDER_A_MANO not in (lead.get("etiquetas") or []):
                continue
            con.execute(
                "INSERT OR IGNORE INTO responder_a_mano_desde "
                "(lead_ref, puesta_en) VALUES (?, ?)", (lead["ref"], ahora))
            sembrados.append(lead["ref"])
    fijar_config(CLAVE_SEMBRADA, ahora)
    if sembrados:
        registro_aviso(
            "Responder a mano: siembra de estreno para "
            + ", ".join(sembrados) + " (empiezan a contar desde hoy).")
    return sembrados


def reconciliar(leads):
    """Borra las filas cuya etiqueta ya NO está en Linear (la quitó alguien
    a mano allá, o el lead desapareció): la tabla nunca le gana al tablero.

    Recibe la lista de leads que quien llama YA tenía en la mano — no
    consulta Linear por su cuenta. Corre la siembra de estreno primero,
    que necesita exactamente la misma lista. Devuelve los refs borrados.
    """
    sembrar(leads)
    borrados = []
    for fila in _filas():
        lead = linear_leads.uno(fila["lead_ref"], leads)
        if lead is None or (linear_leads.LABEL_RESPONDER_A_MANO
                            not in (lead.get("etiquetas") or [])):
            borrar(fila["lead_ref"])
            borrados.append(fila["lead_ref"])
    return borrados


def _horas_desde(iso_texto):
    try:
        cuando = datetime.fromisoformat(str(iso_texto))
    except (ValueError, TypeError):
        return None
    if cuando.tzinfo is None:
        # No debería pasar (anotar siempre escribe con zona), pero un ISO
        # naive no puede reventar el resumen: se asume Panamá.
        cuando = cuando.replace(tzinfo=ZONA_PANAMA)
    return (datetime.now(ZONA_PANAMA) - cuando).total_seconds() / 3600.0


def vencidos(leads):
    """Los leads con «Responder a mano» prendida hace más del umbral, para
    el renglón del resumen de las 7 p.m.: cliente, responsable y hace
    cuánto. Reconcilia primero, así una etiqueta quitada a mano en Linear
    jamás suena en el aviso.

    Si no hay ninguno, la lista sale vacía y el renglón no aparece — nada
    se inventa, como el resto del resumen.
    """
    reconciliar(leads)
    umbral = umbral_horas()
    filas = []
    for fila in _filas():
        horas = _horas_desde(fila["puesta_en"])
        if horas is None or horas <= umbral:
            continue
        lead = linear_leads.uno(fila["lead_ref"], leads)
        if lead is None:
            continue  # reconciliar ya la habría borrado; cinturón igual
        filas.append({
            "ref": lead["ref"], "nombre": lead["nombre"],
            "resp": lead.get("resp") or "",
            "horas": round(horas, 1),
        })
    filas.sort(key=lambda f: -f["horas"])
    return filas
