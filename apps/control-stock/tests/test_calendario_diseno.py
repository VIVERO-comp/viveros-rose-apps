"""La piel Orquesta del Calendario (pantallas 01, 02, 17 y 18 del lienzo).

Solo cambió el ASPECTO: estas pruebas fijan que cada acción de la pantalla
sigue presente con la piel nueva — el buscador, los filtros, las vistas,
el crear (barra y botón de abajo del celular), los «+» por día, y todos
los botones de la ficha (terminar, guardar, en curso, reabrir, reactivar,
cancelar, nota). Corren en modo muestra, como test_calendario.py.
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


def _una(estado="pend"):
    dia = calendario.hoy().isoformat()
    return [a for a in calendario.listar(dia, dia) if a["estado"] == estado][0]


# ---------------------------------------------------------------------------
# La pantalla: la piel carga y ninguna acción de la barra se perdió.
# ---------------------------------------------------------------------------

def test_la_piel_nueva_carga_despues_de_la_base(cliente):
    cuerpo = _abrir(cliente).text
    base = cuerpo.index("diseno-base.css")
    propia = cuerpo.index("diseno-calendario.css")
    assert base < propia  # gana por orden, sin !important


def test_la_barra_conserva_todas_sus_acciones(cliente):
    cuerpo = _abrir(cliente).text
    # Flechas, Hoy y las cuatro vistas.
    assert 'aria-label="Anterior"' in cuerpo and 'aria-label="Siguiente"' in cuerpo
    assert ">Hoy</a>" in cuerpo
    for vista in ("Semana", "Día", "Mes", "Lista"):
        assert vista in cuerpo
    # La lupa, el «⋯» con terminadas y el refrescar, y el crear.
    assert 'aria-label="Buscar"' in cuerpo
    assert 'aria-label="Más opciones"' in cuerpo
    assert "terminadas" in cuerpo
    assert "⟳ Volver a preguntarle a Linear" in cuerpo
    assert "+ Nueva actividad" in cuerpo
    # La hamburguesa del menú sigue.
    assert 'class="btn icono hamb"' in cuerpo


def test_los_filtros_son_chips_bajo_la_barra(cliente):
    # Los 4 filtros de tipos se mudaron del sidebar a la fila de chips
    # (pantalla 01): mismos enlaces del servidor, una sola copia.
    cuerpo = _abrir(cliente).text
    assert 'class="fila-filtros"' in cuerpo
    assert cuerpo.count('class="cal-filtros"') == 1
    for nombre in ("Eventos", "Paisajismo", "Mantenimiento", "Entrega retail"):
        assert nombre in cuerpo
    # El color del tipo viaja como variable (de paleta.json, vía Python).
    fila = cuerpo.split('class="fila-filtros"')[1].split("</div>")[0]
    assert "--c:#" in fila


def test_los_mas_por_dia_siguen_en_las_cabeceras(cliente):
    cuerpo = _abrir(cliente).text
    assert "nueva=1" in cuerpo
    assert 'title="Nueva actividad este día"' in cuerpo


def test_el_celular_lleva_el_boton_negro_abajo(cliente):
    # Pantalla 17: + Nueva actividad es la barra fija de abajo; la barra
    # de arriba queda hamburguesa · título (sin acción).
    cuerpo = _abrir(cliente).text
    assert 'class="cal-bar-nueva"' in cuerpo
    barra = cuerpo.split('class="cal-bar-nueva"')[1]
    assert "nueva=1" in barra
    assert 'class="bm-negro"' not in cuerpo


def test_la_tarjeta_del_celular_pinta_hora_tipo_y_cliente(cliente):
    cuerpo = _abrir(cliente).text
    assert 'class="mov-tt"' in cuerpo
    assert 'class="mov-cli"' in cuerpo
    # El color del tipo va en línea (paleta.json), no horneado en el CSS.
    tarjeta = cuerpo.split('class="mov-act')[1].split("</a>")[0] \
        if 'class="mov-act' in cuerpo else ""
    if tarjeta:
        assert "border-left-color:#" in tarjeta


# ---------------------------------------------------------------------------
# La ficha (pantallas 02 y 18): cada acción sigue, con un solo botón negro.
# ---------------------------------------------------------------------------

def test_la_ficha_pendiente_trae_todas_sus_acciones(cliente):
    a = _una("pend")
    cuerpo = _abrir(cliente, abrir=a["id"]).text
    # La cabecera nueva: Atrás (celular), X (computadora), chip del tipo.
    assert 'class="pan-atras"' in cuerpo
    assert 'class="pan-volver"' in cuerpo
    assert 'class="pan-x"' in cuerpo
    assert 'class="chip-tipo"' in cuerpo
    # La banda del color del tipo, en línea desde paleta.json.
    assert "border-top-color:#" in cuerpo
    # El único botón negro: terminar. Guardar quedó secundario.
    assert 'class="btn oro pan-principal"' in cuerpo
    assert "Marcar terminada" in cuerpo or "Hecha · entregado" in cuerpo
    assert ">Guardar cambios</button>" in cuerpo
    assert '<button class="btn oro" type="submit" style="margin-top:10px"' not in cuerpo
    # En curso sigue; cancelar vive dentro de «Más opciones».
    assert ">En curso</button>" in cuerpo
    assert 'class="mas-opciones"' in cuerpo
    opciones = cuerpo.split('class="mas-opciones"')[1]
    assert "Cancelar actividad" in opciones
    assert "data-confirmar" in opciones  # la pregunta de siempre
    # Los campos editables y la nota no se perdieron.
    for campo in ('name="fecha"', 'name="hora"', 'name="dur"',
                  'name="lugar"', 'name="prioridad"', 'name="texto"'):
        assert campo in cuerpo
    assert "Agregar nota" in cuerpo
    assert ">Notas</div>" in cuerpo


def test_la_ficha_hecha_ofrece_reabrir_sin_boton_negro(cliente):
    a = _una("pend")
    cliente.post(f"/calendario/actividad/{a['id']}/estado",
                 data={"estado": "hecha"}, follow_redirects=False)
    cuerpo = _abrir(cliente, abrir=a["id"]).text
    assert ">Reabrir</button>" in cuerpo
    assert "pan-principal" not in cuerpo
    # Cancelar sigue disponible en «Más opciones» también estando hecha.
    assert "Cancelar actividad" in cuerpo


def test_la_ficha_cancelada_ofrece_reactivar(cliente):
    a = _una("pend")
    cliente.post(f"/calendario/actividad/{a['id']}/estado",
                 data={"estado": "cancel"}, follow_redirects=False)
    cuerpo = _abrir(cliente, abrir=a["id"]).text
    assert ">Reactivar</button>" in cuerpo
    assert "Cancelar actividad" not in cuerpo


def test_la_advertencia_de_cancelar_es_la_de_siempre(cliente):
    a = _una("pend")
    cuerpo = _abrir(cliente, abrir=a["id"]).text
    assert "No se borra nada" in cuerpo
    assert a["ref"] in cuerpo


def test_los_botones_de_contacto_van_apagados_todavia_no(cliente):
    # Pantallas 02 y 18: Llamar · WhatsApp · Cómo llegar viven en la ficha,
    # pero la actividad no guarda teléfono ni mapa — van APAGADOS, nunca
    # fingiendo que funcionan (BLOQUE 37, fidelidad).
    a = _una("pend")
    cuerpo = _abrir(cliente, abrir=a["id"]).text
    assert 'class="pan-ics"' in cuerpo
    for clase, rotulo in (("c-tel", "Llamar"), ("c-wa", "WhatsApp"),
                          ("c-map", "Cómo llegar")):
        boton = cuerpo.split(f'class="pan-ic {clase}"')[1].split(">")[0]
        assert "disabled" in boton
        assert f'aria-label="{rotulo} — Todavía no"' in cuerpo
    # Y el recordatorio de cierre es honesto: sin prometer la tecla Esc.
    assert "Se cierra con la X o haciendo clic afuera." in cuerpo
    assert "tecla Esc" not in cuerpo
