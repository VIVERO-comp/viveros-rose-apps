from app import datos


def test_ajuste_rapido_viaja_al_order_api(cliente, con_inventario, ajustes_registrados):
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2})
    assert r.status_code == 200
    assert r.json()["resultado"] == "aplicado"
    assert ajustes_registrados == [{
        "ajustes": [{"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}],
        "empleado": "genesis", "motivo": "ajuste_rapido",
    }]


def test_ajuste_aplicado_cierra_la_alerta_a_nombre_de_quien_ajusto(
        cliente, con_inventario, ajustes_registrados):
    cliente.get("/")  # crea la alerta del Romero
    cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2})
    assert datos.alertas_pendientes() == []
    with datos._db() as con:
        fila = con.execute("SELECT atendida_por FROM alertas").fetchone()
    assert fila["atendida_por"] == "genesis"


def test_conflicto_pasa_tal_cual_y_no_toca_alertas(cliente, con_inventario, monkeypatch):
    cliente.get("/")

    def conflicto(ajustes, empleado, motivo):
        return {"ok": True, "resultados": [
            {"sku": "PL-ROMERO", "cantidad": 7, "resultado": "conflicto", "anterior": 5},
        ]}

    monkeypatch.setattr(datos, "ajustar_en_odoo", conflicto)
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2})
    assert r.json() == {"sku": "PL-ROMERO", "cantidad": 7,
                        "resultado": "conflicto", "anterior": 5}
    assert len(datos.alertas_pendientes()) == 1  # la alerta sigue pendiente


def test_payload_invalido_es_400(cliente, con_inventario, ajustes_registrados):
    for cuerpo in [{}, {"sku": "PL-ROMERO"},
                   {"sku": "PL-ROMERO", "cantidad": -1, "esperada": 2},
                   {"sku": "PL-ROMERO", "cantidad": "7", "esperada": 2}]:
        assert cliente.post("/ajustar", json=cuerpo).status_code == 400
    assert ajustes_registrados == []


def test_sin_conexion_es_502(cliente, con_inventario, monkeypatch):
    def caido(ajustes, empleado, motivo):
        raise datos.SinConexion("No hay conexión con el servidor de pedidos")
    monkeypatch.setattr(datos, "ajustar_en_odoo", caido)
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2})
    assert r.status_code == 502
    assert r.json()["error"] == "sin_conexion"
