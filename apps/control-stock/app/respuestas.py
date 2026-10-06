"""Respuestas a clientes — la vista «Respuestas» de Conversaciones
(BLOQUE 21, esqueleto).

ESQUELETO NAVEGABLE (BLOQUE 25, 6/10/2026): la pantalla abre y dice QUÉ
va a mostrar, con sus huecos declarados. El CÁLCULO real (mediana,
reparto, por responsable, «Para leer») llega en la parte (d) del plan —
hoy acá NO hay un solo número fabricado: cada métrica sale «—» con su
descripción, y eso es a propósito.

Reglas del BLOQUE 21 que este módulo ya deja escritas (para que la
parte (d) las implemente sin reinterpretar):

- El tiempo se le anota al RESPONSABLE del lead, no a quien escribió.
- Se cuenta desde que el cliente escribe hasta la primera respuesta de
  cualquiera del equipo; si se sabe quién contestó, se guarda aparte.
- Chats sin responsable: grupo «Sin dueño».
- Respuesta típica = MEDIANA, nunca promedio.
- El umbral es el MISMO dato de Ajustes que usa «Por responder»
  (BLOQUE 22.3/22.4: cuenta siempre, también fuera de horario; se
  calcula en el servidor con las horas de Twenty, jamás con el reloj
  del navegador). Mientras Ajustes no tenga el campo, vive en la tabla
  config (clave respuestas.umbral_min, default 10) — editable sin
  desplegar, igual que pedidos.ventana_dias.
- La app mide el tiempo; el trato lo lee una persona en el chat: la
  app NO califica.

Los TRES huecos del dato `autor` (respuesta del Arquitecto, 6/10) se
DICEN en pantalla, nunca se tapan. SOLO LECTURA: ni un POST.
"""

from . import crm_twenty, datos

CLAVE_UMBRAL = "respuestas.umbral_min"
UMBRAL_DEFAULT = 10

AVISO_SIN_TWENTY = ("Twenty no está conectado en esta instancia: los "
                    "mensajes no se pueden leer, así que no hay nada que "
                    "medir. En producción esta vista leerá los mensajes "
                    "que ya viven en Twenty.")
AVISO_CALCULO = ("El cálculo llega en la siguiente parte del plan: esta "
                 "pantalla ya muestra su estructura para que se vea qué "
                 "va a medir. Ningún número de aquí se inventa.")

# Los 3 huecos conocidos del campo `autor` — declarados, nunca tapados.
HUECOS_DECLARADOS = (
    "Los mensajes anteriores a «quién respondió» no tienen autor: ese "
    "tramo saldrá como «sin autor», nunca con un nombre inventado. (El "
    "TIEMPO de respuesta no necesita el autor: sale de las fechas.)",
    "El teléfono principal sale como «Teléfono»: lo usa más de una "
    "persona y no se le pone nombre a propósito.",
    "Rubén no está mapeado todavía: sus respuestas saldrán como «Equipo "
    "· dispositivo N» hasta que WAHA vea un mensaje de su computadora.",
)


def umbral_min():
    """El umbral en minutos, desde config (clave respuestas.umbral_min).
    Un valor roto o ≤0 cae al default — la pantalla nunca truena por una
    clave mal escrita (mismo patrón que pedidos.ventana_dias)."""
    crudo = datos.config_valores("respuestas.").get("umbral_min")
    try:
        minutos = int(str(crudo).strip())
    except (TypeError, ValueError):
        return UMBRAL_DEFAULT
    return minutos if minutos > 0 else UMBRAL_DEFAULT


def vista():
    """La estructura de la pantalla, ya decidida (regla 10). Cada métrica
    va con valor None (se pinta «—») y la descripción de qué será."""
    umbral = umbral_min()
    conectado = crm_twenty.twenty_configurado()
    return {
        "twenty_conectado": conectado,
        "aviso_twenty": "" if conectado else AVISO_SIN_TWENTY,
        "aviso_calculo": AVISO_CALCULO,
        "umbral_min": umbral,
        "tarjetas": [
            {"titulo": "Respuesta típica", "valor": None,
             "hint": "La mediana: desde que el cliente escribe hasta que "
                     "alguien contesta. La del medio, no el promedio — "
                     "así un chat olvidado no daña el número."},
            {"titulo": f"Contestados en {umbral} min", "valor": None,
             "hint": "Cuántos chats se contestaron dentro del umbral."},
            {"titulo": "Esperando ahora", "valor": None,
             "hint": "Clientes sin respuesta en este momento."},
            {"titulo": "Nadie contestó", "valor": None,
             "hint": "Chats del período que se quedaron sin respuesta."},
        ],
        "por_persona": {
            "columnas": ("Persona", "Chats", "Respuesta típica",
                         f"En {umbral} min", "La más lenta", "Esperando"),
            "filas": [],
            "nota": "El tiempo se anota al RESPONSABLE del lead, no a "
                    "quien escribió; los chats sin responsable van al "
                    "grupo «Sin dueño».",
        },
        "franjas": [
            {"texto": f"Menos de {umbral} min"},
            {"texto": f"De {umbral} min a 1 hora"},
            {"texto": "Más de 1 hora"},
        ],
        "para_leer": {
            "filas": [],
            "nota": "Saldrán los más lentos y algunos al azar, con «Ver "
                    "chat». El sistema mide el tiempo; cómo respondieron "
                    "lo lees tú — la app no pone nota.",
        },
        "huecos_declarados": list(HUECOS_DECLARADOS),
    }
