"""El módulo linear_leads: el equipo LEAD de Linear leído y escrito.

Las pruebas corren en MODO MUESTRA (sin LINEAR_API_KEY), así que el
tablero de ejemplo vive en memoria y nada sale hacia el Linear real. Lo
que se verifica aquí son las reglas, que son lo que no se puede romper:
la escalera que no degrada, el responsable por etiqueta y nunca por
assignee, una sola etiqueta por grupo, y que las etiquetas no se creen.
"""

import pytest

from app import linear_leads


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    linear_leads.reiniciar_muestra()


# ---------------------------------------------------------------------------
# El vocabulario
# ---------------------------------------------------------------------------

def test_el_embudo_tiene_los_8_estados_en_orden():
    assert linear_leads.ORDEN == [
        "NUEVO", "HABLANDO", "COTIZADO", "POR_AGENDAR",
        "AGENDADO", "ENTREGADO", "GANADO", "PERDIDO"]


def test_cada_estado_trae_su_color_de_la_paleta_unica():
    for estado in linear_leads.ESTADOS:
        assert estado["color"].startswith("#")
        assert "background:" in estado["chip"]


def test_los_nombres_son_los_de_las_columnas_de_linear():
    # Si alguien renombra una columna en Linear, el amarre es por ESTE
    # nombre: la prueba deja constancia de cuáles son.
    assert [e["nombre"] for e in linear_leads.ESTADOS] == [
        "Nuevo", "Hablando", "Cotizado", "Por agendar",
        "Agendado", "Entregado", "Ganado", "Perdido"]


def test_los_seis_motivos_de_perdida():
    assert sorted(linear_leads.MOTIVOS_PERDIDA.values()) == [
        "Compró en otro lado", "Fuera de zona", "No respondió",
        "Precio", "Sin stock", "Solo preguntaba"]


def test_los_responsables_salen_de_las_etiquetas():
    # Sumar a alguien al equipo es crear su etiqueta en Linear, sin código.
    assert linear_leads.responsables() == ["Abraham", "Mary", "Ruben", "Salomón"]


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def test_listar_reparte_las_etiquetas_en_sus_grupos():
    tamara = linear_leads.uno("LEAD-91")
    assert tamara["nombre"] == "Tamara"
    assert tamara["pp"] == "PP-70211"
    assert tamara["estado"] == "POR_AGENDAR"
    assert tamara["origen"] == "WhatsApp"
    assert tamara["interes"] == "Plantas"
    assert tamara["pago"] == "Abono 50%"
    assert tamara["resp"] == "Ruben"        # sin el prefijo "Resp: "
    assert tamara["te_toca"] is False


def test_te_toca_es_una_senal_suelta():
    assert linear_leads.uno("LEAD-87")["te_toca"] is True


def test_en_estado_filtra_el_embudo():
    refs = {l["ref"] for l in linear_leads.en_estado("POR_AGENDAR")}
    assert refs == {"LEAD-91", "LEAD-90"}


def test_un_perdido_trae_su_motivo():
    perdido = linear_leads.uno("LEAD-83")
    assert perdido["motivo"] == "Solo preguntaba"
    assert perdido["motivo_clave"] == "SOLO_PREGUNTABA"
    assert perdido["cerrado"] is True


def test_el_nombre_se_saca_del_titulo_con_su_codigo():
    assert linear_leads._nombre_y_pp("Laura Porcell (PP-WATHA)") == (
        "Laura Porcell", "PP-WATHA")
    assert linear_leads._nombre_y_pp("Sin codigo") == ("Sin codigo", "")


# ---------------------------------------------------------------------------
# La escalera: el automático solo avanza
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("desde, hasta, puede", [
    ("NUEVO", "HABLANDO", True),
    ("HABLANDO", "COTIZADO", True),
    ("COTIZADO", "POR_AGENDAR", True),
    ("POR_AGENDAR", "AGENDADO", True),
    ("AGENDADO", "ENTREGADO", True),
    # Hacia atrás, nunca: un pago que llega tarde no devuelve a "Por
    # agendar" un lead ya entregado.
    ("ENTREGADO", "POR_AGENDAR", False),
    ("COTIZADO", "HABLANDO", False),
    ("AGENDADO", "AGENDADO", False),
    # Un lead cerrado no lo mueve ningún automático.
    ("GANADO", "ENTREGADO", False),
    ("PERDIDO", "HABLANDO", False),
])
def test_la_escalera_no_degrada(desde, hasta, puede):
    assert linear_leads.puede_avanzar(desde, hasta) is puede


def test_el_automatico_avanza_y_no_comenta():
    lead = linear_leads.uno("LEAD-86")  # Hablando
    assert linear_leads.mover_estado(lead["id"], "COTIZADO") is True
    assert linear_leads.uno("LEAD-86")["estado"] == "COTIZADO"
    assert linear_leads.comentarios(lead["id"]) == []


def test_el_automatico_no_devuelve_un_lead_entregado():
    lead = linear_leads.uno("LEAD-88")  # Entregado
    assert linear_leads.mover_estado(lead["id"], "POR_AGENDAR") is False
    assert linear_leads.uno("LEAD-88")["estado"] == "ENTREGADO"


def test_la_correccion_manual_exige_motivo():
    lead = linear_leads.uno("LEAD-87")  # Cotizado
    with pytest.raises(linear_leads.ErrorLeads):
        linear_leads.mover_estado(lead["id"], "HABLANDO", manual=True)
    assert linear_leads.uno("LEAD-87")["estado"] == "COTIZADO"


def test_la_correccion_manual_va_hacia_atras_y_queda_firmada():
    lead = linear_leads.uno("LEAD-87")  # Cotizado
    assert linear_leads.mover_estado(
        lead["id"], "HABLANDO", manual=True,
        nota="me equivoqué de tarjeta", autor="Ruben") is True
    assert linear_leads.uno("LEAD-87")["estado"] == "HABLANDO"
    nota = linear_leads.comentarios(lead["id"])[0]["texto"]
    assert "Cotizado" in nota and "Hablando" in nota
    assert "me equivoqué de tarjeta" in nota
    assert "Ruben" in nota  # el bot firma, así que el autor va en el texto


def test_un_estado_que_no_existe_se_rechaza():
    with pytest.raises(linear_leads.ErrorLeads):
        linear_leads.mover_estado(linear_leads.uno("LEAD-86")["id"], "CONTACTADO")


# ---------------------------------------------------------------------------
# Perdido a mano
# ---------------------------------------------------------------------------

def test_perdido_a_mano_pone_la_columna_y_el_motivo():
    lead = linear_leads.uno("LEAD-85")  # Nuevo
    linear_leads.marcar_perdido(lead["id"], "PRECIO", autor="Mary")
    perdido = linear_leads.uno("LEAD-85")
    assert perdido["estado"] == "PERDIDO"
    assert perdido["motivo"] == "Precio"


def test_perdido_sin_motivo_no_se_deja():
    lead = linear_leads.uno("LEAD-85")
    with pytest.raises(linear_leads.ErrorLeads):
        linear_leads.marcar_perdido(lead["id"], "")
    assert linear_leads.uno("LEAD-85")["estado"] == "NUEVO"


# ---------------------------------------------------------------------------
# Etiquetas: una por grupo, el responsable por etiqueta, ninguna se crea
# ---------------------------------------------------------------------------

def test_el_responsable_intercambia_su_etiqueta():
    lead = linear_leads.uno("LEAD-91")  # Resp: Ruben
    linear_leads.poner_responsable(lead["id"], "Mary")
    nuevo = linear_leads.uno("LEAD-91")
    assert nuevo["resp"] == "Mary"
    assert "Resp: Mary" in nuevo["etiquetas"]
    assert "Resp: Ruben" not in nuevo["etiquetas"]


def test_el_responsable_se_puede_quitar():
    lead = linear_leads.uno("LEAD-91")
    linear_leads.poner_responsable(lead["id"], "")
    nuevo = linear_leads.uno("LEAD-91")
    assert nuevo["resp"] == ""
    assert not [e for e in nuevo["etiquetas"] if e.startswith("Resp: ")]


def test_un_lead_sin_responsable_puede_recibir_uno():
    lead = linear_leads.uno("LEAD-90")
    assert lead["resp"] == ""
    linear_leads.poner_responsable(lead["id"], "Salomón")
    assert linear_leads.uno("LEAD-90")["resp"] == "Salomón"


def test_el_grupo_pago_deja_una_sola_etiqueta():
    lead = linear_leads.uno("LEAD-91")  # Abono 50%
    linear_leads.poner_pago(lead["id"], "Pagado 100%")
    nuevo = linear_leads.uno("LEAD-91")
    assert nuevo["pago"] == "Pagado 100%"
    assert "Abono 50%" not in nuevo["etiquetas"]


def test_una_etiqueta_de_pago_inventada_se_rechaza():
    lead = linear_leads.uno("LEAD-91")
    assert linear_leads.poner_pago(lead["id"], "Medio pagado") is False
    assert linear_leads.uno("LEAD-91")["pago"] == "Abono 50%"


def test_te_toca_se_prende_y_se_apaga():
    lead = linear_leads.uno("LEAD-91")
    linear_leads.poner_te_toca(lead["id"], True)
    assert linear_leads.uno("LEAD-91")["te_toca"] is True
    linear_leads.poner_te_toca(lead["id"], False)
    assert linear_leads.uno("LEAD-91")["te_toca"] is False


def test_las_etiquetas_nunca_se_crean(monkeypatch):
    """Con Linear de verdad, una etiqueta que no existe deja el aviso en el
    log y el issue se va sin ella — no se crea NUNCA (ya pasó una vez).

    El catálogo se refresca una sola vez antes de darla por inexistente
    (por si alguien la acaba de crear a mano en Linear), y ahí para: la
    única consulta que sale es la del catálogo, jamás una mutación.
    """
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "1")
    linear_leads._catalogo_cache.update({
        "en": 9e9, "dato": {"equipo": "eq", "estados": {}, "etiquetas": {},
                            "grupos": {}, "responsables": []}})
    linear_leads._lista_cache.update({
        "en": 9e9,
        "dato": [{"id": "i1", "ref": "LEAD-1", "etiquetas": [], "estado": "NUEVO"}]})
    avisos = []
    monkeypatch.setattr(linear_leads, "registro_aviso", avisos.append)
    pedidas = []

    def falso_pedir(consulta, variables=None):
        pedidas.append(consulta)
        assert "mutation" not in consulta, "una etiqueta no se crea nunca"
        return {"teams": {"nodes": [{"id": "eq", "states": {"nodes": []},
                                     "labels": {"nodes": []}}]}}

    monkeypatch.setattr(linear_leads, "_pedir", falso_pedir)

    assert linear_leads.poner_label("i1", "Resp: Fulano") is False
    assert len(pedidas) == 1  # el refresco del catálogo, y nada más
    assert avisos and "no se crea" in avisos[0]


# ---------------------------------------------------------------------------
# Los tres modos
# ---------------------------------------------------------------------------

def test_sin_clave_el_modo_es_muestra():
    assert linear_leads.modo() == "muestra"
    assert linear_leads.configurado() is False


def test_con_clave_y_sin_interruptor_es_solo_lectura(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "0")
    assert linear_leads.modo() == "lectura"
    with pytest.raises(linear_leads.ErrorLeads):
        linear_leads._exigir_escritura()


def test_con_clave_y_el_interruptor_escribe(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "1")
    assert linear_leads.modo() == "escritura"
    assert linear_leads._exigir_escritura() is None


def test_las_consultas_acotan_las_listas_anidadas():
    """Linear cobra la complejidad multiplicando los límites anidados.

    Sin `first` en `teams` asume 50, y 50 × 120 etiquetas devolvió "Query
    too complex" contra el Linear real: el catálogo quedaba caído, y con él
    `responsables()` vacío y Control con una sola columna. Las pruebas
    corren en modo muestra y no lo habrían visto nunca, así que la forma de
    las consultas se cuida aquí.
    """
    import re
    # Toda colección que se pide (la que lleva `nodes`) tiene que acotarse.
    for nombre, consulta in (
            ("catalogo", linear_leads.CONSULTA_CATALOGO),
            ("lista", linear_leads.CONSULTA_LISTA),
            ("comentarios", linear_leads.CONSULTA_COMENTARIOS)):
        colecciones = re.findall(r"(\w+)\(([^)]*)\)\s*\{\s*nodes", consulta)
        assert colecciones, nombre
        for campo, argumentos in colecciones:
            assert "first:" in argumentos, f"{nombre}: {campo} sin tope"
    # Y el catálogo pide UN equipo, no los 50 de por defecto.
    assert "teams(first: 1," in linear_leads.CONSULTA_CATALOGO


def test_el_estado_se_reconoce_por_el_nombre_de_la_columna():
    # Cinco de los ocho estados son del mismo tipo `started` en Linear: el
    # nombre es lo único que los distingue.
    assert linear_leads._estado_de(
        {"state": {"name": "Por agendar", "type": "started"}}) == "POR_AGENDAR"
    assert linear_leads._estado_de(
        {"state": {"name": "Backlog", "type": "backlog"}}) == ""
