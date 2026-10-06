"""Mi CRM — el tablero chico de cada responsable (BLOQUE 20, esqueleto).

ESQUELETO NAVEGABLE (BLOQUE 25, 6/10/2026): esta pantalla abre con datos
REALES en solo lectura; lo que todavía no funciona es un botón APAGADO
con «Todavía no». Regla dura de Abraham: nada que parezca funcionar y no
guarde — acá no hay ni un POST.

Qué es — y qué no:

- **El CRM chico de Rubén y Mary** (diseño: pantallas
  ruben-gerente-de-operaciones-mi-crm / mary-atencion-al-cliente-mi-crm
  de docs/diseno-roles/): 4 columnas — Cotizado · Por agendar · Agendado
  · Ganado — con SOLO las tarjetas del usuario en sesión. Sin Nuevo ni
  Hablando: esos son del Director (/control, que queda como está).
- **El responsable sale de donde siempre**: la etiqueta `Resp:` de
  Linear, casada con la sesión por `agenda.responsable_de_empleada()` —
  el mismo casamiento de `control.alcance()`. Nada nuevo decide quién es
  quién.
- **Nada se inventa**: sin Linear (token neutralizado en pruebas) el
  tablero es la muestra QA de la casa y la pantalla LO DICE; si la
  sesión no casa con ninguna `Resp:`, las columnas quedan vacías y se
  dice por qué; si Twenty no está conectado, la caja «Por responder» lo
  dice en vez de fingir una lista vacía.
- **Las cajas «Peticiones», «Por responder», «Seguimientos» y «Aceptadas
  · falta cotizar» son la ESTRUCTURA del diseño con su estado hueco
  honesto**: las peticiones llegan con su propia parte (BLOQUE 20), el
  cálculo de «Por responder» con la suya (BLOQUE 24.2) y los
  seguimientos con fecha viven en la rama senales-ficha. Hoy declaran su
  lugar, no muestran números fabricados.
- **El celular son dos pestañas** («Por hacer» / «Tablero», pantallas
  ruben-celular-*): server-rendered con enlaces GET (?pestana=,
  ?etapa=), cero JS nuevo (regla 10).
- SOLO LECTURA hacia Linear/Twenty/Odoo. Este módulo no escribe nada.
"""

from . import agenda, colores, control, crm_twenty, linear_leads

# Las 4 columnas del CRM chico (BLOQUE 20: «Solo lo suyo. Columnas:
# Cotizado, Por agendar, Agendado, Ganado. Sin Nuevo ni Hablando»).
COLUMNAS = ("COTIZADO", "POR_AGENDAR", "AGENDADO", "GANADO")

# BLOQUE 29 (decisión de Abraham, 6/10/2026): todo «Ver chat» del diseño
# apunta a la ruta FUTURA de chat individual por lead — la forma
# canónica es /chat/<ref-del-lead>. HOY la ruta NO existe (nace en el
# punto 2/3 de roles, con candado de propiedad server-side), así que los
# botones van DISABLED con «Todavía no» y el destino viaja en data-href
# del markup: al encenderla no habrá que retocar plantillas.
URL_CHAT = "/chat/{ref}"


def url_chat(ref):
    """La forma canónica del chat de un lead (BLOQUE 29). La usan las
    filas de «Por responder», el panel «Hoy» del calendario y la lista
    «Para leer» de Respuestas — un solo lugar para el formato."""
    return URL_CHAT.format(ref=(ref or "").strip())

# Los pies de columna del diseño, tal cual el lienzo.
PIES = {
    "COTIZADO": "Sin pago todavía.",
    "POR_AGENDAR": "Ya pagaron o abonaron. Falta la fecha.",
    "AGENDADO": "Con fecha y contigo a cargo.",
    "GANADO": "Entregado y pagado.",
}

# Los avisos honestos, en un solo lugar (las pruebas y el panel «Hoy»
# del calendario los reusan).
AVISO_MUESTRA = ("Linear no está conectado en esta instancia: lo que ves "
                 "es el tablero de MUESTRA de pruebas, no leads reales.")
AVISO_SIN_RESP = ("Tu usuario no casa con ninguna etiqueta Resp: de "
                  "Linear, así que este tablero chico sale vacío. El CRM "
                  "de todos sigue en Control.")
AVISO_TWENTY_PRUEBAS = ("Twenty no está conectado en esta instancia: los "
                        "chats no se pueden leer.")
AVISO_RESPONDER_LUEGO = ("El cálculo de «Por responder» (con las horas de "
                         "Twenty y el umbral de Ajustes) llega con su "
                         "propia parte. Esta caja solo muestra su lugar.")
AVISO_SEGUIMIENTOS = ("Los seguimientos con fecha y nota llegan con su "
                      "propia parte. Esta caja solo muestra su lugar.")
AVISO_PETICIONES = "Sin peticiones. Esta parte llega después."
AVISO_FALTA_COTIZAR = ("Sin aceptadas por cotizar. Las peticiones llegan "
                       "después.")

VACIO_MUESTRA = "Sin datos en pruebas."
VACIO = "Nada por aquí."


def caja_por_responder():
    """La caja «Por responder»: hoy SOLO estructura, con su hueco dicho.
    Si Twenty no está conectado (el 8095 tiene el token neutralizado), la
    caja dice eso; si está, dice que el cálculo llega con su parte. Nunca
    una lista vacía que mienta."""
    if not crm_twenty.twenty_configurado():
        return {"filas": [], "aviso": AVISO_TWENTY_PRUEBAS}
    return {"filas": [], "aviso": AVISO_RESPONDER_LUEGO}


def caja_seguimientos():
    return {"filas": [], "aviso": AVISO_SEGUIMIENTOS}


def panel_hoy():
    """Lo que el panel «Hoy» de /calendario pinta además de las
    actividades del día (que la pantalla ya tiene en `movil`): las dos
    cajas-hueco y el aviso de peticiones — hoy 0 fijo, con su verdad."""
    return {
        "por_responder": caja_por_responder(),
        "seguimientos": caja_seguimientos(),
        "peticiones": 0,
        "texto_peticiones": ("las peticiones llegan después; se aceptarán "
                             "solo en el CRM"),
    }


def _tarjeta(lead):
    """El lead listo para la tarjeta chica: lo que se ve y nada más.
    Solo lectura — el enlace abre la ficha EXISTENTE de Control."""
    return {
        "ref": lead.get("ref") or "",
        "nombre": lead.get("nombre") or lead.get("ref") or "—",
        "interes": lead.get("interes") or "",
        "interes_chip": colores.chip_interes(lead.get("interes")),
        "interes_color": colores.color_interes(lead.get("interes")),
        "pago": lead.get("pago") or "",
        "pago_clase": control._clase_pago(lead.get("pago") or ""),
        "hace": lead.get("hace") or "",
        "te_toca": bool(lead.get("te_toca")),
        "href": "/control?abrir=" + (lead.get("ref") or ""),
    }


def vista(empleada, es_admin, pestana="", etapa=""):
    """TODO lo que la plantilla pinta, ya decidido (regla 10).

    `es_admin` viaja solo por honestidad del rótulo: el filtro es el
    MISMO para todos — la etiqueta `Resp:` que casa con la sesión. El
    tablero de todos sigue siendo /control (ahí `alcance()` decide)."""
    resp = agenda.responsable_de_empleada(empleada)
    modo = linear_leads.modo()
    aviso_fuente = AVISO_MUESTRA if modo == "muestra" else ""
    try:
        leads = linear_leads.listar()
    except Exception as fallo:
        leads = []
        aviso_fuente = ("Linear no contestó: el tablero no se pudo "
                        f"cargar ({fallo}). Nada se inventa.")

    mios = [l for l in leads if resp and (l.get("resp") or "") == resp]
    vacio = VACIO_MUESTRA if modo == "muestra" else VACIO
    columnas = []
    for clave in COLUMNAS:
        ficha = linear_leads.POR_CLAVE[clave]
        tarjetas = [_tarjeta(l) for l in mios if l.get("estado") == clave]
        columnas.append({
            "clave": clave,
            "titulo": ficha["nombre"],
            "pie": PIES[clave],
            "tarjetas": tarjetas,
            "cuenta": len(tarjetas),
            "vacio": vacio,
            # El único botón del diseño en estas tarjetas es «Agendar»
            # (columna Por agendar): acá va APAGADO — agendar de verdad
            # sigue viviendo en el calendario.
            "boton_apagado": "Agendar" if clave == "POR_AGENDAR" else "",
        })

    # El celular: dos pestañas por GET, una etapa a la vez en «Tablero».
    pestana = "tablero" if (pestana or "").strip() == "tablero" else "hacer"
    etapa = etapa if etapa in COLUMNAS else COLUMNAS[0]
    columna_movil = next(c for c in columnas if c["clave"] == etapa)

    return {
        "resp": resp,
        "es_admin": bool(es_admin),
        "aviso_fuente": aviso_fuente,
        "aviso_resp": "" if resp else AVISO_SIN_RESP,
        "columnas": columnas,
        "total_tablero": sum(c["cuenta"] for c in columnas),
        "por_responder": caja_por_responder(),
        "seguimientos": caja_seguimientos(),
        "peticiones": {"filas": [], "aviso": AVISO_PETICIONES},
        "falta_cotizar": {"filas": [], "aviso": AVISO_FALTA_COTIZAR},
        "pestana": pestana,
        "etapa": etapa,
        "columna_movil": columna_movil,
    }
