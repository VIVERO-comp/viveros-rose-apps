"""El calendario con piel de Twenty (la pestaña Calendario dentro del CRM).

Es la MISMA lógica del calendario de siempre (app/calendario.py: Linear manda,
caché, tipos, leads de servicio) con otra cara: se sirve en /crm/calendario y
nginx del droplet del CRM la muestra dentro de Twenty como un iframe, igual
que la pestaña Chats. Aquí vive solo lo propio de esa cara:

- La paleta: el dueño fijó (22/09/2026) que cada cosa del negocio lleva EL
  MISMO color que su label de Twenty — retail/entrega verde, Eventos ·
  Alquiler rojo, Mantenimiento azul. Los mapas viven en colores.py (la
  paleta unica) y los armadores de calendario.py aceptan `color_de_tipo`,
  asi que esta cara pinta directo con lo suyo sin tocar el calendario de
  inventario (antes habia un `repintar()` por string-replace; murio en la
  Fase 1 del plan de colores).
- Un carril genérico de N días (la vista Día del CRM es una columna sola,
  no una columna por persona como en inventario).
- La ficha del lead: teléfono, Person y la conversación de WhatsApp leídos
  del Twenty real (API REST, solo lectura). Sin TWENTY_API_KEY la ficha
  muestra lo que da Linear y ya: nunca tumba la pantalla.
"""

import os
import time
from datetime import datetime
from urllib.parse import quote

import httpx

from . import calendario, colores

# ---------------------------------------------------------------------------
# Paleta: el color de cada cosa es el de su label en Twenty (dueño,
# 22/09/2026). Los mapas viven en colores.py; aqui solo los resolvedores.
# ---------------------------------------------------------------------------


def color_crm(tipo):
    return colores.COLORES_CRM_CALENDARIO.get(tipo, calendario.color_de(tipo))


def color_etiqueta(etiqueta):
    return colores.ETIQUETAS_CHIP_CRM.get(etiqueta, colores.CHIP_CRM_SIN_ETIQUETA)


# ---------------------------------------------------------------------------
# Carril genérico de N días. La semana del CRM son 7; la vista Día, 1 (una
# columna sola: dentro de Twenty no se abre el día por persona).
# ---------------------------------------------------------------------------

def carril_dias(actividades, dias, dia_hoy):
    del_rango = [a for a in actividades if a["fecha"] in dias]
    desde, hasta = calendario.rango_horas(del_rango)
    filas = calendario.filas_de_horas(del_rango, desde, hasta)
    n = len(dias) or 1
    columnas, bloques = [], []
    for i, dia_iso in enumerate(dias):
        del_dia = calendario._del_dia(actividades, dia_iso)
        fecha = calendario._dia(dia_iso)
        columnas.append({
            "iso": dia_iso, "dow": calendario.DOW[fecha.weekday()], "num": fecha.day,
            "hoy": dia_iso == dia_hoy, "finde": fecha.weekday() > 4, "cant": len(del_dia),
            "estilo": (f"left:calc(46px + (100% - 46px) * {i / n});"
                       f"width:calc((100% - 46px) / {n})"),
        })
        for actividad, columna, total in calendario._repartir(del_dia):
            bloques.append(calendario._bloque(
                actividad, columna, total, i, n, dia_hoy, filas,
                color_de_tipo=color_crm))
    return {"columnas": columnas, "bloques": bloques, "horas": filas,
            "ahora": calendario.linea_ahora(filas) if dia_hoy in dias else None}


# ---------------------------------------------------------------------------
# La ficha del lead: lo que Twenty sabe de él (teléfono, conversación).
# Solo lectura y fail-soft: cualquier tropiezo devuelve None y la ficha se
# pinta con lo que dio Linear.
# ---------------------------------------------------------------------------

TTL_FICHA = 60
_fichas = {}


def _var(nombre):
    return (os.environ.get(nombre) or "").strip()


def twenty_configurado():
    return bool(_var("TWENTY_API_KEY"))


def twenty_publico():
    """La dirección del Twenty que ve la gente (para los saltos)."""
    return _var("TWENTY_PUBLICO") or "https://crm.plantaspanama.com"


def _twenty(ruta):
    base = _var("TWENTY_URL") or twenty_publico()
    respuesta = httpx.get(
        f"{base}/rest/{ruta}",
        headers={"Authorization": f"Bearer {_var('TWENTY_API_KEY')}"},
        timeout=8.0,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def _twenty_patch(ruta, datos):
    """PATCH a un registro de Twenty (el respaldo directo de las escrituras
    del CRM cuando la ruta del panel no está configurada)."""
    base = _var("TWENTY_URL") or twenty_publico()
    respuesta = httpx.patch(
        f"{base}/rest/{ruta}",
        headers={"Authorization": f"Bearer {_var('TWENTY_API_KEY')}"},
        json=datos,
        timeout=8.0,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def telefono_legible(phones):
    """Campos de teléfono de Twenty -> '+507 6567-3062'."""
    if not phones:
        return ""
    digitos = "".join(c for c in str(phones.get("primaryPhoneNumber") or "") if c.isdigit())
    prefijo = (phones.get("primaryPhoneCallingCode") or "").strip()
    if not digitos:
        return ""
    if digitos.startswith("507"):
        digitos = digitos[3:]
    if len(digitos) == 8:
        return f"+507 {digitos[:4]}-{digitos[4:]}"
    return f"{prefijo or '+'}{' ' if prefijo else ''}{digitos}"


def telefono_wame(phones):
    """Dígitos para un enlace wa.me (sin '+'); Panamá asume 507."""
    if not phones:
        return ""
    digitos = "".join(c for c in str(phones.get("primaryPhoneNumber") or "") if c.isdigit())
    codigo = "".join(c for c in str(phones.get("primaryPhoneCallingCode") or "") if c.isdigit())
    if not digitos:
        return ""
    if codigo and not digitos.startswith(codigo):
        return codigo + digitos
    if len(digitos) == 8:
        return "507" + digitos
    return digitos


def _cuando_bonito(iso_texto):
    """ISO de Twenty -> '16/09 · 10:12' en hora de Panamá."""
    try:
        cuando = datetime.fromisoformat(str(iso_texto).replace("Z", "+00:00"))
        cuando = cuando.astimezone(calendario.ZONA_PANAMA)
        return f"{cuando.day:02d}/{cuando.month:02d} · {cuando.hour:02d}:{cuando.minute:02d}"
    except (ValueError, TypeError):
        return ""


def ficha_de_lead(lead):
    """{pp, telefono, wa, llego, mensajes, twenty_url} o None si no hay Twenty."""
    if not twenty_configurado():
        return None
    llave = lead.get("id") or lead.get("ref")
    guardada = _fichas.get(llave)
    if guardada and time.time() - guardada["en"] < TTL_FICHA:
        return guardada["dato"]
    try:
        dato = _buscar_ficha(lead)
    except Exception:  # la ficha es un extra: sin Twenty, se muestra lo de Linear
        dato = None
    _fichas[llave] = {"en": time.time(), "dato": dato}
    return dato


def _buscar_ficha(lead):
    lead_web = None
    if lead.get("id"):
        j = _twenty(f'leadsWeb?filter=linearIssueId[eq]:%22{quote(lead["id"])}%22&limit=1')
        filas = (j.get("data") or {}).get("leadsWeb") or []
        lead_web = filas[0] if filas else None
    if not lead_web and lead.get("pp"):
        j = _twenty(f'leadsWeb?filter=codigoRef[eq]:%22{quote(lead["pp"])}%22&limit=1')
        filas = (j.get("data") or {}).get("leadsWeb") or []
        lead_web = filas[0] if filas else None
    if not lead_web:
        return None

    persona_id = lead_web.get("personaId") or ""
    telefono = wa = ""
    if persona_id:
        p = (_twenty(f"people/{quote(persona_id)}").get("data") or {}).get("person") or {}
        telefono = telefono_legible(p.get("phones"))
        wa = telefono_wame(p.get("phones"))

    mensajes = []
    if persona_id:
        j = _twenty("mensajesWhatsapp?filter=personaId[eq]:%22" + quote(persona_id)
                    + "%22&order_by=createdAt[AscNullsFirst]&limit=60")
        for m in (j.get("data") or {}).get("mensajesWhatsapp") or []:
            mensajes.append({
                "texto": m.get("texto") or "",
                "salida": (m.get("direccion") or "") == "SALIENTE",
                "cuando": _cuando_bonito(m.get("fecha") or m.get("createdAt") or ""),
            })

    llego = ""
    if lead_web.get("createdAt"):
        llego = calendario.dmy(str(lead_web["createdAt"])[:10])
    return {
        "pp": lead_web.get("codigoRef") or lead.get("pp") or "",
        "telefono": telefono,
        "wa": wa,
        "llego": llego,
        "mensajes": mensajes,
        "twenty_url": (f"{twenty_publico()}/object/person/{persona_id}"
                       if persona_id else f"{twenty_publico()}/objects/leads"),
    }
