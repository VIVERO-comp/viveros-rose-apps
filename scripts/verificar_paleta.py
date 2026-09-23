#!/usr/bin/env python3
"""Guardian de la paleta unica de colores (docs/plan-colores.md).

Comprueba que nada se desalinee de paleta.json:

  1. Las dos copias de paleta.json (viveros-rose-apps y
     viveros-rose-frontend) son byte a byte identicas.
  2. El paleta.css generado del frontend esta al dia con su paleta.json
     (delega en scripts/generar_paleta_css.py --check del frontend).
  3. colores.py de control-stock carga exactamente las familias del json.
  4. Las paletas viejas duplicadas no reviven: ni los hex pastel viejos en
     admin.astro/chats.astro, ni repintar()/COLORES_CRM/ETIQUETAS_CHIP en
     crm_twenty.py, ni hex sueltos de retail en retail.py.
  5. Los tonos que emite chips-lead.ts existen como familia (o 'neutro').

Sale con codigo 1 y el detalle si algo no coincide. El frontend se busca en
../viveros-rose-frontend (hermano de este repo) o en $FRONTEND_DIR.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONTEND = Path(os.environ.get(
    "FRONTEND_DIR", RAIZ.parent / "viveros-rose-frontend"))

PALETA_APPS = RAIZ / "apps" / "control-stock" / "app" / "paleta.json"
PALETA_FRONT = FRONTEND / "src" / "data" / "paleta.json"

fallos = []


def fallo(mensaje):
    fallos.append(mensaje)
    print(f"  ✗ {mensaje}")


def bien(mensaje):
    print(f"  ✓ {mensaje}")


def revisar_copias():
    print("1. Copias de paleta.json")
    if not PALETA_FRONT.exists():
        fallo(f"no existe {PALETA_FRONT} (¿repo del frontend en otra ruta? usar FRONTEND_DIR)")
        return
    if PALETA_APPS.read_bytes() == PALETA_FRONT.read_bytes():
        bien("apps y frontend llevan el mismo paleta.json")
    else:
        fallo("paleta.json difiere entre apps y frontend — copiar el bueno al otro repo")


def revisar_css_generado():
    print("2. paleta.css generado del frontend")
    generador = FRONTEND / "scripts" / "generar_paleta_css.py"
    if not generador.exists():
        fallo(f"no existe {generador}")
        return
    r = subprocess.run([sys.executable, str(generador), "--check"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        bien("paleta.css al dia con paleta.json")
    else:
        fallo(f"paleta.css desfasado: {r.stdout.strip() or r.stderr.strip()}")


def revisar_colores_py():
    print("3. colores.py de control-stock")
    sys.path.insert(0, str(RAIZ / "apps" / "control-stock"))
    try:
        from app import colores  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        fallo(f"no importa app.colores: {e}")
        return
    esperado = json.loads(PALETA_APPS.read_text())
    if colores.FAMILIAS == esperado["familias"] and colores.ASIGNACIONES == esperado["asignaciones"]:
        bien("colores.py carga las familias y asignaciones del json")
    else:
        fallo("colores.py no coincide con paleta.json")


HEX_PASTEL_VIEJOS = [
    # los 7 tonos pastel que vivian copiados en admin.astro y chats.astro
    "#e4f1e7", "#fbe8e5", "#fbe6f0", "#e6edfa", "#fbf0dc", "#efe9fa", "#f2ece3",
    # y su variante oscura de chats.astro
    "#1c2f23", "#35201d", "#331f2a", "#1e2739", "#322a18", "#2a2338", "#2e2820",
]


def revisar_duplicados_muertos():
    print("4. Paletas duplicadas eliminadas")
    for nombre in ("admin.astro", "chats.astro"):
        ruta = FRONTEND / "src" / "pages" / nombre
        texto = ruta.read_text() if ruta.exists() else ""
        vivos = [h for h in HEX_PASTEL_VIEJOS if h in texto]
        if vivos:
            fallo(f"{nombre} volvio a tener hex de la paleta vieja: {', '.join(vivos)}")
        else:
            bien(f"{nombre} sin la paleta pastel copiada")
    crm = (RAIZ / "apps" / "control-stock" / "app" / "crm_twenty.py").read_text()
    if re.search(r"\bdef repintar\b|_MAPA_PINTURA|^COLORES_CRM\s*=|^ETIQUETAS_CHIP\s*=", crm, re.M):
        fallo("crm_twenty.py volvio a tener repintar() o mapas de color propios")
    else:
        bien("crm_twenty.py sin repintar() ni mapas propios")
    retail = (RAIZ / "apps" / "control-stock" / "app" / "retail.py").read_text()
    if re.search(r'COLORES\s*=\s*\{', retail):
        fallo("retail.py volvio a definir COLORES con hex propios")
    else:
        bien("retail.py toma COLORES de colores.py")
    calendario = (RAIZ / "apps" / "control-stock" / "app" / "calendario.py").read_text()
    if re.search(r'^TIPOS\s*=\s*\[|^FILTROS\s*=\s*\[', calendario, re.M):
        fallo("calendario.py volvio a definir TIPOS/FILTROS con hex propios")
    else:
        bien("calendario.py toma TIPOS y FILTROS de colores.py")


def revisar_tonos_chips_lead():
    print("5. Tonos de chips-lead.ts")
    ruta = FRONTEND / "src" / "lib" / "chips-lead.ts"
    if not ruta.exists():
        fallo(f"no existe {ruta}")
        return
    texto = ruta.read_text()
    familias = set(json.loads(PALETA_APPS.read_text())["familias"]) | {"neutro"}
    tonos = set(re.findall(r"tono:\s*'([a-z]+)'", texto))
    for bloque in re.findall(r"TONO_\w+: Record<string, string> = \{(.*?)\};", texto, re.S):
        tonos |= set(re.findall(r":\s*'([a-z]+)'", bloque))
    raros = sorted(tonos - familias)
    if raros:
        fallo(f"chips-lead.ts usa tonos que no son familia de la paleta: {', '.join(raros)}")
    else:
        bien(f"todos los tonos son familias de la paleta ({len(tonos)} usados)")


def main():
    print(f"Paleta unica — apps: {RAIZ.name}, frontend: {FRONTEND}")
    revisar_copias()
    revisar_css_generado()
    revisar_colores_py()
    revisar_duplicados_muertos()
    revisar_tonos_chips_lead()
    if fallos:
        print(f"\n{len(fallos)} problema(s). La fuente de verdad es paleta.json.")
        return 1
    print("\nTodo alineado con paleta.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
