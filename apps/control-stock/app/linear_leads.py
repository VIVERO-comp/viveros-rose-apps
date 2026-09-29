"""El equipo LEAD de Linear: el único tablero donde vive el estado del lead.

Fases 4 y 5 del rediseño del CRM (24/09/2026). Este módulo junta en un
solo lugar lo que estaba suelto entre `calendario.py` (el log de leads de
servicio del menú) y `crm_flujo.py` (el drag del kanban viejo): leer los
issues del equipo LEAD con su estado, sus etiquetas y sus comentarios,
moverlos de estado, intercambiar la etiqueta `Resp:` y comentar firmado.

**Un sistema, un trabajo**: Twenty es la ficha del cliente, Odoo es solo
dinero, el calendario son solo fechas y Linear es el tablero. Por eso este
módulo no guarda NADA: no hay tabla, no hay caché en disco, no hay estado
propio. Lo que la pantalla muestra sale de Linear y lo que el empleado
toca se escribe en Linear.

Las reglas del CLAUDE.md que este módulo respeta, y por qué:

- **Las etiquetas nunca se crean solas.** `_label_id()` solo BUSCA: si el
  nombre no existe, queda el aviso en el log y el issue se va sin ella.
  Una etiqueta creada al vuelo nace suelta, fuera de su grupo, y ensucia
  el vocabulario (ya pasó una vez).
- **El responsable va por etiqueta `Resp: <nombre>`, nunca por
  `assignee`**, para no pagar un asiento de Linear por empleado. Son dos
  cosas distintas y no se mezclan: `poner_responsable()` toca etiquetas y
  jamás el asignado del issue.
- **El bot solo firma.** La única key de Linear de la app es la del bot
  «Vivero rose», así que todo lo que este módulo escribe sale firmado por
  él; los issues se quedan asignados a Abraham.
- **El embudo no degrada solo.** `mover_estado()` solo avanza hacia la
  derecha cuando el movimiento es automático; ir hacia atrás exige
  `manual=True` y un motivo, que queda como comentario firmado en el
  issue.

Tres modos, los mismos del calendario y con el MISMO interruptor
(`CALENDARIO_ESCRITURA`), porque es la misma cuenta de Linear y la misma
pregunta: ¿esta instancia escribe en Linear o solo mira?

- Sin `LINEAR_API_KEY`: **modo muestra**, con leads de ejemplo en memoria.
  Es el modo de desarrollo local y el de las pruebas.
- Con clave y `CALENDARIO_ESCRITURA` apagado: **solo lectura** del tablero
  real. La instancia de pruebas del droplet corre así: ve los leads de
  verdad sin poder moverlos.
- Con clave y `CALENDARIO_ESCRITURA=1`: lectura y escritura.
"""

import os
import re
import time
import unicodedata
from datetime import datetime

from . import calendario, colores

TTL_LEADS = 60          # segundos de caché de la lista
TTL_CATALOGO = 900      # estados y etiquetas cambian poquísimo

ZONA_PANAMA = calendario.ZONA_PANAMA


class ErrorLeads(Exception):
    """Falla al hablar con Linear, con el texto que se le muestra al empleado."""


# ---------------------------------------------------------------------------
# Los 8 estados del embudo único (24/09/2026)
#
# `nombre` es el nombre EXACTO de la columna en el equipo LEAD de Linear:
# es lo que amarra un estado de aquí con su estado de allá. El color sale
# de la paleta única (la misma familia que usan el panel /admin y los
# chips del frontend, para que un estado se vea igual en todas las caras).
#
# `auto` dice quién mueve la tarjeta cuando nadie la toca a mano:
# documenta el embudo y sirve de pie en la pantalla.
# ---------------------------------------------------------------------------

ESTADOS = [
    {"clave": "NUEVO", "nombre": "Nuevo",
     "auto": "solo, al nacer el lead"},
    {"clave": "HABLANDO", "nombre": "Hablando",
     "auto": "solo, cuando el cliente escribe"},
    {"clave": "COTIZADO", "nombre": "Cotizado",
     "auto": "solo, al generar la cotización"},
    {"clave": "POR_AGENDAR", "nombre": "Por agendar",
     "auto": "solo, cuando entra un pago real en Odoo"},
    {"clave": "AGENDADO", "nombre": "Agendado",
     "auto": "solo, al crear la actividad del calendario"},
    {"clave": "ENTREGADO", "nombre": "Entregado",
     "auto": "un toque: «Hecha» en el calendario"},
    {"clave": "GANADO", "nombre": "Ganado",
     "auto": "solo, entregado + saldo 0"},
    {"clave": "PERDIDO", "nombre": "Perdido",
     "auto": "barrido de 14 días, o a mano con motivo"},
]
for _e in ESTADOS:
    _e["familia"] = colores.ASIGNACIONES["estado_lead"][_e["clave"]]
    _e["color"] = colores.FAMILIAS[_e["familia"]]["solido_hex"]
    _e["chip"] = colores.chip_estilo(_e["familia"])

POR_CLAVE = {e["clave"]: e for e in ESTADOS}
ORDEN = [e["clave"] for e in ESTADOS]

# La escalera del embudo: los estados por los que una tarjeta AVANZA sola.
# Ganado y Perdido cierran el issue y quedan fuera: ahí no se llega por
# avance automático (Ganado exige saldo 0, Perdido exige motivo).
ESCALERA = ["NUEVO", "HABLANDO", "COTIZADO", "POR_AGENDAR", "AGENDADO",
            "ENTREGADO"]

# Los estados que cierran el issue en Linear (completed / canceled): a un
# lead cerrado no se le mueve la columna por automático.
CERRADOS = {"GANADO", "PERDIDO"}


# ---------------------------------------------------------------------------
# Las etiquetas del equipo LEAD: 5 grupos + una señal suelta
#
# Los nombres son los de Linear, tal cual. Nada de esto se crea: son el
# vocabulario y si un nombre no está, el issue se queda sin la etiqueta.
# ---------------------------------------------------------------------------

GRUPO_ORIGEN = "Origen"
GRUPO_INTERES = "Interés"
GRUPO_MOTIVO = "Motivo de pérdida"
GRUPO_PAGO = "Pago"
GRUPO_RESPONSABLE = "Responsable"

GRUPOS = (GRUPO_ORIGEN, GRUPO_INTERES, GRUPO_MOTIVO, GRUPO_PAGO,
          GRUPO_RESPONSABLE)

# «Te toca» es suelta a propósito: es una señal binaria (hay un cliente
# esperando respuesta), no una categoría. La pone el mensaje del cliente y
# la quita nuestra respuesta.
LABEL_TE_TOCA = "Te toca"

# Las tres señales sueltas del panel de Control (25/09/2026, pedido de
# Abraham): banderas binarias, independientes entre sí y de ningún grupo —
# un lead puede llevar las tres a la vez. UN renglón por señal: sumar o
# quitar una es tocar esta lista sola. El botón de cada una solo aparece en
# la pantalla si Abraham ya creó su etiqueta en Linear (`senales_disponibles`
# más abajo); el código nunca la crea.
LABELS_SENAL = ("Seguimiento", "Importante", "Cliente potencial")

# La segunda etiqueta que «Responder» prende junto a «Te toca» cuando se
# enciende A MANO (28/09/2026, pedido de Abraham: "que no se vaya hasta
# que responda"). El receptor del frontend NO quita «Te toca» con nuestra
# respuesta mientras el issue tenga esta etiqueta puesta — así lo que un
# empleado prende a mano solo lo apaga el mismo botón, nunca el próximo
# mensaje saliente. Puede no existir todavía en Linear (Abraham la crea a
# mano, como las señales): si no está en el catálogo, no se pone y no es
# un error — el botón sigue prendiendo y apagando «Te toca» igual.
LABEL_RESPONDER_A_MANO = "Responder a mano"

# El prefijo del grupo Responsable. El responsable es `Resp: Abraham`, no
# el assignee: sumar a alguien al equipo es crear su etiqueta en Linear,
# sin tocar código (por eso la lista sale del catálogo y no está aquí).
PREFIJO_RESP = "Resp: "

# Las 3 etiquetas del grupo Pago, que escribe Odoo al entrar un pago real.
# Un issue lleva UNA sola: la del saldo de ahora.
LABELS_PAGO = ("Abono 50%", "Pagado 100%", "Cobrar saldo")

# Los 6 motivos de pérdida, con el mismo vocabulario del frontend
# (src/lib/chips-lead.ts) y del select motivoNoAvance de Twenty.
MOTIVOS_PERDIDA = {
    "PRECIO": "Precio",
    "NO_RESPONDIO": "No respondió",
    "SIN_STOCK": "Sin stock",
    "FUERA_DE_ZONA": "Fuera de zona",
    "COMPRO_EN_OTRO_LADO": "Compró en otro lado",
    "SOLO_PREGUNTABA": "Solo preguntaba",
}
MOTIVO_POR_NOMBRE = {v: k for k, v in MOTIVOS_PERDIDA.items()}


def chip_de_motivo(clave):
    """El estilo del chip de un motivo de pérdida (paleta única)."""
    familia = colores.ASIGNACIONES["motivo_no_avance"].get(clave, "gray")
    return colores.chip_estilo(familia)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def configurado():
    """¿Hay credenciales para leer el equipo LEAD real?"""
    return bool((os.environ.get("LINEAR_API_KEY") or "").strip())


def escritura_activa():
    """El MISMO interruptor del calendario (`CALENDARIO_ESCRITURA`).

    No son dos permisos distintos: es la misma cuenta de Linear y la misma
    pregunta. Así la instancia de pruebas del droplet lee el tablero real
    sin riesgo de mover un lead de verdad, con una sola variable.
    """
    return configurado() and (os.environ.get("CALENDARIO_ESCRITURA") or "").strip() == "1"


def modo():
    if not configurado():
        return "muestra"
    return "escritura" if escritura_activa() else "lectura"


def _exigir_escritura():
    if not configurado():
        return  # modo muestra: se escribe en memoria
    if not escritura_activa():
        raise ErrorLeads(
            "Esta instancia mira el tablero de leads pero no escribe en "
            "Linear. Se enciende con CALENDARIO_ESCRITURA=1.")


def _sin_acentos(texto):
    plano = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in plano if not unicodedata.combining(c)).strip().lower()


# ---------------------------------------------------------------------------
# Catálogo del equipo LEAD (estados y etiquetas), cacheado
# ---------------------------------------------------------------------------

# `first: 1` en `teams` NO es decoración: Linear cobra la complejidad de una
# consulta multiplicando los límites de las listas anidadas, y sin `first`
# asume 50 equipos — 50 × 120 etiquetas pasó del tope y la respuesta fue
# "Query too complex" (con el catálogo caído, `responsables()` devolvía
# lista vacía y Control se quedaba con una sola columna). Hay un solo equipo
# LEAD: pedir uno es lo correcto y además cabe de sobra.
CONSULTA_CATALOGO = """
query {
  teams(first: 1, filter: { key: { eq: "LEAD" } }) {
    nodes {
      id
      states(first: 30) { nodes { id name type } }
      labels(first: 60) { nodes { id name isGroup parent { name } } }
    }
  }
}
"""

_catalogo_cache = {"en": 0, "dato": None}


def catalogo(refrescar=False):
    """{"equipo", "estados": {clave: id}, "etiquetas": {nombre: id},
    "grupos": {grupo: [nombres]}, "responsables": [nombres sin prefijo]}.

    Igual que el resto de la app: si hay catálogo guardado sale YA y, si
    venció el TTL, Linear se consulta por detrás.
    """
    if _catalogo_cache["dato"] and not refrescar:
        if time.time() - _catalogo_cache["en"] >= TTL_CATALOGO:
            calendario._en_fondo("leads-catalogo", lambda: catalogo(refrescar=True))
        return _catalogo_cache["dato"]

    datos = _pedir(CONSULTA_CATALOGO)
    equipos = (datos.get("teams") or {}).get("nodes") or []
    if not equipos:
        raise ErrorLeads("Linear no tiene el equipo LEAD.")
    equipo = equipos[0]

    estados = {}
    for estado in (equipo.get("states") or {}).get("nodes") or []:
        for clave, ficha in POR_CLAVE.items():
            if _sin_acentos(estado.get("name")) == _sin_acentos(ficha["nombre"]):
                estados[clave] = estado["id"]

    etiquetas, grupos = {}, {}
    for label in (equipo.get("labels") or {}).get("nodes") or []:
        if label.get("isGroup"):
            grupos.setdefault(label["name"], [])
            continue
        etiquetas[label["name"]] = label["id"]
        padre = (label.get("parent") or {}).get("name") or ""
        grupos.setdefault(padre, []).append(label["name"])

    responsables = sorted(
        n[len(PREFIJO_RESP):] for n in grupos.get(GRUPO_RESPONSABLE, [])
        if n.startswith(PREFIJO_RESP))

    dato = {"equipo": equipo["id"], "estados": estados, "etiquetas": etiquetas,
            "grupos": grupos, "responsables": responsables}
    _catalogo_cache.update({"en": time.time(), "dato": dato})
    return dato


def responsables():
    """Los nombres del grupo Responsable, para los selectores y las
    columnas de Control.

    Salen de las etiquetas de Linear a propósito: sumar a alguien al
    equipo es crear su etiqueta `Resp: <nombre>` allá, sin tocar código ni
    desplegar nada.
    """
    if not configurado():
        return list(_RESPONSABLES_MUESTRA)
    try:
        return catalogo()["responsables"]
    except ErrorLeads:
        return []


def responder_a_mano_disponible():
    """¿Ya existe «Responder a mano» en Linear (o en la muestra)? Mismo
    candado que `senales_disponibles()`: si Abraham no la creó todavía,
    no se pone y no es un error."""
    if not configurado():
        return _MUESTRA_RESPONDER_A_MANO_EXISTE
    try:
        return LABEL_RESPONDER_A_MANO in catalogo()["etiquetas"]
    except ErrorLeads:
        return False


def senales_disponibles():
    """Los nombres de `LABELS_SENAL` que YA existen como etiqueta en Linear.

    El catálogo decide qué botón de señal aparece, nunca al revés: la
    etiqueta no se crea desde acá (regla que no se rompe), así que el día
    que Abraham la crea a mano, el botón aparece solo, sin desplegar nada.
    Si Linear no contesta, ningún botón se muestra — mejor un botón de menos
    que uno que falle al tocarlo.
    """
    if not configurado():
        return set(_MUESTRA_SENALES_EXISTENTES)
    try:
        existentes = catalogo()["etiquetas"]
    except ErrorLeads:
        return set()
    return {n for n in LABELS_SENAL if n in existentes}


def _pedir(consulta, variables=None):
    """Un viaje a Linear. Reusa la puerta del calendario: misma API, misma
    key del bot, mismo manejo de errores."""
    try:
        return calendario._pedir(consulta, variables)
    except calendario.ErrorCalendario as fallo:
        raise ErrorLeads(str(fallo)) from fallo


# ---------------------------------------------------------------------------
# Lectura de leads
# ---------------------------------------------------------------------------

CAMPOS = """
  id identifier title description url createdAt updatedAt
  state { id name type }
  labels(first: 20) { nodes { id name parent { name } } }
"""

# Se traen TODOS los issues del equipo LEAD menos los cancelados
# (Perdido): los cerrados Ganado sí vienen, porque las vistas de Control
# los muestran en su columna. `orderBy: updatedAt` para que, si algún día
# hay más de 250, los vivos no se caigan por culpa de los viejos.
CONSULTA_LISTA = """
query { issues(first: 250, orderBy: updatedAt, filter: {
    team: { key: { eq: "LEAD" } }
  }) { nodes { %s } }
}
""" % CAMPOS


def _nombre_y_pp(titulo):
    """"Laura Porcell (PP-WATHA)" -> ("Laura Porcell", "PP-WATHA")."""
    titulo = (titulo or "").strip()
    encontrado = re.search(r"\((PP-[A-Z0-9]+)\)", titulo)
    pp = encontrado.group(1) if encontrado else ""
    nombre = titulo.split(" (PP-")[0].strip() or titulo
    return nombre, pp


def _celular_de(descripcion):
    """El celular del cliente, sacado de la tarjeta del issue.

    La tarjeta trae el link de WhatsApp (o el bloque Cliente con el
    +507): de ahi sale el numero, sin preguntarle nada a Twenty. Vuelve
    "6999-9901", o "" si la tarjeta no trae telefono.
    """
    texto = descripcion or ""
    encontrado = re.search(r"wa\.me/(\d{8,15})", texto)
    digitos = encontrado.group(1)[-8:] if encontrado else ""
    if not digitos:
        encontrado = re.search(r"\+507\s?(\d{4})[- ]?(\d{4})", texto)
        digitos = (encontrado.group(1) + encontrado.group(2)) if encontrado else ""
    return f"{digitos[:4]}-{digitos[4:]}" if len(digitos) == 8 else ""


def _dias_desde(iso_texto):
    try:
        cuando = datetime.fromisoformat(str(iso_texto).replace("Z", "+00:00"))
        return (datetime.now(ZONA_PANAMA).date()
                - cuando.astimezone(ZONA_PANAMA).date()).days
    except (ValueError, TypeError):
        return 0


def hace_bonito(dias):
    if dias <= 0:
        return "hoy"
    return f"hace {dias} día" + ("s" if dias > 1 else "")


def _estado_de(issue):
    """La clave del embudo de un issue, por el NOMBRE de su columna.

    Por nombre y no por tipo porque cinco de los ocho estados son del
    mismo tipo `started` en Linear: solo el nombre los distingue. Una
    columna que no es del embudo (Backlog, Duplicate) cae en "".
    """
    nombre = (issue.get("state") or {}).get("name") or ""
    for clave, ficha in POR_CLAVE.items():
        if _sin_acentos(nombre) == _sin_acentos(ficha["nombre"]):
            return clave
    return ""


def _normalizar(issue):
    etiquetas = (issue.get("labels") or {}).get("nodes") or []
    nombres = [l.get("name") or "" for l in etiquetas]
    por_grupo = {}
    for label in etiquetas:
        padre = (label.get("parent") or {}).get("name") or ""
        por_grupo.setdefault(padre, []).append(label.get("name") or "")

    def una(grupo):
        return (por_grupo.get(grupo) or [""])[0]

    nombre, pp = _nombre_y_pp(issue.get("title"))
    celular = _celular_de(issue.get("description"))
    clave_estado = _estado_de(issue)
    resp = una(GRUPO_RESPONSABLE)
    dias = _dias_desde(issue.get("createdAt"))
    # La etiqueta de pago dice el saldo SIN preguntarle a Odoo: sirve de
    # respaldo para pintar la tarjeta mientras el saldo real llega.
    pago = una(GRUPO_PAGO)
    return {
        "id": issue["id"],
        "ref": issue.get("identifier") or "",
        "url": issue.get("url") or "",
        "titulo": issue.get("title") or "",
        "nombre": nombre,
        "pp": pp,
        "descripcion": issue.get("description") or "",
        "celular": celular,
        "wa": ("https://wa.me/507" + celular.replace("-", "")) if celular else "",
        "estado": clave_estado,
        "estado_nombre": (issue.get("state") or {}).get("name") or "",
        "estado_ficha": POR_CLAVE.get(clave_estado),
        "cerrado": ((issue.get("state") or {}).get("type") or "") in
                   ("completed", "canceled"),
        "etiquetas": nombres,
        "origen": una(GRUPO_ORIGEN),
        "interes": una(GRUPO_INTERES),
        "pago": pago,
        "motivo": una(GRUPO_MOTIVO),
        "motivo_clave": MOTIVO_POR_NOMBRE.get(una(GRUPO_MOTIVO), ""),
        "resp": resp[len(PREFIJO_RESP):] if resp.startswith(PREFIJO_RESP) else "",
        "te_toca": LABEL_TE_TOCA in nombres,
        "creado": issue.get("createdAt") or "",
        "dias": dias,
        "hace": hace_bonito(dias),
    }


_lista_cache = {"en": 0, "dato": None}

# La generación de la caché (28/09/2026): sube en cada `refrescar()`, o
# sea en cada escritura de Control. `_buscar()` anota con qué generación
# arrancó y, si cambió para cuando termina, TIRA lo que trajo en vez de
# guardarlo — es una foto de ANTES de esa escritura.
#
# Sin esto: un refresco de fondo que arrancó ANTES de una escritura (lo
# dispara el TTL de una pintada anterior) puede terminar DESPUÉS y
# sobreescribir la caché con la foto vieja, pisando el cambio que se
# acababa de hacer. La pantalla parecía no haber hecho nada hasta un
# segundo clic — el bug de "hay que apretar el botón dos veces", y no
# era solo de Responder: pasa con cualquier escritura (estado,
# responsable, señales).
_generacion = {"n": 0}


def listar(refrescar=False):
    """Todos los leads del equipo LEAD, normalizados.

    Mismo patrón de velocidad del resto de la app (pedido del dueño,
    22/09/2026: cambiar de pestaña sin esperar): lo guardado sale al
    instante y, si venció el TTL, Linear se consulta por detrás.
    """
    if not configurado():
        return [dict(l) for l in _muestra()]
    if _lista_cache["dato"] is not None and not refrescar:
        if time.time() - _lista_cache["en"] >= TTL_LEADS:
            calendario._en_fondo("leads-lista", _buscar)
        return [dict(l) for l in _lista_cache["dato"]]
    return [dict(l) for l in _buscar()]


def _buscar():
    generacion = _generacion["n"]
    filas = []
    for issue in (_pedir(CONSULTA_LISTA).get("issues") or {}).get("nodes") or []:
        lead = _normalizar(issue)
        if not lead["estado"]:
            continue  # Backlog, Duplicate y demás: no son del embudo
        filas.append(lead)
    filas.sort(key=lambda l: -l["dias"])  # el más viejo arriba: es el urgente
    if _generacion["n"] == generacion:
        # Nadie escribió mientras esto viajaba: esta foto sigue vigente.
        _lista_cache.update({"en": time.time(), "dato": filas})
    # Si la generación cambió, `filas` es de antes del cambio: se
    # devuelve igual (por si alguien la está esperando en el camino
    # sincrónico), pero NO se guarda — guardarla pisaría lo escrito.
    return filas


def refrescar():
    """Marca la caché vencida y sube su generación. Lo llama toda
    escritura, para que la próxima pintada reconcilie con Linear.

    NO vacía `dato`: los `poner_*`/`mover_estado` ya parchean el lead que
    tocaron (`_parchear_etiqueta`/`_parchear_estado`) antes de llamar
    esto, así que lo que queda cacheado sigue sirviendo para todo lo
    demás. Vaciar forzaría una consulta sincrónica a Linear en la
    próxima pintada — justo lo que la generación de arriba ya vuelve
    innecesario, y más lento.
    """
    _generacion["n"] += 1
    _lista_cache["en"] = 0


def _lead_en_cache(id_o_ref):
    """El dict del lead TAL CUAL vive en la caché (el objeto, no una
    copia) — para parchearlo in situ después de escribir en Linear. None
    si la caché está fría o el lead no aparece ahí."""
    for lead in _lista_cache["dato"] or []:
        if lead["id"] == id_o_ref or lead["ref"] == id_o_ref:
            return lead
    return None


def _campos_derivados(etiquetas):
    """Los campos que `_normalizar()` deriva de la lista de etiquetas de
    un issue (origen, interés, pago, motivo, responsable, «Te toca»),
    recalculados desde el catálogo de grupos — mismo criterio, para que
    un parche de la caché y una lectura fresca de Linear nunca se
    contradigan."""
    grupos = catalogo()["grupos"]

    def una(grupo):
        del_grupo = [n for n in etiquetas if n in (grupos.get(grupo) or [])]
        return del_grupo[0] if del_grupo else ""

    resp = una(GRUPO_RESPONSABLE)
    motivo = una(GRUPO_MOTIVO)
    return {
        "origen": una(GRUPO_ORIGEN), "interes": una(GRUPO_INTERES),
        "pago": una(GRUPO_PAGO), "motivo": motivo,
        "motivo_clave": MOTIVO_POR_NOMBRE.get(motivo, ""),
        "resp": resp[len(PREFIJO_RESP):] if resp.startswith(PREFIJO_RESP) else "",
        "te_toca": LABEL_TE_TOCA in etiquetas,
    }


def _parchear_etiqueta(id_issue, nombre, puesta):
    """Aplica a la caché, EN EL MOMENTO, un cambio de etiqueta que Linear
    ya confirmó — para que la pintada de después de un clic muestre la
    verdad al instante, sin esperar el refresco de fondo (que igual
    reconcilia después, y ya no puede pisar esto: ver `refrescar()`)."""
    lead = _lead_en_cache(id_issue)
    if lead is None:
        return
    etiquetas = lead["etiquetas"]
    if puesta and nombre not in etiquetas:
        etiquetas.append(nombre)
    if not puesta and nombre in etiquetas:
        etiquetas.remove(nombre)
    try:
        lead.update(_campos_derivados(etiquetas))
    except ErrorLeads:
        pass  # la etiqueta ya quedó parcheada; los derivados los trae el refresco


def _parchear_estado(id_issue, clave):
    """Lo mismo que `_parchear_etiqueta`, para un cambio de columna que
    Linear ya confirmó."""
    lead = _lead_en_cache(id_issue)
    if lead is None:
        return
    ficha = POR_CLAVE[clave]
    lead.update({"estado": clave, "estado_nombre": ficha["nombre"],
                "estado_ficha": ficha, "cerrado": clave in CERRADOS})


def en_estado(clave, leads=None):
    """Los leads de un estado del embudo."""
    return [l for l in (leads if leads is not None else listar())
            if l["estado"] == clave]


def uno(id_o_ref, leads=None):
    """El lead por su id de Linear o por su referencia LEAD-NN."""
    buscado = (id_o_ref or "").strip()
    if not buscado:
        return None
    for lead in (leads if leads is not None else listar()):
        if lead["id"] == buscado or lead["ref"] == buscado:
            return lead
    return None


def mensaje_lead_ausente(ref):
    """El error de «no encontré ese lead», con el ref adentro.

    Son DOS casos, no uno (29/09/2026, visto en producción): un ref VACÍO
    no es un lead borrado — es un POST que llegó sin decir cuál (el
    navegador arrastró el enlace de adentro de la tarjeta, no la tarjeta,
    porque todo <a> es arrastrable por naturaleza). Decirle al empleado
    «ya no está en Linear» lo mandaba a buscar un lead borrado que no
    existe. Y cuando el ref sí llegó y no está, el mensaje lo NOMBRA:
    sin el ref no hay forma de averiguar qué pasó.
    """
    ref = (ref or "").strip()
    if not ref:
        return ("No llegó qué lead tocar. Probá arrastrando la tarjeta "
                "entera, no el enlace de adentro.")
    return f"El lead {ref} ya no está en Linear."


# ---------------------------------------------------------------------------
# Escritura: estado, etiquetas, comentarios
# ---------------------------------------------------------------------------

MUTACION_ESTADO = """
mutation($id: String!, $estado: String!) {
  issueUpdate(id: $id, input: { stateId: $estado }) { success }
}
"""

MUTACION_PONER_LABEL = """
mutation($id: String!, $label: String!) {
  issueAddLabel(id: $id, labelId: $label) { success }
}
"""

MUTACION_QUITAR_LABEL = """
mutation($id: String!, $label: String!) {
  issueRemoveLabel(id: $id, labelId: $label) { success }
}
"""

MUTACION_COMENTAR = """
mutation($id: String!, $texto: String!) {
  commentCreate(input: { issueId: $id, body: $texto }) { success }
}
"""


def _label_id(nombre):
    """El id de la etiqueta `nombre`, o None.

    NUNCA la crea (regla del 24/09/2026): las etiquetas del equipo LEAD
    viven en grupos y una creada al vuelo nace suelta, fuera de su grupo,
    ensuciando el vocabulario. Si no existe, queda el aviso en el log y
    quien llama sigue sin ella.
    """
    etiquetas = catalogo()["etiquetas"]
    if nombre in etiquetas:
        return etiquetas[nombre]
    # Puede ser una etiqueta recién creada a mano en Linear: se refresca el
    # catálogo UNA vez antes de darla por inexistente.
    etiquetas = catalogo(refrescar=True)["etiquetas"]
    if nombre in etiquetas:
        return etiquetas[nombre]
    registro_aviso(f'La etiqueta "{nombre}" no existe en Linear y no se crea '
                   f'sola: el issue queda sin ella.')
    return None


def registro_aviso(texto):
    """Un aviso al log. Aparte para que las pruebas puedan mirarlo."""
    import logging
    logging.getLogger("control_stock").warning(texto)


def _linear_dice_que_ya_estaba_asi(fallo, puesta):
    """¿Este error de Linear dice que la etiqueta YA está como se pidió?

    El bug de LEAD-62 (28/09/2026, dos veces): la caché local decía que
    «Te toca» estaba puesta, Linear ya no la tenía, y el quitar reventaba
    con «Label not on issue» — un 400 que NO es una falla, porque el
    estado deseado ya está. Se compara por el MENSAJE puntual que viene
    en la lista `errors` del GraphQL (que `_pedir` mete en el texto de la
    excepción), nunca por «cualquier 400»: cualquier otro error sigue
    reventando como siempre.

    - Al QUITAR: «Label not on issue», textual — es el que salió dos
      veces en el log de producción.
    - Al PONER: el gemelo «ya la tiene» no se pudo provocar en vivo, así
      que se reconoce por familia (un error de esta mutación que diga
      «already» sobre un label). Si Linear usa otra frase, el error
      revienta como hoy — el lado seguro.
    """
    texto = str(fallo)
    if not puesta:
        return "Label not on issue" in texto
    return "already" in texto.lower() and "label" in texto.lower()


def poner_label(id_issue, nombre):
    """Le pone la etiqueta al issue. Idempotente y fail-soft: si la
    etiqueta no existe, el issue se queda sin ella y la acción sigue."""
    _exigir_escritura()
    if not configurado():
        return _muestra_label(id_issue, nombre, poner=True)
    lead = uno(id_issue)
    if lead and nombre in lead["etiquetas"]:
        return True
    label = _label_id(nombre)
    if not label:
        return False
    try:
        datos = _pedir(MUTACION_PONER_LABEL, {"id": id_issue, "label": label})
        exito = bool((datos.get("issueAddLabel") or {}).get("success"))
    except ErrorLeads as fallo:
        if not _linear_dice_que_ya_estaba_asi(fallo, puesta=True):
            raise
        # La caché local iba atrás: Linear ya la tenía. El estado deseado
        # está — se parchea la caché y se sigue, no es un error.
        exito = True
    if exito:
        _parchear_etiqueta(id_issue, nombre, puesta=True)
    refrescar()
    return exito


def quitar_label(id_issue, nombre):
    """Le quita la etiqueta al issue si la tiene. Idempotente."""
    _exigir_escritura()
    if not configurado():
        return _muestra_label(id_issue, nombre, poner=False)
    label = catalogo()["etiquetas"].get(nombre)
    if not label:
        return False
    try:
        datos = _pedir(MUTACION_QUITAR_LABEL, {"id": id_issue, "label": label})
        exito = bool((datos.get("issueRemoveLabel") or {}).get("success"))
    except ErrorLeads as fallo:
        if not _linear_dice_que_ya_estaba_asi(fallo, puesta=False):
            raise
        # «Label not on issue»: la caché local iba atrás y Linear ya no
        # la tenía. El estado deseado está — se parchea y se sigue.
        exito = True
    if exito:
        _parchear_etiqueta(id_issue, nombre, puesta=False)
    refrescar()
    return exito


def poner_label_de_grupo(id_issue, grupo, nombre):
    """Deja UNA sola etiqueta del grupo en el issue: pone `nombre` y quita
    las otras del mismo grupo.

    Así funcionan los cinco grupos del vocabulario (un origen, un interés,
    un motivo, un pago, un responsable): son categorías, no banderas.
    """
    _exigir_escritura()
    lead = uno(id_issue)
    if lead is None:
        return False
    if configurado():
        del_grupo = set(catalogo()["grupos"].get(grupo) or [])
    else:
        del_grupo = set(_MUESTRA_GRUPOS.get(grupo) or [])
    for otra in sorted(set(lead["etiquetas"]) & del_grupo):
        if otra != nombre:
            quitar_label(id_issue, otra)
    return poner_label(id_issue, nombre) if nombre else True


def poner_responsable(id_issue, nombre):
    """El responsable del lead: intercambia la etiqueta `Resp: <nombre>`.

    NUNCA toca el `assignee` del issue (regla que no se rompe): los issues
    se quedan asignados a Abraham y el responsable va por etiqueta, para
    no pagar un asiento de Linear por empleado.
    """
    nombre = (nombre or "").strip()
    return poner_label_de_grupo(
        id_issue, GRUPO_RESPONSABLE, PREFIJO_RESP + nombre if nombre else "")


def poner_pago(id_issue, nombre):
    """La etiqueta de pago vigente (una sola del grupo Pago)."""
    if nombre and nombre not in LABELS_PAGO:
        return False
    return poner_label_de_grupo(id_issue, GRUPO_PAGO, nombre)


def poner_etiqueta_suelta(id_issue, nombre, prendida):
    """El interruptor genérico: prende o apaga UNA etiqueta suelta (fuera
    de cualquier grupo) en el issue. «Te toca» y las tres señales del panel
    de Control comparten este mismo mecanismo — una etiqueta por nombre,
    sin categoría."""
    return poner_label(id_issue, nombre) if prendida else quitar_label(id_issue, nombre)


def poner_te_toca(id_issue, prendida):
    """La señal «Te toca»: la pone el mensaje del cliente, la quita nuestra
    respuesta. Suelta a propósito, no es de ningún grupo."""
    return poner_etiqueta_suelta(id_issue, LABEL_TE_TOCA, prendida)


def comentar(id_issue, texto, autor=""):
    """Un comentario en el issue, firmado por quien lo escribió.

    Lo que la app escribe sale firmado por el bot «Vivero rose» (es su
    key), así que el nombre del empleado va en el texto: sin eso, un
    comentario del sistema y uno de Rubén se verían iguales.
    """
    _exigir_escritura()
    texto = (texto or "").strip()
    if not texto:
        raise ErrorLeads("Escribí la nota primero.")
    firma = f"{texto}\n\n_— {autor} desde Control Viverorose_" if autor else texto
    if not configurado():
        return _muestra_comentar(id_issue, firma)
    datos = _pedir(MUTACION_COMENTAR, {"id": id_issue, "texto": firma[:4000]})
    if not (datos.get("commentCreate") or {}).get("success"):
        raise ErrorLeads("Linear no pudo guardar la nota.")
    return True


CONSULTA_COMENTARIOS = """
query($id: String!) {
  issue(id: $id) { comments(first: 50) {
    nodes { body createdAt user { displayName name } } } }
}
"""


def comentarios(id_issue):
    """Los comentarios del issue, del más viejo al más nuevo."""
    if not configurado():
        return list(_MUESTRA_COMENTARIOS.get(id_issue, []))
    try:
        datos = _pedir(CONSULTA_COMENTARIOS, {"id": id_issue})
    except ErrorLeads:
        return []  # las notas son un extra: sin Linear no tumban la pantalla
    nodos = ((datos.get("issue") or {}).get("comments") or {}).get("nodes") or []
    notas = []
    for nodo in nodos:
        usuario = nodo.get("user") or {}
        notas.append({
            "quien": usuario.get("displayName") or usuario.get("name") or "Alguien",
            "cuando": (nodo.get("createdAt") or "")[:10],
            "fecha": nodo.get("createdAt") or "",
            "texto": nodo.get("body") or "",
        })
    notas.sort(key=lambda n: n["fecha"])
    return notas


def puede_avanzar(desde, hasta):
    """¿El automático puede mover un lead de `desde` a `hasta`?

    La escalera no degrada: un automático solo empuja hacia la derecha, y
    nunca toca un lead ya cerrado (Ganado / Perdido). Ir hacia atrás es
    una CORRECCIÓN MANUAL, que pasa por `mover_estado(manual=True)` y deja
    su comentario firmado.
    """
    if hasta not in POR_CLAVE:
        return False
    if desde in CERRADOS:
        return False
    if hasta in CERRADOS:
        return True  # Ganado y Perdido los decide quien llama, no la escalera
    if desde not in ESCALERA:
        return True  # venía de un estado raro: cualquier destino es avance
    return ESCALERA.index(hasta) > ESCALERA.index(desde)


def mover_estado(id_issue, clave, manual=False, nota="", autor=""):
    """Mueve el lead a la columna `clave` del equipo LEAD.

    Automático (`manual=False`): solo avanza. Si el lead ya está en ese
    estado o más allá, no se toca nada y vuelve False — así un pago que
    llega tarde no devuelve a «Por agendar» un lead ya entregado.

    Manual (`manual=True`): puede ir hacia atrás, pero EXIGE un motivo
    corto, que queda como comentario firmado en el issue («Ruben movió de
    Hablando a Cotizado · le pasé el precio por teléfono»). Sin el motivo
    no se mueve nada.
    """
    if clave not in POR_CLAVE:
        raise ErrorLeads("Ese estado no existe en el embudo.")
    _exigir_escritura()
    lead = uno(id_issue)
    if lead is None:
        raise ErrorLeads("Ese lead ya no está en Linear.")
    if lead["estado"] == clave:
        return False
    if manual:
        if not (nota or "").strip():
            raise ErrorLeads("Para mover una tarjeta a mano hace falta el motivo.")
    elif not puede_avanzar(lead["estado"], clave):
        return False

    if not configurado():
        _muestra_mover(id_issue, clave)
    else:
        estado = catalogo()["estados"].get(clave)
        if not estado:
            raise ErrorLeads(
                f'El equipo LEAD de Linear no tiene la columna '
                f'"{POR_CLAVE[clave]["nombre"]}".')
        datos = _pedir(MUTACION_ESTADO, {"id": id_issue, "estado": estado})
        if not (datos.get("issueUpdate") or {}).get("success"):
            raise ErrorLeads("Linear no pudo mover la tarjeta.")
        _parchear_estado(id_issue, clave)
        refrescar()

    if manual:
        desde = (POR_CLAVE.get(lead["estado"]) or {}).get("nombre") or "ningún estado"
        comentar(
            id_issue,
            f"Movido a mano de **{desde}** a **{POR_CLAVE[clave]['nombre']}**: "
            f"{nota.strip()}",
            autor=autor)
    return True


def marcar_perdido(id_issue, motivo_clave, nota="", autor=""):
    """Perdido a mano: la columna Perdido + su etiqueta de motivo.

    El motivo es obligatorio: un Perdido sin porqué no le sirve a nadie
    cuando alguien revisa el embudo dos semanas después. La nota de quien
    lo cerró y el motivo van en UN solo comentario —dos seguidos diciendo
    lo mismo solo ensucian el issue.
    """
    if motivo_clave not in MOTIVOS_PERDIDA:
        raise ErrorLeads("Falta el motivo de la pérdida.")
    nombre = MOTIVOS_PERDIDA[motivo_clave]
    nota = (nota or "").strip()
    mover_estado(id_issue, "PERDIDO", manual=True,
                 nota=f"{nota} · motivo: {nombre}" if nota else f"motivo: {nombre}",
                 autor=autor)
    poner_label_de_grupo(id_issue, GRUPO_MOTIVO, nombre)
    return True


# ---------------------------------------------------------------------------
# Modo muestra (sin Linear): todo vive en memoria del proceso.
#
# Son los mismos leads que usan las pruebas y el desarrollo local. Se
# parecen al tablero real a propósito: dos por agendar con su etiqueta de
# pago, uno agendado, uno entregado con saldo y varios conversando.
# ---------------------------------------------------------------------------

# Salomón salió del equipo (28/09/2026): sus 4 etiquetas de WhatsApp y
# `Resp: Salomón` de Linear ya se borraron (no la tenía ningún issue). Solo
# quedan los tres que siguen repartiendo trabajo.
_RESPONSABLES_MUESTRA = ("Abraham", "Mary", "Ruben")

# En modo muestra, DOS de las tres señales "existen" (como si Abraham ya
# las hubiera creado a mano) y una no — a propósito, para poder probar el
# candado «si la etiqueta no existe, no hay botón» sin tocar Linear real.
_MUESTRA_SENALES_EXISTENTES = {"Seguimiento", "Importante"}

# En modo muestra, "Responder a mano" SÍ existe por defecto — el camino
# feliz es el más común de probar; una prueba que necesite el camino sin
# ella la apaga con monkeypatch.
_MUESTRA_RESPONDER_A_MANO_EXISTE = True

_MUESTRA_GRUPOS = {
    GRUPO_ORIGEN: ["WhatsApp", "plantaspanama.com", "viverorose.com",
                   "Instagram", "Facebook", "TikTok", "Google", "ChatGPT",
                   "Vivero", "Desconocido"],
    GRUPO_INTERES: ["Plantas", "Eventos", "Paisajismo", "Mantenimiento",
                    "Mayorista"],
    GRUPO_MOTIVO: list(MOTIVOS_PERDIDA.values()),
    GRUPO_PAGO: list(LABELS_PAGO),
    GRUPO_RESPONSABLE: [PREFIJO_RESP + n for n in _RESPONSABLES_MUESTRA],
}

_SEMILLA = [
    ("LEAD-91", "Tamara", "PP-70211", "POR_AGENDAR", 1, "6552-0966",
     ["WhatsApp", "Plantas", "Abono 50%", "Resp: Ruben"]),
    ("LEAD-90", "Juan Carlos Lopez", "PP-70208", "POR_AGENDAR", 2, "6033-2211",
     ["plantaspanama.com", "Plantas", "Pagado 100%"]),
    ("LEAD-89", "Boda Las Nubes", "PP-70207", "AGENDADO", 3, "6788-4102",
     ["Instagram", "Eventos", "Abono 50%", "Resp: Mary"]),
    ("LEAD-88", "Hotel Bristol", "PP-70205", "ENTREGADO", 5, "6209-7754",
     ["WhatsApp", "Mantenimiento", "Cobrar saldo", "Resp: Ruben"]),
    ("LEAD-87", "Ximena Dávila", "PP-70203", "COTIZADO", 4, "6455-1832",
     ["viverorose.com", "Eventos", "Te toca"]),
    ("LEAD-86", "Nedjaira", "PP-70202", "HABLANDO", 2, "6114-9077",
     ["WhatsApp", "Plantas", "Te toca"]),
    ("LEAD-85", "Diego Armando", "PP-70199", "NUEVO", 0, "6987-5510",
     ["TikTok", "Mayorista"]),
    ("LEAD-84", "Soledad", "PP-70195", "GANADO", 8, "6740-0923",
     ["WhatsApp", "Plantas", "Pagado 100%", "Resp: Abraham"]),
    ("LEAD-83", "Monica Gama", "PP-70190", "PERDIDO", 9, "",
     ["Google", "Plantas", "Solo preguntaba"]),
]

_MUESTRA = None
_MUESTRA_COMENTARIOS = {}


def _muestra():
    global _MUESTRA
    if _MUESTRA is None:
        _MUESTRA = []
        for ref, nombre, pp, estado, dias, celular, etiquetas in _SEMILLA:
            ficha = POR_CLAVE[estado]
            por_grupo = {}
            for nombre_label in etiquetas:
                for grupo, nombres in _MUESTRA_GRUPOS.items():
                    if nombre_label in nombres:
                        por_grupo.setdefault(grupo, []).append(nombre_label)
            resp = (por_grupo.get(GRUPO_RESPONSABLE) or [""])[0]
            motivo = (por_grupo.get(GRUPO_MOTIVO) or [""])[0]
            _MUESTRA.append({
                "id": "muestra-" + ref, "ref": ref, "url": "",
                "titulo": f"{nombre} ({pp})", "nombre": nombre, "pp": pp,
                "descripcion": "", "celular": celular,
                "wa": ("https://wa.me/507" + celular.replace("-", "")) if celular else "",
                "estado": estado,
                "estado_nombre": ficha["nombre"], "estado_ficha": ficha,
                "cerrado": estado in CERRADOS, "etiquetas": list(etiquetas),
                "origen": (por_grupo.get(GRUPO_ORIGEN) or [""])[0],
                "interes": (por_grupo.get(GRUPO_INTERES) or [""])[0],
                "pago": (por_grupo.get(GRUPO_PAGO) or [""])[0],
                "motivo": motivo,
                "motivo_clave": MOTIVO_POR_NOMBRE.get(motivo, ""),
                "resp": resp[len(PREFIJO_RESP):] if resp else "",
                "te_toca": LABEL_TE_TOCA in etiquetas,
                "creado": "", "dias": dias, "hace": hace_bonito(dias),
            })
    return _MUESTRA


def reiniciar_muestra():
    """Cada prueba arranca con el tablero de ejemplo limpio."""
    global _MUESTRA
    _MUESTRA = None
    _MUESTRA_COMENTARIOS.clear()
    _catalogo_cache.update({"en": 0, "dato": None})
    refrescar()


def _muestra_uno(id_issue):
    for lead in _muestra():
        if lead["id"] == id_issue or lead["ref"] == id_issue:
            return lead
    return None


def _muestra_mover(id_issue, clave):
    lead = _muestra_uno(id_issue)
    if lead is None:
        return False
    lead.update({"estado": clave, "estado_nombre": POR_CLAVE[clave]["nombre"],
                 "estado_ficha": POR_CLAVE[clave],
                 "cerrado": clave in CERRADOS})
    return True


def _muestra_label(id_issue, nombre, poner):
    lead = _muestra_uno(id_issue)
    if lead is None or not nombre:
        return False
    if poner and nombre not in lead["etiquetas"]:
        lead["etiquetas"].append(nombre)
    if not poner and nombre in lead["etiquetas"]:
        lead["etiquetas"].remove(nombre)
    # Los campos derivados se recalculan para que la pantalla de muestra
    # se comporte igual que con Linear de verdad.
    for grupo, campo in ((GRUPO_ORIGEN, "origen"), (GRUPO_INTERES, "interes"),
                         (GRUPO_PAGO, "pago"), (GRUPO_MOTIVO, "motivo")):
        del_grupo = [n for n in lead["etiquetas"]
                     if n in (_MUESTRA_GRUPOS.get(grupo) or [])]
        lead[campo] = del_grupo[0] if del_grupo else ""
    lead["motivo_clave"] = MOTIVO_POR_NOMBRE.get(lead["motivo"], "")
    resp = next((n for n in lead["etiquetas"] if n.startswith(PREFIJO_RESP)), "")
    lead["resp"] = resp[len(PREFIJO_RESP):] if resp else ""
    lead["te_toca"] = LABEL_TE_TOCA in lead["etiquetas"]
    return True


def _muestra_comentar(id_issue, texto):
    lead = _muestra_uno(id_issue)
    clave = lead["id"] if lead else id_issue
    _MUESTRA_COMENTARIOS.setdefault(clave, []).append({
        "quien": "Vivero rose", "cuando": calendario.hoy().isoformat(),
        "fecha": calendario.hoy().isoformat(), "texto": texto})
    return True
