"""El − cantidad + de la lista de Stock: A3 y A4 del BLOQUE 53 (lista de
Korto del 7/10/2026).

A3 — el número SE TECLEA. Antes el pie de la tarjeta pintaba el físico en
un `<b>`: − y + lo movían y no había forma de escribirlo.

A4 — al tocar − o + aparecían «Guardar en Odoo» y «Cancelar» y la fila se
deformaba (el bloque de botones empujaba todo lo demás). Ahora el cambio
se manda SOLO un momento después del último toque, con un aviso chico de
«Guardado» y un «Deshacer» al lado, y la fila NO cambia de tamaño en
ningún estado.

Lo que estas pruebas amarran, que es lo que se puede romper sin que nadie
avise:

- que el juez de lo tecleado sea PYTHON y sea EL MISMO de la vista plana
  (`stock_escritura.cantidad_contada`), con su texto, y que lo ilegible
  no escriba nada en Odoo;
- que un cambio no se pueda perder: el guardado se dispara también con
  `visibilitychange` y `pagehide` —los dos eventos que sí llegan cuando
  se cierra la pestaña— y las peticiones salen con `keepalive`;
- que «Deshacer» pase por el MISMO punto único de escritura (otro POST
  /ajustar → stock_escritura.escribir_stock, que lo anota en la
  bitácora): deshacer es otro cambio registrado, no un borrado;
- que la fila no pueda cambiar de alto: el avisito cuelga FUERA del flujo
  y el campo tiene ancho y alto fijos;
- y que el candado del rol Inventario siga donde estaba.
"""

import re

from app import datos, stock_escritura

JS = "app/static/app.js"
CSS = "app/static/styles.css"
PIEL = "app/static/diseno-stock.css"


def pie_de_app_js():
    """El trozo de `pintar()` que arma el pie de la tarjeta."""
    js = open(JS).read()
    arranque = js.index('<div class="card-pie solo-pc">')
    return js[arranque:js.index("</div>", arranque)]


# ---------------------------------------------------------------------------
# A3: se puede teclear, y lo que vale lo decide Python
# ---------------------------------------------------------------------------

def test_el_fisico_de_la_fila_es_un_campo_que_se_teclea():
    pie = pie_de_app_js()
    assert '<input class="pie-valor"' in pie
    assert 'inputmode="numeric"' in pie
    # El `<b>` de solo lectura no vuelve.
    assert "<b class=\"pie-valor\"" not in pie
    # Y − y + siguen ahí: tocar sigue siendo más rápido que escribir.
    assert 'data-paso="-1"' in pie and 'data-paso="1"' in pie


def test_lo_tecleado_viaja_crudo_y_lo_juzga_python():
    """El navegador no decide si «12a» es un número: manda el texto tal
    cual en `cantidadTexto` y pinta el aviso que redacte el servidor."""
    js = open(JS).read()
    assert "cantidadTexto: campo.value" in js
    # Nada de parseInt sobre el campo antes de mandarlo (eso convertía
    # «12a» en 12 y «abc» en 0, justo lo que la regla prohíbe).
    assert "parseInt(campo.value" not in js.split("function mandarPie", 1)[1].split("}", 1)[0]


def test_un_numero_tecleado_se_guarda_por_el_punto_unico(
        cliente, con_inventario, ajustes_registrados):
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO",
                                       "cantidadTexto": "7", "esperada": 2})
    assert r.status_code == 200
    assert r.json()["resultado"] == "aplicado"
    assert ajustes_registrados == [{
        "ajustes": [{"sku": "PL-ROMERO", "cantidad": 7, "esperada": 2}],
        "empleado": "genesis", "motivo": "ajuste_rapido",
    }]


def test_lo_ilegible_se_rechaza_con_el_aviso_de_siempre_y_no_escribe(
        cliente, con_inventario, ajustes_registrados):
    """Texto, negativo, decimales o vacío: el MISMO juez y el MISMO texto
    que la vista plana del rol Inventario. Y nada llega a Odoo — el
    valor tecleado se queda en pantalla porque nadie lo toca."""
    for crudo in ("abc", "-3", "2.5", "", "   ", "7 plantas"):
        r = cliente.post("/ajustar", json={"sku": "PL-ROMERO",
                                           "cantidadTexto": crudo,
                                           "esperada": 2})
        assert r.status_code == 400, crudo
        cuerpo = r.json()
        assert cuerpo["error"] == "cantidad", crudo
        _, esperado = stock_escritura.cantidad_contada(crudo)
        assert cuerpo["mensaje"] == esperado, crudo
    assert ajustes_registrados == []


def test_el_cero_vale(cliente, con_inventario, ajustes_registrados):
    """Contar cero plantas es un conteo: no es un error."""
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO",
                                       "cantidadTexto": "0", "esperada": 2})
    assert r.status_code == 200
    assert ajustes_registrados[0]["ajustes"][0]["cantidad"] == 0


def test_el_contrato_viejo_del_modal_no_se_movio(
        cliente, con_inventario, ajustes_registrados):
    """Modificar stock sigue mandando `cantidad` como entero, y un string
    ahí sigue siendo peticion_invalida: la puerta nueva es `cantidadTexto`
    y no afloja la vieja."""
    assert cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": "7",
                                          "esperada": 2}).status_code == 400
    assert ajustes_registrados == []
    assert cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7,
                                          "esperada": 2}).status_code == 200


# ---------------------------------------------------------------------------
# A4: se guarda solo, y no se pierde nada
# ---------------------------------------------------------------------------

def test_ya_no_hay_guardar_ni_cancelar_en_la_fila():
    pie = pie_de_app_js()
    assert "Guardar en Odoo" not in pie
    assert "Cancelar" not in pie
    # Y el pie ya no esconde un bloque de botones que al aparecer corría
    # todo lo demás de lugar (eso era «la fila se deforma»).
    assert "pie-acciones" not in open(JS).read()
    assert "pie-acciones" not in open(CSS).read()
    assert "pie-acciones" not in open(PIEL).read()


def test_el_guardado_sale_solo_tras_el_ultimo_toque():
    js = open(JS).read()
    assert re.search(r"const ESPERA_GUARDADO = \d+", js)
    # Cada toque reinicia la cuenta: sin el clearTimeout, diez toques
    # serían diez escrituras en Odoo.
    anotar = js.split("function anotarPendiente", 1)[1].split("\n}", 1)[0]
    assert "clearTimeout" in anotar and "setTimeout" in anotar


def test_un_cambio_no_se_pierde_si_se_cierra_la_pestana():
    """Los tres disparos, no uno: el temporizador, el campo al perder el
    foco, y la página al irse. `visibilitychange` y `pagehide` son los
    dos eventos que SÍ llegan al cerrar la pestaña, y `keepalive` hace
    que la petición termine aunque el documento ya esté muerto."""
    js = open(JS).read()
    assert 'document.addEventListener("visibilitychange"' in js
    assert 'window.addEventListener("pagehide", volcarPendientes)' in js
    assert 'addEventListener("focusout"' in js
    assert "keepalive: true" in js
    # Y repintar la lista (buscar, filtrar, cambiar de vista) borra las
    # filas: lo pendiente se manda ANTES de que el campo desaparezca.
    assert js.split("function pintar()", 1)[1].lstrip().startswith(
        "//") or "volcarPendientes();" in js.split("function pintar()", 1)[1][:400]


def test_el_aviso_es_guardado_con_su_deshacer():
    js = open(JS).read()
    assert '"✓ Guardado"' in js
    assert '"Deshacer"' in js


def test_deshacer_pasa_por_el_punto_unico_y_no_borra_nada():
    """Deshacer es OTRO cambio registrado: el mismo POST /ajustar, que
    entra por stock_escritura.escribir_stock y deja su propia fila en la
    bitácora. No existe ninguna ruta que borre un stock_cambio."""
    js = open(JS).read()
    deshacer = js.split("function pieGuardado", 1)[1].split("\n}", 1)[0]
    # El botón solo repone el valor y vuelve a mandar por el camino único.
    assert "mandarPie(fila, p)" in deshacer
    assert "fetch(" not in deshacer
    # Un solo destino de escritura de stock en todo el JS de la pantalla.
    assert set(re.findall(r'fetch\("(/[a-z-]+)"', js)) >= {"/ajustar"}
    assert "/stock/cambios" not in deshacer
    # Y en Python no hay forma de borrar una fila de la bitácora.
    fuente = open("app/stock_escritura.py").read()
    assert "DELETE" not in fuente.upper().replace("DELETED", "")


def test_deshacer_queda_anotado_como_un_cambio_mas(
        cliente, con_inventario, ajustes_registrados):
    """La prueba de verdad: guardar 7 y deshacer a 2 deja DOS filas en
    stock_cambio, no cero."""
    cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidadTexto": "7",
                                   "esperada": 2})
    cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidadTexto": "2",
                                   "esperada": 7})
    filas = stock_escritura.cambios_recientes("PL-ROMERO")
    assert [(f["antes"], f["despues"]) for f in filas] == [(7, 2), (2, 7)]
    assert {f["tipo_operacion"] for f in filas} == {"ajuste_rapido"}
    assert {f["por"] for f in filas} == {"genesis"}


def test_la_campana_no_se_queda_mintiendo_sin_recarga(
        cliente, con_inventario, ajustes_registrados):
    """Antes de A4 cada ajuste recargaba la página y eso traía la campana
    al día. Ahora no hay recarga: el servidor sigue cerrando la alerta
    (`atender_alerta`, solo con «aplicado») y la pantalla quita la fila
    que el servidor ya no pintaría, recontando el número — no lo
    adivina restando uno."""
    cliente.get("/?tab=stock")          # abre las alertas
    pendientes = {a["sku"] for a in datos.alertas_pendientes()}
    assert "PL-ROMERO" in pendientes
    cliente.post("/ajustar", json={"sku": "PL-ROMERO", "cantidadTexto": "40",
                                   "esperada": 2})
    assert "PL-ROMERO" not in {a["sku"] for a in datos.alertas_pendientes()}
    js = open(JS).read()
    assert 'if (r.resultado === "aplicado") quitarAlerta(p.sku)' in js
    quitar = js.split("function quitarAlerta", 1)[1].split("\n}", 1)[0]
    # Se RECUENTA lo que queda en la lista; nada de `-1` a ciegas.
    assert 'querySelectorAll("[data-ir]").length' in quitar
    assert "- 1" not in quitar and "-1" not in quitar


def test_el_conflicto_no_celebra_y_muestra_lo_que_hay_en_odoo(
        cliente, con_inventario, monkeypatch):
    """Si alguien movió el stock en el medio, nada se escribe y el aviso
    lo dice con el valor fresco."""
    def conflicto(ajustes, empleado, motivo):
        return {"ok": True, "resultados": [
            {"sku": "PL-ROMERO", "cantidad": 7, "resultado": "conflicto",
             "anterior": 5}]}

    monkeypatch.setattr(datos, "ajustar_en_odoo", conflicto)
    r = cliente.post("/ajustar", json={"sku": "PL-ROMERO",
                                       "cantidadTexto": "7", "esperada": 2})
    assert r.json()["resultado"] == "conflicto"
    assert stock_escritura.cambios_recientes("PL-ROMERO") == []
    js = open(JS).read()
    assert 'r.resultado === "conflicto"' in js


# ---------------------------------------------------------------------------
# La fila NO cambia de tamaño
# ---------------------------------------------------------------------------

def test_el_campo_rechazado_queda_marcado():
    """El avisito se va a los segundos (cuelga sobre la fila de abajo y
    dejarlo para siempre llenaría la lista de globitos), así que el campo
    se queda marcado: el borde rojo aguanta hasta que se corrija."""
    js = open(JS).read()
    assert 'classList.toggle("con-error", clase === "mal")' in js
    for hoja in (CSS, PIEL):
        assert ".pie-valor.con-error{" in open(hoja).read(), hoja
    # Y el error NO le roba el clic a la fila de abajo.
    regla = open(CSS).read().split(".pie-estado.mal{", 1)[1].split("}", 1)[0]
    assert "pointer-events:none" in regla


def test_el_avisito_cuelga_fuera_del_flujo():
    """La garantía de que la fila mida lo mismo antes y después del toque
    en CUALQUIER estado (guardando, guardado, error): el renglón del
    aviso está posicionado, así que no ocupa alto en la fila."""
    css = open(CSS).read()
    regla = css.split(".pie-estado{", 1)[1].split("}", 1)[0]
    assert "position:absolute" in regla
    assert "top:100%" in regla
    # Y su ancla es el pie, no la página.
    assert ".card-pie{position:relative}" in css


def test_el_campo_tiene_ancho_y_alto_fijos():
    """Pasar de 9 a 100 no puede ensanchar nada: con un ancho elástico la
    fila se movía sola al escribir."""
    for hoja in (CSS, PIEL):
        regla = open(hoja).read().split(".pie-valor{", 1)[1].split("}", 1)[0]
        assert re.search(r"width:\d+px", regla), hoja
        assert re.search(r"height:\d+px", regla), hoja


def test_el_campo_mide_lo_mismo_que_los_botones_de_su_fila():
    """El campo y los cuadrados de − y + tienen que medir lo MISMO de alto
    en cada piel: si uno crece, la fila crece con él. Se compara uno con
    otro y no contra un número, para que al reafinar la piel haya que
    mover los dos a la vez."""
    for hoja in (CSS, PIEL):
        css = open(hoja).read()
        marca = "#tab-stock " if hoja == PIEL else ""
        boton = css.split(marca + ".pie-btn{", 1)[1].split("}", 1)[0]
        campo = css.split(marca + ".pie-valor{", 1)[1].split("}", 1)[0]
        de = lambda regla: re.search(r"height:(\d+)px", regla).group(1)
        assert de(boton) == de(campo), f"{hoja}: {de(boton)} vs {de(campo)}"


# ---------------------------------------------------------------------------
# Lo que NO se tocó
# ---------------------------------------------------------------------------

def test_el_candado_del_rol_inventario_sigue_donde_estaba():
    """A3 y A4 son aspecto y guardado de la lista: ni una línea de
    permisos. El rol sigue decidiéndose en el predicado único."""
    predicado = open("app/datos_roles.py").read()
    assert "def solo_inventario(empleada):" in predicado
    assert "len(mios) == 1 and mios[0][\"slug\"] == SLUG_INVENTARIO" in predicado


def test_los_tokens_de_color_estan_definidos_para_TODA_la_hoja():
    """Un `var()` sin valor no da error: invalida la propiedad y CALLA.

    Esta prueba nació acotando tres tokens a `main.plano`, porque el
    defecto se vio ahí: el campo de la cantidad de la vista plana del rol
    Inventario se quedaba sin borde (parecía texto) y «Guardar» salía
    blanco sobre blanco, ya que `--tinta/--borde/--suave` viven en
    `calendario.css` y esa pantalla no la carga.

    **Medido después, el agujero era MUCHO más grande**: son CUATRO
    tokens (`--fondo` también) usados en **34 lugares de esta hoja** —los
    selectores de cliente y de lead de Vender, el marco de vista previa y
    los campos de Ajustes (roles, dispositivos, chips)— y todos salían sin
    borde o transparentes. Acotarlos a `main.plano` arreglaba una pantalla
    y dejaba las otras rotas en silencio, así que ahora viven en `:root`,
    con los mismos valores de la otra piel para que las dos pinten el
    mismo gris. El parche acotado se retiró: era redundante, con valores
    idénticos.

    Lo que esta prueba cuida, entonces, es lo que de verdad importa:
    **ningún `var()` de esta hoja se queda sin valor.**
    """
    css = open(CSS).read()
    raiz = re.search(r"^:root\{(.*?)\}", css, re.S | re.M)
    assert raiz, "styles.css tiene que definir sus tokens en :root"
    for token in ("--fondo:", "--borde:", "--suave:", "--tinta:"):
        assert token in raiz.group(1), token
    # Y el parche viejo no vuelve: definir el mismo token en dos lugares
    # es la puerta para que mañana difieran sin que nada avise.
    assert "main.plano{--tinta:" not in css
