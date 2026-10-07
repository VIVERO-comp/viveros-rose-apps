"""Contactos — las tres pantallas SOLO LECTURA (BLOQUE 37 + BLOQUE 43).

Diseño corto aprobado: docs/DISENO-ITEM2-contactos.md del repo
plantaspanama; lienzos en docs/diseno-roles/pantallas/
abraham-contactos.html (la lista, a TODO EL ANCHO: tocar un contacto abre
su página, no un panel al costado), abraham-contacto-abierto.html (la
página del contacto, estilo ficha de Odoo: datos a la izquierda y un panel
derecho con dos pestañas) y contacto-whatsapp.html (la pestaña del chat).

El BLOQUE 43 suma tres cosas al módulo:

- **Las dos pestañas del panel derecho** («Historial de leads», la que
  abre, y «WhatsApp»), que son ENLACES GET decididos en Python
  (`?panel=leads|whatsapp`) — cero JS nuevo, regla 10.
- **El candado del DINERO y del CHAT** (`_permiso`): los ve quien atiende
  al contacto (su `Resp:` casa con el usuario en sesión), el director y
  finanzas. Para los demás la página abre igual y esas DOS zonas dicen de
  quién son, SIN montos y SIN hilo — y el recorte se hace **en Python,
  antes de la plantilla**, así que lo tapado no viaja en el HTML.
- **Los apagados del lienzo** (`APAGADOS`), presentes en su lugar con
  «Todavía no»: ningún número inventado para ellos.

Qué es — y qué no:

- **Twenty es la fuente de verdad de la PERSONA** (el modelo de la casa:
  «Twenty = la ficha del cliente»). En el 8095 su token está neutralizado,
  así que ese lado se muestra como HUECO HONESTO («no conectado en
  pruebas») y la lista funciona igual con lo que sí hay: los partner de
  Odoo (que no se vuelven contactos: se CASAN) y los clientes locales de
  Vender (ventas_locales y cotizaciones_servicio).
- **El casamiento es EN LECTURA**, calculado en Python al armar la vista,
  por teléfono normalizado (solo dígitos, sin el 507 inicial — el mismo
  normalizador conceptual del resto de la casa). Un contacto = la UNIÓN
  de sus apariciones. **Nada se escribe en ningún lado para «unir»**: la
  unión es una vista. El nombre plano SOLO sugiere («posible mismo»),
  jamás amarra.
- **Ningún código crea una Person solo**: «+ Nuevo contacto» y «Nuevo
  lead para este contacto» nacen APAGADOS con «Todavía no» (regla dura de
  Abraham: nada que parezca funcionar y no guarde). Este módulo no
  registra ni una ruta POST — hay prueba que recorre app.routes.
- **«Con venta»** = tiene venta local (no cancelada) u orden CONFIRMADA
  en Odoo. Se cuenta desde lo ya leído (una pasada por sale.order),
  sin segunda pasada cara; la lectura de Odoo se cachea como hace
  pedidos.py con la plata: con Odoo caído se sirve la última lectura
  buena DICIENDO su edad — jamás una lista vacía fingida.
- **El responsable del contacto no existe como dato hoy** (vive por lead
  en la etiqueta `Resp:` de Linear, no por contacto): la columna sale
  vacía honesta y el filtro «Sin responsable» va APAGADO con «Todavía
  no» — no se inventa.
- SOLO LECTURA hacia Odoo/Twenty/Linear. Este módulo no escribe nada,
  ni siquiera local.

El BLOQUE 53 (7/10/2026) suma tres cosas más, las tres MEDIDAS contra el
proceso del 8095 antes de escribirlas:

- **A7 · «todo lead debe tener su contacto».** El buscador por teléfono ya
  normalizaba la consulta Y el dato (`normalizar_telefono`: solo dígitos,
  sin 507 inicial), y medido en el 8095 encuentra con guiones, con
  espacios, con `+507` y con `00507`. Los 0 resultados que vio Abraham NO
  eran del buscador: eran de la LISTA. Salía solo de Odoo + los clientes
  locales de Vender, así que **66 de 83 leads del CRM no tenían fila**
  (22 sin teléfono y 44 cuyo número no casaba con ningún partner ni
  cliente local). Desde aquí **el lead de Linear es una TERCERA FUENTE**
  de la lista, casada por el mismo teléfono normalizado: un lead que ya
  casa con un partner o con un cliente local se UNE a esa fila (no la
  duplica), y uno que no casa con nada estrena su propia fila con fuente
  «CRM». Si Linear no se puede leer, esas filas no están y **la pantalla
  lo dice** — jamás un contacto inventado.
- **A8 · las cuentas de SISTEMA fuera de la lista** (`_ids_de_sistema`),
  con un criterio estructural y conservador, nunca una lista de nombres:
  ver el docstring de esa función. Si la comprobación no se puede hacer,
  **no se saca a nadie** y el aviso lo dice.
- **A14 · la página del contacto como el lienzo**: fuera «Gastos y
  compras / Total gastado»; dentro «Cuándo nos pagan» y las TRES tablas
  (Cotizado · Facturado · Pagado), que salen de las facturas reales de
  Odoo (`account.move`) del partner casado. «Citas» se queda. Con Odoo
  sin configurar o caído los tres números salen «sin dato» y el hueco se
  dice — nunca un $0 fingido.
"""

import os
import re
import time

from . import (agenda, control, cotizaciones, crm_twenty, datos_roles,
               linear_leads, ventas)

# ---------------------------------------------------------------------------
# Los avisos honestos, en un solo lugar (las pruebas los reusan)
# ---------------------------------------------------------------------------

AVISO_TWENTY_PRUEBAS = ("Twenty no está conectado en pruebas: la ficha del "
                        "cliente y el último mensaje no se pueden leer.")
AVISO_TWENTY_LUEGO = ("La lectura de Twenty (la ficha del cliente y el "
                      "último mensaje) llega con su propia parte.")
AVISO_SIN_ODOO = ("Odoo no está configurado en esta instancia: la lista "
                  "sale solo de los clientes locales de Vender.")
AVISO_CITAS_LUEGO = ("Las citas del calendario de este contacto llegan "
                     "con su propia parte.")
AVISO_SIN_TRATOS = "Sin cotizaciones ni ventas con nosotros todavía."
VACIO_LISTA = "Ningún contacto con lo que hay en las fuentes de hoy."

# --- A7: el lead como tercera fuente de la lista ---------------------------
AVISO_CRM_HUECO = ("Los leads del CRM no se pudieron leer, así que los "
                   "contactos que solo existen como lead no están en la "
                   "lista.")

# --- A8: las cuentas de sistema fuera de la lista --------------------------
AVISO_SISTEMA_SIN_COMPROBAR = ("No se pudo comprobar cuáles partner de Odoo "
                               "son del sistema, así que no se sacó a "
                               "ninguno de la lista.")

# --- A14: «Cuándo nos pagan» y las tres tablas ----------------------------
AVISO_FACTURAS_CAIDO = ("Las facturas de este contacto no se pudieron leer, "
                        "así que lo facturado y lo cobrado salen sin dato.")
AVISO_FACTURAS_SIN_ODOO = ("Sin Odoo no hay facturas que leer: lo facturado "
                           "y lo cobrado salen sin dato.")
SIN_PARTNER_FACTURAS = ("Este contacto todavía no es un cliente de Odoo, así "
                        "que no tiene ninguna factura.")
SIN_COTIZADO = "Nada cotizado pendiente de facturar."
SIN_FACTURADO = "Nada facturado pendiente de cobro."
SIN_PAGADO = "Todavía no se le ha cobrado nada a este contacto."
TODO_COBRADO = ("Todo lo facturado a este contacto ya está cobrado, así que "
                "no hay fecha de cobro.")

# --- la pestaña «Historial de leads» ---------------------------------------
AVISO_LINEAR_PRUEBAS = ("Linear no está conectado en pruebas: los leads de "
                        "este contacto no se pueden leer.")
AVISO_LINEAR_CAIDO = ("Linear no contestó: los leads de este contacto no se "
                      "pudieron leer.")
SIN_LEADS = "Este contacto todavía no tiene ningún lead en el tablero."
SIN_TELEFONO_LEADS = ("Sin teléfono no hay con qué casar: los leads se buscan "
                      "por el número del contacto.")
PIE_LEADS = ("Verde: leads activos. Gris: terminados y los que nunca se "
             "activaron. Toca uno para abrirlo en el CRM.")

# --- la pestaña «WhatsApp» -------------------------------------------------
AVISO_CHAT_PRUEBAS = ("Twenty no está conectado en pruebas: el chat de "
                      "WhatsApp de este contacto no se puede leer.")
AVISO_CHAT_CAIDO = ("Twenty no contestó: el chat de este contacto no se pudo "
                    "cargar.")
SIN_CHAT = "No hay mensajes de WhatsApp de este contacto en Twenty."
SIN_TELEFONO_CHAT = ("Sin teléfono no hay chat que buscar: este contacto no "
                     "tiene número.")
PIE_CHAT = ("Solo lectura. Se responde desde WhatsApp y el mensaje aparece "
            "aquí solo.")

# --- el candado del dinero y del chat (frente D) ---------------------------
CANDADO_AJENO = ("El dinero y el chat de este contacto son de otro "
                 "responsable.")
CANDADO_SIN_MIO = ("Este contacto no tiene ningún lead a tu nombre: su dinero "
                   "y su chat no se muestran.")
CANDADO_SIN_FUENTE = ("No se pueden leer los leads, así que no se sabe quién "
                      "atiende a este contacto: su dinero y su chat no se "
                      "muestran.")
CANDADO_SIN_SESION = ("Sin sesión no se muestran ni el dinero ni el chat de "
                      "un contacto.")

# Lo que todavía NO existe, presente en su lugar del lienzo y apagado. Ni un
# número inventado para ninguno (frente C del BLOQUE 43).
TODAVIA_NO = "Todavía no"
APAGADOS_ACCION = ("Mandar petición", "Reasignar",
                   "Poner o cambiar seguimiento")
APAGADOS_PLATA = ("Gastado", "Ganancia")
# «Gastos y compras / Total gastado» SALIÓ de la página el 7/10/2026
# (BLOQUE 53, A14: lo pidió Abraham). No volver a ponerlo sin su palabra:
# los dos apagados de plata que quedan son los del lienzo, arriba.

# Las dos pestañas del panel derecho, en su orden: abre la primera.
PANELES = (("leads", "Historial de leads"), ("whatsapp", "WhatsApp"))
PANEL_POR_DEFECTO = PANELES[0][0]

# Cuántos mensajes se traen del chat. El mismo orden de magnitud del hilo
# de la ficha del lead (60): es una supervisión, no un archivo.
MENSAJES_DEL_CHAT = 60

# Los filtros como enlaces GET (?f=). «Sin responsable» NO está aquí:
# va aparte, APAGADO, porque el dato no existe todavía en ninguna fuente.
FILTROS = (
    ("todos", "Todos"),
    ("con_venta", "Con venta"),
    ("sin_venta", "Sin venta"),
    ("empresas", "Empresas"),
)
FILTRO_APAGADO = "Sin responsable"

# Estados de sale.order que cuentan como VENTA (confirmada) y como
# COTIZACIÓN abierta. 'cancel' no cuenta en ningún lado.
_ESTADOS_VENTA = ("sale", "done")
_ESTADOS_COTIZACION = ("draft", "sent")


def normalizar_telefono(crudo):
    """Solo dígitos, sin el 507 inicial — el casamiento del diseño corto.

    «+507 6000-0001» → «60000001»; «0050760000001» → «60000001»;
    «60000001» queda igual. Un fijo de 7 dígitos que empiece en 507 NO se
    recorta (solo se quita el prefijo cuando lo que queda es un número
    completo)."""
    digitos = re.sub(r"\D", "", str(crudo or ""))
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if digitos.startswith("507") and len(digitos) >= 10:
        digitos = digitos[3:]
    return digitos


def _nombre_plano(nombre):
    """El nombre para SUGERIR «posible mismo»: minúsculas y espacios
    plegados. Solo sugiere — jamás amarra dos contactos."""
    return " ".join(str(nombre or "").lower().split())


# ---------------------------------------------------------------------------
# Odoo: una lectura (partners + plata por partner), con la última buena
# ---------------------------------------------------------------------------

_cache_odoo = {"en": None, "partners": [], "plata": {}, "sistema": 0,
               "aviso_sistema": ""}


def reiniciar_cache():
    """Solo para pruebas."""
    _cache_odoo["en"] = None
    _cache_odoo["partners"] = []
    _cache_odoo["plata"] = {}
    _cache_odoo["sistema"] = 0
    _cache_odoo["aviso_sistema"] = ""


def _ids_de_sistema():
    """(ids, aviso) — los partner de Odoo que NO son el contacto de nadie.

    **A8 del BLOQUE 53.** El criterio es ESTRUCTURAL y conservador: nada
    de una lista de nombres a dedo (un nombre raro puede ser un cliente de
    verdad, y una lista a dedo envejece sola). Dos preguntas, las dos
    sobre el papel del registro en Odoo, no sobre cómo se llama:

    1. **¿Ese partner es un USUARIO del sistema?** (`res.users`, incluidos
       los archivados). Son los administradores y las cuentas de las apps
       —«Administrator», «ADMINISTRADOR WEB», «App Ventas», «ORDER-API»,
       «App recepcion»—. Un cliente de verdad **nunca** entra a Odoo, así
       que esta pregunta no puede sacar a ninguno.
    2. **¿Vino instalado con un módulo?** (tiene xmlid en `ir.model.data`).
       Así nacen la compañía, OdooBot, el «Public user» y los registros de
       demostración. Un cliente lo crea una persona o la app, y eso **no
       deja xmlid**: esta pregunta tampoco puede sacar a ninguno.

    Y de yapa, por si una compañía no trajera xmlid, el partner de cada
    `res.company`: la empresa no es cliente de sí misma.

    **Si alguna de las tres lecturas falla, no se saca a NADIE** y el
    aviso lo dice. Tapar un cliente por un error de lectura sería peor que
    mostrar un administrador.

    Lo que este criterio NO saca, y se dice para que no sorprenda: las
    filas de PRUEBA cargadas a mano en la base de pruebas (nombres tipo
    «QA …», «Prueba …», «cliente_relleno», «EJEMPLO»). Son partner
    normales, creados como se crea un cliente: no hay una sola señal
    estructural que las separe de uno real, y sacarlas por su nombre es
    justo la lista a dedo que la regla prohíbe. Viven en Odoo PRUEBAS, no
    en producción.
    """
    ids = set()
    try:
        usuarios = ventas._ejecutar(
            "res.users", "search_read", [[]],
            {"fields": ["partner_id"], "limit": 1000,
             "context": {"active_test": False}})
        ids.update(u["partner_id"][0] for u in usuarios if u.get("partner_id"))
        xmlids = ventas._ejecutar(
            "ir.model.data", "search_read",
            [[["model", "=", "res.partner"]]],
            {"fields": ["res_id"], "limit": 5000})
        ids.update(x["res_id"] for x in xmlids if x.get("res_id"))
        companias = ventas._ejecutar(
            "res.company", "search_read", [[]],
            {"fields": ["partner_id"], "limit": 50})
        ids.update(c["partner_id"][0] for c in companias
                   if c.get("partner_id"))
    except Exception as fallo:
        return set(), f"{AVISO_SISTEMA_SIN_COMPROBAR} ({fallo})"
    return ids, ""


def _leer_odoo():
    """Los res.partner activos con nombre —menos las cuentas de sistema
    (A8)—, y la plata por partner contada de UNA pasada por sale.order
    (sin canceladas) — nunca una consulta por contacto.

    Devuelve (partners, plata, sistema_n, aviso_sistema)."""
    partners = ventas._ejecutar(
        "res.partner", "search_read",
        [[["active", "=", True], ["name", "!=", False]]],
        {"fields": ["name", "phone", "is_company"], "limit": 2000})
    de_sistema, aviso_sistema = _ids_de_sistema()
    if de_sistema:
        antes = len(partners)
        partners = [p for p in partners if p["id"] not in de_sistema]
        sistema_n = antes - len(partners)
    else:
        sistema_n = 0
    ordenes = ventas._ejecutar(
        "sale.order", "search_read",
        [[["partner_id", "!=", False], ["state", "!=", "cancel"]]],
        {"fields": ["partner_id", "amount_total", "state"], "limit": 4000})
    plata = {}
    for orden in ordenes:
        pid = orden["partner_id"][0]
        fila = plata.setdefault(pid, {"ventas_n": 0, "ventas_total": 0.0,
                                      "cotiz_n": 0, "cotiz_total": 0.0})
        monto = float(orden.get("amount_total") or 0)
        if orden.get("state") in _ESTADOS_VENTA:
            fila["ventas_n"] += 1
            fila["ventas_total"] += monto
        elif orden.get("state") in _ESTADOS_COTIZACION:
            fila["cotiz_n"] += 1
            fila["cotiz_total"] += monto
    return partners, plata, sistema_n, aviso_sistema


def _odoo_vacio(aviso):
    return {"partners": [], "plata": {}, "ok": False, "aviso": aviso,
            "sistema": 0, "aviso_sistema": ""}


def datos_odoo():
    """{'partners', 'plata', 'ok', 'aviso', 'sistema', 'aviso_sistema'}.
    Nada se inventa: sin configuración o con Odoo caído y sin lectura
    previa, ok=False y el aviso lo dice; con una lectura buena anterior se
    sirve ESA con su edad en minutos (el patrón de pedidos.plata).

    `sistema` es cuántas cuentas de sistema quedaron fuera (A8) y
    `aviso_sistema` el hueco cuando esa comprobación no se pudo hacer."""
    if not ventas.configurado():
        return _odoo_vacio(AVISO_SIN_ODOO)
    try:
        partners, plata, sistema_n, aviso_sistema = _leer_odoo()
    except Exception as fallo:
        if _cache_odoo["en"] is not None:
            edad = max(0, int((time.time() - _cache_odoo["en"]) // 60))
            return {"partners": _cache_odoo["partners"],
                    "plata": _cache_odoo["plata"], "ok": True,
                    "sistema": _cache_odoo["sistema"],
                    "aviso_sistema": _cache_odoo["aviso_sistema"],
                    "aviso": (f"Odoo no contestó: mostrando la última "
                              f"lectura buena, de hace {edad} min.")}
        return _odoo_vacio(f"Odoo no contestó: la lista sale solo de los "
                           f"clientes locales de Vender ({fallo}).")
    _cache_odoo["en"] = time.time()
    _cache_odoo["partners"] = partners
    _cache_odoo["plata"] = plata
    _cache_odoo["sistema"] = sistema_n
    _cache_odoo["aviso_sistema"] = aviso_sistema
    return {"partners": partners, "plata": plata, "ok": True, "aviso": "",
            "sistema": sistema_n, "aviso_sistema": aviso_sistema}


def twenty_conectado():
    """¿Twenty se puede leer DE VERDAD?

    `crm_twenty.twenty_configurado()` solo mira que la variable EXISTA, y
    en el 8095 existe con el valor NEUTRALIZADO: arranca con «CLAVE», que
    es la convención de la casa para «esto no es una key». Medido contra
    el proceso del 8095 (6/10/2026): sin esta distinción la pantalla
    decía que Twenty estaba conectado justo donde no lo está."""
    if not crm_twenty.twenty_configurado():
        return False
    valor = (os.environ.get("TWENTY_API_KEY") or "").strip()
    return not valor.upper().startswith("CLAVE")


def aviso_twenty():
    """El lado Twenty, dicho con la verdad: en el 8095 el token está
    neutralizado (no conectado); conectado de verdad, la lectura llega
    con su propia parte. Nunca una columna vacía que mienta."""
    if not twenty_conectado():
        return AVISO_TWENTY_PRUEBAS
    return AVISO_TWENTY_LUEGO


def linear_conectado():
    """¿Linear se puede leer DE VERDAD? La MISMA pregunta que
    `twenty_conectado()`, por el mismo motivo.

    `linear_leads.configurado()` solo mira que `LINEAR_API_KEY` exista, y
    una key neutralizada (la convención de la casa: arranca con «CLAVE»)
    existe igual. Importa el doble aquí porque
    **`linear_leads.listar()` devuelve leads DE MUESTRA cuando no hay
    key**: sin esta pregunta la pestaña pintaría leads de ejemplo con
    pinta de reales, que es justo lo que no puede pasar (medido en el
    8095 el 6/10/2026: allá la key de Linear SÍ es real y
    `CALENDARIO_ESCRITURA=0`, o sea lectura; la neutralizada es la de
    Twenty)."""
    if not linear_leads.configurado():
        return False
    valor = (os.environ.get("LINEAR_API_KEY") or "").strip()
    return not valor.upper().startswith("CLAVE")


# ---------------------------------------------------------------------------
# Las fuentes locales (Vender)
# ---------------------------------------------------------------------------

def _locales():
    """Las apariciones locales: ventas_locales y cotizaciones_servicio,
    cada una con su href a la ficha EXISTENTE (/venta/estado/...). Una
    venta cancelada aparece como aparición (la persona existe) pero NO
    cuenta como venta."""
    filas = []
    for v in ventas.ventas_todas():
        filas.append({
            "origen": "venta", "n": v["n"],
            "cliente": (v.get("cliente") or "").strip(),
            "celular": v.get("celular") or "",
            "orden": (v.get("orden") or "").strip(),
            "total": v.get("total"),
            "titulo": "Venta de plantas",
            "es_venta": (v.get("estado") or "") != "cancelada",
            "cancelada": (v.get("estado") or "") == "cancelada",
            "creado_en": v.get("creado_en") or "",
            "href": f"/venta/estado/venta/{v['n']}",
        })
    for c in cotizaciones.cotizaciones_todas():
        filas.append({
            "origen": "servicio", "n": c["n"],
            "cliente": (c.get("cliente") or "").strip(),
            "celular": c.get("celular") or "",
            "orden": (c.get("orden") or "").strip(),
            "total": c.get("total"),
            "titulo": ("Cotización de servicio · "
                       + cotizaciones.etiqueta_de(c.get("tipo"))),
            "es_venta": False,
            "cancelada": False,
            "creado_en": c.get("creado_en") or "",
            "href": f"/venta/estado/servicio/{c['n']}",
        })
    return filas


# ---------------------------------------------------------------------------
# La unión EN LECTURA: un contacto = sus apariciones casadas por teléfono
# ---------------------------------------------------------------------------

def _contacto_nuevo(cid, tel_norm):
    return {
        "id": cid, "tel_norm": tel_norm, "nombre": "", "telefono": "",
        "tipo": "Persona", "partner_ids": [], "locales": [], "leads": [],
        "fuentes": [],  # se arma al final: Odoo / Local / CRM
    }


def _clave_de_lead(lead):
    """La clave de un lead SIN teléfono: su ref de Linear, que es única y
    estable (LEAD-62 → «ldLEAD-62»). Sin ref —que no pasa— su id."""
    ref = (lead.get("ref") or lead.get("id") or "").strip()
    return "ld" + re.sub(r"[^A-Za-z0-9_-]", "", ref)


def _unir(od, leads=()):
    """La lista unificada, calculada al armar la vista (casamiento EN
    LECTURA, sin escribir NADA). Clave: el teléfono normalizado; sin
    teléfono cada aparición queda como su propio contacto (el nombre
    plano no amarra — solo sugerirá «posible mismo» en la ficha).

    TRES fuentes desde el BLOQUE 53 (A7, «todo lead debe tener su
    contacto»): los partner de Odoo, los clientes locales de Vender y
    **los leads del CRM**. Los leads van al final a propósito, para que el
    nombre que manda siga siendo el de Odoo y después el local: el título
    de un issue de Linear es el nombre tal como lo escribió quien atendió
    el chat, y la ficha de Odoo suele estar más cuidada."""
    contactos = {}

    def tomar(clave, tel_norm):
        if clave not in contactos:
            contactos[clave] = _contacto_nuevo(clave, tel_norm)
        return contactos[clave]

    for p in od["partners"]:
        tel_norm = normalizar_telefono(p.get("phone"))
        clave = f"t{tel_norm}" if tel_norm else f"o{p['id']}"
        c = tomar(clave, tel_norm)
        c["partner_ids"].append(p["id"])
        # El nombre de Odoo manda sobre el texto libre local.
        if not c["nombre"] or not c.get("_nombre_odoo"):
            c["nombre"] = (p.get("name") or "").strip() or c["nombre"]
            c["_nombre_odoo"] = True
        if p.get("is_company"):
            c["tipo"] = "Empresa"
        if not c["telefono"] and p.get("phone"):
            c["telefono"] = str(p["phone"]).strip()

    for fila in _locales():
        tel_norm = normalizar_telefono(fila["celular"])
        if tel_norm:
            clave = f"t{tel_norm}"
        else:
            prefijo = "lv" if fila["origen"] == "venta" else "ls"
            clave = prefijo + str(fila["n"])
        c = tomar(clave, tel_norm)
        c["locales"].append(fila)
        if not c["nombre"]:
            c["nombre"] = fila["cliente"] or "—"
        if not c["telefono"] and fila["celular"]:
            c["telefono"] = str(fila["celular"]).strip()

    # La TERCERA fuente (A7): el lead del CRM. Un lead cuyo número ya casa
    # con un partner o con un cliente local se UNE a esa fila; uno que no
    # casa con nada estrena la suya, para que ningún lead quede sin
    # contacto. No aporta un centavo: la plata sigue saliendo de Odoo y de
    # lo local.
    for lead in leads:
        tel_norm = normalizar_telefono(lead.get("celular"))
        clave = f"t{tel_norm}" if tel_norm else _clave_de_lead(lead)
        c = tomar(clave, tel_norm)
        c["leads"].append(lead.get("ref") or "")
        if not c["nombre"] or c["nombre"] == "—":
            c["nombre"] = (lead.get("nombre") or "").strip() or c["nombre"]
        if not c["telefono"] and lead.get("celular"):
            c["telefono"] = str(lead["celular"]).strip()

    lista = []
    for c in contactos.values():
        c.pop("_nombre_odoo", None)
        c["nombre"] = c["nombre"] or "—"
        fuentes = []
        if c["partner_ids"]:
            fuentes.append("Odoo")
        if c["locales"]:
            fuentes.append("Local")
        if c["leads"]:
            fuentes.append("CRM")
        c["fuentes"] = fuentes
        c["fuente_texto"] = " + ".join(fuentes)
        # La plata: con partner casado manda Odoo (la venta local vive
        # allá también — no se suma dos veces); sin partner, lo local.
        if c["partner_ids"]:
            c["ventas_n"] = sum(
                od["plata"].get(pid, {}).get("ventas_n", 0)
                for pid in c["partner_ids"])
            c["ventas_total"] = round(sum(
                od["plata"].get(pid, {}).get("ventas_total", 0.0)
                for pid in c["partner_ids"]), 2)
            c["cotiz_n"] = sum(
                od["plata"].get(pid, {}).get("cotiz_n", 0)
                for pid in c["partner_ids"])
            c["cotiz_total"] = round(sum(
                od["plata"].get(pid, {}).get("cotiz_total", 0.0)
                for pid in c["partner_ids"]), 2)
        else:
            ventas_loc = [f for f in c["locales"] if f["es_venta"]]
            c["ventas_n"] = len(ventas_loc)
            c["ventas_total"] = round(sum(
                float(f["total"] or 0) for f in ventas_loc), 2)
            cotiz_loc = [f for f in c["locales"]
                         if not f["es_venta"] and not f["cancelada"]]
            c["cotiz_n"] = len(cotiz_loc)
            c["cotiz_total"] = round(sum(
                float(f["total"] or 0) for f in cotiz_loc), 2)
        c["con_venta"] = c["ventas_n"] > 0
        lista.append(c)
    lista.sort(key=lambda c: (_nombre_plano(c["nombre"]) or "~", c["id"]))
    return lista


# ---------------------------------------------------------------------------
# La pestaña «Historial de leads»: los leads REALES de esa persona
# ---------------------------------------------------------------------------

def _leads_crudos():
    """(leads, aviso). NUNCA la muestra: `linear_leads.listar()` devuelve
    leads de ejemplo cuando no hay key, y una pantalla que inventa un lead
    es mucho peor que una que dice su hueco."""
    if not linear_conectado():
        return [], AVISO_LINEAR_PRUEBAS
    try:
        return linear_leads.listar(), ""
    except Exception as fallo:
        return [], f"{AVISO_LINEAR_CAIDO} ({fallo})"


def _chip_de_lead(lead):
    """(texto, activo) — los tres chips del lienzo, por regla explícita:

    - **Terminado** (gris): el issue está cerrado en Linear (Ganado o
      Perdido).
    - **No activado** (gris): sigue en «Nuevo» y nadie lo tomó (sin
      `Resp:`) — el «nunca se activó» del lienzo.
    - **Activo** (verde): todo lo demás, que es trabajo en curso.
    """
    if lead.get("cerrado") or lead.get("estado") in linear_leads.CERRADOS:
        return "Terminado", False
    if lead.get("estado") == "NUEVO" and not (lead.get("resp") or ""):
        return "No activado", False
    return "Activo", True


def _leads_del_contacto(contacto, leads):
    """Los leads de ESA persona, casados por el MISMO teléfono normalizado
    del resto del módulo. Sin teléfono no se casa nada: el nombre plano
    sugiere, jamás amarra (diseño corto)."""
    tel = contacto.get("tel_norm") or ""
    if not tel:
        return []
    filas = []
    for lead in leads:
        if normalizar_telefono(lead.get("celular")) != tel:
            continue
        chip, activo = _chip_de_lead(lead)
        filas.append({
            "ref": lead.get("ref") or "",
            "url": lead.get("url") or "",
            # El lienzo pone un título de trabajo («Jardín del lobby») que
            # en Linear NO EXISTE: el title del issue es el nombre de la
            # persona más su código PP, y en la página del contacto ese
            # nombre ya está arriba — repetirlo tres veces es ruido. Así
            # que el renglón lo encabeza lo que de verdad distingue un
            # lead del otro: su ref, con el código PP al lado.
            "pp": lead.get("pp") or "",
            "estado_nombre": lead.get("estado_nombre") or "",
            "interes": lead.get("interes") or "",
            "resp": lead.get("resp") or "",
            "chip": chip,
            "activo": activo,
            "hace": lead.get("hace") or "",
        })
    # Los activos arriba; dentro de cada grupo, el más reciente primero
    # (las refs de Linear suben: LEAD-94 es posterior a LEAD-62).
    filas.sort(key=lambda f: (not f["activo"], -_numero_de_ref(f["ref"])))
    return filas


def _numero_de_ref(ref):
    digitos = re.sub(r"\D", "", str(ref or ""))
    return int(digitos) if digitos else 0


def panel_leads(contacto, leads, aviso_leads):
    """TODO lo que pinta la pestaña «Historial de leads», ya decidido."""
    filas = _leads_del_contacto(contacto, leads)
    aviso = aviso_leads
    if not aviso and not filas:
        aviso = (SIN_TELEFONO_LEADS if not contacto.get("tel_norm")
                 else SIN_LEADS)
    activos = sum(1 for f in filas if f["activo"])
    return {
        "leads": filas,
        "cuenta": len(filas),
        "activos": activos,
        "resumen": (f"{len(filas)} · {activos} activo"
                    f"{'s' if activos != 1 else ''}") if filas else "",
        "aviso": aviso,
        "pie": PIE_LEADS,
    }


# ---------------------------------------------------------------------------
# La pestaña «WhatsApp»: el chat en SOLO LECTURA
# ---------------------------------------------------------------------------

def panel_chat(contacto):
    """El hilo del contacto, armado con el MISMO `control.hilo()` de la
    ficha del lead (cliente a la izquierda, equipo a la derecha con el
    nombre de quien respondió). Acá NUNCA se escribe ni se manda nada:
    esta pantalla no tiene una sola ruta POST.

    Se busca por teléfono, que es lo que Twenty guarda del chat
    (`_mensajes_por_telefono` ya resuelve las dos grafías de Panamá en UN
    solo `filter`, porque dos `filter=` en la misma URL no se suman)."""
    panel = {"ok": False, "hilo": [], "cant": 0, "aviso": "", "pie": PIE_CHAT}
    telefono = contacto.get("telefono") or contacto.get("tel_norm") or ""
    if not telefono:
        panel["aviso"] = SIN_TELEFONO_CHAT
        return panel
    if not twenty_conectado():
        panel["aviso"] = AVISO_CHAT_PRUEBAS
        return panel
    try:
        crudos = crm_twenty._mensajes_por_telefono(telefono, MENSAJES_DEL_CHAT)
    except Exception as fallo:
        panel["aviso"] = f"{AVISO_CHAT_CAIDO} ({fallo})"
        return panel
    panel["ok"] = True
    if not crudos:
        panel["aviso"] = SIN_CHAT
        return panel
    mensajes = [crm_twenty.mensaje_legible(m) for m in crudos]
    panel["cant"] = len(crudos)
    # Sin sucesos: esos son comentarios del issue de un lead, y un
    # contacto puede no tener ninguno (el mismo criterio de
    # /conversaciones).
    panel["hilo"] = control.hilo(mensajes, (), contacto.get("nombre") or "")
    return panel


# ---------------------------------------------------------------------------
# El candado del DINERO y del CHAT (frente D) — decidido en el SERVIDOR
# ---------------------------------------------------------------------------

def sesion_de(empleada, es_admin=False):
    """Quién está mirando, reducido a lo único que el candado necesita:
    {"ve_todo", "resp"}.

    `ve_todo` es el director, finanzas y el admin — y también quien no
    tiene una puerta por rol, que es el MISMO fail-open de transición que
    ya aplica `datos_roles.acceso_de` en toda la app (alcance None =
    director, sin rol, o un rol sin slug). Operaciones y Atención sí
    quedan acotados: su alcance existe y no es de ver-todo.

    `resp` es la etiqueta `Resp:` que le corresponde a la persona, la
    misma que usa `control.puede_tocar()`. Solo se pregunta cuando hace
    falta: a quien ve todo no se le consulta Linear."""
    ve_todo = bool(es_admin)
    if not ve_todo:
        alcance = datos_roles.acceso_de(empleada)["alcance"]
        ve_todo = alcance is None or bool(alcance.get("ver_todo"))
    return {
        "ve_todo": ve_todo,
        "resp": "" if ve_todo else agenda.responsable_de_empleada(empleada),
    }


def _permiso(sesion, leads, aviso_leads=""):
    """¿Quien mira puede ver el DINERO y el CHAT de ESTE contacto?

    La regla del diseño corto, punto 5: los ve quien lo atiende (alguno de
    sus leads lleva su `Resp:`), el director y finanzas. Los datos de
    contacto puros —nombre, teléfono, de dónde llegó— los ve todo el que
    ve la lista: el candado es sobre el dinero y el chat ajenos, no sobre
    la persona.

    Dos decisiones que NO se aflojan:

    - **Sin sesión, cerrado.** Una llamada que no dice quién es no puede
      ver plata; el módulo no tiene un default abierto que una ruta nueva
      pueda heredar sin darse cuenta.
    - **Sin poder leer Linear, cerrado.** Si no se sabe de quién es el
      contacto, no se adivina a favor: se dice y se tapa.
    """
    if sesion is None:
        return {"dinero": False, "chat": False, "motivo": CANDADO_SIN_SESION}
    if sesion.get("ve_todo"):
        return {"dinero": True, "chat": True, "motivo": ""}
    resp = sesion.get("resp") or ""
    if resp and any((l.get("resp") or "") == resp for l in leads):
        return {"dinero": True, "chat": True, "motivo": ""}
    if aviso_leads:
        return {"dinero": False, "chat": False, "motivo": CANDADO_SIN_FUENTE}
    if any((l.get("resp") or "") for l in leads):
        return {"dinero": False, "chat": False, "motivo": CANDADO_AJENO}
    return {"dinero": False, "chat": False, "motivo": CANDADO_SIN_MIO}


# ---------------------------------------------------------------------------
# El buscador (A7)
# ---------------------------------------------------------------------------

def _casa_busqueda(contacto, q):
    """¿Este contacto casa con lo que se escribió en el buscador?

    **A7 del BLOQUE 53.** El teléfono se compara NORMALIZADO de los dos
    lados con el mismo `normalizar_telefono` del casamiento, así que da
    igual cómo se escriba: «6000-0001», «6000 0001», «+507 6000-0001»,
    «00507 6000 0001» y «60000001» encuentran lo mismo, aunque Odoo tenga
    guardado el número con guiones y con el +507 por delante (medido en el
    8095 el 7/10/2026). El nombre va por texto suelto, como siempre.

    Ojo con lo que NO arregla este buscador, porque es el hallazgo de
    fondo de A7: si la persona no está en NINGUNA de las tres fuentes de
    la lista, no hay nada que encontrar. Por eso el lead es fuente."""
    q = (q or "").strip()
    if not q:
        return True
    if q.lower() in (contacto.get("nombre") or "").lower():
        return True
    q_tel = normalizar_telefono(q)
    if not q_tel:
        return False
    # La forma normalizada del contacto y, por si acaso, la del texto
    # crudo que se muestra: las dos salen del mismo normalizador.
    for guardado in (contacto.get("tel_norm") or "",
                     normalizar_telefono(contacto.get("telefono"))):
        if guardado and q_tel in guardado:
            return True
    return False


# ---------------------------------------------------------------------------
# La lista (GET /contactos)
# ---------------------------------------------------------------------------

def lista(q="", filtro="", sesion=None):
    """TODO lo que la plantilla de la lista pinta, ya decidido (regla 10):
    los contactos filtrados, los filtros GET con su activo, el buscador
    server-rendered y los avisos honestos de cada fuente.

    La lista va a TODO EL ANCHO desde el BLOQUE 43 (lienzo refrescado):
    sin panel al costado — tocar un contacto abre su página.

    El candado del dinero viaja hasta acá: las columnas Ventas y Total de
    un contacto ajeno salen TAPADAS (`dinero` en False y los montos en
    None), porque si no, quien no puede abrir su página leería su plata de
    la fila. Se tapa en Python: el monto no llega al HTML.

    Desde el BLOQUE 53 los leads se leen SIEMPRE, también para quien ve
    todo: ya no son solo la pregunta del candado, son una FUENTE de la
    lista (A7). Cuesta poco: `linear_leads.listar()` sirve lo guardado al
    instante y refresca por detrás."""
    od = datos_odoo()
    leads, aviso_leads = _leads_crudos()
    contactos = _unir(od, leads)
    total_sin_filtrar = len(contactos)

    filtro = filtro if filtro in {clave for clave, _n in FILTROS} else "todos"
    if filtro == "con_venta":
        contactos = [c for c in contactos if c["con_venta"]]
    elif filtro == "sin_venta":
        contactos = [c for c in contactos if not c["con_venta"]]
    elif filtro == "empresas":
        contactos = [c for c in contactos if c["tipo"] == "Empresa"]

    q = (q or "").strip()
    if q:
        contactos = [c for c in contactos if _casa_busqueda(c, q)]

    # El candado, fila por fila.
    ve_todo = bool(sesion and sesion.get("ve_todo"))
    tapados = 0
    for c in contactos:
        c["dinero"] = ve_todo or _permiso(
            sesion, _leads_del_contacto(c, leads), aviso_leads)["dinero"]
        if not c["dinero"]:
            tapados += 1
            # El recorte es aquí, no en la plantilla: lo tapado no viaja.
            for campo in ("ventas_n", "ventas_total", "cotiz_n",
                          "cotiz_total"):
                c[campo] = None

    return {
        "contactos": contactos,
        "cuenta": len(contactos),
        "tapados": tapados,
        "aviso_tapados": (
            "Las ventas y los totales de los contactos que no atiendes no "
            "se muestran." if tapados else ""),
        "total": total_sin_filtrar,
        "q": q,
        "filtro": filtro,
        "filtros": [{"clave": clave, "nombre": nombre,
                     "activo": clave == filtro}
                    for clave, nombre in FILTROS],
        "filtro_apagado": FILTRO_APAGADO,
        "aviso_odoo": od["aviso"],
        "aviso_twenty": aviso_twenty(),
        # A7: si los leads no se pudieron leer, falta una fuente entera y
        # se dice. A8: cuántas cuentas de sistema quedaron fuera (o el
        # hueco de no haber podido comprobarlo).
        "aviso_crm": AVISO_CRM_HUECO if aviso_leads else "",
        "sistema": od.get("sistema") or 0,
        "aviso_sistema": (
            od.get("aviso_sistema")
            or (f"{od['sistema']} cuenta{'s' if od['sistema'] != 1 else ''} "
                f"del sistema (administradores, apps y la compañía) fuera "
                f"de la lista." if od.get("sistema") else "")),
        "vacio": VACIO_LISTA,
    }


# ---------------------------------------------------------------------------
# La ficha (GET /contactos/<id>)
# ---------------------------------------------------------------------------

def _ordenes_odoo(partner_ids):
    """Las órdenes del partner casado: números S00xxx, montos y estado.
    Una sola consulta; 'cancel' fuera.

    `facturada` dice si esa orden ya está facturada (`invoice_status`):
    las facturadas salen de la tabla «Cotizado» y su plata se ve en
    «Facturado» o en «Pagado», que es lo que hace el lienzo."""
    if not partner_ids:
        return []
    filas = ventas._ejecutar(
        "sale.order", "search_read",
        [[["partner_id", "in", list(partner_ids)],
          ["state", "!=", "cancel"]]],
        {"fields": ["name", "amount_total", "state", "date_order",
                    "invoice_status"],
         "limit": 200})
    etiqueta = {"draft": "Cotización", "sent": "Cotización enviada",
                "sale": "Confirmada", "done": "Entregada"}
    return [{"orden": f.get("name") or "", "total": f.get("amount_total"),
             "estado": etiqueta.get(f.get("state"), f.get("state") or ""),
             "fecha": str(f.get("date_order") or ""),
             "facturada": f.get("invoice_status") == "invoiced"}
            for f in filas]


# ---------------------------------------------------------------------------
# A14 · «Cuándo nos pagan» y las tablas «Facturado» y «Pagado»
# ---------------------------------------------------------------------------

_CENTAVO = 0.009


def _facturas_odoo(partner_ids):
    """Las facturas de venta PUBLICADAS del partner casado. Una sola
    consulta; los borradores y las canceladas no son plata facturada."""
    return ventas._ejecutar(
        "account.move", "search_read",
        [[["partner_id", "in", list(partner_ids)],
          ["move_type", "=", "out_invoice"],
          ["state", "=", "posted"]]],
        {"fields": ["name", "amount_total", "amount_residual",
                    "invoice_date", "invoice_date_due"],
         "limit": 200})


def _facturacion(contacto, hay_odoo):
    """«Cuándo nos pagan» y las dos tablas de facturas, ya decididas.

    Reglas, para que nadie tenga que adivinar mirando la pantalla:

    - **Facturado** = la suma de las facturas de venta publicadas.
    - **Por cobrar** = la suma de sus saldos (`amount_residual`).
    - **Próximo vencimiento** = el `invoice_date_due` más cercano de las
      que todavía deben; si no debe nada, «—» y la frase lo explica.
    - **Pagado** = las facturas con saldo 0; «Cobrado» es su suma.

    Nada se inventa: sin partner de Odoo la respuesta honesta es que no
    tiene facturas (no es un hueco, es un hecho); con Odoo caído o sin
    configurar los tres números salen en None —«sin dato»— y el aviso lo
    dice. Jamás un $0 fingido, que es lo que se celebra o se cobra mal.
    """
    vacio = {"ok": False, "aviso": "", "facturado": None, "por_cobrar": None,
             "cobrado": None, "vence": "", "nota": "", "facturas": [],
             "pagadas": []}
    if not contacto.get("partner_ids"):
        return dict(vacio, ok=True, aviso=SIN_PARTNER_FACTURAS)
    if not hay_odoo:
        return dict(vacio, aviso=(AVISO_FACTURAS_SIN_ODOO
                                  if not ventas.configurado()
                                  else AVISO_FACTURAS_CAIDO))
    try:
        filas = _facturas_odoo(contacto["partner_ids"])
    except Exception as fallo:
        return dict(vacio, aviso=f"{AVISO_FACTURAS_CAIDO} ({fallo})")

    deben, pagadas, vencimientos = [], [], []
    facturado = cobrado = por_cobrar = 0.0
    for f in filas:
        total = float(f.get("amount_total") or 0)
        saldo = float(f.get("amount_residual") or 0)
        facturado += total
        renglon = {"factura": f.get("name") or "", "total": total,
                   "saldo": round(saldo, 2),
                   "fecha": str(f.get("invoice_date") or "") or "—",
                   "vence": str(f.get("invoice_date_due") or "") or "—"}
        if saldo > _CENTAVO:
            por_cobrar += saldo
            deben.append(renglon)
            if f.get("invoice_date_due"):
                vencimientos.append(str(f["invoice_date_due"]))
        else:
            cobrado += total
            pagadas.append(renglon)
    deben.sort(key=lambda r: r["vence"])
    pagadas.sort(key=lambda r: r["fecha"], reverse=True)
    return {
        "ok": True, "aviso": "",
        "facturado": round(facturado, 2),
        "por_cobrar": round(por_cobrar, 2),
        "cobrado": round(cobrado, 2),
        "vence": min(vencimientos) if vencimientos else "—",
        "nota": "" if vencimientos else (TODO_COBRADO if filas else ""),
        "facturas": deben, "pagadas": pagadas,
    }


def ficha(cid, sesion=None, panel=""):
    """LA PÁGINA del contacto (BLOQUE 43, lienzo abraham-contacto-abierto):
    los datos a la izquierda y el panel derecho con sus dos pestañas.
    None si el id ya no existe.

    Las pestañas son enlaces GET resueltos ACÁ (`?panel=leads|whatsapp`),
    abriendo siempre en «Historial de leads»; un valor inventado cae en
    esa misma. Cero JS: la plantilla solo pinta lo que este dict trae.

    El candado (frente D) se aplica antes de devolver nada: con el dinero
    tapado, los montos salen en None, las tres tablas van vacías y **a
    Odoo ni se le preguntan las órdenes ni las facturas** — lo que no se
    puede ver no se lee. Con el chat tapado, el hilo no se arma siquiera.

    Del BLOQUE 53 (A14): la página es la del lienzo. «Gastos y compras /
    Total gastado» SALIÓ, y entraron «Cuándo nos pagan» y las tres tablas
    (Cotizado · Facturado · Pagado). «Citas» se queda."""
    od = datos_odoo()
    leads, aviso_leads = _leads_crudos()
    contactos = _unir(od, leads)
    contacto = next((c for c in contactos if c["id"] == cid), None)
    if contacto is None:
        return None

    mios = _leads_del_contacto(contacto, leads)
    permiso = _permiso(sesion, mios, aviso_leads)

    # Quién lo atiende: sale de los `Resp:` de SUS leads, que es el único
    # lugar donde ese dato existe hoy. Sin leads legibles, vacío honesto.
    atiende = sorted({f["resp"] for f in mios if f["resp"]})

    aviso_odoo_ficha = od["aviso"]
    tratos = []
    facturacion = {"ok": False, "aviso": "", "facturado": None,
                   "por_cobrar": None, "cobrado": None, "vence": "",
                   "nota": "", "facturas": [], "pagadas": []}
    if permiso["dinero"]:
        # TABLA 1 «Cotizado»: lo que todavía no está facturado. Primero lo
        # local (trae href a la ficha existente), luego lo de Odoo que no
        # sea la MISMA orden ya listada.
        facturadas = set()
        ordenes_odoo = []
        if contacto["partner_ids"] and od["ok"]:
            try:
                ordenes_odoo = _ordenes_odoo(contacto["partner_ids"])
                facturadas = {o["orden"] for o in ordenes_odoo
                              if o["facturada"] and o["orden"]}
            except Exception as fallo:
                aviso_odoo_ficha = (f"Odoo no contestó las órdenes de este "
                                    f"contacto ({fallo}). Nada se inventa.")
        ordenes_vistas = set()
        for fila in contacto["locales"]:
            if fila["orden"] and fila["orden"] in facturadas:
                # Ya facturada: su plata se ve en «Facturado» o «Pagado».
                ordenes_vistas.add(fila["orden"])
                continue
            tratos.append({
                "titulo": fila["titulo"], "orden": fila["orden"] or "—",
                "total": fila["total"], "href": fila["href"],
                "estado": "Cancelada" if fila["cancelada"] else "",
                "fecha": fila["creado_en"],
            })
            if fila["orden"]:
                ordenes_vistas.add(fila["orden"])
        for orden in ordenes_odoo:
            if orden["orden"] in ordenes_vistas or orden["facturada"]:
                continue
            tratos.append({
                "titulo": "Orden en Odoo", "orden": orden["orden"],
                "total": orden["total"], "href": "",
                "estado": orden["estado"], "fecha": orden["fecha"],
            })
        tratos.sort(key=lambda t: t["fecha"], reverse=True)
        # TABLAS 2 y 3 y «Cuándo nos pagan»: las facturas reales.
        facturacion = _facturacion(contacto, od["ok"])
    else:
        contacto = dict(contacto)
        for campo in ("ventas_n", "ventas_total", "cotiz_n", "cotiz_total"):
            contacto[campo] = None

    # «Posible mismo»: mismo nombre plano en OTRO contacto. Solo sugiere
    # y apunta — jamás amarra ni escribe.
    plano = _nombre_plano(contacto["nombre"])
    posibles = [{"id": c["id"], "nombre": c["nombre"],
                 "fuente_texto": c["fuente_texto"]}
                for c in contactos
                if c["id"] != cid and plano and plano != "—"
                and _nombre_plano(c["nombre"]) == plano]

    claves = [clave for clave, _n in PANELES]
    panel = panel if panel in claves else PANEL_POR_DEFECTO
    chat = (panel_chat(contacto) if permiso["chat"]
            else {"ok": False, "hilo": [], "cant": 0,
                  "aviso": permiso["motivo"], "pie": PIE_CHAT})

    return {
        "contacto": contacto,
        "tratos": tratos,
        "sin_tratos": AVISO_SIN_TRATOS,
        # A14: las tres tablas del lienzo y «Cuándo nos pagan».
        "facturacion": facturacion,
        "sin_cotizado": SIN_COTIZADO,
        "sin_facturado": SIN_FACTURADO,
        "sin_pagado": SIN_PAGADO,
        # Nada en ninguna de las tres: se dice UNA vez, no tres.
        "nada_con_nosotros": (permiso["dinero"] and not tratos
                              and not facturacion["facturas"]
                              and not facturacion["pagadas"]),
        "posibles": posibles,
        "atiende": atiende,
        "aviso_odoo": aviso_odoo_ficha,
        "aviso_twenty": aviso_twenty(),
        "aviso_citas": AVISO_CITAS_LUEGO,
        "permiso": permiso,
        "panel": panel,
        "paneles": [{"clave": clave, "nombre": nombre,
                     "activo": clave == panel,
                     "href": f"/contactos/{cid}?panel={clave}"}
                    for clave, nombre in PANELES],
        "leads": panel_leads(contacto, leads, aviso_leads),
        "chat": chat,
        "apagados_accion": APAGADOS_ACCION,
        "apagados_plata": APAGADOS_PLATA,
        "todavia_no": TODAVIA_NO,
    }
