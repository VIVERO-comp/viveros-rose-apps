"""El resumen del día, al celular del dueño a las 7 de la tarde.

Pedido de Abraham (25/09/2026): un aviso al celular —**no** WhatsApp— solo
para él, con cinco cosas: los leads nuevos del día por origen, las
cotizaciones, los pagos registrados, los leads sin dueño y quién tiene
clientes esperando y hace cuánto.

Desde el 25/09/2026 lleva un sexto renglón que no es del negocio sino de la
salud del sistema: **cuánto pesa el almacén de WAHA**. Se sumó porque una vez
se disparó a 70 MB bajando 20 584 mensajes de historial y nadie lo vio hasta
que alguien se acordó de mirar. Vive en el OTRO droplet, así que se pregunta
por el puente que ya existe (ver `almacen_waha.py`).

Vive en control-stock y no en order-api por una razón que no se negocia:
el navegador amarra la suscripción de los avisos al **origen**. El celular
del dueño se suscribe en `inventario.plantaspanama.com`, así que su
suscripción está en la base de ESTA app. order-api también sabe mandar Web
Push, pero con las suscripciones de los repartidores, de otro origen: desde
allá no le llegaría nada.

El aviso lleva solo el titular y abre la pantalla `/resumen` con el
detalle. En un push no caben cinco bloques, y partirlo en cinco avisos
sería peor.

**Nada se inventa.** Cada bloque dice de dónde sale, y si su fuente no
contesta, el resumen lo DICE en vez de mostrar un cero que no es cierto —
un cero falso es peor que un hueco, porque parece una noticia buena.

Lo dispara un cron del droplet a las 19:00 de Panamá contra
`POST /avisos/resumen` con `RESUMEN_SECRETO`. Sin la variable el endpoint
responde 503 y no hace nada, igual que el barrido del frontend.
"""

import os
from datetime import datetime, timedelta

from . import (almacen_waha, avisos, calendario, crm_twenty, linear_leads,
               ventas)
from .datos import ZONA_PANAMA

# El horario de atención del vivero (Abraham, 25/09/2026). Aquí solo se usa
# para saber si el día estuvo cerrado; el domingo sin novedades no merece
# un aviso. `0` es lunes, como `date.weekday()`.
HORARIO = {0: ("08:00", "17:00"), 1: ("08:00", "17:00"), 2: ("08:00", "17:00"),
           3: ("08:00", "17:00"), 4: ("08:00", "17:00"), 5: ("08:00", "12:00")}


def hoy():
    return datetime.now(ZONA_PANAMA).date()


def cerrado(dia):
    """¿El vivero estuvo cerrado ese día? (domingo)."""
    return dia.weekday() not in HORARIO


def usuario_dueno():
    """A quién le llega el resumen. Uno solo, a propósito: es SU resumen."""
    return (os.environ.get("AVISOS_DUENO_USUARIO")
            or "admin@viverorose.com").strip().lower()


def _secreto():
    return (os.environ.get("RESUMEN_SECRETO") or "").strip()


def armado():
    """Sin el secreto el endpoint no corre: un resumen que cualquiera puede
    disparar es un resumen que cualquiera puede usar para sondear el
    negocio."""
    return bool(_secreto())


def credencial_valida(cabecera):
    """Compara el secreto sin filtrar por el tiempo que tarda."""
    import hmac
    esperado = _secreto()
    if not esperado:
        return False
    dado = (cabecera or "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(dado, esperado)


# ---------------------------------------------------------------------------
# El día, en hora de Panamá, traducido a lo que Odoo entiende
# ---------------------------------------------------------------------------

def _ventana_utc(dia):
    """(desde, hasta) del día en Panamá, como texto UTC para Odoo.

    Odoo guarda los `create_date` en UTC sin zona. Panamá no tiene horario
    de verano (siempre −05:00), pero la conversión se hace igual con la
    zona: escribir "+5 horas" a mano es la clase de atajo que se rompe el
    día que alguien cambia la zona del servidor.
    """
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=ZONA_PANAMA)
    fin = inicio + timedelta(days=1)
    from datetime import timezone
    fmt = "%Y-%m-%d %H:%M:%S"
    return (inicio.astimezone(timezone.utc).strftime(fmt),
            fin.astimezone(timezone.utc).strftime(fmt))


def _es_de_hoy(iso_texto, dia):
    try:
        cuando = datetime.fromisoformat(str(iso_texto).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return cuando.astimezone(ZONA_PANAMA).date() == dia


# ---------------------------------------------------------------------------
# Los cinco bloques
# ---------------------------------------------------------------------------

def _leads(dia):
    """(nuevos_por_origen, sin_resp, esperando) del equipo LEAD."""
    leads = linear_leads.listar()
    vivos = [l for l in leads if l["estado"] not in linear_leads.CERRADOS]

    nuevos = [l for l in leads if _es_de_hoy(l.get("creado"), dia)]
    por_origen = {}
    for lead in nuevos:
        por_origen[lead.get("origen") or "Desconocido"] = (
            por_origen.get(lead.get("origen") or "Desconocido", 0) + 1)

    sin_resp = [l for l in vivos if not l.get("resp")]

    esperando = {}
    for lead in vivos:
        if lead.get("te_toca"):
            esperando.setdefault(lead.get("resp") or "", []).append(lead)

    return {
        "nuevos": {
            "total": len(nuevos),
            "por_origen": sorted(por_origen.items(), key=lambda p: (-p[1], p[0])),
            "filas": [{"ref": l["ref"], "nombre": l["nombre"],
                       "origen": l.get("origen") or "Desconocido",
                       "interes": l.get("interes") or ""} for l in nuevos],
        },
        "sin_resp": {"total": len(sin_resp), "de": len(vivos),
                     "filas": [{"ref": l["ref"], "nombre": l["nombre"],
                                "estado": l["estado_nombre"]} for l in sin_resp]},
        "esperando": {
            "total": sum(len(v) for v in esperando.values()),
            # Sin dueño va PRIMERO: es el que nadie va a contestar, así que
            # es el que hay que ver antes. (`p[0] != ""` y no `== ""`: True
            # ordena después, y con el `==` los sin dueño caían al final.)
            "por_resp": [
                {"resp": resp or "", "leads": [_con_espera(l) for l in filas]}
                for resp, filas in sorted(esperando.items(),
                                          key=lambda p: (p[0] != "", p[0]))],
        },
    }


def _con_espera(lead):
    """El lead con hace cuánto que espera respuesta.

    Se cuenta desde el ÚLTIMO MENSAJE DEL CLIENTE (`mensajesWhatsapp.fecha`
    de Twenty), nunca desde el `updatedAt` del issue: cualquier toque
    nuestro reinicia ese campo, y por eso el barrido no lo usa tampoco.
    Si Twenty no contesta, el lead sale igual pero sin el tiempo — mejor un
    hueco que un número inventado.
    """
    ficha = {"ref": lead["ref"], "nombre": lead["nombre"],
             "espera_desde": "", "espera": "", "horas": None}
    desde = _ultimo_mensaje_del_cliente(lead)
    if not desde:
        return ficha
    ficha["espera_desde"] = crm_twenty._cuando_bonito(desde)
    try:
        cuando = datetime.fromisoformat(str(desde).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ficha
    horas = (datetime.now(ZONA_PANAMA) - cuando.astimezone(ZONA_PANAMA)) \
        .total_seconds() / 3600.0
    ficha["horas"] = round(horas, 1)
    ficha["espera"] = _hace_cuanto(horas)
    return ficha


def _hace_cuanto(horas):
    if horas < 1:
        minutos = max(1, int(horas * 60))
        return f"hace {minutos} min"
    if horas < 24:
        enteras = int(horas)
        return f"hace {enteras} hora" + ("s" if enteras != 1 else "")
    dias = int(horas // 24)
    return f"hace {dias} día" + ("s" if dias != 1 else "")


def _ultimo_mensaje_del_cliente(lead):
    """La fecha ISO del último mensaje ENTRANTE de ese lead, o ""."""
    if not crm_twenty.twenty_configurado():
        return ""
    from urllib.parse import quote
    try:
        persona = ""
        for campo, valor in (("linearIssueId", lead.get("id")),
                             ("codigoRef", lead.get("pp"))):
            if persona or not valor:
                continue
            j = crm_twenty._twenty(
                f"leadsWeb?filter={campo}[eq]:%22{quote(str(valor))}%22&limit=1")
            filas = (j.get("data") or {}).get("leadsWeb") or []
            persona = (filas[0].get("personaId") or "") if filas else ""
        if not persona:
            return ""
        # UN solo `filter` con `and(...)`. Dos `filter=` en la misma URL NO
        # se suman: el segundo pisa al primero, y Twenty devuelve el último
        # entrante de TODO el sistema —el de otra persona— igual para todos
        # los leads. Se vio en pruebas: los tres decían "hace 32 min", que
        # era el mensaje de un desconocido, mientras dos de ellos llevaban
        # esperando desde el día anterior.
        j = crm_twenty._twenty(
            "mensajesWhatsapp?filter=and(personaId[eq]:%22" + quote(persona)
            + "%22,direccion[eq]:%22ENTRANTE%22)"
            + "&order_by=fecha[DescNullsLast]&limit=1")
        mensajes = (j.get("data") or {}).get("mensajesWhatsapp") or []
        return (mensajes[0].get("fecha") or mensajes[0].get("createdAt") or "") \
            if mensajes else ""
    except Exception:
        return ""


def _dinero(dia):
    """(cotizaciones, pagos) de Odoo. Lanza si Odoo no contesta."""
    desde, hasta = _ventana_utc(dia)

    ordenes = ventas._ejecutar("sale.order", "search_read", [
        [("create_date", ">=", desde), ("create_date", "<", hasta),
         ("state", "!=", "cancel"),
         "|",
         ("client_order_ref", "=like", "PP-%"),
         ("opportunity_id.lead_ref", "=like", "PP-%")],
        ["name", "partner_id", "amount_total", "client_order_ref", "etapa_cobro"],
    ], {"limit": 100, "order": "id desc"})

    pagos = ventas._ejecutar("account.payment", "search_read", [
        [("create_date", ">=", desde), ("create_date", "<", hasta),
         ("state", "in", ("paid", "in_process", "posted"))],
        ["amount", "partner_id", "vivero_orden_id", "date", "journal_id"],
    ], {"limit": 100, "order": "id desc"})

    return (
        {"total": len(ordenes),
         "monto": round(sum(float(o.get("amount_total") or 0) for o in ordenes), 2),
         "filas": [{"orden": o.get("name") or "",
                    "cliente": (o.get("partner_id") or ["", ""])[1],
                    "total": float(o.get("amount_total") or 0),
                    "ref": o.get("client_order_ref") or "",
                    "cobro": o.get("etapa_cobro") or ""} for o in ordenes]},
        {"total": len(pagos),
         "monto": round(sum(float(p.get("amount") or 0) for p in pagos), 2),
         "filas": [{"monto": float(p.get("amount") or 0),
                    "cliente": (p.get("partner_id") or ["", ""])[1],
                    "orden": ((p.get("vivero_orden_id") or ["", ""])[1]
                              if p.get("vivero_orden_id") else ""),
                    "medio": ((p.get("journal_id") or ["", ""])[1]
                              if p.get("journal_id") else "")} for p in pagos]},
    )


def del_dia(dia=None):
    """Los cinco bloques del día. `errores` dice qué fuente no contestó."""
    dia = dia or hoy()
    datos = {"dia": dia.isoformat(), "dia_texto": calendario.dmy(dia.isoformat()),
             "cerrado": cerrado(dia), "errores": []}

    try:
        datos.update(_leads(dia))
    except Exception as fallo:
        datos["errores"].append(f"Linear no contestó: {fallo}")
        datos.update({"nuevos": None, "sin_resp": None, "esperando": None})

    if not ventas.configurado():
        datos.update({"cotizaciones": None, "pagos": None})
        datos["errores"].append("Odoo no está configurado en esta instancia.")
    else:
        try:
            cotizaciones, pagos = _dinero(dia)
            datos.update({"cotizaciones": cotizaciones, "pagos": pagos})
        except Exception as fallo:
            datos.update({"cotizaciones": None, "pagos": None})
            datos["errores"].append(f"Odoo no contestó: {fallo}")

    # El sexto: el almacén de WAHA, que vive en el droplet del CRM. Si el
    # puente no está o no contesta, el renglón queda en blanco y lo dice —
    # jamás en 0 MB, que es justo la mentira tranquilizadora que este
    # renglón existe para evitar.
    if not almacen_waha.configurado():
        datos["almacen"] = None
        datos["errores"].append(
            "El almacén de WhatsApp no se pudo leer: falta el puente con el "
            "droplet del CRM (SINCRO_URL y SINCRO_SECRET).")
    else:
        try:
            datos["almacen"] = almacen_waha.bloque(hoy_iso=dia.isoformat())
        except Exception as fallo:
            datos["almacen"] = None
            datos["errores"].append(
                f"El almacén de WhatsApp no se pudo leer: {str(fallo)[:160]}")

    return datos


# ---------------------------------------------------------------------------
# El titular del aviso
# ---------------------------------------------------------------------------

def hay_algo(datos):
    """¿Pasó algo que valga un aviso? Un día cerrado y en blanco, no."""
    for clave in ("nuevos", "cotizaciones", "pagos", "esperando"):
        bloque = datos.get(clave)
        if bloque and bloque.get("total"):
            return True
    # Un almacén disparado SÍ es novedad, aunque sea domingo: si nadie lo
    # ve, crece toda la semana. Un hueco no, en cambio — no saber no es una
    # noticia por la que valga despertar el teléfono.
    almacen = datos.get("almacen")
    return bool(almacen and almacen.get("alerta"))


def titular(datos):
    """La línea que cabe en la notificación. Los ceros no se nombran: en un
    titular, un `0 pagos` gasta el espacio que necesita lo que sí pasó."""
    partes = []
    n = datos.get("nuevos")
    if n and n["total"]:
        partes.append(f"{n['total']} nuevo" + ("s" if n["total"] != 1 else ""))
    c = datos.get("cotizaciones")
    if c and c["total"]:
        partes.append(f"{c['total']} cotizado" + ("s" if c["total"] != 1 else ""))
    p = datos.get("pagos")
    if p and p["total"]:
        partes.append(f"{p['total']} pago" + ("s" if p["total"] != 1 else "")
                      + f" ({_plata(p['monto'])})")
    s = datos.get("sin_resp")
    if s and s["total"]:
        partes.append(f"{s['total']} sin dueño")
    e = datos.get("esperando")
    if e and e["total"]:
        partes.append(f"{e['total']} esperando")
    # El almacén solo se nombra cuando está mal. Sano no gasta titular: el
    # número se ve en la pantalla, que es a donde lleva el aviso.
    a = datos.get("almacen")
    if a and a.get("corto"):
        partes.append(a["corto"])
    if datos.get("errores"):
        partes.append("con huecos")
    return " · ".join(partes) or "Día sin novedades."


def _plata(monto):
    return "$" + f"{float(monto or 0):,.2f}".replace(",", " ")


def mandar(dia=None):
    """Arma el resumen y lo manda al celular del dueño.

    Vuelve {"mandado", "motivo", "titular", "datos"}. No manda cuando el
    día estuvo cerrado y no pasó nada: un domingo en blanco no merece que
    le suene el teléfono.
    """
    datos = del_dia(dia)
    resultado = {"mandado": False, "motivo": "", "titular": titular(datos),
                 "datos": datos}

    if datos["cerrado"] and not hay_algo(datos):
        resultado["motivo"] = "domingo sin novedades: no se manda"
        return resultado
    if not avisos.configurado():
        resultado["motivo"] = "sin claves VAPID: el resumen queda en el log"
        return resultado
    if not avisos.cuantos(usuario_dueno()):
        resultado["motivo"] = (f"{usuario_dueno()} no tiene ningún celular "
                               f"con los avisos activados")
        return resultado

    avisos.avisar(usuario_dueno(), f"Resumen · {datos['dia_texto']}",
                  resultado["titular"], "/resumen?dia=" + datos["dia"])
    resultado.update({"mandado": True, "motivo": "mandado"})
    return resultado
