"""Cálculos puros de una orden: la regla de oro es devuelto = enviado − aceptado.

El empleado nunca escribe la devolución: solo ajusta lo aceptado, acotado a
0 ≤ aceptado ≤ enviado, y todo lo demás se deriva de ahí.
"""


def acotar_aceptado(aceptado, enviado):
    """Acota la cantidad aceptada al rango [0, enviado]."""
    return max(0, min(int(aceptado), int(enviado)))


def calcular_orden(lineas, aceptado):
    """Totales y diferencias de una orden.

    lineas: [{sku, nombre, precio, enviado}]
    aceptado: {sku: cantidad aceptada}; un SKU ausente cuenta como todo aceptado.
    """
    enviadas = 0
    aceptadas = 0
    total_original = 0.0
    total_aceptado = 0.0
    diferencias = []
    for linea in lineas:
        cantidad = acotar_aceptado(aceptado.get(linea["sku"], linea["enviado"]), linea["enviado"])
        enviadas += linea["enviado"]
        aceptadas += cantidad
        total_original += linea["enviado"] * linea["precio"]
        total_aceptado += cantidad * linea["precio"]
        if cantidad != linea["enviado"]:
            diferencias.append(
                {**linea, "aceptado": cantidad, "devuelto": linea["enviado"] - cantidad}
            )
    return {
        "env": enviadas,
        "acep": aceptadas,
        "dev": enviadas - aceptadas,
        "t_orig": round(total_original, 2),
        "t_acep": round(total_aceptado, 2),
        "t_dev": round(total_original - total_aceptado, 2),
        "dif": diferencias,
    }


def dinero(monto):
    """Formato de moneda de la app: B/.116.99."""
    return f"B/.{monto:.2f}"


def unidades(cantidad):
    """'1 unidad' / '3 unidades', como el prototipo."""
    return f"{cantidad} unidad{'' if cantidad == 1 else 'es'}"
