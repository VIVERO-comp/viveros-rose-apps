#!/usr/bin/env python3
"""Las dos preguntas que control-stock le hace al droplet del CRM.

1. Sincronización INMEDIATA de un lead, para que Control no espere 2 minutos:

    POST http://10.116.0.3:3002/sincro/lead
    Authorization: Bearer <SINCRO_SECRET>
    {"lead": "LEAD-62"}

2. El tamaño del almacén de WAHA, para el resumen de las 7 p.m.:

    GET http://10.116.0.3:3002/almacen
    Authorization: Bearer <SINCRO_SECRET>

Escucha SOLO en la IP privada del droplet (10.116.0.3), que es por donde se
ven los dos droplets. Nunca en la pública: desde internet no existe. Por eso
el almacén se pregunta por AQUÍ y no abriendo un puerto, un túnel ni un
servicio nuevos: la puerta y el secreto ya estaban.

Contrato con Control, y esto es lo importante: **si esto falla o tarda,
Control sigue igual.** Quien llama no espera la respuesta ni la mira; el
sincronizador de cada 2 minutos arregla después lo que aquí se pierda. Por
eso el endpoint puede ser simple y no necesita cola ni reintentos. El
resumen sí mira la respuesta de `/almacen`, pero si no llega deja el
renglón en blanco y lo dice — nunca en cero.

Este servidor solo LEE: mide el disco y lee el diario del limpiador. No
escribe nada en `~/waha` (su volumen está montado de solo lectura).

El compose de este servicio NO vive en `~/waha` sino en `~/sincro/` del
droplet del CRM (proyecto `sincro`, `network_mode: host`, monta
`/home/hermes/waha` como `:ro`): un `docker compose restart` ahí toma este
archivo nuevo **sin tocar el contenedor de WAHA**, que es lo que hay que
cuidar — recrear WAHA se lleva la sesión y el webhook del «autor».
"""
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/waha")
import sincronizador as sinc  # noqa: E402

PUERTO = int(os.environ.get("SINCRO_PUERTO", "3002"))
ESCUCHA = os.environ.get("SINCRO_ESCUCHA", "0.0.0.0")
SECRETO = sinc.ENV.get("SINCRO_SECRET", "")

# La carpeta donde vive este archivo: `/waha` dentro del contenedor,
# `/home/hermes/waha` si alguien lo corre a mano en el droplet. El mismo
# truco que usa el sincronizador, para que las dos formas funcionen.
RUTA = os.path.dirname(os.path.abspath(__file__))
DIARIO = os.path.join(RUTA, "almacen.log")
ALMACEN = os.path.join(RUTA, ".sessions")

# Una corrida del limpiador, tal como la escribe `limpiar_almacen.sh`:
#   2026-09-25 18:17:03 · almacen 5 MB -> 2 MB · quedan 132 mensajes
# De esta línea se leen los MB y los mensajes. **Su fecha NO se usa para
# nada**: ver `_ultima_corrida()`.
CORRIDA = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d · almacen (\d+) MB -> (\d+) MB"
    r" · quedan (\S+) mensajes")

# La otra línea que el limpiador sabe escribir, inmediatamente después de su
# corrida y en la misma pasada, cuando el almacén quedó grande:
#   2026-09-25 18:17:03 · OJO: el almacen sigue en 68 MB (lo sano es ~2)...
ALERTA = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d · OJO:")


def _ultima_corrida():
    """{mtime, edad_horas, cola, antes_mb, mb, mensajes} de la última corrida
    del limpiador, o {} si el diario no existe.

    LA EDAD SALE DEL `mtime` DEL ARCHIVO, NO DE LA FECHA DE LA LÍNEA. Ese fue
    un bug real (25/09/2026): el HOST de este droplet corre en UTC y es el
    host quien escribe el diario por cron, mientras este proceso corre con
    `TZ=America/Panama`. Parsear el texto y compararlo contra el reloj de acá
    daba **−4 horas**, así que la edad nunca pasaba de las 3 horas y la
    detección de «el limpiador murió» quedaba apagada **en silencio** — justo
    la red de seguridad que este endpoint existe para alimentar. Un `mtime` es
    un epoch absoluto: no hay zona que interpretar, y sigue siendo correcto si
    mañana alguien cambia el `TZ` del contenedor o la del host.

    Por eso tampoco viaja la fecha de la línea: si viajara, alguien la usaría
    para mostrar la hora y el bug volvería por la puerta de atrás. Lo que
    viaja es el `mtime`, y quien lo muestra lo formatea en hora de Panamá.

    El caso de borde: el `mtime` es del ARCHIVO, no de la línea. Vale como
    fecha de la última corrida solo si el diario TERMINA en una corrida, o en
    la alerta que el limpiador escribe justo después de una (misma pasada,
    segundos de diferencia). Si termina en cualquier otra cosa —alguien
    escribió a mano, un logrotate, un formato nuevo— el archivo es fresco pero
    la corrida podría ser vieja: entonces `edad_horas` vuelve **None** y el
    resumen lo trata como vencido. Preferir «no sé, mirá el disco» a un «todo
    bien» que no se puede sostener.
    """
    try:
        mtime = os.path.getmtime(DIARIO)
        with open(DIARIO, "r", encoding="utf-8", errors="replace") as diario:
            lineas = [l.strip() for l in diario.readlines()[-400:] if l.strip()]
    except OSError:
        return {}

    cola = "desconocida"
    if lineas and CORRIDA.match(lineas[-1]):
        cola = "corrida"
    elif (len(lineas) > 1 and ALERTA.match(lineas[-1])
            and CORRIDA.match(lineas[-2])):
        cola = "alerta"

    datos = {"mtime": round(mtime), "cola": cola, "edad_horas": None,
             "antes_mb": None, "mb": None, "mensajes": None}
    if cola != "desconocida":
        datos["edad_horas"] = round((time.time() - mtime) / 3600.0, 2)

    # Los MB de la última corrida que haya, incluso si el diario termina en
    # algo raro: es el último «después» conocido, y sigue siendo el número
    # honesto. Lo que no se puede sostener en ese caso es su FECHA.
    for linea in reversed(lineas):
        calza = CORRIDA.match(linea)
        if not calza:
            continue
        antes, despues, mensajes = calza.groups()
        datos.update({"antes_mb": int(antes), "mb": int(despues),
                      "mensajes": None if mensajes == "?" else int(mensajes)})
        break
    return datos


def _disco_mb():
    """Lo que pesa el almacén AHORA MISMO, en MB, o None si no se pudo medir.

    Aproximado a propósito: suma tamaños de archivo, no bloques de disco
    como `du`. Sirve para un solo caso —saber si el disco creció cuando el
    limpiador dejó de correr—, y para eso la diferencia no importa.
    """
    total = 0
    visto = False
    for carpeta, _subcarpetas, archivos in os.walk(ALMACEN):
        for nombre in archivos:
            try:
                total += os.path.getsize(os.path.join(carpeta, nombre))
                visto = True
            except OSError:
                pass
    return round(total / 1048576) if visto else None


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _responder(self, codigo, cuerpo):
        datos = json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, formato, *args):
        # El log NUNCA lleva el secreto: solo la ruta y el codigo.
        sys.stderr.write("%s · %s\n" % (self.log_date_time_string(),
                                        formato % args))

    def _autorizado(self):
        """El mismo Bearer para las dos puertas: es el mismo secreto."""
        import hmac
        dado = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return hmac.compare_digest(dado, SECRETO)

    def do_GET(self):
        if self.path == "/salud":
            return self._responder(200, {"ok": True, "armado": bool(SECRETO)})
        if self.path == "/almacen":
            # Con candado: cuánto guarda el WhatsApp del negocio es dato del
            # negocio, y nada de aquí se contesta sin credencial.
            if not SECRETO:
                return self._responder(503, {"ok": False,
                                             "motivo": "falta SINCRO_SECRET"})
            if not self._autorizado():
                return self._responder(401, {"ok": False})
            cuerpo = {"ok": True, "mtime": None, "cola": "", "mb": None,
                      "antes_mb": None, "mensajes": None, "edad_horas": None,
                      "ahora_mb": _disco_mb()}
            cuerpo.update(_ultima_corrida())
            return self._responder(200, cuerpo)
        self._responder(404, {"ok": False})

    def do_POST(self):
        if self.path != "/sincro/lead":
            return self._responder(404, {"ok": False})
        if not SECRETO:
            return self._responder(503, {"ok": False,
                                         "motivo": "falta SINCRO_SECRET"})
        if not self._autorizado():
            return self._responder(401, {"ok": False})
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        except ValueError:
            return self._responder(400, {"ok": False, "motivo": "cuerpo ilegible"})
        ref = str(cuerpo.get("lead") or "").strip().upper()
        if not ref.startswith("LEAD-"):
            return self._responder(400, {"ok": False, "motivo": "falta lead"})
        try:
            hecho = sinc.sincronizar_uno(ref, aplicar=True)
        except Exception as fallo:            # nunca tumba el servidor
            return self._responder(500, {"ok": False, "motivo": str(fallo)[:160]})
        self._responder(200, {"ok": True, **hecho})


if __name__ == "__main__":
    print("endpoint de sincronizacion en %s:%s · armado=%s" % (
        ESCUCHA, PUERTO, bool(SECRETO)), flush=True)
    ThreadingHTTPServer((ESCUCHA, PUERTO), Manejador).serve_forever()
