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
