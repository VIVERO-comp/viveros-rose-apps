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
        if op in ("=", "=="):
            return val == str(valor)
        if op == "=ilike":
            return val.lower() == str(valor).lower()
        if op == "ilike":
            return str(valor).lower() in val.lower()
        if op == "like":
            return str(valor) in val
        if op == "not like":
            # Como en Odoo: el partner sin ese campo también pasa.
            return str(valor) not in val
        return False

    def _evaluar(self, dominio, p):
        """La notación polaca de Odoo ("|", "&", "!" como prefijos; AND
        implícito entre criterios sueltos): desde la exclusión del comodín
        (_sin_comodines, 5/10/2026) el dominio mezcla un AND arriba con el
        OR de teléfonos, y un fake que haga any() de todo ya no sirve."""
        def parcial(i):
            token = dominio[i]
            if token == "|":
                a, i = parcial(i + 1)
                b, i = parcial(i)
                return a or b, i
            if token == "&":
                a, i = parcial(i + 1)
                b, i = parcial(i)
                return a and b, i
            if token == "!":
                a, i = parcial(i + 1)
                return not a, i
            return self._condicion(p, token), i + 1

        i, resultado = 0, True
        while i < len(dominio):
            r, i = parcial(i)
            resultado = resultado and r
        return resultado

    def search(self, args, kw):
        ids = [i for i, p in self.partners.items()
               if self._evaluar(args[0], p)]
        limite = kw.get("limit")
        return ids[:limite] if limite else ids

    def read(self, args, kw):
        return [{"id": i, **{c: self.partners[i].get(c) for c in kw.get("fields", [])}}
                for i in args[0] if i in self.partners]

    def search_read(self, args, kw):
        # Lo usa cotizaciones.buscar_clientes (el autocompletado).
        return self.read([self.search(args, kw)], kw)

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
    assert cayo.value.telefono == "6567-3062"
    # El aviso dice nombre y teléfono, no el id (2/10/2026): la empleada
    # reconoce al cliente por sus datos; el id es técnico.
    assert ("Ese teléfono ya es de otro cliente "
            "(Zoila González · 6567-3062)" in str(cayo.value))
    assert "id 7" not in str(cayo.value)
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


def test_reusar_con_decision_tampoco_escribe_fiscales_sin_confirmar(odoo):
    # R2 (2/10/2026): la elección usar-<id> con una cédula tecleada ya no
    # descarta en silencio NI escribe en silencio — pide confirmación
    # (ConfirmarDatoFiscal, probado abajo). Sin ella, nada fiscal se toca.
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    with pytest.raises(ventas.ConfirmarDatoFiscal):
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
        "error": "Ese teléfono ya es de otro cliente "
                 "(Zoila González · 6567-3062).",
        "conflicto": "7", "conflicto_nombre": "Zoila González",
        "conflicto_telefono": "6567-3062"}).text
    assert "cliente_decision" in pagina
    assert 'value="usar-7"' in pagina
    assert 'value="nuevo"' in pagina
    # El aviso muestra nombre y teléfono; el id 7 solo vive en el value
    # técnico usar-7 del radio, nunca en el texto que lee la empleada.
    assert "Zoila González · 6567-3062" in pagina
    assert "id 7" not in pagina


def test_el_redirect_del_conflicto_lleva_nombre_y_telefono():
    # El 303 tras un ClienteAjeno (2/10/2026): el teléfono viaja en la URL
    # (conflicto_telefono) para que /venta/nueva lo pinte junto al nombre;
    # el id sigue viajando, pero solo alimenta el value de usar-<id>.
    from app import main as app_main
    error = ventas.ClienteAjeno(7, "Zoila González", telefono="6567-3062")
    respuesta = app_main._redirigir_venta(
        str(error), nueva=True, conflicto=app_main._conflicto_de(error))
    destino = respuesta.headers["location"]
    assert "conflicto=7" in destino
    assert "conflicto_telefono=6567-3062" in destino
    assert "conflicto_nombre=" in destino


# --- R1 (2/10/2026): el borrador no aporta NADA del cliente anterior ---------

def test_correo_y_direccion_tampoco_se_heredan_al_cambiar_de_cliente(db_limpia):
    # Cambió el cliente y el formulario no trajo los campos: el borrador
    # deja TODO dato de cliente en blanco; los cargos (envío) no son dato
    # del cliente y sobreviven.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021",
                                   "cedula": "8-111-2222",
                                   "correo": "ana@jardines.com",
                                   "direccion": "Vía España",
                                   "envio": "10"})
    ventas.guardar_borrador("genesis", "Beto Mendoza", "", datos=None)
    borrador = ventas.borrador_de("genesis")
    for campo in ("ruc", "cedula", "correo", "direccion"):
        assert borrador[campo] == "", campo
    assert borrador["envio"] == "10"  # el cargo no es dato del cliente


def test_el_eco_no_vuelve_en_los_posts_siguientes(db_limpia):
    # venta.js reenvía el formulario completo a cada tecla, con los datos
    # del cliente anterior todavía pintados en pantalla: el filtro
    # (cliente_anterior) tiene que aguantar el SEGUNDO y el TERCER eco,
    # no solo el primero.
    eco = {"ruc": "155712345-2-2021", "cedula": "8-111-2222",
           "correo": "ana@jardines.com", "direccion": "Vía España"}
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062", datos=eco)
    ventas.guardar_borrador("genesis", "B", "6567-3062", datos=eco)
    ventas.guardar_borrador("genesis", "Beto", "6567-3062", datos=eco)
    ventas.guardar_borrador("genesis", "Beto Mendoza", "6567-3062", datos=eco)
    borrador = ventas.borrador_de("genesis")
    for campo in ("ruc", "cedula", "correo", "direccion"):
        assert borrador[campo] == "", campo


def test_telefono_completo_distinto_tambien_cuenta_como_cambio(db_limpia):
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021"})
    ventas.guardar_borrador("genesis", "Ana Vega", "6400-1122",
                            datos={"ruc": "155712345-2-2021"})
    assert ventas.borrador_de("genesis")["ruc"] == ""


def test_telefono_a_medio_teclear_no_es_cambio(db_limpia):
    # El mismo cliente con el celular apenas empezado (venta.js guarda a
    # cada tecla): nada se pierde.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021"})
    ventas.guardar_borrador("genesis", "Ana Vega", "64",
                            datos={"ruc": "155712345-2-2021"})
    assert ventas.borrador_de("genesis")["ruc"] == "155712345-2-2021"


def test_crear_sin_recargar_tampoco_lleva_el_eco_a_odoo(db_limpia):
    # La otra puerta del eco: cambiar el nombre y CREAR sin que la página
    # se recargue — el formulario manda los datos del cliente anterior
    # directo a la creación, sin pasar por el borrador.
    ventas.guardar_borrador("genesis", "Ana Vega", "6567-3062",
                            datos={"ruc": "155712345-2-2021",
                                   "correo": "ana@jardines.com"})
    filtrado = ventas.datos_del_cliente_actual(
        "genesis", "Beto Mendoza", "6400-1122",
        {"ruc": "155712345-2-2021", "cedula": "8-999-0000",
         "correo": "ana@jardines.com"})
    assert filtrado["ruc"] == ""                 # eco de Ana
    assert filtrado["correo"] == ""              # eco de Ana
    assert filtrado["cedula"] == "8-999-0000"    # tecleado para Beto


# --- R2: dato fiscal en cliente existente solo con confirmación --------------

def test_usar_cliente_sin_vat_con_cedula_pide_confirmacion(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    with pytest.raises(ventas.ConfirmarDatoFiscal) as cayo:
        ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                      decision="usar-7",
                                      datos={"cedula": "8-123-4567"})
    assert cayo.value.partner_id == 7
    assert "guardar" in str(cayo.value)
    assert "8-123-4567" in str(cayo.value)
    # El aviso nombra al cliente con su teléfono, nunca con el id.
    assert "del cliente Zoila González · 6567-3062" in str(cayo.value)
    assert "id 7" not in str(cayo.value)
    assert cayo.value.telefono == "6567-3062"
    # Sin confirmar, nada fiscal se escribió.
    assert not odoo.partners[7].get("vat")
    assert not odoo.partners[7].get("ref")


def test_confirmar_si_guarda_el_dato_en_ese_cliente(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    pid = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                        decision="usar-7",
                                        datos={"cedula": "8-123-4567"},
                                        confirmar_fiscal="si")
    assert pid == 7
    assert odoo.partners[7]["vat"] == "8-123-4567"
    assert odoo.partners[7]["ref"] == "8-123-4567"


def test_confirmar_no_sigue_la_venta_sin_tocar_lo_fiscal(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062"}
    pid = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                        decision="usar-7",
                                        datos={"cedula": "8-123-4567"},
                                        confirmar_fiscal="no")
    assert pid == 7
    assert not odoo.partners[7].get("vat")
    assert not odoo.partners[7].get("ref")


def test_vat_distinto_pide_confirmacion_aparte_y_no_pisa_sin_ella(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062",
                        "vat": "RUC-VIEJO", "ref": "RUC-VIEJO"}
    with pytest.raises(ventas.ConfirmarDatoFiscal) as cayo:
        ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                      decision="usar-7",
                                      datos={"cedula": "8-123-4567"})
    # El aviso dice que REEMPLAZA y muestra el valor que ya tiene.
    assert "REEMPLAZAR" in str(cayo.value)
    assert "RUC-VIEJO" in str(cayo.value)
    assert odoo.partners[7]["vat"] == "RUC-VIEJO"  # intacto sin confirmar
    pid = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                        decision="usar-7",
                                        datos={"cedula": "8-123-4567"},
                                        confirmar_fiscal="si")
    assert pid == 7
    assert odoo.partners[7]["vat"] == "8-123-4567"  # pisado SOLO con el sí


def test_vat_ya_igual_no_pide_nada(odoo):
    odoo.partners[7] = {"name": "Zoila González", "phone": "6567-3062",
                        "vat": "8-123-4567", "ref": "8-123-4567"}
    pid = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                        decision="usar-7",
                                        datos={"cedula": "8-123-4567"})
    assert pid == 7


def test_sin_eleccion_explicita_lo_fiscal_sigue_prohibido(odoo):
    # La reutilización AUTOMÁTICA (teléfono + nombre compatible) no pasa
    # por R2: el autocompletado fiscal sigue excluido del todo.
    odoo.partners[7] = {"name": "Beto Mendoza", "phone": "6567-3062"}
    pid = ventas.buscar_o_crear_cliente("Beto Mendoza", "6567-3062",
                                        datos={"cedula": "8-123-4567"},
                                        confirmar_fiscal="si")
    assert pid == 7
    assert not odoo.partners[7].get("vat")
    assert not odoo.partners[7].get("ref")


# --- R3: sin teléfono, el nombre no reutiliza en silencio --------------------

def test_sin_telefono_el_nombre_que_casa_no_se_reusa_solo(odoo):
    odoo.partners[7] = {"name": "María López", "phone": ""}
    with pytest.raises(ventas.ClienteAjeno) as cayo:
        ventas.buscar_o_crear_cliente("maría lópez", "")
    assert cayo.value.partner_id == 7
    assert cayo.value.motivo == "nombre"
    # Sin teléfono guardado el aviso muestra solo el nombre: ni el id,
    # ni un «· None» colgando.
    assert "Ya existe un cliente con ese nombre (María López)." in str(cayo.value)
    assert "id 7" not in str(cayo.value) and "·" not in str(cayo.value)
    assert len(odoo.partners) == 1  # nada creado sin la decisión


def test_sin_telefono_usar_id_reusa_y_nuevo_crea_aparte(odoo):
    odoo.partners[7] = {"name": "María López", "phone": ""}
    assert ventas.buscar_o_crear_cliente("María López", "",
                                         decision="usar-7") == 7
    nuevo = ventas.buscar_o_crear_cliente("María López", "",
                                          decision="nuevo")
    assert nuevo != 7
    assert len(odoo.partners) == 2


def test_cotizaciones_tambien_avisa_por_nombre_sin_telefono(odoo):
    odoo.partners[7] = {"name": "María López", "phone": ""}
    with pytest.raises(ventas.ClienteAjeno):
        cotizaciones._cliente_id("María López", "")


# --- La vista previa y el cliente (bug Nº2, 5/10/2026) -----------------------
# La vista previa ya NO crea ni usa partners reales: trabaja sobre el
# comodín de la empleada (probado en test_ventas.py), y la puerta única
# lo deja FUERA de sus búsquedas aunque cargue el nombre y el teléfono del
# último vistazo. El recuerdo del borrador guarda solo el TEXTO tecleado
# (nunca un partner id) y se olvida al cambiar de cliente y al terminar la
# venta. [La exención «recordado» del 2/10 se retiró: las preguntas por el
# cliente viven SOLO al concretar.]

def _sembrar_comodin(odoo, usuario, nombre, phone=""):
    pid = ventas._partner_vista_previa(usuario, nombre, phone)
    assert ventas.MARCA_COMODIN in odoo.partners[pid]["comment"]
    return pid


def test_el_comodin_no_se_encuentra_por_telefono(odoo):
    # El vistazo dejó al comodín con el teléfono de Marta; concretar con
    # ese mismo teléfono NO tropieza con él: crea el cliente real.
    comodin = _sembrar_comodin(odoo, "genesis", "Marta", "6000-0000")
    pid = ventas.buscar_o_crear_cliente("Marta", "6000-0000")
    assert pid != comodin
    assert odoo.partners[pid]["name"] == "Marta"
    assert "comment" not in odoo.partners[pid]


def test_el_comodin_no_se_encuentra_por_nombre(odoo):
    # Tampoco por nombre (el caso R3 sin teléfono): sin la exclusión, el
    # homónimo que levanta ClienteAjeno sería el propio comodín y la
    # empleada podría colgarle la venta real con «usar».
    comodin = _sembrar_comodin(odoo, "genesis", "María López")
    pid = ventas.buscar_o_crear_cliente("María López", "")
    assert pid != comodin
    assert len(odoo.partners) == 2  # comodín + la María real nueva


def test_un_cliente_real_homonimo_si_pregunta_al_concretar(odoo):
    # Los avisos B.2 quedan intactos: con una María REAL en Odoo, la
    # pregunta llega (al concretar, nunca en el vistazo) y señala a la
    # real, no al comodín.
    _sembrar_comodin(odoo, "genesis", "María López")
    odoo.partners[7] = {"name": "María López", "phone": ""}
    with pytest.raises(ventas.ClienteAjeno) as cayo:
        ventas.buscar_o_crear_cliente("María López", "")
    assert cayo.value.partner_id == 7


def test_el_comodin_se_crea_una_sola_vez_por_empleada(odoo):
    a = ventas._partner_vista_previa("genesis", "Marta", "6000-0000")
    b = ventas._partner_vista_previa("genesis", "Rosa Díaz", "6111-2222")
    assert a == b
    assert odoo.partners[a]["name"] == "Rosa Díaz"
    assert odoo.partners[a]["phone"] == "6111-2222"
    assert ventas._partner_vista_previa("ruben", "Marta") != a


def test_el_comodin_de_ana_no_es_el_de_anabel(odoo):
    # El separador de la marca evita que «ana» calce con el comodín de
    # «anabel» en la búsqueda por like (y al revés).
    anabel = ventas._partner_vista_previa("anabel", "Cliente X")
    ana = ventas._partner_vista_previa("ana", "Cliente Y")
    assert ana != anabel
    assert len(odoo.partners) == 2


def test_buscar_clientes_no_lista_el_comodin(odoo):
    # El autocompletado de las cotizaciones tampoco lo ofrece.
    _sembrar_comodin(odoo, "genesis", "Marta", "6000-0000")
    odoo.partners[7] = {"name": "Marta Real", "phone": "6000-0000"}
    resultados = cotizaciones.buscar_clientes("Marta")
    assert [r["id"] for r in resultados] == [7]


def test_el_recuerdo_guarda_solo_el_texto_tecleado(db_limpia):
    ventas.recordar_cliente_vista_previa("genesis", "María López", "6567-3062")
    assert ventas.cliente_de_vista_previa("genesis") == {
        "nombre": "María López", "celular": "6567-3062"}
    assert ventas.cliente_de_vista_previa("ruben") is None  # por empleada


def test_un_recuerdo_viejo_con_partner_id_ya_no_aplica(db_limpia):
    # Un borrador guardado por la versión del 2/10 (con partner id) no
    # puede volver como exención: se trata como ausencia.
    import json as _json
    with ventas._db() as con:
        con.execute(
            "INSERT INTO venta_borrador (usuario, preview_partner)"
            " VALUES (?,?)",
            ("genesis", _json.dumps({"id": 7, "nombre": "María López",
                                     "celular": ""})))
    assert ventas.cliente_de_vista_previa("genesis") is None


def test_el_recuerdo_se_olvida_al_cambiar_de_cliente_en_el_borrador(db_limpia):
    ventas.recordar_cliente_vista_previa("genesis", "María López", "6567-3062")
    # El mismo cliente en el borrador no borra nada.
    ventas.guardar_borrador("genesis", "María López", "6567-3062")
    assert ventas.cliente_de_vista_previa("genesis")["nombre"] == "María López"
    # Cambió el cliente (la lógica R1/_es_otro_cliente): se olvida.
    ventas.guardar_borrador("genesis", "Beto Mendoza", "6111-0000")
    assert ventas.cliente_de_vista_previa("genesis") is None


def test_el_recuerdo_se_olvida_al_terminar_la_venta(db_limpia):
    ventas.recordar_cliente_vista_previa("genesis", "María López", "")
    ventas._limpiar_borrador("genesis")
    assert ventas.cliente_de_vista_previa("genesis") is None


# --- R4: ninguna opción viene preseleccionada ---------------------------------

def test_los_radios_del_conflicto_no_traen_checked():
    import pathlib

    from app import main as app_main
    plantilla = (pathlib.Path(app_main.__file__).parent
                 / "plantillas" / "_cliente.html").read_text()
    assert 'name="cliente_decision"' in plantilla
    assert 'name="confirmar_fiscal"' in plantilla
    assert "checked" not in plantilla


def test_la_confirmacion_fiscal_se_pinta_sin_preseleccion(cliente, odoo, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    for variable in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD"):
        monkeypatch.setenv(variable, "prueba")
    ventas.reiniciar_cache()

    def partners_nada_mas(modelo, metodo, args, kw=None):
        if modelo == "res.partner":
            return OdooClientes().ejecutar(modelo, metodo, args, kw)
        return []

    monkeypatch.setattr(ventas, "_ejecutar", partners_nada_mas)
    pagina = cliente.get("/venta/nueva", params={
        "error": "x", "fiscal": "7", "fiscal_nombre": "Zoila González",
        "fiscal_detalle": "Se va a guardar el RUC/Tax ID «8-123-4567» del "
                          "cliente Zoila González · 6567-3062."}).text
    assert 'name="confirmar_fiscal"' in pagina
    assert 'value="si"' in pagina and 'value="no"' in pagina
    assert 'value="usar-7"' in pagina          # la elección viaja de vuelta
    assert "8-123-4567" in pagina
    bloque = pagina[pagina.index('id="confirmar-fiscal"'):]
    bloque = bloque[:bloque.index("</div>")]
    assert "checked" not in bloque
