"""Los vehículos de entrega de la planta (Odoo, product.template).

El addon de Odoo ganó el 29/09/2026 tres Boolean en `product.template` —
`viaja_moto`, `viaja_carro`, `viaja_pickup`: en qué vehículos puede viajar
la planta para la entrega de la tienda en línea. En Odoo esa sección solo
se ve si el producto está publicado; la ficha de la app repite la misma
regla (para_planta devuelve None si no está publicada, y la sección no se
pinta).

La lectura y la escritura van DIRECTO a Odoo por la puerta XML-RPC de
ventas (`_ejecutar`), no por el stock-proxy ni el order-api: ninguno de
los dos conoce los campos nuevos. Tolerante a un Odoo viejo: si los campos
no existen todavía (el de pruebas puede llevar el addon anterior), leer()
devuelve None, la sección no se pinta y nada revienta.

El guardado tiene su candado (decidir_guardado): el POST de la ficha solo
escribe los tres Boolean si el formulario trae el marcador escondido
`tiene_vehiculo` — el mismo patrón que `casillas` en Vender. Sin marcador,
la sección no se mostró (producto sin publicar, Odoo viejo, o un borrador
de antes del cambio) y escribir False sobre lo que Odoo tenga sería pisar
datos con un formulario que nunca los enseñó.
"""

import time

from . import datos, ventas

# Los nombres de los campos en Odoo, en el orden de la pantalla.
CAMPOS = ("viaja_moto", "viaja_carro", "viaja_pickup")

# Cómo viaja cada campo dentro de la planta del datos_json (llaves cortas,
# como hmin/hmax): {"moto": bool, "carro": bool, "pickup": bool}.
_A_PLANTA = {"viaja_moto": "moto", "viaja_carro": "carro",
             "viaja_pickup": "pickup"}

TTL_VEHICULOS = 300  # segundos; mismo espíritu que el TTL del inventario

_cache = {}  # {"mapa": {"valor": dict|None, "en": epoch}}


def reiniciar_cache():
    """Solo para pruebas (y tras una escritura, para releer fresco)."""
    _cache.clear()


def a_planta(valores):
    """{viaja_moto: b, ...} de Odoo → {moto: b, carro: b, pickup: b}."""
    return {_A_PLANTA[campo]: bool(valores.get(campo)) for campo in CAMPOS}


def leer():
    """{sku: {moto, carro, pickup}} para todo el catálogo, o None.

    None = no se pudo saber (sin Odoo configurado, Odoo caído, o un Odoo
    viejo sin los campos): la ficha entonces no pinta la sección. Con un
    valor guardado ya viejo se sirve al instante y se renueva por detrás,
    igual que el inventario (pedido del dueño: nada espera a Odoo).
    """
    if not ventas.configurado():
        return None
    entrada = _cache.get("mapa")
    if entrada:
        if time.time() - entrada["en"] >= TTL_VEHICULOS:
            datos._en_fondo("vehiculos", _leer)
        return entrada["valor"]
    try:
        return _leer()
    except Exception:
        return None


def _leer():
    """La lectura real (bloqueante) a Odoo; actualiza el caché.

    Si Odoo no tiene los campos (addon viejo) el search_read revienta con
    un Fault: se guarda None con su TTL para no insistir en cada pintada.
    """
    try:
        filas = ventas._ejecutar(
            "product.template", "search_read",
            [[["default_code", "!=", False]]],
            {"fields": ["default_code", *CAMPOS]})
        mapa = {fila["default_code"]: a_planta(fila) for fila in filas}
    except Exception as excepcion:
        print(f"vehiculos: no se pudieron leer de Odoo: {excepcion!r}",
              flush=True)
        mapa = None
    _cache["mapa"] = {"valor": mapa, "en": time.time()}
    return mapa


def para_planta(sku, publicado, mapa):
    """Lo que viaja en la planta del datos_json: el dict o None.

    None cuando el producto no está publicado (la sección no aplica, igual
    que en Odoo) o cuando no se pudo leer de Odoo: en los dos casos la
    ficha no pinta la sección.
    """
    if not publicado or not mapa:
        return None
    return mapa.get(sku)


def publicado_de(sku):
    """True/False según el inventario; None si no se pudo saber."""
    try:
        inventario, _ = datos.obtener_inventario()
    except datos.SinConexion:
        return None
    for producto in inventario:
        if producto["sku"] == sku:
            return bool(producto.get("publicado", True))
    return None


def decidir_guardado(sku, crudo):
    """Qué escribir en Odoo, o None si este POST no toca los vehículos.

    - Sin el marcador `tiene_vehiculo`: None. El formulario vino sin la
      sección (no se pintó) y un checkbox ausente ahí no significa
      «desmarcado», significa «no preguntado».
    - Con marcador pero el producto NO publicado según el inventario:
      None. La sección no debió mostrarse; no se pisa nada.
    - Publicado desconocido (inventario caído): se confía en el marcador —
      solo viaja si el servidor pintó la sección, y eso exigió publicado.
    - Checkbox ausente en el cuerpo = False (el clásico de los checkboxes).
    """
    if not crudo.get("tiene_vehiculo"):
        return None
    if publicado_de(sku) is False:
        return None
    return {campo: bool(crudo.get(campo)) for campo in CAMPOS}


def fijar_en_odoo(sku, valores):
    """Escribe los tres Boolean en el product.template del sku.

    Sin Odoo configurado (desarrollo) se simula el éxito, igual que la
    altura. Un sku que no está en Odoo o un fallo de red suben como
    SinConexion: quien guarda tiene que enterarse de que no quedó.
    """
    if not ventas.configurado():
        return {"ok": True, "sku": sku, "resultado": "aplicado"}
    try:
        ids = ventas._ejecutar("product.template", "search",
                               [[["default_code", "=", sku]]], {"limit": 1})
        if not ids:
            raise datos.SinConexion(f"El producto {sku} no está en Odoo.")
        ventas._ejecutar("product.template", "write",
                         [ids, {campo: bool(valores.get(campo))
                                for campo in CAMPOS}])
    except datos.SinConexion:
        raise
    except Exception as excepcion:
        print(f"vehiculos: error guardando {sku} en Odoo: {excepcion!r}",
              flush=True)
        raise datos.SinConexion(
            "No se pudieron guardar los vehículos en Odoo. Intenta de nuevo.")
    # Cambió en Odoo: la próxima pintada va fresca.
    reiniciar_cache()
    return {"ok": True, "sku": sku, "resultado": "aplicado"}
