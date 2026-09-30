"""Cuánto le costó Google el día: las consultas de tiempo de viaje del envío.

Las tarifas de envío nuevas preguntan a la **Routes API de Google** cuánto se
tarda en llegar al punto que el cliente marcó en el mapa. Eso se paga por
consulta, así que el order-api lleva dos topes: 10 por hora por visitante y un
**tope diario global** (`ROUTES_TOPE_DIARIO`, hoy 300). Tocado el tope, el
sistema deja de llamar a Google y cobra con el **tiempo de respaldo del
corregimiento** — una tabla fija, sin costo.

Ese freno es exactamente lo que nadie ve si no se lo cuentan: el envío sigue
cotizando, ningún cliente se queja y ninguna pantalla se rompe. El día que el
tope se toque a las diez de la mañana, el resto de la jornada se cobra con un
tiempo aproximado y el negocio se entera cuando alguien se acuerda de mirar.
Por eso el renglón vive en el resumen de las 7 p.m., al lado del almacén de
WhatsApp: no es un bloque del negocio, es salud del sistema.

**Los números viven en el order-api, que es quien llama a Google.** Se
preguntan por la puerta que YA existe entre las dos apps —`ORDER_API_URL` +
`ORDER_API_KEY`, la misma con la que control-stock ajusta inventario y publica
plantas—, con `GET /api/admin/metricas-envio?dia=YYYY-MM-DD`. Ninguna variable
nueva, ningún puerto nuevo: pedir una segunda variable para la misma puerta es
cómo nació el lío de `SINCRO_SECRETO` contra `SINCRO_SECRET`, que apagó el
enganche de WhatsApp una vez sin dejar un solo error en ningún log.

**Si no se puede leer, el renglón NO sale y lo dice** — nunca «0 consultas»,
que parecería la mejor noticia del día (nada gastado) cuando en realidad
significa que no se sabe. Y a diferencia de los otros bloques, un renglón de
mapas que no se puede leer **no ensucia el titular con «con huecos»**: esto
mide una función que se está encendiendo, y mientras el order-api no tenga la
ruta —se despliega aparte— el titular del dueño no tiene por qué degradarse
todas las noches. El motivo se dice en la pantalla y queda en el log.
"""

import logging
import os

import httpx

# La ruta del order-api. Se escribe aquí una sola vez: si allá la mueven, se
# cambia en este renglón y en ningún otro lado.
RUTA = "/api/admin/metricas-envio"


def _aviso(texto):
    """Un aviso al log. Aparte para que las pruebas puedan mirarlo — y propio
    de este módulo: tomarlo prestado de otro es cómo se cuela un NameError que
    solo aparece el día que algo falla de verdad."""
    logging.getLogger("control_stock").warning(texto)


def _url():
    return (os.environ.get("ORDER_API_URL") or "").strip().rstrip("/")


def _clave():
    return (os.environ.get("ORDER_API_KEY") or "").strip()


def configurado():
    """¿Se le puede preguntar al order-api? Hacen falta las dos, como para
    ajustar inventario."""
    return bool(_url() and _clave())


def leer(dia_iso, timeout=6.0):
    """El crudo del order-api para ese día. Lanza si no se puede saber.

    `dia_iso` es el día en hora de PANAMÁ (`YYYY-MM-DD`), el mismo que el
    resumen muestra: el corte del tope diario lo decide el order-api, y las
    dos apps tienen que estar hablando del mismo día o el número no dice nada.
    """
    respuesta = httpx.get(
        _url() + RUTA,
        params={"dia": dia_iso},
        headers={"X-API-Key": _clave()},
        timeout=timeout)
    if respuesta.status_code == 404:
        # El order-api contesta, pero es una versión sin esta ruta. Se dice con
        # nombre y apellido: las tarifas de envío por punto se despliegan
        # aparte, así que este es el error esperado hasta que aquello suba, y
        # el crudo de httpx no lo explicaría.
        raise RuntimeError(f"el order-api todavía no tiene la ruta {RUTA} "
                           f"(falta desplegar las tarifas de envío por punto)")
    if respuesta.status_code == 401:
        raise RuntimeError("el order-api rechazó la ORDER_API_KEY de esta app")
    respuesta.raise_for_status()
    return respuesta.json()


def bloque(dia_iso, timeout=6.0):
    """El renglón listo para la pantalla. Lanza si el order-api no contesta."""
    return armar(leer(dia_iso, timeout))


def armar(crudo):
    """Decide el renglón a partir del crudo. Sin red: aquí está toda la regla,
    y por eso se puede probar sin levantar nada.

    Vuelve {consultas, respaldo, tope, alerta, corto, frase}. `corto` es lo que
    el titular del aviso nombra — vacío en un día normal, porque un titular no
    gasta espacio en lo que está bien.
    """
    consultas = _entero(crudo.get("consultas_google"))
    respaldo = _entero(crudo.get("respaldo_corregimiento"))
    if consultas is None or respaldo is None:
        # Sin los dos números no hay renglón que armar. Rellenarlos con ceros
        # sería inventar la mejor noticia posible («no se gastó nada») a partir
        # de no saber nada.
        raise RuntimeError("el order-api no mandó las cuentas del día "
                           "(consultas_google y respaldo_corregimiento)")

    tope = _entero(crudo.get("tope"))
    if "tope_alcanzado" in crudo:
        alerta = bool(crudo["tope_alcanzado"])
    else:
        # No es un invento: es aritmética sobre lo que sí mandó. Si tampoco
        # mandó el tope, no hay con qué comparar y no se grita.
        alerta = tope is not None and consultas >= tope

    return {
        "consultas": consultas,
        "respaldo": respaldo,
        "tope": tope,
        "alerta": alerta,
        "corto": "tope de Google" if alerta else "",
        "frase": (_frase_tope(respaldo, tope) if alerta
                  else _frase_sana(consultas, respaldo, tope)),
    }


def _frase_sana(consultas, respaldo, tope):
    """«Mapas: 42 consultas a Google hoy, 3 veces se usó el tiempo de
    respaldo.» — en palabras del dueño, sin jerga de API.

    Un `0` de verdad se escribe `0` y no se adorna: decir «hoy no hizo falta
    preguntarle a Google» sería explicar el cero, y el cero puede venir de
    cosas distintas (nadie cotizó, o el freno por visitante se adelantó). El
    renglón cuenta lo que pasó; el porqué no lo sabe.
    """
    cuenta = f"{consultas} consulta{'s' if consultas != 1 else ''} a Google hoy"
    if respaldo:
        veces = "1 vez se usó" if respaldo == 1 else f"{respaldo} veces se usó"
        cuenta += f", {veces} el tiempo de respaldo del corregimiento"

    frase = "Mapas: " + cuenta + "."
    if tope:
        frase += f" El tope del día son {tope}."
    return frase


def _frase_tope(respaldo, tope):
    """El aviso destacado, con el mismo tono que el del almacén disparado: qué
    pasó, y qué significa para lo que se cobró el resto del día.

    Lleva las veces del respaldo aunque sea el caso de alarma: son los tres
    números que pidió el dueño, y el que dice cuánto de la jornada se cobró
    con un tiempo aproximado. El de consultas ya está en el título del
    renglón.
    """
    # Sin el número la frase tiene que seguir leyéndose sola: «el tope diario
    # de consultas a Google». Pegar el número con un trozo opcional dejaba
    # «el tope diario a Google», que no es español.
    cuantas = f"de {tope} consultas" if tope else "de consultas"
    veces = (f" ({respaldo} {'vez' if respaldo == 1 else 'veces'})"
             if respaldo else "")
    return (f"⚠️ Se alcanzó el tope diario {cuantas} a Google; el resto del día "
            f"se cobró con el tiempo de respaldo del corregimiento{veces}. Los "
            f"envíos siguen cotizando, pero con un tiempo aproximado en vez del "
            f"real. Si esto se repite, hay que subir el tope "
            f"(ROUTES_TOPE_DIARIO en el order-api) o ver quién está "
            f"consultando tanto.")


def _entero(valor):
    """El número, o None si no vino o no es un número. None es «no sé», que
    NO es lo mismo que 0 y nunca se confunde con él."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None
