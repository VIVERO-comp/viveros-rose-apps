"""Contactos — la pantalla SOLO LECTURA (BLOQUE 37, item 2 de Jay).

Diseño corto aprobado: docs/DISENO-ITEM2-contactos.md del repo
plantaspanama; lienzo v3 en docs/diseno-roles/pantallas/
abraham-contactos.html.

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
"""

import os
import re
import time

from . import cotizaciones, crm_twenty, ventas

# ---------------------------------------------------------------------------
# Los avisos honestos, en un solo lugar (las pruebas los reusan)
# ---------------------------------------------------------------------------

AVISO_TWENTY_PRUEBAS = ("Twenty no está conectado en pruebas: la ficha del "
                        "cliente y el último mensaje no se pueden leer.")
AVISO_TWENTY_LUEGO = ("La lectura de Twenty (la ficha del cliente y el "
                      "último mensaje) llega con su propia parte.")
AVISO_SIN_ODOO = ("Odoo no está configurado en esta instancia: la lista "
                  "sale solo de los clientes locales de Vender.")
AVISO_LEADS_LUEGO = ("Los leads de Linear de este contacto llegan con su "
                     "propia parte.")
AVISO_CITAS_LUEGO = ("Las citas del calendario de este contacto llegan "
                     "con su propia parte.")
AVISO_SIN_TRATOS = "Sin cotizaciones ni ventas con nosotros todavía."
VACIO_LISTA = "Ningún contacto con lo que hay en las fuentes de hoy."

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

_cache_odoo = {"en": None, "partners": [], "plata": {}}


def reiniciar_cache():
    """Solo para pruebas."""
    _cache_odoo["en"] = None
    _cache_odoo["partners"] = []
    _cache_odoo["plata"] = {}


def _leer_odoo():
    """Los res.partner activos con nombre, y la plata por partner contada
    de UNA pasada por sale.order (sin canceladas) — nunca una consulta
    por contacto."""
    partners = ventas._ejecutar(
        "res.partner", "search_read",
        [[["active", "=", True], ["name", "!=", False]]],
        {"fields": ["name", "phone", "is_company"], "limit": 2000})
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
    return partners, plata


def datos_odoo():
    """{'partners', 'plata', 'ok', 'aviso'}. Nada se inventa: sin
    configuración o con Odoo caído y sin lectura previa, ok=False y el
    aviso lo dice; con una lectura buena anterior se sirve ESA con su
    edad en minutos (el patrón de pedidos.plata)."""
    if not ventas.configurado():
        return {"partners": [], "plata": {}, "ok": False,
                "aviso": AVISO_SIN_ODOO}
    try:
        partners, plata = _leer_odoo()
    except Exception as fallo:
        if _cache_odoo["en"] is not None:
            edad = max(0, int((time.time() - _cache_odoo["en"]) // 60))
            return {"partners": _cache_odoo["partners"],
                    "plata": _cache_odoo["plata"], "ok": True,
                    "aviso": (f"Odoo no contestó: mostrando la última "
                              f"lectura buena, de hace {edad} min.")}
        return {"partners": [], "plata": {}, "ok": False,
                "aviso": (f"Odoo no contestó: la lista sale solo de los "
                          f"clientes locales de Vender ({fallo}).")}
    _cache_odoo["en"] = time.time()
    _cache_odoo["partners"] = partners
    _cache_odoo["plata"] = plata
    return {"partners": partners, "plata": plata, "ok": True, "aviso": ""}


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
        "tipo": "Persona", "partner_ids": [], "locales": [],
        "fuentes": [],  # se arma al final: Odoo / Local
    }


def _unir(od):
    """La lista unificada, calculada al armar la vista (casamiento EN
    LECTURA, sin escribir NADA). Clave: el teléfono normalizado; sin
    teléfono cada aparición queda como su propio contacto (el nombre
    plano no amarra — solo sugerirá «posible mismo» en la ficha)."""
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

    lista = []
    for c in contactos.values():
        c.pop("_nombre_odoo", None)
        c["nombre"] = c["nombre"] or "—"
        fuentes = []
        if c["partner_ids"]:
            fuentes.append("Odoo")
        if c["locales"]:
            fuentes.append("Local")
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
# La lista (GET /contactos)
# ---------------------------------------------------------------------------

def lista(q="", filtro=""):
    """TODO lo que la plantilla de la lista pinta, ya decidido (regla 10):
    los contactos filtrados, los filtros GET con su activo, el buscador
    server-rendered y los avisos honestos de cada fuente."""
    od = datos_odoo()
    contactos = _unir(od)
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
        q_plano = q.lower()
        q_tel = normalizar_telefono(q)
        contactos = [
            c for c in contactos
            if q_plano in c["nombre"].lower()
            or (q_tel and q_tel in c["tel_norm"])]

    return {
        "contactos": contactos,
        "cuenta": len(contactos),
        "total": total_sin_filtrar,
        "q": q,
        "filtro": filtro,
        "filtros": [{"clave": clave, "nombre": nombre,
                     "activo": clave == filtro}
                    for clave, nombre in FILTROS],
        "filtro_apagado": FILTRO_APAGADO,
        "aviso_odoo": od["aviso"],
        "aviso_twenty": aviso_twenty(),
        "vacio": VACIO_LISTA,
    }


# ---------------------------------------------------------------------------
# La ficha (GET /contactos/<id>)
# ---------------------------------------------------------------------------

def _ordenes_odoo(partner_ids):
    """Las órdenes del partner casado: números S00xxx, montos y estado.
    Una sola consulta; 'cancel' fuera."""
    if not partner_ids:
        return []
    filas = ventas._ejecutar(
        "sale.order", "search_read",
        [[["partner_id", "in", list(partner_ids)],
          ["state", "!=", "cancel"]]],
        {"fields": ["name", "amount_total", "state", "date_order"],
         "limit": 200})
    etiqueta = {"draft": "Cotización", "sent": "Cotización enviada",
                "sale": "Confirmada", "done": "Entregada"}
    return [{"orden": f.get("name") or "", "total": f.get("amount_total"),
             "estado": etiqueta.get(f.get("state"), f.get("state") or ""),
             "fecha": str(f.get("date_order") or "")}
            for f in filas]


def ficha(cid):
    """La ficha agrupada del lienzo en su versión mínima honesta: datos
    del contacto, sus tratos (locales + Odoo, apuntando con href a
    /venta donde aplica, sin duplicar la misma orden) y las secciones
    sin fuente hoy como huecos honestos. None si el id ya no existe."""
    od = datos_odoo()
    contactos = _unir(od)
    contacto = next((c for c in contactos if c["id"] == cid), None)
    if contacto is None:
        return None

    # Los tratos: primero lo local (trae href a la ficha existente),
    # luego lo de Odoo que no sea la MISMA orden ya listada.
    tratos = []
    ordenes_vistas = set()
    for fila in contacto["locales"]:
        tratos.append({
            "titulo": fila["titulo"], "orden": fila["orden"] or "—",
            "total": fila["total"], "href": fila["href"],
            "estado": "Cancelada" if fila["cancelada"] else "",
            "fecha": fila["creado_en"],
        })
        if fila["orden"]:
            ordenes_vistas.add(fila["orden"])
    aviso_odoo_ficha = od["aviso"]
    if contacto["partner_ids"] and od["ok"]:
        try:
            for orden in _ordenes_odoo(contacto["partner_ids"]):
                if orden["orden"] in ordenes_vistas:
                    continue
                tratos.append({
                    "titulo": "Orden en Odoo", "orden": orden["orden"],
                    "total": orden["total"], "href": "",
                    "estado": orden["estado"], "fecha": orden["fecha"],
                })
        except Exception as fallo:
            aviso_odoo_ficha = (f"Odoo no contestó las órdenes de este "
                                f"contacto ({fallo}). Nada se inventa.")
    tratos.sort(key=lambda t: t["fecha"], reverse=True)

    # «Posible mismo»: mismo nombre plano en OTRO contacto. Solo sugiere
    # y apunta — jamás amarra ni escribe.
    plano = _nombre_plano(contacto["nombre"])
    posibles = [{"id": c["id"], "nombre": c["nombre"],
                 "fuente_texto": c["fuente_texto"]}
                for c in contactos
                if c["id"] != cid and plano and plano != "—"
                and _nombre_plano(c["nombre"]) == plano]

    return {
        "contacto": contacto,
        "tratos": tratos,
        "sin_tratos": AVISO_SIN_TRATOS,
        "posibles": posibles,
        "aviso_odoo": aviso_odoo_ficha,
        "aviso_twenty": aviso_twenty(),
        "aviso_leads": AVISO_LEADS_LUEGO,
        "aviso_citas": AVISO_CITAS_LUEGO,
    }
