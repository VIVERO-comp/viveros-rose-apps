"""Las pantallas responden y muestran lo que deben (contra una DB temporal)."""

from app import datos


def test_entregas_muestra_los_tres_pedidos(cliente, con_ordenes):
    r = cliente.get("/")
    assert r.status_code == 200
    for numero in ("Pedido 00774", "Pedido 00781", "Pedido 00770"):
        assert numero in r.text          # en pantalla, sin la S
    assert 'href="/orden/S00774"' in r.text  # por dentro, completa
    assert "B/.116.99" in r.text
    assert "Ref. súper: FAC-774" in r.text
    assert "Hola, Génesis" in r.text
    # La sucursal se muestra sin repetir el nombre del cliente.
    assert "Super Xtra · Villalobos" in r.text


def test_orden_por_fecha_mas_reciente_primero(cliente, con_ordenes):
    texto = cliente.get("/").text
    assert texto.index("Pedido 00774") < texto.index("Pedido 00770")


def test_sin_proxy_no_hay_entregas_simuladas(cliente, monkeypatch):
    monkeypatch.delenv("STOCK_PROXY_URL", raising=False)
    datos.reiniciar_cache_proxy()
    r = cliente.get("/")
    assert r.status_code == 200
    assert "No hay entregas pendientes" in r.text
    assert "S00774" not in r.text


def test_tabs_vacias_con_sus_mensajes(cliente):
    assert "Nada por regresar" in cliente.get("/devoluciones").text
    assert "plantas dañadas" in cliente.get("/intercambios").text
    assert "Aún no hay movimientos hoy" in cliente.get("/historial").text


def test_migracion_desde_esquema_con_factura(tmp_path, monkeypatch):
    """Una base v1 (columna "factura") se migra sola a "pedido"."""
    import json
    import sqlite3

    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.executescript(
        """
        CREATE TABLE recepciones (factura TEXT PRIMARY KEY, aceptado TEXT NOT NULL,
                                  confirmada_en TEXT NOT NULL);
        CREATE TABLE devoluciones (factura TEXT PRIMARY KEY, datos TEXT NOT NULL,
                                   regresada INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE historial (n INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL,
                                datos TEXT NOT NULL, creado_en TEXT NOT NULL);
        """
    )
    con.execute("INSERT INTO recepciones VALUES ('774', '{}', 'x')")
    con.execute("INSERT INTO historial (tipo, datos, creado_en) VALUES ('entrega', ?, 'x')",
                (json.dumps({"factura": "774", "regreso": False}),))
    con.commit()
    con.close()

    monkeypatch.setenv("RECEPCION_DB", str(ruta))
    datos.iniciar_db()
    con = sqlite3.connect(ruta)
    columnas = [f[1] for f in con.execute("PRAGMA table_info(recepciones)")]
    assert "pedido" in columnas and "factura" not in columnas
    datos_historial = json.loads(con.execute("SELECT datos FROM historial").fetchone()[0])
    assert datos_historial == {"pedido": "774", "regreso": False}
