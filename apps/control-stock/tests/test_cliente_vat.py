"""Pruebas del B.2 (2/10/2026): la contaminación de cliente / VAT.

El bug: el teléfono identificaba solo (cualquier nombre con el mismo
celular caía en el partner de otra persona), y el borrador —una fila por
empleada— arrastraba el RUC/cédula de un cliente al siguiente, así que un
cliente nuevo nacía con los datos fiscales de otro.

Las reglas que se prueban aquí (decididas por Korto):
1. El teléfono BUSCA/SUGIERE, no identifica solo.
2. Reusar un partner existente SOLO si el teléfono coincide Y el nombre es
   compatible (iguales tras normalizar: sin acentos, minúsculas, espacios
   colapsados). Nada de parecidos ni parciales.
3. Teléfono igual + nombre distinto -> ClienteAjeno: la empleada decide
   (usar ese cliente, o crear uno nuevo), nunca se funde en silencio.
4. Un cliente NUEVO no hereda ruc/cedula del borrador de otro cliente.
5. vat/ref NUNCA se escriben por autocompletado (completar_cliente los
   excluye); solo nacen con el cliente nuevo, de lo tecleado ahora.

El Odoo falso es el estilo de la casa (monkeypatch de ventas._ejecutar):
solo lo que la puerta única toca — res.partner search/read/write/create.
"""

import pytest

from app import cotizaciones, ventas


class OdooClientes:
    """res.partner y nada más: la puerta única no toca otro modelo."""

    def __init__(self):
        self.partners = {}
        self.siguiente = 100

    def ejecutar(self, modelo, metodo, args, kw=None):
        kw = kw or {}
        assert modelo == "res.partner", f"modelo inesperado: {modelo}"
        return getattr(self, metodo)(args, kw)

    def _condicion(self, p, c):
        campo, op, valor = c
        val = str(p.get(campo) or "")
        if op == "=ilike":
            return val.lower() == str(valor).lower()
        if op == "ilike":
            return str(valor).lower() in val.lower()
        return False

    def search(self, args, kw):
        condiciones = [c for c in args[0] if isinstance(c, list)]
        ids = [i for i, p in self.partners.items()
               if any(self._condicion(p, c) for c in condiciones)]
        limite = kw.get("limit")
        return ids[:limite] if limite else ids

    def read(self, args, kw):
        return [{"id": i, **{c: self.partners[i].get(c) for c in kw.get("fields", [])}}
                for i in args[0] if i in self.partners]

    def write(self, args, kw):
        for pid in args[0]:
            self.partners[pid].update(args[1])
        return True

    def create(self, args, kw):
        self.siguiente += 1
        self.partners[self.siguiente] = dict(args[0])
        return self.siguiente


@pytest.fixture
def odoo(monkeypatch, db_limpia):
    falso = OdooClientes()
    monkeypatch.setattr(ventas, "_ejecutar", falso.ejecutar)
    return falso


# --- la regla del nombre compatible -----------------------------------------

def test_nombres_compatibles_normaliza_acentos_mayusculas_y_espacios():
    assert ventas.nombres_compatibles("José Pérez", "  jose  perez ")
    assert ventas.nombres_compatibles("MARÍA", "maría")
    # Nada de parecidos ni parciales: cualquier otra diferencia es NO.
    assert not ventas.nombres_compatibles("José Pérez", "José")
    assert not ventas.nombres_compatibles("José Pérez", "José Pérez R.")
    assert not ventas.nombres_compatibles("Ana", "Anna")
    assert not ventas.nombres_compatibles("", "")


# --- teléfono + nombre -------------------------------------------------------

def test_mismo_telefono_y_mismo_nombre_reusa_el_partner(odoo):
    odoo.partners[7] = {"name": "José Pérez", "phone": "65673062"}
    pid = ventas.buscar_o_crear_cliente("  jose  perez ", "6567-3062")
    assert pid == 7
    assert len(odoo.partners) == 1
    # El nombre guardado en Odoo manda: no se renombra con lo digitado,
    # así el PDF sale con el mismo nombre que siempre tuvo ese cliente.
    assert odoo.partners[7]["name"] == "José Pérez"


def test_mismo_telefono_y_otro_nombre_no_se_reusa_en_silencio(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    with pytest.raises(ventas.ClienteAjeno) as cayo:
        ventas.buscar_o_crear_cliente("Beto Mendoza", "65673062")
    assert cayo.value.partner_id == 7
    assert cayo.value.nombre_existente == "Zoila González"
    assert "Ese teléfono ya es de otro cliente (id 7" in str(cayo.value)
    # Sin la decisión no se creó nada: ni fusión ni duplicado a ciegas.
    assert len(odoo.partners) == 1


def test_dos_clientes_distintos_pueden_compartir_telefono(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    nuevo = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                          decision="nuevo")
    assert nuevo != 7
    assert odoo.partners[nuevo]["name"] == "Beto Mendoza"
    assert odoo.partners[nuevo]["phone"] == "65673062"
    assert odoo.partners[7]["name"] == "Zoila González"  # intacto
    assert len(odoo.partners) == 2  # no se fundieron


def test_decision_usar_reutiliza_ese_cliente_a_sabiendas(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    pid = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                        decision="usar-7")
    assert pid == 7
    # Tampoco aquí se renombra: usar al existente es usarlo tal cual.
    assert odoo.partners[7]["name"] == "Zoila González"


def test_decision_usar_con_otro_id_vuelve_a_avisar(odoo):
    # Una decisión vieja (de otro intento) no abre la puerta: el id tiene
    # que ser el del cliente que el aviso señaló.
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    with pytest.raises(ventas.ClienteAjeno):
        ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                      decision="usar-99")


def test_decision_nuevo_no_se_funde_ni_por_nombre_exacto(odoo):
    # "Es otro cliente" significa OTRO: ni siquiera un homónimo exacto
    # (con otro teléfono) lo absorbe.
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    odoo.partners[8] = {"name": "Beto Mendoza", "phone": "6111-0000"}
    nuevo = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                          decision="nuevo")
    assert nuevo not in (7, 8)
    assert len(odoo.partners) == 3


# --- vat/ref nunca por autocompletado ----------------------------------------

def test_vat_existente_no_se_sobrescribe(odoo):
    odoo.partners[7] = {"name": "Ana", "phone": "6567-3062", "vat": "RUC-VIEJO"}
    ventas.buscar_o_crear_cliente("Ana", "6567-3062",
                                  datos={"ruc": "RUC-NUEVO"})
    assert odoo.partners[7]["vat"] == "RUC-VIEJO"


def test_cliente_sin_vat_tampoco_recibe_el_de_otro_por_autocompletado(odoo):
    # El hueco fiscal NO se rellena solo: el RUC/cédula del formulario pudo
    # quedar arrastrado de otro cliente. Lo no-fiscal (correo) sí se llena.
    odoo.partners[7] = {"name": "Ana", "phone": "6567-3062"}
    ventas.buscar_o_crear_cliente("Ana", "6567-3062",
                                  datos={"ruc": "RUC-AJENO",
                                         "cedula": "8-999-0000",
                                         "correo": "ana@jardines.com"})
    assert not odoo.partners[7].get("vat")
    assert not odoo.partners[7].get("ref")
    assert odoo.partners[7]["email"] == "ana@jardines.com"


def test_reusar_con_decision_tampoco_escribe_fiscales(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                  decision="usar-7",
                                  datos={"cedula": "8-123-4567"})
    assert not odoo.partners[7].get("vat")
    assert not odoo.partners[7].get("ref")


def test_cliente_nuevo_si_nace_con_lo_tecleado_ahora(odoo):
    pid = ventas.buscar_o_crear_cliente(
        "Carla Ríos", "6400-1122",
        datos={"ruc": "155712345-2-2021", "cedula": "8-123-4567"})
    assert odoo.partners[pid]["vat"] == "155712345-2-2021"
    assert odoo.partners[pid]["ref"] == "8-123-4567"


def test_ruc_y_cedula_no_se_mezclan_entre_clientes(odoo):
    # A nace con su cédula; B comparte el teléfono y nace aparte SIN datos
    # fiscales: no hereda los de A por ningún camino.
    a = ventas.buscar_o_crear_cliente("Ana Vega", "6567-3062",
                                      datos={"cedula": "8-111-2222"})
    b = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                      decision="nuevo")
    assert odoo.partners[a]["vat"] == "8-111-2222"
    assert odoo.partners[a]["ref"] == "8-111-2222"
    assert "vat" not in odoo.partners[b]
    assert "ref" not in odoo.partners[b]


def test_completar_cliente_excluye_vat_y_ref_siempre(odoo):
    odoo.partners[7] = {"name": "Ana"}
    ventas.completar_cliente(7, {"vat": "RUC-X", "ref": "8-1", "street": "Vía España"})
    assert not odoo.partners[7].get("vat")
    assert not odoo.partners[7].get("ref")
    assert odoo.partners[7]["street"] == "Vía España"


# --- las puertas de Nueva Venta y de las cotizaciones son LA MISMA -----------

def test_cliente_id_de_cotizaciones_delega_en_la_puerta_unica(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    with pytest.raises(ventas.ClienteAjeno):
        cotizaciones._cliente_id("Beto Mendoza", "6567-3062")
    # Y guarda el phone solo en dígitos, como siempre hizo este módulo.
    pid = cotizaciones._cliente_id("Beto Mendoza", "6567-3062", decision="nuevo")
    assert odoo.partners[pid]["phone"] == "65673062"


def test_cliente_id_de_ventas_guarda_el_phone_tal_cual(odoo, monkeypatch):
    pid = ventas._cliente_id("María", "6567-3062")
    assert odoo.partners[pid]["phone"] == "6567-3062"
    odoo.partners[pid]["phone"] = "6567-3062"
    with pytest.raises(ventas.ClienteAjeno):
        ventas._cliente_id("Otro Nombre", "65673062")


# --- el borrador ya no contamina al cliente siguiente -------------------------

def test_cliente_nuevo_no_hereda_los_fiscales_del_borrador(db_limpia):
    # La venta de Ana dejó su RUC/cédula en el borrador; la empleada borra
    # el nombre, escribe el del cliente siguiente, y venta.js repite los
    # campos tal cual se ven (el eco del prellenado): esos fiscales NO son
    # del cliente nuevo y se vacían.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021",
                                   "cedula": "8-111-2222",
                                   "correo": "ana@jardines.com"})
    ventas.guardar_borrador("genesis", "Beto Mendoza", "6400-1122",
                            datos={"ruc": "155712345-2-2021",
                                   "cedula": "8-111-2222",
                                   "correo": "ana@jardines.com"})
    borrador = ventas.borrador_de("genesis")
    assert borrador["ruc"] == ""
    assert borrador["cedula"] == ""


def test_el_cambio_de_nombre_limpia_los_fiscales_aunque_el_form_no_los_traiga(db_limpia):
    # La vía de herencia original: _datos_cliente_del_form devuelve None si
    # el formulario no trae los campos, y el COALESCE del borrador dejaba
    # el RUC del cliente anterior vivo para siempre.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021", "cedula": "8-111-2222"})
    ventas.guardar_borrador("genesis", "Beto Mendoza", "6400-1122", datos=None)
    borrador = ventas.borrador_de("genesis")
    assert borrador["ruc"] == ""
    assert borrador["cedula"] == ""


def test_lo_tecleado_para_el_cliente_nuevo_si_se_queda(db_limpia):
    # Si junto con el nombre nuevo viene una cédula DISTINTA a la guardada,
    # eso se tecleó ahora y es del cliente nuevo: se respeta.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"cedula": "8-111-2222"})
    ventas.guardar_borrador("genesis", "Beto Mendoza", "6400-1122",
                            datos={"cedula": "8-999-0000"})
    assert ventas.borrador_de("genesis")["cedula"] == "8-999-0000"


def test_el_mismo_cliente_conserva_sus_fiscales_en_el_borrador(db_limpia):
    # Mientras el nombre siga siendo el mismo (tras normalizar), nada se
    # pierde: los reloads de agregar/quitar plantas siguen como siempre.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021"})
    ventas.guardar_borrador("genesis", "  ana  vega ", "6567-3062", datos=None)
    assert ventas.borrador_de("genesis")["ruc"] == "155712345-2-2021"


# --- el aviso llega a la pantalla ---------------------------------------------

def test_el_formulario_pinta_las_dos_opciones_del_conflicto(cliente, odoo, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "prueba")
    ventas.reiniciar_cache()

    def partners_nada_mas(modelo, metodo, args, kw=None):
        if modelo == "res.partner":
            return OdooClientes().ejecutar(modelo, metodo, args, kw)
        return []  # product.product de la personalizada, etc.

    monkeypatch.setattr(ventas, "_ejecutar", partners_nada_mas)
    pagina = cliente.get("/venta/nueva", params={
        "error": "Ese teléfono ya es de otro cliente (id 7: Zoila González).",
        "conflicto": "7", "conflicto_nombre": "Zoila González"}).text
    assert "cliente_decision" in pagina
    assert 'value="usar-7"' in pagina
    assert 'value="nuevo"' in pagina
    assert "Zoila González" in pagina
