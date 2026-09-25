#!/usr/bin/env python3
"""Las etiquetas de WhatsApp en cuatro familias, un color cada una.

Orden pedido por Abraham (25/09/2026), y ese orden es el de CREACION: es
la unica palanca que tenemos: WhatsApp las lista como las fue recibiendo.
Por eso las nuestras se borran y se vuelven a crear en secuencia, en vez
de renombrarlas en su sitio (renombrar funciona, pero deja el orden viejo).

Las tres de WhatsApp ('No leidos', 'Favoritos', 'Grupos') NO se tocan.

Un color por FAMILIA, no por etiqueta: el color dice de que familia es y
el nombre dice cual. Esto reemplaza a proposito la regla de "cada cosa su
color de paleta.json" para este caso; lo pidio asi para reconocerlas de un
vistazo en el telefono.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

RUTA = os.path.dirname(os.path.abspath(__file__))
WAHA = "http://127.0.0.1:3001"
SESION = "vivero"
DE_WHATSAPP = {"No leídos", "No leidos", "Favoritos", "Grupos"}

AZUL, VERDE, ROJO, MORADO = "#00a0f2", "#93ceac", "#f74848", "#9368cf"

# El orden importa: es el que va a ver en el telefono.
PLAN = (
    [("Mary", AZUL), ("Ruben", AZUL), ("Salomón", AZUL), ("Abraham", AZUL)]
    + [(n, VERDE) for n in ("Plantas", "Eventos", "Paisajismo",
                            "Mantenimiento", "Mayorista")]
    + [("🔴 Responder", ROJO)]
    + [(n, MORADO) for n in ("Nuevo", "Hablando", "Cotizado", "Por agendar",
                             "Agendado", "Entregado")]
)

clave = None
for linea in open(os.path.join(RUTA, ".env")):
    if linea.startswith("WAHA_API_KEY="):
        clave = linea.split("=", 1)[1].strip()


def api(ruta, datos=None, metodo=None):
    pet = urllib.request.Request(
        WAHA + ruta,
        data=json.dumps(datos).encode() if datos is not None else None,
        headers={"X-Api-Key": clave, "Content-Type": "application/json"},
        method=metodo)
    try:
        with urllib.request.urlopen(pet, timeout=25) as r:
            cuerpo = r.read()
        return json.loads(cuerpo) if cuerpo else None
    except urllib.error.HTTPError as e:
        return {"__error": e.code, "__cuerpo": e.read().decode()[:200]}


antes = api("/api/%s/labels" % SESION)
nuestras = [l for l in antes if l["name"] not in DE_WHATSAPP]
print("ANTES: %d etiquetas (%d nuestras)" % (len(antes), len(nuestras)))
for l in antes:
    print("   id=%-4s %-9s %r%s" % (l["id"], l.get("colorHex"), l["name"],
                                    "   <- de WhatsApp, no se toca"
                                    if l["name"] in DE_WHATSAPP else ""))
print()

print("BORRANDO las nuestras (%d)" % len(nuestras))
for l in nuestras:
    r = api("/api/%s/labels/%s" % (SESION, l["id"]), metodo="DELETE")
    err = (r or {}).get("__error") if isinstance(r, dict) else None
    print("   %-16s %s" % (l["name"], "borrada" if not err else "ERROR %s %s" % (err, r.get("__cuerpo"))))
    time.sleep(0.4)
print()

print("CREANDO en el orden pedido (%d)" % len(PLAN))
creadas, fallidas = [], []
for nombre, color in PLAN:
    r = api("/api/%s/labels" % SESION, datos={"name": nombre, "colorHex": color})
    if isinstance(r, dict) and r.get("__error"):
        fallidas.append((nombre, r.get("__cuerpo")))
        print("   %-16s ERROR %s %s" % (nombre, r["__error"], r["__cuerpo"]))
    else:
        creadas.append(nombre)
        print("   %-16s id=%-4s %s" % (nombre, (r or {}).get("id"), color))
    time.sleep(0.5)
print()

despues = api("/api/%s/labels" % SESION)
print("DESPUES: %d etiquetas, en este orden" % len(despues))
familia = {AZUL: "representante", VERDE: "interés", ROJO: "responder",
           MORADO: "estado"}
for l in despues:
    print("   id=%-4s %-9s %-16s %s" % (
        l["id"], l.get("colorHex"), l["name"],
        familia.get(l.get("colorHex"), "de WhatsApp")))
if fallidas:
    print()
    print("FALLARON:", fallidas)
    sys.exit(1)
