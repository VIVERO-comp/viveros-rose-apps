"""Login con Google por invitación (email o link) y la pestaña Ajustes."""

from fastapi.testclient import TestClient

from app import acceso_google, seguridad
from app.main import app


def _con_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente-de-prueba")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secreto-de-prueba")


def _cuenta_google(monkeypatch, email, nombre="Quien Sea"):
    """Sustituye el canje del código: Google 'devuelve' esta cuenta."""
    monkeypatch.setattr(acceso_google, "canjear_codigo",
                        lambda codigo, redirect_uri: {"email": email, "nombre": nombre})


def _volver_de_google(cliente):
    """El ida y vuelta completo: /auth/google (state en cookie) y callback."""
    ida = cliente.get("/auth/google", follow_redirects=False)
    assert ida.status_code == 303
    assert ida.headers["location"].startswith(acceso_google.URL_AUTORIZACION)
    estado = cliente.cookies.get("oauth_estado")
    return cliente.get(f"/auth/google/callback?code=abc&state={estado}",
                       follow_redirects=False)


def test_boton_google_solo_si_esta_configurado(db_limpia, monkeypatch):
    c = TestClient(app)
    assert "Entrar con Google" not in c.get("/login").text
    _con_google(monkeypatch)
    pagina = c.get("/login").text
    assert "Entrar con Google" in pagina and "usuario y contraseña" in pagina


def test_invitada_por_email_entra_y_queda_como_empleada(db_limpia, monkeypatch):
    _con_google(monkeypatch)
    _cuenta_google(monkeypatch, "genesis@gmail.com", "Génesis")
    seguridad.invitar("Genesis@Gmail.com", "Génesis", "abraham")
    c = TestClient(app)
    r = _volver_de_google(c)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.cookies.get("sesion")
    empleadas = {e["usuario"]: e for e in seguridad.listar()}
    assert empleadas["genesis@gmail.com"]["email"] == "genesis@gmail.com"
    # La invitación quedó aceptada: ya no está pendiente.
    assert seguridad.invitaciones_pendientes() == []


def test_link_de_invitacion_entra_aunque_no_haya_email(db_limpia, monkeypatch):
    _con_google(monkeypatch)
    _cuenta_google(monkeypatch, "marta@gmail.com", "Marta")
    _, token = seguridad.invitar("", "Marta", "abraham")
    c = TestClient(app)
    abierto = c.get(f"/invitacion/{token}", follow_redirects=False)
    assert abierto.status_code == 303 and abierto.headers["location"] == "/login"
    assert c.cookies.get("invitacion") == token
    assert "Te invitaron" in c.get("/login").text
    r = _volver_de_google(c)
    assert r.status_code == 303 and c.cookies.get("sesion")
    # De un solo uso: otra persona con el mismo link ya no entra.
    _cuenta_google(monkeypatch, "colado@gmail.com")
    c2 = TestClient(app)
    r2 = c2.get(f"/invitacion/{token}", follow_redirects=False)
    assert r2.status_code == 410 and "ya se usó" in r2.text


def test_link_invalido_avisa(db_limpia, monkeypatch):
    _con_google(monkeypatch)
    c = TestClient(app)
    assert c.get("/invitacion/no-existe", follow_redirects=False).status_code == 410


def test_sin_invitacion_no_entra(db_limpia, monkeypatch):
    _con_google(monkeypatch)
    _cuenta_google(monkeypatch, "colada@gmail.com")
    c = TestClient(app)
    r = _volver_de_google(c)
    assert r.status_code == 401 and "no tiene invitación" in r.text
    assert not c.cookies.get("sesion")
    assert seguridad.listar() == []


def test_admin_entra_sin_invitacion(db_limpia, monkeypatch):
    _con_google(monkeypatch)
    monkeypatch.setenv("AJUSTES_ADMINS", "abraham@gmail.com")
    _cuenta_google(monkeypatch, "abraham@gmail.com", "Abraham")
    c = TestClient(app)
    r = _volver_de_google(c)
    assert r.status_code == 303 and c.cookies.get("sesion")


def test_state_ajeno_no_entra(db_limpia, monkeypatch):
    _con_google(monkeypatch)
    _cuenta_google(monkeypatch, "genesis@gmail.com")
    seguridad.invitar("genesis@gmail.com", "", "abraham")
    c = TestClient(app)
    c.get("/auth/google", follow_redirects=False)
    r = c.get("/auth/google/callback?code=abc&state=otro", follow_redirects=False)
    assert r.status_code == 400 and not c.cookies.get("sesion")


def test_revocada_no_reentra(db_limpia):
    seguridad.invitar("genesis@gmail.com", "Génesis", "abraham")
    assert seguridad.entrar_con_google("genesis@gmail.com", "Génesis") is not None
    seguridad.desactivar("genesis@gmail.com")
    assert seguridad.entrar_con_google("genesis@gmail.com", "Génesis") is None
    assert seguridad.verificar("genesis@gmail.com", "loquesea") is None


def test_contrasena_no_sirve_para_cuentas_de_google(db_limpia):
    seguridad.invitar("genesis@gmail.com", "Génesis", "abraham")
    seguridad.entrar_con_google("genesis@gmail.com", "Génesis")
    # El hash marcador 'google' nunca valida como contraseña (y no revienta).
    assert seguridad.verificar("genesis@gmail.com", "google") is None


def test_invitar_a_quien_ya_tiene_acceso(db_limpia):
    seguridad.invitar("nueva@gmail.com", "", "abraham")
    seguridad.entrar_con_google("nueva@gmail.com", "Nueva")
    assert seguridad.invitar("nueva@gmail.com", "", "abraham") == ("ya_activa", None)


def test_reinvitar_pendiente_no_acumula_links(db_limpia):
    _, token1 = seguridad.invitar("nueva@gmail.com", "Nueva", "abraham")
    _, token2 = seguridad.invitar("nueva@gmail.com", "Nueva N.", "abraham")
    assert token1 == token2
    assert len(seguridad.invitaciones_pendientes()) == 1


def test_ajustes_exige_admin(cliente, con_inventario):
    r = cliente.post("/ajustes/invitar", data={"email": "x@y.com"})
    assert r.status_code == 403
    # Todos ven la pestaña Ajustes (Mi cuenta), pero sin la parte de admin.
    pagina = cliente.get("/").text
    assert "Mi cuenta" in pagina and "Invitar a alguien" not in pagina


def test_mi_email_se_guarda_y_enlaza_la_cuenta(cliente, con_inventario, monkeypatch):
    r = cliente.post("/ajustes/mi-email", data={"email": "Genesis@Gmail.com"},
                     follow_redirects=False)
    assert "aviso=email-guardado" in r.headers["location"]
    # Guardado pero sin verificar (lo anotó a mano).
    empleada = {e["usuario"]: e for e in seguridad.listar()}["genesis"]
    assert empleada["email"] == "genesis@gmail.com"
    # Al entrar con Google con ese email, conserva SU cuenta (no se crea otra).
    resultado = seguridad.entrar_con_google("genesis@gmail.com", "Génesis")
    assert resultado["id"] == "genesis" and resultado["email_verificado"] == 1
    assert len(seguridad.listar()) == 1


def test_mi_email_no_puede_ser_el_de_otra(cliente, con_inventario):
    seguridad.crear_empleada("otra", "Otra", "clave-de-prueba")
    seguridad.fijar_email("otra", "otra@gmail.com")
    r = cliente.post("/ajustes/mi-email", data={"email": "otra@gmail.com"},
                     follow_redirects=False)
    assert "aviso=email-ocupado" in r.headers["location"]
    empleada = {e["usuario"]: e for e in seguridad.listar()}["genesis"]
    assert not empleada["email"]


def test_email_sin_verificar_no_da_admin(cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "jefa@gmail.com")
    # genesis se anota el email de la admin: no gana la pestaña ni las rutas.
    cliente.post("/ajustes/mi-email", data={"email": "jefa@gmail.com"})
    assert "Invitar a alguien" not in cliente.get("/").text
    assert cliente.post("/ajustes/invitar", data={"email": "x@y.com"}).status_code == 403
    # Verificado por Google, sí es admin.
    seguridad.entrar_con_google("jefa@gmail.com", "Génesis", es_admin=True)
    assert cliente.post("/ajustes/invitar", data={"email": "x@y.com"},
                        follow_redirects=False).status_code == 303


def test_ajustes_admin_invita_cancela_y_revoca(cliente, con_inventario, monkeypatch):
    monkeypatch.setenv("AJUSTES_ADMINS", "genesis")
    assert "tab-ajustes" in cliente.get("/").text

    r = cliente.post("/ajustes/invitar",
                     data={"email": "Nueva@Gmail.com", "nombre": "Nueva"},
                     follow_redirects=False)
    assert r.status_code == 303 and "aviso=invitada" in r.headers["location"]
    pendiente = seguridad.invitaciones_pendientes()[0]
    assert pendiente["email"] == "nueva@gmail.com"
    # La pantalla ofrece copiar el link de la invitación.
    assert f"/invitacion/{pendiente['token']}" in cliente.get(
        f"/?tab=ajustes&aviso=invitada&inv={pendiente['token']}").text

    # Sin email también: la invitación de solo link.
    r = cliente.post("/ajustes/invitar", data={"nombre": "Repartidor"},
                     follow_redirects=False)
    assert "aviso=invitada" in r.headers["location"]
    assert len(seguridad.invitaciones_pendientes()) == 2

    r = cliente.post("/ajustes/invitar", data={"email": "sin-arroba"},
                     follow_redirects=False)
    assert "aviso=email" in r.headers["location"]

    for inv in seguridad.invitaciones_pendientes():
        cliente.post("/ajustes/invitacion/cancelar", data={"token": inv["token"]})
    assert seguridad.invitaciones_pendientes() == []

    # Revocar a otra sí; a sí misma no.
    seguridad.crear_empleada("otra", "Otra", "clave-de-prueba")
    cliente.post("/ajustes/revocar", data={"usuario": "otra"})
    cliente.post("/ajustes/revocar", data={"usuario": "genesis"})
    estados = {e["usuario"]: e["activa"] for e in seguridad.listar()}
    assert estados["otra"] == 0 and estados["genesis"] == 1
