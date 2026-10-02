"""El apoyo de la pantalla «Ventas a revisar» (Orquesta · M1, 01/10/2026).

La pantalla es SOLO LECTURA sobre la foto que arma `app/reconciliacion.py`
(`informe_datos()`, el contrato de M1): acá no se consulta Odoo ni Linear
ni Twenty — este módulo solo traduce esa foto a palabras de pantalla y
guarda las notas de revisión del admin.

Las palabras de pantalla son las del negocio: Cotizada, Vencida, Debe,
Pagado, Entregado, Revisar. La jerga técnica de los sistemas de origen
tiene PROHIBIDO llegar al HTML (hay prueba que lo vigila).

Lo único que se ESCRIBE es la tabla `revision_nota`: texto libre de quién
revisó qué. **Nunca guarda nada que signifique «pagado»** — registrar un
pago o confirmar una entrega es la fase M2, y sus botones salen
desactivados a propósito.

El import de `reconciliacion` es perezoso (dentro de `informe()`): la otra
mitad de M1 vive en ese módulo y así este archivo no se cae si aquel
todavía se está escribiendo — y las pruebas pueden suplantarlo limpio.
"""

from datetime import datetime

from .datos import ZONA_PANAMA, _db

# La palabra de pantalla de cada clase del informe. Las clases A..H NO son
# estados del embudo: no tocan paleta.json ni colores.ASIGNACIONES — el
# chip se pinta con CSS local de la plantilla.
PALABRA_DE_CLASE = {
    "A": "Cotizada",   # cotización viva, sin plata
    "B": "Vencida",    # cotización que ya venció
    "C": "Debe",       # entregada con saldo pendiente (la marca roja)
    "D": "Pagado",     # pagada completa
    "E": "Entregado",  # entregada y al día
    "F": "Revisar",    # pago fuera del sistema
    "G": "Revisar",    # entrega por confirmar
    "H": "Revisar",    # diferencia que no calza en ninguna otra
}

# El tono del chip (clases CSS locales rv-*): gris/neutro por defecto,
# verde lo sano, dorado lo que espera confirmación, rojo las diferencias.
TONO_DE_CLASE = {
    "A": "neutro", "B": "neutro",
    "C": "rojo", "H": "rojo",
    "D": "verde", "E": "verde",
    "F": "dorado", "G": "dorado",
}


def informe():
    """La foto completa de la reconciliación, tal como la arma el módulo
    real. Import perezoso a propósito (ver el docstring del módulo)."""
    from . import reconciliacion
    return reconciliacion.informe_datos()


def _dinero(monto):
    """Con separador de miles: el renglón de fuera de alcance habla de
    montos de cuatro cifras ($3,109.85) y sin la coma se leen mal."""
    return f"${monto:,.2f}"


def preparar(ventas):
    """Cada venta del informe, con sus palabras de pantalla ya decididas
    en Python (la plantilla no traduce nada): `chip`, `tono` y — solo para
    la clase C con saldo — la marca roja «Entregado, debe $X»."""
    listas = []
    for venta in ventas:
        v = dict(venta)
        clase = (v.get("clase") or "").strip().upper()
        v["chip"] = PALABRA_DE_CLASE.get(clase, "Revisar")
        v["tono"] = TONO_DE_CLASE.get(clase, "rojo")
        debe = v.get("debe") or 0
        # La marca roja es SOLO para la C entregada con saldo (regla 4 de
        # Korto): una C confirmada sin entregar debe su plata, pero no
        # puede decir «Entregado».
        v["marca_debe"] = (f"Entregado, debe {_dinero(debe)}"
                           if clase == "C" and debe > 0
                           and v.get("entregado_odoo") else None)
        # Una venta HISTÓRICA (vieja, registrada tarde el 1/10) lleva su
        # chip gris aparte, pero NUNCA cambia el tono de su clase: una D
        # histórica sigue verde — no es una advertencia de hoy. Default
        # False para no romper con un informe que aún no traiga el campo.
        v["historica"] = bool(v.get("historica") or False)
        listas.append(v)
    return listas


def fuera_texto(fuera):
    """El renglón de fuera de alcance, armado del dict del informe (nunca
    cifras fijas en la plantilla). None si el informe no trae nada."""
    if not fuera or not fuera.get("n"):
        return None
    nombre = str(fuera.get("nombre") or "").strip()
    diario = f"del diario «{nombre}» " if nombre else ""
    return (f"{fuera['n']} facturas {diario}"
            f"({_dinero(fuera.get('total') or 0)}) — fuera de alcance")


# ---------------------------------------------------------------------------
# Las notas de revisión: lo ÚNICO que esta pantalla escribe.
# ---------------------------------------------------------------------------

def iniciar_tablas():
    with _db() as con:
        # Texto libre de quién revisó qué. SIN ninguna columna de estado
        # ni de monto: «pagado» no se puede guardar acá ni por accidente.
        con.execute("""
            CREATE TABLE IF NOT EXISTS revision_nota (
                orden_id TEXT NOT NULL,
                nota TEXT NOT NULL,
                quien TEXT NOT NULL,
                creada_en TEXT NOT NULL
            )
        """)


def guardar_nota(orden_id, nota, quien):
    """Guarda la nota; devuelve el error en palabras (o '' si quedó)."""
    orden_id = (orden_id or "").strip()
    nota = (nota or "").strip()
    if not orden_id:
        return "No llegó a qué venta va la nota."
    if not nota:
        return "La nota está vacía: escribí qué se revisó."
    iniciar_tablas()
    with _db() as con:
        con.execute(
            "INSERT INTO revision_nota (orden_id, nota, quien, creada_en) "
            "VALUES (?,?,?,?)",
            (orden_id, nota[:2000], quien,
             datetime.now(ZONA_PANAMA).isoformat()))
    return ""


def notas_de(orden_id):
    """Las notas de una venta, de la más vieja a la más nueva (el hilo se
    lee hacia abajo, como la conversación de la ficha)."""
    iniciar_tablas()
    with _db() as con:
        filas = con.execute(
            "SELECT orden_id, nota, quien, creada_en FROM revision_nota "
            "WHERE orden_id=? ORDER BY creada_en, rowid",
            (orden_id,)).fetchall()
    return [dict(f) for f in filas]
