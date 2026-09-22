"""El empuje a Google Calendar (fase 2), contra un Google de mentira.

Sin GOOGLE_CLIENT_ID el módulo queda apagado: eso también se prueba.
"""

import httpx
import pytest

from app import calendario, calendario_google


@pytest.fixture(autouse=True)
def tablas(db_limpia):
    calendario_google.iniciar_tablas()
    calendario_google._tokens.clear()


ACTIVIDAD = {
    "id": "lin-1", "ref": "VIV-7", "tipo": "entrega", "cliente": "Hotel Miramar",
    "lugar": "Costa del Este", "nota": "Por la mañana", "hora": "09:30", "dur": 90,
    "fecha": "2026-09-25", "estado": "pend", "prioridad": 2,
    "resp": "Génesis", "resp_id": "u-gen", "url": "https://linear.app/x/VIV-7",
    "titulo": "",
}


def test_el_cuerpo_del_evento(monkeypatch):
    cuerpo = calendario_google.cuerpo_de(ACTIVIDAD)
    assert cuerpo["summary"] == "Entrega — Hotel Miramar"
    assert cuerpo["start"] == {"dateTime": "2026-09-25T09:30:00", "timeZone": "America/Panama"}
    assert cuerpo["end"]["dateTime"] == "2026-09-25T11:00:00"
    assert cuerpo["extendedProperties"]["private"] == {
        "linear": "lin-1", "origen": "calendario-rose"}
    assert "VIV-7" in cuerpo["description"]


class GoogleFalso:
    """Contesta como la API de eventos y anota todo lo que le llega."""

    def __init__(self, existentes=None):
        self.existentes = existentes or []
        self.llamadas = []

    def request(self, metodo, url, headers=None, timeout=None, **kwargs):
        self.llamadas.append((metodo, url, kwargs))
        if metodo == "GET":
            return httpx.Response(200, json={"items": self.existentes})
        return httpx.Response(200, json={"id": "g-nuevo"})


@pytest.fixture
def google_falso(monkeypatch):
    falso = GoogleFalso()
    monkeypatch.setattr(calendario_google.httpx, "request", falso.request)
    monkeypatch.setattr(calendario_google, "_access_token", lambda *a: "tok")
    return falso


def test_empujar_crea_cuando_no_existe(google_falso):
    assert calendario_google.empujar("gen", "rt", ACTIVIDAD) == "creado"
    metodo, url, kwargs = google_falso.llamadas[-1]
    assert metodo == "POST" and kwargs["json"]["summary"] == "Entrega — Hotel Miramar"


def test_empujar_actualiza_sin_duplicar(google_falso):
    google_falso.existentes = [{"id": "g-9"}]
    assert calendario_google.empujar("gen", "rt", ACTIVIDAD) == "actualizado"
    metodo, url, _ = google_falso.llamadas[-1]
    assert metodo == "PUT" and url.endswith("/g-9")


def test_cancelada_se_borra(google_falso):
    google_falso.existentes = [{"id": "g-9"}]
    cancelada = dict(ACTIVIDAD, estado="cancel")
    assert calendario_google.empujar("gen", "rt", cancelada) == "borrado"
    metodo, url, _ = google_falso.llamadas[-1]
    assert metodo == "DELETE" and url.endswith("/g-9")


def test_conciliar_borra_los_huerfanos(google_falso, monkeypatch):
    monkeypatch.setattr(calendario, "responsables",
                        lambda: [{"id": "u-gen", "nombre": "Génesis", "email": "gen@x.com"}])
    google_falso.existentes = [
        {"id": "g-viejo", "extendedProperties": {"private": {"linear": "lin-ajeno"}}}]
    fila = {"usuario": "gen", "email": "gen@x.com", "refresh_token": "rt"}
    calendario_google.sincronizar_usuario(fila, [ACTIVIDAD])
    borrados = [u for m, u, _ in google_falso.llamadas if m == "DELETE"]
    assert any(u.endswith("/g-viejo") for u in borrados)


def test_conectar_y_desconectar_guardan_la_fila(monkeypatch):
    monkeypatch.setattr(calendario_google.httpx, "post",
                        lambda *a, **k: httpx.Response(200))
    calendario_google.conectar("gen", "gen@x.com", "rt-1")
    assert calendario_google.conexion_de("gen")["email"] == "gen@x.com"
    calendario_google.desconectar("gen")
    assert calendario_google.conexion_de("gen") is None


def test_sin_credenciales_todo_queda_apagado(cliente, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert not calendario_google.configurado()
    # La tarjeta no aparece en Ajustes y conectar rebota a Ajustes.
    assert "Google Calendar al instante" not in cliente.get("/?tab=ajustes").text
    r = cliente.get("/calendario/google/conectar", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?tab=ajustes")
