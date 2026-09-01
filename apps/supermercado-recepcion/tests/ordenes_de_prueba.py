"""Órdenes falsas para los tests, con la forma que entrega datos.obtener_ordenes().

Replican las tres facturas del prototipo (la S00774 suma B/.116.99) para que
las verificaciones de montos sigan ancladas al ejemplo real de la spec.
"""

import copy

from app.datos import POR_SKU


def _linea(sku, enviado):
    p = POR_SKU[sku]
    return {"sku": sku, "nombre": p["nombre"], "precio": p["precio"], "enviado": enviado}


_ORDENES = [
    {
        "pedido": "S00774", "refSuper": "FAC-774", "odooId": 10774,
        "cliente": "Super Xtra", "sucursal": "Super Xtra Villalobos",
        "sucursalRef": "CL-0032", "fecha": "2026-06-18",
        "lineas": [_linea("VR-001", 2), _linea("VR-002", 3), _linea("VR-003", 2),
                   _linea("VR-004", 3), _linea("VR-005", 6), _linea("VR-006", 4),
                   _linea("VR-007", 4), _linea("VR-008", 4), _linea("VR-009", 3),
                   _linea("VR-010", 2), _linea("VR-011", 2), _linea("VR-012", 4),
                   _linea("VR-013", 2), _linea("VR-014", 3), _linea("VR-015", 4),
                   _linea("VR-016", 2)],
    },
    {
        "pedido": "S00781", "refSuper": None, "odooId": 10781,
        "cliente": "Riba Smith", "sucursal": "Riba Smith Bella Vista",
        "sucursalRef": None, "fecha": "2026-06-18",
        "lineas": [_linea("VR-007", 4), _linea("VR-012", 3), _linea("VR-008", 4)],
    },
    {
        "pedido": "S00770", "refSuper": None, "odooId": 10770,
        "cliente": "Super 99", "sucursal": "Super 99 Costa del Este",
        "sucursalRef": None, "fecha": "2026-06-17",
        "lineas": [_linea("VR-017", 3), _linea("VR-018", 2), _linea("VR-015", 3)],
    },
]


def ordenes_falsas():
    return copy.deepcopy(_ORDENES)


def orden_774():
    return copy.deepcopy(_ORDENES[0])
