"""Números de coworkers en Ajustes: chats internos que no se vuelven leads.

La lista real vive en la base `tienda` del droplet (migración 018 del
order-api); aquí se prueba el respaldo SQLite de desarrollo, que usa el mismo
SQL, y el contrato de las rutas de Ajustes.
"""

from app import coworkers


def test_normalizar_acepta_como_se_escribe_en_panama():
    assert coworkers.normalizar("66752380") == "66752380"
    assert coworkers.normalizar("6675-2380") == "66752380"
    assert coworkers.normalizar("+507 6675 2380") == "50766752380"
    # Lo que no es un teléfono, no entra.
    assert coworkers.normalizar("jefe") == ""
    assert coworkers.normalizar("123") == ""
    assert coworkers.normalizar("") == ""


def test_agregar_listar_y_quitar(db_limpia, monkeypatch):
    monkeypatch.delenv("TIENDA_DSN", raising=False)
    coworkers.agregar("66752380", "Jefe", "genesis")
    coworkers.agregar("65673062", "", "genesis")
    assert [c["numero"] for c in coworkers.listar()] == ["66752380", "65673062"]
    # Volver a agregar el mismo número solo actualiza la nota.
    coworkers.agregar("66752380", "El jefe", "genesis")
    guardados = {c["numero"]: c["nota"] for c in coworkers.listar()}
    assert guardados == {"66752380": "El jefe", "65673062": ""}
    coworkers.quitar("65673062")
    assert [c["numero"] for c in coworkers.listar()] == ["66752380"]


def test_ajustes_admin_agrega_y_quita_numeros(cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    monkeypatch.delenv("TIENDA_DSN", raising=False)

    r = cliente.post("/ajustes/coworkers/agregar",
                     data={"numero": "6675-2380", "nota": "Jefe"},
                     follow_redirects=False)
    assert r.status_code == 303 and "aviso=coworker-agregado" in r.headers["location"]
    pagina = cliente.get("/?tab=ajustes").text
    assert "66752380" in pagina and "Jefe" in pagina

    # Un número ilegible avisa sin guardar nada.
    r = cliente.post("/ajustes/coworkers/agregar", data={"numero": "jefe"},
                     follow_redirects=False)
    assert "aviso=coworker-invalido" in r.headers["location"]
    assert [c["numero"] for c in coworkers.listar()] == ["66752380"]

    r = cliente.post("/ajustes/coworkers/quitar", data={"numero": "66752380"},
                     follow_redirects=False)
    assert "aviso=coworker-quitado" in r.headers["location"]
    assert coworkers.listar() == []


def test_quien_no_es_admin_ni_ve_ni_toca_la_lista(cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "otra-persona")
    monkeypatch.delenv("TIENDA_DSN", raising=False)
    assert "Números de coworkers" not in cliente.get("/?tab=ajustes").text
    r = cliente.post("/ajustes/coworkers/agregar", data={"numero": "66752380"})
    assert r.status_code == 403
    assert coworkers.listar() == []
