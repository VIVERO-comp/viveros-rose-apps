"""Pruebas de las cotizaciones de servicio (Alquiler, Boda, Mantenimiento…)
de la pestaña Vender, con un Odoo simulado.

El simulado reproduce lo que importa de vivero_rose_pedidos: las
referencias XML de plantillas y productos SV- (ir.model.data), la nota y
validez de cada plantilla, y el etiquetado de cliente/orden — NO reproduce
las reglas de negocio del addon en sí (como forzar a $0 las plantas
informativas de renta/mantenimiento): esas ya están probadas del lado de
Odoo. Aquí se prueba que cotizaciones.py arma el pedido correcto.
"""

import pytest

from app import cotizaciones, ventas

XML_IDS = {
    "vivero_rose_pedidos.plantilla_servicio_renta": 9001,
    "vivero_rose_pedidos.plantilla_servicio_boda": 9002,
    "vivero_rose_pedidos.plantilla_servicio_evento": 9003,
    "vivero_rose_pedidos.plantilla_servicio_mantenimiento": 9004,
    "vivero_rose_pedidos.plantilla_servicio_paisajismo": 9005,
    "vivero_rose_pedidos.plantilla_servicio_proyecto": 9006,
    "vivero_rose_pedidos.plantilla_servicio_instalacion": 9007,
    "vivero_rose_pedidos.producto_sv_alquiler_evento": 8001,
    "vivero_rose_pedidos.producto_sv_instalacion": 8002,
    "vivero_rose_pedidos.producto_sv_boda": 8003,
    "vivero_rose_pedidos.producto_sv_renta": 8004,
    "vivero_rose_pedidos.producto_sv_transporte": 8005,
    "vivero_rose_pedidos.producto_sv_evento": 8006,
    "vivero_rose_pedidos.producto_sv_mantenimiento_total": 8007,
    "vivero_rose_pedidos.etapa_flujo_cotizado": 7001,
}

PLANTILLAS = {
    9001: {"note": "Nota renta", "number_of_days": 15},
    9002: {"note": "Nota boda", "number_of_days": 15},
    9003: {"note": "Nota evento", "number_of_days": 15},
    9004: {"note": "Nota mantenimiento", "number_of_days": 30},
    9005: {"note": "Nota paisajismo", "number_of_days": 15},
    9006: {"note": "Nota proyecto", "number_of_days": 15},
    9007: {"note": "Nota instalación", "number_of_days": 15},
}


class OdooServicios:
    def __init__(self):
        self.productos = {
            601: {"default_code": "PL-CROTO", "name": "CROTO",
                  "list_price": 45.0, "type": "consu"},
            # Los productos de servicio del addon (los ids de XML_IDS):
            # la edición los lee para saber qué renglón es servicio.
            8001: {"default_code": "SV-ALQUILER",
                   "name": "Alquiler de plantas para evento",
                   "list_price": 0.0, "type": "service"},
            8002: {"default_code": "SV-INSTALACION",
                   "name": "Instalación, transporte y mantenimiento inicial",
                   "list_price": 0.0, "type": "service"},
            8003: {"default_code": "SV-BODA", "name": "Ambientación de boda",
                   "list_price": 0.0, "type": "service"},
            8006: {"default_code": "SV-EVENTO", "name": "Ambientación de evento",
                   "list_price": 0.0, "type": "service"},
            8007: {"default_code": "SV-MANTENIMIENTO",
                   "name": "Mantenimiento por contrato",
                   "list_price": 0.0, "type": "service"},
        }
        self.partners = {}
        self.categorias = {}
        self.tags = {}
        self.ordenes = {}
        self.oportunidades = {}
        self.siguiente = 2000

    def _nuevo(self):
        self.siguiente += 1
        return self.siguiente

    def ejecutar(self, modelo, metodo, args, kw=None):
        kw = kw or {}
        manejador = getattr(self, (modelo + "_" + metodo).replace(".", "_"))
        return manejador(args, kw)

    # ---- referencias XML y plantillas ----
    def ir_model_data_search_read(self, args, kw):
        dominio = args[0]
        modulo = next(c[2] for c in dominio if c[0] == "module")
        nombre = next(c[2] for c in dominio if c[0] == "name")
        res_id = XML_IDS.get(f"{modulo}.{nombre}")
        return [{"res_id": res_id}] if res_id else []

    def sale_order_template_read(self, args, kw):
        tid = args[0][0]
        return [{"id": tid, **PLANTILLAS[tid]}]

    # ---- productos (buscador de plantas y carrito) ----
    def product_product_search_read(self, args, kw):
        texto = next(c[2].lower() for c in args[0]
                     if isinstance(c, list) and c[0] == "name")
        return [{"id": i, **p} for i, p in self.productos.items()
                if texto in p["name"].lower()]

    def product_product_read(self, args, kw):
        return [{"id": i, **self.productos[i]} for i in args[0] if i in self.productos]

    def product_product_search(self, args, kw):
        # Solo por default_code: es como cotizaciones.py busca el producto
        # de los renglones libres (SV-PERSONALIZADO).
        codigo = next(c[2] for c in args[0] if c[0] == "default_code")
        return [i for i, p in self.productos.items() if p.get("default_code") == codigo]

    def product_product_create(self, args, kw):
        nuevo = self._nuevo()
        self.productos[nuevo] = {"list_price": 0.0, **args[0]}
        return nuevo

    # ---- partners: un evaluador de dominio simplificado (solo lo que
    # cotizaciones.py arma: 0, 1 o 2 "|" seguidos de condiciones ilike o
    # =ilike, siempre en OR) ----
    def _condicion(self, p, c):
        campo, op, valor = c
        val = str(p.get(campo) or "")
        if op == "=ilike":
            return val.lower() == str(valor).lower()
        if op == "ilike":
            return str(valor).lower() in val.lower()
        return False

    def _coincide(self, p, dominio):
        condiciones = [c for c in dominio if isinstance(c, list)]
        return any(self._condicion(p, c) for c in condiciones)

    def res_partner_search(self, args, kw):
        ids = [i for i, p in self.partners.items() if self._coincide(p, args[0])]
        limite = kw.get("limit")
        return ids[:limite] if limite else ids

    def res_partner_search_read(self, args, kw):
        campos = kw.get("fields", [])
        limite = kw.get("limit")
        filas = [{"id": i, **{c: p.get(c) for c in campos}} for i, p in self.partners.items()
                 if self._coincide(p, args[0])]
        return filas[:limite] if limite else filas

    def res_partner_read(self, args, kw):
        campos = kw.get("fields", [])
        return [{"id": i, **{c: self.partners[i].get(c) for c in campos}}
                for i in args[0] if i in self.partners]

    def res_partner_create(self, args, kw):
        nuevo = self._nuevo()
        self.partners[nuevo] = {**args[0], "category_id": []}
        return nuevo

    def res_partner_write(self, args, kw):
        for pid in args[0]:
            for campo, valor in args[1].items():
                if campo == "category_id":
                    for comando in valor:
                        if comando[0] == 4:
                            self.partners[pid].setdefault("category_id", []).append(comando[1])
                else:
                    self.partners[pid][campo] = valor
        return True

    # ---- categorías y etiquetas ----
    def res_partner_category_search(self, args, kw):
        nombre = args[0][0][2]
        return [i for i, n in self.categorias.items() if n == nombre]

    def res_partner_category_create(self, args, kw):
        nuevo = self._nuevo()
        self.categorias[nuevo] = args[0]["name"]
        return nuevo

    def crm_tag_search(self, args, kw):
        nombre = args[0][0][2].lower()
        return [i for i, n in self.tags.items() if n.lower() == nombre]

    def crm_tag_create(self, args, kw):
        nuevo = self._nuevo()
        self.tags[nuevo] = args[0]["name"]
        return nuevo

    # ---- oportunidades del Flujo CRM ----
    def crm_lead_create(self, args, kw):
        vals = args[0]
        nuevo = self._nuevo()
        self.oportunidades[nuevo] = {**vals, "tag_ids": [], "expected_revenue": 0.0}
        return nuevo

    def crm_lead_write(self, args, kw):
        for oid in args[0]:
            for campo, valor in args[1].items():
                if campo == "tag_ids":
                    for comando in valor:
                        if comando[0] == 4:
                            self.oportunidades[oid]["tag_ids"].append(comando[1])
                else:
                    self.oportunidades[oid][campo] = valor
        return True

    # ---- órdenes ----
    def sale_order_create(self, args, kw):
        vals = args[0]
        nuevo = self._nuevo()
        lineas = [l[2] for l in vals["order_line"]]
        total = 0.0
        for l in lineas:
            if l.get("display_type"):
                continue
            precio = l.get("price_unit")
            if precio is None:
                precio = self.productos.get(l.get("product_id"), {}).get("list_price", 0.0)
            total += precio * l.get("product_uom_qty", 1)
        self.ordenes[nuevo] = {
            "name": f"S{nuevo}", "amount_total": round(total, 2),
            "vals": vals, "lineas": lineas, "tag_ids": [],
            "state": "draft", "invoice_ids": [],
        }
        return nuevo

    def _total_de(self, lineas):
        total = 0.0
        for l in lineas:
            if l.get("display_type"):
                continue
            precio = l.get("price_unit")
            if precio is None:
                precio = self.productos.get(l.get("product_id"), {}).get("list_price", 0.0)
            total += precio * l.get("product_uom_qty", 1)
        return round(total, 2)

    def sale_order_read(self, args, kw):
        # Los campos que no viven arriba (opportunity_id) caen a los vals
        # con que se creó la orden, como haría Odoo.
        return [{"id": i, **{c: self.ordenes[i].get(c, self.ordenes[i]["vals"].get(c))
                             for c in kw["fields"]}} for i in args[0]]

    def sale_order_search(self, args, kw):
        # Solo por client_order_ref + state: es como se busca la cotización
        # de muestra.
        ref = next((c[2] for c in args[0] if c[0] == "client_order_ref"), None)
        return [i for i, o in self.ordenes.items()
                if o["vals"].get("client_order_ref") == ref]

    def sale_order_search_read(self, args, kw):
        dominio = args[0]
        if dominio and dominio[0][0] == "id":
            ids = [i for i in dominio[0][2] if i in self.ordenes]
        elif dominio and dominio[0][0] == "opportunity_id":
            objetivo = dominio[0][2]
            ids = [i for i, o in self.ordenes.items()
                   if o["vals"].get("opportunity_id") == objetivo]
        else:
            ids = list(self.ordenes)
        return [{"id": i, **{c: self.ordenes[i].get(c) for c in kw["fields"]}}
                for i in ids]

    def sale_order_line_search_read(self, args, kw):
        # Las lineas guardadas son los dicts crudos del create/write; aqui
        # se les da la forma que devuelve Odoo (product_id como [id, nombre],
        # name heredado del producto cuando la linea no trajo uno).
        oid = args[0][0][2]
        filas = []
        for idx, l in enumerate(self.ordenes[oid]["lineas"], start=1):
            pid = l.get("product_id")
            producto = self.productos.get(pid, {})
            filas.append({
                "id": idx,
                "name": l.get("name") or producto.get("name") or "",
                "display_type": l.get("display_type") or False,
                "product_id": [pid, producto.get("name", "")] if pid else False,
                "product_uom_qty": l.get("product_uom_qty", 0.0),
                "price_unit": (l.get("price_unit")
                               if l.get("price_unit") is not None
                               else producto.get("list_price", 0.0)),
            })
        return filas

    def sale_order_unlink(self, args, kw):
        for oid in args[0]:
            self.borradas = getattr(self, "borradas", [])
            self.borradas.append(oid)
            del self.ordenes[oid]
        return True

    def sale_order_write(self, args, kw):
        for oid in args[0]:
            for campo, valor in args[1].items():
                if campo == "order_line":
                    lineas = [c[2] for c in valor if c[0] == 0]
                    self.ordenes[oid]["lineas"] = lineas
                    self.ordenes[oid]["amount_total"] = self._total_de(lineas)
                elif campo == "tag_ids":
                    for comando in valor:
                        if comando[0] == 4:
                            self.ordenes[oid]["tag_ids"].append(comando[1])
                else:
                    self.ordenes[oid][campo] = valor
        return True


@pytest.fixture
def odoo(monkeypatch, tmp_path, db_limpia):
    falso = OdooServicios()
    for variable, valor in {
        "ODOO_URL": "http://odoo-de-prueba:8069", "ODOO_DB": "pruebas",
        "ODOO_USER": "prueba", "ODOO_PASSWORD": "prueba",
        "VENTA_FOTOS_DIR": str(tmp_path / "fotos"),
    }.items():
        monkeypatch.setenv(variable, valor)
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


def test_renta_exige_al_menos_un_servicio(odoo):
    with pytest.raises(ValueError, match="al menos un servicio"):
        cotizaciones.crear_cotizacion(
            {"id": "g", "nombre": "Génesis"}, "renta", "María", "", [], [])


def test_un_parrafo_sin_monto_avisa_y_no_crea_nada(odoo):
    with pytest.raises(ValueError, match="Falta el monto del servicio"):
        cotizaciones.crear_cotizacion(
            {"id": "g", "nombre": "Génesis"}, "instalacion", "María", "",
            [{"texto": "Instalación de 12 palmas", "monto": ""}],
            [{"producto_id": 601, "cantidad": 3}])
    assert not odoo.ordenes


def test_renglon_vacio_se_ignora(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "renta", "María", "",
        [{"texto": "Alquiler de 20 plantas", "monto": "850"},
         {"texto": "", "monto": ""}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    servicios = [l for l in orden["lineas"] if l.get("product_id") == 8001]
    assert len(servicios) == 1
    assert orden["amount_total"] == 850.0


def test_renta_arma_seccion_y_el_parrafo_va_como_descripcion(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "renta", "María", "6567-3062",
        [{"texto": "Alquiler de 20 plantas para el evento del sábado",
          "monto": "850"}], [{"producto_id": 601, "cantidad": 10}])
    orden = odoo.ordenes[registro["orden_id"]]
    tipos = [l.get("display_type") for l in orden["lineas"]]
    assert tipos[0] == "line_section"  # "Alquiler del evento"
    assert orden["lineas"][1]["price_unit"] == 850.0
    assert orden["lineas"][1]["product_id"] == 8001  # SV-ALQUILER
    assert orden["lineas"][1]["name"].startswith("Alquiler de 20 plantas")
    assert tipos[-2] == "line_section"  # "Plantas alquiladas..."
    assert orden["lineas"][-1]["product_id"] == 601  # la planta del carrito
    assert orden["vals"]["tipo_servicio"] == "renta"
    assert orden["vals"]["sale_order_template_id"] == 9001
    assert orden["vals"]["note"] == "Nota renta"
    assert registro["orden"] == orden["name"]


def test_varios_servicios_suman_sus_montos(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "instalacion", "María", "",
        [{"texto": "Instalación de 12 palmas en el jardín frontal", "monto": "250"},
         {"texto": "Transporte y montaje", "monto": "50"},
         {"texto": "Primera visita de mantenimiento incluida", "monto": "0"}],
        [{"producto_id": 601, "cantidad": 1}])
    orden = odoo.ordenes[registro["orden_id"]]
    servicios = [l for l in orden["lineas"] if l.get("product_id") == 8002]
    assert [l["price_unit"] for l in servicios] == [250.0, 50.0, 0.0]
    assert [l["name"] for l in servicios][1] == "Transporte y montaje"
    # El 0 escrito a mano sí crea su renglón (servicio incluido sin cargo).
    assert orden["amount_total"] == pytest.approx(300 + 45.0)


def test_servicio_sin_parrafo_hereda_el_nombre_del_producto(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "boda", "Ana", "",
        [{"texto": "", "monto": "300"}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    linea = next(l for l in orden["lineas"] if l.get("product_id") == 8003)
    assert "name" not in linea
    assert linea["price_unit"] == 300.0


def test_paisajismo_puede_ir_sin_plantas(odoo):
    # Sin mínimo de plantas (pedido del dueño 17/09/2026): una cotización
    # puede ser solo de servicio.
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "paisajismo", "María", "",
        [{"texto": "Diseño e instalación", "monto": "500"}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["amount_total"] == 500.0
    assert not any(l.get("product_id") == 601 for l in orden["lineas"])


def test_paisajismo_cobra_las_plantas_a_precio_de_catalogo(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "paisajismo", "María", "",
        [{"texto": "Diseño e instalación", "monto": "500"}],
        [{"producto_id": 601, "cantidad": 17}])
    orden = odoo.ordenes[registro["orden_id"]]
    planta = next(l for l in orden["lineas"] if l.get("product_id") == 601)
    assert "price_unit" not in planta  # Odoo pone el precio de catálogo
    assert orden["amount_total"] == pytest.approx(500 + 17 * 45.0)


def test_servicios_del_formulario_empareja_los_renglones(odoo):
    assert cotizaciones.servicios_del_formulario(
        ["Uno", "Dos"], ["10"]) == [
            {"texto": "Uno", "monto": "10", "descripcion": ""},
            {"texto": "Dos", "monto": "", "descripcion": ""}]


def test_datos_opcionales_del_cliente_en_una_cotizacion(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "instalacion", "Ana", "", 
        [{"texto": "Instalación", "monto": "300"}], [],
        {"ruc": "155712345-2-2021", "cedula": "8-123-4567",
         "correo": "ana@jardines.com", "direccion": "Vía España"})
    orden = odoo.ordenes[registro["orden_id"]]
    partner = odoo.partners[orden["vals"]["partner_id"]]
    assert partner["vat"] == "155712345-2-2021"
    assert partner["ref"] == "8-123-4567"
    assert partner["email"] == "ana@jardines.com"
    assert partner["street"] == "Vía España"


def test_a_un_cliente_existente_solo_se_le_llenan_los_huecos(odoo):
    odoo.partners[77] = {"name": "Ana", "phone": "6567-3062", "vat": "RUC-VIEJO",
                         "category_id": []}
    cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "instalacion", "Ana", "6567-3062",
        [{"texto": "Instalación", "monto": "300"}], [],
        {"ruc": "RUC-NUEVO", "correo": "ana@jardines.com"})
    # Odoo es la fuente de verdad: el RUC que ya estaba no se toca.
    assert odoo.partners[77]["vat"] == "RUC-VIEJO"
    assert odoo.partners[77]["email"] == "ana@jardines.com"


def test_cliente_se_busca_primero_por_telefono(odoo):
    odoo.partners[55] = {"name": "Nombre viejo", "phone": "6567-3062", "category_id": []}
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "mantenimiento", "Otro nombre",
        "6567-3062", [{"texto": "Contrato mensual", "monto": "250"}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["vals"]["partner_id"] == 55
    assert len(odoo.partners) == 1  # no creó un cliente nuevo


def test_cliente_nuevo_se_etiqueta_por_tipo(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "boda", "Ana", "",
        [{"texto": "Ambientación de la ceremonia", "monto": "300"}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    partner = odoo.partners[orden["vals"]["partner_id"]]
    assert odoo.categorias[partner["category_id"][0]] == "Boda"
    assert odoo.tags[orden["tag_ids"][0]] == "BODA"


def test_crea_oportunidad_en_el_crm_ya_cotizada(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "boda", "Ana", "",
        [{"texto": "Ambientación de la ceremonia", "monto": "300"}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    oportunidad_id = orden["vals"]["opportunity_id"]
    oportunidad = odoo.oportunidades[oportunidad_id]
    assert oportunidad["name"] == "Ana"
    assert oportunidad["type"] == "opportunity"
    assert oportunidad["stage_id"] == 7001  # etapa_flujo_cotizado
    assert oportunidad["expected_revenue"] == 300.0
    assert odoo.tags[oportunidad["tag_ids"][0]] == "BODA"


def test_nombre_obligatorio(odoo):
    with pytest.raises(ValueError, match="nombre"):
        cotizaciones.crear_cotizacion(
            {"id": "g", "nombre": "Génesis"}, "mantenimiento", "", "",
            [{"texto": "Contrato", "monto": "100"}], [])


def test_tipo_desconocido(odoo):
    with pytest.raises(ValueError, match="desconocido"):
        cotizaciones.crear_cotizacion(
            {"id": "g", "nombre": "Génesis"}, "no-existe", "Ana", "", [], [])


def test_personalizada_exige_nombre_y_algo_que_cotizar(odoo):
    with pytest.raises(ValueError, match="nombre"):
        cotizaciones.crear_personalizada({"id": "g", "nombre": "Génesis"}, "", "", [])
    with pytest.raises(ValueError, match="al menos un renglón, un servicio o una planta"):
        cotizaciones.crear_personalizada({"id": "g", "nombre": "Génesis"}, "Ana", "", [])


def test_personalizada_renglones_libres_con_cantidad_y_precio(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "", [],
        [{"texto": "50 sacos de tierra negra", "cantidad": "50", "precio": "4"},
         {"texto": "Mano de obra de la siembra", "cantidad": "", "precio": "120"}])
    orden = odoo.ordenes[registro["orden_id"]]
    # La sección primero (aquí solo hay renglones libres) y luego las líneas.
    assert orden["lineas"][0] == {"display_type": "line_section",
                                 "name": "Renglones", "sequence": 1}
    lineas = [l for l in orden["lineas"] if not l.get("display_type")]
    assert lineas[0]["name"] == "50 sacos de tierra negra"
    assert lineas[0]["product_uom_qty"] == 50.0
    assert lineas[0]["price_unit"] == 4.0
    # Cantidad en blanco = 1.
    assert lineas[1]["product_uom_qty"] == 1.0
    assert orden["amount_total"] == 320.0
    # Todos con el producto SV-PERSONALIZADO, creado al vuelo si no existía.
    codigos = {odoo.productos[l["product_id"]]["default_code"] for l in lineas}
    assert codigos == {"SV-PERSONALIZADO"}


def test_personalizada_separa_plantas_servicios_y_renglones(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "",
        [{"producto_id": 601, "cantidad": 2}],
        [{"texto": "Macetas de barro #12", "cantidad": "4", "precio": "9"}],
        None,
        [{"texto": "Instalación y transporte", "monto": "150"}])
    orden = odoo.ordenes[registro["orden_id"]]
    secciones = [l["name"] for l in orden["lineas"] if l.get("display_type")]
    assert secciones == ["Plantas y materiales", "Servicios", "Renglones"]
    assert orden["amount_total"] == pytest.approx(2 * 45.0 + 150 + 4 * 9)


def test_personalizada_omite_las_secciones_vacias(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "", [], None, None,
        [{"texto": "Solo el servicio", "monto": "80"}])
    orden = odoo.ordenes[registro["orden_id"]]
    secciones = [l["name"] for l in orden["lineas"] if l.get("display_type")]
    assert secciones == ["Servicios"]


def test_personalizada_avisa_del_precio_o_la_descripcion_que_falta(odoo):
    with pytest.raises(ValueError, match="Falta el precio del renglón"):
        cotizaciones.crear_personalizada(
            {"id": "g", "nombre": "Génesis"}, "Ana", "", [],
            [{"texto": "Mano de obra", "cantidad": "1", "precio": ""}])
    with pytest.raises(ValueError, match="Falta la descripción"):
        cotizaciones.crear_personalizada(
            {"id": "g", "nombre": "Génesis"}, "Ana", "", [],
            [{"texto": "", "cantidad": "", "precio": "80"}])
    assert not odoo.ordenes


def test_personalizada_mezcla_renglones_libres_y_catalogo(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "",
        [{"producto_id": 601, "cantidad": 2}],
        [{"texto": "Instalación", "cantidad": "1", "precio": "80"}])
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["amount_total"] == pytest.approx(80 + 2 * 45.0)


def test_personalizada_sin_plantilla(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "",
        [{"producto_id": 601, "cantidad": 2}])
    orden = odoo.ordenes[registro["orden_id"]]
    assert orden["vals"]["tipo_servicio"] == "general"
    assert "sale_order_template_id" not in orden["vals"]
    assert registro["total"] == 90.0
    oportunidad = odoo.oportunidades[orden["vals"]["opportunity_id"]]
    assert oportunidad["expected_revenue"] == 90.0
    assert odoo.tags[oportunidad["tag_ids"][0]] == "SERVICIO"


# ---------------------------------------------------------------------------
# Rutas (FastAPI)
# ---------------------------------------------------------------------------

def test_venta_muestra_los_botones_por_tipo(cliente, odoo):
    pagina = cliente.get("/venta")
    assert "/venta/servicio/renta" in pagina.text
    assert "/venta/servicio-personalizada" in pagina.text
    assert "Alquiler" in pagina.text


def test_formulario_de_tipo_desconocido_redirige(cliente, odoo):
    r = cliente.get("/venta/servicio/no-existe", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/venta"


def test_crear_cotizacion_de_renta_por_http(cliente, odoo):
    r = cliente.post("/venta/servicio/renta",
                     data={"cliente": "María", "celular": "",
                           "servicio_texto": "Alquiler de 20 plantas",
                           "servicio_monto": "850"})
    assert "Cotización de servicio creada" in r.text
    assert "S" in r.text
    pagina = cliente.get("/venta")
    assert "Cotizaciones de servicios" in pagina.text
    assert "María" in pagina.text


def test_sin_monto_reaparece_el_formulario_con_lo_escrito(cliente, odoo):
    # No redirige: vuelve a pintar el formulario con el párrafo intacto,
    # que es lo que se perdería en un redirect.
    r = cliente.post("/venta/servicio/renta",
                     data={"cliente": "María", "celular": "",
                           "servicio_texto": "Alquiler de 20 plantas y montaje",
                           "servicio_monto": ""})
    assert r.status_code == 200
    assert "Falta el monto del servicio" in r.text
    assert "Alquiler de 20 plantas y montaje" in r.text
    assert not odoo.ordenes


def test_servicios_escritos_sobreviven_a_agregar_una_planta(cliente, odoo):
    # venta.js guarda los renglones en el borrador del servidor: al volver
    # del POST de agregar al carrito, el párrafo sigue ahí.
    cliente.post("/venta/borrador",
                 data={"cliente": "Ana", "celular": "", "servicios": "1",
                       "servicio_texto": "Instalación de 12 palmas",
                       "servicio_monto": "250"})
    pagina = cliente.get("/venta/servicio/instalacion")
    assert "Instalación de 12 palmas" in pagina.text
    assert "250" in pagina.text


def test_carrito_de_servicio_reusa_el_de_nueva_venta(cliente, odoo):
    cliente.post("/venta/carrito/agregar",
                 data={"producto_id": 601, "cantidad": 1, "volver": "/venta/servicio/paisajismo"},
                 follow_redirects=False)
    pagina = cliente.get("/venta/servicio/paisajismo")
    assert "CROTO" in pagina.text
    r = cliente.post("/venta/servicio/paisajismo",
                     data={"cliente": "Ana", "servicio_texto": "Diseño e instalación",
                           "servicio_monto": "500"})
    assert "Cotización de servicio creada" in r.text
    # El carrito se vació al crear la cotización.
    assert ventas.carrito_de("genesis") == ([], 0.0)


def test_personalizada_por_http(cliente, odoo):
    cliente.post("/venta/carrito/agregar",
                 data={"producto_id": 601, "cantidad": 3,
                       "volver": "/venta/servicio-personalizada"},
                 follow_redirects=False)
    r = cliente.post("/venta/servicio-personalizada", data={"cliente": "Ana"})
    assert "Cotización creada" in r.text


def test_pdf_de_muestra_reutiliza_una_sola_cotizacion(odoo, monkeypatch):
    monkeypatch.setenv("VENTA_CLIENTE_LOCAL", "77")
    odoo.partners[77] = {"name": "Cliente Local", "category_id": []}
    vistas = []

    def descargar(reporte, orden_id):
        vistas.append((reporte, orden_id, odoo.ordenes[orden_id]["lineas"]))
        return b"%PDF-falso"

    monkeypatch.setattr(ventas, "descargar_pdf", descargar)
    assert cotizaciones.pdf_de_muestra() == b"%PDF-falso"
    secciones = [l["name"] for l in vistas[0][2] if l.get("display_type")]
    assert secciones == ["Plantas y materiales", "Servicio de instalación",
                         "Otros renglones"]
    assert odoo.ordenes[vistas[0][1]]["vals"]["client_order_ref"] == "MUESTRA-PDF"
    # Un segundo clic no crea otra cotización: reusa la misma.
    cotizaciones.pdf_de_muestra()
    assert vistas[1][1] == vistas[0][1]
    assert len(odoo.ordenes) == 1
    # Y no deja nada más: ni oportunidad en el CRM ni fila en el historial.
    assert odoo.oportunidades == {}
    assert not cotizaciones.cotizaciones_todas()


# ---------------------------------------------------------------------------
# Descripción del servicio (22/09/2026): además del título, cada servicio
# lleva un párrafo de descripción opcional que en el PDF sale en gris debajo
# del título, como en las cotizaciones de City Mall (S00077). Viaja a Odoo
# como un renglón line_subsection pegado al servicio.
# ---------------------------------------------------------------------------

def test_formulario_empareja_la_descripcion():
    servicios = cotizaciones.servicios_del_formulario(
        ["Instalación de riego", "Transporte"], ["8000", "30"],
        ["Suministro e instalación con pruebas", ""])
    assert servicios == [
        {"texto": "Instalación de riego", "monto": "8000",
         "descripcion": "Suministro e instalación con pruebas"},
        {"texto": "Transporte", "monto": "30", "descripcion": ""},
    ]


def test_la_descripcion_sale_como_subsection_pegada_al_servicio(odoo):
    registro = cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "renta", "María", "",
        [{"texto": "Alquiler de 20 plantas", "monto": "850",
          "descripcion": "Incluye transporte, montaje y retiro"},
         {"texto": "Instalación", "monto": "100"}], [])
    orden = odoo.ordenes[registro["orden_id"]]
    lineas = orden["lineas"]
    # sección, servicio 1, SU descripción, servicio 2 (sin descripción)
    assert lineas[1]["name"] == "Alquiler de 20 plantas"
    assert lineas[2]["display_type"] == "line_subsection"
    assert lineas[2]["name"] == "Incluye transporte, montaje y retiro"
    assert lineas[3]["name"] == "Instalación"
    assert not any(l.get("display_type") == "line_subsection"
                   for l in lineas[4:])
    # La descripción no toca el total: es un renglón sin monto.
    assert orden["amount_total"] == 950.0


def test_descripcion_sola_no_alcanza(odoo):
    with pytest.raises(ValueError, match="monto"):
        cotizaciones.crear_cotizacion(
            {"id": "g", "nombre": "Génesis"}, "renta", "María", "",
            [{"texto": "", "monto": "", "descripcion": "Solo un párrafo"}], [])
    assert not odoo.ordenes


def test_personalizada_lleva_la_descripcion_del_servicio(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "", [],
        None, None,
        [{"texto": "Instalación de sistema de riego", "monto": "8000",
          "descripcion": "Suministro e instalación, con pruebas"}])
    orden = odoo.ordenes[registro["orden_id"]]
    lineas = orden["lineas"]
    assert lineas[0]["display_type"] == "line_section"  # "Servicios"
    assert lineas[1]["name"] == "Instalación de sistema de riego"
    assert lineas[2]["display_type"] == "line_subsection"
    assert lineas[2]["name"] == "Suministro e instalación, con pruebas"


def test_el_form_trae_el_campo_descripcion(cliente, odoo):
    pagina = cliente.get("/venta/servicio/renta")
    assert 'name="servicio_descripcion"' in pagina.text
    personalizada = cliente.get("/venta/servicio-personalizada")
    assert 'name="servicio_descripcion"' in personalizada.text


def test_la_descripcion_sobrevive_en_el_borrador(cliente, odoo):
    cliente.post("/venta/borrador",
                 data={"cliente": "Ana", "celular": "", "servicios": "1",
                       "servicio_texto": "Instalación de 12 palmas",
                       "servicio_monto": "250",
                       "servicio_descripcion": "Incluye tierra y abono"})
    pagina = cliente.get("/venta/servicio/instalacion")
    assert "Incluye tierra y abono" in pagina.text


def test_crear_renta_por_http_con_descripcion(cliente, odoo):
    r = cliente.post("/venta/servicio/renta",
                     data={"cliente": "María", "celular": "",
                           "servicio_texto": "Alquiler de 20 plantas",
                           "servicio_monto": "850",
                           "servicio_descripcion": "Incluye montaje y retiro"})
    assert "Cotización de servicio creada" in r.text
    orden = list(odoo.ordenes.values())[-1]
    subsecciones = [l for l in orden["lineas"]
                    if l.get("display_type") == "line_subsection"]
    assert [s["name"] for s in subsecciones] == ["Incluye montaje y retiro"]


# ---------------------------------------------------------------------------
# Edición de una cotización (22/09/2026): solo mientras siga en cotización.
# Con factura ligada en Odoo, ni botón ni escritura.
# ---------------------------------------------------------------------------

def _cotizacion_de_renta(odoo, plantas=True):
    return cotizaciones.crear_cotizacion(
        {"id": "g", "nombre": "Génesis"}, "renta", "María", "6567-3062",
        [{"texto": "Alquiler de 20 plantas", "monto": "850",
          "descripcion": "Incluye transporte y montaje"}],
        [{"producto_id": 601, "cantidad": 10}] if plantas else [])


def test_cargar_para_editar_reconstruye_el_formulario(odoo):
    registro = _cotizacion_de_renta(odoo)
    datos = cotizaciones.cargar_para_editar(registro["n"])
    assert datos["editable"] and not datos["facturada"]
    assert datos["servicios"] == [{"texto": "Alquiler de 20 plantas",
                                   "descripcion": "Incluye transporte y montaje",
                                   "monto": "850"}]
    assert datos["plantas"] == [{"producto_id": 601, "nombre": "CROTO",
                                 "cantidad": "10"}]


def test_editar_reescribe_los_renglones_y_el_total(odoo):
    registro = _cotizacion_de_renta(odoo)
    editado = cotizaciones.editar_cotizacion(
        registro["n"],
        [{"texto": "Alquiler de 30 plantas", "monto": "1200",
          "descripcion": "Con retiro al final"}],
        [{"producto_id": "601", "cantidad": "5"}])
    # El fake no reproduce la regla del addon que pone en $0 las plantas
    # informativas de renta (eso se prueba del lado de Odoo): aquí el
    # total suma el servicio + 5 CROTO a precio de lista.
    assert editado["total"] == 1200.0 + 5 * 45.0
    orden = odoo.ordenes[registro["orden_id"]]
    nombres = [l.get("name") for l in orden["lineas"]]
    assert "Alquiler de 30 plantas" in nombres
    assert "Con retiro al final" in nombres  # la descripción sigue viajando
    planta = next(l for l in orden["lineas"] if l.get("product_id") == 601)
    assert planta["product_uom_qty"] == 5.0
    # El ingreso esperado de la oportunidad acompaña al nuevo total.
    oportunidad = odoo.oportunidades[orden["vals"]["opportunity_id"]]
    assert oportunidad["expected_revenue"] == 1200.0 + 5 * 45.0


def test_planta_en_cero_se_quita(odoo):
    registro = _cotizacion_de_renta(odoo)
    cotizaciones.editar_cotizacion(
        registro["n"],
        [{"texto": "Alquiler", "monto": "850"}],
        [{"producto_id": "601", "cantidad": "0"}])
    orden = odoo.ordenes[registro["orden_id"]]
    assert not any(l.get("product_id") == 601 for l in orden["lineas"])


def test_facturada_no_se_edita(odoo):
    registro = _cotizacion_de_renta(odoo)
    odoo.ordenes[registro["orden_id"]]["invoice_ids"] = [901]
    lineas_antes = list(odoo.ordenes[registro["orden_id"]]["lineas"])
    with pytest.raises(ValueError, match="facturada"):
        cotizaciones.editar_cotizacion(
            registro["n"], [{"texto": "Otro", "monto": "1"}], [])
    assert odoo.ordenes[registro["orden_id"]]["lineas"] == lineas_antes


def test_estados_en_odoo_marca_la_facturada(odoo):
    a = _cotizacion_de_renta(odoo)
    b = _cotizacion_de_renta(odoo)
    odoo.ordenes[b["orden_id"]]["invoice_ids"] = [902]
    estados = cotizaciones.estados_en_odoo([a["orden_id"], b["orden_id"]])
    assert estados[a["orden_id"]]["editable"]
    assert estados[b["orden_id"]]["facturada"]
    assert not estados[b["orden_id"]]["editable"]


def test_editar_personalizada_conserva_sus_tres_secciones(odoo):
    registro = cotizaciones.crear_personalizada(
        {"id": "g", "nombre": "Génesis"}, "Ana", "",
        [{"producto_id": 601, "cantidad": 2}],
        [{"texto": "Sacos de tierra", "cantidad": "100", "precio": "5.75"}],
        None,
        [{"texto": "Instalación de riego", "monto": "8000",
          "descripcion": "Con pruebas de funcionamiento"}])
    datos = cotizaciones.cargar_para_editar(registro["n"])
    assert datos["servicios"][0]["texto"] == "Instalación de riego"
    assert datos["servicios"][0]["descripcion"] == "Con pruebas de funcionamiento"
    assert datos["renglones"][0] == {"texto": "Sacos de tierra",
                                     "cantidad": "100", "precio": "5.75"}
    assert datos["plantas"][0]["producto_id"] == 601
    editado = cotizaciones.editar_cotizacion(
        registro["n"],
        [{"texto": "Instalación de riego", "monto": "7500"}],
        [{"producto_id": "601", "cantidad": "2"}],
        [{"texto": "Sacos de tierra", "cantidad": "50", "precio": "5.75"}])
    secciones = [l["name"] for l in odoo.ordenes[registro["orden_id"]]["lineas"]
                 if l.get("display_type") == "line_section"]
    assert secciones == ["Plantas y materiales", "Servicios", "Renglones"]
    assert editado["total"] == 7500 + 50 * 5.75 + 2 * 45.0


def test_editar_por_http_y_volver_anclado(cliente, odoo):
    registro = _cotizacion_de_renta(odoo)
    pagina = cliente.get(f"/venta/servicio/{registro['n']}/editar")
    assert "Alquiler de 20 plantas" in pagina.text
    assert "Incluye transporte y montaje" in pagina.text
    assert "CROTO" in pagina.text
    r = cliente.post(f"/venta/servicio/{registro['n']}/editar",
                     data={"servicio_texto": "Alquiler de 30 plantas",
                           "servicio_monto": "1200",
                           "servicio_descripcion": "",
                           "planta_id": "601", "planta_cantidad": "10"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/venta#cot-{registro['n']}"


def test_la_lista_muestra_editar_solo_en_cotizacion(cliente, odoo):
    editable = _cotizacion_de_renta(odoo)
    facturada = _cotizacion_de_renta(odoo)
    odoo.ordenes[facturada["orden_id"]]["invoice_ids"] = [903]
    pagina = cliente.get("/venta").text
    assert f"/venta/servicio/{editable['n']}/editar" in pagina
    assert f"/venta/servicio/{facturada['n']}/editar" not in pagina
    assert "Facturado" in pagina
    # Y editar la facturada por URL directa tampoco pasa.
    r = cliente.get(f"/venta/servicio/{facturada['n']}/editar",
                    follow_redirects=False)
    assert r.status_code == 303
    assert "facturada" in r.headers["location"]


def test_sin_monto_al_editar_no_pierde_lo_escrito(cliente, odoo):
    registro = _cotizacion_de_renta(odoo)
    r = cliente.post(f"/venta/servicio/{registro['n']}/editar",
                     data={"servicio_texto": "Alquiler corregido",
                           "servicio_monto": "",
                           "planta_id": "601", "planta_cantidad": "10"})
    assert r.status_code == 200
    assert "Falta el monto" in r.text
    assert "Alquiler corregido" in r.text
