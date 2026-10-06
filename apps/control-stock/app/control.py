"""La pestaña Control: reparte el trabajo del equipo.

Fase 5 del rediseño del CRM (24/09/2026). **Regla de oro: Control no
guarda nada propio.** El estado, el responsable y las notas se leen y se
escriben en el equipo LEAD de Linear (`linear_leads`). Murieron las tablas
`control_tablero` y `control_visto`, y con ellas el kanban de chats con su
semáforo: aquí ya no hay una verdad local que pueda discrepar del tablero.

Dos vistas:

- **Por empleado** (solo admin): una columna por etiqueta del grupo
  Responsable de Linear, más «Sin asignar». Arrastrar una tarjeta cambia
  la etiqueta `Resp:` del issue — nunca el `assignee`, que sigue siendo
  Abraham. Las columnas salen de las etiquetas, así que sumar a alguien al
  equipo es crear su etiqueta en Linear, sin tocar código ni desplegar.
- **Por estado**: las 8 columnas del embudo. El dueño ve todo con la
  etiqueta de responsable en color; un empleado ve **solo lo suyo**, y ese
  filtro se aplica AQUÍ, en el servidor, no en el navegador.

Arrastrar entre estados es una **corrección manual**: pide un motivo corto
y queda como comentario firmado en el issue («Ruben movió de Hablando a
Cotizado · le pasé el precio por teléfono»). Perdido pide además su motivo
de pérdida. Lo automático del embudo lo mueven el mensaje del cliente, la
cotización, el pago y el calendario — no esta pantalla.

Lo único que esta pestaña guarda es una **tabla mínima de acuse**
(`control_acuse`): a quién ya se le avisó de qué. No es dato del negocio y
no cabe en Linear — es memoria de avisos, para que el mismo aviso no suene
dos veces en cada recarga de la pantalla.
"""

import os
import re
import time

import httpx
from datetime import date, datetime, timedelta
from urllib.parse import quote

from . import (agenda, avisos, calendario, colores, cot_lead, cotizaciones, crm_twenty,
               datos_roles, linear_leads, mantenimiento, resumen, ventas)
from .datos import ZONA_PANAMA, _db

VISTAS = ("empleado", "estado")

# Apagada una semana a pedido de Abraham (28/09/2026): "Por empleado" es
# la vista de reparto, y mientras la prueba, prefiere que nadie la toque
# por error. Vuelve SOLA el 5 de octubre — sin desplegar nada, la fecha es
# la única bandera.
VISTA_EMPLEADO_VUELVE = date(2026, 10, 5)


def vista_empleado_apagada():
    """¿Todavía no llegó el día en que "Por empleado" vuelve?

    En zona de Panamá (`ZONA_PANAMA`), nunca la del servidor a secas — la
    diferencia de zonas entre los dos droplets ya costó caro una vez.
    """
    return datetime.now(ZONA_PANAMA).date() < VISTA_EMPLEADO_VUELVE

# El texto de "nadie lo tiene" en las tarjetas. La columna de Sin asignar
# va primera a propósito: es la que hay que vaciar.
SIN_ASIGNAR = "Sin asignar"


def iniciar_tablas():
    with _db() as con:
        # El acuse de un aviso: `clave` dice de qué aviso se trata
        # ("te-toca:LEAD-91", "wa-label:LEAD-91:Ruben"). Una fila = ese
        # aviso ya se dio. Nada más: ni estado, ni columna, ni motivo —
        # eso vive en Linear.
        con.execute("""
            CREATE TABLE IF NOT EXISTS control_acuse (
                clave TEXT PRIMARY KEY,
                cuando TEXT NOT NULL
            )
        """)
        # La caché del orden (25/09/2026): cuándo fue el último mensaje del
        # CLIENTE de cada lead, como EPOCH — nunca texto: comparar horas
        # escritas entre máquinas de zonas distintas ya costó caro una vez
        # (el almacén de WAHA). Es caché, no dato del negocio: si la tabla
        # se borra, se vuelve a llenar sola en la próxima pasada de fondo.
        con.execute("""
            CREATE TABLE IF NOT EXISTS control_espera (
                ref TEXT PRIMARY KEY,
                epoch INTEGER NOT NULL
            )
        """)
        # El motivo del Recordatorio (29/09/2026): qué espera el cliente
        # («espera Monstera grande, llega en octubre»). Texto de APOYO de
        # pantalla, como `control_espera` — la historia durable es el
        # comentario firmado en el issue, que el camino manual ya deja. Una
        # fila por lead; `cerrado` (epoch) se llena cuando el producto
        # llegó o el lead salió de Recordatorio: la fila queda de historia
        # y la tarjeta deja de mostrarla. Un lead movido a Recordatorio
        # directo en Linear no pasa por aquí: su tarjeta dice «sin motivo
        # anotado», nunca un motivo viejo de otra vuelta.
        con.execute("""
            CREATE TABLE IF NOT EXISTS recordatorio_motivo (
                ref TEXT PRIMARY KEY,
                motivo TEXT NOT NULL,
                autor TEXT NOT NULL DEFAULT '',
                epoch INTEGER NOT NULL,
                cerrado INTEGER
            )
        """)


def _ya_avisado(clave):
    """True si este aviso ya se dio.

    La base es la que decide, con un INSERT que no hace nada si la fila ya
    existe: dos pestañas abiertas a la vez no mandan el mismo aviso dos
    veces.
    """
    iniciar_tablas()
    ahora = datetime.now(ZONA_PANAMA).isoformat()
    with _db() as con:
        nuevo = con.execute(
            "INSERT OR IGNORE INTO control_acuse (clave, cuando) VALUES (?, ?)",
            (clave, ahora)).rowcount
    return not nuevo


def _olvidar_acuse(prefijo):
    """Olvida los acuses que empiezan con `prefijo`: cuando la señal se
    apaga, el aviso tiene que poder sonar de nuevo la próxima vez."""
    iniciar_tablas()
    with _db() as con:
        con.execute("DELETE FROM control_acuse WHERE clave LIKE ?", (prefijo + "%",))


# ---------------------------------------------------------------------------
# El motivo del Recordatorio (29/09/2026): qué espera el cliente. La tabla
# es apoyo de pantalla; la historia durable vive en el comentario del issue.
# ---------------------------------------------------------------------------

def _guardar_motivo_recordatorio(ref, motivo, autor=""):
    """El motivo vigente del lead: una fila por ref, la nueva pisa la
    vieja (volver a Recordatorio es una vuelta nueva, no la de antes)."""
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO recordatorio_motivo (ref, motivo, autor, epoch, cerrado) "
            "VALUES (?, ?, ?, ?, NULL) "
            "ON CONFLICT(ref) DO UPDATE SET motivo = excluded.motivo, "
            "autor = excluded.autor, epoch = excluded.epoch, cerrado = NULL",
            (ref, (motivo or "").strip(), autor or "", int(time.time())))


def _cerrar_motivo_recordatorio(ref):
    """El lead salió de Recordatorio: la fila queda de historia (con su
    epoch) y deja de mostrarse. Sin esto, una vuelta futura a Recordatorio
    hecha directo en Linear mostraría el motivo viejo como si fuera de
    ahora."""
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "UPDATE recordatorio_motivo SET cerrado = ? "
            "WHERE ref = ? AND cerrado IS NULL",
            (int(time.time()), ref))


def motivo_recordatorio(ref):
    """El motivo VIGENTE del lead, o "" — la tarjeta pinta entonces
    «sin motivo anotado» (el caso del lead movido directo en Linear)."""
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT motivo FROM recordatorio_motivo "
            "WHERE ref = ? AND cerrado IS NULL", (ref,)).fetchone()
    return fila["motivo"] if fila else ""


def _motivos_recordatorio_vigentes():
    """{ref: motivo} de todos los vigentes, en UNA consulta — para la
    columna del tablero, que trae varios leads."""
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT ref, motivo FROM recordatorio_motivo "
            "WHERE cerrado IS NULL").fetchall()
    return {f["ref"]: f["motivo"] for f in filas}


# ---------------------------------------------------------------------------
# El enganche para WAHA (Fase W): hoy el aviso es manual
# ---------------------------------------------------------------------------

def registro_aviso(texto):
    """Un aviso al log. Aparte para que las pruebas puedan mirarlo — y
    propio de este módulo: tomarlo prestado de otro es cómo se cuela un
    NameError que solo aparece el día que algo falla de verdad."""
    import logging
    logging.getLogger("control_stock").warning(texto)


def _sincro_url():
    return (os.environ.get("SINCRO_URL") or "").strip()


def _sincro_secreto():
    # `SINCRO_SECRET`, el MISMO nombre que en ~/waha/.env del droplet del
    # CRM. Es el mismo secreto para la misma puerta: dos nombres para una
    # sola cosa es cómo se pierde media hora buscando por qué no anda.
    return (os.environ.get("SINCRO_SECRET") or "").strip()


def etiquetar_en_whatsapp(lead_ref):
    """Pide al sincronizador que deje YA las etiquetas de ese lead en su
    chat de WhatsApp, en vez de esperar su pasada de cada 2 minutos.

    **No bloquea y no puede tumbar nada.** Sale en un hilo aparte con
    timeout corto, y si el endpoint falla o tarda, la pantalla sigue igual:
    solo queda el error en el log y el sincronizador lo arregla en la
    próxima pasada. Por eso vuelve `True` cuando el aviso se DESPACHÓ, no
    cuando la etiqueta quedó puesta — desde acá eso no se puede saber sin
    hacer esperar a quien está usando la app.

    Vive en el droplet del CRM, junto a WAHA, y se le llega por la red
    privada (`10.116.0.3:3002`): WAHA escucha solo en su localhost.
    """
    url, secreto = _sincro_url(), _sincro_secreto()
    if not (url and secreto and lead_ref):
        return False

    def tarea():
        try:
            respuesta = httpx.post(
                url, json={"lead": lead_ref},
                headers={"Authorization": "Bearer " + secreto},
                timeout=6.0)
            if respuesta.status_code >= 300:
                registro_aviso(
                    "el sincronizador de WhatsApp rechazó %s: HTTP %s"
                    % (lead_ref, respuesta.status_code))
        except Exception as fallo:
            # Nunca se propaga: Control no depende de WhatsApp.
            registro_aviso("el sincronizador de WhatsApp no contestó por %s: %s"
                           % (lead_ref, fallo))

    calendario._en_fondo("sincro-wa-" + lead_ref, tarea)
    return True


def waha_activo():
    """¿Ya se puede etiquetar en WhatsApp desde el código?

    Es True cuando están puestas las dos variables del endpoint de
    sincronización (`SINCRO_URL` y `SINCRO_SECRET`). Mientras falte
    alguna, la pantalla sigue pidiendo el aviso manual («Pon en WhatsApp la
    etiqueta: X»): prenderlo antes de que funcione dejaría al equipo
    creyendo que la etiqueta se puso sola.
    """
    return bool(_sincro_url() and _sincro_secreto())


# ---------------------------------------------------------------------------
# El orden dentro de cada columna (25/09/2026, pedido de Abraham): primero
# las tarjetas con «Te toca», y entre esas, arriba la que más tiempo lleva
# esperando. El reloj es el ÚLTIMO MENSAJE ENTRANTE del cliente en Twenty —
# el mismo que ya usa `resumen._ultimo_mensaje_del_cliente` — nunca
# `updatedAt` del issue, que la migración del 24/09 dejó mintiendo en los
# 29 issues viejos.
#
# Son ~20 consultas a Twenty (una por lead), así que la pantalla no las
# espera (regla del 22/09: cambiar de vista se siente como una app). Una
# tabla local chica (`control_espera`) guarda el epoch de cada lead y se
# refresca por detrás, con el mismo patrón que `avisar_en_fondo` y
# `wa_autor.aplicar_en_fondo`: la pintada lee lo guardado y sigue.
# ---------------------------------------------------------------------------

TTL_ESPERA = 120  # la misma cadencia del sincronizador de WhatsApp


def _espera_guardada(ref):
    """El epoch guardado del último mensaje del cliente de ese lead, o
    `None` si la caché todavía no lo tiene (fría, o el lead es nuevo)."""
    iniciar_tablas()
    with _db() as con:
        fila = con.execute(
            "SELECT epoch FROM control_espera WHERE ref = ?", (ref,)).fetchone()
    return fila["epoch"] if fila else None


def _guardar_espera(ref, epoch):
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO control_espera (ref, epoch) VALUES (?, ?) "
            "ON CONFLICT(ref) DO UPDATE SET epoch = excluded.epoch",
            (ref, int(epoch)))


def _refrescar_espera(leads):
    """Trae de Twenty el último mensaje del cliente de cada lead vivo.

    Un lead cerrado (Ganado, Perdido) no entra: a nadie le importa hace
    cuánto esperó uno que ya no está en juego. Reusa la MISMA lógica del
    resumen de las 7 (`resumen._ultimo_mensaje_del_cliente`) en vez de
    volver a escribirla: es la que ya sabe pedirle a Twenty UN solo
    `filter` con `and(...)`, la trampa que costó encontrar una vez.
    """
    for lead in leads:
        if lead["estado"] in linear_leads.CERRADOS:
            continue
        try:
            iso = resumen._ultimo_mensaje_del_cliente(lead)
        except Exception:
            continue  # era un refresco: lo guardado sigue sirviendo
        if not iso:
            continue
        try:
            cuando = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        _guardar_espera(lead["ref"], cuando.timestamp())


_espera_estado = {"en": 0}


def refrescar_espera_en_fondo(leads):
    """Pide el refresco de la caché de espera, sin que la pantalla lo
    espere. `TTL_ESPERA` evita mandar ~20 consultas a Twenty en cada
    recarga; `calendario._en_fondo` evita dos refrescos a la vez."""
    if time.time() - _espera_estado["en"] < TTL_ESPERA:
        return

    def tarea():
        _refrescar_espera(leads)
        _espera_estado["en"] = time.time()

    calendario._en_fondo("control-espera", tarea)


def _epoch_de_creado(lead):
    """El desempate cuando la caché no tiene el lead (fría, o recién
    llegado): el epoch de `createdAt` si Linear lo dio, o — en modo
    muestra, que no trae fecha real — `dias` restado de ahora. Nunca un
    orden que baile entre recargas."""
    creado = lead.get("creado") or ""
    if creado:
        try:
            return datetime.fromisoformat(
                str(creado).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            pass
    dias = lead.get("dias") or 0
    return (datetime.now(ZONA_PANAMA) - timedelta(days=dias)).timestamp()


def _clave_espera(lead):
    """El epoch guardado si la caché lo tiene; si no, el de `createdAt` —
    más chico, más urgente: el que espera desde hace más lleva arriba."""
    epoch = _espera_guardada(lead["ref"])
    return epoch if epoch is not None else _epoch_de_creado(lead)


def _orden_columna(leads):
    """Primero «Te toca», ordenados por hace-cuánto-espera; después, el
    resto tal como venía (por `dias`, lo que ya hacía `linear_leads._buscar`)."""
    con_te_toca = [l for l in leads if l.get("te_toca")]
    resto = [l for l in leads if not l.get("te_toca")]
    con_te_toca.sort(key=_clave_espera)
    return con_te_toca + resto


# ---------------------------------------------------------------------------
# Las dos vistas
# ---------------------------------------------------------------------------

# El «hace N días» de la tarjeta se resalta a partir de aquí (T2,
# 29/09/2026, maqueta «Control más simple»): un lead que lleva 5 días o
# más en el tablero merece que el ojo lo agarre. El umbral vive en
# Python — la plantilla solo pinta la clase que esto decide.
DIAS_HACE_ALERTA = 5


def hace_alerta(dias):
    """¿El «hace N días» de la tarjeta va resaltado?"""
    return (dias or 0) >= DIAS_HACE_ALERTA


# El chip de pago de la tarjeta (diseño Orquesta, 2/10/2026): rojo solo
# para plata que falta cobrar, verde para lo saldado. La clase la decide
# Python — la plantilla no compara textos (regla 10).
_PAGO_DEBE = ("Abono 50%", "Cobrar saldo")
_PAGO_OK = ("Pagado 100%",)


def _clase_pago(pago):
    """'debe' | 'ok' | '' según la etiqueta de pago que puso Odoo."""
    if pago in _PAGO_DEBE:
        return "debe"
    if pago in _PAGO_OK:
        return "ok"
    return ""


def _tarjeta(lead):
    """El lead listo para la tarjeta: lo que se ve y nada más.

    `monto` nace en None y solo lo llena `_poner_montos`, cuando hay un
    alcance que diga quién está mirando: sin orden real conectada —o sin
    permiso de ver la plata— la tarjeta no pinta ningún número.
    """
    estado = lead.get("estado_ficha") or {}
    return dict(lead, **{
        "monto": None,
        "estado_titulo": estado.get("nombre") or lead.get("estado_nombre") or "",
        "estado_chip": estado.get("chip") or "",
        "motivo_chip": (linear_leads.chip_de_motivo(lead["motivo_clave"])
                        if lead.get("motivo_clave") else ""),
        "resp_titulo": lead.get("resp") or SIN_ASIGNAR,
        "hace_alerta": hace_alerta(lead.get("dias")),
        # Diseño Orquesta (2/10/2026): el color del interés sale de
        # paleta.json (colores.py), nunca de un hex en la plantilla. Sin
        # interés conocido, "" y la tarjeta queda neutra.
        "interes_color": colores.color_interes(lead.get("interes")),
        "interes_chip": colores.chip_interes(lead.get("interes")),
        "pago_clase": _clase_pago(lead.get("pago") or ""),
    })


def _vivos(leads):
    """Los leads que siguen en juego: los cerrados (Ganado, Perdido) no se
    reparten entre empleados — ya no hay trabajo que hacerles."""
    return [l for l in leads if l["estado"] not in linear_leads.CERRADOS]


# ---------------------------------------------------------------------------
# El monto de la tarjeta (BLOQUE 43, lienzo de Roles): el nombre y la plata
# arriba, el interés y la persona abajo. El número sale de la ORDEN REAL
# conectada al lead (cot_lead) — nunca de una suma de esta app, nunca de un
# promedio, nunca de un cero por defecto. Sin orden real, la tarjeta no
# lleva monto y eso es la verdad: todavía no hay plata que mostrar.
# ---------------------------------------------------------------------------

def _poner_montos(tarjetas, alcance_actual):
    """Le pone su `monto` a cada tarjeta que lo tenga Y que esta sesión
    pueda ver (`puede_ver_plata`), en UNA sola consulta a Odoo.

    Sin `alcance_actual` no se le pregunta nada a Odoo: así las llamadas
    sueltas a `tablero_por_estado()` —las de las pruebas y las de los
    avisos— siguen costando cero.
    """
    if not alcance_actual:
        return
    visibles = [t for t in tarjetas if puede_ver_plata(t, alcance_actual)]
    if not visibles:
        return
    por_lead = cot_lead.plata_de_varios(visibles).get("por_lead") or {}
    for tarjeta in visibles:
        tarjeta["monto"] = por_lead.get(tarjeta["ref"])


def _total_de_columna(leads, alcance_actual):
    """La suma del pie de la columna (lienzo de Roles), o None.

    Solo para quien ve TODA la plata (Director y Finanzas): un total
    armado con los montos que a alguien le tocan y sin los que no, sería
    un número que no es el de nadie. Y sin un solo monto conocido tampoco
    se pinta: un `$0.00` diría que no hay plata, cuando lo cierto es que
    no se sabe.
    """
    if not (alcance_actual or {}).get("plata_todo"):
        return None
    montos = [l["monto"]["total"] for l in leads if l.get("monto")]
    return round(sum(montos), 2) if montos else None


def tablero_por_empleado(leads=None, alcance_actual=None):
    """[{clave, titulo, pie, leads}] — una columna por responsable.

    «Sin asignar» primero: es la que hay que vaciar. Después cada `Resp:`
    de Linear, en orden. Las columnas SALEN de las etiquetas, así que
    sumar a alguien al equipo es crear su etiqueta allá.
    """
    leads = _vivos([_tarjeta(l) for l in (leads if leads is not None
                                          else linear_leads.listar())])
    _poner_montos(leads, alcance_actual)
    columnas = [{"clave": "", "titulo": SIN_ASIGNAR,
                 "pie": "Nadie los tiene: repártelos."}]
    for nombre in linear_leads.responsables():
        columnas.append({"clave": nombre, "titulo": nombre,
                         "pie": f"Lo que le toca a {nombre}."})
    for columna in columnas:
        columna["leads"] = _orden_columna(
            [l for l in leads if (l["resp"] or "") == columna["clave"]])
        columna["total"] = _total_de_columna(columna["leads"], alcance_actual)
    return columnas


def tablero_por_estado(leads=None, alcance_actual=None):
    """[{clave, titulo, pie, leads}] — las 8 columnas del embudo.

    Sin filtro por responsable: desde el 28/09/2026 («en crm solo admin lo
    puede ver, que todos lo puedan ver», Abraham) TODOS ven el tablero
    completo, empleados incluidos. Lo que cada quien puede MOVER sigue
    siendo otra cosa: eso lo decide `puede_tocar()` con su `Resp:` propio.
    """
    todos = [_tarjeta(l) for l in (leads if leads is not None
                                   else linear_leads.listar())]
    _poner_montos(todos, alcance_actual)
    columnas = []
    for estado in linear_leads.ESTADOS:
        de_esta = _orden_columna(
            [l for l in todos if l["estado"] == estado["clave"]])
        columnas.append({
            "clave": estado["clave"], "titulo": estado["nombre"],
            "color": estado["color"], "chip": estado["chip"],
            "pie": estado["auto"],
            "leads": de_esta,
            "total": _total_de_columna(de_esta, alcance_actual),
        })
        # Solo la columna Recordatorio pinta el motivo (29/09/2026): las
        # demás tarjetas no cambian. Una consulta para toda la columna, y
        # "" cuando el lead llegó movido directo en Linear — la plantilla
        # lo pinta como «sin motivo anotado».
        if estado["clave"] == "RECORDATORIO" and columnas[-1]["leads"]:
            vigentes = _motivos_recordatorio_vigentes()
            for l in columnas[-1]["leads"]:
                l["recordatorio_motivo"] = vigentes.get(l["ref"], "")
    return columnas


def alcance(empleada, es_admin):
    """Qué vistas puede ver quien está en la sesión, y qué puede mover.

    El dueño ve las dos vistas — salvo que «Por empleado» esté apagada
    (`vista_empleado_apagada()`), en cuyo caso ni el dueño la ve: es el
    candado del servidor, no solo el botón que no se pinta. Un empleado ve
    solo «Por estado»: la vista «Por empleado» es de reparto, y repartir
    es cosa del dueño.

    Desde el 28/09/2026 el tablero se VE completo para todos («en crm solo
    admin lo puede ver, que todos lo puedan ver», Abraham): ya no hay
    filtro de vista por responsable. Lo que sí sigue es el permiso de
    TOCAR: `resp_propio` es la etiqueta `Resp:` del empleado, y
    `puede_tocar()` la usa para que cada quien mueva solo lo suyo. Ver y
    mover son dos cosas distintas a propósito — vaciar el filtro sin
    separar esto dejaría al empleado sin poder mover ni sus propios leads.
    """
    vistas_admin = [v for v in VISTAS
                    if v != "empleado" or not vista_empleado_apagada()]
    if es_admin:
        return {"vistas": vistas_admin, "resp_propio": "", "admin": True,
                "plata_todo": True}
    return {"vistas": ["estado"],
            "resp_propio": agenda.responsable_de_empleada(empleada),
            "admin": False,
            "plata_todo": _ve_toda_la_plata(empleada)}


# Los dos roles que ven la plata de CUALQUIER lead (BLOQUE 43, lienzo de
# Roles): el Director —que además es admin— y Finanzas. Se comparan por
# SLUG, nunca por el nombre visible de la fila: renombrar el rol desde
# Ajustes no suelta ni afloja este candado.
SLUGS_DE_PLATA = (datos_roles.SLUG_DIRECTOR, datos_roles.SLUG_FINANZAS)


def _ve_toda_la_plata(empleada):
    """¿Esta sesión ve el dinero de todos los leads, no solo el de los
    suyos? Solo Director y Finanzas. Si la tabla de roles todavía no
    existe (una instalación recién nacida), la respuesta es NO: el
    candado falla cerrado."""
    try:
        mios = datos_roles.roles_activos_de((empleada or {}).get("id") or "")
    except Exception:
        return False
    return any(r.get("slug") in SLUGS_DE_PLATA for r in mios)


def puede_ver_plata(lead, alcance_actual):
    """¿Quien está en la sesión puede ver el DINERO de ESTE lead?

    El mismo criterio que la ficha de contacto: lo ve quien lo atiende (su
    `Resp:`), el Director y Finanzas — nadie más. Ver el tablero completo
    (28/09/2026) nunca fue ver la plata de todos: el monto de la tarjeta y
    el de la ficha son lo que cobra otra persona.

    Es un candado de LECTURA y vive aparte de `puede_tocar()` a propósito:
    quien puede tocar un lead también ve su plata, pero Finanzas ve la
    plata sin poder mover nada.
    """
    alcance_actual = alcance_actual or {}
    if alcance_actual.get("admin") or alcance_actual.get("plata_todo"):
        return True
    propio = alcance_actual.get("resp_propio") or ""
    return bool(propio) and ((lead or {}).get("resp") or "") == propio


def vista_pedida(pedida, alcance_actual):
    """La vista que se pinta: la pedida si se puede, y si no la primera que
    le toca (el dueño abre en «Por empleado», el empleado en «Por estado»)."""
    if pedida in alcance_actual["vistas"]:
        return pedida
    return alcance_actual["vistas"][0]


def puede_tocar(lead, alcance_actual):
    """¿Quien está en la sesión puede mover ESTE lead?

    El dueño, todo. Un empleado, solo los suyos (los de su `Resp:`, que
    viaja en `resp_propio`) — y eso se verifica aquí, en el servidor, no
    confiando en que el navegador no mande lo que no debe. Que desde el
    28/09/2026 el empleado VEA todos los leads no cambia esto: repartir y
    mover lo ajeno siguen siendo cosa del dueño.
    """
    if alcance_actual["admin"]:
        return True
    return bool(alcance_actual["resp_propio"]) and (
        (lead or {}).get("resp") or "") == alcance_actual["resp_propio"]


# ---------------------------------------------------------------------------
# Repartir: arrastrar cambia la etiqueta `Resp:`
# ---------------------------------------------------------------------------

def mover_a_empleado(ref, nombre, autor=""):
    """El lead pasa a manos de `nombre` (o a Sin asignar con "").

    Devuelve ("aviso", "error"). El aviso lleva el recordatorio de poner la
    etiqueta en WhatsApp a mano mientras WAHA no exista, y ese recordatorio
    se da UNA vez por lead y responsable (la tabla de acuse), para que no
    reaparezca en cada recarga.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    nombre = (nombre or "").strip()
    if nombre and nombre not in linear_leads.responsables():
        # Las etiquetas no se crean solas: si el nombre no existe en
        # Linear, aquí se dice en vez de inventarlo.
        return "", f"No existe la etiqueta «Resp: {nombre}» en Linear."
    anterior = lead.get("resp") or ""
    if anterior == nombre:
        return "", ""
    try:
        linear_leads.poner_responsable(lead["id"], nombre)
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    linear_leads.refrescar()

    # El lead cambió de manos: el recordatorio viejo ya no aplica.
    _olvidar_acuse(f"wa-label:{lead['ref']}:")
    if not nombre:
        return f"{lead['nombre']} quedó sin asignar.", ""

    aviso = f"{lead['nombre']} es de {nombre}."
    # El enganche de la Fase W, ya llamado: cuando WAHA ande, esto etiqueta
    # el chat y el recordatorio manual deja de salir.
    if waha_activo():
        if etiquetar_en_whatsapp(lead["ref"]):
            return aviso + " La etiqueta de WhatsApp se está poniendo sola.", ""
    if not _ya_avisado(f"wa-label:{lead['ref']}:{nombre}"):
        aviso += f" Pon en WhatsApp la etiqueta: {nombre}"
        if anterior:
            aviso += f" (y quita la de {anterior})"
        aviso += "."
    return aviso, ""


# ---------------------------------------------------------------------------
# Corregir el estado a mano: exige motivo y queda firmado
# ---------------------------------------------------------------------------

def mover_a_estado(ref, clave, nota="", motivo="", autor=""):
    """Corrección manual del estado. Devuelve ("aviso", "error").

    El motivo no es un capricho: el embudo se mueve solo, así que una
    tarjeta empujada a mano es una excepción, y en dos semanas nadie se
    acuerda de por qué. Queda como comentario firmado en el issue.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    if clave not in linear_leads.POR_CLAVE:
        return "", "Ese estado no existe en el embudo."
    if lead["estado"] == clave:
        return "", ""
    nota = (nota or "").strip()
    if not nota:
        return "", "Escribí por qué la moviste."
    try:
        if clave == "PERDIDO":
            if motivo not in linear_leads.MOTIVOS_PERDIDA:
                return "", "Falta el motivo de la pérdida."
            # Un solo comentario con la nota Y el motivo adentro.
            linear_leads.marcar_perdido(lead["id"], motivo, nota=nota, autor=autor)
        else:
            linear_leads.mover_estado(lead["id"], clave, manual=True,
                                      nota=nota, autor=autor)
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    # El motivo del Recordatorio (29/09/2026): la nota del modal ES qué
    # espera el cliente — se guarda para la tarjeta (el comentario del
    # issue ya la lleva, esa es la historia durable). Y al salir de
    # Recordatorio por CUALQUIER camino manual, el motivo deja de estar
    # vigente: queda de historia con su epoch.
    if clave == "RECORDATORIO":
        _guardar_motivo_recordatorio(lead["ref"], nota, autor=autor)
    elif lead["estado"] == "RECORDATORIO":
        _cerrar_motivo_recordatorio(lead["ref"])
    linear_leads.refrescar()
    # El estado tambien es una etiqueta del chat: que no espere los 2
    # minutos del sincronizador.
    if waha_activo():
        etiquetar_en_whatsapp(lead["ref"])
    desde = (linear_leads.POR_CLAVE.get(lead["estado"]) or {}).get("nombre") or "—"
    hasta = linear_leads.POR_CLAVE[clave]["nombre"]
    return f"{lead['nombre']}: {desde} → {hasta}. Quedó anotado en el issue.", ""


def ya_llego(ref, autor=""):
    """El producto que el cliente esperaba LLEGÓ: el botón «Ya llegó» de
    la columna Recordatorio (29/09/2026).

    Va por el camino manual de siempre (`mover_a_estado`, comentario
    firmado con el motivo adentro: «Ya llegó el producto: …») a
    **Hablando**, y le pone «Te toca» para que alguien le escriba al
    cliente HOY — es el único sentido de haberlo parqueado. El motivo
    guardado se cierra (queda de historia con su epoch) y la tarjeta deja
    de mostrarlo. Nada se le manda al cliente: esto solo mueve el tablero.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    if lead["estado"] != "RECORDATORIO":
        return "", "Ese lead no está en Recordatorio."
    motivo = motivo_recordatorio(lead["ref"])
    nota = (f"Ya llegó el producto: {motivo}" if motivo
            else "Ya llegó el producto.")
    # mover_a_estado cierra el motivo (sale de Recordatorio) y sincroniza
    # el chat; por eso el motivo se leyó ANTES.
    aviso, error = mover_a_estado(ref, "HABLANDO", nota=nota, autor=autor)
    if error:
        return "", error
    try:
        # Idempotente: si «Te toca» ya estaba (el cliente escribió justo
        # antes), no pasa nada — el arreglo del 29/09 en poner_label.
        linear_leads.poner_te_toca(lead["id"], True)
    except linear_leads.ErrorLeads as fallo:
        return aviso, (f"El lead volvió a Hablando, pero «Te toca» no se "
                       f"pudo poner: {fallo}")
    linear_leads.refrescar()
    if waha_activo():
        # Segunda pasada a WhatsApp: la de mover_a_estado salió ANTES de
        # poner «Te toca», así que el 🔴 del chat sale de esta.
        etiquetar_en_whatsapp(lead["ref"])
    return (f"{lead['nombre']}: ya llegó — de vuelta en Hablando, con "
            "🔴 Responder para escribirle hoy."), ""


def escribir_nota(ref, texto, autor=""):
    """Una nota sobre el lead: un comentario en su issue de Linear."""
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    try:
        linear_leads.comentar(lead["id"], texto, autor=autor)
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    return "Nota guardada en el issue.", ""


# ---------------------------------------------------------------------------
# El interruptor «🔴 Responder» y las señales sueltas (25/09/2026, pedido
# de Abraham): el mismo mecanismo genérico —prender o apagar UNA etiqueta
# suelta del issue—, con Responder como primer caso y su propio botón
# destacado. Solo Responder (la etiqueta «Te toca») sale al chat de
# WhatsApp; las tres señales son internas y ninguna manda nada al cliente.
#
# La INTENCIÓN viaja en el formulario (28/09/2026): el botón manda
# `prender=1`/`prender=0` según lo que él mismo pintó, y estas funciones
# FIJAN ese estado — nunca lo niegan a partir de una lectura. Negarlo
# (`not lead["te_toca"]`) era el bug de "hay que apretar dos veces": la
# lectura podía venir de una caché de hasta 60 s, y con la caché vieja el
# primer clic hacía lo CONTRARIO de lo que el botón prometía (ponía una
# etiqueta que ya estaba, o quitaba una que no estaba) sin que la pantalla
# cambiara. Fijar el estado es idempotente: da igual la lectura, apretar
# dos veces seguidas con la misma intención no deshace nada.
# ---------------------------------------------------------------------------

def alternar_responder(ref, prender, autor=""):
    """Prende o apaga «Te toca» a mano: el botón 🔴 Responder de la ficha.

    Prender pone, ADEMÁS, «Responder a mano» (28/09/2026, pedido de
    Abraham: "que no se vaya hasta que responda") — si Abraham ya la creó
    en Linear, con el mismo candado que las señales: si no existe, no se
    pone y no es un error, el botón sigue prendiendo y apagando «Te toca»
    igual. Con ella puesta, el receptor del frontend no apaga «Te toca»
    con nuestra respuesta: lo que un empleado prende a mano solo lo apaga
    el mismo botón, nunca el próximo mensaje saliente.

    El orden importa para no dejar un estado a medias: al prender, primero
    «Te toca» (la señal que todo lo demás — el sincronizador de WhatsApp,
    el aviso al celular — ya entiende) y recién después «Responder a
    mano»; al apagar, al revés, para que «Responder a mano» nunca
    sobreviva sin «Te toca».

    Si WAHA ya anda, se pide la sincronización inmediata al chat en vez de
    esperar los 2 minutos del sincronizador. Nunca manda nada al cliente:
    esto solo cambia etiquetas, no manda WhatsApp saliente.

    NO deja comentario en el issue (decisión de Abraham, 1/10/2026): el
    botón ya lo aprieta él mismo y el estado ya se ve en la tarjeta y en la
    ficha, así que el comentario no le decía nada nuevo y solo le mandaba
    una notificación de Linear más. Medido contra el Linear real: 33 de
    311 comentarios del bot eran justo este eco. Mismo criterio que ya
    tiene `alternar_senal()`, que nunca comentó.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    hay_responder_a_mano = linear_leads.responder_a_mano_disponible()
    try:
        if prender:
            linear_leads.poner_te_toca(lead["id"], True)
            if hay_responder_a_mano:
                linear_leads.poner_etiqueta_suelta(
                    lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, True)
        else:
            if hay_responder_a_mano:
                linear_leads.poner_etiqueta_suelta(
                    lead["id"], linear_leads.LABEL_RESPONDER_A_MANO, False)
            linear_leads.poner_te_toca(lead["id"], False)
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    linear_leads.refrescar()
    if waha_activo():
        etiquetar_en_whatsapp(lead["ref"])
    aviso = (f"{lead['nombre']}: Responder "
            + ("prendido." if prender else "apagado."))
    if prender and not hay_responder_a_mano:
        aviso += (" Ojo: como «Responder a mano» todavía no existe en "
                  "Linear, nuestra respuesta lo va a apagar igual que "
                  "siempre.")
    return aviso, ""


def alternar_senal(ref, nombre, prender, autor=""):
    """Prende o apaga una señal suelta (Seguimiento, Importante, Cliente
    potencial, Llamar) A LO QUE PIDE EL FORMULARIO — mismo criterio que
    `alternar_responder`. Sin comentario en el issue y sin tocar WhatsApp:
    son internas, y `etiquetar_en_whatsapp` nunca se llama para ellas.

    El candado no es solo del navegador: si el botón no debería haber
    aparecido (la etiqueta todavía no existe en Linear) y el POST llegó
    igual, acá no se toca nada.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    if nombre not in linear_leads.senales_disponibles():
        registro_aviso(
            f"Se pidió la señal «{nombre}» para {ref}, pero esa etiqueta no "
            f"existe en Linear: no se tocó nada.")
        return "", ""
    try:
        linear_leads.poner_etiqueta_suelta(lead["id"], nombre, prender)
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    linear_leads.refrescar()
    return (f"{lead['nombre']}: {nombre} "
            + ("puesta." if prender else "quitada."), "")



# ---------------------------------------------------------------------------
# El hilo de la ficha (Fase C, 25/09/2026)
#
# La conversación entera dentro del panel: el cliente a la izquierda, el
# equipo a la derecha CON EL NOMBRE de quien respondió, y los sucesos del
# sistema como un renglón lila en medio del hilo («Cotización S00089
# generada»), para que la plata y la conversación se lean juntas.
#
# Los mensajes seguidos del mismo autor van bajo UNA firma. En una tanda de
# diez mensajes de Mary, repetir «Mary» diez veces no dice nada nuevo y
# empuja la conversación fuera de la pantalla.
#
# Todo se decide aquí: la plantilla recorre bloques ya armados y no toma
# ninguna decisión.
# ---------------------------------------------------------------------------

# La firma que `linear_leads.comentar()` y `calendario.comentar()` le ponen
# a lo que escribe una PERSONA desde Control: «_— <nombre> desde Control
# Viverorose_», siempre al FINAL del comentario. Su ausencia es la señal de
# que ese comentario lo escribió el sistema; no se compara contra el nombre
# del bot a propósito, que es un dato que puede cambiar en Linear sin
# avisar.
#
# Anclada al final (no un «contiene» suelto): un comentario del sistema que
# cite el mensaje de un cliente podría traer «_— » en medio del texto sin
# ser, por eso, una nota de una persona.
_FIRMA_PERSONA_RE = re.compile(r"_— .+ desde Control Viverorose_\s*$")

# Los ecos: comentarios del sistema que REPITEN un mensaje que el hilo ya
# muestra dos líneas más arriba. Son ciertos y en el issue de Linear tienen
# sentido —allá no se ve la conversación—, pero dentro del hilo son ruido:
# en el lead de Diana Caballero había cinco en una sola tarde, cada uno
# debajo del mensaje que repetía. Medido sobre el tablero real del
# 25/09/2026: 96 de los 100 comentarios del sistema son ecos.
#
# Lo que NO es eco se queda: «🧾 S00089 · $55.00 — hecha en inventario», el
# cierre del barrido de 14 días, el origen emparejado por hora. Eso cuenta
# algo que la conversación no dice, y por eso el hilo existe.
_ECOS = (
    # La Fase B, cuando el equipo rompe el silencio.
    re.compile(r"^.{1,40} respondió[:.]"),
    # El receptor del frontend, cada vez que el cliente vuelve a escribir.
    re.compile(r"^Volvió a escribir por WhatsApp"),
)


def _es_eco(texto):
    return any(patron.match(texto) for patron in _ECOS)

# Cómo se ve el que escribe. «Vivero» es el que no sabemos: un saliente que
# WAHA todavía no anotó no tiene autor, y ponerle un nombre sería inventarlo.
SIN_AUTOR = "Vivero"


def _iniciales(nombre):
    """Dos letras para el avatar del hilo.

    Con nombre y apellido, la inicial de cada uno (Jenny Londoño -> JL).
    Con una sola palabra, sus dos primeras letras. Un dispositivo que nadie
    reclamó sale como «?4», para que se note que le falta dueño. Un nombre
    que es puro emoji cae en «?».
    """
    nombre = (nombre or "").strip()
    if nombre.startswith("Equipo"):
        numeros = [c for c in nombre if c.isdigit()]
        return "?" + (numeros[-1] if numeros else "")
    palabras = [p for p in nombre.replace("·", " ").split() if p[:1].isalpha()]
    if len(palabras) >= 2:
        return (palabras[0][0] + palabras[1][0]).upper()
    letras = [c for c in nombre if c.isalpha()]
    return ("".join(letras[:2]) or "?").upper()


def separar_notas(notas):
    """Parte los comentarios del issue en (sucesos del hilo, notas internas).

    Un comentario firmado lo escribió una persona: es una nota interna y va
    APARTE, debajo del hilo, nunca mezclada con lo que vio el cliente. Uno
    sin firma lo escribió el sistema y cuenta algo que la conversación no
    dice —la cotización, el pago—, así que entra al hilo.
    """
    sucesos, internas = [], []
    for nota in notas or []:
        texto = (nota.get("texto") or "").strip()
        if _FIRMA_PERSONA_RE.search(texto):
            internas.append(nota)
            continue
        if _es_eco(texto):
            continue
        sucesos.append(nota)
    return sucesos, internas


def hilo(mensajes, sucesos=(), nombre_cliente=""):
    """Los bloques del chat, en orden y ya agrupados.

    Tres tipos de bloque, y la plantilla no hace más que pintarlos:

    - `dia`: el separador («Jueves 24 sep»).
    - `grupo`: una tanda de mensajes del mismo autor, con su firma y su
      avatar. `mio` dice si va a la derecha.
    - `suceso`: el renglón lila del sistema, en medio del hilo.
    """
    linea = []
    for m in mensajes or []:
        linea.append({
            "orden": m.get("fecha") or "",
            "tipo": "mensaje",
            "dia": m.get("dia") or "",
            "hora": m.get("hora") or "",
            "texto": m.get("texto") or "",
            "salida": bool(m.get("salida")),
            "autor": (m.get("autor") or "").strip(),
        })
    for s in sucesos or []:
        linea.append({
            "orden": s.get("fecha") or "",
            "tipo": "suceso",
            "dia": crm_twenty.dia_legible(s.get("fecha") or ""),
            "hora": crm_twenty.hora_legible(s.get("fecha") or ""),
            "texto": (s.get("texto") or "").strip(),
        })
    linea.sort(key=lambda x: x["orden"])

    bloques, dia_actual, grupo = [], None, None
    for item in linea:
        if item["dia"] and item["dia"] != dia_actual:
            bloques.append({"tipo": "dia", "texto": item["dia"]})
            dia_actual, grupo = item["dia"], None
        if item["tipo"] == "suceso":
            # Un suceso corta la tanda: lo que venga después vuelve a
            # firmarse, aunque sea del mismo autor.
            grupo = None
            bloques.append({"tipo": "suceso", "texto": item["texto"],
                            "hora": item["hora"]})
            continue
        nombre = (item["autor"] or SIN_AUTOR) if item["salida"] else (
            nombre_cliente or "Cliente")
        if grupo is None or grupo["nombre"] != nombre or grupo["mio"] != item["salida"]:
            grupo = {
                "tipo": "grupo",
                "nombre": nombre,
                "iniciales": _iniciales(nombre),
                "mio": item["salida"],
                "desconocido": item["salida"] and (
                    not item["autor"] or item["autor"].startswith("Equipo")),
                "sistema": item["autor"] == "Sistema",
                "mensajes": [],
            }
            bloques.append(grupo)
        grupo["mensajes"].append({"hora": item["hora"], "texto": item["texto"]})
    return bloques


# ---------------------------------------------------------------------------
# Las CINCO filas del panel del lead (BLOQUE 43, lienzo de Roles
# «crm-lead-abierto»): Lo atiende · Seguimiento · Último mensaje ·
# Cotización · Historial. Se toca la fila ENTERA, así que cada una viaja
# con su destino ya decidido aquí — la plantilla solo pinta.
#
# Ninguna fila inventa: cuando el dato no tiene fuente (Twenty o Linear
# neutralizados en el 8095, Odoo caído, una etiqueta que Abraham todavía
# no creó), la fila lo DICE en su lugar y, si lo que falta es la acción,
# va apagada con su «Todavía no».
# ---------------------------------------------------------------------------

# El recorte del texto de un mensaje en su fila: una línea, no un párrafo.
LARGO_ULTIMO_MENSAJE = 72

LEYENDA_SIN_PLATA = ("El monto lo ven quien lo atiende, el Director y "
                     "Finanzas.")


def _dinero(monto):
    """El mismo formato del filtro `dinero` de las plantillas, con la coma
    de los miles: una fila que dice «Total $1,150.00» se lee de un golpe.
    Vive acá porque estas filas se componen en Python (regla 10) y el
    filtro de Jinja no se puede llamar desde el servidor sin arrastrar
    main.py — que importa este módulo."""
    return f"${monto:,.2f}"


def _recortado(texto, largo=LARGO_ULTIMO_MENSAJE):
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= largo else texto[:largo - 1].rstrip() + "…"


def _fila(clave, etiqueta, valor, detalle="", href="", inicial="",
          todavia_no="", alerta=False):
    return {"clave": clave, "etiqueta": etiqueta, "valor": valor,
            "detalle": detalle, "href": href, "inicial": inicial,
            "todavia_no": todavia_no, "alerta": alerta}


def _fila_atiende(abierta, vista):
    """Quién lo atiende: la etiqueta `Resp:` del issue, el único lugar
    donde vive el responsable (nunca el `assignee`). La fila entera abre
    el cuadro de asignar/reasignar."""
    resp = abierta.get("resp") or ""
    destino = ("/control?vista=" + quote(vista or "estado")
               + "&abrir=" + quote(abierta["ref"]) + "&asignar=1")
    return _fila("atiende", "Lo atiende", resp or SIN_ASIGNAR,
                 detalle="" if resp else "Nadie lo tiene todavía",
                 href=destino, inicial=resp[:1].upper() if resp else "",
                 alerta=not resp)


def _fila_seguimiento(abierta):
    """El seguimiento de hoy es la SEÑAL de Linear, que está prendida o
    apagada y nada más: la fecha y la nota del lienzo no existen todavía
    como dato en ningún lado, así que la fila lo dice y la acción va
    apagada en vez de fingir un vencimiento."""
    disponibles = {s["nombre"] for s in abierta.get("senales") or []}
    if linear_leads.LABEL_SEGUIMIENTO not in disponibles:
        return _fila("seguimiento", "Seguimiento",
                     "La etiqueta «Seguimiento» no existe en Linear",
                     detalle="La crea Abraham allá; el código nunca la crea.",
                     todavia_no="Todavía no")
    prendida = any(s["nombre"] == linear_leads.LABEL_SEGUIMIENTO and s["prendida"]
                   for s in abierta["senales"])
    return _fila("seguimiento", "Seguimiento",
                 "Marcado para seguir" if prendida else "Sin seguimiento",
                 detalle="La fecha y la nota todavía no existen como dato.",
                 todavia_no="Todavía no")


def _fila_ultimo_mensaje(abierta, mensajes, nombre_cliente):
    """Lo último que se dijo en el chat, tal cual lo tiene Twenty. Twenty
    caído no es «sin mensajes»: son cosas distintas y la fila las
    distingue (Nº12 del lote, 2/10/2026)."""
    if abierta.get("hilo_error"):
        return _fila("mensaje", "Último mensaje", abierta["hilo_error"],
                     href="#dc-conversacion")
    ultimo = max(mensajes, key=lambda m: m.get("fecha") or "") if mensajes else None
    if ultimo is None:
        return _fila("mensaje", "Último mensaje", "Sin mensajes todavía",
                     href="#dc-conversacion")
    if ultimo.get("salida"):
        quien = (ultimo.get("autor") or "").strip() or SIN_AUTOR
    else:
        quien = nombre_cliente or "Cliente"
    cuando = (ultimo.get("cuando") or "").strip()
    return _fila("mensaje", "Último mensaje",
                 "«" + _recortado(ultimo.get("texto") or "") + "»",
                 detalle=quien + (" · " + cuando if cuando else ""),
                 href="#dc-conversacion")


def _fila_cotizacion(cot, ve_plata):
    """La cotización conectada: el número de la orden REAL y su plata.
    Sin orden real no hay monto; con Odoo caído se dice, en vez de pasar
    por «sin cotización»."""
    if not cot.get("ok"):
        return _fila("cotizacion", "Cotización", "No se pudo leer Odoo",
                     detalle=cot.get("error") or "", href="#dc-cotizacion")
    plata = cot.get("plata")
    if plata:
        if ve_plata:
            detalle = (f"Total {_dinero(plata['total'])} · saldo "
                       f"{_dinero(plata['saldo'])}")
        else:
            detalle = LEYENDA_SIN_PLATA
        return _fila("cotizacion", "Cotización", plata["orden"],
                     detalle=detalle, href="#dc-cotizacion")
    conectadas = cot.get("ordenes") or []
    if conectadas:
        return _fila("cotizacion", "Cotización",
                     f"{len(conectadas)} conectada"
                     + ("s" if len(conectadas) != 1 else ""),
                     detalle="Ninguna marcada como la real.",
                     href="#dc-cotizacion", alerta=True)
    return _fila("cotizacion", "Cotización", "Sin cotización conectada",
                 href="#dc-cotizacion")


def _fila_historial(abierta, cuantos, ultimo):
    """El historial del lead son los comentarios de su issue: cada
    corrección manual, cada cotización conectada y cada nota firmada deja
    uno. La fila lleva a Linear, que es donde viven de verdad; sin URL
    (modo muestra) cae en las notas internas de esta misma ficha."""
    if not cuantos:
        return _fila("historial", "Historial", "Sin movimientos anotados",
                     href=abierta.get("url") or "#dc-notas")
    return _fila("historial", "Historial",
                 f"{cuantos} movimiento" + ("s" if cuantos != 1 else ""),
                 detalle=("Último: " + ultimo) if ultimo else "",
                 href=abierta.get("url") or "#dc-notas")


def _monto_del_panel(cot, ve_plata):
    """El número grande de arriba del panel y su renglón de abajo.

    `{"total": …, "nota": …}` con el total de la orden REAL, o
    `{"total": None, "nota": …}` cuando no hay plata que mostrar — y la
    nota dice POR QUÉ: sin cotización conectada, Odoo caído, o plata que
    esta sesión no ve. Nunca un cero.
    """
    if not ve_plata:
        return {"total": None, "texto": "", "nota": LEYENDA_SIN_PLATA}
    if not cot.get("ok"):
        return {"total": None, "texto": "",
                "nota": "No se pudo leer Odoo: " + (cot.get("error") or "")}
    plata = cot.get("plata")
    if not plata:
        return {"total": None, "texto": "",
                "nota": "Sin cotización conectada: todavía no hay monto."}
    if plata["saldo"] <= 0:
        cobro = "pagado completo"
    elif plata["pagado"]:
        cobro = "debe " + _dinero(plata["saldo"])
    else:
        cobro = "sin pago todavía"
    # `texto` ya viene formateado (con la coma de los miles, como el
    # lienzo): el filtro `dinero` de las plantillas no la pone, y el
    # número grande del panel es de cuatro cifras a menudo.
    return {"total": plata["total"], "texto": _dinero(plata["total"]),
            "nota": plata["orden"] + " · " + cobro}


def ficha(ref, buscar_cotizacion="", vista="", ve_plata=True):
    """El lead con su conversación, sus notas y su cotización conectada,
    para el panel de la derecha.

    El chat es un extra: si Twenty no contesta, el panel se pinta igual y
    dice que no hay chat, en vez de quedarse en blanco. Lo mismo con Odoo
    (`_cotizacion_de`): "no se pudo leer Odoo" nunca se confunde con "sin
    cotización conectada".

    `vista` solo arma los enlaces de vuelta; `ve_plata` lo decide
    `puede_ver_plata()` en la ruta — con él en False el panel pinta todo
    menos los números de dinero, y dice quién los ve.
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return None
    abierta = _tarjeta(lead)
    # Las señales sueltas del panel: solo las que Abraham ya creó en
    # Linear, en el orden fijo de `LABELS_SENAL`, cada una con su estado
    # actual en ESTE lead.
    disponibles = linear_leads.senales_disponibles()
    abierta["senales"] = [
        {"nombre": n, "prendida": n in lead["etiquetas"]}
        for n in linear_leads.LABELS_SENAL if n in disponibles]
    sucesos, internas = separar_notas(linear_leads.comentarios(lead["id"]))
    abierta["notas"] = internas
    abierta["cot"] = _cotizacion_de(lead, buscar_cotizacion)
    # El botón «Parar mantenimiento» (28/09/2026) solo aparece si el lead
    # tiene una serie activa — mantenimiento.py es quien lo sabe.
    abierta["mantenimiento_activo"] = mantenimiento.activo(lead["ref"])
    # Qué espera el cliente (29/09/2026): solo tiene sentido mientras el
    # lead está parqueado en Recordatorio.
    abierta["recordatorio_motivo"] = (
        motivo_recordatorio(lead["ref"])
        if lead["estado"] == "RECORDATORIO" else "")

    ficha_twenty = crm_twenty.ficha_de_lead(lead) or {}
    # Twenty caído no es «sin chat»: la ficha lo dice (Nº12, 2/10/2026).
    abierta["hilo_error"] = ficha_twenty.get("fallo") or ""
    mensajes = ficha_twenty.get("mensajes") or []
    abierta["hilo"] = hilo(mensajes, sucesos, lead.get("nombre") or "")
    abierta["twenty_url"] = ficha_twenty.get("twenty_url") or ""
    # El teléfono de Twenty completa al del issue cuando allá no quedó.
    if not abierta.get("celular") and ficha_twenty.get("telefono"):
        abierta["celular"] = ficha_twenty["telefono"]

    # El monto grande de arriba y las cinco filas (lienzo de Roles). Todo
    # sale de lo que ya se leyó acá: ni una consulta más.
    abierta["ve_plata"] = bool(ve_plata)
    # `monto_panel` y el `monto` de la tarjeta son cosas distintas a
    # propósito: el de la tarjeta es la plata entera de la orden real (o
    # None), y este es lo que se PINTA arriba del panel, con su renglón
    # que explica por qué cuando no hay número.
    abierta["monto_panel"] = _monto_del_panel(abierta["cot"], ve_plata)
    historial = (sucesos or []) + (internas or [])
    crudo = max((n.get("fecha") or n.get("cuando") or "" for n in historial),
                default="")
    # «Martes 6 oct» en vez de «2026-10-06»: el mismo día legible que ya
    # usa el hilo. Si no se pudo leer la fecha queda lo crudo, nunca una
    # fecha inventada.
    ultimo_apunte = crm_twenty.dia_legible(crudo) or crudo
    abierta["filas"] = [
        _fila_atiende(abierta, vista),
        _fila_seguimiento(abierta),
        _fila_ultimo_mensaje(abierta, mensajes, lead.get("nombre") or ""),
        _fila_cotizacion(abierta["cot"], ve_plata),
        _fila_historial(abierta, len(historial), ultimo_apunte),
    ]
    return abierta


# ---------------------------------------------------------------------------
# El cuadro de asignar / reasignar (BLOQUE 43, lienzo de Roles
# «crm-asignar»). Es una PANTALLA, no una escritura: el botón «Mandar
# petición» va apagado porque las peticiones —y con ellas el «no queda
# asignado hasta que la persona acepte»— llegan en el punto 2 del plan de
# roles. Lo que sí reparte HOY sigue donde estaba: «Se lo doy a» dentro de
# «Más opciones», que cambia la etiqueta `Resp:` de una.
#
# Nadie viene premarcado, a propósito (pedido del lienzo): una opción
# preseleccionada es una decisión que el cuadro tomó por quien reparte.
# ---------------------------------------------------------------------------

def cuadro_asignar(abierta, leads=None):
    """Lo que el cuadro necesita, ya decidido: a quién se le puede mandar,
    con cuánto trabajo abierto lleva encima cada quien.

    El conteo de «abiertos» es REAL: los leads vivos que hoy tienen esa
    etiqueta `Resp:` en Linear. Lo que el lienzo llama «2 sin aceptar» no
    se pinta: las peticiones no existen todavía y un número inventado ahí
    sería el peor de todos — el que decide a quién cargarle un trabajo.
    """
    leads = _vivos(leads if leads is not None else linear_leads.listar())
    abiertos = {}
    for lead in leads:
        nombre = lead.get("resp") or ""
        if nombre:
            abiertos[nombre] = abiertos.get(nombre, 0) + 1
    actual = (abierta or {}).get("resp") or ""
    gente = []
    for nombre in linear_leads.responsables():
        cuantos = abiertos.get(nombre, 0)
        gente.append({
            "nombre": nombre,
            "inicial": nombre[:1].upper(),
            "actual": nombre == actual,
            "abiertos": cuantos,
            "detalle": (f"{cuantos} lead" + ("s" if cuantos != 1 else "")
                        + " abierto" + ("s" if cuantos != 1 else "")),
        })
    return {
        "lead": abierta,
        "gente": gente,
        "reasigna": bool(actual),
        # El rótulo del botón apagado dice qué sería: mandar la petición
        # por primera vez, o reasignar lo que ya tiene dueño.
        "accion": "Reasignar" if actual else "Mandar petición",
    }


# ---------------------------------------------------------------------------
# La cotización conectada (28/09/2026, pedido de Abraham): qué órdenes de
# Odoo están amarradas a este lead, cuál es la real, su plata, y las
# candidatas para conectar una más. Todo el dato sale de `cot_lead.py`
# (capa aparte, ya probada contra Odoo); acá solo se compone para la
# pantalla y se cablea el avance del embudo.
# ---------------------------------------------------------------------------

# El badge del estado de cobro de cada orden conectada (28/09/2026, pedido
# de Abraham: "que se vea como en Ventas"): los mismos cuatro colores que
# usa venta.html para sus estados (b-ok/b-bajo/b-critico/b-neutro), sobre
# el `etapa_cobro` que ya trae `cot_lead._orden_legible`. "Abono" en rojo
# a propósito: es plata que todavía falta cobrar, el mismo criterio que
# usa venta.html con sus estados intermedios (confirmada/entregada/
# facturada, todas b-critico mientras algo siga pendiente).
_BADGES_ETAPA_COBRO = {
    "cotizado": {"texto": "Cotización", "clase": "b-bajo"},
    "abono": {"texto": "Abono 50%", "clase": "b-critico"},
    "pagado": {"texto": "Pagado", "clase": "b-ok"},
}
_BADGE_SIN_COBRO = {"texto": "Cancelada", "clase": "b-neutro"}


def _cotizacion_de(lead, buscar_cotizacion=""):
    """{"ok", "error", "ordenes", "plata", "candidatas", "buscar"} — todo
    lo que el panel necesita sobre Odoo, en una sola composición.

    Fail-soft con criterio: si CUALQUIERA de las tres consultas falló,
    "ok" es False y "error" trae el primer motivo — la pantalla tiene que
    poder decir "no se pudo leer Odoo" en vez de "sin cotización
    conectada", que sería mentir.
    """
    ordenes = cot_lead.ordenes_del_lead(lead)
    plata = cot_lead.plata_de_la_real(lead)
    candidatas = cot_lead.candidatas_del_lead(lead, buscar_cotizacion)
    fallos = [r["error"] for r in (ordenes, plata, candidatas) if not r["ok"]]
    return {
        "ok": not fallos,
        "error": fallos[0] if fallos else "",
        "ordenes": _con_edicion(ordenes.get("ordenes") or [], lead.get("nombre") or ""),
        "plata": plata if plata.get("hay_real") else None,
        "candidatas": candidatas.get("candidatas") or [],
        "buscar": buscar_cotizacion,
    }


def _con_edicion(ordenes, cliente=""):
    """Cada orden conectada, con su badge de cobro, el nombre del PDF
    (mismo cálculo que arma `/control/cotizacion/{id}.pdf`, para que el
    botón «Compartir» comparta con el nombre EXACTO que va a bajar —
    28/09/2026) y, si nació como cotización de SERVICIO en Vender y
    todavía es editable ahí (ni facturada ni cancelada), el `n` de su
    registro local para el enlace «Editar»
    (`/venta/servicio/{n}/editar`) — nunca se inventa edición para una
    orden que no nació en Vender.

    Cruza por `orden_id` contra `cotizaciones.cotizaciones_todas()` (el
    registro local, solo lectura — esta app nunca escribe ahí desde
    acá) y, solo para las que matchean, UNA consulta a Odoo para saber si
    siguen editables (`cotizaciones.estados_en_odoo`, la misma que ya usa
    la pestaña Vender) — no una por orden.
    """
    if not ordenes:
        return []
    locales = {fila["orden_id"]: fila["n"]
              for fila in cotizaciones.cotizaciones_todas() if fila.get("orden_id")}
    candidatos = [o["orden_id"] for o in ordenes if o["orden_id"] in locales]
    editables = set()
    if candidatos:
        try:
            estados = cotizaciones.estados_en_odoo(candidatos)
            editables = {oid for oid, e in estados.items() if e.get("editable")}
        except Exception:
            pass  # sin Odoo, ningún enlace de Editar: mejor sin botón que uno roto
    resultado = []
    for o in ordenes:
        badge = _BADGES_ETAPA_COBRO.get(o.get("etapa_cobro"), _BADGE_SIN_COBRO)
        n_local = locales.get(o["orden_id"])
        editar_n = n_local if (n_local is not None and o["orden_id"] in editables) else None
        nombre_pdf = ventas.nombre_de_pdf((o.get("nombre") or str(o["orden_id"])).replace("/", "-"),
                                          cliente)
        resultado.append({**o, "badge": badge, "editar_n": editar_n, "nombre_pdf": nombre_pdf})
    return resultado


def _nombre_de_orden(lead, orden_id):
    """El "S000xx" de una orden ya conectada, para nombrarla en el aviso y
    en el comentario del issue. "#<id>" si por lo que sea no aparece (Odoo
    no contestó al releer, por ejemplo) — nunca se inventa un nombre."""
    resultado = cot_lead.ordenes_del_lead(lead)
    for orden in resultado.get("ordenes") or []:
        if orden["orden_id"] == orden_id:
            return orden["nombre"] or f"#{orden_id}"
    return f"#{orden_id}"


def _avanzar_segun_sugerido(lead):
    """Aplica al embudo lo que sugiera la orden real, vía
    `cot_lead.estado_sugerido()`: sin pago -> Cotizado; con pago -> Por
    agendar + su etiqueta. NUNCA hacia atrás — `mover_estado(manual=False)`
    ya solo avanza, así que un lead más adelantado (Agendado, Entregado) se
    queda donde está. Devuelve ("aviso", "error")."""
    sugerido = cot_lead.estado_sugerido(lead)
    if not sugerido["ok"]:
        return "", f"No se pudo leer Odoo: {sugerido['error']}"
    if not sugerido["estado"]:
        return "", ""
    try:
        avanzo = linear_leads.mover_estado(lead["id"], sugerido["estado"])
        if sugerido["etiqueta_pago"]:
            linear_leads.poner_pago(lead["id"], sugerido["etiqueta_pago"])
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    if avanzo:
        nombre_estado = linear_leads.POR_CLAVE[sugerido["estado"]]["nombre"]
        return f"Pasó a {nombre_estado}.", ""
    return "", ""


def _orden_id(crudo):
    """El id de orden del formulario, o None si no es un número — nunca se
    manda algo raro a Odoo."""
    try:
        return int(str(crudo).strip())
    except (TypeError, ValueError):
        return None


def conectar_cotizacion(ref, orden_id, autor=""):
    """Conecta una orden de Odoo al lead: la deja en su lista, comenta en
    el issue quién la conectó («Venta S000xx conectada.», firmado, como
    `alternar_responder`) y avanza el estado según lo que sugiera esa
    orden — nunca hacia atrás. Devuelve ("aviso", "error").

    `estado_sugerido()` mira SOLO la orden real (`cot_lead._orden_real`,
    interno): conectar una orden no la hace real por sí sola. Si el lead
    todavía no tenía ninguna real, la que se acaba de conectar es la única
    candidata a serlo — se vuelve la real acá mismo, para que "el estado
    avanza al conectar" sea cierto en el caso normal (un lead, una
    cotización). Si YA había una real, esto no se la quita: conectar una
    segunda orden (el caso de Tamara, dos trabajos) es solo sumarla a la
    lista — cuál manda para el embudo es una decisión aparte, "marcar como
    la real".
    """
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    orden_id = _orden_id(orden_id)
    if orden_id is None:
        return "", "Esa orden no es válida."
    try:
        cot_lead.conectar(orden_id, lead)
    except ValueError as fallo:
        return "", str(fallo)
    except Exception as fallo:
        return "", ventas._mensaje_de_error(fallo)

    resultado = cot_lead.ordenes_del_lead(lead)
    ordenes = resultado.get("ordenes") or []
    nombre_orden = next(
        (o["nombre"] for o in ordenes if o["orden_id"] == orden_id),
        f"#{orden_id}")
    if resultado["ok"] and not any(
            o["es_real"] for o in ordenes if o["orden_id"] != orden_id):
        try:
            cot_lead.marcar_real(orden_id, lead)
        except Exception:
            pass  # no bloquea la conexión: el aviso de conectar ya salió

    try:
        linear_leads.comentar(
            lead["id"], f"Venta {nombre_orden} conectada.", autor=autor)
    except linear_leads.ErrorLeads as fallo:
        return "", str(fallo)
    aviso_avance, error = _avanzar_segun_sugerido(lead)
    aviso = f"{nombre_orden} conectada a {lead['nombre']}."
    if aviso_avance:
        aviso += " " + aviso_avance
    return aviso, error


def desconectar_cotizacion(ref, orden_id, autor=""):
    """Quita una orden de la lista del lead. No toca el estado del embudo
    — desconectar corrige un error de conexión, no es una noticia del
    negocio que avance o retroceda nada."""
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    orden_id = _orden_id(orden_id)
    if orden_id is None:
        return "", "Esa orden no es válida."
    try:
        cot_lead.desconectar(orden_id)
    except Exception as fallo:
        return "", ventas._mensaje_de_error(fallo)
    return "Cotización desconectada.", ""


def marcar_real_cotizacion(ref, orden_id, autor=""):
    """Marca esta orden como LA real del lead (se la quita a cualquier
    otra, la invariante la garantiza `cot_lead.marcar_real`) y, con la
    plata que trae, avanza el embudo — nunca hacia atrás, igual que al
    conectar."""
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    orden_id = _orden_id(orden_id)
    if orden_id is None:
        return "", "Esa orden no es válida."
    try:
        cot_lead.marcar_real(orden_id, lead)
    except ValueError as fallo:
        return "", str(fallo)
    except Exception as fallo:
        return "", ventas._mensaje_de_error(fallo)
    aviso_avance, error = _avanzar_segun_sugerido(lead)
    return (aviso_avance or "Marcada como la real.", error)


def quitar_real_cotizacion(ref, orden_id, autor=""):
    """Le quita a esta orden la marca de «la real» — el lead se queda SIN
    ninguna (no pasa a otra sola; para eso está «Marcar la real» en la que
    corresponda). No toca el estado del embudo: quitar una marca no es una
    noticia del negocio que avance o retroceda nada, igual que
    desconectar. Deja un comentario firmado en el issue."""
    lead = linear_leads.uno(ref)
    if lead is None:
        return "", linear_leads.mensaje_lead_ausente(ref)
    orden_id = _orden_id(orden_id)
    if orden_id is None:
        return "", "Esa orden no es válida."
    nombre_orden = _nombre_de_orden(lead, orden_id)
    try:
        cot_lead.quitar_real(orden_id)
    except Exception as fallo:
        return "", ventas._mensaje_de_error(fallo)
    try:
        linear_leads.comentar(
            lead["id"], f"{nombre_orden} dejó de ser la real.", autor=autor)
    except linear_leads.ErrorLeads as fallo:
        return "Ya no es la real.", str(fallo)
    return f"{nombre_orden} ya no es la real.", ""


# ---------------------------------------------------------------------------
# El mantenimiento mensual (28/09/2026): el botón «Parar mantenimiento» de
# la ficha. Todo lo demás (arrancar la serie, crear la cita siguiente)
# corre solo desde agenda.py al ganar y al marcar Hecha — acá solo el
# apagado a mano, que es lo único que le toca a esta pantalla.
# ---------------------------------------------------------------------------

def parar_mantenimiento(ref, autor=""):
    """Apaga la serie de mantenimiento mensual del lead: ver
    `mantenimiento.parar`. Un envoltorio chico, para que la ruta de
    main.py llame a `control.*` igual que las demás acciones de la ficha."""
    return mantenimiento.parar(ref, autor=autor)


# ---------------------------------------------------------------------------
# El aviso al celular: hay un cliente esperando respuesta
# ---------------------------------------------------------------------------

# La marca de "esta instalación ya corrió una vez". Sin ella, la PRIMERA
# pintada de la pantalla después de un deploy avisaría de cada lead que
# tenga «Te toca» acumulado, y al encargado le sonaría el celular diez
# veces de golpe por conversaciones viejas. Es un centinela y no "¿hay
# filas?" a propósito: cuando todos contestan, la tabla se vacía, y eso no
# puede volver a parecer una primera corrida.
ACUSE_ESTRENO = "te-toca:__estreno__"


def avisar_a_quien_le_toca(leads=None):
    """Web Push del SALTO a «Te toca», no de estar ahí.

    La etiqueta «Te toca» la pone el mensaje del cliente y la quita nuestra
    respuesta; eso ya lo hace el frontend. Aquí solo suena el celular del
    encargado, una sola vez por lead — de eso es la tabla de acuse. Cuando
    la etiqueta se apaga, el acuse se olvida, así que la próxima vez que el
    cliente escriba vuelve a sonar.

    La primera corrida después de un deploy solo TOMA NOTA de quién está
    esperando: avisa de lo que pase de ahí en adelante, no del acumulado.

    Devuelve los leads por los que sonó.
    """
    if not avisos.configurado():
        return []
    leads = leads if leads is not None else linear_leads.listar()
    estreno = not _ya_avisado(ACUSE_ESTRENO)
    sonaron = []
    for lead in leads:
        clave = f"te-toca:{lead['ref']}"
        if not lead.get("te_toca"):
            _olvidar_acuse(clave)
            continue
        if _ya_avisado(clave):
            continue
        sonaron.append(lead)
    if estreno:
        return []  # ya quedó anotado quién espera; el aviso empieza mañana
    for lead in sonaron:
        quien = lead.get("resp") or "nadie todavía"
        avisos.avisar(
            avisos.USUARIO_CHATS,
            f"Te toca · {lead['nombre']}",
            f"{lead['estado_nombre']} · {quien}.",
            "/control?abrir=" + lead["ref"])
    return sonaron


def avisar_en_fondo(leads):
    """El aviso, sin que la pantalla lo espere."""
    calendario._en_fondo("control-avisos",
                         lambda: avisar_a_quien_le_toca(leads))


def refrescar():
    linear_leads.refrescar()


def celular_legible(telefono):
    """El teléfono crudo -> '6203-7333' (o tal cual si no es panameño)."""
    digitos = re.sub(r"\D", "", telefono or "")
    if digitos.startswith("507"):
        digitos = digitos[3:]
    if len(digitos) == 8:
        return f"{digitos[:4]}-{digitos[4:]}"
    return digitos
