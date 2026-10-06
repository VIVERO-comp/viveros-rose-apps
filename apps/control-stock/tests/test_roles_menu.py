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

La excepción del ADMIN (hoy un admin pasa siempre) quedó COMO ESTABA, en
el punto único main._puerta_por_rol — BLOQUE 29 aprobado: quitarla es la
v2, un ciclo aparte.
"""

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


def test_menus_por_rol_segun_el_diseno():
    menus = {s: [c for c in datos_roles.MENU_DE_ROL[s]] for s in LOS_5}
    assert menus["director"] == list(datos_roles.MENU_COMPLETO)
    assert menus["operaciones"] == ["calendario", "stock", "vender",
                                    "control", "mi_crm", "pedidos", "compras"]
    assert menus["atencion"] == ["calendario", "vender", "control",
                                 "mi_crm", "pedidos"]
    assert menus["inventario"] == []
    # Finanzas sin Ajustes (precisión 10) y con sus dos pantallas.
    assert menus["finanzas"][:2] == ["finanzas", "respuestas"]
    assert "ajustes" not in menus["finanzas"]
    assert "ajustes" not in menus["operaciones"]
    assert "ajustes" not in menus["atencion"]


# ---------------------------------------------------------------------------
# La matriz por slug: rutas × métodos (precisiones 3 y 9, BLOQUE 22.7)
# ---------------------------------------------------------------------------

def test_matriz_operaciones(db_limpia, con_inventario):
    c = _con_roles("opera", "operaciones")
    casa = "/control"
    # GET/HEAD dentro del alcance: pasan la puerta (200, o 404 si la ruta
    # aún no existe — /mi-crm la construye otra rama).
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.get("/?tab=stock", follow_redirects=False).status_code == 200
    assert c.get("/mi-crm", follow_redirects=False).status_code == 404
    # /stock no es su vista plana: el handler lo manda a la pestaña.
    r = c.get("/stock", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?tab=stock")
    # GET/HEAD fuera del alcance: 303 a su casa.
    for ruta in ("/resumen", "/equipo", "/finanzas",
                 "/conversaciones/respuestas", "/revisar"):
        r = c.get(ruta, follow_redirects=False)
        assert (r.status_code, r.headers.get("location")) == (303, casa), ruta
        r = c.request("HEAD", ruta, follow_redirects=False)
        assert (r.status_code, r.headers.get("location")) == (303, casa), ruta
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
    casa = "/control"
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.get("/mi-crm", follow_redirects=False).status_code == 404
    # SIN stock, compras ni ajustes: ni el tablero de "/" ni /compras.
    for ruta in ("/?tab=stock", "/?tab=ajustes", "/compras", "/stock",
                 "/resumen", "/finanzas"):
        r = c.get(ruta, follow_redirects=False)
        assert (r.status_code, r.headers.get("location")) == (303, casa), ruta
    assert c.post("/venta/no-existe").status_code == 404
    for ruta in ("/ajustar", "/compras/borrador", "/ajustes/invitar",
                 "/productos/crear"):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            assert c.request(metodo, ruta).status_code == 403, (ruta, metodo)


def test_matriz_finanzas_ve_todo_y_no_escribe_nada(db_limpia, con_inventario,
                                                   ajustes_registrados):
    c = _con_roles("fin", "finanzas")
    # TODOS los GET pasan: modo ver de verdad (abre fichas y pestañas).
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.get("/?tab=stock", follow_redirects=False).status_code == 200
    assert c.get("/finanzas", follow_redirects=False).status_code == 404
    assert c.get("/conversaciones/respuestas",
                 follow_redirects=False).status_code == 404
    r = c.get("/stock", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/?tab=stock")
    # TODA escritura es 403 — también bajo /finanzas (sin lista blanca de
    # POST hasta el sí de Jay, BLOQUE 22.1) — con todos los métodos.
    for ruta in ("/ajustar", "/venta/lead", "/control/nota",
                 "/finanzas/confirmar", "/ajustes/invitar",
                 "/calendario/actividad"):
        for metodo in ("POST", "PUT", "PATCH", "DELETE"):
            r = c.request(metodo, ruta, json={"sku": "PL-ROMERO",
                                              "cantidad": 7, "esperada": 2})
            assert r.status_code == 403, (ruta, metodo)
            assert "solo ver" in r.text, (ruta, metodo)
    # Y de verdad no pasó nada: ni un ajuste viajó al order-api.
    assert ajustes_registrados == []


def test_matriz_inventario_identica_al_bloque_13(db_limpia):
    """La generalización no le cambia ni un pelo al rol de Omar: mismo
    redirect, mismo 403 con su mismo texto (el resto vive en
    test_rol_inventario.py, que no se tocó)."""
    c = _con_roles("omar2", "inventario")
    r = c.get("/venta", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/stock")
    r = c.post("/ajustar", json={"sku": "PL-X", "cantidad": 1, "esperada": 1})
    assert r.status_code == 403 and "solo de inventario" in r.text
    for metodo in ("PUT", "PATCH", "DELETE"):
        assert c.request(metodo, "/venta/lead").status_code == 403, metodo


def test_matriz_director_sin_puerta(db_limpia):
    c = _con_roles("dire", "director")
    assert c.get("/venta", follow_redirects=False).status_code == 200
    # La puerta no corta nada: una ruta inexistente llega al router (404),
    # y los candados propios de cada pantalla siguen mandando (el 403 de
    # /ajustes/invitar es de _solo_admin, no de la puerta por rol).
    assert c.get("/finanzas", follow_redirects=False).status_code == 404
    assert c.post("/venta/no-existe").status_code == 404
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
    # Ni redirect a una casa ni 403 de rol: la ruta inexistente llega al
    # router tal cual.
    assert cliente.get("/finanzas", follow_redirects=False).status_code == 404
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
    # Con atención sola, GET /finanzas rebotaría 303 a /control; con el
    # pod al lado la puerta queda abierta y la ruta llega al router.
    assert c.get("/finanzas", follow_redirects=False).status_code == 404
    assert c.post("/venta/no-existe").status_code == 404


# ---------------------------------------------------------------------------
# Varios roles: la UNIÓN y la casa por prioridad (precisión 4)
# ---------------------------------------------------------------------------

def test_union_operaciones_mas_inventario(db_limpia, con_inventario):
    c = _con_roles("union1", "operaciones", "inventario")
    # Alcance: la unión — lo de operaciones Y /stock.
    assert c.get("/venta", follow_redirects=False).status_code == 200
    assert c.post("/venta/no-existe").status_code == 404
    # Casa: la de operaciones (/control), por PRIORIDAD_CASA — nunca la
    # vista plana de inventario.
    r = c.get("/resumen", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/control")


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
    # Primero el menú de atención (primer rol en PRIORIDAD_CASA), después
    # lo que agrega finanzas, sin repetir.
    assert claves == ["calendario", "vender", "control", "mi_crm", "pedidos",
                      "finanzas", "respuestas", "stock", "compras"]
    assert acceso["alcance"]["casa"] == "/control"
    assert acceso["alcance"]["ver_todo"] is True
    # Prioridad de casa estable: inventario solo -> /stock; con
    # operaciones delante -> /control.
    seguridad.crear_empleada("union4", "Unión4", CLAVE)
    datos_roles.poner_persona(_rol_n("inventario"), "union4", "x")
    assert datos_roles.acceso_de(_empleada("union4"))["alcance"]["casa"] == "/stock"
    datos_roles.poner_persona(_rol_n("operaciones"), "union4", "x")
    assert datos_roles.acceso_de(_empleada("union4"))["alcance"]["casa"] == "/control"


# ---------------------------------------------------------------------------
# El menú en el HTML del nav (las rutas nuevas NO se siguen: las está
# construyendo otra rama — aquí solo el enlace y la puerta)
# ---------------------------------------------------------------------------

def test_nav_de_atencion(db_limpia):
    nav = _nav_de(_con_roles("atenta2", "atencion").get("/venta").text)
    for enlace in ('href="/calendario"', 'href="/venta"', 'href="/control"',
                   'href="/mi-crm"', 'href="/pedidos"'):
        assert enlace in nav, enlace
    for enlace in ('href="/compras"', 'href="/?tab=stock"',
                   'href="/?tab=ajustes"', 'href="/finanzas"'):
        assert enlace not in nav, enlace


def test_nav_de_operaciones(db_limpia):
    nav = _nav_de(_con_roles("opera2", "operaciones").get("/venta").text)
    for enlace in ('href="/?tab=stock"', 'href="/compras"', 'href="/mi-crm"'):
        assert enlace in nav, enlace
    assert 'href="/?tab=ajustes"' not in nav


def test_nav_de_finanzas(db_limpia):
    nav = _nav_de(_con_roles("fin2", "finanzas").get("/venta").text)
    for enlace in ('href="/finanzas"', 'href="/conversaciones/respuestas"',
                   'href="/venta"', 'href="/control"'):
        assert enlace in nav, enlace
    assert 'href="/?tab=ajustes"' not in nav


def test_nav_completo_para_director_y_sin_rol(db_limpia):
    for c in (_con_roles("dire2", "director"), _con_roles("libre2")):
        nav = _nav_de(c.get("/venta").text)
        for enlace in ('href="/calendario"', 'href="/?tab=stock"',
                       'href="/venta"', 'href="/control"', 'href="/pedidos"',
                       'href="/compras"', 'href="/?tab=ajustes"'):
            assert enlace in nav, enlace
        # Las pantallas nuevas no se le ofrecen a quien no las lleva.
        assert 'href="/mi-crm"' not in nav
        assert 'href="/finanzas"' not in nav


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
