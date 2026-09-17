"""Pruebas de los Proyectos (el contenedor de varias cotizaciones de un
mismo cliente), con un Odoo simulado.

El simulado imita lo que importa del addon vivero_rose_pedidos: la
oportunidad con su `lead_ref` y su `etapa_proyecto`, las cotizaciones
colgadas por `opportunity_id`, el modelo de gastos, y — clave — los campos
de plata CALCULADOS (total_cotizado, total_gastado, ganancia_proyecto), que
en Odoo son computados y que esta app solo lee. Si el simulado los guardara
en vez de calcularlos, las pruebas no verían el error de leer un total
desactualizado.
"""

import pytest

from app import cotizaciones, proyectos, ventas

XML_IDS = {
    "vivero_rose_pedidos.etapa_flujo_cotizado": 7002,
}

SECUENCIAS_ETAPA = {7001: 0, 7002: 2, 7003: 3, 7004: 4}
NOMBRES_ETAPA = {7001: "Nuevo", 7002: "Cotizado", 7003: "Facturado", 7004: "Pagado"}

EMPLEADA = {"id": "genesis", "nombre": "Génesis"}


class OdooProyectos:
    """Odoo de mentira: solo los modelos que toca proyectos.py."""

    def __init__(self):
        self.leads = {}
        self.ordenes = {}
        self.gastos = {}
        self.partners = {}
        self.tags = {}
        self.categorias = {}
        self.siguiente = 3000

    def _nuevo(self):
        self.siguiente += 1
        return self.siguiente

    def ejecutar(self, modelo, metodo, args, kw=None):
        manejador = getattr(self, (modelo + "_" + metodo).replace(".", "_"))
        return manejador(args, kw or {})

    # ---- semillas ----
    def sembrar_lead(self, ref, nombre, etapa="nuevo", tags=(), etapa_flujo=7001):
        lead_id = self._nuevo()
        self.leads[lead_id] = {
            "name": nombre, "lead_ref": ref, "partner_id": [1, "Cliente"],
            "tag_ids": list(tags), "etapa_proyecto": etapa,
            "stage_id": [etapa_flujo, NOMBRES_ETAPA[etapa_flujo]],
            "create_date": "2026-09-17 10:00:00", "description": "",
            "phone": "6567-3062", "expected_revenue": 0.0,
        }
        return lead_id

    def sembrar_orden(self, lead_id, nombre, total, estado="draft"):
        orden_id = self._nuevo()
        self.ordenes[orden_id] = {
            "name": nombre, "amount_total": total, "state": estado,
            "create_date": "2026-09-17 11:00:00",
            "opportunity_id": [lead_id, "proyecto"], "tipo_servicio": "paisajismo",
        }
        return orden_id

    # ---- los campos que Odoo calcula ----
    def _plata_de(self, lead_id):
        suyas = [o for o in self.ordenes.values()
                 if o["opportunity_id"] and o["opportunity_id"][0] == lead_id]
        vivas = [o for o in suyas if o["state"] != "cancel"]
        gastado = sum(g["monto"] for g in self.gastos.values()
                      if g["lead_id"] == lead_id)
        vendido = sum(o["amount_total"] for o in vivas if o["state"] == "sale")
        return {
            "total_cotizado": sum(o["amount_total"] for o in vivas),
            "total_vendido": vendido,
            "total_gastado": gastado,
            "ganancia_proyecto": vendido - gastado,
            "total_facturado": 0.0,
            "por_cobrar": 0.0,
            "proximo_cobro": False,
        }

    def _lead_leido(self, lead_id, campos):
        datos = {**self.leads[lead_id], **self._plata_de(lead_id)}
        return {"id": lead_id, **{c: datos.get(c) for c in campos}}

    # ---- referencias XML ----
    def ir_model_data_search_read(self, args, kw):
        dominio = args[0]
        modulo = next(c[2] for c in dominio if c[0] == "module")
        nombre = next(c[2] for c in dominio if c[0] == "name")
        res_id = XML_IDS.get(f"{modulo}.{nombre}")
        return [{"res_id": res_id}] if res_id else []

    # ---- crm.lead ----
    def _coincide_lead(self, lead, dominio):
        campo, operador, valor = dominio[0]
        actual = str(lead.get(campo) or "")
        if operador == "=like":
            return actual.startswith(valor.rstrip("%"))
        if operador == "=ilike":
            return actual.lower() == str(valor).lower()
        return False

    def crm_lead_search_read(self, args, kw):
        campos = kw.get("fields", [])
        filas = [self._lead_leido(i, campos) for i, l in self.leads.items()
                 if self._coincide_lead(l, args[0])]
        limite = kw.get("limit")
        return filas[:limite] if limite else filas

    def crm_lead_read(self, args, kw):
        return [self._lead_leido(i, kw.get("fields", [])) for i in args[0]
                if i in self.leads]

    def crm_lead_create(self, args, kw):
        nuevo = self._nuevo()
        valores = dict(args[0])
        etiquetas = [o[1] for o in valores.pop("tag_ids", []) if o[0] == 4]
        self.leads[nuevo] = {
            **valores, "tag_ids": etiquetas,
            "stage_id": [7001, "Nuevo"],
            "create_date": "2026-09-17 12:00:00",
            "partner_id": [valores.get("partner_id"), "Cliente"],
            "phone": "", "expected_revenue": 0.0,
        }
        return nuevo

    def crm_lead_write(self, args, kw):
        for lid in args[0]:
            for campo, valor in args[1].items():
                if campo == "stage_id":
                    self.leads[lid]["stage_id"] = [valor, NOMBRES_ETAPA.get(valor, "")]
                else:
                    self.leads[lid][campo] = valor
        return True

    def crm_stage_read(self, args, kw):
        return [{"id": i, "sequence": SECUENCIAS_ETAPA.get(i, 0)} for i in args[0]]

    # ---- sale.order ----
    def sale_order_search_read(self, args, kw):
        campo, operador, valor = args[0][0]
        campos = kw.get("fields", [])
        def coincide(o):
            actual = o[campo][0] if isinstance(o[campo], list) else o[campo]
            return actual in valor if operador == "in" else actual == valor
        return [{"id": i, **{c: o.get(c) for c in campos}}
                for i, o in self.ordenes.items() if coincide(o)]

    def sale_order_write(self, args, kw):
        for oid in args[0]:
            for campo, valor in args[1].items():
                self.ordenes[oid][campo] = (
                    [valor, "proyecto"] if campo == "opportunity_id" else valor)
        return True

    # ---- gastos ----
    def vivero_rose_proyecto_gasto_create(self, args, kw):
        nuevo = self._nuevo()
        self.gastos[nuevo] = dict(args[0])
        return nuevo

    def vivero_rose_proyecto_gasto_search_read(self, args, kw):
        lead_id = args[0][0][2]
        campos = kw.get("fields", [])
        return [{"id": i, **{c: g.get(c) for c in campos}}
                for i, g in self.gastos.items() if g["lead_id"] == lead_id]

    def vivero_rose_proyecto_gasto_search(self, args, kw):
        condiciones = {c[0]: c[2] for c in args[0]}
        return [i for i, g in self.gastos.items()
                if i == condiciones.get("id") and g["lead_id"] == condiciones.get("lead_id")]

    def vivero_rose_proyecto_gasto_unlink(self, args, kw):
        for i in args[0]:
            self.gastos.pop(i, None)
        return True

    # ---- etiquetas y cliente ----
    def crm_tag_search(self, args, kw):
        nombre = args[0][0][2].lower()
        return [i for i, t in self.tags.items() if t["name"].lower() == nombre]

    def crm_tag_create(self, args, kw):
        nuevo = self._nuevo()
        self.tags[nuevo] = dict(args[0])
        return nuevo

    def crm_tag_read(self, args, kw):
        return [{"id": i, "name": self.tags[i]["name"]} for i in args[0] if i in self.tags]

    def res_partner_search(self, args, kw):
        campo, operador, valor = args[0][0]
        if operador == "=ilike":
            return [i for i, p in self.partners.items()
                    if str(p.get(campo) or "").lower() == str(valor).lower()]
        return []

    def res_partner_create(self, args, kw):
        nuevo = self._nuevo()
        self.partners[nuevo] = {**args[0], "category_id": []}
        return nuevo

    def res_partner_write(self, args, kw):
        return True

    def res_partner_category_search(self, args, kw):
        nombre = args[0][0][2]
        return [i for i, c in self.categorias.items() if c["name"] == nombre]

    def res_partner_category_create(self, args, kw):
        nuevo = self._nuevo()
        self.categorias[nuevo] = dict(args[0])
        return nuevo


@pytest.fixture
def odoo(monkeypatch, db_limpia):
    falso = OdooProyectos()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    # Con el Odoo simulado en pie, la app tiene que considerarse conectada:
    # si no, las pantallas salen con el aviso de "Proyectos está apagado".
    monkeypatch.setattr(ventas, "configurado", lambda: True)
    proyectos.iniciar_tablas()
    cotizaciones.reiniciar_cache()
    return falso


def _lead_de(odoo, ref):
    return next(i for i, l in odoo.leads.items() if l["lead_ref"] == ref)


# ---------------------------------------------------------------------------
# El número y el nombre
# ---------------------------------------------------------------------------

def test_el_primer_proyecto_es_el_01(odoo):
    assert proyectos.siguiente_ref() == "PROYECTO-01"


def test_la_numeracion_sigue_al_mayor_existente(odoo):
    odoo.sembrar_lead("PROYECTO-01", "Proyecto Morris")
    odoo.sembrar_lead("PROYECTO-02", "Proyecto Pérez")
    # Un lead del sitio web no entra en la cuenta: numeración aparte.
    odoo.sembrar_lead("PP-JXW75", "Lead web")
    assert proyectos.siguiente_ref() == "PROYECTO-03"


def test_el_proyecto_se_llama_proyecto_algo(odoo):
    assert proyectos.nombre_de_proyecto("Morris", "Morris Cohen") == "Proyecto Morris"
    # No se duplica la palabra si ya la escribieron.
    assert proyectos.nombre_de_proyecto("Proyecto Morris", "") == "Proyecto Morris"
    # Sin nombre, se arma con el NOMBRE DE PILA del cliente, no el apellido:
    # "Proyecto Morris" es de un cliente que se llama Morris Cohen.
    assert proyectos.nombre_de_proyecto("", "Morris Cohen") == "Proyecto Morris"
    assert proyectos.nombre_de_proyecto("", "") == "Proyecto"


def test_crear_deja_la_oportunidad_con_su_ref_nombre_y_etiqueta(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "6567-3062", "paisajismo",
                          nota="Jardín de la entrada", nombre_proyecto="Morris")
    assert ref == "PROYECTO-01"
    lead = odoo.leads[_lead_de(odoo, ref)]
    # El título es el del proyecto, no el del cliente.
    assert lead["name"] == "Proyecto Morris"
    assert lead["lead_ref"] == "PROYECTO-01"
    assert lead["etapa_proyecto"] == "nuevo"
    assert [odoo.tags[t]["name"] for t in lead["tag_ids"]] == ["PAISAJISMO"]


def test_el_cliente_se_busca_por_nombre_y_no_por_telefono(odoo):
    """Un teléfono repetido no debe colgarle el proyecto a otro cliente:
    probándolo en pruebas, un proyecto de "Morris Cohen" quedó a nombre de
    "Administrator" porque ese número ya estaba en ese contacto."""
    ajeno = odoo.res_partner_create([{"name": "Administrator",
                                      "mobile": "65673062"}], {})
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "6567-3062", "paisajismo")
    lead = odoo.leads[_lead_de(odoo, ref)]
    assert lead["partner_id"][0] != ajeno
    assert odoo.partners[lead["partner_id"][0]]["name"] == "Morris Cohen"


def test_un_cliente_que_ya_existe_no_se_duplica(odoo):
    ya_esta = odoo.res_partner_create([{"name": "Morris Cohen"}], {})
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    lead = odoo.leads[_lead_de(odoo, ref)]
    assert lead["partner_id"][0] == ya_esta


def test_crear_exige_nombre_de_cliente_y_tipo_conocido(odoo):
    with pytest.raises(ValueError):
        proyectos.crear(EMPLEADA, "  ", "", "paisajismo")
    with pytest.raises(ValueError):
        proyectos.crear(EMPLEADA, "Morris Cohen", "", "tipo-que-no-existe")


# ---------------------------------------------------------------------------
# Las cotizaciones y el avance de columna
# ---------------------------------------------------------------------------

def test_enlazar_cuelga_la_cotizacion_del_proyecto(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    orden_id = odoo.sembrar_orden(0, "S00021", 1200.0)
    proyectos.enlazar_cotizacion(orden_id, ref)
    assert odoo.ordenes[orden_id]["opportunity_id"][0] == _lead_de(odoo, ref)
    assert [c["orden"] for c in proyectos.detalle(ref)["cotizaciones"]] == ["S00021"]


def test_la_primera_cotizacion_mueve_la_tarjeta_a_cotizado(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    lead_id = _lead_de(odoo, ref)
    assert odoo.leads[lead_id]["etapa_proyecto"] == "nuevo"

    primera = odoo.sembrar_orden(lead_id, "S00021", 1200.0)
    proyectos.enlazar_cotizacion(primera, ref)
    # La columna del proyecto y el Flujo del CRM avanzan los dos.
    assert odoo.leads[lead_id]["etapa_proyecto"] == "cotizado"
    assert odoo.leads[lead_id]["stage_id"][1] == "Cotizado"
    assert odoo.leads[lead_id]["expected_revenue"] == 1200.0

    # La segunda cotización SUMA al ingreso esperado, no lo reemplaza.
    segunda = odoo.sembrar_orden(lead_id, "S00022", 800.0)
    proyectos.enlazar_cotizacion(segunda, ref)
    assert odoo.leads[lead_id]["expected_revenue"] == 2000.0


def test_una_tarjeta_que_ya_avanzo_no_retrocede(odoo):
    odoo.sembrar_lead("PROYECTO-01", "Proyecto Morris", etapa="ganado",
                      etapa_flujo=7004)
    lead_id = _lead_de(odoo, "PROYECTO-01")
    orden = odoo.sembrar_orden(lead_id, "S00021", 500.0)
    proyectos.enlazar_cotizacion(orden, "PROYECTO-01")
    assert odoo.leads[lead_id]["etapa_proyecto"] == "ganado"
    assert odoo.leads[lead_id]["stage_id"][1] == "Pagado"


# ---------------------------------------------------------------------------
# La plata
# ---------------------------------------------------------------------------

def test_la_ficha_muestra_los_totales_que_calcula_odoo(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    lead_id = _lead_de(odoo, ref)
    odoo.sembrar_orden(lead_id, "S00021", 1200.0, estado="sale")
    odoo.sembrar_orden(lead_id, "S00022", 800.0, estado="draft")
    # Una cancelada no cuenta para nada.
    odoo.sembrar_orden(lead_id, "S00023", 5000.0, estado="cancel")
    proyectos.agregar_compra(EMPLEADA, ref, "Contenedor de plantas", "500")
    proyectos.agregar_compra(EMPLEADA, ref, "Transporte", "80")

    ficha = proyectos.detalle(ref)
    assert ficha["totales"]["cotizado"] == 2000.0
    assert ficha["totales"]["vendido"] == 1200.0
    assert ficha["totales"]["gastado"] == 580.0
    # La ganancia se mide contra lo que el cliente ya aceptó, no contra lo
    # cotizado: 1200 vendido - 580 de compras.
    assert ficha["totales"]["ganancia"] == 620.0
    assert ficha["cuantas"] == 2


# ---------------------------------------------------------------------------
# Las compras (viven en Odoo)
# ---------------------------------------------------------------------------

def test_la_compra_se_guarda_en_odoo_no_en_la_base_local(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    proyectos.agregar_compra(EMPLEADA, ref, "Tierra y abono", "45.50")

    # En Odoo, colgada del proyecto y con la firma de quién la cargó.
    assert len(odoo.gastos) == 1
    gasto = next(iter(odoo.gastos.values()))
    assert gasto["lead_id"] == _lead_de(odoo, ref)
    assert gasto["concepto"] == "Tierra y abono"
    assert gasto["monto"] == 45.5
    assert "Génesis" in gasto["nota"]

    # Y la tabla local ya no se usa.
    from app.datos import _db
    with _db() as con:
        assert con.execute("SELECT COUNT(*) c FROM proyecto_compras").fetchone()["c"] == 0


def test_las_compras_se_listan_y_se_pueden_quitar(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    proyectos.agregar_compra(EMPLEADA, ref, "Tierra", "45.50")
    compras = proyectos.compras_de(ref)
    assert len(compras) == 1
    assert compras[0]["concepto"] == "Tierra"
    proyectos.quitar_compra(ref, compras[0]["n"])
    assert proyectos.compras_de(ref) == []


def test_no_se_borra_una_compra_de_otro_proyecto(odoo):
    uno = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    otro = proyectos.crear(EMPLEADA, "Ana Pérez", "", "mantenimiento")
    proyectos.agregar_compra(EMPLEADA, uno, "Contenedor", "500")
    ajena = proyectos.compras_de(uno)[0]["n"]
    proyectos.quitar_compra(otro, ajena)
    assert len(proyectos.compras_de(uno)) == 1


def test_una_compra_sin_monto_o_sin_concepto_no_entra(odoo):
    ref = proyectos.crear(EMPLEADA, "Morris Cohen", "", "paisajismo")
    with pytest.raises(ValueError):
        proyectos.agregar_compra(EMPLEADA, ref, "Tierra", "0")
    with pytest.raises(ValueError):
        proyectos.agregar_compra(EMPLEADA, ref, "  ", "50")
    with pytest.raises(ValueError):
        proyectos.agregar_compra(EMPLEADA, "PROYECTO-99", "Tierra", "50")


# ---------------------------------------------------------------------------
# El tablero
# ---------------------------------------------------------------------------

def test_el_kanban_usa_las_cuatro_columnas_del_proyecto(odoo):
    uno = odoo.sembrar_lead("PROYECTO-01", "Proyecto Morris", etapa="cotizado")
    odoo.sembrar_lead("PROYECTO-02", "Proyecto Pérez", etapa="ganado")
    odoo.sembrar_lead("PROYECTO-03", "Proyecto Nuevo", etapa="conversacion")
    odoo.sembrar_orden(uno, "S00021", 1500.0, estado="sale")

    columnas = {c["clave"]: c for c in proyectos.kanban()}
    assert [c["titulo"] for c in proyectos.kanban()] == [
        "Nuevo", "En conversación", "Cotizado", "Ganado"]
    assert [t["ref"] for t in columnas["cotizado"]["tarjetas"]] == ["PROYECTO-01"]
    assert [t["ref"] for t in columnas["ganado"]["tarjetas"]] == ["PROYECTO-02"]
    assert [t["ref"] for t in columnas["conversacion"]["tarjetas"]] == ["PROYECTO-03"]
    assert columnas["nuevo"]["tarjetas"] == []

    tarjeta = columnas["cotizado"]["tarjetas"][0]
    assert tarjeta["nombre"] == "Proyecto Morris"
    assert tarjeta["cotizado"] == 1500.0
    assert tarjeta["fecha"] == "17/09/2026"


def test_un_proyecto_que_no_existe_no_tiene_ficha(odoo):
    assert proyectos.detalle("PROYECTO-99") is None


# ---------------------------------------------------------------------------
# Las pantallas
# ---------------------------------------------------------------------------

def test_el_tablero_muestra_las_cuatro_columnas(odoo, cliente):
    odoo.sembrar_lead("PROYECTO-01", "Proyecto Morris", etapa="cotizado")
    pagina = cliente.get("/proyecto")
    assert pagina.status_code == 200
    for titulo in ("Nuevo", "En conversación", "Cotizado", "Ganado"):
        assert titulo in pagina.text
    assert "PROYECTO-01" in pagina.text
    assert "Proyecto Morris" in pagina.text


def test_crear_desde_la_pantalla_lleva_a_la_ficha(odoo, cliente):
    respuesta = cliente.post("/proyecto/nuevo", follow_redirects=False, data={
        "nombre_proyecto": "Morris", "nombre": "Morris Cohen",
        "celular": "6567-3062", "tipo": "paisajismo", "nota": "Jardín"})
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/proyecto/PROYECTO-01"

    ficha = cliente.get("/proyecto/PROYECTO-01")
    assert ficha.status_code == 200
    assert "Proyecto Morris" in ficha.text
    assert "PAISAJISMO" in ficha.text


def test_un_proyecto_sin_cliente_vuelve_con_el_error(odoo, cliente):
    respuesta = cliente.post("/proyecto/nuevo", follow_redirects=False,
                             data={"nombre": "  ", "tipo": "paisajismo"})
    assert respuesta.status_code == 303
    assert "/proyecto/nuevo?error=" in respuesta.headers["location"]


def test_la_compra_se_agrega_y_se_ve_en_la_ficha(odoo, cliente):
    cliente.post("/proyecto/nuevo", data={"nombre": "Morris Cohen",
                                          "tipo": "paisajismo"})
    cliente.post("/proyecto/PROYECTO-01/compra",
                 data={"concepto": "Contenedor de plantas", "monto": "500"})
    ficha = cliente.get("/proyecto/PROYECTO-01")
    assert "Contenedor de plantas" in ficha.text
    assert "$500.00" in ficha.text


def test_la_ficha_de_un_proyecto_inexistente_devuelve_al_tablero(odoo, cliente):
    respuesta = cliente.get("/proyecto/PROYECTO-99", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/proyecto?error=")
