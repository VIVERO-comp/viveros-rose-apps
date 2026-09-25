#!/usr/bin/env python3
"""Sincronizador CRM -> etiquetas de WhatsApp (Fase W).

Cada 2 minutos: lee los leads VIVOS del equipo LEAD de Linear, saca el
telefono de Twenty, y deja en cada chat de WhatsApp las etiquetas que
tocan. Lee de vuelta el empleado y el interes si alguien los cambio a mano
en el telefono.

  Linear/Twenty  --(estado, 🔴 Responder)-->  WhatsApp     solo bajan
  Linear/Twenty  <--(empleado, interes)-->    WhatsApp     suben y bajan

Por que esa division: el estado lo mueve el embudo (el mensaje del cliente,
la cotizacion, el pago, el calendario) y «🔴 Responder» lo pone y lo quita
el mensaje. Esos dos no se corrigen desde el telefono. El empleado y el
interes SI: quien atiende el chat es quien mejor sabe de quien es y de que
se trata.

REGLAS QUE NO SE ROMPEN

- LISTA BLANCA: este script solo llama a los endpoints de etiquetas y a
  check-exists. `_waha()` rechaza cualquier otra ruta, asi que no puede
  mandar un mensaje ni leer una conversacion ni por error.
- Los chats que no son leads no se tocan. El sincronizador trabaja DESDE
  los leads, asi que un chat cualquiera nunca entra.
- Los numeros internos del negocio se saltan, aunque tengan lead viejo.
- Las etiquetas de WhatsApp ('No leidos', 'Favoritos', 'Grupos') no se
  tocan nunca: son del telefono, no nuestras.
- En seco (por defecto) no escribe NADA: imprime el plan y se va.

Uso:
    ./sincronizador.py            # seco: dice que haria
    ./sincronizador.py --aplicar  # de verdad
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RUTA = os.path.dirname(os.path.abspath(__file__))
WAHA = "http://127.0.0.1:3001"
SESION = "vivero"
EQUIPO_LEAD = "793e59fb-508e-4b85-b308-de6f77879ce6"
ESTADO = os.path.join(RUTA, "sincronizador.estado.json")

# --- lista blanca: lo UNICO que este script puede llamar en WAHA ---------
PERMITIDO = (
    re.compile(r"^/api/%s/labels$" % SESION),
    re.compile(r"^/api/%s/labels/[^/]+/chats$" % SESION),   # leer por etiqueta
    re.compile(r"^/api/%s/labels/chats/[^/]+$" % SESION),   # leer/guardar de un chat
    re.compile(r"^/api/contacts/check-exists$"),
    re.compile(r"^/api/sessions/%s$" % SESION),
)

# Las etiquetas de WhatsApp que son del telefono y no nuestras.
DE_WHATSAPP = {"No leídos", "No leidos", "Favoritos", "Grupos"}

# Los estados del embudo que merecen etiqueta. Ganado y Perdido NO: un lead
# cerrado no necesita que el telefono lo anuncie, y las etiquetas de
# WhatsApp tienen tope (ver SINCRONIZADOR.md).
ESTADOS_CON_ETIQUETA = ("Nuevo", "Hablando", "Cotizado", "Por agendar",
                        "Agendado", "Entregado")
INTERESES = ("Plantas", "Eventos", "Paisajismo", "Mantenimiento", "Mayorista")
RESPONDER = "🔴 Responder"

# Las cuatro familias de etiquetas de WhatsApp, cada una con su color
# (25/09/2026). El color dice de que familia es; el nombre, cual.
REPRESENTANTES = ("Mary", "Ruben", "Salomón", "Abraham")

# EL PUENTE DE NOMBRES. En Linear la etiqueta es `Resp: Mary`; en WhatsApp,
# solo `Mary`. Los dos vocabularios se quedan como estan y el traductor
# vive aqui, en un solo lugar: asi nadie tiene que renombrar nada a los
# lados. (Y ojo: en WhatsApp estuvo escrita «Mari» hasta hoy; se rehizo
# como «Mary» para que haya UN solo nombre.)
PREFIJO_RESP = "Resp: "


def a_whatsapp(resp_linear):
    """`Resp: Mary` (o `Mary`) -> la etiqueta de WhatsApp, `Mary`."""
    n = (resp_linear or "").strip()
    return n[len(PREFIJO_RESP):] if n.startswith(PREFIJO_RESP) else n


def a_linear(etiqueta_whatsapp):
    """`Mary` -> la etiqueta de Linear, `Resp: Mary`."""
    n = (etiqueta_whatsapp or "").strip()
    return n if n.startswith(PREFIJO_RESP) else (PREFIJO_RESP + n if n else "")


def leer_env():
    datos = {}
    for linea in open(os.path.join(RUTA, ".env")):
        linea = linea.strip()
        if "=" in linea and not linea.startswith("#"):
            k, _, v = linea.partition("=")
            datos[k.strip()] = v.strip()
    return datos


ENV = leer_env()


def _pedir(url, datos=None, cabeceras=None, metodo=None):
    pet = urllib.request.Request(url, data=datos, method=metodo)
    for k, v in (cabeceras or {}).items():
        pet.add_header(k, v)
    with urllib.request.urlopen(pet, timeout=25) as r:
        cuerpo = r.read()
    return json.loads(cuerpo) if cuerpo else None


def _waha(ruta, datos=None, metodo=None):
    """La unica puerta a WAHA, y solo abre lo de la lista blanca.

    La lista se compara contra la ruta SIN query: check-exists lleva
    `?phone=...&session=...` y con el query pegado ningun patron calzaba.
    """
    solo_ruta = ruta.split("?", 1)[0]
    if not any(p.match(solo_ruta) for p in PERMITIDO):
        raise RuntimeError("ruta fuera de la lista blanca: %s" % solo_ruta)
    return _pedir(WAHA + ruta,
                  datos=json.dumps(datos).encode() if datos is not None else None,
                  cabeceras={"X-Api-Key": ENV["WAHA_API_KEY"],
                             "Content-Type": "application/json"},
                  metodo=metodo)


def linear(consulta, variables=None):
    return _pedir("https://api.linear.app/graphql",
                  datos=json.dumps({"query": consulta,
                                    "variables": variables or {}}).encode(),
                  cabeceras={"Content-Type": "application/json",
                             "Authorization": ENV["LINEAR_API_KEY"]})["data"]


def twenty(ruta):
    base = (ENV.get("TWENTY_URL") or "https://crm.plantaspanama.com").rstrip("/")
    return _pedir("%s/rest/%s" % (base, ruta),
                  cabeceras={"Authorization": "Bearer " + ENV["TWENTY_API_KEY"]})


# ---------------------------------------------------------------------------
# Lo que dice el CRM
# ---------------------------------------------------------------------------

CONSULTA = """
query($equipo: ID!) {
  issues(first: 250, filter: { team: { id: { eq: $equipo } } }) {
    nodes { identifier title description
            state { name type }
            labels { nodes { name parent { name } } } } }
}
"""


def solo_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def telefono_de_la_tarjeta(descripcion):
    m = re.search(r"wa\.me/(\d{8,15})", descripcion or "")
    return m.group(1) if m else ""


def leads_del_crm():
    """[{ref, nombre, pp, estado, resp, interes, te_toca, telefono}] vivos."""
    nodos = linear(CONSULTA, {"equipo": EQUIPO_LEAD})["issues"]["nodes"]
    # El telefono manda Twenty; la tarjeta de Linear es el respaldo.
    por_pp = {}
    try:
        j = twenty("leadsWeb?limit=200")
        for lw in (j.get("data") or {}).get("leadsWeb") or []:
            if lw.get("codigoRef"):
                por_pp[lw["codigoRef"].upper()] = lw.get("personaId") or ""
    except Exception as fallo:
        print("   (Twenty no contesto para los leadsWeb: %s)" % str(fallo)[:60])
    telefonos = {}
    if por_pp:
        try:
            j = twenty("people?limit=200")
            for p in (j.get("data") or {}).get("people") or []:
                crudo = ((p.get("phones") or {}).get("primaryPhoneNumber") or "")
                cc = ((p.get("phones") or {}).get("primaryPhoneCallingCode") or "")
                if crudo:
                    telefonos[p["id"]] = solo_digitos(cc + crudo)
        except Exception as fallo:
            print("   (Twenty no contesto para las Person: %s)" % str(fallo)[:60])

    leads = []
    for n in nodos:
        estado = (n["state"] or {}).get("name") or ""
        if (n["state"] or {}).get("type") in ("completed", "canceled"):
            continue  # Ganado y Perdido: el chat se queda sin etiquetas
        grupos = {}
        for l in n["labels"]["nodes"]:
            grupos[((l.get("parent") or {}).get("name") or "")] = l["name"]
        nombres = [l["name"] for l in n["labels"]["nodes"]]
        titulo = n["title"] or ""
        pp = (re.search(r"\((PP-[A-Z0-9]+)\)", titulo) or [None, ""])[1] \
            if "(PP-" in titulo else ""
        m = re.search(r"\((PP-[A-Z0-9]+)\)", titulo)
        pp = m.group(1) if m else ""
        resp = grupos.get("Responsable", "")
        tel = telefonos.get(por_pp.get(pp.upper(), ""), "") \
            or telefono_de_la_tarjeta(n.get("description"))
        leads.append({
            "ref": n["identifier"],
            "nombre": titulo.split(" (PP-")[0].strip() or titulo,
            "pp": pp,
            "estado": estado,
            "resp": resp[len(PREFIJO_RESP):] if resp.startswith(PREFIJO_RESP) else "",
            "interes": grupos.get("Interés", ""),
            "te_toca": "Te toca" in nombres,
            "telefono": tel,
        })
    return leads


ETIQUETA_EQUIPO = "Equipo"


def internos():
    """Los numeros del negocio: los del equipo, que nunca son clientes.

    Dos fuentes, en este orden:

      1. El order-api del droplet de apps, que es LA lista (la que
         administra Abraham en Ajustes y la que usa el receptor de
         WhatsApp). Hace falta `ORDER_API_KEY` en el .env.
      2. Si no esta, `NUMEROS_INTERNOS` del propio .env.

    La 2 funciona pero DUPLICA la lista: un numero agregado en Ajustes no
    llegaria aqui solo, y su chat se quedaria sin la etiqueta «Equipo».
    Con la key, esto sigue a Ajustes sin que nadie copie nada.
    """
    clave = ENV.get("ORDER_API_KEY")
    url = ENV.get("COWORKERS_URL") or \
        "https://pedidos.plantaspanama.com/api/crm/numeros-coworkers"
    if clave:
        try:
            j = _pedir(url, cabeceras={"X-Api-Key": clave})
            return {solo_digitos(n)[-8:] for n in (j.get("numeros") or [])}, \
                "del order-api (una sola verdad)"
        except Exception as fallo:
            print("   (el order-api no contesto: %s)" % str(fallo)[:60])
    crudos = (ENV.get("NUMEROS_INTERNOS") or "").split(",")
    lista = {solo_digitos(n)[-8:] for n in crudos if solo_digitos(n)}
    if lista:
        return lista, "de NUMEROS_INTERNOS del .env (copia local: ojo)"
    return set(), "sin lista"


def etiquetar_equipo(lista_interna, aplicar):
    """Deja «Equipo» —y SOLO eso— en los chats de los numeros internos.

    Son los telefonos del equipo, no clientes: no llevan representante, ni
    interes, ni estado, ni «Responder». La etiqueta existe para lo
    contrario: para reconocer de un vistazo que ese chat NO es un lead.

    Estos numeros nunca llegan a ser leads —el receptor los descarta antes
    de crear lead, Person o copia de chat—, asi que el sincronizador no los
    ve por el camino normal. Hay que recorrerlos aparte, y es esto.
    """
    if not lista_interna:
        return 0, []
    etiquetas = etiquetas_de_whatsapp()
    if ETIQUETA_EQUIPO not in etiquetas:
        return 0, [("Equipo", "la etiqueta %r no existe en WhatsApp"
                    % ETIQUETA_EQUIPO)]
    quiere = [{"id": etiquetas[ETIQUETA_EQUIPO]}]
    hechos, errores = 0, []
    print("-" * 76)
    print("NUMEROS INTERNOS (solo «Equipo», nunca etiquetas del CRM): %d"
          % len(lista_interna))
    for digitos in sorted(lista_interna):
        chat = chat_id_real("507" + digitos)
        if not chat:
            print("   %-10s no tiene WhatsApp" % digitos)
            continue
        tiene = etiquetas_del_chat(chat)
        sobran = sorted(tiene - {ETIQUETA_EQUIPO})
        falta = ETIQUETA_EQUIPO not in tiene
        print("   %-10s %-22s tiene: %s" % (
            digitos, chat, ", ".join(sorted(tiene)) or "(ninguna)"))
        if sobran:
            print("              del CRM, a quitar: %s" % ", ".join(sobran))
        if not (sobran or falta):
            continue
        if not aplicar:
            print("              (en seco)")
            continue
        try:
            _waha("/api/%s/labels/chats/%s" % (SESION, chat),
                  datos={"labels": quiere}, metodo="PUT")
            hechos += 1
            print("              → queda solo «Equipo»")
        except Exception as fallo:
            errores.append((digitos, str(fallo)[:80]))
            print("              → ERROR: %s" % str(fallo)[:60])
    print()
    return hechos, errores


def _internos_viejo_no_se_usa():
    """Los numeros internos NO se filtran aqui, y es a proposito.

    Decision de Abraham (25/09/2026): "la lista de internos ya se aplica en
    el receptor". Y es cierto: un numero de la lista nunca llega a ser lead
    —el receptor de WhatsApp lo descarta antes de crear lead, Person o copia
    de chat—, y este sincronizador trabaja DESDE los leads. Un numero
    interno no puede entrar por aqui.

    El unico hueco que esto deja son los leads creados ANTES de que su
    numero entrara a la lista (paso con el de Mary el 25/09). Esos se
    borran a mano una vez y no vuelven.
    """
    return set(), ""


# ---------------------------------------------------------------------------
# Lo que dice WhatsApp
# ---------------------------------------------------------------------------

_lid = {}


def chat_id_real(telefono):
    """El chatId que el TELEFONO usa para ese numero: el `@lid`.

    Aqui estaba el bug del 25/09. Poniamos las etiquetas en
    `<numero>@c.us`, que la API acepta sin chistar, y el telefono no
    mostraba ninguna: el WhatsApp de hoy identifica el chat por su `@lid`
    y es ahi donde las lee. Se veia clarisimo mirando el mismo chat por
    los dos ids — @c.us con etiquetas, @lid vacio.

    Se resuelve con la ruta GLOBAL `/api/contacts/check-exists`, con la
    sesion como parametro. La de la sesion
    (`/api/vivero/contacts/check-exists`) NO sirve: toma «check-exists»
    como el id del contacto y devuelve `check-exists@c.us`.

    Vuelve "" si el numero no tiene WhatsApp — y entonces no hay chat que
    etiquetar, que es la respuesta correcta.
    """
    digitos = solo_digitos(telefono)
    if digitos in _lid:
        return _lid[digitos]
    try:
        r = _waha("/api/contacts/check-exists?phone=%s&session=%s"
                  % (digitos, SESION)) or {}
    except Exception:
        _lid[digitos] = ""
        return ""
    # `chatId` es el @lid; si no viene, se cae al `pn` (@c.us) para no
    # quedarse sin nada, pero eso es la excepcion, no lo normal.
    ident = "" if not r.get("numberExists") else (
        r.get("chatId") or r.get("pn") or "")
    _lid[digitos] = ident
    return ident


def etiquetas_del_chat(chat_id):
    """Las etiquetas que ese chat tiene AHORA, preguntando por el chat.

    Antes se armaba el mapa al reves (una vuelta por etiqueta, mirando sus
    chats). Preguntar por el chat es mas directo y, sobre todo, no deja
    lugar a que un id se compare contra otro: se lee el mismo chatId con
    el que se escribe.
    """
    try:
        r = _waha("/api/%s/labels/chats/%s" % (SESION, chat_id)) or []
    except Exception:
        return set()
    return {l["name"] for l in r if l.get("name")}


def etiquetas_de_whatsapp():
    return {l["name"]: l["id"] for l in (_waha("/api/%s/labels" % SESION) or [])}


def chats_por_etiqueta(etiquetas):
    """{chat_id: {nombres de etiqueta}} — una vuelta por etiqueta nuestra."""
    por_chat = {}
    for nombre, ident in etiquetas.items():
        if nombre in DE_WHATSAPP:
            continue
        try:
            chats = _waha("/api/%s/labels/%s/chats" % (SESION, ident)) or []
        except urllib.error.HTTPError:
            continue
        for c in chats:
            cid = c.get("id") if isinstance(c, dict) else c
            por_chat.setdefault(str(cid), set()).add(nombre)
    return por_chat


def deseadas(lead, disponibles):
    """Las etiquetas que ese chat deberia tener, con lo que existe hoy."""
    quiere = []
    if lead["estado"] in ESTADOS_CON_ETIQUETA:
        quiere.append(lead["estado"])
    if lead["interes"] in INTERESES:
        quiere.append(lead["interes"])
    if lead["resp"]:
        quiere.append(a_whatsapp(lead["resp"]))
    if lead["te_toca"]:
        quiere.append(RESPONDER)
    faltan = [q for q in quiere if q not in disponibles]
    return [q for q in quiere if q in disponibles], faltan


def cargar_estado():
    """Lo que el CRM decia en la pasada anterior, por lead.

    Sin esto la lectura de vuelta no puede existir: para saber si el
    telefono cambio algo hay que saber que era antes. Si el archivo no
    esta (primera corrida), no se devuelve nada — lo correcto, porque no
    hay con que comparar.
    """
    try:
        return json.load(open(ESTADO))
    except Exception:
        return {}


def guardar_estado(leads):
    try:
        json.dump({l["ref"]: {"resp": l["resp"], "interes": l["interes"]}
                   for l in leads}, open(ESTADO, "w"))
    except Exception as fallo:
        print("   (no se pudo guardar el estado: %s)" % str(fallo)[:70])


CONSULTA_LABELS = """
query($equipo: ID!) { team(id: $equipo) {
  labels(first: 60) { nodes { id name isGroup parent { name } } } } }
"""

MUT_PONER = """
mutation($i: String!, $l: String!) {
  issueAddLabel(id: $i, labelId: $l) { success } }
"""

MUT_QUITAR = """
mutation($i: String!, $l: String!) {
  issueRemoveLabel(id: $i, labelId: $l) { success } }
"""

CONSULTA_ISSUE = """
query($ref: String!) { issues(first: 1, filter: { number: { eq: $ref } }) {
  nodes { id } } }
"""

_labels_linear = {}


def labels_de_linear():
    """{nombre: (id, grupo)} del equipo LEAD. NUNCA crea ninguna: si el
    nombre no existe, la escritura de vuelta se salta y lo dice."""
    if not _labels_linear:
        d = linear(CONSULTA_LABELS, {"equipo": EQUIPO_LEAD})
        for l in (d["team"]["labels"]["nodes"]):
            if not l.get("isGroup"):
                _labels_linear[l["name"]] = (
                    l["id"], (l.get("parent") or {}).get("name") or "")
    return _labels_linear


def _id_del_issue(ref):
    numero = int(ref.split("-")[-1])
    d = linear("""query($n: Float!) { issues(first: 1, filter:
        { team: { key: { eq: "LEAD" } }, number: { eq: $n } })
        { nodes { id } } }""", {"n": numero})
    nodos = d["issues"]["nodes"]
    return nodos[0]["id"] if nodos else ""


def escribir_en_linear(lead, grupo, nombre_nuevo):
    """Deja en el issue UNA sola etiqueta de ese grupo: la nueva.

    Es la lectura de vuelta: alguien cambio el representante o el interes
    desde el telefono y el CRM se pone al dia. Solo estos dos grupos
    suben; el estado y «Responder» bajan nada mas.
    """
    catalogo = labels_de_linear()
    if nombre_nuevo not in catalogo:
        return False, "la etiqueta %r no existe en Linear (no se crea sola)" % nombre_nuevo
    issue = _id_del_issue(lead["ref"])
    if not issue:
        return False, "no se encontro el issue %s" % lead["ref"]
    nuevo_id, _g = catalogo[nombre_nuevo]
    for nombre, (ident, g) in catalogo.items():
        if g == grupo and nombre != nombre_nuevo:
            try:
                linear(MUT_QUITAR, {"i": issue, "l": ident})
            except Exception:
                pass
    linear(MUT_PONER, {"i": issue, "l": nuevo_id})
    return True, ""


def sincronizar_uno(ref, aplicar=False):
    """Sincroniza UN lead. Lo usa el endpoint de sincronizacion inmediata.

    Vuelve {"lead", "puestas", "quitadas", "motivo"}. No revienta si el
    lead no existe o no tiene telefono: lo dice y se va, porque quien
    llama (Control) no puede quedarse esperando.
    """
    leads = [l for l in leads_del_crm() if l["ref"].upper() == ref.upper()]
    if not leads:
        return {"lead": ref, "motivo": "no es un lead vivo", "puestas": [],
                "quitadas": []}
    lead = leads[0]
    if not lead["telefono"]:
        return {"lead": ref, "motivo": "sin telefono: no hay chat",
                "puestas": [], "quitadas": []}
    etiquetas = {n: i for n, i in etiquetas_de_whatsapp().items()
                 if n not in DE_WHATSAPP}
    chat = chat_id_real(lead["telefono"])
    if not chat:
        return {"lead": ref, "motivo": "el numero no tiene WhatsApp",
                "puestas": [], "quitadas": []}
    tiene = etiquetas_del_chat(chat)
    quiere, faltan = deseadas(lead, etiquetas)
    poner = [q for q in quiere if q not in tiene]
    quitar = [t for t in tiene if t not in quiere]
    if aplicar and (poner or quitar):
        # PUT /labels/chats/{chat} REEMPLAZA la lista completa del chat, asi
        # que se manda lo que debe quedar (`quiere`), no solo lo que falta:
        # mandar el delta borraria lo que ya estaba bien.
        _waha("/api/%s/labels/chats/%s" % (SESION, chat),
              datos={"labels": [{"id": etiquetas[n]} for n in quiere]},
              metodo="PUT")
    return {"lead": ref, "chat": chat, "puestas": poner, "quitadas": quitar,
            "faltan_en_whatsapp": faltan,
            "motivo": "aplicado" if aplicar else "en seco"}


def main():
    aplicar = "--aplicar" in sys.argv
    print("SINCRONIZADOR CRM -> WhatsApp   ·   %s" %
          ("APLICANDO" if aplicar else "EN SECO (no escribe nada)"))
    print("=" * 76)

    sesion = _waha("/api/sessions/%s" % SESION)
    print("sesion %r: %s · numero %s" % (
        SESION, sesion.get("status"), (sesion.get("me") or {}).get("id", "-")))

    etiquetas = etiquetas_de_whatsapp()
    nuestras = {n: i for n, i in etiquetas.items() if n not in DE_WHATSAPP}
    print("etiquetas en WhatsApp: %d (%d de WhatsApp, %d nuestras)" % (
        len(etiquetas), len(etiquetas) - len(nuestras), len(nuestras)))

    leads = leads_del_crm()
    lista_interna, de_donde = internos()
    print("numeros internos: %d · %s" % (len(lista_interna), de_donde))
    print("leads vivos en Linear: %d" % len(leads))
    print()

    estado_previo = cargar_estado()

    faltantes, sin_telefono, saltados, planes, devoluciones = set(), [], [], [], []
    sin_whatsapp = []
    for lead in sorted(leads, key=lambda l: l["ref"]):
        if not lead["telefono"]:
            sin_telefono.append(lead)
            continue
        if lista_interna and solo_digitos(lead["telefono"])[-8:] in lista_interna:
            saltados.append(lead)
            continue
        # El chatId que usa el TELEFONO (@lid), no el @c.us: ahi es donde
        # WhatsApp lee las etiquetas.
        chat = chat_id_real(lead["telefono"])
        if not chat:
            sin_whatsapp.append(lead)
            continue
        tiene = etiquetas_del_chat(chat)
        quiere, faltan = deseadas(lead, nuestras)
        faltantes.update(faltan)
        poner = [q for q in quiere if q not in tiene]
        quitar = [t for t in tiene if t not in quiere]

        # --- lectura de vuelta: empleado e interes ---
        previo = estado_previo.get(lead["ref"], {})
        for grupo, valores, campo in (
                ("responsable", [t for t in tiene if t in REPRESENTANTES], "resp"),
                ("interes", [t for t in tiene if t in INTERESES], "interes")):
            en_wa = valores[0] if valores else ""
            si_resp = campo == "resp"
            en_wa_limpio = a_whatsapp(en_wa) if si_resp else en_wa
            en_crm = lead[campo]
            if en_wa_limpio and en_wa_limpio != en_crm and previo.get(campo, "") == en_crm:
                # WhatsApp cambio y el CRM no: manda el telefono.
                devoluciones.append((lead, grupo, en_crm or "(nada)", en_wa_limpio))
                if si_resp:
                    poner = [p for p in poner if p not in REPRESENTANTES]
                    quitar = [q for q in quitar if q not in REPRESENTANTES]
                else:
                    poner = [p for p in poner if p not in INTERESES]
                    quitar = [q for q in quitar if q not in INTERESES]
        if poner or quitar:
            planes.append((lead, chat, sorted(tiene), poner, quitar))

    print("-" * 76)
    print("%s  (%d de %d leads necesitan cambio)" % (
        "APLICANDO EN CADA CHAT" if aplicar else "QUE HARIA EN CADA CHAT",
        len(planes), len(leads)))
    print("-" * 76)
    hechos, errores = 0, []
    for lead, chat, tiene, poner, quitar in planes:
        print("%-9s %-24s %s" % (lead["ref"], lead["nombre"][:22], chat))
        print("          estado=%-12s interes=%-14s resp=%s%s" % (
            lead["estado"], lead["interes"] or "-", lead["resp"] or "-",
            "  🔴" if lead["te_toca"] else ""))
        print("          tiene:  %s" % (", ".join(tiene) or "(ninguna)"))
        if poner:
            print("          PONER:  %s" % ", ".join(poner))
        if quitar:
            print("          QUITAR: %s" % ", ".join(quitar))
        if aplicar:
            quiere, _f = deseadas(lead, nuestras)
            try:
                # El PUT REEMPLAZA la lista completa del chat: se manda lo
                # que debe quedar, no el delta.
                _waha("/api/%s/labels/chats/%s" % (SESION, chat),
                      datos={"labels": [{"id": nuestras[n]} for n in quiere]},
                      metodo="PUT")
                hechos += 1
                print("          → aplicado")
            except Exception as fallo:
                errores.append((lead["ref"], str(fallo)[:90]))
                print("          → ERROR: %s" % str(fallo)[:70])
        print()

    if devoluciones:
        print("-" * 76)
        print("LECTURA DE VUELTA (alguien lo cambio en el telefono)")
        for lead, grupo, antes, despues in devoluciones:
            print("   %-9s %-22s %s: %s -> %s" % (
                lead["ref"], lead["nombre"][:20], grupo, antes, despues))
            if not aplicar:
                print("             (en seco: no se escribe en Linear)")
                continue
            nombre = a_linear(despues) if grupo == "responsable" else despues
            grupo_linear = "Responsable" if grupo == "responsable" else "Interés"
            ok, motivo = escribir_en_linear(lead, grupo_linear, nombre)
            print("             %s" % ("escrito en Linear" if ok
                                       else "NO se escribio: " + motivo))
            if not ok:
                errores.append((lead["ref"], motivo))
        print()

    if faltantes:
        print("-" * 76)
        print("ETIQUETAS QUE EL CRM PIDE Y NO EXISTEN EN WHATSAPP (%d)" % len(faltantes))
        for f in sorted(faltantes):
            print("   falta: %r" % f)
        print()
    if sin_telefono:
        print("-" * 76)
        print("LEADS SIN TELEFONO (no hay chat que etiquetar): %d" % len(sin_telefono))
        for l in sin_telefono[:10]:
            print("   %-9s %-24s %s" % (l["ref"], l["nombre"][:22], l["estado"]))
        print()
    if sin_whatsapp:
        print("-" * 76)
        print("NUMEROS SIN WHATSAPP (no hay chat que etiquetar): %d" % len(sin_whatsapp))
        for l in sin_whatsapp:
            print("   %-9s %-24s %s" % (l["ref"], l["nombre"][:22], l["telefono"]))
        print()
    if saltados:
        print("-" * 76)
        print("SALTADOS POR SER NUMERO INTERNO: %d" % len(saltados))
        for l in saltados:
            print("   %-9s %s" % (l["ref"], l["nombre"]))
        print()

    hechos_eq, err_eq = etiquetar_equipo(lista_interna, aplicar)
    errores.extend(err_eq)

    print("=" * 76)
    if not aplicar:
        print("EN SECO: no se escribio nada, ni en WhatsApp ni en Linear.")
        return 0

    # El estado se guarda DESPUES de aplicar: si la pasada reventó a medias,
    # la próxima vuelve a comparar contra lo de antes y no pierde el hilo.
    guardar_estado(leads)
    print("APLICADO · %d chats de leads · %d internos · %d leads sin cambio"
          " · %d errores"
          % (hechos, hechos_eq, len(leads) - len(planes), len(errores)))
    for ref, motivo in errores:
        print("   ERROR %s: %s" % (ref, motivo))
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
