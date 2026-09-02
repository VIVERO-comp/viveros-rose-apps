from app import calculos


def test_estado_con_umbral_3():
    assert calculos.estado(0, 3) == "agotada"
    assert calculos.estado(-1, 3) == "agotada"
    assert calculos.estado(1, 3) == "critico"
    assert calculos.estado(2, 3) == "critico"
    assert calculos.estado(3, 3) == "bajo"
    assert calculos.estado(5, 3) == "bajo"
    assert calculos.estado(6, 3) == "ok"


def test_estado_sigue_al_umbral():
    assert calculos.estado(4, 5) == "critico"
    assert calculos.estado(9, 5) == "bajo"
    assert calculos.estado(10, 5) == "ok"


def test_score_resta_por_criticos_bajos_y_conteo():
    assert calculos.score(0, 0, False) == 100
    assert calculos.score(3, 7, False) == 100 - 3 * 6 - 7 * 2
    assert calculos.score(0, 0, True) == 85
    # Nunca baja de cero por muchos críticos que haya.
    assert calculos.score(50, 50, True) == 0


def test_clasificar_cuenta_todo():
    inventario = [
        {"disponible": 0}, {"disponible": 2}, {"disponible": 4}, {"disponible": 10},
    ]
    cuentas = calculos.clasificar(inventario, 3)
    assert cuentas["agotadas"] == 1
    assert cuentas["criticos"] == 1
    assert cuentas["bajos"] == 1
    assert cuentas["ok"] == 1
    assert cuentas["unidades"] == 16
    assert cuentas["con_stock"] == 3


def test_fisico_negativo_cuenta_como_critico():
    # Se vendió sin existencias: disponible sale 0 (el proxy lo recorta) pero
    # el físico viene negativo tal cual. No es "agotada": es un error de
    # datos que resta en el score como crítico.
    inventario = [
        {"disponible": 0, "fisico": -2},
        {"disponible": 0, "fisico": 0},
    ]
    cuentas = calculos.clasificar(inventario, 3)
    assert cuentas["criticos"] == 1
    assert cuentas["agotadas"] == 1
    assert cuentas["unidades"] == 0


def test_emoji_por_palabra_clave():
    assert calculos.emoji_de("Cactus San Pedro") == "🌵"
    assert calculos.emoji_de("Menta") == "🌿"
    assert calculos.emoji_de("Croton Petra") == "🌺"
    assert calculos.emoji_de("Algo Desconocido") == calculos.EMOJI_DEFECTO


def test_emoji_por_categoria():
    assert calculos.emoji_categoria("Aromáticas") == "🌿"
    assert calculos.emoji_categoria("Florales") == "🌺"
    assert calculos.emoji_categoria("Frutales") == "🍋"
    assert calculos.emoji_categoria("Interior") == "🪴"
    assert calculos.emoji_categoria("Exterior") == "🌳"
    assert calculos.emoji_categoria("Sin categoría") == calculos.EMOJI_DEFECTO
