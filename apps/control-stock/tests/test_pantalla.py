from app import datos


def test_home_muestra_score_y_totales(cliente, con_inventario):
    r = cliente.get("/")
    assert r.status_code == 200
    # 1 crítico (Romero=2) y 1 bajo (Ixora=4) sin conteo: 100-6-2-15 = 77.
    assert ">77</b>" in r.text
    assert "1 crítica" in r.text and "1 baja" in r.text
    assert "sin conteo quincenal aún" in r.text
    # Totales: 47 unidades, 3 con stock, 1 agotada.
    assert ">47</b>" in r.text


def test_alerta_se_crea_para_criticos_y_bajos(cliente, con_inventario):
    # Alerta desde 'bajo' (< 2×umbral): Romero (2, crítico) e Ixora (4, bajo).
    cliente.get("/")
    pendientes = datos.alertas_pendientes()
    assert [a["sku"] for a in pendientes] == ["PL-ROMERO", "PL-IXORA"]
    assert pendientes[0]["cantidad"] == 2 and pendientes[1]["cantidad"] == 4


def test_alerta_atendida_y_reabierta_no_se_duplica(cliente, con_inventario):
    cliente.get("/")
    cliente.post("/alertas/atender", data={"sku": "PL-ROMERO"}, follow_redirects=False)
    assert [a["sku"] for a in datos.alertas_pendientes()] == ["PL-IXORA"]
    # Sigue bajo: la próxima carga reabre Romero una sola vez (no duplica).
    cliente.get("/")
    pendientes = [a["sku"] for a in datos.alertas_pendientes()]
    assert pendientes.count("PL-ROMERO") == 1
    assert set(pendientes) == {"PL-ROMERO", "PL-IXORA"}


def test_alerta_se_cierra_sola_si_el_stock_se_recupera(cliente, con_inventario):
    cliente.get("/")
    con_inventario[0]["disponible"] = 9  # Romero repuesto (>= 2×umbral)
    con_inventario[2]["disponible"] = 9  # Ixora repuesta también
    cliente.get("/")
    assert datos.alertas_pendientes() == []
    with datos._db() as con:
        fila = con.execute("SELECT atendida_por FROM alertas").fetchone()
    assert fila["atendida_por"] == "auto"


def test_umbral_configurable_reclasifica(cliente, con_inventario):
    cliente.post("/umbral", data={"umbral": "5"})
    assert datos.umbral() == 5
    r = cliente.get("/")
    # Con umbral 5, Ixora (4) también es crítica: 2 críticas.
    assert "2 críticas" in r.text
    assert [a["sku"] for a in datos.alertas_pendientes()] == ["PL-ROMERO", "PL-IXORA"]


def test_umbral_invalido_se_ignora(cliente, con_inventario):
    cliente.post("/umbral", data={"umbral": "no-numero"})
    cliente.post("/umbral", data={"umbral": "0"})
    assert datos.umbral() == 3


def test_fisico_negativo_alerta_con_el_numero_real(cliente, con_inventario):
    # Ixora queda en negativo: -2 físicas (el disponible el proxy lo da en 0).
    con_inventario[2]["fisico"] = -2
    con_inventario[2]["disponible"] = 0
    r = cliente.get("/")
    alertas = datos.alertas_pendientes()
    skus = {a["sku"]: a for a in alertas}
    assert "PL-IXORA" in skus
    assert skus["PL-IXORA"]["cantidad"] == -2
    assert "Stock negativo" in r.text and "-2" in r.text
    # Y en el score cuenta como crítico (Romero crítico + Ixora negativa).
    assert "2 críticas" in r.text
    # Al corregirse (conteo real, por encima de 2×umbral), se cierra sola.
    con_inventario[2]["fisico"] = 9
    con_inventario[2]["disponible"] = 9
    cliente.get("/")
    assert "PL-IXORA" not in {a["sku"] for a in datos.alertas_pendientes()}


def test_revision_hecha_queda_en_el_historial(cliente, con_inventario):
    cliente.post("/revisiones", follow_redirects=False)
    conteos = datos.conteos_recientes()
    assert conteos[0]["tipo"] == "revision"
    assert conteos[0]["estado"] == "hecha"
    assert conteos[0]["empleada"] == "genesis"
    r = cliente.get("/")
    assert "Revisión semanal" in r.text and "Hecha ✓" in r.text
    assert "Revisión hecha" in r.text  # el botón junto a la hoja


def test_sin_proxy_la_pantalla_avisa(cliente, monkeypatch, db_limpia):
    def caido(refrescar=False):
        raise datos.SinConexion("El stock-proxy no responde y no hay datos previos")
    monkeypatch.setattr(datos, "obtener_inventario", caido)
    r = cliente.get("/")
    assert r.status_code == 200
    assert "No hay conexión con el stock" in r.text
