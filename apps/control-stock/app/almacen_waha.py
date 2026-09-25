"""Cuánto pesa el almacén de WAHA, preguntado al OTRO droplet.

WAHA vive en el droplet del CRM (con Twenty y OpenWA); esta app vive en el
droplet de apps. Un `du` local no sirve: son dos máquinas, y control-stock no
ve el disco de la otra. El puente que YA existe entre las dos es el endpoint
de sincronización inmediata de etiquetas —`SINCRO_URL` + `SINCRO_SECRET`, por
la red privada `10.116.0.3:3002`—, así que aquí solo se le hace una pregunta
más, `GET /almacen`, con el MISMO secreto. Nada de abrir un puerto, un túnel
ni un servicio nuevos: ninguna integración de este proyecto abre puertos.

**El número que importa no es el instantáneo.** El almacén sube durante la
hora (WhatsApp va dejando mensajes) y el limpiador lo pliega de vuelta cada
hora a los :17. El patrón sano es `almacen 5 MB -> 2 MB`. Mirar el disco a
las 7 p.m. —43 minutos después del último plegado— daría un número alto que
NO es una mala noticia: son falsas alarmas garantizadas. Por eso el renglón
muestra el **«después»** del limpiador, que es el piso real, y nombra el
«antes» al lado, porque el salto es la señal de verdad: un `5 -> 2` está
sano; un `71 -> 68` dice que ya hay historial que el limpiador no puede
plegar (fue lo que pasó el 25/09, con 20 584 mensajes bajados).

El instantáneo existe para un solo caso, y es el que de otro modo pasaría
desapercibido: si el limpiador deja de correr, su última línea se queda
congelada en un tranquilizador «2 MB» mientras el disco crece sin techo.
Cuando el diario está vencido este módulo NO repite ese número viejo como si
fuera de hoy: dice que el limpiador no corre y muestra el disco de ahora.

Y si el droplet del CRM no contesta, esto LANZA. El resumen deja entonces el
renglón en blanco y lo dice — nunca en 0 MB, que parecería una buena noticia.
"""

import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import calendario, control

# El mismo tope con el que `~/waha/limpiar_almacen.sh` grita en su diario
# («OJO: el almacen sigue en X MB»). Lo sano ronda 2 MB; 20 ya es historial.
TOPE_MB = 20

# El limpiador corre una vez por hora. Tres horas sin una línea nueva no es
# un retraso: es que su cron no está corriendo.
VENCE_HORAS = 3


def _endpoint():
    """La URL de `GET /almacen`, derivada de `SINCRO_URL`.

    A propósito NO es una variable nueva del `.env`: el endpoint es el mismo
    servidor, en el mismo puerto, con el mismo secreto. Pedirle a Abraham una
    segunda variable para la misma puerta es cómo se termina con dos nombres
    para una sola cosa —y eso ya apagó el enganche de WhatsApp una vez—. Solo
    se toma el `http://host:puerto` y se le pone la ruta propia.
    """
    base = control._sincro_url()
    if not base:
        return ""
    partes = urlsplit(base)
    if not (partes.scheme and partes.netloc):
        return ""
    return urlunsplit((partes.scheme, partes.netloc, "/almacen", "", ""))


def configurado():
    """¿Se puede preguntar? Hacen falta las dos, como para etiquetar."""
    return bool(_endpoint() and control._sincro_secreto())


def leer(timeout=6.0):
    """El crudo del endpoint del droplet del CRM. Lanza si no se puede saber."""
    respuesta = httpx.get(
        _endpoint(),
        headers={"Authorization": "Bearer " + control._sincro_secreto()},
        timeout=timeout)
    if respuesta.status_code == 404:
        # El endpoint del droplet del CRM contesta, pero es una versión sin
        # esta ruta. Se dice con nombre y apellido: `waha/` no tiene despliegue
        # automático y se copia a mano, así que este es el error más probable
        # la primera vez, y el crudo de httpx no lo explicaría.
        raise RuntimeError("el droplet del CRM todavía no tiene la ruta "
                          "/almacen (falta copiarle waha/endpoint.py)")
    respuesta.raise_for_status()
    datos = respuesta.json()
    if not datos.get("ok"):
        raise RuntimeError(str(datos.get("motivo") or "contestó que no")[:120])
    return datos


def bloque(timeout=6.0, hoy_iso=""):
    """El renglón listo para la pantalla. Lanza si el droplet no contesta."""
    return armar(leer(timeout), hoy_iso)


def armar(crudo, hoy_iso=""):
    """Decide el renglón a partir del crudo. Sin red: aquí está toda la
    regla, y por eso se puede probar sin levantar nada.

    Vuelve {mb, antes_mb, mensajes, cuando_texto, edad_horas, vencido,
    alerta, frase, corto}. `corto` es lo que el titular del aviso nombra —
    vacío cuando está sano, porque un titular no gasta espacio en lo que
    está bien.
    """
    mb = crudo.get("mb")
    antes = crudo.get("antes_mb")
    edad = crudo.get("edad_horas")
    ahora = crudo.get("ahora_mb")
    cuando = _cuando_texto(crudo.get("cuando") or "", hoy_iso)

    # Vencido = el limpiador no dejó línea, o la que dejó ya no es de ahora.
    # Una edad negativa (línea con hora del futuro, relojes desalineados) no
    # es vencimiento: se trata como fresca.
    vencido = mb is None or edad is None or edad > VENCE_HORAS

    if not vencido:
        alerta = mb > TOPE_MB
        if alerta:
            frase = _frase_grande(mb, antes, cuando)
        else:
            frase = _frase_sano(mb, antes, crudo.get("mensajes"), cuando)
        return {
            "mb": mb, "antes_mb": antes, "mensajes": crudo.get("mensajes"),
            "cuando_texto": cuando, "edad_horas": edad, "vencido": False,
            "alerta": alerta, "corto": f"almacén {mb} MB" if alerta else "",
            "frase": frase,
        }

    if ahora is None:
        # Ni diario ni disco: no se sabe. Que lo diga el hueco del resumen.
        raise RuntimeError("el limpiador no dejó ninguna corrida en su diario "
                           "y el disco no se pudo medir")

    return {
        "mb": ahora, "antes_mb": None, "mensajes": None,
        "cuando_texto": cuando, "edad_horas": edad, "vencido": True,
        "alerta": True, "corto": "almacén sin limpiar",
        "frase": _frase_vencido(ahora, cuando),
    }


def _frase_sano(mb, antes, mensajes, cuando):
    partes = [_plegado(mb, antes, cuando) + "."]
    if mensajes is not None:
        partes.append(f"Quedan {_miles(mensajes)} mensajes guardados.")
    partes.append("Lo sano ronda 2 MB.")
    return " ".join(partes)


def _frase_grande(mb, antes, cuando):
    return (f"OJO: pasa de {TOPE_MB} MB. {_plegado(mb, antes, cuando)} y ahí "
            f"se quedó: hay historial acumulado que no logra plegar. Revisar "
            f"los seis límites de sincronización de historial de WAHA — en "
            f"WAHA un 0 no es un límite, es «sin fijar».")


def _frase_vencido(ahora, cuando):
    cuenta = (f"El limpiador no ha corrido desde su última pasada, {cuando},"
              if cuando else
              "El limpiador no tiene ninguna pasada en su diario,")
    return (f"{cuenta} así que su último número ya no dice nada. El disco "
            f"tiene {ahora} MB ahora mismo. Mientras su cron no corra el "
            f"almacén solo crece: es lo primero que hay que revisar en el "
            f"droplet del CRM.")


def _plegado(mb, antes, cuando):
    """«El limpiador lo bajó de 5 MB a 2 MB a las 6:17 pm», sin el punto.

    El salto se nombra porque ES la señal: un `5 -> 2` está plegando bien y un
    `71 -> 68` dice que ya hay historial que no puede plegar. Cuando no hubo
    salto no se menciona: repetir «de 2 MB a 2 MB» no dice nada.
    """
    if antes is not None and antes != mb:
        nucleo = f"lo bajó de {antes} MB a {mb} MB"
    else:
        nucleo = f"lo dejó en {mb} MB"
    return f"El limpiador {nucleo}{_a_las(cuando)}"


def _a_las(cuando):
    return f" {cuando}" if cuando else ""


def _miles(n):
    """20584 -> «20 584». Con espacio, como el resto de los números del
    negocio; la coma la ocupan los decimales."""
    return f"{int(n):,}".replace(",", " ")


def _cuando_texto(cuando, hoy_iso=""):
    """«a las 6:17 pm» si la corrida es de hoy; «el 24/09/2026 a las 6:17 pm»
    si es de otro día. La fecha solo aparece cuando aporta algo, y cuando
    aparece es día/mes, como todas las fechas del negocio."""
    # Se valida el formato aunque el endpoint ya lo garantice: el resumen no
    # puede reventar porque el otro droplet escriba una línea rara.
    calza = re.match(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d)", str(cuando))
    if not calza:
        return ""
    dia, reloj = calza.groups()
    hora = calendario.hora_bonita(reloj)
    if hoy_iso and dia != hoy_iso:
        return f"el {calendario.dmy(dia)} a las {hora}"
    return f"a las {hora}"
