"""Un chat con VARIOS issues: se escribe una sola vez y manda el vivo más nuevo.

El bug que estas pruebas cierran (medido el 7/10/2026): el sincronizador
planeaba por ISSUE y escribía por CHAT con un `PUT` que **reemplaza la lista
completa**. Dos issues de la misma persona se borraban las etiquetas el uno
al otro en cada vuelta —113 horas seguidas, ~3.400 escrituras inútiles por
chat— y el chat quedaba clavado en el issue que escribía último, que por el
orden de texto del `ref` era el más VIEJO: un Ganado cerrado tapando a un
lead Agendado y pagado.

Dos caminos distintos llevan al mismo chat compartido, y los dos están
cubiertos acá: un Ganado que vuelve a escribir estrena issue con el MISMO
`PP-` (regla del 1/10/2026), y el receptor que abrió un segundo lead con
`PP-` NUEVO para alguien que ya tenía uno vivo (LEAD-128 + LEAD-129).

Mismo mecanismo de carga que los otros `test_sincronizador_*.py`: se copia
el `.py` real a una carpeta temporal con su propio `.env` de prueba.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

RUTA_REAL = Path(__file__).resolve().parents[3] / "waha" / "sincronizador.py"

CATALOGO = ("Nuevo", "Hablando", "Cotizado", "Por agendar", "Agendado",
            "Entregado", "Recordar", "Plantas", "Eventos", "Paisajismo",
            "Mantenimiento", "Mayorista", "🔴 Responder",
            "‎Pedido completado", "‎Importante", "‎Seguimiento",
            "‎Cliente potencial", "Llamar", "Entrega pendiente", "Equipo")


@pytest.fixture
def sinc(tmp_path):
    """El sincronizador real, cargado desde una copia con su propio `.env`."""
    if not RUTA_REAL.exists():
        pytest.skip("este checkout no trae la carpeta waha/")
    destino = tmp_path / "sincronizador.py"
    shutil.copy(RUTA_REAL, destino)
    (tmp_path / ".env").write_text(
        "WAHA_API_KEY=clave-de-prueba\nLINEAR_API_KEY=clave-de-prueba\n"
        "ETIQUETAS_REPRESENTANTE=off\n")
    camino = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        "sincronizador_prueba_chat_compartido", destino)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    sys.path[:] = camino
    return modulo


def lead(ref, estado, creado, cerrado=False, **cambios):
    base = {
        "ref": ref, "nombre": "Cliente de prueba", "pp": "PP-TEST01",
        "estado": estado, "resp": "", "interes": "Plantas",
        "te_toca": False, "senales": set(), "telefono": "50760000001",
        "nombre_primero": "", "nombre_segundo": "",
        "cerrado": cerrado, "creado": creado,
        "numero": int(ref.rsplit("-", 1)[1]),
    }
    base.update(cambios)
    return base


# --------------------------------------------------------------------------
# El número del issue, que es de donde venía el orden al revés
# --------------------------------------------------------------------------

def test_numero_de_ref_saca_el_entero(sinc):
    assert sinc._numero_de_ref("LEAD-128") == 128
    assert sinc._numero_de_ref("LEAD-62") == 62
    assert sinc._numero_de_ref("") == 0


def test_ordenar_por_numero_no_pone_el_110_antes_del_62(sinc):
    """Como TEXTO, "LEAD-110" < "LEAD-62". Por número, no."""
    assert "LEAD-110" < "LEAD-62"           # el orden viejo, el del bug
    refs = ["LEAD-110", "LEAD-62"]
    assert sorted(refs, key=sinc._numero_de_ref) == ["LEAD-62", "LEAD-110"]


# --------------------------------------------------------------------------
# Quién manda
# --------------------------------------------------------------------------

def test_un_solo_issue_manda_y_no_calla_a_nadie(sinc):
    solo = lead("LEAD-7", "Hablando", "2026-10-01T00:00:00.000Z")
    manda, callados = sinc.lead_que_manda([solo])
    assert manda is solo
    assert callados == []


def test_entre_dos_vivos_manda_el_mas_nuevo(sinc):
    """LEAD-128 (Nuevo) + LEAD-129 (Hablando): el caso del lead duplicado."""
    viejo = lead("LEAD-128", "Nuevo", "2026-10-06T01:25:56.701Z")
    nuevo = lead("LEAD-129", "Hablando", "2026-10-06T01:29:45.099Z")
    manda, callados = sinc.lead_que_manda([viejo, nuevo])
    assert manda["ref"] == "LEAD-129"
    assert [c["ref"] for c in callados] == ["LEAD-128"]


def test_el_vivo_manda_aunque_el_cerrado_sea_mas_nuevo(sinc):
    """«Manda el issue VIVO más nuevo» — vivo primero, nuevo después."""
    vivo = lead("LEAD-110", "Agendado", "2026-10-02T21:51:21.749Z")
    cerrado_nuevo = lead("LEAD-140", "Ganado", "2026-10-07T10:00:00.000Z",
                         cerrado=True)
    manda, callados = sinc.lead_que_manda([vivo, cerrado_nuevo])
    assert manda["ref"] == "LEAD-110"
    assert [c["ref"] for c in callados] == ["LEAD-140"]


def test_el_caso_real_lead110_contra_lead62(sinc):
    """El par medido en producción: gana el Agendado, no el Ganado viejo."""
    ganado = lead("LEAD-62", "Ganado", "2026-09-23T03:19:29.492Z",
                  cerrado=True)
    agendado = lead("LEAD-110", "Agendado", "2026-10-02T21:51:21.749Z")
    # En el orden del bug, LEAD-62 escribía último y ganaba.
    manda, callados = sinc.lead_que_manda([agendado, ganado])
    assert manda["ref"] == "LEAD-110"
    assert [c["ref"] for c in callados] == ["LEAD-62"]


def test_sin_ninguno_vivo_manda_el_mas_reciente(sinc):
    viejo = lead("LEAD-69", "Perdido", "2026-09-25T00:00:00.000Z",
                 cerrado=True)
    nuevo = lead("LEAD-109", "Perdido", "2026-10-01T00:00:00.000Z",
                 cerrado=True)
    manda, callados = sinc.lead_que_manda([viejo, nuevo])
    assert manda["ref"] == "LEAD-109"
    assert [c["ref"] for c in callados] == ["LEAD-69"]


def test_sin_fecha_desempata_el_numero_del_issue(sinc):
    """Si Linear no trajera `createdAt`, el número del issue decide."""
    a = lead("LEAD-62", "Hablando", "")
    b = lead("LEAD-110", "Hablando", "")
    manda, _callados = sinc.lead_que_manda([a, b])
    assert manda["ref"] == "LEAD-110"


# --------------------------------------------------------------------------
# Agrupar por chat
# --------------------------------------------------------------------------

def test_dos_issues_del_mismo_telefono_caen_en_un_solo_chat(sinc):
    dos = [lead("LEAD-128", "Nuevo", "2026-10-06T01:25:56.701Z"),
           lead("LEAD-129", "Hablando", "2026-10-06T01:29:45.099Z")]
    por_chat, sin_tel, saltados, sin_wa = sinc.agrupar_por_chat(
        dos, [], resolver_chat=lambda tel: "111@lid")
    assert list(por_chat) == ["111@lid"]
    assert [l["ref"] for l in por_chat["111@lid"]] == ["LEAD-128", "LEAD-129"]
    assert (sin_tel, saltados, sin_wa) == ([], [], [])


def test_telefonos_distintos_son_chats_distintos(sinc):
    dos = [lead("LEAD-1", "Nuevo", "2026-10-01T00:00:00.000Z",
                telefono="50760000001"),
           lead("LEAD-2", "Nuevo", "2026-10-02T00:00:00.000Z",
                telefono="50760000002")]
    por_chat, _st, _sa, _sw = sinc.agrupar_por_chat(
        dos, [], resolver_chat=lambda tel: tel[-1] + "@lid")
    assert len(por_chat) == 2


def test_sin_telefono_interno_y_sin_whatsapp_se_apartan(sinc):
    leads = [
        lead("LEAD-1", "Nuevo", "2026-10-01T00:00:00.000Z", telefono=""),
        lead("LEAD-2", "Nuevo", "2026-10-02T00:00:00.000Z",
             telefono="50765673062"),          # interno
        lead("LEAD-3", "Nuevo", "2026-10-03T00:00:00.000Z",
             telefono="50760000009"),          # no tiene WhatsApp
    ]
    por_chat, sin_tel, saltados, sin_wa = sinc.agrupar_por_chat(
        leads, ["65673062"], resolver_chat=lambda tel: "")
    assert por_chat == {}
    assert [l["ref"] for l in sin_tel] == ["LEAD-1"]
    assert [l["ref"] for l in saltados] == ["LEAD-2"]
    assert [l["ref"] for l in sin_wa] == ["LEAD-3"]


def test_el_orden_de_los_chats_es_por_numero_de_issue(sinc):
    leads = [lead("LEAD-110", "Hablando", "2026-10-02T00:00:00.000Z",
                  telefono="50760000110"),
             lead("LEAD-62", "Hablando", "2026-09-23T00:00:00.000Z",
                  telefono="50760000062")]
    por_chat, _st, _sa, _sw = sinc.agrupar_por_chat(
        leads, [], resolver_chat=lambda tel: tel[-3:] + "@lid")
    assert list(por_chat) == ["062@lid", "110@lid"]


# --------------------------------------------------------------------------
# La prueba que falla si vuelven a pisarse: UNA escritura por chat
# --------------------------------------------------------------------------

@pytest.fixture
def waha_falso(sinc, monkeypatch):
    """WAHA de mentira: guarda las etiquetas por chat y anota cada PUT."""
    estado = {}
    puts = []

    def _waha(ruta, datos=None, metodo=None):
        if ruta.startswith("/api/sessions/"):
            return {"status": "WORKING", "me": {"id": "50760991459@c.us"}}
        if ruta.endswith("/labels"):
            return [{"id": "id-%s" % n, "name": n} for n in CATALOGO]
        if "/labels/chats/" in ruta:
            chat = ruta.rsplit("/", 1)[1]
            if metodo == "PUT":
                nombres = sorted(
                    n for n in CATALOGO
                    if {"id": "id-%s" % n} in (datos or {}).get("labels", []))
                puts.append((chat, nombres))
                estado[chat] = nombres
                return {}
            return [{"id": "id-%s" % n, "name": n}
                    for n in estado.get(chat, [])]
        raise AssertionError("ruta no prevista en la prueba: %s" % ruta)

    monkeypatch.setattr(sinc, "_waha", _waha)
    monkeypatch.setattr(sinc, "internos", lambda: ([], "prueba"))
    monkeypatch.setattr(sinc, "cargar_estado", lambda: {})
    monkeypatch.setattr(sinc, "guardar_estado", lambda leads: None)
    monkeypatch.setattr(sinc, "etiquetar_equipo", lambda l, a: (0, []))
    monkeypatch.setattr(sinc, "nombres_de_contactos",
                        lambda *a, **k: ([], [], 0, []))
    monkeypatch.setattr(sinc, "chat_id_real", lambda tel: "999@lid")
    monkeypatch.setattr(sys, "argv", ["sincronizador.py", "--aplicar"])
    return estado, puts


def test_una_sola_escritura_por_chat_aunque_haya_dos_issues(sinc, monkeypatch,
                                                            waha_falso):
    """Si vuelven a pisarse, acá salen DOS PUT al mismo chat y esto falla."""
    estado, puts = waha_falso
    monkeypatch.setattr(sinc, "leads_del_crm", lambda: [
        lead("LEAD-62", "Ganado", "2026-09-23T03:19:29.492Z", cerrado=True,
             te_toca=True),
        lead("LEAD-110", "Agendado", "2026-10-02T21:51:21.749Z",
             te_toca=True),
    ])
    sinc.main()
    chats_escritos = [chat for chat, _ in puts]
    assert len(chats_escritos) == len(set(chats_escritos)), (
        "se escribió el mismo chat más de una vez en la misma vuelta: %s"
        % puts)
    # Y manda el vivo: queda Agendado, no «Pedido completado».
    assert estado["999@lid"] == sorted(["Agendado", "Plantas", "🔴 Responder"])


def test_la_segunda_vuelta_no_vuelve_a_escribir_nada(sinc, monkeypatch,
                                                     waha_falso):
    """El churn se ve así: la vuelta 2 no debe tener nada que hacer."""
    estado, puts = waha_falso
    monkeypatch.setattr(sinc, "leads_del_crm", lambda: [
        lead("LEAD-62", "Ganado", "2026-09-23T03:19:29.492Z", cerrado=True,
             te_toca=True),
        lead("LEAD-110", "Agendado", "2026-10-02T21:51:21.749Z",
             te_toca=True),
    ])
    assert sinc.main() == sinc.SALIDA_CON_CAMBIOS
    puts.clear()
    assert sinc.main() == sinc.SALIDA_SIN_NADA
    assert puts == [], "la segunda vuelta volvió a escribir: %s" % puts


def test_dos_issues_vivos_queda_el_mas_nuevo_y_una_escritura(sinc, monkeypatch,
                                                             waha_falso):
    """LEAD-128 + LEAD-129, el par con `PP-` distintos."""
    estado, puts = waha_falso
    monkeypatch.setattr(sinc, "leads_del_crm", lambda: [
        lead("LEAD-128", "Nuevo", "2026-10-06T01:25:56.701Z", te_toca=True),
        lead("LEAD-129", "Hablando", "2026-10-06T01:29:45.099Z"),
    ])
    sinc.main()
    assert len(puts) == 1, puts
    assert estado["999@lid"] == sorted(["Hablando", "Plantas"])
