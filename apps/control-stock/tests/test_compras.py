"""La pestaña Compras, Fase 1: el tablero de lo que se le compra al proveedor.

Corren en modo muestra (sin LINEAR_API_KEY), así que el proyecto COMPRAS
vive en memoria. Las que necesitan el camino REAL de Linear no salen a la
red tampoco: le ponen un doble a `compras._pedir` (`_linear_falso`), que es
la única puerta del módulo a la API.

Lo que se cuida acá es lo que duele si se rompe:

- que las 7 columnas estén completas y en orden;
- que a `asignaciones.estado_compra` de paleta.json no le falte ni una
  clave — si falta, el KeyError salta AL IMPORTAR y la app entera no
  arranca;
- que un Linear SIN el proyecto COMPRAS —o sin alguna de sus columnas— y un
  Odoo sin el módulo `purchase` (o sea, sin `purchase.order`) den una
  pantalla vacía con su aviso y NUNCA un 500. **Ojo al leer esto: las dos
  cosas YA existen** — el proyecto en Linear, y `purchase` instalado en
  producción y en pruebas, medido en el proceso vivo el 30/09/2026 de
  tarde. Lo que estas pruebas cuidan es la TOLERANCIA, que sigue haciendo
  falta para una base nueva o un entorno de pruebas;
- que las etiquetas no se creen solas: sin `Resp: <nombre>` en el equipo
  VIV la compra se crea igual, sin responsable y con el aviso en el log;
- que el issue se asigne a Abraham y jamás al bot;
- que un `ref` vacío en el drop se corte sin preguntarle nada a Linear;
- y que cada quien mueva solo lo suyo, verificado en el SERVIDOR.

Las compras de muestra (app/compras.py):

    VIV-201  50 sacos de tierra negra    Por pedir   Abraham  sin orden
    VIV-202  Macetas de barro            Cotizando   —        sin orden
    VIV-203  Palmas areca (LEAD-88)      Pedido      Mary     P00014  0 de 840
    VIV-204  Abono orgánico              Abonado     Abraham  P00015  160 de 320
    VIV-205  Grama San Agustín           En camino   Mary     P00016  625 de 1250
    VIV-206  Piedra blanca               Recibido    —        P00017  480 de 480
    VIV-207  Mangueras y aspersores      Cerrado     Mary     P00012  210 de 210
    VIV-208  Bolsas de vivero            Por pedir   Abraham  sin orden
"""

import importlib
import logging

import pytest

from app import colores, compras, control, linear_leads, ventas


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("AJUSTES_ADMINS", raising=False)
    monkeypatch.delenv("LINEAR_ASSIGNEE_ID", raising=False)
    linear_leads.reiniciar_muestra()
    compras.reiniciar_muestra()
    compras.iniciar_tablas()
    control.iniciar_tablas()


@pytest.fixture
def de_dueno(monkeypatch):
    """La sesión de las pruebas es el dueño: mueve todo el tablero.

    `genesis` es la empleada con la que entra el TestClient (conftest), y
    su nombre no casa con ninguna etiqueta `Resp:`, así que sin esto es un
    empleado SIN etiqueta — que ve todo y no mueve nada.
    """
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")


DUENO = {"vistas": ["estado"], "resp_propio": "", "admin": True}
DE_MARY = {"vistas": ["estado"], "resp_propio": "Mary", "admin": False}
SIN_ETIQUETA = {"vistas": ["estado"], "resp_propio": "", "admin": False}


def _linear_falso(monkeypatch, con_proyecto=True, columnas=None,
                  etiquetas=(), issues=(), escritas=None):
    """Un doble de Linear para las pruebas del camino REAL.

    Reemplaza `compras._pedir`, que es la única puerta del módulo a la API
    (por eso alcanza con una sola línea para que nada salga a la red), y
    anota en `escritas` las mutaciones que se le mandan — así se puede
    mirar qué input se armó sin adivinarlo.
    """
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "1")
    compras._catalogo_cache.update({"en": 0, "dato": None})
    compras._lista_cache.update({"en": 0, "dato": None})
    compras.refrescar()
    # Quiénes son el equipo lo dice el catálogo del equipo LEAD, que es otra
    # puerta: se responde con la misma lista de la muestra para que estas
    # pruebas hablen SOLO de las etiquetas del equipo VIV.
    monkeypatch.setattr(linear_leads, "responsables",
                        lambda: list(linear_leads._RESPONSABLES_MUESTRA))
    nombres = ([e["nombre"] for e in compras.ESTADOS] if columnas is None
               else list(columnas))

    def pedir(consulta, variables=None):
        if escritas is not None and consulta.strip().startswith("mutation"):
            escritas.append((consulta, variables))
        if "teams(" in consulta:
            return {"teams": {"nodes": [{
                "id": "equipo-viv",
                "states": {"nodes": [{"id": "st-" + n, "name": n,
                                      "type": "started"} for n in nombres]},
                "labels": {"nodes": [{"id": "lb-" + n, "name": n,
                                      "isGroup": False} for n in etiquetas]},
                "projects": {"nodes": ([{"id": "pr-compras", "name": "COMPRAS"}]
                                       if con_proyecto else [])},
            }]}}
        if "issues(" in consulta:
            return {"issues": {"nodes": list(issues)}}
        if "issueCreate" in consulta:
            return {"issueCreate": {"success": True, "issue": {
                "id": "iss-nuevo", "identifier": "VIV-301",
                "url": "https://linear.app/viverorose/issue/VIV-301"}}}
        if "issueUpdate" in consulta:
            return {"issueUpdate": {"success": True}}
        if "commentCreate" in consulta:
            return {"commentCreate": {"success": True}}
        return {}

    monkeypatch.setattr(compras, "_pedir", pedir)
    # El comentario firmado sale por `linear_leads`, que tiene SU propia
    # puerta: también se dobla, para que tampoco salga a la red.
    monkeypatch.setattr(linear_leads, "_pedir", pedir)
    return pedir


def _issue(identifier="VIV-401", titulo="Sacos de tierra",
           columna="Por pedir", etiquetas=()):
    return {
        "id": "iss-" + identifier, "identifier": identifier, "title": titulo,
        "description": "", "url": "", "createdAt": "2026-09-20T10:00:00.000Z",
        "state": {"id": "st", "name": columna, "type": "started"},
        "labels": {"nodes": [{"id": "lb", "name": n} for n in etiquetas]},
    }


# ---------------------------------------------------------------------------
# Las 7 columnas
# ---------------------------------------------------------------------------

def test_el_tablero_trae_las_7_columnas_en_orden():
    columnas = compras.tablero()
    assert [c["clave"] for c in columnas] == [
        "POR_PEDIR", "COTIZANDO", "PEDIDO", "ABONADO", "EN_CAMINO",
        "RECIBIDO", "CERRADO"]
    assert [c["titulo"] for c in columnas] == [
        "Por pedir", "Cotizando", "Pedido", "Abonado", "En camino",
        "Recibido", "Cerrado"]


def test_los_dos_nombres_largos_no_chocan_con_los_pedidos_en_linea():
    """El equipo VIV ya tiene las columnas `Pedido` y `En camino`, que son
    de los pedidos de la tienda. Las de compras se llaman distinto EN
    LINEAR a propósito; el rótulo corto es solo de la pantalla."""
    assert compras.POR_CLAVE["PEDIDO"]["nombre"] == "Pedido a proveedor"
    assert compras.POR_CLAVE["PEDIDO"]["titulo"] == "Pedido"
    assert compras.POR_CLAVE["EN_CAMINO"]["nombre"] == "En camino al vivero"
    assert compras.POR_CLAVE["EN_CAMINO"]["titulo"] == "En camino"


def test_cada_compra_cae_en_su_columna():
    por_clave = {c["clave"]: [x["ref"] for x in c["compras"]]
                 for c in compras.tablero()}
    assert por_clave["POR_PEDIR"] == ["VIV-208", "VIV-201"]  # la más vieja arriba
    assert por_clave["EN_CAMINO"] == ["VIV-205"]
    assert por_clave["CERRADO"] == ["VIV-207"]


# ---------------------------------------------------------------------------
# La paleta: si falta una clave, la app NO arranca
# ---------------------------------------------------------------------------

def test_la_paleta_tiene_las_7_claves_con_una_familia_que_existe():
    asignado = colores.ASIGNACIONES["estado_compra"]
    assert set(asignado) == set(compras.ORDEN)
    for estado in compras.ESTADOS:
        assert estado["familia"] in colores.FAMILIAS
        assert estado["color"].startswith("#")
        assert estado["chip"]


def test_una_familia_que_falte_en_la_paleta_revienta_al_importar(monkeypatch):
    """El lookup de `colores.ASIGNACIONES` es DIRECTO a propósito: si a
    paleta.json le faltara una clave, el KeyError salta al importar el
    módulo —la app no arranca y se ve enseguida— en vez de dejar una
    columna sin color que nadie nota."""
    incompleta = {k: v for k, v in colores.ASIGNACIONES["estado_compra"].items()
                  if k != "ABONADO"}
    monkeypatch.setitem(colores.ASIGNACIONES, "estado_compra", incompleta)
    with pytest.raises(KeyError):
        importlib.reload(compras)
    # El módulo quedó a medio importar: se devuelve sano para las demás.
    monkeypatch.undo()
    importlib.reload(compras)
    assert len(compras.ESTADOS) == 7


# ---------------------------------------------------------------------------
# La tabla local: apoyo de pantalla y nada más
# ---------------------------------------------------------------------------

def test_la_tabla_compra_no_guarda_ni_estado_ni_plata():
    """El estado vive en Linear y el dinero en Odoo: si estuvieran también
    acá, algún día discreparían y habría que decidir cuál manda."""
    from app.datos import _db
    with _db() as con:
        columnas = {f["name"] for f in con.execute(
            "PRAGMA table_info(compra)")}
    assert columnas == {"ref", "que_compro", "proveedor_id",
                        "proveedor_nombre", "orden_compra_id",
                        "orden_compra_nombre", "lead_ref", "creada"}
    assert "estado" not in columnas
    assert "total" not in columnas and "pagado" not in columnas


# ---------------------------------------------------------------------------
# La plata: nunca un 0 inventado
# ---------------------------------------------------------------------------

def test_una_compra_sin_orden_de_odoo_no_trae_plata():
    plata = compras.plata_de("VIV-201")
    assert plata["ok"] is True          # "no hay" no es "no sé"
    assert plata["hay"] is False
    assert plata["total"] is None and plata["pagado"] is None


def test_la_plata_de_una_orden_conectada_sale_completa():
    plata = compras.plata_de("VIV-205")
    assert plata["ok"] and plata["hay"]
    assert (plata["orden"], plata["total"], plata["pagado"]) == (
        "P00016", 1250.0, 625.0)
    assert plata["saldo"] == 625.0
    assert plata["porcentaje"] == 50


def test_la_barra_nunca_pasa_de_100(monkeypatch):
    """Una orden sobrepagada (o un redondeo raro de Odoo) no puede pintar
    una barra que se sale del riel: se vería como un bug."""
    llena = compras._plata("P00099", 100.0, 180.0)
    assert llena["porcentaje"] == 100
    vacia = compras._plata("P00098", 0.0, 0.0)
    assert vacia["porcentaje"] == 0


def test_sin_purchase_order_en_odoo_la_plata_dice_que_no_se_pudo(monkeypatch):
    """El mundo de hoy: el módulo `purchase` de Odoo no está instalado, así
    que `purchase.order` NO existe y la consulta devuelve un Fault. Eso es
    `ok: False` con su motivo — nunca un total en 0, que sería mentir."""
    monkeypatch.setattr(compras, "configurado", lambda: True)
    monkeypatch.setattr(compras, "listar", lambda refrescar=False: [
        {"id": "i1", "ref": "VIV-401", "que_compro": "Sacos",
         "estado": "POR_PEDIR", "orden_compra_id": 7, "resp": "", "dias": 1}])
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(modelo, metodo, args, kw=None):
        assert modelo == "purchase.order"
        raise RuntimeError("Object purchase.order doesn't exist")

    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    plata = compras.plata_de("VIV-401")
    assert plata["ok"] is False
    assert "purchase.order" in plata["error"]
    assert plata["hay"] is False and plata["total"] is None


def test_la_tarjeta_sin_plata_no_pinta_barra(cliente):
    """VIV-201 no tiene orden de compra: su tarjeta sale sin barra, y la de
    VIV-205 (que sí tiene) con la suya."""
    texto = cliente.get("/compras").text
    tarjeta_201 = texto.split('data-ref="VIV-201"')[1].split("</div>")[0]
    assert "cmp-barra" not in tarjeta_201
    tarjeta_205 = texto.split('data-ref="VIV-205"')[1].split("</div>")[0]
    assert "cmp-barra" in tarjeta_205
    assert "$625.00 / $1250.00" in tarjeta_205


def test_la_pantalla_no_espera_a_odoo_para_la_plata(monkeypatch):
    """Con la caché fría la tarjeta sale SIN barra y Odoo se consulta por
    detrás: ninguna pintada espera a la red (regla del 22/09/2026)."""
    monkeypatch.setattr(compras, "configurado", lambda: True)
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    fondo = []
    monkeypatch.setattr("app.calendario._en_fondo",
                        lambda clave, tarea: fondo.append(clave))
    unas = [{"ref": "VIV-401", "orden_compra_id": 7}]
    assert compras.plata_de_varias(unas) == {}
    assert fondo == ["compras-plata"]


# ---------------------------------------------------------------------------
# El mundo de hoy: el proyecto COMPRAS todavía no existe en Linear
# ---------------------------------------------------------------------------

def test_sin_el_proyecto_el_tablero_sale_vacio_con_su_aviso(monkeypatch, cliente):
    _linear_falso(monkeypatch, con_proyecto=False)
    respuesta = cliente.get("/compras")
    assert respuesta.status_code == 200          # nunca un 500
    texto = respuesta.text
    assert "El proyecto COMPRAS todavía no existe" in texto
    assert "el código no crea proyectos ni columnas" in texto
    # Las 7 columnas siguen ahí, vacías.
    for estado in compras.ESTADOS:
        assert estado["titulo"] in texto
    assert "data-ref=" not in texto


def test_sin_el_proyecto_no_se_puede_anotar_una_compra(monkeypatch):
    _linear_falso(monkeypatch, con_proyecto=False)
    with pytest.raises(compras.ErrorCompras) as fallo:
        compras.crear("Sacos de tierra", autor="Génesis")
    assert "COMPRAS" in str(fallo.value)


def test_si_falta_una_columna_la_pantalla_la_nombra(monkeypatch, cliente):
    faltante = compras.POR_CLAVE["ABONADO"]["nombre"]
    columnas = [e["nombre"] for e in compras.ESTADOS if e["nombre"] != faltante]
    _linear_falso(monkeypatch, columnas=columnas)
    assert faltante in compras.columnas_que_faltan()
    texto = cliente.get("/compras").text
    assert "le faltan estas columnas" in texto and faltante in texto


def test_si_linear_no_contesta_la_pantalla_lo_dice_y_no_revienta(monkeypatch, cliente):
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    compras._catalogo_cache.update({"en": 0, "dato": None})
    compras.refrescar()

    def muerto(consulta, variables=None):
        raise compras.ErrorCompras("No se pudo hablar con Linear")

    monkeypatch.setattr(compras, "_pedir", muerto)
    assert "No se pudo leer Linear" in compras.falta_en_linear()
    # `listar()` sí revienta (quien escribe tiene que enterarse); la
    # PANTALLA usa `listar_o_vacio()`, que no.
    with pytest.raises(compras.ErrorCompras):
        compras.listar()
    assert compras.listar_o_vacio() == []
    respuesta = cliente.get("/compras")
    assert respuesta.status_code == 200
    assert "No se pudo leer Linear" in respuesta.text


def test_en_modo_muestra_no_falta_nada():
    assert compras.falta_en_linear() == ""
    assert compras.listo() is True
    assert compras.columnas_que_faltan() == []


# ---------------------------------------------------------------------------
# Crear una compra: las etiquetas nunca se crean solas
# ---------------------------------------------------------------------------

def test_una_compra_nueva_nace_en_por_pedir():
    nueva = compras.crear("Tierra de hoja", proveedor_nombre="Don Pepe",
                          resp="Mary", autor="Génesis")
    compra = compras.uno(nueva["ref"])
    assert compra["estado"] == "POR_PEDIR"
    assert compra["que_compro"] == "Tierra de hoja"
    assert compra["proveedor"] == "Don Pepe"
    assert compra["resp"] == "Mary"


def test_la_compra_nueva_queda_en_la_tabla_local():
    nueva = compras.crear("Tierra de hoja", proveedor_nombre="Don Pepe",
                          proveedor_id=42, autor="Génesis")
    from app.datos import _db
    with _db() as con:
        fila = con.execute("SELECT * FROM compra WHERE ref = ?",
                           (nueva["ref"],)).fetchone()
    assert fila["que_compro"] == "Tierra de hoja"
    assert fila["proveedor_nombre"] == "Don Pepe" and fila["proveedor_id"] == 42


def test_sin_que_se_compra_no_se_crea_nada():
    with pytest.raises(compras.ErrorCompras) as fallo:
        compras.crear("   ", autor="Génesis")
    assert "qué se compra" in str(fallo.value)


def test_una_persona_que_no_esta_en_el_equipo_rebota():
    """Dos cosas distintas: quién es del equipo (las etiquetas `Resp:` del
    equipo LEAD) y si su etiqueta ya existe en VIV. Un nombre que no está
    en el equipo se rebota; una etiqueta que falta en VIV deja pasar (ver
    la prueba de abajo)."""
    with pytest.raises(compras.ErrorCompras) as fallo:
        compras.crear("Sacos", resp="Fulanito", autor="Génesis")
    assert "Fulanito" in str(fallo.value)
    assert "no está en el equipo" in str(fallo.value)


def test_un_lead_que_no_existe_rebota_nombrando_el_ref():
    with pytest.raises(compras.ErrorCompras) as fallo:
        compras.crear("Palmas", lead_ref="LEAD-999", autor="Génesis")
    assert "LEAD-999" in str(fallo.value)


def test_la_compra_para_un_cliente_guarda_su_lead():
    nueva = compras.crear("Palmas areca", lead_ref="LEAD-88", autor="Génesis")
    assert compras.uno(nueva["ref"])["lead_ref"] == "LEAD-88"


def test_una_etiqueta_resp_que_no_existe_no_frena_la_compra(caplog):
    """`Resp: Ruben` no existe en el equipo VIV de la muestra: la compra se
    crea IGUAL, sin responsable, y queda el aviso en el log. Nunca se crea
    una etiqueta al vuelo (regla del 24/09/2026)."""
    assert "Ruben" in linear_leads.responsables()      # está en el equipo…
    assert compras.resp_label_disponible("Ruben") is False   # …pero sin etiqueta en VIV
    with caplog.at_level(logging.WARNING, logger="control_stock"):
        nueva = compras.crear("Mangueras", resp="Ruben", autor="Génesis")
    compra = compras.uno(nueva["ref"])
    assert compra is not None            # la compra SÍ se creó
    assert compra["resp"] == ""          # y sin responsable, no con uno inventado
    assert "Resp: Ruben" in caplog.text and "no se crea sola" in caplog.text


def test_el_modulo_no_tiene_ninguna_mutacion_que_cree_etiquetas():
    """El candado, mirado en el código: si algún día alguien agrega un
    `issueLabelCreate` acá, esta prueba lo caza. Una etiqueta creada al
    vuelo nace suelta, fuera de su grupo, y ensucia el vocabulario."""
    fuente = open(compras.__file__).read()
    for prohibida in ("issueLabelCreate", "projectCreate", "workflowStateCreate"):
        assert prohibida not in fuente


# ---------------------------------------------------------------------------
# Crear una compra contra el Linear real (con su doble)
# ---------------------------------------------------------------------------

def test_el_issue_nace_en_el_proyecto_compras_del_equipo_viv(monkeypatch):
    escritas = []
    _linear_falso(monkeypatch, etiquetas=["Resp: Mary"], escritas=escritas)
    nueva = compras.crear("Sacos de tierra", resp="Mary", autor="Génesis")
    assert nueva["ref"] == "VIV-301"
    creadas = [v for c, v in escritas if "issueCreate" in c]
    datos = creadas[0]["datos"]
    assert datos["teamId"] == "equipo-viv"
    assert datos["projectId"] == "pr-compras"
    assert datos["stateId"] == "st-Por pedir"
    assert datos["title"] == "Sacos de tierra"
    assert datos["labelIds"] == ["lb-Resp: Mary"]


def test_el_issue_se_asigna_a_abraham_y_jamas_al_bot(monkeypatch):
    escritas = []
    monkeypatch.setenv("LINEAR_ASSIGNEE_ID", "id-de-abraham")
    _linear_falso(monkeypatch, escritas=escritas)
    compras.crear("Sacos de tierra", autor="Génesis")
    datos = [v for c, v in escritas if "issueCreate" in c][0]["datos"]
    assert datos["assigneeId"] == "id-de-abraham"


def test_sin_la_variable_del_asignado_el_issue_nace_sin_asignar(monkeypatch):
    """Sin `LINEAR_ASSIGNEE_ID` no se manda `assigneeId`: el issue queda sin
    asignar, que sigue sin ser el bot — `issueCreate` no asigna solo a quien
    tiene la key."""
    escritas = []
    _linear_falso(monkeypatch, escritas=escritas)
    compras.crear("Sacos de tierra", autor="Génesis")
    datos = [v for c, v in escritas if "issueCreate" in c][0]["datos"]
    assert "assigneeId" not in datos


def test_el_responsable_va_por_etiqueta_nunca_por_assignee(monkeypatch):
    escritas = []
    monkeypatch.setenv("LINEAR_ASSIGNEE_ID", "id-de-abraham")
    _linear_falso(monkeypatch, etiquetas=["Resp: Mary"], escritas=escritas)
    compras.crear("Sacos de tierra", resp="Mary", autor="Génesis")
    datos = [v for c, v in escritas if "issueCreate" in c][0]["datos"]
    # El responsable es la etiqueta; el asignado sigue siendo Abraham.
    assert datos["labelIds"] == ["lb-Resp: Mary"]
    assert datos["assigneeId"] == "id-de-abraham"


def test_contra_linear_real_una_etiqueta_resp_ausente_no_frena_la_compra(
        monkeypatch, caplog):
    escritas = []
    _linear_falso(monkeypatch, etiquetas=[], escritas=escritas)  # VIV sin Resp:
    with caplog.at_level(logging.WARNING, logger="control_stock"):
        compras.crear("Sacos de tierra", resp="Mary", autor="Génesis")
    datos = [v for c, v in escritas if "issueCreate" in c][0]["datos"]
    assert "labelIds" not in datos
    assert "Resp: Mary" in caplog.text


def test_la_compra_recien_creada_aparece_en_el_tablero_al_instante(monkeypatch):
    """`refrescar()` a propósito no tira la caché (la pintada siguiente
    sirve lo guardado mientras el refresco viaja por detrás), así que la
    compra nueva se INSERTA ahí: sin eso el aviso «VIV-301 anotada»
    hablaría de una tarjeta que no está en la pantalla."""
    _linear_falso(monkeypatch, issues=[_issue("VIV-401")])
    assert [c["ref"] for c in compras.listar()] == ["VIV-401"]  # caché tibia
    compras.crear("Sacos de tierra", autor="Génesis")
    refs = [c["ref"] for c in compras.listar()]
    assert "VIV-301" in refs and "VIV-401" in refs
    nueva = compras.uno("VIV-301")
    assert nueva["estado"] == "POR_PEDIR"
    assert nueva["que_compro"] == "Sacos de tierra"


def test_si_linear_no_dice_el_numero_del_issue_no_se_guarda_nada(monkeypatch):
    """Sin el VIV-XX la fila local nacería con la llave vacía y la tarjeta
    no se podría mover nunca."""
    base = _linear_falso(monkeypatch)

    def sin_numero(consulta, variables=None):
        if "issueCreate" in consulta:
            return {"issueCreate": {"success": True, "issue": {
                "id": "iss", "identifier": "", "url": ""}}}
        return base(consulta, variables)

    monkeypatch.setattr(compras, "_pedir", sin_numero)
    with pytest.raises(compras.ErrorCompras) as fallo:
        compras.crear("Sacos de tierra", autor="Génesis")
    assert "no dijo su número" in str(fallo.value)
    from app.datos import _db
    with _db() as con:
        assert con.execute("SELECT count(*) FROM compra").fetchone()[0] == 0


def test_solo_lectura_no_crea_nada(monkeypatch):
    _linear_falso(monkeypatch)
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "0")
    with pytest.raises(compras.ErrorCompras) as fallo:
        compras.crear("Sacos de tierra", autor="Génesis")
    assert "CALENDARIO_ESCRITURA" in str(fallo.value)


# ---------------------------------------------------------------------------
# Mover una compra
# ---------------------------------------------------------------------------

def test_mover_cambia_la_columna_y_lo_dice():
    aviso, error = compras.mover("VIV-201", "COTIZANDO", autor="Génesis")
    assert error == ""
    assert "Por pedir → Cotizando" in aviso
    assert compras.uno("VIV-201")["estado"] == "COTIZANDO"


def test_mover_a_la_misma_columna_no_hace_nada():
    aviso, error = compras.mover("VIV-201", "POR_PEDIR", autor="Génesis")
    assert (aviso, error) == ("", "")


def test_mover_a_una_columna_que_no_existe():
    aviso, error = compras.mover("VIV-201", "INVENTADA", autor="Génesis")
    assert aviso == "" and "no existe" in error
    assert compras.uno("VIV-201")["estado"] == "POR_PEDIR"


def test_mover_deja_un_comentario_firmado_en_el_issue(monkeypatch):
    escritas = []
    _linear_falso(monkeypatch, issues=[_issue("VIV-401")], escritas=escritas)
    aviso, error = compras.mover("VIV-401", "PEDIDO", autor="Génesis")
    assert error == "" and aviso
    comentarios = [v for c, v in escritas if "commentCreate" in c]
    assert len(comentarios) == 1
    texto = comentarios[0]["texto"]
    assert "Por pedir" in texto and "Pedido" in texto
    # El bot solo FIRMA: el nombre de quien lo hizo va en el texto.
    assert "_— Génesis desde Control Viverorose_" in texto


def test_un_ref_vacio_se_corta_sin_preguntarle_a_linear(monkeypatch):
    """El POST del enlace arrastrado (todo <a> es arrastrable por
    naturaleza) llega sin ref. Eso no es una compra borrada, y no hay que
    preguntarle nada a Linear para saberlo."""
    def nadie(*_a, **_k):
        raise AssertionError("no se le pregunta a Linear por un ref vacío")

    monkeypatch.setattr(compras, "listar", nadie)
    aviso, error = compras.mover("", "PEDIDO", autor="Génesis")
    assert aviso == ""
    assert "No llegó qué compra tocar" in error
    assert "arrastrando la tarjeta entera" in error


def test_una_compra_que_ya_no_esta_se_nombra_en_el_error():
    assert "VIV-999" in compras.mensaje_compra_ausente("VIV-999")
    aviso, error = compras.mover("VIV-999", "PEDIDO", autor="Génesis")
    assert aviso == "" and "VIV-999" in error


# ---------------------------------------------------------------------------
# Quién puede mover: el MISMO mecanismo de Control
# ---------------------------------------------------------------------------

def test_el_dueno_mueve_todo_el_tablero():
    for compra in compras.listar():
        assert control.puede_tocar(compra, DUENO) is True


def test_un_empleado_mueve_lo_suyo_y_no_lo_ajeno():
    de_mary = compras.uno("VIV-205")      # Resp: Mary
    de_abraham = compras.uno("VIV-204")   # Resp: Abraham
    assert control.puede_tocar(de_mary, DE_MARY) is True
    assert control.puede_tocar(de_abraham, DE_MARY) is False


def test_un_empleado_sin_etiqueta_no_mueve_ni_las_sin_asignar():
    sin_resp = compras.uno("VIV-202")
    assert control.puede_tocar(sin_resp, SIN_ETIQUETA) is False


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def test_la_pantalla_pinta_las_7_columnas_y_sus_compras(cliente):
    respuesta = cliente.get("/compras")
    assert respuesta.status_code == 200
    texto = respuesta.text
    for estado in compras.ESTADOS:
        assert estado["titulo"] in texto
    assert "50 sacos de tierra negra" in texto
    assert "Agroservicios del Istmo" in texto
    assert "P00016" in texto


def test_la_columna_manda_el_drop_a_compras_estado(cliente):
    """El arrastre es el control.js de Control sin tocarlo: la columna dice
    a dónde va el POST y cómo se llama el campo."""
    texto = cliente.get("/compras").text
    assert 'data-destino="/compras/estado"' in texto
    assert 'data-campo="estado"' in texto
    assert 'data-columna="EN_CAMINO"' in texto


def test_el_hace_cuanto_se_resalta_desde_los_5_dias(cliente):
    """El umbral lo decide Python (`control.DIAS_HACE_ALERTA`), no la
    plantilla — y es el MISMO de las tarjetas de Control."""
    assert control.DIAS_HACE_ALERTA == 5
    texto = cliente.get("/compras").text
    tarjeta_208 = texto.split('data-ref="VIV-208"')[1].split("</div>")[0]
    assert "hace-alerta" in tarjeta_208          # 14 días
    tarjeta_201 = texto.split('data-ref="VIV-201"')[1].split("</div>")[0]
    assert "hace-alerta" not in tarjeta_201      # 1 día


def test_la_compra_de_un_cliente_enlaza_a_su_lead(cliente):
    texto = cliente.get("/compras").text
    tarjeta = texto.split('data-ref="VIV-203"')[1].split("</div>")[0]
    assert "/control?abrir=LEAD-88" in tarjeta


def test_solo_el_que_puede_mover_ve_la_tarjeta_arrastrable(cliente, de_dueno):
    """El dueño puede arrastrar; `genesis` sin ser dueño no tiene etiqueta
    `Resp:` y ninguna tarjeta le queda arrastrable. El candado real está en
    el servidor: esto es cortesía, para no invitar a un error."""
    assert 'draggable="true"' in cliente.get("/compras").text


def test_un_empleado_sin_etiqueta_no_ve_nada_arrastrable(cliente):
    texto = cliente.get("/compras").text
    assert 'draggable="true"' not in texto
    assert "Sin etiqueta." in texto
    # Pero VE el tablero completo, igual que en Control.
    assert "50 sacos de tierra negra" in texto


def test_el_formulario_solo_aparece_con_nueva(cliente, de_dueno):
    assert 'id="cp-que"' not in cliente.get("/compras").text
    texto = cliente.get("/compras?nueva=1").text
    assert 'id="cp-que"' in texto
    assert 'id="cp-prov"' in texto and 'id="cp-resp"' in texto
    assert 'id="cp-lead"' in texto


def test_el_selector_de_proveedor_aguanta_la_lista_vacia(cliente, de_dueno):
    """Hoy Odoo no tiene ni un contacto marcado como proveedor: el campo es
    de texto con sugerencias, para que se pueda anotar el nombre igual."""
    assert compras.proveedores() == {"ok": True, "error": "",
                                     "proveedores": []}
    texto = cliente.get("/compras?nueva=1").text
    assert '<datalist id="cp-proveedores">' in texto
    assert 'name="proveedor"' in texto


def test_los_proveedores_de_odoo_salen_como_sugerencias(monkeypatch, cliente,
                                                        de_dueno):
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    pedidos = []

    def falso(modelo, metodo, args, kw=None):
        pedidos.append((modelo, metodo, args, kw))
        return [{"id": 9, "name": "Agroservicios del Istmo",
                 "phone": "6000-0000", "email": "", "city": "Panamá"}]

    monkeypatch.setattr(ventas, "_ejecutar", falso)
    assert "Agroservicios del Istmo" in cliente.get("/compras?nueva=1").text
    modelo, metodo, args, kw = pedidos[0]
    assert (modelo, metodo) == ("res.partner", "search_read")
    assert args == [[["supplier_rank", ">", 0]]]
    # OJO con Odoo 19: `res.partner` ya NO tiene `mobile` y pedirlo revienta
    # la consulta entera. El teléfono es `phone`.
    assert "mobile" not in kw["fields"] and "phone" in kw["fields"]


def test_si_odoo_no_contesta_el_formulario_lo_dice_y_deja_anotar(
        monkeypatch, cliente, de_dueno):
    monkeypatch.setattr(ventas, "configurado", lambda: True)

    def revienta(*_a, **_k):
        raise RuntimeError("Odoo no contesta")

    monkeypatch.setattr(ventas, "_ejecutar", revienta)
    texto = cliente.get("/compras?nueva=1").text
    assert "No se pudo leer los proveedores de Odoo" in texto
    assert 'name="proveedor"' in texto      # se puede anotar igual


def test_anotar_una_compra_desde_la_pantalla(cliente, de_dueno):
    respuesta = cliente.post("/compras/nueva", data={
        "que_compro": "Tierra de hoja", "proveedor": "Don Pepe",
        "resp": "Mary", "lead_ref": ""}, follow_redirects=False)
    assert respuesta.status_code == 303
    assert "anotada" in respuesta.headers["location"]
    nuevas = [c for c in compras.listar() if c["que_compro"] == "Tierra de hoja"]
    assert len(nuevas) == 1 and nuevas[0]["estado"] == "POR_PEDIR"


def test_el_drop_sin_ref_devuelve_el_mensaje_claro(cliente, de_dueno):
    respuesta = cliente.post("/compras/estado",
                             data={"ref": "", "estado": "PEDIDO"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "No%20lleg%C3%B3%20qu%C3%A9%20compra%20tocar" in \
        respuesta.headers["location"]


def test_un_empleado_no_puede_mover_una_compra_ajena_ni_por_post(cliente):
    """El candado está en el SERVIDOR: que el navegador no la muestre
    arrastrable no basta, porque un POST se puede mandar a mano."""
    respuesta = cliente.post("/compras/estado",
                             data={"ref": "VIV-204", "estado": "PEDIDO"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "no%20la%20mov%C3%A9s%20vos" in respuesta.headers["location"]
    assert compras.uno("VIV-204")["estado"] == "ABONADO"   # no se movió


def test_el_dueno_si_puede_mover_por_post(cliente, de_dueno):
    respuesta = cliente.post("/compras/estado",
                             data={"ref": "VIV-204", "estado": "EN_CAMINO"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error" not in respuesta.headers["location"]
    assert compras.uno("VIV-204")["estado"] == "EN_CAMINO"


def test_el_menu_lleva_a_compras(cliente):
    for ruta in ("/compras", "/control", "/calendario", "/venta", "/"):
        texto = cliente.get(ruta).text
        assert 'href="/compras"' in texto or ruta == "/compras", ruta
    # Y la pestaña se marca como activa en su propia pantalla.
    assert 'class="pil on"' in cliente.get("/compras").text
