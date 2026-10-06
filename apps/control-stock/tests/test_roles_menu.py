"""Punto 1 del plan de roles (BLOQUE 20): los 5 roles y el menú por rol.

Lo que se prueba, del diseño aprobado (docs/DISENO-roles-punto1-menu-por-rol
de plantaspanama) con las 10 precisiones del review del Arquitecto:

- la migración asegura los 5 slugs (director · operaciones · atencion ·
  inventario · finanzas), idempotente, SIN personas nuevas, y adopta un
  tocayo creado a mano en vez de duplicarlo;
- el slug NO es editable desde Ajustes, para los 5 (precisión 5);
- menú y alcance salen de la MISMA fuente y con varios roles la UNIÓN
  aplica igual a los dos; la casa sale de PRIORIDAD_CASA, estable
  (precisión 4);
- la matriz por slug: rutas representativas × TODOS los métodos
  (GET/HEAD/POST/PUT/PATCH/DELETE), por URL directa y request a mano
  (precisión 3 y BLOQUE 22.7); /logout pasa para los 5 (precisión 9);
- finanzas: TODOS los GET pasan (modo ver de verdad) y TODA escritura es
  403 — sin lista blanca de POST (BLOQUE 22.1);
- sin rol = como hoy: FAIL-OPEN de transición, fijado como decisión
  explícita (precisión 8);
- asignar/quitar rol queda AUDITADO en rol_persona_bitacora, INSERT-only,
  epoch UTC (precisión 7).

La semántica del rol Inventario NO cambia ni un pelo: sus tests viven en
test_rol_inventario.py y siguen verdes sin tocarse.

La V2 (BLOQUE 29 aprobado + docs/ANALISIS-rol-manda-sobre-admin.md): «el
rol manda aunque seas admin». La excepción global del admin murió; la
que queda es POR RUTA (RUTAS_SISTEMA → Ajustes y sistema), y esa celda
—admin + CADA rol → Ajustes SIEMPRE pasa— es obligatoria: es el seguro
contra el encierro de emergencia. Ajustes además quedó partido: lo
TÉCNICO sigue solo-admin y lo de NEGOCIO pasa a «admin O director».
"""

from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import datos, datos_roles, seguridad
from app.main import app

CLAVE = "clave-de-prueba"
LOS_5 = ("director", "operaciones", "atencion", "inventario", "finanzas")


def _rol_n(slug):
    with datos._db() as con:
        return con.execute("SELECT n FROM roles WHERE slug=?",
                           (slug,)).fetchone()["n"]


def _empleada(usuario):
    return {"id": usuario, "nombre": usuario.capitalize(),
            "email": None, "email_verificado": 0}


def _con_roles(usuario, *slugs):
    """Crea la empleada, le pone los roles y devuelve su TestClient."""
    seguridad.crear_empleada(usuario, usuario.capitalize(), CLAVE)
    for slug in slugs:
        assert datos_roles.poner_persona(_rol_n(slug), usuario, "korto") is None
    c = TestClient(app)
    r = c.post("/login", data={"usuario": usuario, "contrasena": CLAVE},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _nav_de(html):
    """El trozo <nav>…</nav> de una página: el menú por rol se afirma
    AHÍ, no en el resto de la página (que tiene sus propios enlaces)."""
    return html[html.index("<nav>"):html.index("</nav>")]


# ---------------------------------------------------------------------------
# La migración: los 5 slugs
# ---------------------------------------------------------------------------

def test_migracion_asegura_los_5_slugs_sin_personas_nuevas(db_limpia):
    with datos._db() as con:
        for slug in LOS_5:
            fila = con.execute(
                "SELECT n, deber, activo FROM roles WHERE slug=?",
                (slug,)).fetchone()
            assert fila is not None, slug
            assert fila["deber"] is None and fila["activo"] == 1, slug
            # Los roles con slug nacen SIN personas: la asignación es
            # dato (pantalla/BD), nunca semilla de código.
            assert con.execute("SELECT 1 FROM rol_persona WHERE rol=?",
                               (fila["n"],)).fetchone() is None, slug


def test_migracion_idempotente_para_los_5(db_limpia):
    datos_roles.iniciar_tablas()
    datos_roles.iniciar_tablas()
    with datos._db() as con:
        for slug in LOS_5:
            assert con.execute("SELECT count(*) c FROM roles WHERE slug=?",
                               (slug,)).fetchone()["c"] == 1, slug


def test_un_rol_creado_a_mano_se_adopta_no_se_duplica(db_limpia):
    """Base vieja: alguien creó «Finanzas» por pantalla antes del slug.
    La migración le estampa el slug a ESA fila (mismo trato que el
    Inventario del BLOQUE 13)."""
    with datos._db() as con:
        con.execute("DELETE FROM roles WHERE slug='finanzas'")
        con.execute("INSERT INTO roles (nombre, activo, creado_en) "
                    "VALUES ('Finanzas', 1, 'x')")
    datos_roles.iniciar_tablas()
    with datos._db() as con:
        filas = con.execute("SELECT nombre FROM roles WHERE slug='finanzas'"
                            ).fetchall()
    assert len(filas) == 1 and filas[0]["nombre"] == "Finanzas"


def test_renombrar_no_suelta_el_slug_en_ninguno_de_los_5(db_limpia):
    """Precisión 5: el slug no es editable — renombrar (la única escritura
    de la fila que ofrece Ajustes) lo deja intacto en los 5."""
    for slug in LOS_5:
        assert datos_roles.renombrar_rol(_rol_n(slug), f"Otro {slug}") is None
        with datos._db() as con:
            fila = con.execute("SELECT nombre, slug FROM roles WHERE n=?",
                               (_rol_n(slug),)).fetchone()
        # _rol_n busca por slug: si el slug se hubiera movido, ya no
        # encontraría la fila. Doble seguro:
        assert fila["slug"] == slug and fila["nombre"] == f"Otro {slug}"


def test_renombrar_por_la_ruta_de_ajustes_tampoco_toca_el_slug(db_limpia):
    seguridad.crear_empleada("jefa", "Jefa", CLAVE)
    seguridad.fijar_admin("jefa", True, "prueba")
    c = TestClient(app)
    assert c.post("/login", data={"usuario": "jefa", "contrasena": CLAVE},
                  follow_redirects=False).status_code == 303
    for slug in LOS_5:
        r = c.post("/ajustes/roles/renombrar",
                   data={"rol": str(_rol_n(slug)), "nombre": f"R-{slug}"},
                   follow_redirects=False)
        assert r.status_code == 303
        with datos._db() as con:
            assert con.execute("SELECT slug FROM roles WHERE n=?",
                               (_rol_n(slug),)).fetchone()["slug"] == slug


def test_duplicar_no_copia_el_slug_en_ninguno_de_los_5(db_limpia):
    for slug in LOS_5:
        error, nuevo = datos_roles.duplicar_rol(_rol_n(slug), f"Copia {slug}")
        assert error is None, slug
        with datos._db() as con:
            assert con.execute("SELECT slug FROM roles WHERE n=?",
                               (nuevo,)).fetchone()["slug"] is None, slug


# ---------------------------------------------------------------------------
# La fuente única: menú y alcance del mismo mapa (precisión 4)
# ---------------------------------------------------------------------------

def test_el_alcance_se_deriva_de_las_mismas_pestanas_del_menu():
    """Cada pestaña del menú de un rol acotado tiene sus prefijos dentro
    del alcance de ese rol: el menú nunca ofrece una puerta cerrada."""
    for slug in ("operaciones", "atencion"):
        prefijos = datos_roles.ALCANCE_DE_ROL[slug]["prefijos"]
        for clave in datos_roles.MENU_DE_ROL[slug]:
            for p in datos_roles.PESTANAS[clave]["prefijos"]:
                assert p in prefijos, (slug, clave, p)
    # Finanzas: sus dos pantallas propias son GET y pasan por ver_todo.
    assert datos_roles.ALCANCE_DE_ROL["finanzas"]["ver_todo"] is True
    assert datos_roles.ALCANCE_DE_ROL["finanzas"]["prefijos"] == ()


def test_la_casa_de_cada_rol_esta_dentro_de_su_alcance():
    """El rebote honesto manda a la casa: si la casa quedara fuera del
    alcance, la puerta la rebotaría otra vez — un loop infinito."""
    for slug, alcance in datos_roles.ALCANCE_DE_ROL.items():
        if alcance is None:
            continue
        if alcance["ver_todo"]:
            continue  # finanzas: ningún GET rebota
        from app.main import _ruta_en_alcance
        assert _ruta_en_alcance(alcance["casa"], alcance["prefijos"]), slug


def test_menus_por_rol_segun_el_diseno():
    """Los 5 menús EXACTOS del BLOQUE 39.1, en su orden."""
    menus = {s: list(datos_roles.MENU_DE_ROL[s]) for s in LOS_5}
    assert menus["director"] == ["calendario", "stock", "crm", "contactos",
                                 "pedidos", "vender", "compras",
                                 "conversaciones", "finanzas", "ajustes"]
    assert menus["director"] == list(datos_roles.MENU_COMPLETO)
    assert menus["operaciones"] == ["calendario", "stock", "crm", "contactos",
                                    "pedidos", "vender", "compras"]
    assert menus["atencion"] == ["calendario", "crm", "contactos", "pedidos",
                                 "vender"]
    # Inventario: SOLO Stock (antes no llevaba menú ninguno).
    assert menus["inventario"] == ["stock"]
    # Finanzas: sus dos pantallas propias, Finanzas primero (lienzo
    # jordan-finanzas). El resto lo abre por ver_todo, SIN entrada.
    assert menus["finanzas"] == ["finanzas", "conversaciones"]
    for sin_ajustes in ("operaciones", "atencion", "inventario", "finanzas"):
        assert "ajustes" not in menus[sin_ajustes], sin_ajustes


def _menu_de(usuario):
    return [(p["clave"], p["href"])
            for p in datos_roles.acceso_de(_empleada(usuario))["menu"]]


def test_el_menu_servido_es_el_del_rol_con_sus_destinos(db_limpia):
    """Lo que acceso_de entrega (clave + href), rol por rol: es LO MISMO
    que pintan _nav.html, _lado.html y el nav de app.html."""
    for slug in LOS_5:
        seguridad.crear_empleada(slug, slug.capitalize(), CLAVE)
        assert datos_roles.poner_persona(_rol_n(slug), slug, "x") is None
    assert _menu_de("director") == [
        ("calendario", "/calendario"), ("stock", "/?tab=stock"),
        ("crm", "/control"), ("contactos", "/contactos"),
        ("pedidos", "/pedidos"), ("vender", "/venta"),
        ("compras", "/compras"), ("conversaciones", "/conversaciones"),
        ("finanzas", "/finanzas"), ("ajustes", "/?tab=ajustes")]
    # Operaciones y Atención: su CRM es el chico (lienzos «Mi CRM»).
    assert _menu_de("operaciones") == [
        ("calendario", "/calendario"), ("stock", "/?tab=stock"),
        ("crm", "/mi-crm"), ("contactos", "/contactos"),
        ("pedidos", "/pedidos"), ("vender", "/venta"),
        ("compras", "/compras")]
    assert _menu_de("atencion") == [
        ("calendario", "/calendario"), ("crm", "/mi-crm"),
        ("contactos", "/contactos"), ("pedidos", "/pedidos"),
        ("vender", "/venta")]
    # Inventario: su Stock es la VISTA PLANA, no la pestaña.
    assert _menu_de("inventario") == [("stock", "/stock")]
    assert _menu_de("finanzas") == [("finanzas", "/finanzas"),
                                    ("conversaciones", "/conversaciones")]


# ---------------------------------------------------------------------------
# La matriz por slug: rutas × métodos (precisiones 3 y 9, BLOQUE 22.7)
# ---------------------------------------------------------------------------

def _rebote(respuesta):
    """(ruta de destino, el aviso que viaja) de un rebote honesto."""
    destino = respuesta.headers.get("location") or ""
    ruta, _, query = destino.partition("?")
    return ruta, unquote(query[len("rebote="):]) if query.startswith("rebote=") else ""


def test_matriz_operaciones(db_limpia, con_inventario):
    c = _con_roles("opera", "operaciones")
    casa = "/mi-crm"
    # GET/HEAD dentro del alcance: pasan la puerta y la ruta responde.
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.get("/?tab=stock", follow_redirects=False).status_code == 200
    assert c.get("/mi-crm", follow_redirects=False).status_code == 200
    # El CRM completo sigue abierto para quien supervisa.
    assert c.get("/control", follow_redirects=False).status_code == 200
    # /stock no es su vista plana: el handler lo manda a la pestaña.
    r = c.get("/stock", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?tab=stock")
    # GET/HEAD fuera del alcance: 303 a su casa CON el aviso honesto
    # (BLOQUE 39.3) — nunca un 403 pelado en un clic del menú.
    for ruta in ("/resumen", "/equipo", "/finanzas",
                 "/conversaciones/respuestas", "/revisar"):
        r = c.get(ruta, follow_redirects=False)
        destino, aviso = _rebote(r)
        assert (r.status_code, destino) == (303, casa), ruta
        # Las que SON pestaña se nombran; /revisar no es pestaña de nadie
        # y sale con el aviso genérico, igual de honesto.
        assert ("tu rol no la usa" in aviso
                if ruta != "/revisar" else "no es de tu rol" in aviso), ruta
        r = c.request("HEAD", ruta, follow_redirects=False)
        assert (r.status_code, _rebote(r)[0]) == (303, casa), ruta
    # Y la casa PINTA el aviso: el rebote no se pierde por el camino.
    assert "tu rol no la usa" in c.get("/resumen").text
    # Escrituras dentro del alcance: pasan la puerta (404 = llegó al
    # router sin efecto; prueba que la puerta no corta lo suyo).
    assert c.post("/venta/no-existe").status_code == 404
    assert c.post("/compras/no-existe").status_code == 404
    # Escrituras fuera: 403 en el servidor, con TODOS los métodos.
    for ruta in ("/ajustes/invitar", "/finanzas/confirmar", "/avisos/prueba"):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            r = c.request(metodo, ruta)
            assert r.status_code == 403, (ruta, metodo)
            assert "Tu rol no permite" in r.text, (ruta, metodo)


def test_matriz_atencion(db_limpia, con_inventario):
    c = _con_roles("atenta", "atencion")
    casa = "/mi-crm"
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.get("/mi-crm", follow_redirects=False).status_code == 200
    # SIN stock, compras ni ajustes: ni el tablero de "/" ni /compras.
    for ruta in ("/?tab=stock", "/?tab=ajustes", "/compras", "/stock",
                 "/resumen", "/finanzas"):
        r = c.get(ruta, follow_redirects=False)
        destino, aviso = _rebote(r)
        assert (r.status_code, destino) == (303, casa), ruta
        assert aviso, ruta
    # El aviso NOMBRA la pestaña, también cuando llega por ?tab=.
    assert "Stock" in _rebote(c.get("/?tab=stock",
                                    follow_redirects=False))[1]
    assert c.post("/venta/no-existe").status_code == 404
    for ruta in ("/ajustar", "/compras/borrador", "/ajustes/invitar",
                 "/productos/crear"):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            assert c.request(metodo, ruta).status_code == 403, (ruta, metodo)


def test_atencion_ve_todos_en_solo_lectura(db_limpia, con_inventario,
                                           monkeypatch):
    """BLOQUE 39.2 (cambio de candado AUTORIZADO por el dueño): el CRM
    completo le queda a Atención como vista secundaria de SOLO LECTURA —
    el GET abre el tablero entero, la escritura sobre un lead AJENO es
    403 duro (no el redirect con error de siempre) y el chat de una ficha
    ajena no se pinta."""
    from app import linear_leads
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    c = _con_roles("atenta4", "atencion")
    # 1) El tablero completo ABRE, y la pantalla DICE que es de lectura.
    r = c.get("/control", follow_redirects=False)
    assert r.status_code == 200
    assert "solo lectura de lo ajeno" in r.text
    # Ve de verdad los leads de los demás (LEAD-91 es de Ruben).
    assert "Tamara" in r.text
    # Y tiene cómo volver a lo suyo.
    assert 'href="/mi-crm"' in r.text
    # 2) La ficha ajena abre, pero su chat NO se muestra: lo dice.
    cuerpo = c.get("/control?abrir=LEAD-91").text
    assert "de otro responsable" in cuerpo
    # 3) Y moverla es 403 DURO, con texto claro — no un redirect con error.
    r = c.post("/control/estado",
               data={"ref": "LEAD-91", "estado": "hablando", "vista": "estado"},
               follow_redirects=False)
    assert r.status_code == 403
    assert "solo lectura" in r.text


def test_atencion_si_mueve_lo_suyo(db_limpia, con_inventario, monkeypatch):
    """El otro lado: la lectura es sobre lo AJENO. Lo propio se mueve
    igual que siempre — si no, «Ver todos» habría apretado un permiso."""
    from app import agenda, linear_leads
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    linear_leads.reiniciar_muestra()
    monkeypatch.setattr(agenda, "responsable_de_empleada", lambda e: "Ruben")
    c = _con_roles("atenta5", "atencion")
    cuerpo = c.get("/control?abrir=LEAD-91").text
    assert "de otro responsable" not in cuerpo
    r = c.post("/control/estado",
               data={"ref": "LEAD-91", "estado": "hablando", "vista": "estado"},
               follow_redirects=False)
    assert r.status_code == 303


def test_matriz_finanzas_ve_todo_y_no_escribe_nada(db_limpia, con_inventario,
                                                   ajustes_registrados):
    c = _con_roles("fin", "finanzas")
    # TODOS los GET pasan: modo ver de verdad (abre fichas y pestañas).
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.get("/?tab=stock", follow_redirects=False).status_code == 200
    # Sus dos pantallas propias, por ROL (V2: ya no por admin).
    assert c.get("/finanzas", follow_redirects=False).status_code == 200
    assert c.get("/conversaciones", follow_redirects=False).status_code == 200
    assert c.get("/conversaciones/respuestas",
                 follow_redirects=False).status_code == 200
    assert c.get("/revisar", follow_redirects=False).status_code == 200
    r = c.get("/stock", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?tab=stock")
    # Ajustes NO: la pestaña de "/" rebota a SU casa con el aviso.
    r = c.get("/?tab=ajustes", follow_redirects=False)
    assert (r.status_code, _rebote(r)[0]) == (303, "/finanzas")
    # TODA escritura es 403 — también bajo /finanzas (sin lista blanca de
    # POST hasta el sí de Jay, BLOQUE 22.1) — con todos los métodos.
    for ruta in ("/ajustar", "/venta/lead", "/control/nota",
                 "/finanzas/confirmar", "/ajustes/invitar",
                 "/ajustes/roles/renombrar", "/revisar/nota",
                 "/calendario/actividad"):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            r = c.request(metodo, ruta, json={"sku": "PL-ROMERO",
                                              "cantidad": 7, "esperada": 2})
            assert r.status_code == 403, (ruta, metodo)
            assert "solo ver" in r.text, (ruta, metodo)
    # Y de verdad no pasó nada: ni un ajuste viajó al order-api.
    assert ajustes_registrados == []


def test_matriz_inventario_encerrado_en_su_stock(db_limpia):
    """Mismo encierro del BLOQUE 13: el redirect lleva a /stock (ahora con
    su aviso) y el 403 conserva su texto de siempre."""
    c = _con_roles("omar2", "inventario")
    r = c.get("/venta", follow_redirects=False)
    destino, aviso = _rebote(r)
    assert (r.status_code, destino) == (303, "/stock")
    assert "Vender" in aviso
    assert c.get("/stock", follow_redirects=False).status_code == 200
    r = c.post("/ajustar", json={"sku": "PL-X", "cantidad": 1, "esperada": 1})
    assert r.status_code == 403 and "solo de inventario" in r.text
    for metodo in ("PUT", "PATCH", "DELETE"):
        assert c.request(metodo, "/venta/lead").status_code == 403, metodo


def test_matriz_director_sin_puerta(db_limpia):
    c = _con_roles("dire", "director")
    assert c.get("/venta", follow_redirects=False).status_code == 200
    # La puerta no corta nada, y los candados propios de cada pantalla
    # siguen mandando: /finanzas y la supervisión abren para el Director.
    assert c.get("/finanzas", follow_redirects=False).status_code == 200
    assert c.get("/conversaciones", follow_redirects=False).status_code == 200
    assert c.get("/revisar", follow_redirects=False).status_code == 200
    assert c.get("/stock/cambios", follow_redirects=False).status_code == 200
    assert c.post("/venta/no-existe").status_code == 404
    # Lo TÉCNICO sigue siendo del admin, aunque sea el Director.
    r = c.post("/ajustes/invitar", data={"email": "a@b.co"})
    assert r.status_code == 403 and "Solo para administradores" in r.text


def test_logout_pasa_para_los_5_roles(db_limpia):
    for i, slug in enumerate(LOS_5):
        c = _con_roles(f"sale{i}", slug)
        r = c.post("/logout", follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/login"), slug


def test_sin_rol_sigue_como_hoy_fail_open_explicito(cliente):
    """Precisión 8: «sin rol = como hoy» es un FAIL-OPEN de transición,
    fijado aquí como decisión explícita (y va visible en el aviso de
    prueba a Abraham). Mientras una empleada no tenga rol asignado, la
    puerta no la corta: empleada completa, como antes del punto 1."""
    assert cliente.get("/venta", follow_redirects=False).status_code == 200
    # Ni redirect a una casa ni 403 DE LA PUERTA: la ruta llega a su
    # handler, y el 403 que devuelve /finanzas es su candado propio
    # (nace cerrada, BLOQUE 22.7) — no un corte por rol.
    r = cliente.get("/finanzas", follow_redirects=False)
    assert r.status_code == 403 and "cola de pagos" in r.text
    assert cliente.post("/venta/no-existe").status_code == 404


def test_un_rol_sin_slug_mantiene_abierta_la_puerta(db_limpia):
    """El fail-open de hoy también cubre los roles-espacio (pods como
    Eventos): atención + un pod navega como hoy — la puerta acotada es de
    quien SOLO tiene roles con alcance. (Mismo trato que inventario+otro
    rol en el BLOQUE 13.)"""
    with datos._db() as con:
        pod = con.execute("SELECT n FROM roles WHERE slug IS NULL "
                          "AND activo=1 LIMIT 1").fetchone()["n"]
    seguridad.crear_empleada("mixta2", "Mixta2", CLAVE)
    datos_roles.poner_persona(_rol_n("atencion"), "mixta2", "x")
    datos_roles.poner_persona(pod, "mixta2", "x")
    c = TestClient(app)
    assert c.post("/login", data={"usuario": "mixta2", "contrasena": CLAVE},
                  follow_redirects=False).status_code == 303
    # Con atención sola, GET /finanzas rebotaría 303 a su casa; con el
    # pod al lado la puerta queda abierta y la ruta llega a su handler
    # (cuyo candado propio responde 403: ni rol de finanzas ni admin).
    r = c.get("/finanzas", follow_redirects=False)
    assert r.status_code == 403 and "cola de pagos" in r.text
    assert c.post("/venta/no-existe").status_code == 404


# ---------------------------------------------------------------------------
# V2: el rol manda aunque seas admin (BLOQUE 29 + ANALISIS-rol-manda-sobre-admin)
# ---------------------------------------------------------------------------

def _admin_con(usuario, *slugs):
    c = _con_roles(usuario, *slugs)
    seguridad.fijar_admin(usuario, True, "prueba")
    return c


def test_el_admin_ya_no_pasa_por_ser_admin(db_limpia, con_inventario):
    """La excepción global murió: un admin con rol restrictivo queda
    acotado como cualquiera. Lo que conserva es el TIMÓN (abajo)."""
    c = _admin_con("admin_inv", "inventario")
    r = c.get("/venta", follow_redirects=False)
    assert (r.status_code, _rebote(r)[0]) == (303, "/stock")
    r = c.post("/ajustar", json={"sku": "PL-ROMERO", "cantidad": 7,
                                 "esperada": 2})
    assert r.status_code == 403


def test_ajustes_siempre_pasa_para_un_admin_con_cualquier_rol(
        db_limpia, con_inventario):
    """LA celda obligatoria del análisis (riesgo «encierro de
    emergencia»): admin + CADA rol → la pestaña Ajustes abre. De ahí se
    quita el rol mal puesto; sin esto, un rol mal asignado dejaría al
    dueño sin timón."""
    for i, slug in enumerate(LOS_5):
        c = _admin_con(f"jefa{i}", slug)
        r = c.get("/?tab=ajustes", follow_redirects=False)
        assert r.status_code == 200, slug
        # Y el panel de negocio (roles) viene servido, no vacío.
        assert "Roles y catálogos de venta" in r.text, slug
        # El menú se lo OFRECE, no hay que adivinar la URL (en Inicio la
        # entrada Ajustes es el botón de pestaña, no un enlace).
        assert 'data-tab="ajustes"' in r.text, slug
        # Y las rutas de sistema tampoco lo rebotan.
        for ruta in ("/ajustes", "/avisos", "/equipo", "/resumen"):
            assert c.get(ruta, follow_redirects=False).status_code != 303, (
                slug, ruta)


def test_el_admin_encerrado_sigue_viendo_su_salida_en_la_vista_plana(
        db_limpia, con_inventario):
    """El timón tiene que estar A LA VISTA: la vista plana del rol
    Inventario le ofrece Ajustes al admin (y a nadie más)."""
    c = _admin_con("jefa_inv", "inventario")
    assert 'href="/?tab=ajustes"' in c.get("/stock").text
    assert 'href="/?tab=ajustes"' not in _con_roles(
        "omar3", "inventario").get("/stock").text


# Las 9 rutas de NEGOCIO que pasan de `_solo_admin` a «admin O director»
# (inventario del análisis, punto 4) + /stock/cambios.
NEGOCIO = [
    ("/ajustes/roles/renombrar", {"rol": "1", "nombre": "Z"}),
    ("/ajustes/roles/duplicar", {"rol": "1", "nombre": "Copia"}),
    ("/ajustes/roles/persona/poner", {"rol": "1", "usuario": "dire2"}),
    ("/ajustes/roles/persona/quitar", {"rol": "1", "usuario": "dire2"}),
    ("/ajustes/deberes", {"deber": "system_manager", "rol": "1"}),
    ("/ajustes/catalogo/agregar", {"tabla": "marcas", "nombre": "M"}),
    ("/ajustes/catalogo/renombrar", {"tabla": "marcas", "n": "1",
                                     "nombre": "M2"}),
    ("/ajustes/catalogo/activar", {"tabla": "marcas", "n": "1",
                                   "activo": "0"}),
    ("/ajustes/tipos/termino", {"tipo": "1", "termino": "30 días"}),
]

# Las 8 TÉCNICAS que NO se mueven: siguen siendo solo-admin.
TECNICAS = [
    ("/ajustes/invitar", {"email": "a@b.co"}),
    ("/ajustes/invitacion/cancelar", {"token": "x"}),
    ("/ajustes/revocar", {"usuario": "x"}),
    ("/ajustes/admin", {"usuario": "x", "dar": "1"}),
    ("/ajustes/coworkers/agregar", {"numero": "60000000"}),
    ("/ajustes/coworkers/quitar", {"numero": "60000000"}),
    ("/ajustes/envio", {}),
    ("/ajustes/dispositivos/nombrar", {"dispositivo": "3", "nombre": "X"}),
]


def test_las_9_de_negocio_las_abre_el_director_sin_ser_admin(db_limpia):
    """BLOQUE 28: el Director reparte roles y edita los catálogos del
    negocio SIN ser admin. Éxito = 303 de vuelta a la pestaña (nunca un
    403), y la ruta de verdad corrió."""
    c = _con_roles("dire2", "director")
    for ruta, datos_form in NEGOCIO:
        r = c.post(ruta, data=datos_form, follow_redirects=False)
        assert r.status_code == 303, ruta
        assert r.headers["location"].startswith("/?tab=ajustes"), ruta
    # /stock/cambios: la auditoría que el Director necesita para
    # supervisar a Inventario.
    assert c.get("/stock/cambios", follow_redirects=False).status_code == 200


def test_las_8_tecnicas_siguen_siendo_del_admin(db_limpia):
    """El Director NO invita, no revoca, no hace admins, no toca
    coworkers, envío ni dispositivos: eso es sistema."""
    c = _con_roles("dire3", "director")
    for ruta, datos_form in TECNICAS:
        r = c.post(ruta, data=datos_form, follow_redirects=False)
        assert r.status_code == 403, ruta
        assert "Solo para administradores" in r.text, ruta


def test_las_9_de_negocio_le_cierran_a_quien_no_es_ni_admin_ni_director(
        db_limpia):
    """Una empleada sin rol que acote llega al candado (la puerta no la
    corta: fail-open) y es ahí donde recibe el 403 con su texto."""
    seguridad.crear_empleada("suelta", "Suelta", CLAVE)
    c = TestClient(app)
    assert c.post("/login", data={"usuario": "suelta", "contrasena": CLAVE},
                  follow_redirects=False).status_code == 303
    for ruta, datos_form in NEGOCIO:
        r = c.post(ruta, data=datos_form, follow_redirects=False)
        assert r.status_code == 403, ruta
        assert "rol Director" in r.text, ruta
    assert c.get("/stock/cambios", follow_redirects=False).status_code == 403


def test_los_otros_roles_tampoco_entran_a_las_de_negocio(db_limpia):
    """Operaciones, Atención, Inventario y Finanzas: 403 en las 9 — cada
    uno con el 403 que le toca (la puerta por rol para los tres primeros,
    el candado de solo-ver para Finanzas)."""
    for i, slug in enumerate(("operaciones", "atencion", "inventario",
                              "finanzas")):
        c = _con_roles(f"nope{i}", slug)
        for ruta, datos_form in NEGOCIO:
            r = c.post(ruta, data=datos_form, follow_redirects=False)
            assert r.status_code == 403, (slug, ruta)


def test_la_supervision_es_de_director_y_finanzas(db_limpia):
    """V2: /conversaciones, /revisar y POST /revisar/nota dejan de ser
    «solo admin» y pasan a los dos roles que supervisan."""
    for slug in ("director", "finanzas"):
        c = _con_roles(f"sup_{slug}", slug)
        for ruta in ("/conversaciones", "/conversaciones/respuestas",
                     "/revisar"):
            assert c.get(ruta, follow_redirects=False).status_code == 200, (
                slug, ruta)
    # Operaciones y Atención: ni por URL. (Las rebota la puerta antes.)
    for slug in ("operaciones", "atencion"):
        c = _con_roles(f"nosup_{slug}", slug)
        for ruta in ("/conversaciones", "/revisar"):
            r = c.get(ruta, follow_redirects=False)
            assert r.status_code == 303, (slug, ruta)


def test_el_admin_sin_rol_conserva_la_supervision_fail_open(db_limpia):
    """EXCEPCIÓN DE TRANSICIÓN, documentada: un admin cuyo alcance es
    None (sin rol, o solo pods sin slug) sigue viendo la supervisión.
    Cuando el fail-open muera, esta celda muere con él."""
    seguridad.crear_empleada("jefa_sr", "Jefa SR", CLAVE)
    seguridad.fijar_admin("jefa_sr", True, "prueba")
    c = TestClient(app)
    assert c.post("/login", data={"usuario": "jefa_sr", "contrasena": CLAVE},
                  follow_redirects=False).status_code == 303
    assert datos_roles.acceso_de(_empleada("jefa_sr"))["alcance"] is None
    for ruta in ("/conversaciones", "/conversaciones/respuestas", "/revisar",
                 "/stock/cambios"):
        assert c.get(ruta, follow_redirects=False).status_code == 200, ruta
    assert c.post("/revisar/nota", data={"orden": "S00001", "nota": "x"},
                  follow_redirects=False).status_code in (303, 404)


def test_un_admin_con_rol_de_atencion_no_recupera_la_supervision(db_limpia):
    """El otro lado de la misma moneda: con un rol que acota, el admin
    NO vuelve a ver lo que su rol no usa — solo Ajustes y sistema."""
    c = _admin_con("jefa_at", "atencion")
    for ruta in ("/conversaciones", "/revisar", "/finanzas"):
        r = c.get(ruta, follow_redirects=False)
        assert (r.status_code, _rebote(r)[0]) == (303, "/mi-crm"), ruta
    assert c.get("/?tab=ajustes", follow_redirects=False).status_code == 200


# ---------------------------------------------------------------------------
# Varios roles: la UNIÓN y la casa por prioridad (precisión 4)
# ---------------------------------------------------------------------------

def test_union_operaciones_mas_inventario(db_limpia, con_inventario):
    c = _con_roles("union1", "operaciones", "inventario")
    # Alcance: la unión — lo de operaciones Y /stock.
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.post("/venta/no-existe").status_code == 404
    # Casa: la de operaciones (/mi-crm), por PRIORIDAD_CASA — nunca la
    # vista plana de inventario.
    r = c.get("/resumen", follow_redirects=False)
    assert (r.status_code, _rebote(r)[0]) == (303, "/mi-crm")


def test_union_atencion_mas_finanzas(db_limpia, con_inventario):
    c = _con_roles("union2", "atencion", "finanzas")
    # Los GET de finanzas abren todo…
    assert c.get("/?tab=stock", follow_redirects=False).status_code == 200
    # …y las escrituras de atención siguen vivas donde le tocan…
    assert c.post("/venta/no-existe").status_code == 404
    # …pero fuera de la unión sigue el 403.
    for metodo in ("POST", "PUT", "PATCH", "DELETE"):
        assert c.request(metodo, "/ajustar").status_code == 403, metodo


def test_union_menu_y_casa(db_limpia):
    seguridad.crear_empleada("union3", "Unión3", CLAVE)
    datos_roles.poner_persona(_rol_n("atencion"), "union3", "x")
    datos_roles.poner_persona(_rol_n("finanzas"), "union3", "x")
    acceso = datos_roles.acceso_de(_empleada("union3"))
    claves = [p["clave"] for p in acceso["menu"]]
    # Con VARIOS roles la unión sale en el orden del diseño
    # (MENU_COMPLETO), no en el de los roles.
    assert claves == ["calendario", "crm", "contactos", "pedidos", "vender",
                      "conversaciones", "finanzas"]
    assert acceso["alcance"]["casa"] == "/mi-crm"
    assert acceso["alcance"]["ver_todo"] is True
    # Prioridad de casa estable: inventario solo -> /stock; con
    # operaciones delante -> /mi-crm.
    seguridad.crear_empleada("union4", "Unión4", CLAVE)
    datos_roles.poner_persona(_rol_n("inventario"), "union4", "x")
    assert datos_roles.acceso_de(_empleada("union4"))["alcance"]["casa"] == "/stock"
    datos_roles.poner_persona(_rol_n("operaciones"), "union4", "x")
    assert datos_roles.acceso_de(_empleada("union4"))["alcance"]["casa"] == "/mi-crm"


# ---------------------------------------------------------------------------
# El menú en el HTML: la MISMA lista en las tres caras del nav
# ---------------------------------------------------------------------------

def test_nav_de_atencion(db_limpia):
    nav = _nav_de(_con_roles("atenta3", "atencion").get("/venta").text)
    for enlace in ('href="/calendario"', 'href="/venta"', 'href="/mi-crm"',
                   'href="/contactos"', 'href="/pedidos"'):
        assert enlace in nav, enlace
    for enlace in ('href="/compras"', 'href="/?tab=stock"',
                   'href="/?tab=ajustes"', 'href="/finanzas"',
                   'href="/conversaciones"'):
        assert enlace not in nav, enlace


def test_nav_de_operaciones(db_limpia):
    nav = _nav_de(_con_roles("opera2", "operaciones").get("/venta").text)
    for enlace in ('href="/?tab=stock"', 'href="/compras"', 'href="/mi-crm"',
                   'href="/contactos"'):
        assert enlace in nav, enlace
    assert 'href="/?tab=ajustes"' not in nav
    assert 'href="/finanzas"' not in nav


def test_nav_de_finanzas(db_limpia):
    nav = _nav_de(_con_roles("fin2", "finanzas").get("/venta").text)
    for enlace in ('href="/finanzas"', 'href="/conversaciones"'):
        assert enlace in nav, enlace
    # Sus pantallas de ver NO son entradas del menú (BLOQUE 39.1).
    for enlace in ('href="/venta"', 'href="/control"', 'href="/?tab=stock"',
                   'href="/?tab=ajustes"'):
        assert enlace not in nav, enlace
    # Finanzas abre su menú, no Conversaciones (lienzo jordan-finanzas).
    assert nav.index('href="/finanzas"') < nav.index('href="/conversaciones"')


def test_nav_completo_para_director_y_sin_rol(db_limpia):
    for c in (_con_roles("dire4", "director"), _con_roles("libre2")):
        nav = _nav_de(c.get("/venta").text)
        for enlace in ('href="/calendario"', 'href="/?tab=stock"',
                       'href="/venta"', 'href="/control"', 'href="/contactos"',
                       'href="/pedidos"', 'href="/compras"',
                       'href="/conversaciones"', 'href="/finanzas"',
                       'href="/?tab=ajustes"'):
            assert enlace in nav, enlace
        # El CRM del Director es el completo, no el chico.
        assert 'href="/mi-crm"' not in nav


def test_el_mismo_menu_en_todas_las_caras_del_nav(db_limpia, con_inventario):
    """BLOQUE 37 (brecha B1): el costado de las pantallas de tablero
    (_lado.html), el cajón/sidebar de base.html (_nav.html) y el nav
    propio de Inicio (app.html) recorren la MISMA lista de Python."""
    c = _con_roles("opera3", "operaciones")
    for pagina in ("/venta", "/control", "/calendario", "/compras", "/mi-crm"):
        cuerpo = c.get(pagina).text
        assert 'href="/contactos"' in cuerpo, pagina
        # Y ninguna cara ofrece lo que su rol no puede abrir.
        assert 'href="/?tab=ajustes"' not in cuerpo, pagina
        assert 'href="/finanzas"' not in cuerpo, pagina
    # El de Inicio (app.html) es el mismo, con Stock y Ajustes como
    # botones de pestaña (lo que app.js ya sabe manejar).
    inicio = c.get("/?tab=stock").text
    assert 'data-tab="stock"' in inicio
    assert 'data-tab="ajustes"' not in inicio
    assert 'href="/contactos"' in inicio


def test_el_pie_del_costado_dice_nombre_y_rol(db_limpia):
    cuerpo = _con_roles("pie1", "operaciones").get("/control").text
    assert "Pie1" in cuerpo
    # El rótulo es el nombre VISIBLE de la fila (renombrable), no el slug.
    with datos._db() as con:
        nombre = con.execute("SELECT nombre FROM roles WHERE slug='operaciones'"
                             ).fetchone()["nombre"]
    assert nombre in cuerpo


# ---------------------------------------------------------------------------
# La bitácora de asignaciones (precisión 7): INSERT-only, epoch UTC
# ---------------------------------------------------------------------------

def _bitacora():
    with datos._db() as con:
        return [dict(f) for f in con.execute(
            "SELECT rol, usuario, accion, por, epoch "
            "FROM rol_persona_bitacora ORDER BY n")]


def test_poner_y_quitar_quedan_auditados(db_limpia):
    seguridad.crear_empleada("ana", "Ana", CLAVE)
    antes = len(_bitacora())
    assert datos_roles.poner_persona(_rol_n("atencion"), "ana", "Korto") is None
    # Re-ponerla es idempotente: NO ensucia la bitácora.
    assert datos_roles.poner_persona(_rol_n("atencion"), "ana", "Korto") is None
    assert datos_roles.quitar_persona(_rol_n("atencion"), "ana",
                                      por="Korto") is None
    # Quitar a quien ya no está tampoco anota nada.
    assert datos_roles.quitar_persona(_rol_n("atencion"), "ana",
                                      por="Korto") is None
    filas = _bitacora()[antes:]
    assert [(f["accion"], f["usuario"], f["por"]) for f in filas] == [
        ("alta", "ana", "Korto"), ("baja", "ana", "Korto")]
    # Nada se actualiza ni se borra: la fila del alta sigue ahí después
    # de la baja, y el cuándo es epoch UTC en segundos (entero).
    for f in filas:
        assert isinstance(f["epoch"], int) and f["epoch"] > 1_700_000_000


def test_la_pantalla_de_ajustes_registra_en_la_bitacora(db_limpia):
    seguridad.crear_empleada("jefa2", "Jefa Dos", CLAVE)
    seguridad.fijar_admin("jefa2", True, "prueba")
    seguridad.crear_empleada("bea", "Bea", CLAVE)
    c = TestClient(app)
    assert c.post("/login", data={"usuario": "jefa2", "contrasena": CLAVE},
                  follow_redirects=False).status_code == 303
    antes = len(_bitacora())
    assert c.post("/ajustes/roles/persona/poner",
                  data={"rol": str(_rol_n("operaciones")), "usuario": "bea"},
                  follow_redirects=False).status_code == 303
    assert c.post("/ajustes/roles/persona/quitar",
                  data={"rol": str(_rol_n("operaciones")), "usuario": "bea"},
                  follow_redirects=False).status_code == 303
    filas = _bitacora()[antes:]
    assert [(f["accion"], f["usuario"]) for f in filas] == [
        ("alta", "bea"), ("baja", "bea")]
    # Quién hizo el cambio: el nombre de la sesión, nunca un invento.
    assert all(f["por"] == "Jefa Dos" for f in filas)


def test_duplicar_un_rol_audita_las_altas_copiadas(db_limpia):
    seguridad.crear_empleada("cata", "Cata", CLAVE)
    assert datos_roles.poner_persona(_rol_n("atencion"), "cata", "x") is None
    antes = len(_bitacora())
    error, nuevo = datos_roles.duplicar_rol(_rol_n("atencion"), "Pod B",
                                            por="Korto")
    assert error is None
    filas = [f for f in _bitacora()[antes:] if f["rol"] == nuevo]
    assert [(f["accion"], f["usuario"], f["por"]) for f in filas] == [
        ("alta", "cata", "Korto")]
