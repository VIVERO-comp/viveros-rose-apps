"""Sucursales y catálogo vía stock-proxy, con su degradación en cadena."""

import pytest
from app import datos

RESPUESTA_SUCURSALES = {
    "clients": [{
        "ref": "SUPER-EXTRA", "name": "Super Extra",
        "branches": [
            {"ref": "CL-0001", "name": "Super Extra Arraiján"},
            {"ref": "CL-0032", "name": "Super Extra Villa Lobos"},
        ],
    }],
    "stale": False, "as_of": "2026-09-01T00:00:00+00:00",
}
RESPUESTA_ENTREGAS = {
    "orders": [{
        "id": 55, "name": "S00901", "customer_ref": "FAC-901", "date": "2026-09-01",
        "client": {"ref": "SUPER-EXTRA", "name": "Super Extra"},
        "branch": {"ref": "CL-0001", "name": "Super Extra Arraiján"},
        "lines": [
            {"sku": "PL-MENTA-01", "name": "MENTA", "unit_price_cents": 300, "qty": 4},
            {"sku": "PL-JADE-01", "name": "JADE", "unit_price_cents": 550, "qty": 0},
        ],
    }],
    "stale": False, "as_of": "2026-09-01T00:00:00+00:00",
}
RESPUESTA_CATALOGO = {
    "items": [
        {"sku": "PLT-MENTA-01", "name": "MENTA", "price_cents": 300, "available": 14},
        {"sku": "PLT-JADE-01", "name": "JADE", "price_cents": 550, "available": 0},
    ],
    "stale": False, "as_of": "2026-09-01T00:00:00+00:00",
}


@pytest.fixture
def proxy_configurado(monkeypatch):
    monkeypatch.setenv("STOCK_PROXY_URL", "https://stock.ejemplo/v1")
    monkeypatch.setenv("STOCK_API_KEY", "clave-de-prueba")
    datos.reiniciar_cache_proxy()
    estado = {"caido": False, "llamadas": 0}

    def pedir(recurso):
        if estado["caido"]:
            raise RuntimeError("proxy caido")
        estado["llamadas"] += 1
        return {"sucursales": RESPUESTA_SUCURSALES, "catalogo": RESPUESTA_CATALOGO,
                "entregas": RESPUESTA_ENTREGAS}[recurso]

    monkeypatch.setattr(datos, "_pedir_al_proxy", pedir)
    yield estado
    datos.reiniciar_cache_proxy()


def test_sin_configuracion_usa_datos_de_prueba(cliente, monkeypatch):
    monkeypatch.delenv("STOCK_PROXY_URL", raising=False)
    datos.reiniciar_cache_proxy()
    clientes = datos.obtener_clientes_supermercado()
    assert [c["nombre"] for c in clientes] == [
        "Super Xtra", "Riba Smith", "Super 99", "Supermercados Rey",
    ]
    assert all(p["disponible"] is None for p in datos.obtener_catalogo())
    # Y la app entera arranca igual.
    assert "¿En qué súper estás?" in cliente.get("/intercambios/nuevo").text


def test_con_proxy_llegan_sucursales_reales(cliente, proxy_configurado):
    r = cliente.get("/intercambios/nuevo")
    assert "Super Extra" in r.text and "Supermercados Rey" not in r.text

    r = cliente.get("/intercambios/nuevo/sucursales", params={"cliente": "SUPER-EXTRA"})
    assert "¿En qué sucursal?" in r.text
    # El nombre se muestra sin el prefijo del cliente, con su código CL.
    assert ">Villa Lobos<" in r.text and "CL-0032" in r.text


def test_con_proxy_el_catalogo_trae_disponible(cliente, proxy_configurado):
    catalogo = datos.obtener_catalogo()
    menta = next(p for p in catalogo if p["sku"] == "PLT-MENTA-01")
    assert menta == {"sku": "PLT-MENTA-01", "nombre": "MENTA", "precio": 3.0, "disponible": 14}
    # La página de plantas embebe ese catálogo para la búsqueda local.
    r = cliente.get("/intercambios/nuevo/plantas",
                         params={"cliente": "Super Extra", "sucursal": "Super Extra Arraiján"})
    assert "PLT-MENTA-01" in r.text and '"disponible": 14' in r.text


def test_proxy_caido_sirve_ultimo_valor_bueno(cliente, proxy_configurado, monkeypatch):
    monkeypatch.setattr(datos, "TTL_SUCURSALES", 0)
    assert datos.obtener_clientes_supermercado()[0]["nombre"] == "Super Extra"
    proxy_configurado["caido"] = True
    assert datos.obtener_clientes_supermercado()[0]["nombre"] == "Super Extra"


def test_proxy_caido_sin_historia_cae_a_datos_de_prueba(cliente, proxy_configurado):
    proxy_configurado["caido"] = True
    assert datos.obtener_clientes_supermercado()[0]["nombre"] == "Super Xtra"


def test_intercambio_con_producto_real_usa_su_precio(cliente, proxy_configurado):
    r = cliente.post("/intercambios/nuevo/revisar", data={
        "cliente": "Super Extra", "sucursal": "Super Extra Arraiján", "d_PLT-JADE-01": "2",
    })
    assert "JADE" in r.text and "B/.11.00" in r.text


def test_con_proxy_llegan_pedidos_reales(cliente, proxy_configurado):
    ordenes = datos.obtener_ordenes()
    assert len(ordenes) == 1
    orden = ordenes[0]
    assert orden["pedido"] == "S00901" and orden["refSuper"] == "FAC-901"
    # La línea con cantidad 0 se descarta; la otra llega mapeada.
    assert orden["lineas"] == [
        {"sku": "PL-MENTA-01", "nombre": "MENTA", "precio": 3.0, "enviado": 4}
    ]
    r = cliente.get("/")
    assert "S00901" in r.text and "Ref. súper: FAC-901" in r.text
    assert "Super Extra · Arraiján" in r.text


def test_sin_proxy_las_ordenes_quedan_vacias(cliente, monkeypatch):
    monkeypatch.delenv("STOCK_PROXY_URL", raising=False)
    datos.reiniciar_cache_proxy()
    assert datos.obtener_ordenes() == []
