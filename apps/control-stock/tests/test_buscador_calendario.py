"""EL BUSCADOR ÚNICO de «Actividad nueva» (BLOQUE 59, item 4 de Jay).

Estas pruebas corren **con Linear SIMULADO**: sin `LINEAR_API_KEY`,
`calendario.modo()` y `linear_leads.modo()` dan `"muestra"` y cada
escritura se bifurca a memoria (`_muestra_crear`, `_muestra_mover`). Las
validaciones NO se saltan —`crear()` valida tipo/cliente/fecha y
`mover_estado()` valida `puede_avanzar()` antes de bifurcar—, así que el
camino entero se recorre de verdad sin tocar el CALENDARIO ROSE, ni el
equipo LEAD, ni Odoo, ni Twenty.

**La prueba que importa es `test_lo_elegido_en_el_buscador_llega_al_
guardado`**, y lo que mide no es que el buscador PINTE: mide que el dato
VIAJE. Cada salto se lee del HTML renderizado en vez de escribirse a mano:

    1. el pedazo del buscador trae una fila                 → se saca su href
    2. ese href devuelve el formulario                      → se sacan sus campos
    3. esos campos se mandan al POST de siempre             → se mira qué nació

Si en cualquier salto se perdiera el `lead` o el `cliente` —un campo
renombrado, un enlace que no lo lleva, un `name` que el POST no lee— la
prueba falla, porque nada de lo que viaja lo escribe la prueba.
"""

import html
import re
from urllib.parse import unquote

import pytest

from app import agenda, calendario, contactos, datos, linear_leads


@pytest.fixture(autouse=True)
def linear_simulado(monkeypatch, db_limpia):
    """Linear SIMULADO y las otras dos fuentes apagadas, como el 8095.

    `LINEAR_PROJECT_CALENDARIO_ID` se borra junto con la llave a
    propósito: quitar solo el project id deja `calendario` en muestra
    mientras `linear_leads.configurado()` se cree vivo (exige solo la
    llave), y entonces la actividad se crea en memoria para después
    reventar con `ErrorLeads`. Los tres módulos tienen que caer juntos.
    """
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PROJECT_CALENDARIO_ID", raising=False)
    monkeypatch.delenv("CALENDARIO_ESCRITURA", raising=False)
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv(contactos.VAR_PREFIJOS_OCULTOS, raising=False)
    calendario.reiniciar_muestra()
    calendario.invalidar_cache()
    linear_leads.reiniciar_muestra()
    contactos.iniciar_tablas()
    contactos.reiniciar_cache()
    assert calendario.modo() == "muestra"
    assert linear_leads.modo() == "muestra"


def _venta_local(cliente, celular, n_orden="S00999"):
    """Un cliente que existe SOLO del lado de Vender: no es lead ninguno.

    Es la mitad del encargo que el selector viejo no cubría — había que
    saber de antemano que esta persona no estaba en el CRM.
    """
    with datos._db() as con:
        con.execute(
            "INSERT INTO ventas_locales (creado_en, empleada, cliente,"
            " celular, orden_id, orden, total, estado)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("2026-10-02T12:00:00", "Génesis", cliente, celular, 999,
             n_orden, 42.0, "pagado"))


# ---------------------------------------------------------------------------
# Leer el HTML como lo leería un navegador, no como lo escribiría la prueba
# ---------------------------------------------------------------------------

def _filas_del_buscador(trozo):
    """[(href, texto)] de cada fila del buscador, tal como se pintaron."""
    filas = []
    for bloque in re.findall(r'<a class="bus-it" href="([^"]+)"(.*?)</a>',
                             trozo, re.S):
        filas.append((html.unescape(bloque[0]),
                      re.sub(r"<[^>]+>", " ", bloque[1])))
    return filas


def _campos_del_form(cuerpo, accion="/calendario/actividad"):
    """{name: value} del formulario, como lo mandaría el navegador.

    Toma los `<input>` y, de cada `<select>`, la opción `selected` o —si
    ninguna lo está— la primera, que es lo que manda un navegador. Así el
    POST de la prueba lleva EXACTAMENTE lo que el servidor pintó, y no lo
    que la prueba supone que pintó.
    """
    inicio = cuerpo.index(f'<form class="modal" method="post" action="{accion}')
    form = cuerpo[inicio:cuerpo.index("</form>", inicio)]
    campos = {}
    for etiqueta in re.findall(r"<input\b[^>]*>", form):
        nombre = re.search(r'\bname="([^"]+)"', etiqueta)
        if not nombre or 'type="search"' in etiqueta:
            continue
        valor = re.search(r'\bvalue="([^"]*)"', etiqueta)
        campos[nombre.group(1)] = html.unescape(valor.group(1)) if valor else ""
    for bloque in re.findall(r"<select\b[^>]*>.*?</select>", form, re.S):
        nombre = re.search(r'\bname="([^"]+)"', bloque)
        if not nombre:
            continue
        opciones = re.findall(r'<option value="([^"]*)"([^>]*)>', bloque)
        elegida = next((v for v, resto in opciones if "selected" in resto),
                       opciones[0][0] if opciones else "")
        campos[nombre.group(1)] = html.unescape(elegida)
    for etiqueta, dentro in re.findall(
            r"(<textarea\b[^>]*>)(.*?)</textarea>", form, re.S):
        nombre = re.search(r'\bname="([^"]+)"', etiqueta)
        if nombre:
            campos[nombre.group(1)] = html.unescape(dentro)
    return campos


# ---------------------------------------------------------------------------
# LA PRUEBA DEL PUNTO 3: lo elegido LLEGA al guardado
# ---------------------------------------------------------------------------

def test_lo_elegido_en_el_buscador_llega_al_guardado(cliente):
    """El camino entero con Linear simulado: buscar → elegir → guardar.

    Lo que mide, salto por salto, sin que la prueba escriba el dato:

      · el pedazo del buscador encuentra el lead por su nombre,
      · el enlace de esa fila devuelve el formulario con `lead` y
        `cliente` PUESTOS (los dos campos que el POST ya leía),
      · mandar esos campos crea la actividad AMARRADA a ese lead y con el
        nombre del lead como cliente,
      · y el embudo se movió igual que siempre (Por agendar → Agendado),
        porque el buscador no cambió el guardado: solo le dice a quién.
    """
    dia = calendario.hoy().isoformat()

    # 1 · buscar. El pedazo se pide como lo pide el navegador.
    pedazo = cliente.get("/calendario/buscar",
                         params={"nueva": "1", "qp": "Tamara",
                                 "tipo": "entrega", "fecha": dia}).text
    filas = _filas_del_buscador(pedazo)
    assert filas, "el buscador no encontró a nadie con «Tamara»"
    liga, texto = next(f for f in filas if "LEAD-91" in f[1])
    assert "Tamara" in texto

    # 2 · elegir. El href de la fila, tal cual se pintó.
    formulario = cliente.get(liga).text
    campos = _campos_del_form(formulario)
    assert campos["lead"] == "LEAD-91"
    assert campos["cliente"] == "Tamara"
    # Y lo que ya estaba escrito en el formulario no se perdió al elegir.
    assert campos["fecha"] == dia
    assert campos["tipo"] == "entrega"

    # 3 · guardar. Los campos del formulario renderizado, sin tocarlos.
    respuesta = cliente.post("/calendario/actividad", data=campos,
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" not in respuesta.headers["location"]

    # Qué nació: la actividad amarrada, con el nombre del lead.
    creada = next(a for a in calendario.listar(dia, dia)
                  if a.get("lead") == "LEAD-91")
    assert creada["cliente"] == "Tamara"
    assert creada["tipo"] == "entrega"
    # Y el embudo se movió, como con el selector de antes.
    assert linear_leads.uno("LEAD-91")["estado"] == "AGENDADO"


def test_un_contacto_que_no_es_lead_tambien_llega_al_guardado(cliente):
    """La otra mitad del encargo: la persona que NO está en el CRM.

    Se busca por TELÉFONO y escrito distinto de como está guardado (con
    guiones y +507, el caso real de Odoo). Elegirla deja la actividad
    suelta —sin lead— y no mueve ni un issue: ese es justo el punto de
    que las dos fuentes salgan juntas en una sola caja.
    """
    _venta_local("Cliente QA Rosa", "60000001")
    contactos.reiniciar_cache()
    dia = calendario.hoy().isoformat()
    antes = {l["ref"]: l["estado"] for l in linear_leads.listar()}

    pedazo = cliente.get("/calendario/buscar",
                         params={"nueva": "1", "qp": "+507 6000-0001",
                                 "tipo": "visita", "fecha": dia}).text
    filas = _filas_del_buscador(pedazo)
    liga, texto = next(f for f in filas if "Cliente QA Rosa" in f[1])
    assert "LEAD-" not in texto          # no es lead de nadie

    campos = _campos_del_form(cliente.get(liga).text)
    assert campos["cliente"] == "Cliente QA Rosa"
    assert campos["lead"] == ""          # suelta, y el POST lo lee así

    cliente.post("/calendario/actividad", data=campos, follow_redirects=False)
    creada = next(a for a in calendario.listar(dia, dia)
                  if a["cliente"] == "Cliente QA Rosa")
    assert creada["lead"] == ""
    assert {l["ref"]: l["estado"] for l in linear_leads.listar()} == antes


# ---------------------------------------------------------------------------
# Una sola función para los dos caminos
# ---------------------------------------------------------------------------

def test_el_pedazo_es_EL_MISMO_que_pinta_la_pantalla(cliente):
    """El pedazo de `/calendario/buscar` y el de la página son idénticos.

    Es la prueba de que no hay una segunda puerta: los dos salen de
    `main._buscador_persona_contexto`. Si alguien agregara una decisión en
    uno solo de los dos caminos, los HTML dejarían de coincidir.
    """
    params = {"nueva": "1", "qp": "Tamara", "tipo": "entrega",
              "fecha": calendario.hoy().isoformat()}
    pedazo = cliente.get("/calendario/buscar", params=params).text.strip()
    pagina = cliente.get("/calendario", params=params).text
    inicio = pagina.index('<div class="bus"')
    assert pagina[inicio:inicio + len(pedazo)] == pedazo


# ---------------------------------------------------------------------------
# Casar: nombre o teléfono, con el MISMO criterio de la pestaña Contactos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("escrito", [
    "Tamara", "tamara", "TAMARA",          # el nombre, como se escriba
    "6552-0966", "65520966", "+507 6552-0966", "0050765520966",
])
def test_el_mismo_lead_se_encuentra_por_nombre_y_por_telefono(escrito):
    hallado = agenda.buscar_personas(escrito)
    assert [f["lead"] for f in hallado["filas"]] == ["LEAD-91"]


def test_casa_con_el_mismo_criterio_que_contactos():
    # No es una copia del criterio: es LA función de contactos.
    assert contactos.casa_busqueda is not None
    ficha = {"nombre": "Tamara", "telefono": "6552-0966",
             "tel_norm": "65520966"}
    assert contactos.casa_busqueda(ficha, "+507 6552 0966")
    assert not contactos.casa_busqueda(ficha, "6552-0967")


def test_sin_escribir_nada_salen_los_vivos_y_lo_dice(cliente):
    hallado = agenda.buscar_personas("")
    refs = {f["lead"] for f in hallado["filas"]}
    # Los vivos del embudo, que es lo que enseñaba el selector de antes.
    assert "LEAD-91" in refs
    assert "LEAD-84" not in refs         # Ganado: cerrado
    assert "LEAD-83" not in refs         # Perdido: cerrado
    # Y se DICE que los contactos entran al escribir, en vez de dejar
    # creer que esto es toda la gente que hay.
    assert hallado["pista"] == agenda.PISTA_SIN_BUSCAR
    assert hallado["con_contactos"] is False


def test_una_persona_con_lead_sale_UNA_vez_y_con_su_lead(cliente):
    """El mismo teléfono en Vender y en el CRM es UNA persona, no dos.

    Y la fila se queda con el lead, que es lo que amarra el trabajo: si
    saliera dos veces, elegir la equivocada dejaría la actividad suelta
    sin que nada avisara.
    """
    _venta_local("Tamara", "6552-0966", n_orden="S00998")
    contactos.reiniciar_cache()
    filas = agenda.buscar_personas("Tamara")["filas"]
    assert [f["lead"] for f in filas] == ["LEAD-91"]
    # Y la fila dice de dónde sale la persona (Vender), sin perder el lead.
    assert "Local" in filas[0]["fuente"]


def test_el_lead_va_con_su_estado_y_su_responsable():
    fila = next(f for f in agenda.buscar_personas("Tamara")["filas"]
                if f["lead"] == "LEAD-91")
    lead = linear_leads.uno("LEAD-91")
    assert lead["estado_nombre"] in fila["detalle"]


def test_los_leads_van_primero(cliente):
    # Elegir un lead es lo que amarra el trabajo: va arriba. (El nombre
    # «Aaa…» ordenaría primero si el orden fuera solo alfabético.)
    _venta_local("Aaa Cliente Suelto", "60000055")
    contactos.reiniciar_cache()
    filas = agenda.buscar_personas("a")["filas"]
    assert filas[0]["es_lead"] is True
    assert any(not f["es_lead"] for f in filas)


# ---------------------------------------------------------------------------
# Nada se inventa: una fuente que no se lee se DICE
# ---------------------------------------------------------------------------

def test_si_linear_no_contesta_el_buscador_lo_dice(monkeypatch):
    def revienta(*_a, **_k):
        raise linear_leads.ErrorLeads("Linear no contestó (503)")
    monkeypatch.setattr(linear_leads, "listar", revienta)
    hallado = agenda.buscar_personas("Tamara")
    # Ni una fila inventada, y el hueco escrito con su motivo.
    assert hallado["filas"] == []
    assert any(agenda.AVISO_LEADS_HUECO in a for a in hallado["avisos"])


def test_el_hueco_de_linear_se_ve_en_el_pedazo(cliente, monkeypatch):
    """El aviso se PINTA, no solo se calcula.

    Se mide contra `/calendario/buscar`, que es el pedazo del buscador y
    nada más. La pantalla ENTERA no se puede medir acá: `agenda.
    por_agendar()` le pide los leads a Linear sin red de seguridad y un
    `ErrorLeads` ahí la tumba con 500 — es de antes de este buscador y
    queda apuntado como pendiente, no se arregla de pasada.
    """
    def revienta(*_a, **_k):
        raise linear_leads.ErrorLeads("Linear no contestó (503)")
    monkeypatch.setattr(linear_leads, "listar", revienta)
    pedazo = cliente.get("/calendario/buscar",
                         params={"nueva": "1", "qp": "Tamara"}).text
    assert agenda.AVISO_LEADS_HUECO in pedazo
    assert "bus-nota-hueco" in pedazo
    assert "bus-it" not in pedazo          # ni una fila inventada


def test_el_hueco_de_linear_de_contactos_no_se_repite(cliente):
    """El aviso de Linear que trae `contactos` NO se pinta acá.

    Sería contradictorio: los leads de ESTA pantalla salen de
    `linear_leads` directo y están a la vista, así que decir «los leads no
    se pueden leer» mientras se ven sería el aviso al revés.
    """
    hallado = agenda.buscar_personas("Tamara")
    assert any(f["lead"] == "LEAD-91" for f in hallado["filas"])
    assert not any(contactos.AVISO_LINEAR_PRUEBAS in a
                   for a in hallado["avisos"])


def test_nadie_encontrado_no_es_una_lista_vacia_muda():
    hallado = agenda.buscar_personas("Zzzz Nadie Asi")
    assert hallado["filas"] == []
    assert hallado["vacio"] == agenda.VACIO_BUSCADOR


def test_el_tope_dice_cuantas_quedaron_fuera():
    hallado = agenda.buscar_personas("", tope=2)
    assert len(hallado["filas"]) == 2
    assert hallado["mas"] == hallado["cuenta"] - 2
    assert hallado["mas"] > 0


# ---------------------------------------------------------------------------
# La pastilla del elegido: lo que dice la pantalla ES lo que se guarda
# ---------------------------------------------------------------------------

def test_la_pastilla_sale_de_los_mismos_campos_que_se_guardan(cliente):
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "lead": "LEAD-91", "cliente": "Tamara",
        "tipo": "entrega"}).text
    assert 'name="lead" value="LEAD-91"' in cuerpo
    assert '<b class="bus-ref">LEAD-91</b>' in cuerpo
    assert "el lead pasa a «Agendado»" in cuerpo


def test_con_un_tipo_que_no_agenda_la_pastilla_dice_la_verdad(cliente):
    # Un Alquiler amarra la actividad pero NO mueve el embudo: la regla ya
    # vivía en el POST, y la pastilla la dice antes de guardar.
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "lead": "LEAD-86", "tipo": "alquiler"}).text
    assert "sin mover" in cuerpo


def test_un_lead_que_ya_no_esta_se_avisa_antes_de_guardar(cliente):
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "lead": "LEAD-999"}).text
    assert linear_leads.mensaje_lead_ausente("LEAD-999") in cuerpo
    assert "bus-elegido-roto" in cuerpo


def test_un_cliente_suelto_dice_que_no_se_amarra(cliente):
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "cliente": "Casa Nueva"}).text
    assert "no se amarra a ningún lead" in cuerpo


def test_quitar_al_elegido_deja_el_resto_del_formulario(cliente):
    dia = calendario.hoy().isoformat()
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "lead": "LEAD-91", "cliente": "Tamara",
        "tipo": "entrega", "fecha": dia, "nota": "cuidado con el portón"}).text
    liga = html.unescape(
        re.search(r'href="([^"]+)">Quitar</a>', cuerpo).group(1))
    assert "lead=LEAD-91" not in liga
    campos = _campos_del_form(cliente.get(liga).text)
    assert campos["lead"] == ""
    assert campos["cliente"] == ""
    # Lo demás sigue puesto: quitar a la persona no vacía el formulario.
    assert campos["fecha"] == dia
    assert campos["tipo"] == "entrega"
    assert "cuidado con el portón" in cliente.get(liga).text


# ---------------------------------------------------------------------------
# El form GET del buscador: no pisa el buscador de ACTIVIDADES ni el POST
# ---------------------------------------------------------------------------

def test_el_campo_del_buscador_no_viaja_en_el_guardado(cliente):
    # `qp` pertenece al form GET (atributo `form=`), no al POST: si viajara
    # al guardado sería un campo que `calendario_crear` no sabe leer.
    cuerpo = cliente.get("/calendario", params={"nueva": "1",
                                                "qp": "Tamara"}).text
    assert 'name="qp"' in cuerpo and 'form="f-buscar-persona"' in cuerpo
    campos = _campos_del_form(cuerpo)
    assert "qp" not in campos


def test_buscar_una_persona_no_filtra_el_calendario(cliente):
    # El buscador de la barra de arriba usa `q` (actividades). El de
    # personas usa `qp`: pisarlo filtraría el calendario entero.
    dia = calendario.hoy().isoformat()
    cliente.post("/calendario/actividad",
                 data={"tipo": "entrega", "cliente": "Casa Testigo",
                       "fecha": dia}, follow_redirects=False)
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "qp": "Tamara", "dia": dia, "vista": "lista"}).text
    assert "Casa Testigo" in cuerpo


def test_los_campos_escondidos_devuelven_el_mismo_formulario(cliente):
    dia = calendario.hoy().isoformat()
    cuerpo = cliente.get("/calendario", params={
        "nueva": "1", "qp": "Tamara", "lead": "LEAD-91", "cliente": "Tamara",
        "tipo": "entrega", "fecha": dia, "nota": "traer escalera"}).text
    form = cuerpo[cuerpo.index('id="f-buscar-persona"'):]
    form = form[:form.index("</form>")]
    ocultos = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
    # Buscar otra vez no puede perder nada de lo que ya estaba escrito…
    assert ocultos["fecha"] == dia
    assert ocultos["tipo"] == "entrega"
    assert html.unescape(ocultos["nota"]) == "traer escalera"
    # …ni des-elegir a quien ya se eligió.
    assert ocultos["lead"] == "LEAD-91"
    assert ocultos["cliente"] == "Tamara"
    assert ocultos["nueva"] == "1"


def test_lead_y_cliente_existen_como_campos_aun_vacios(cliente):
    """Son los dos únicos campos que se mandan vacíos, a propósito.

    Es donde el buscador escribe a quién se eligió: sin el campo vacío no
    habría dónde ponerlo, y elegir una fila dejaría de llevarse lo recién
    escrito (caería al enlace pelado). Los demás se omiten vacíos, igual
    que en `_liga`, para no ensuciar la dirección.
    """
    cuerpo = cliente.get("/calendario", params={"nueva": "1"}).text
    form = cuerpo[cuerpo.index('id="f-buscar-persona"'):]
    form = form[:form.index("</form>")]
    assert 'name="lead" value=""' in form
    assert 'name="cliente" value=""' in form
    # Y la fila lleva los dos valores que el JS copia a esos campos: los
    # MISMOS que el enlace, decididos en el servidor.
    fila = re.search(r'<a class="bus-it"[^>]*>', cuerpo).group(0)
    assert 'data-lead="LEAD-' in fila
    assert "data-cliente=" in fila


# ---------------------------------------------------------------------------
# El 8095: buscar funciona completo, GUARDAR no (condiciones 1 y 2)
# ---------------------------------------------------------------------------

@pytest.fixture
def como_el_8095(monkeypatch):
    """Lo del 8095: llave de Linear puesta y `CALENDARIO_ESCRITURA=0`.

    O sea `modo()` = `lectura` en los dos módulos (es la MISMA variable
    para el calendario y para los leads). La lectura se queda respondiendo
    el mismo tablero de muestra —se toma ANTES de poner la llave— para que
    ninguna prueba salga a la red: lo que se mide acá es el candado del
    guardado, no la lectura.
    """
    leads = list(linear_leads.listar())      # todavía sin llave: la muestra
    nombres = list(linear_leads.responsables())
    monkeypatch.setenv("LINEAR_API_KEY", "clave-que-no-se-usa")
    monkeypatch.setenv("LINEAR_PROJECT_CALENDARIO_ID", "proyecto-de-mentira")
    monkeypatch.setenv("CALENDARIO_ESCRITURA", "0")
    monkeypatch.setattr(linear_leads, "listar",
                        lambda refrescar=False: [dict(l) for l in leads])
    monkeypatch.setattr(linear_leads, "responsables", lambda: list(nombres))
    monkeypatch.setattr(calendario, "listar",
                        lambda desde, hasta, refrescar=False: [])
    monkeypatch.setattr(calendario, "responsables", lambda: [])
    monkeypatch.setattr(calendario, "leads_de_servicio", lambda: [])
    monkeypatch.setattr(calendario, "comentarios", lambda _id: [])
    assert calendario.modo() == "lectura"
    assert linear_leads.modo() == "lectura"


def test_en_el_8095_el_buscador_funciona_completo(cliente, como_el_8095):
    # Condición 1 de Abraham: buscar es LECTURA, no hace falta llave de
    # escritura. El formulario se abre y el buscador encuentra.
    cuerpo = cliente.get("/calendario", params={"nueva": "1",
                                                "qp": "Tamara"}).text
    assert '<div class="bus"' in cuerpo
    filas = _filas_del_buscador(cuerpo)
    assert any("LEAD-91" in texto for _liga, texto in filas)
    # Y el pedazo también, que es el camino del navegador.
    pedazo = cliente.get("/calendario/buscar",
                         params={"nueva": "1", "qp": "Tamara"}).text
    assert any("LEAD-91" in texto for _liga, texto in
               _filas_del_buscador(pedazo))


def test_en_el_8095_el_guardar_sale_APAGADO_con_su_aviso(cliente, como_el_8095):
    # Condición 2: el botón se pinta igual —esconderlo hace que alguien lo
    # busque— apagado y con su motivo escrito.
    cuerpo = cliente.get("/calendario", params={"nueva": "1"}).text
    assert "En pruebas no se guarda" in cuerpo
    boton = re.search(r"<button[^>]*>Crear en Linear[^<]*</button>", cuerpo)
    assert boton and "disabled" in boton.group(0)
    assert 'type="button"' in boton.group(0)
    assert "data-guardando" not in boton.group(0)


def test_en_el_8095_un_POST_forzado_no_escribe_nada(cliente, como_el_8095):
    # El candado de verdad NO es el botón: es el POST, que ya rechazaba en
    # lectura desde antes de este buscador. Se comprueba contra la ruta.
    dia = calendario.hoy().isoformat()
    respuesta = cliente.post("/calendario/actividad",
                             data={"tipo": "entrega", "cliente": "Forzado",
                                   "fecha": dia, "lead": "LEAD-91"},
                             follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert "no escribe en Linear" in unquote(respuesta.headers["location"])


def test_fuera_del_8095_el_guardar_sale_entero(cliente):
    # En muestra y en escritura el botón es el de siempre.
    cuerpo = cliente.get("/calendario", params={"nueva": "1"}).text
    assert "En pruebas no se guarda" not in cuerpo
    assert 'data-guardando="Guardando…"' in cuerpo


# ---------------------------------------------------------------------------
# El guardado NO se tocó (condición 4)
# ---------------------------------------------------------------------------

def test_el_guardado_sigue_leyendo_los_mismos_dos_campos(cliente):
    """Un POST escrito a mano, sin pasar por el buscador, hace lo de antes.

    Es la prueba de que el buscador es pantalla: `calendario_crear` sigue
    recibiendo `lead` y `cliente` y decidiendo igual.
    """
    dia = calendario.hoy().isoformat()
    cliente.post("/calendario/actividad",
                 data={"tipo": "entrega", "cliente": "", "fecha": dia,
                       "lead": "LEAD-90", "resp_nombre": "Mary"},
                 follow_redirects=False)
    creada = next(a for a in calendario.listar(dia, dia)
                  if a.get("lead") == "LEAD-90")
    assert creada["cliente"] == "Juan Carlos Lopez"   # el nombre del lead
    assert creada["resp_lead"] == "Mary"
    assert linear_leads.uno("LEAD-90")["estado"] == "AGENDADO"


def test_la_lista_de_leads_vivos_tiene_UN_solo_dueno():
    # `leads_para_conectar()` se fue con el selector que la pedía: la lista
    # de leads vivos la sirve ahora `leads_para_buscar()` y nada más, así
    # que no hay dos sitios que puedan discrepar sobre qué lead está vivo.
    assert not hasattr(agenda, "leads_para_conectar")
    vivos, aviso = agenda.leads_para_buscar()
    assert aviso == ""
    refs = {l["ref"] for l in vivos}
    assert "LEAD-91" in refs
    assert "LEAD-84" not in refs and "LEAD-83" not in refs
    # Y es la misma que alimenta el buscador con la caja vacía.
    assert refs == {f["lead"] for f in agenda.buscar_personas("", tope=0)["filas"]}


def test_el_tope_de_contactos_no_esconde_la_cuenta(monkeypatch):
    """«Hay N más» cuenta también los que `contactos` recortó.

    Sin esto el número mentía por lo bajo: decía solo los que este módulo
    cortó, y los que la otra puerta ya había dejado fuera desaparecían sin
    que nada avisara.
    """
    monkeypatch.setattr(contactos, "buscar", lambda q, tope=0: {
        "filas": [{"id": f"c{i}", "nombre": f"Cliente {i}", "telefono": "",
                   "tel_norm": "", "tipo": "Persona", "leads": [],
                   "fuente_texto": "Odoo"} for i in range(tope or 1)],
        "cuenta": 40, "mas": 40 - (tope or 1),
        "aviso_odoo": "", "aviso_crm": ""})
    hallado = agenda.buscar_personas("Cliente", tope=5)
    assert len(hallado["filas"]) == 5
    # 40 personas encontradas del lado de contactos + los leads que casen.
    assert hallado["cuenta"] >= 40
    assert hallado["mas"] == hallado["cuenta"] - 5
