"""El calentamiento de arranque (29/09/2026).

La PRIMERA petición tras levantar el proceso pagaba TODAS las consultas
en línea con las cachés vacías — hasta 21 segundos medidos en producción.
`main.calentar_arranque_en_fondo()` adelanta ese trabajo en un hilo de
fondo al importar el módulo. Lo que estas pruebas amarran:

- se calienta SOLO lo configurado (sin token no hay ni un hilo ni una
  consulta — la regla de que pruebas no puede tocar nada real);
- el calentamiento NO bloquea: la primera petición contesta aunque el
  hilo esté a medias o trancado;
- un fallo de una pieza queda en el log, no frena a las siguientes y no
  tumba el proceso;
- una sola corrida a la vez (la clave de `_en_fondo` no admite dos).
"""

import logging
import threading
import time

from fastapi.testclient import TestClient

from app import calendario, control, crm_twenty, datos, linear_leads, main

VARS_DE_SERVICIOS = ("LINEAR_API_KEY", "TWENTY_API_KEY",
                     "STOCK_PROXY_URL", "STOCK_API_KEY")


def _sin_servicios(monkeypatch):
    for var in VARS_DE_SERVICIOS:
        monkeypatch.delenv(var, raising=False)


def test_sin_credenciales_no_hay_piezas(monkeypatch):
    """Sin token no hay nada que calentar: lista vacía."""
    _sin_servicios(monkeypatch)
    assert main._piezas_de_arranque() == []


def test_sin_credenciales_no_dispara_ni_un_hilo(monkeypatch):
    _sin_servicios(monkeypatch)
    disparos = []
    monkeypatch.setattr(calendario, "_en_fondo",
                        lambda clave, tarea: disparos.append(clave))
    main.calentar_arranque_en_fondo()
    assert disparos == []


def test_calienta_lo_configurado_en_orden(monkeypatch):
    """Con Linear y el proxy configurados (y sin Twenty), se calientan el
    catálogo, los leads, el inventario y los publicados — en ese orden, y
    la lista de leads con `refrescar=True` (la caché acaba de nacer)."""
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.setenv("STOCK_PROXY_URL", "http://proxy.de.prueba")
    monkeypatch.setenv("STOCK_API_KEY", "clave-de-prueba")

    llamadas = []
    monkeypatch.setattr(
        linear_leads, "catalogo",
        lambda refrescar=False: llamadas.append("catalogo") or {})
    monkeypatch.setattr(
        linear_leads, "listar",
        lambda refrescar=False: llamadas.append(
            "leads:" + ("fresco" if refrescar else "cache")) or [])
    monkeypatch.setattr(
        datos, "obtener_inventario",
        lambda refrescar=False: llamadas.append("inventario") or ([], 0))
    monkeypatch.setattr(
        datos, "obtener_publicados",
        lambda: llamadas.append("publicados") or (set(), None))

    main._calentar_piezas(main._piezas_de_arranque())
    assert llamadas == ["catalogo", "leads:fresco", "inventario", "publicados"]


def test_la_espera_solo_se_pide_con_twenty(monkeypatch):
    """La pieza de la espera necesita Twenty además de Linear, y usa el
    mecanismo propio de Control (`refrescar_espera_en_fondo`) con los
    leads recién calentados."""
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.delenv("STOCK_PROXY_URL", raising=False)
    monkeypatch.delenv("STOCK_API_KEY", raising=False)

    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    nombres = [n for n, _ in main._piezas_de_arranque()]
    assert not any("espera" in n for n in nombres)

    monkeypatch.setenv("TWENTY_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(linear_leads, "catalogo", lambda refrescar=False: {})
    monkeypatch.setattr(linear_leads, "listar",
                        lambda refrescar=False: [{"ref": "LEAD-1"}])
    pedidos = []
    monkeypatch.setattr(control, "refrescar_espera_en_fondo",
                        lambda leads: pedidos.append(leads))
    main._calentar_piezas(main._piezas_de_arranque())
    assert pedidos == [[{"ref": "LEAD-1"}]]


def test_un_fallo_queda_en_log_y_no_frena_al_resto(caplog):
    """La pieza rota deja su warning (con cuánto llevaba) y la siguiente
    corre igual; nada revienta hacia afuera."""
    corridas = []

    def rota():
        raise RuntimeError("Linear no contesta")

    with caplog.at_level(logging.INFO, logger="control_stock"):
        main._calentar_piezas([("pieza rota", rota),
                               ("pieza sana", lambda: corridas.append("sana"))])

    assert corridas == ["sana"]
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "pieza rota" in avisos[0].getMessage()
    assert "Linear no contesta" in avisos[0].getMessage()
    exitos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("pieza sana" in r.getMessage() for r in exitos)


def test_publicados_con_error_cuenta_como_fallo(monkeypatch, caplog):
    """`obtener_publicados` no lanza (devuelve el motivo): el calentamiento
    lo convierte en fallo para que el log no diga «calentado» en falso."""
    monkeypatch.setattr(datos, "obtener_publicados",
                        lambda: (None, "el sitio no responde"))
    with caplog.at_level(logging.INFO, logger="control_stock"):
        main._calentar_piezas([("catálogo publicado del sitio",
                                main._calentar_publicados)])
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "el sitio no responde" in avisos[0].getMessage()


def test_no_bloquea_y_es_una_sola_corrida(monkeypatch, db_limpia):
    """El calentamiento corre en su hilo: mientras está trancado, una
    petición contesta igual, y una segunda llamada NO abre otra corrida
    (misma clave de `_en_fondo`)."""
    arranco = threading.Event()
    suelta = threading.Event()
    corridas = []

    def pieza_lenta():
        corridas.append(1)
        arranco.set()
        suelta.wait(timeout=10)

    monkeypatch.setattr(main, "_piezas_de_arranque",
                        lambda: [("pieza lenta", pieza_lenta)])
    try:
        main.calentar_arranque_en_fondo()
        assert arranco.wait(timeout=10)  # el hilo de fondo sí arrancó

        # Con el calentamiento a medias (trancado), la app contesta:
        respuesta = TestClient(main.app).get("/login")
        assert respuesta.status_code == 200

        # Y una segunda llamada no duplica la corrida.
        main.calentar_arranque_en_fondo()
    finally:
        suelta.set()

    # Esperar a que el hilo termine, para no dejarle residuo a otra prueba.
    limite = time.time() + 10
    while "arranque" in calendario._refrescos_lista and time.time() < limite:
        time.sleep(0.01)
    assert "arranque" not in calendario._refrescos_lista
    assert corridas == [1]
