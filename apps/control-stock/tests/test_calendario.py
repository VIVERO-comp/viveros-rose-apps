"""La pestaña Calendario, en modo muestra (sin credenciales de Linear).

Las pruebas corren sin LINEAR_API_KEY, así que `app.calendario` usa sus
actividades de ejemplo en memoria: la pantalla y las acciones se prueban
enteras sin tocar el CALENDARIO ROSE real.
"""

import pytest

from app import calendario


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PROJECT_CALENDARIO_ID", raising=False)
    calendario.reiniciar_muestra()
    calendario.invalidar_cache()


def _abrir(cliente, **params):
    return cliente.get("/calendario", params=params)


def test_la_pantalla_abre_en_la_semana(cliente):
    respuesta = _abrir(cliente)
    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Calendario" in cuerpo
    # La semana trae los carriles de hora armados desde Python.
    assert "carril-caja" in cuerpo
    assert "Datos de muestra" in cuerpo


@pytest.mark.parametrize("vista, marca", [
    ("semana", "carril-caja"),
    ("dia", "carril-caja"),
    ("mes", 'class="mes"'),
    ("lista", "lista-caja"),
])
def test_las_cuatro_vistas_pintan(cliente, vista, marca):
    respuesta = _abrir(cliente, vista=vista)
    assert respuesta.status_code == 200
    assert marca in respuesta.text


def test_crear_actividad_la_deja_en_el_dia(cliente):
    dia = calendario.hoy().isoformat()
    respuesta = cliente.post(
        "/calendario/actividad",
        params={"volver": f"/calendario?dia={dia}&vista=lista"},
        data={"tipo": "entrega", "cliente": "Hotel Bristol", "lugar": "Obarrio",
              "fecha": dia, "hora": "11:30", "dur": "60", "prioridad": "3"},
        follow_redirects=False)
    assert respuesta.status_code == 303
    assert "aviso=" in respuesta.headers["location"]
    cuerpo = _abrir(cliente, dia=dia, vista="lista").text
    assert "Hotel Bristol" in cuerpo


def test_alquiler_crea_tambien_su_recogida(cliente):
    dia = calendario.hoy().isoformat()
    vuelta = calendario.hoy().replace(day=28).isoformat()
    cliente.post("/calendario/actividad",
                 data={"tipo": "alquiler", "cliente": "Boda Las Nubes", "fecha": dia,
                       "hora": "13:00", "dur": "120", "recogida": vuelta},
                 follow_redirects=False)
    recogidas = [a for a in calendario.listar(dia, vuelta)
                 if a["tipo"] == "recogida" and a["cliente"] == "Boda Las Nubes"]
    assert len(recogidas) == 1
    assert recogidas[0]["fecha"] == vuelta


def test_marcar_terminada_y_reabrir(cliente):
    dia = calendario.hoy().isoformat()
    actividad = [a for a in calendario.listar(dia, dia) if a["estado"] == "pend"][0]
    cliente.post(f"/calendario/actividad/{actividad['id']}/estado",
                 data={"estado": "hecha"}, follow_redirects=False)
    assert calendario._muestra_buscar(actividad["id"])["estado"] == "hecha"
    cliente.post(f"/calendario/actividad/{actividad['id']}/estado",
                 data={"estado": "pend"}, follow_redirects=False)
    assert calendario._muestra_buscar(actividad["id"])["estado"] == "pend"


def test_mover_cambia_la_fecha(cliente):
    dia = calendario.hoy().isoformat()
    manana = (calendario.hoy().replace(day=1)).isoformat()
    actividad = calendario.listar(dia, dia)[0]
    cliente.post(f"/calendario/actividad/{actividad['id']}/mover",
                 data={"fecha": manana, "hora": "15:00"}, follow_redirects=False)
    movida = calendario._muestra_buscar(actividad["id"])
    assert movida["fecha"] == manana
    assert movida["hora"] == "15:00"


def test_cancelar_no_borra_la_actividad(cliente):
    dia = calendario.hoy().isoformat()
    actividad = calendario.listar(dia, dia)[0]
    cuantas = len(calendario._muestra())
    cliente.post(f"/calendario/actividad/{actividad['id']}/estado",
                 data={"estado": "cancel"}, follow_redirects=False)
    assert len(calendario._muestra()) == cuantas
    assert calendario._muestra_buscar(actividad["id"])["estado"] == "cancel"


def test_una_actividad_sin_cliente_no_se_crea(cliente):
    respuesta = cliente.post("/calendario/actividad",
                             data={"tipo": "entrega", "cliente": "  ",
                                   "fecha": calendario.hoy().isoformat()},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]


def test_los_filtros_de_la_barra_se_aplican_en_el_servidor(cliente):
    dia = calendario.hoy().isoformat()
    todas = calendario.listar(dia, dia)
    entregas = calendario.filtrar(todas, tipos_apagados={"entrega"})
    assert all(a["tipo"] != "entrega" for a in entregas)
    solo_texto = calendario.filtrar(todas, texto="bristol")
    assert all("bristol" in (a["cliente"] + a["lugar"]).lower() for a in solo_texto)


def test_la_semana_reparte_las_actividades_que_chocan():
    dia = calendario.hoy().isoformat()
    choque = [
        {"id": "a", "ref": "VIV-1", "tipo": "entrega", "cliente": "A", "lugar": "", "nota": "",
         "hora": "09:00", "dur": 60, "fecha": dia, "estado": "pend", "prioridad": 3,
         "resp": "Juan", "resp_id": "juan", "url": "", "titulo": ""},
        {"id": "b", "ref": "VIV-2", "tipo": "visita", "cliente": "B", "lugar": "", "nota": "",
         "hora": "09:30", "dur": 60, "fecha": dia, "estado": "pend", "prioridad": 3,
         "resp": "Pedro", "resp_id": "pedro", "url": "", "titulo": ""},
    ]
    carril = calendario.carril_semana(choque, [dia], dia)
    assert len(carril["bloques"]) == 2
    # Se reparten el ancho de la columna en vez de taparse.
    assert "* 0.0" in carril["bloques"][0]["estilo"] or "left:calc" in carril["bloques"][0]["estilo"]
    assert carril["bloques"][0]["estilo"] != carril["bloques"][1]["estilo"]


def test_las_horas_vacias_miden_menos_y_los_bloques_siguen_en_su_lugar():
    """Regla del dueño: la fila de una hora sin trabajo se encoge."""
    dia = calendario.hoy().isoformat()
    una = {"id": "x", "ref": "VIV-9", "tipo": "entrega", "cliente": "C", "lugar": "", "nota": "",
           "hora": "10:00", "dur": 60, "fecha": dia, "estado": "pend", "prioridad": 3,
           "resp": "Juan", "resp_id": "juan", "url": "", "titulo": ""}
    carril = calendario.carril_semana([una], [dia], dia)
    horas = {f["h"]: f for f in carril["horas"]}
    vacias = [f for f in carril["horas"] if f["vacia"]]
    # Los altos son % del total y la ocupada pesa ALTO_HORA/ALTO_HORA_VACIA
    # veces lo que una vacía; entre todas suman el 100 %.
    assert vacias
    proporcion = horas[10]["alto"] / vacias[0]["alto"]
    assert abs(proporcion - calendario.ALTO_HORA / calendario.ALTO_HORA_VACIA) < 0.01
    assert abs(sum(f["alto"] for f in carril["horas"]) - 100) < 0.1
    # El bloque arranca justo donde empieza su fila (en %, +2px de aire).
    assert f"top:calc({horas[10]['top']:.4f}% + 2px)" in carril["bloques"][0]["estilo"]


def test_el_color_del_bloque_es_opaco():
    """Con rgba() los bloques encimados se transparentaban entre sí."""
    for chip in calendario.chips([], set()):
        assert chip["color"].startswith("#")
    dia = calendario.hoy().isoformat()
    una = {"id": "y", "ref": "VIV-8", "tipo": "visita", "cliente": "C", "lugar": "", "nota": "",
           "hora": "09:00", "dur": 60, "fecha": dia, "estado": "pend", "prioridad": 3,
           "resp": "Juan", "resp_id": "juan", "url": "", "titulo": ""}
    bloque = calendario.carril_semana([una], [dia], dia)["bloques"][0]
    assert "rgba(" not in bloque["estilo"]


def test_el_inicio_muestra_el_calendario(cliente):
    """La pestaña Inicio trae hoy, atrasadas, la agenda y los 7 días."""
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Tu día" in cuerpo
    assert "cal-agenda" in cuerpo
    assert "cal-barras" in cuerpo
    assert "Ver el calendario" in cuerpo


def test_el_panel_del_inicio_cuenta_bien():
    dia = calendario.hoy().isoformat()
    actividades = calendario.listar(dia, dia)
    panel = calendario.panel_inicio(actividades, dia)
    del_dia = [a for a in actividades if a["fecha"] == dia and a["estado"] != "cancel"]
    assert panel["numeros"]["hoy"] == len(del_dia)
    assert len(panel["barras"]) == 7
    assert panel["barras"][0]["hoy"] is True
    assert all(0 <= b["alto"] <= 100 for b in panel["barras"])


def test_si_linear_falla_el_inicio_igual_abre(cliente, monkeypatch):
    """El stock no puede quedarse sin pantalla porque Linear esté caído."""
    def explota(*_args, **_kwargs):
        raise calendario.ErrorCalendario("Linear no responde")

    monkeypatch.setattr(calendario, "listar", explota)
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "No se pudo leer el calendario" in respuesta.text


# ---------------------------------------------------------------------------
# La suscripción del teléfono (feed ICS) y la pantalla limpia
# ---------------------------------------------------------------------------

def test_sin_chips_ni_alcance_en_la_pantalla(cliente):
    """Pedido del dueño (22/09/2026): fuera la fila de chips de tipos y el
    segmento "Mi calendario / Todo el equipo" — quitaban espacio. El alcance
    lo sigue decidiendo el servidor y los colores hablan en los bloques."""
    cuerpo = _abrir(cliente).text
    assert 'class="chips"' not in cuerpo
    assert "Todo el equipo" not in cuerpo
    assert "Ocultar terminadas" in cuerpo  # sobrevive, ahora en la barra


def test_atrasadas_es_un_renglon_que_lleva_a_la_lista(cliente):
    cuerpo = _abrir(cliente).text
    if "atrasada" in cuerpo:
        assert "Verlas" in cuerpo


def test_feed_sin_token_no_existe(cliente):
    assert cliente.get("/calendario.ics").status_code == 404
    assert cliente.get("/calendario.ics", params={"t": "nadie"}).status_code == 404


def test_feed_con_token_trae_ics(cliente):
    from app import calendario_ics
    # Abrir el calendario crea el token de la empleada de la sesión.
    _abrir(cliente)
    token = calendario_ics.token_de("genesis")
    respuesta = cliente.get("/calendario.ics", params={"t": token})
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/calendar")
    cuerpo = respuesta.text
    assert "BEGIN:VCALENDAR" in cuerpo
    assert "BEGIN:VEVENT" in cuerpo
    # El UID amarra el evento al issue: editar actualiza, no duplica.
    assert "@calendario.plantaspanama.com" in cuerpo
    assert "TZID=America/Panama" in cuerpo
    # Una cancelada viaja cancelada, no desaparece.
    assert ("STATUS:CANCELLED" in cuerpo) == any(
        a["estado"] == "cancel" and a["fecha"] for a in calendario._muestra())


def test_feed_regenerar_mata_el_enlace_viejo(cliente):
    from app import calendario_ics
    _abrir(cliente)
    viejo = calendario_ics.token_de("genesis")
    respuesta = cliente.post("/calendario/suscripcion/regenerar", follow_redirects=False)
    assert respuesta.status_code == 303
    # El botón vive en Ajustes: la vuelta es para allá.
    assert respuesta.headers["location"].startswith("/?tab=ajustes")
    assert cliente.get("/calendario.ics", params={"t": viejo}).status_code == 404
    nuevo = calendario_ics.token_de("genesis")
    assert nuevo != viejo
    assert cliente.get("/calendario.ics", params={"t": nuevo}).status_code == 200


def test_el_enlace_de_sync_vive_en_ajustes(cliente):
    """La suscripción se toma de Ajustes (pedido del 22/09/2026), no de la
    pantalla del calendario."""
    inicio = cliente.get("/").text
    assert "calendario.ics?t=" in inicio
    assert "Sync con iPhone o Google Calendar" in inicio
    assert "calendario.ics?t=" not in cliente.get("/calendario").text


def test_el_carril_llena_el_alto_en_porcentaje(cliente):
    """Las filas pesan por flex-grow y los bloques van en % del carril:
    así la semana estira hasta abajo como la vista de mes."""
    cuerpo = _abrir(cliente).text
    assert "flex-grow:" in cuerpo
    assert "top:calc(" in cuerpo
    assert "height:46px" not in cuerpo  # ya no hay alturas fijas de fila


def test_los_botones_de_sync_son_directos(cliente):
    """El dueño pidió botones que suscriben al toque, no un enlace a copiar."""
    inicio = cliente.get("/").text
    assert "webcal://" in inicio
    assert "calendar.google.com/calendar/render?cid=webcal" in inicio


def test_compras_se_esconde_con_la_bandera(cliente, monkeypatch):
    monkeypatch.setenv("COMPRAS_ACTIVAS", "0")
    inicio = cliente.get("/").text
    assert 'href="/compras"' not in inicio
    # Y el POST rebota en el servidor, no solo se esconde el botón.
    r = cliente.post("/compras/nueva", data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/compras"


def test_proyectos_se_esconde_con_la_bandera(cliente, monkeypatch):
    monkeypatch.setenv("PROYECTOS_ACTIVOS", "0")
    assert 'href="/proyecto"' not in _abrir(cliente).text
    r = cliente.get("/proyecto", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


# ---------------------------------------------------------------------------
# La vista del teléfono (rediseño del 22/09/2026): un día a la vez.
# ---------------------------------------------------------------------------

def test_la_pagina_trae_la_vista_del_telefono(cliente):
    """La misma página trae la tira de la semana, la agenda del día, el
    navbar de abajo compartido y el "+" flotante; el CSS decide qué se ve."""
    cuerpo = _abrir(cliente).text
    assert "mov-tira" in cuerpo
    assert "mov-agenda" in cuerpo
    assert cuerpo.count("mov-dia") >= 7  # los siete días de la semana
    assert 'class="fab"' in cuerpo
    assert "<nav>" in cuerpo  # el navbar de _nav.html


def test_vista_movil_un_dia_a_la_vez():
    dia = calendario.hoy().isoformat()
    actividades = calendario.listar(dia, dia)
    movil = calendario.vista_movil(actividades, dia, dia)
    assert len(movil["tira"]) == 7
    assert [d for d in movil["tira"] if d["sel"]][0]["iso"] == dia
    # Todas las actividades DEL DÍA caen en alguna fila de hora (listar
    # también trae atrasadas de otros días; esas no son de esta agenda).
    del_dia = [a for a in actividades if a["fecha"] == dia]
    en_filas = sum(len(f["actividades"]) for f in movil["horas"])
    assert en_filas == movil["total"] == len(del_dia)
    # Y las de la misma hora se apilan (traen su rango legible, no posición).
    for fila in movil["horas"]:
        for a in fila["actividades"]:
            assert "–" in a["rango"]


def test_tocar_una_hora_vacia_preselecciona_fecha_y_hora(cliente):
    import re
    cuerpo = _abrir(cliente).text
    # Cada fila VACÍA de la agenda enlaza al formulario con fecha y hora ya
    # puestas (las ocupadas muestran sus tarjetas, no un enlace de crear).
    assert re.search(r'nueva=1&(?:amp;)?fecha=[^"&]+&(?:amp;)?hora=\d{2}%3A00', cuerpo)


def test_error_al_crear_conserva_lo_escrito(cliente, monkeypatch):
    """Si Linear falla, el formulario vuelve abierto y con lo tipeado."""
    def truena(**_kw):
        raise calendario.ErrorCalendario("Linear no respondió.")
    monkeypatch.setattr(calendario, "crear", truena)
    dia = calendario.hoy().isoformat()
    r = cliente.post(
        "/calendario/actividad",
        params={"volver": f"/calendario?dia={dia}&vista=semana"},
        data={"tipo": "entrega", "cliente": "Hotel Prueba", "lugar": "Obarrio",
              "fecha": dia, "hora": "11:30", "dur": "90", "prioridad": "2",
              "nota": "dos palmas"},
        follow_redirects=False)
    assert r.status_code == 303
    destino = r.headers["location"]
    assert "nueva=1" in destino and "error=" in destino
    cuerpo = cliente.get(destino).text
    assert 'value="Hotel Prueba"' in cuerpo
    assert 'value="Obarrio"' in cuerpo
    assert "dos palmas" in cuerpo
    assert "Linear no respondi" in cuerpo


def test_el_nav_sin_inicio_y_calendario_primero(cliente):
    """Pedido del dueño (22/09/2026): "quita inicio y pon calendario de
    primero". El navbar compartido no lleva Inicio y arranca en Calendario."""
    cuerpo = cliente.get("/venta").text
    nav = cuerpo.split("<nav>")[1].split("</nav>")[0]
    assert "Inicio" not in nav
    assert nav.find("Calendario") < nav.find("Stock") < nav.find("Vender")


def test_catalogo_guarda_su_marca_de_tiempo(monkeypatch):
    """Regresión del 500 del 22/09: el refactor de catalogo() dejó una
    variable sin definir al guardar el caché. Se llama con Linear fingido."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_x")
    monkeypatch.setenv("LINEAR_PROJECT_CALENDARIO_ID", "p1")
    monkeypatch.setattr(calendario, "_pedir", lambda *_a, **_k: {"team": {
        "states": {"nodes": [{"id": "s1", "name": "Todo", "type": "unstarted", "position": 0}]},
        "labels": {"nodes": []},
        "members": {"nodes": [{"id": "u1", "name": "Ana", "displayName": "Ana",
                               "email": "ana@x.com", "active": True}]},
    }})
    dato = calendario.catalogo(refrescar=True)
    assert dato["gente"][0]["nombre"] == "Ana"
    assert calendario._catalogo_cache["en"] > 0


def test_proyectos_sale_en_el_nav_compartido(cliente):
    """Al pasar a Vender (o Stock) la pestaña Proyectos no se esconde:
    el navbar compartido la trae cuando la bandera está encendida."""
    nav = cliente.get("/venta").text.split("<nav>")[1].split("</nav>")[0]
    assert "Proyectos" in nav
