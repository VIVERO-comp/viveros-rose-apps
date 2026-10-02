"""La regla 5 de formularios en todo el lote (2/10/2026): el error de
validación sale DEBAJO del campo que falló, la pantalla aterriza en ese
campo (autofocus, sin una línea de JS) y lo escrito no se pierde.

El patrón, decidido en Python: el validador levanta
`ventas.ErrorDeCampo(mensaje, campo)` (un ValueError con el name= del
input; un campo repetido lleva su índice pegado, «servicio_monto-2»), la
ruta pone `campo_error` en el contexto, y la plantilla incluye
`_error_campo.html` debajo del campo y le pone autofocus +
aria-invalid. El banner de arriba queda SOLO para errores sin campo
(Odoo caído).

Ninguna prueba sale a la red: `ventas._ejecutar` es un doble que revienta
si alguien lo llama — los errores de validación tienen que rebotar ANTES
de hablar con Odoo."""

import pytest

from app import cotizaciones, ventas


@pytest.fixture
def sin_odoo_pero_activo(monkeypatch):
    """Vender encendido (ventas_activo) pero con un Odoo que NO debe
    recibir ni una llamada: todos los casos de acá rebotan en la
    validación."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def nadie_deberia_llamar(*args, **kw):
        raise AssertionError(f"La validación debía rebotar antes de Odoo: {args[:2]}")

    monkeypatch.setattr(ventas, "_ejecutar", nadie_deberia_llamar)


# ---------------------------------------------------------------------------
# Cotizar servicio (Eventos·alquiler, Mantenimiento, Paisajismo, Proyecto)
# ---------------------------------------------------------------------------

def test_servicio_monto_ilegible_sale_debajo_del_renglon(cliente,
                                                         sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje del evento"],
        "servicio_monto": ["12x"],
        "servicio_descripcion": [""],
    })
    assert pagina.status_code == 200
    texto = pagina.text
    assert 'value="12x"' in texto                 # lo escrito no se pierde
    assert 'class="error-campo"' in texto
    assert "no se entiende" in texto
    assert 'aria-invalid="true"' in texto and "autofocus" in texto
    # El banner de arriba NO sale: el error es de un campo.
    assert '<div class="aviso-error">' not in texto


def test_servicio_el_error_marca_el_renglon_correcto(cliente,
                                                     sin_odoo_pero_activo):
    """Dos renglones, el malo es el SEGUNDO: el autofocus y el mensaje van
    con servicio_monto-1, no con el primero."""
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje", "Desmontaje"],
        "servicio_monto": ["100", "abc"],
        "servicio_descripcion": ["", ""],
    }).text
    assert 'value="100"' in pagina and 'value="abc"' in pagina
    assert pagina.count('class="error-campo"') == 1
    # El campo marcado es el del segundo renglón: el aria-invalid aparece
    # DESPUÉS del value del primero.
    assert pagina.index('value="100"') < pagina.index('aria-invalid="true"')


def test_servicio_sin_cliente_aterriza_en_el_campo_cliente(
        cliente, sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "", "celular": "",
        "servicio_texto": ["Montaje"], "servicio_monto": ["100"],
        "servicio_descripcion": [""],
    }).text
    assert "El nombre del cliente es obligatorio." in pagina
    assert 'class="error-campo"' in pagina
    # El autofocus quedó en el input del nombre.
    assert 'id="cliente-nombre"' in pagina
    tramo = pagina[pagina.index('id="cliente-nombre"'):]
    assert "autofocus" in tramo[:400]
    # Y el monto escrito sigue en pantalla.
    assert 'value="100"' in pagina


def test_servicio_cargo_instalacion_ilegible_marca_su_campo(
        cliente, sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje"], "servicio_monto": ["100"],
        "servicio_descripcion": [""],
        "instalacion": "12x",
    }).text
    assert "Instalación" in pagina and "no se entiende" in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="instalacion"'):]
    assert "aria-invalid" in tramo[:400]


def test_servicio_con_odoo_caido_el_banner_de_arriba_sigue(cliente,
                                                           monkeypatch):
    """El error SIN campo (Odoo no aceptó) conserva el banner de siempre."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(*args, **kw):
        raise RuntimeError("Odoo caído")

    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    pagina = cliente.post("/venta/servicio/renta", data={
        "cliente": "Ana", "celular": "",
        "servicio_texto": ["Montaje"], "servicio_monto": ["100"],
        "servicio_descripcion": [""],
    }).text
    assert '<div class="aviso-error">' in pagina
    assert 'class="error-campo"' not in pagina


# ---------------------------------------------------------------------------
# Nueva venta (/venta/cotizar y /venta/vender: el POST redirige, el campo
# viaja en la URL y el GET pinta el error debajo del campo)
# ---------------------------------------------------------------------------

@pytest.fixture
def odoo_vacio(monkeypatch):
    """Vender encendido con un Odoo que contesta vacío: suficiente para
    pintar /venta/nueva sin red y sin tumbar el formulario."""
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "_ejecutar", lambda *a, **k: [])


def test_nueva_venta_cargo_ilegible_redirige_con_su_campo(cliente,
                                                          odoo_vacio):
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Ana", "celular": "", "instalacion": "12x",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert destino.startswith("/venta/nueva?error=")
    assert "campo=instalacion" in destino


def test_nueva_venta_el_error_sale_debajo_del_campo_y_conserva_el_borrador(
        cliente, odoo_vacio):
    """El viaje completo: el beacon ya había guardado el borrador (como en
    la pantalla real), el POST rebota y el GET repinta lo escrito con el
    error debajo de «Instalación» — sin banner arriba. La venta lleva un
    renglón de planta personalizada: sin nada en el carrito la sección de
    cargos ni se pinta."""
    ventas.agregar_renglon_planta("genesis", "Croton de otro vivero", "1", "5")
    cliente.post("/venta/borrador", data={
        "cliente": "Ana", "celular": "6677-8899", "instalacion": "12x"})
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Ana", "celular": "6677-8899", "instalacion": "12x",
    }, follow_redirects=True)
    texto = respuesta.text
    assert 'value="12x"' in texto                # lo tecleado sigue
    assert 'value="Ana"' in texto
    assert 'class="error-campo"' in texto
    assert "Instalación" in texto and "no se entiende" in texto
    tramo = texto[texto.index('name="instalacion"'):]
    assert "aria-invalid" in tramo[:400]
    assert '<div class="aviso-error">' not in texto


def test_guardar_venta_tambien_viaja_con_su_campo(cliente, odoo_vacio):
    respuesta = cliente.post("/venta/vender", data={
        "cliente": "Ana", "celular": "", "mantenimiento": "abc",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "campo=mantenimiento" in respuesta.headers["location"]


def test_nueva_venta_error_sin_campo_conserva_el_banner(cliente, odoo_vacio):
    """«Agrega al menos una planta» no es de un campo: banner de siempre."""
    respuesta = cliente.post("/venta/cotizar", data={
        "cliente": "Ana", "celular": "",
    }, follow_redirects=True)
    texto = respuesta.text
    assert '<div class="aviso-error">' in texto
    assert "Agrega al menos una planta" in texto
    assert 'class="error-campo"' not in texto


# ---------------------------------------------------------------------------
# Cotización personalizada (render directo del POST)
# ---------------------------------------------------------------------------

def test_personalizada_precio_de_renglon_marca_el_renglon(cliente,
                                                          sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Ana", "celular": "",
        "renglon_texto": ["Sacos de tierra", "Piedras"],
        "renglon_cantidad": ["2", "3"],
        "renglon_precio": ["5", "x9"],
        "renglon_descripcion": ["", ""],
    })
    assert pagina.status_code == 200
    texto = pagina.text
    assert 'value="x9"' in texto and 'value="5"' in texto   # nada se pierde
    assert texto.count('class="error-campo"') == 1
    assert "no se entiende" in texto
    # El marcado quedó en el precio del SEGUNDO renglón.
    assert texto.index('value="5"') < texto.index('aria-invalid="true"')
    assert '<div class="aviso-error">' not in texto


def test_personalizada_renglon_sin_titulo_marca_el_texto(cliente,
                                                         sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Ana", "celular": "",
        "renglon_texto": [""],
        "renglon_cantidad": ["2"],
        "renglon_precio": ["5"],
        "renglon_descripcion": [""],
    }).text
    assert "Falta la descripción de un renglón." in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="renglon_texto"'):]
    assert "aria-invalid" in tramo[:400]


def test_personalizada_servicio_sin_monto_marca_su_renglon(
        cliente, sin_odoo_pero_activo):
    pagina = cliente.post("/venta/servicio-personalizada", data={
        "cliente": "Ana", "celular": "",
        "servicios": "1",
        "servicio_texto": ["Diseño del jardín"],
        "servicio_monto": [""],
        "servicio_descripcion": [""],
    }).text
    assert "Falta el monto del servicio" in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="servicio_monto"'):]
    assert "aria-invalid" in tramo[:400]


# ---------------------------------------------------------------------------
# Editar cotización (render directo del POST, como ya hacía al crear)
# ---------------------------------------------------------------------------

from test_cotizaciones import _cotizacion_de_renta  # noqa: E402
from test_cotizaciones import odoo as odoo_servicios  # noqa: E402,F401


def test_editar_cantidad_ilegible_marca_la_planta(cliente, odoo_servicios):
    registro = _cotizacion_de_renta(odoo_servicios)
    pagina = cliente.post(f"/venta/servicio/{registro['n']}/editar", data={
        "servicio_texto": "Alquiler de 20 plantas",
        "servicio_monto": "850", "servicio_descripcion": "",
        "planta_id": "601", "planta_nombre": "CROTO",
        "planta_cantidad": "2x", "planta_precio": "45",
    })
    assert pagina.status_code == 200
    texto = pagina.text
    assert 'value="2x"' in texto                 # lo tecleado sigue
    assert "Cantidad inválida en una planta." in texto
    assert 'class="error-campo"' in texto
    tramo = texto[texto.index('name="planta_cantidad"'):]
    assert "aria-invalid" in tramo[:500]
    assert '<div class="aviso-error">' not in texto


def test_editar_monto_de_servicio_ilegible_marca_su_renglon(cliente,
                                                            odoo_servicios):
    registro = _cotizacion_de_renta(odoo_servicios)
    pagina = cliente.post(f"/venta/servicio/{registro['n']}/editar", data={
        "servicio_texto": "Alquiler de 20 plantas",
        "servicio_monto": "85O", "servicio_descripcion": "",
    }).text
    assert 'value="85O"' in pagina
    assert 'class="error-campo"' in pagina
    tramo = pagina[pagina.index('name="servicio_monto"'):]
    assert "aria-invalid" in tramo[:500]


# ---------------------------------------------------------------------------
# Crear producto (maceta / insumo)
# ---------------------------------------------------------------------------

from test_alta_producto import MACETA  # noqa: E402
from test_alta_producto import odoo as odoo_altas  # noqa: E402,F401


def test_crear_producto_precio_ilegible_marca_su_campo(cliente, odoo_altas):
    pagina = cliente.post("/productos/crear", data={
        "tipo": "maceta", **{**MACETA, "precio": "mucho"}})
    assert pagina.status_code == 400
    texto = pagina.text
    assert 'value="mucho"' in texto                 # lo escrito sigue
    assert 'class="error-campo"' in texto
    tramo = texto[texto.index('name="precio"'):]
    assert "aria-invalid" in tramo[:300]
    assert '<div class="aviso-error">' not in texto
    # revisar también reporta el campo directamente.
    from app import altas
    assert altas.revisar("maceta", {**MACETA, "precio": "mucho"})[2] == "precio"
    assert altas.revisar("maceta", {**MACETA, "costo": "-2"})[2] == "costo"
    assert altas.revisar("insumo", {**MACETA, "unidad": ""})[2] in ("unidad", "nombre")


def test_crear_producto_sin_nombre_enfoca_el_nombre(cliente, odoo_altas):
    pagina = cliente.post("/productos/crear", data={
        "tipo": "maceta", **{**MACETA, "nombre": "🤍"}}).text
    assert "Escribe el nombre del producto." in pagina
    tramo = pagina[pagina.index('name="nombre"'):]
    assert "aria-invalid" in tramo[:300]


# ---------------------------------------------------------------------------
# + Compra (Anotar compra): el POST redirige con el campo y el borrador
# conserva lo escrito
# ---------------------------------------------------------------------------

from app import compras, linear_leads  # noqa: E402


@pytest.fixture
def compras_muestra(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    from app import control
    control.iniciar_tablas()


def test_compra_sin_que_compro_viaja_con_su_campo(cliente, compras_muestra):
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "", "proveedor": "Don Pedro",
    }, follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]
    assert "error=" in destino and "campo=que_compro" in destino
    # Y el GET pinta el error debajo del campo, con el borrador intacto.
    pagina = cliente.get(destino.split("#")[0]).text
    assert "Escribí qué se compra." in pagina
    assert 'class="error-campo"' in pagina
    assert 'value="Don Pedro"' in pagina          # el borrador conserva
    tramo = pagina[pagina.index('name="que_compro"'):]
    assert "aria-invalid" in tramo[:400]
    assert "Ups." not in pagina                   # sin franja arriba


def test_compra_lead_inexistente_marca_el_selector(cliente, compras_muestra):
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Tierra", "lead_ref": "LEAD-999",
    }, follow_redirects=False)
    assert "campo=lead_ref" in respuesta.headers["location"]
