"""Ayudas compartidas de las pruebas."""

import io

from openpyxl import Workbook

from app.conteos import ENCABEZADOS


def xlsx_de_conteo(filas):
    """Un .xlsx como el que sube la empleada: (sku, conteo) por fila. El
    nombre/categoría/en-sistema van vacíos a propósito: el import solo confía
    en el SKU y el conteo."""
    libro = Workbook()
    hoja = libro.active
    hoja.append(ENCABEZADOS)
    for sku, conteo in filas:
        hoja.append([sku, None, None, None, conteo])
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
