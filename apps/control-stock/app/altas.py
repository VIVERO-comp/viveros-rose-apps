"""El alta de productos: Planta · Maceta · Insumo (dueño, 30/09/2026).

El botón negro de Stock dejó de ser "Crear planta": ahora es "Crear
producto", porque el negocio empieza a vender MACETAS (y ya compraba
INSUMOS). Cada tipo tiene su formulario y su categoría de Odoo, y el paso de
elegir el tipo es un SELECTOR que arranca vacío en «— elegir —» (dueño,
30/09/2026), dentro de un form GET de HTML puro que manda
/productos/crear?tipo=… — nada de JS.

Las PLANTAS no pasan por aquí: siguen por el camino de siempre (el modal de
Stock → POST /productos/nuevo → order-api), con su selector de categoría
Exterior / Interior / Florales. Aquí viven maceta e insumo, que escriben
DIRECTO en Odoo por la puerta XML-RPC de ventas (`ventas._ejecutar`), igual
que vehiculos.py: ni el stock-proxy ni el order-api conocen macetas.

Tres reglas del dueño que este módulo hace cumplir:

1. IMPUESTOS. Un producto creado desde la app nace SIN ITBMS, ni de venta ni
   de compra, y los dos campos van EXPLÍCITAMENTE vacíos en el create: si no
   se mandan, Odoo los llena con el impuesto por defecto de la compañía —
   así nacieron los 123 productos activos que hoy cargan un 7% de compra que
   nadie decidió y que nunca se usó. La casilla «Cobra ITBMS (7%)» nace
   APAGADA y, marcada, pone SOLO el de venta; el de compra queda vacío
   siempre (esa decisión la está viendo el contador del dueño). El impuesto
   se BUSCA, nunca se crea —igual que las etiquetas de Linear—: si no
   aparece, el producto se crea sin él y queda el aviso.
2. CATEGORÍAS POR NOMBRE, nunca por id: «Macetas» es 13 en producción y 12
   en pruebas, «Insumos» 10 en las dos. La categoría que falte apaga su tipo
   con un aviso en la pantalla, no revienta. La categoría «Plantas» existe
   vacía y NO se usa (dueño, 30/09/2026): no se ofrece en ningún formulario.
3. UNA MACETA NACE NO PUBLICADA. `publicado` —el Boolean del addon, NO
   `is_published`— viene con `default=True` en Odoo, así que hay que
   mandarlo en False a propósito; sin eso la maceta nacería lista para la
   tienda. Publicar es una casilla reversible que no borra nada.

Y una tolerancia: los cuatro campos de la maceta (`maceta_material`,
`maceta_diametro_cm`, `maceta_alto_cm`, `maceta_color`) los está agregando
otra tanda al addon y TODAVÍA no existen en el Odoo real. Se pregunta por
ellos con `fields_get`: los que no estén se omiten con un aviso en la
pantalla y la maceta se crea igual, nunca un error. Mismo espíritu que
vehiculos.py con los ganchitos de vehículo.
"""

import time
import unicodedata

from . import datos, ventas

# ---------------------------------------------------------------------------
# Los tres tipos
# ---------------------------------------------------------------------------

# El tipo planta no tiene categoría fija: la elige el empleado en su
# formulario (Exterior / Interior / Florales, datos.CATEGORIAS_PLANTA).
CATEGORIA_DE = {"maceta": "Macetas", "insumo": "Insumos"}

# El prefijo del SKU por tipo. Las plantas son PL- desde siempre (es lo que
# el stock-proxy y la tienda filtran); macetas e insumos estrenan el suyo
# para no colarse en el catálogo del sitio por el solo hecho de existir.
PREFIJO_DE = {"maceta": "MC-", "insumo": "IN-"}

# Los cuatro campos del addon, en el orden de la pantalla.
CAMPOS_MACETA = ("maceta_material", "maceta_diametro_cm", "maceta_alto_cm",
                 "maceta_color")

# (clave, etiqueta). La clave es lo que viaja al Selection del addon; la
# etiqueta es lo que se lee en la pantalla.
MATERIALES = (("fibra", "Fibra"), ("barro", "Barro"),
              ("plastico", "Plástico"), ("cemento", "Cemento"))

# La unidad del insumo se mapea a una unidad de medida que Odoo YA tenga:
# esta app no crea unidades. `nombres` son los nombres plausibles en Odoo
# (español e inglés); se comparan sin tildes ni mayúsculas.
#
# SOLO van las que Odoo tiene de verdad. "Saco" estaba y se QUITÓ (dueño,
# 01/10/2026: «saco no lo pongas, ponlo como insumo y listo»): en este Odoo
# no existe esa unidad de medida, así que un insumo por saco se creaba con la
# unidad por defecto y un aviso. Se le ofreció crearla en Odoo y dijo que no.
# Litro cae en "L" (id 13 en el Odoo real) y unidad en "Unidades" (id 1).
# Para sumar una unidad nueva: primero se crea en Odoo (a mano, con su OK) y
# después entra acá con sus nombres plausibles.
UNIDADES = (
    {"clave": "litro", "etiqueta": "Litro",
     "nombres": ("L", "Litro", "Litros", "Liter", "Litre", "Liters")},
    {"clave": "unidad", "etiqueta": "Unidad",
     "nombres": ("Unidades", "Unidad", "Units", "Unit", "Unit(s)", "Uds")},
)

# Los avisos viajan como CÓDIGOS en la URL del redirect (?aviso=…) y el texto
# vive aquí, en Python: así la pantalla no arma frases y la URL no lleva
# párrafos.
TEXTO_AVISO = {
    "sin_campos_maceta":
        "Este Odoo todavía no tiene los datos de la maceta (material, "
        "diámetro, alto y color): se guardó todo lo demás. Cuando el addon "
        "los traiga, se completan en la ficha de Odoo.",
    "sin_itbms":
        "No se encontró el ITBMS del 7% de venta en Odoo, así que el "
        "producto quedó SIN impuesto. Avísale a Abraham.",
    "sin_unidad":
        "La unidad elegida no existe en Odoo: el insumo quedó con la unidad "
        "por defecto. Se cambia en la ficha de Odoo.",
    "sin_publicado":
        "Este Odoo no tiene la casilla «Publicada en la tienda», así que no "
        "se pudo marcar como NO publicada. Revísalo en Odoo.",
}


def texto_de_avisos(codigos):
    """Los textos de esos códigos, en orden y sin repetir; lo que no
    conozca se ignora (una URL manoseada no pinta frases raras)."""
    vistos, textos = set(), []
    for codigo in codigos or ():
        if codigo in TEXTO_AVISO and codigo not in vistos:
            vistos.add(codigo)
            textos.append(TEXTO_AVISO[codigo])
    return textos


# ---------------------------------------------------------------------------
# Lo que hay que preguntarle a Odoo (cacheado, tolerante)
# ---------------------------------------------------------------------------

TTL_ODOO = 300  # segundos; mismo espíritu que el TTL de vehiculos.py

# Los campos opcionales que se consultan de una sola vez con fields_get: los
# cuatro de la maceta, la casilla del addon y las unidades de medida.
CAMPOS_CONSULTADOS = CAMPOS_MACETA + ("publicado", "uom_id", "uom_po_id")

_cache = {}


def reiniciar_cache():
    """Solo para pruebas (y tras crear, para que la próxima lea fresco)."""
    _cache.clear()


def _cacheado(clave, leer):
    """El valor guardado si está vigente; si no, lo lee y lo guarda.

    A diferencia del inventario, aquí NO se refresca por detrás: son
    consultas de una pantalla que se abre de vez en cuando, y un valor
    recién leído es más importante que ahorrarse el viaje.

    Un fallo (None) NO se guarda: un hipo de Odoo dejaría los dos tipos
    apagados cinco minutos aunque ya hubiera vuelto.
    """
    entrada = _cache.get(clave)
    if entrada and time.time() - entrada["en"] < TTL_ODOO:
        return entrada["valor"]
    valor = leer()
    if valor is not None:
        _cache[clave] = {"valor": valor, "en": time.time()}
    return valor


def metadatos():
    """{campo: {type, selection}} de los CAMPOS_CONSULTADOS que Odoo tenga.

    {} = este Odoo no tiene ninguno (addon viejo). None = no se pudo
    preguntar (sin Odoo configurado o Odoo caído): quien llama decide, y en
    general "no sé" se trata como "mándalo igual" para los campos que se dan
    por ciertos en producción, y como "omitir" para los que no.
    """
    if not ventas.configurado():
        return None
    return _cacheado("metadatos", _leer_metadatos)


def _leer_metadatos():
    """fields_get pide SOLO esos nombres y Odoo devuelve los que existen:
    el que no esté simplemente no aparece en la respuesta."""
    try:
        return ventas._ejecutar(
            "product.template", "fields_get",
            [list(CAMPOS_CONSULTADOS)],
            {"attributes": ["type", "selection", "string"]}) or {}
    except Exception as excepcion:
        print(f"altas: no se pudieron leer los campos de product.template: "
              f"{excepcion!r}", flush=True)
        return None


def _manda(metas, campo):
    """¿Se manda este campo en el create? Sí cuando Odoo dice que existe, y
    también cuando no se pudo preguntar (mejor intentarlo que nacer con el
    dato equivocado). Solo se omite si Odoo dijo claramente que NO está."""
    return metas is None or campo in metas


def categorias_de_producto():
    """{nombre: id} de las categorías de los tipos, buscadas por NOMBRE.

    None cuando no se pudo preguntar. Un nombre que no aparece deja su tipo
    apagado en la pantalla, con su aviso: la categoría se crea en Odoo, no
    desde aquí.
    """
    if not ventas.configurado():
        return None
    return _cacheado("categorias", _leer_categorias)


def _leer_categorias():
    nombres = sorted(set(CATEGORIA_DE.values()))
    try:
        filas = ventas._ejecutar("product.category", "search_read",
                                 [[["name", "in", nombres]]],
                                 {"fields": ["name"]})
    except Exception as excepcion:
        print(f"altas: no se pudieron leer las categorías de Odoo: "
              f"{excepcion!r}", flush=True)
        return None
    # Si hubiera dos con el mismo nombre (una anidada), gana la de id más
    # bajo: la que existía primero.
    mapa = {}
    for fila in sorted(filas, key=lambda f: f["id"]):
        mapa.setdefault(fila["name"], fila["id"])
    return mapa


def id_itbms_venta():
    """El id del ITBMS del 7% de VENTA, o None.

    Se busca; JAMÁS se crea (misma regla que las etiquetas de Linear). Un
    None NO se cachea: el impuesto puede aparecer en Odoo sin que este
    servidor se reinicie.
    """
    if not ventas.configurado():
        return None
    if _cache.get("itbms"):
        return _cache["itbms"]
    try:
        ids = ventas._ejecutar(
            "account.tax", "search",
            [[["type_tax_use", "=", "sale"], ["amount", "=", 7],
              ["amount_type", "=", "percent"], ["active", "=", True]]],
            {"limit": 1, "order": "id"})
    except Exception as excepcion:
        print(f"altas: no se pudo buscar el ITBMS en Odoo: {excepcion!r}",
              flush=True)
        return None
    if not ids:
        print("altas: no hay un account.tax de venta al 7% activo en este "
              "Odoo; el producto se crea sin impuesto", flush=True)
        return None
    _cache["itbms"] = ids[0]
    return ids[0]


def _sin_tildes(texto):
    limpio = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in limpio
                   if unicodedata.category(c) != "Mn").strip().lower()


def id_de_unidad(clave):
    """El id de la unidad de medida de Odoo para litro / unidad, o None.

    Busca entre las que YA existen, comparando el nombre sin tildes ni
    mayúsculas. No crea unidades: sin coincidencia el insumo se queda con la
    que Odoo ponga por defecto y la pantalla lo dice.

    Hoy las dos que ofrece el formulario existen en el Odoo real, así que por
    el camino normal esto no devuelve None. El camino sigue acá igual, y con
    su prueba: es la red de seguridad para el día en que alguien renombre o
    archive una unidad en Odoo — eso NO puede dejar sin crear un insumo.
    """
    if not ventas.configurado():
        return None
    opcion = next((u for u in UNIDADES if u["clave"] == clave), None)
    if not opcion:
        return None
    unidades = _cacheado("unidades", _leer_unidades)
    if not unidades:
        return None
    for nombre in opcion["nombres"]:
        id_unidad = unidades.get(_sin_tildes(nombre))
        if id_unidad:
            return id_unidad
    print(f"altas: en este Odoo no hay una unidad de medida que calce con "
          f"«{clave}»; el insumo queda con la de por defecto", flush=True)
    return None


def _leer_unidades():
    """{nombre sin tildes: id} de todas las unidades de medida (son pocas)."""
    try:
        filas = ventas._ejecutar("uom.uom", "search_read", [[]],
                                 {"fields": ["name"]})
    except Exception as excepcion:
        print(f"altas: no se pudieron leer las unidades de medida: "
              f"{excepcion!r}", flush=True)
        return None
    mapa = {}
    for fila in sorted(filas, key=lambda f: f["id"]):
        mapa.setdefault(_sin_tildes(fila["name"]), fila["id"])
    return mapa


# ---------------------------------------------------------------------------
# La referencia (SKU)
# ---------------------------------------------------------------------------

def sku_de(prefijo, nombre):
    """MC-MACETA-BARRO-30 / IN-ABONO-ORGANICO desde el nombre escrito.

    Misma limpieza que el SKU de las plantas —sin tildes, sin signos, en
    mayúsculas— reusando datos.sku_sugerido y cambiándole el prefijo, para
    que las dos altas armen la referencia con la MISMA regla.
    """
    base = datos.sku_sugerido(nombre)  # "PL-…" o ""
    return (prefijo + base[len("PL-"):]) if base else ""


def sku_libre(prefijo, nombre):
    """El SKU sugerido, o el primero con sufijo -2, -3… que Odoo no tenga.

    Dos macetas pueden llamarse casi igual (mismo nombre, otro color) y
    bloquear el alta por eso sería peor que darle su referencia con sufijo:
    la pantalla dice cuál quedó. Sin Odoo configurado devuelve el sugerido.
    """
    base = sku_de(prefijo, nombre)
    if not base or not ventas.configurado():
        return base
    for intento in range(1, 21):
        candidato = base if intento == 1 else f"{base}-{intento}"
        try:
            repetidos = ventas._ejecutar(
                "product.template", "search",
                [[["default_code", "=", candidato]]],
                {"limit": 1, "context": {"active_test": False}})
        except Exception as excepcion:
            # Sin poder comprobar, se devuelve el sugerido: Odoo no impone
            # referencias únicas, así que en el peor caso quedan dos iguales
            # y se corrige a mano — mejor que no poder crear el producto.
            print(f"altas: no se pudo comprobar si {candidato} ya existe: "
                  f"{excepcion!r}", flush=True)
            return base
        if not repetidos:
            return candidato
    return base


# ---------------------------------------------------------------------------
# Validación del formulario (pura: no toca Odoo)
# ---------------------------------------------------------------------------

def revisar(tipo, form):
    """(limpio, error) del POST crudo del formulario.

    `limpio` es lo que entra a crear(); `error` es la frase que se le muestra
    al empleado con lo que escribió todavía en pantalla.
    """
    if tipo == "planta":
        return None, ("Las plantas se crean desde Stock → Crear producto → "
                      "Planta.")
    if tipo not in CATEGORIA_DE:
        return None, "Ese tipo de producto no existe."
    nombre = " ".join((form.get("nombre") or "").split())
    if not ventas._tiene_letras(nombre):
        # Un nombre sin ni una letra no es un nombre (misma regla que los
        # contactos de WhatsApp): "30" o un emoji no identifican nada.
        return None, "Escribe el nombre del producto."
    if not sku_de(PREFIJO_DE[tipo], nombre):
        # El nombre tiene letras pero ninguna que sirva para armar la
        # referencia (otro alfabeto): un producto sin default_code queda
        # invisible para el stock y para las ventas.
        return None, ("Escribe el nombre con letras y números normales: la "
                      "referencia de Odoo se arma con ellos.")
    precio = ventas._num_positivo(form.get("precio"), defecto=0.0,
                                  permitir_cero=True)
    costo = ventas._num_positivo(form.get("costo"), defecto=0.0,
                                 permitir_cero=True)
    if precio is None or costo is None:
        return None, "Revisa el precio y el costo: van en números, sin signos."
    limpio = {"tipo": tipo, "nombre": nombre[:120], "precio": precio,
              "costo": costo,
              # La casilla nace apagada: un checkbox ausente es "no cobra".
              "itbms": bool(form.get("itbms"))}
    if tipo == "maceta":
        material = (form.get("material") or "").strip()
        if material not in dict(MATERIALES):
            return None, "Elige el material de la maceta."
        diametro = ventas._num_positivo(form.get("diametro"), defecto=0.0,
                                        permitir_cero=True)
        alto = ventas._num_positivo(form.get("alto"), defecto=0.0,
                                    permitir_cero=True)
        if diametro is None or alto is None:
            return None, ("El diámetro y el alto van en centímetros, "
                          "en números.")
        limpio.update({"material": material, "diametro": diametro,
                       "alto": alto,
                       "color": " ".join((form.get("color") or "").split())[:60]})
    else:
        unidad = (form.get("unidad") or "").strip()
        if unidad not in {u["clave"] for u in UNIDADES}:
            return None, "Elige la unidad del insumo."
        limpio["unidad"] = unidad
    return limpio, None


# ---------------------------------------------------------------------------
# El alta en Odoo
# ---------------------------------------------------------------------------

def valores_para_odoo(limpio, categoria_id, metas, avisos):
    """El dict del `create` de product.template. `avisos` se va llenando
    con los códigos de lo que no se pudo guardar.

    Separado de crear() a propósito: así las pruebas miran el dict sin
    simular media Odoo, y se lee de un tirón qué nace con qué.
    """
    valores = {
        "name": limpio["nombre"],
        "default_code": limpio["sku"],
        # Igual que las plantas del catálogo: bien consumible y almacenable
        # (lleva stock), se vende, y factura por pedido.
        "type": "consu",
        "is_storable": True,
        "sale_ok": True,
        "invoice_policy": "order",
        "categ_id": categoria_id,
        "list_price": limpio["precio"],
        "standard_price": limpio["costo"],
        # LOS DOS IMPUESTOS, EXPLÍCITAMENTE VACÍOS. Sin estas dos líneas
        # Odoo los llena con el impuesto por defecto de la compañía; así
        # nacieron los 123 productos que cargan un 7% de compra que nadie
        # decidió. El de compra se queda vacío SIEMPRE.
        "taxes_id": [[6, 0, []]],
        "supplier_taxes_id": [[6, 0, []]],
    }
    if limpio["itbms"]:
        id_impuesto = id_itbms_venta()
        if id_impuesto:
            # Solo el de VENTA, nunca el de compra.
            valores["taxes_id"] = [[6, 0, [id_impuesto]]]
        else:
            avisos.append("sin_itbms")
    # Nace NO publicada (dueño): la casilla del addon trae default=True, así
    # que hay que mandarla en False a propósito.
    if _manda(metas, "publicado"):
        valores["publicado"] = False
    else:
        avisos.append("sin_publicado")
    if limpio["tipo"] == "maceta":
        valores.update(_valores_maceta(limpio, metas, avisos))
    elif limpio["tipo"] == "insumo":
        valores.update(_valores_insumo(limpio, metas, avisos))
    return valores


def _valores_maceta(limpio, metas, avisos):
    """Los cuatro datos de la maceta, solo los que este Odoo tenga."""
    puestos, faltan = {}, False
    crudos = {"maceta_material": limpio["material"],
              "maceta_diametro_cm": limpio["diametro"],
              "maceta_alto_cm": limpio["alto"],
              "maceta_color": limpio["color"]}
    for campo in CAMPOS_MACETA:
        valor = crudos[campo]
        # Un campo que el addon todavía no trajo se OMITE (mandarlo sería un
        # error de Odoo y la maceta no se crearía).
        if metas is None or campo not in metas:
            faltan = True
            continue
        if campo == "maceta_material":
            valor = _clave_de_seleccion(metas[campo], valor)
            if valor is None:
                faltan = True
                continue
        elif valor in ("", 0, 0.0):
            # Diámetro, alto y color son opcionales: un vacío no se escribe
            # (Odoo ya pone 0 / falso por su cuenta).
            continue
        puestos[campo] = valor
    if faltan:
        avisos.append("sin_campos_maceta")
    return puestos


def _clave_de_seleccion(meta, valor):
    """La clave que el Selection de Odoo acepta para `valor`, o None.

    El addon puede definir el material como Selection (lo esperable) o como
    texto libre. Si es Selection se casa por clave y, si no, por etiqueta
    sin tildes: así un addon que use "Plástico" como clave sigue funcionando.
    """
    if meta.get("type") != "selection":
        return valor
    opciones = meta.get("selection") or []
    claves = {str(clave) for clave, _ in opciones}
    if valor in claves:
        return valor
    for clave, etiqueta in opciones:
        if _sin_tildes(etiqueta) == _sin_tildes(_etiqueta_material(valor)):
            return str(clave)
    return None


def _etiqueta_material(clave):
    return dict(MATERIALES).get(clave, clave)


def _valores_insumo(limpio, metas, avisos):
    """La unidad de medida, si Odoo tiene una que calce."""
    id_unidad = id_de_unidad(limpio["unidad"])
    if not id_unidad:
        avisos.append("sin_unidad")
        return {}
    puestos = {}
    for campo in ("uom_id", "uom_po_id"):
        # uom_po_id puede no existir según la versión de Odoo; uom_id sí.
        if metas is not None and campo not in metas:
            continue
        puestos[campo] = id_unidad
    return puestos


def crear(limpio, foto=None):
    """Crea el producto en Odoo. Devuelve {"sku", "id", "avisos"}.

    Levanta datos.SinConexion con la frase que va a leer el empleado cuando
    NO quedó creado — nunca un éxito a medias.
    """
    if not ventas.configurado():
        raise datos.SinConexion(
            "La conexión con Odoo no está configurada en este servidor.")
    nombre_categoria = CATEGORIA_DE[limpio["tipo"]]
    categorias = categorias_de_producto()
    categoria_id = (categorias or {}).get(nombre_categoria)
    if not categoria_id:
        raise datos.SinConexion(
            f"La categoría «{nombre_categoria}» no está en Odoo, así que no "
            f"se puede crear el producto. Créala en Odoo y vuelve.")
    avisos = []
    limpio = {**limpio, "sku": sku_libre(PREFIJO_DE[limpio["tipo"]],
                                         limpio["nombre"])}
    valores = valores_para_odoo(limpio, categoria_id, metadatos(), avisos)
    if foto:
        valores["image_1920"] = foto
    try:
        nuevo = ventas._ejecutar("product.template", "create", [valores])
    except Exception as excepcion:
        print(f"altas: no se pudo crear el producto en Odoo: {excepcion!r}",
              flush=True)
        raise datos.SinConexion(
            "Odoo no aceptó el producto. Vuelve a intentar; si sigue, "
            "avísale a Abraham.")
    if isinstance(nuevo, list):
        nuevo = nuevo[0] if nuevo else 0
    # El producto nuevo tiene que poder aparecer en la lista al volver.
    datos.reiniciar_cache_proxy()
    return {"sku": limpio["sku"], "id": nuevo, "avisos": avisos}


# ---------------------------------------------------------------------------
# La pantalla de elegir el tipo
# ---------------------------------------------------------------------------

def tipos_para_pantalla():
    """Los tres tipos con su estado, EN EL ORDEN DEL SELECTOR.

    Planta va primera porque es lo que se crea todos los días (131 plantas y
    0 macetas en el catálogo al escribir esto); el orden del selector es este
    y cambiarlo es mover estas líneas. Planta está además SIEMPRE lista: es
    el formulario de siempre y no depende de ninguna categoría nueva. Maceta
    e insumo se apagan con su motivo cuando su categoría no está en Odoo (o
    cuando no se le pudo preguntar).

    Nadie trae su URL: el selector manda los tres a /productos/crear?tipo=…
    y lo que pasa con cada uno —el formulario, o la redirección al modal de
    la planta— lo decide la ruta (main.alta_producto). Un formulario HTML no
    puede tener dos destinos sin JavaScript, así que esa regla vive en un
    solo lugar.
    """
    categorias = categorias_de_producto()
    lista = [{
        "clave": "planta",
        "etiqueta": "Planta",
        "detalle": "Exterior, Interior o Florales · la que va a la tienda",
        "listo": True,
        "motivo": "",
    }]
    for clave, titulo, detalle in (
            ("maceta", "Maceta", "Material, diámetro, alto y color"),
            ("insumo", "Insumo", "Por litro o por unidad")):
        nombre_categoria = CATEGORIA_DE[clave]
        if categorias is None:
            motivo = ("No se pudo hablar con Odoo, así que no se puede crear "
                      "por ahora.")
        elif nombre_categoria not in categorias:
            motivo = (f"Falta la categoría «{nombre_categoria}» en Odoo: "
                      f"créala y vuelve.")
        else:
            motivo = ""
        lista.append({"clave": clave, "etiqueta": titulo, "detalle": detalle,
                      "listo": not motivo, "motivo": motivo})
    return lista
