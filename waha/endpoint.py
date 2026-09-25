#!/usr/bin/env python3
"""Sincronización INMEDIATA de un lead, para que Control no espere 2 minutos.

Lo llama control-stock al asignar o mover un lead:

    POST http://10.116.0.3:3002/sincro/lead
    Authorization: Bearer <SINCRO_SECRET>
    {"lead": "LEAD-62"}

Escucha SOLO en la IP privada del droplet (10.116.0.3), que es por donde se
ven los dos droplets. Nunca en la pública: desde internet no existe.

Contrato con Control, y esto es lo importante: **si esto falla o tarda,
Control sigue igual.** Quien llama no espera la respuesta ni la mira; el
sincronizador de cada 2 minutos arregla después lo que aquí se pierda. Por
eso el endpoint puede ser simple y no necesita cola ni reintentos.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/waha")
import sincronizador as sinc  # noqa: E402

PUERTO = int(os.environ.get("SINCRO_PUERTO", "3002"))
ESCUCHA = os.environ.get("SINCRO_ESCUCHA", "0.0.0.0")
SECRETO = sinc.ENV.get("SINCRO_SECRET", "")


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

    def do_GET(self):
        if self.path == "/salud":
            return self._responder(200, {"ok": True, "armado": bool(SECRETO)})
        self._responder(404, {"ok": False})

    def do_POST(self):
        if self.path != "/sincro/lead":
            return self._responder(404, {"ok": False})
        if not SECRETO:
            return self._responder(503, {"ok": False,
                                         "motivo": "falta SINCRO_SECRET"})
        import hmac
        dado = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(dado, SECRETO):
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
