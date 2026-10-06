"""El punto único de escritura de stock (review del Arquitecto, 5/10/2026).

- TODO cambio de cantidad pasa por stock_escritura.escribir_stock y deja
  su fila en stock_cambio (ajuste rápido, alta con cantidad, conteo
  quincenal; la vista plana se prueba en test_stock_plano.py);
- el «antes» registrado es el que Odoo devolvió AL ESCRIBIR (`anterior`),
  jamás la `esperada` del formulario;
- en_epoch es epoch UTC (entero, de un datetime aware);
- si el INSERT local falla, Odoo ya quedó escrito y la pantalla LO DICE
  (error ruidoso), nunca silencio;
- nadie en main.py llama a datos.ajustar_en_odoo directo (vigilado
  leyendo el código: un camino nuevo no puede saltarse la bitácora).
"""

import pathlib
import time

import pytest

from app import datos, stock_escritura


def _cambios():
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT sku, antes, despues, tipo_operacion, por, en_epoch "
            "FROM stock_cambio ORDER BY n")]


@pytest.fixture
def odoo_con_otro_antes(monkeypatch):
    """Odoo responde 'aplicado' con un `anterior` DISTINTO de la esperada
    del formulario: lo que debe quedar en la bitácora es el de Odoo."""
    def ajustar(ajustes, empleado, motivo):
        return {"ok": True, "resultados": [
            {"sku": a["sku"], "cantidad": a["cantidad"],
             "resultado": "aplicado", "anterior": 55}
            for a in ajustes
        ]}
    monkeypatch.setattr(datos, "ajustar_en_odoo", ajustar)


# ---------------------------------------------------------------------------
# La función
# ---------------------------------------------------------------------------

def test_el_antes_viene_de_odoo_nunca_del_formulario(db_limpia, odoo_con_otro_antes):
    antes = int(time.time())
    r = stock_escritura.escribir_stock(
        [{"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}], "omar",
        "conteo_inventario")
    assert r["registro_fallo"] == []
    (fila,) = _cambios()
    assert fila["sku"] == "PL-ROMERO"
    assert fila["antes"] == 55          # lo que Odoo tenía al escribir
    assert fila["antes"] != 2           # jamás la esperada del hidden
    assert fila["despues"] == 7
    assert fila["tipo_operacion"] == "conteo_inventario"
    assert fila["por"] == "omar"
    # epoch UTC-aware: un entero en segundos, del reloj de ahora.
    assert isinstance(fila["en_epoch"], int)
    assert antes <= fila["en_epoch"] <= int(time.time()) + 1


def test_solo_lo_aplicado_deja_fila(db_limpia, monkeypatch):
    def ajustar(ajustes, empleado, motivo):
        return {"ok": True, "resultados": [
            {"sku": "PL-A", "cantidad": 3, "resultado": "aplicado", "anterior": 1},
            {"sku": "PL-B", "cantidad": 3, "resultado": "sin_cambio", "anterior": 3},
            {"sku": "PL-C", "cantidad": 3, "resultado": "conflicto", "anterior": 9},
            {"sku": "PL-D", "cantidad": 3, "resultado": "no_existe"},
        ]}
    monkeypatch.setattr(datos, "ajustar_en_odoo", ajustar)
    r = stock_escritura.escribir_stock(
        [{"sku": s, "cantidad": 3, "esperada": 1} for s in
         ("PL-A", "PL-B", "PL-C", "PL-D")], "omar", "conteo_quincenal")
    assert r["registro_fallo"] == []
    assert [f["sku"] for f in _cambios()] == ["PL-A"]


def test_insert_fallido_no_se_calla(db_limpia, odoo_con_otro_antes, monkeypatch):
    def revienta(*args, **kwargs):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(stock_escritura, "_registrar", revienta)
    r = stock_escritura.escribir_stock(
        [{"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}], "omar",
        "ajuste_rapido")
    # Odoo quedó escrito (aplicado) y el fallo del registro SE DEVUELVE.
    assert r["resultados"][0]["resultado"] == "aplicado"
    assert r["registro_fallo"] == ["PL-ROMERO"]


# ---------------------------------------------------------------------------
# Los caminos de la app: cada uno deja su fila (auditoría atómica)
# ---------------------------------------------------------------------------

def test_ajuste_rapido_deja_bitacora(cliente, con_inventario, ajustes_registrados):
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7,
                                       "esperada": 2})
    assert r.status_code == 200 and r.json()["resultado"] == "aplicado"
    assert "aviso" not in r.json()  # sin fallo no hay ruido
    (fila,) = _cambios()
    assert (fila["sku"], fila["despues"], fila["tipo_operacion"], fila["por"]) \
        == ("PL-ROMERO", 7, "ajuste_rapido", "genesis")


def test_ajuste_rapido_avisa_si_la_bitacora_fallo(cliente, con_inventario,
                                                  ajustes_registrados,
                                                  monkeypatch):
    def revienta(*args, **kwargs):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(stock_escritura, "_registrar", revienta)
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7,
                                       "esperada": 2})
    assert r.status_code == 200
    assert r.json()["resultado"] == "aplicado"  # Odoo sí quedó
    assert r.json()["aviso"] == stock_escritura.AVISO_REGISTRO


def test_alta_de_planta_con_cantidad_deja_bitacora(cliente, con_inventario,
                                                   ajustes_registrados,
                                                   monkeypatch):
    monkeypatch.setattr(datos, "crear_planta_en_odoo",
                        lambda *a, **k: {"id": 321})
    r = cliente.post("/productos/nuevo", json={
        "nombre": "Lavanda", "sku": "PL-LAVANDA", "categoria": "Exterior",
        "precioCentavos": 500, "cantidad": 6})
    assert r.status_code == 200 and r.json()["stock"] == "aplicado"
    assert r.json()["registroAviso"] == ""
    (fila,) = _cambios()
    assert (fila["sku"], fila["antes"], fila["despues"],
            fila["tipo_operacion"]) == ("PL-LAVANDA", 0, 6, "alta_de_planta")


def test_conteo_quincenal_deja_bitacora(cliente, con_inventario,
                                        ajustes_registrados):
    n = datos.crear_conteo("quincenal", "pendiente", "genesis", {
        "diferencias": [{"sku": "PL-ROMERO", "nombre": "Romero",
                         "en_sistema": 2, "contado": 9}],
        "sin_contar": 0, "contados": 4})
    r = cliente.post(f"/conteos/{n}/confirmar", follow_redirects=False)
    assert r.status_code == 303
    (fila,) = _cambios()
    assert (fila["sku"], fila["despues"], fila["tipo_operacion"]) \
        == ("PL-ROMERO", 9, "conteo_quincenal")


def test_conteo_confirmado_con_bitacora_caida_lo_dice(cliente, con_inventario,
                                                      ajustes_registrados,
                                                      monkeypatch):
    def revienta(*args, **kwargs):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(stock_escritura, "_registrar", revienta)
    n = datos.crear_conteo("quincenal", "pendiente", "genesis", {
        "diferencias": [{"sku": "PL-ROMERO", "nombre": "Romero",
                         "en_sistema": 2, "contado": 9}],
        "sin_contar": 0, "contados": 4})
    r = cliente.post(f"/conteos/{n}/confirmar")
    # El conteo SÍ quedó confirmado (Odoo se escribió)…
    assert datos.conteo(n)["estado"] == "confirmado"
    # …y la pantalla dice, ruidosamente, que la bitácora no.
    assert stock_escritura.AVISO_REGISTRO in r.text
    assert "PL-ROMERO" in r.text


def test_nadie_llama_al_order_api_por_fuera_del_punto_unico():
    """Un camino nuevo de cantidad no puede saltarse la bitácora: en toda
    la app, datos.ajustar_en_odoo solo lo llama stock_escritura."""
    raiz = pathlib.Path(__file__).resolve().parents[1] / "app"
    for archivo in raiz.rglob("*.py"):
        if archivo.name in ("stock_escritura.py", "datos.py"):
            continue
        assert "ajustar_en_odoo(" not in archivo.read_text(), (
            f"{archivo.name} escribe stock por fuera de "
            "stock_escritura.escribir_stock")


# ---------------------------------------------------------------------------
# El parser de la cantidad contada (regla del lote de formularios)
# ---------------------------------------------------------------------------

def test_cantidad_contada_acepta_enteros_y_el_cero():
    assert stock_escritura.cantidad_contada("7") == (7, "")
    assert stock_escritura.cantidad_contada(" 0 ") == (0, "")


@pytest.mark.parametrize("crudo", ["", "  ", "abc", "-3", "2.5", "2,5",
                                   "+4", "7 plantas", "1234567"])
def test_cantidad_contada_rechaza_lo_ilegible_con_aviso(crudo):
    cantidad, error = stock_escritura.cantidad_contada(crudo)
    assert cantidad is None and error  # jamás un 0 en silencio
