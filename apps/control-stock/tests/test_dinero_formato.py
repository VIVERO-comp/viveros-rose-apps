"""UN SOLO formato de dinero en toda la app (item 6 del lote, 7/10/2026).

El problema que esto clava: la misma pantalla de Finanzas pintaba
«$50,403.00» en la tarjeta de arriba y «$1522.50» tres renglones abajo. El
mismo dinero con dos caras es lo que hace dudar de una cifra. Había además
una tercera cara —«$1 525.00», con espacio— en la agenda y en el aviso de
las 7 p.m.

La regla, para TODA la app: **coma de miles y dos decimales**, y un solo
lugar que lo decida (`calculos.dinero`, que es también el filtro `dinero`
de las plantillas).

Tres candados, de más fuerte a más barato:

1. **Las pantallas**: se piden de verdad y se busca en el HTML un monto de
   cuatro cifras o más SIN su coma. Si alguien pinta `$1522.50` en
   cualquier pantalla que estas pruebas alcancen, esto falla.
2. **El código**: ni un `$` pegado a un formato de monto a mano, ni en los
   módulos ni en las plantillas. Es el candado que atrapa al que todavía
   no tiene pantalla en la lista de arriba.
3. **El formateador**: su contrato, incluido que `None` NO es cero.

Datos QA solamente: ni Odoo, ni Linear, ni Twenty.
"""

import pathlib
import re

import pytest

from app import calculos, pagos_confirmar


# Un monto de cuatro cifras o más SIN su coma: `$1522.50`, `$50403`. El
# bien formateado (`$1,522.50`) no calza porque el `\d{4,}` arranca pegado
# al `$` y se topa con la coma al segundo dígito.
MONTO_SIN_COMA = re.compile(r"\$\d{4,}(?:\.\d{2})?\b")

RAIZ = pathlib.Path(calculos.__file__).resolve().parent


def montos_sin_coma(texto):
    """Los montos mal formateados que haya en `texto` (lista, para que el
    mensaje del fallo diga CUÁL se escapó, no solo que hay uno)."""
    return MONTO_SIN_COMA.findall(texto)


# ---------------------------------------------------------------------------
# 3 · El formateador: su contrato
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("monto,esperado", [
    (1522.5, "$1,522.50"),
    (50403, "$50,403.00"),
    (1525, "$1,525.00"),
    (3.5, "$3.50"),
    (0, "$0.00"),
    (-41068, "$-41,068.00"),
    (1234567.891, "$1,234,567.89"),
])
def test_el_formateador_pone_coma_de_miles_y_dos_decimales(monto, esperado):
    assert calculos.dinero(monto) == esperado


def test_sin_dato_no_es_cero():
    """`None` sale vacío: «no se sabe» nunca se pinta como $0.00 (la misma
    regla del resumen de las 7 p.m.)."""
    assert calculos.dinero(None) == ""


def test_el_filtro_de_las_plantillas_ES_el_formateador():
    """Jinja y Python tienen que usar la MISMA función, no dos copias que
    puedan separarse con el tiempo — que es justo lo que pasó."""
    from app.main import plantillas
    assert plantillas.env.filters["dinero"] is calculos.dinero


def test_los_ayudantes_de_la_casa_delegan_en_el_formateador():
    """Los que antes tenían su propio formato: la agenda y el aviso de las
    7 p.m. ponían un ESPACIO donde va la coma, y /revisar y Control tenían
    cada uno su `:,.2f` suelto."""
    from app import agenda, control, resumen, revisar
    for funcion in (agenda.plata, resumen._plata, revisar._dinero,
                    control._dinero):
        assert funcion(1525) == "$1,525.00", funcion


# ---------------------------------------------------------------------------
# 2 · El código: nadie vuelve a escribir el formato a mano
# ---------------------------------------------------------------------------

# Un `$` pegado a un formato de monto: `f"${x:,.2f}"`, `f"${x:.2f}"`,
# `${{ '%.2f' | format(x) }}`. Todos tienen que pasar por `calculos.dinero`
# o por el filtro `dinero`.
A_MANO = re.compile(r"\$(?:\{\{)?[^\"'\n]{0,40}?(?::,?\.2f\}|%\.2f)")

# `calculos.py` ES el formateador: ahí vive el único `:,.2f` legítimo de la
# casa. `reconciliacion.py` escribe el CSV del informe SIN `$` y sin coma a
# propósito — es para una hoja de cálculo, no para leerlo.
SIN_FORMATO_A_PROPOSITO = {"calculos.py", "reconciliacion.py"}


def archivos_de_la_app():
    for ruta in sorted(RAIZ.rglob("*.py")):
        yield ruta
    for ruta in sorted((RAIZ / "plantillas").rglob("*.html")):
        yield ruta


def test_ningun_archivo_formatea_dinero_a_mano():
    culpables = []
    for ruta in archivos_de_la_app():
        if ruta.name in SIN_FORMATO_A_PROPOSITO:
            continue
        for n, linea in enumerate(ruta.read_text().splitlines(), 1):
            if A_MANO.search(linea):
                culpables.append(f"{ruta.relative_to(RAIZ)}:{n}: "
                                 f"{linea.strip()[:90]}")
    assert not culpables, (
        "Dinero formateado a mano. Pasalo por `calculos.dinero` (Python) o "
        "por el filtro `dinero` (plantillas):\n" + "\n".join(culpables))


def test_las_dos_excepciones_siguen_siendo_lo_que_dicen_ser():
    """El contrapeso de la lista de arriba: que las dos exenciones sigan
    siendo UNA cosa cada una, no una grieta que se agrande.

    - `calculos.py`: el `:,.2f` vive en `dinero()` y en ningún otro lado.
    - `reconciliacion.py`: lo crudo es el CSV, y fuera de esas tres celdas
      ese archivo no vuelve a pintar un `$` a mano.
    """
    calc = [l for l in (RAIZ / "calculos.py").read_text().splitlines()
            if not l.lstrip().startswith("#")]
    assert sum(1 for l in calc if ":,.2f" in l) == 1

    recon = (RAIZ / "reconciliacion.py").read_text()
    assert "para una hoja de cálculo" in recon
    sin_csv = recon
    for celda in ("f\"{v['total']:.2f}\"", "f\"{v['pagado']:.2f}\"",
                  "f\"{v['debe']:.2f}\""):
        assert celda in recon, celda
        sin_csv = sin_csv.replace(celda, "")
    assert not A_MANO.search(sin_csv)


# ---------------------------------------------------------------------------
# 1 · Las pantallas: ningún monto de cuatro cifras sale sin su coma
# ---------------------------------------------------------------------------

# Ventas QA con montos GRANDES a propósito: con cifras de tres dígitos esta
# prueba pasaría sin probar nada.
# `confirmada` (BLOQUE 59.2): Finanzas suma SOLO ventas confirmadas, así
# que sin la marca estas dos QA no entrarían en ninguna tarjeta y la
# prueba del formato se quedaría sin montos que mirar. Ponerla es FIEL, no
# un parche: las clases C y D solo existen en órdenes confirmadas — el
# motor las asigna dentro de `if confirmada:` (reconciliacion._clasificar).
VENTAS_GRANDES = [
    {"orden_id": 7001, "nombre": "S07001", "cliente": "Cliente QA Grande",
     "total": 50403.0, "pagado": 9335.0, "debe": 41068.0, "clase": "C",
     "confirmada": True, "motivo": "", "entregado_odoo": True},
    {"orden_id": 7002, "nombre": "S07002", "cliente": "Cliente QA Dos",
     "total": 1522.5, "pagado": 1522.5, "debe": 0.0, "clase": "D",
     "confirmada": True, "motivo": ""},
]

# Las pantallas que esta prueba alcanza sin red. No es «todas»: es el
# conjunto que se puede pedir de verdad con el cliente QA. El candado del
# código (arriba) cubre las demás.
PANTALLAS = ["/finanzas", "/", "/pedidos", "/revisar", "/compras",
             "/contactos", "/control"]


@pytest.fixture
def con_ventas_grandes(monkeypatch):
    monkeypatch.setattr(
        pagos_confirmar, "_informe",
        lambda: {"ventas": [dict(v) for v in VENTAS_GRANDES], "huecos": []})


@pytest.fixture
def admin_qa(cliente, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    return cliente


@pytest.mark.parametrize("ruta", PANTALLAS)
def test_ninguna_pantalla_pinta_un_monto_sin_coma(admin_qa, con_ventas_grandes,
                                                  ruta):
    respuesta = admin_qa.get(ruta)
    if respuesta.status_code != 200:
        pytest.skip(f"{ruta} contestó {respuesta.status_code} en este QA")
    malos = montos_sin_coma(respuesta.text)
    assert not malos, f"{ruta} pinta montos sin coma de miles: {malos[:8]}"


def test_finanzas_pinta_los_miles_con_coma(admin_qa, con_ventas_grandes):
    """El caso concreto que lo destapó: la tarjeta de arriba y la fila de
    la cola, el mismo dinero, con la misma cara."""
    texto = admin_qa.get("/finanzas").text
    assert "$51,925.50" in texto     # vendido: la suma de las dos QA
    assert "$41,068.00" in texto     # por cobrar (la tarjeta roja)
    assert "$10,857.50" in texto     # por confirmar (la tarjeta)
    assert "$9,335.00" in texto      # la FILA grande de la cola
    assert "$1,522.50" in texto      # la otra fila, la que antes decía
    assert "$1522.50" not in texto   # …esto, sin coma, tres renglones abajo


def test_la_prueba_de_pantalla_sabe_fallar():
    """Que el regex de arriba no sea un colador: si un día deja de ver un
    `$1522.50`, estas pruebas dirían «pasa todo» sin mirar nada."""
    assert montos_sin_coma("<b>$1522.50</b>") == ["$1522.50"]
    assert montos_sin_coma("total $50403 hoy") == ["$50403"]
    assert montos_sin_coma("<b>$1,522.50</b>") == []
    assert montos_sin_coma("$50,403.00 y $3.50 y $999.99") == []
