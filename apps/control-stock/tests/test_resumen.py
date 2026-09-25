"""El resumen del día al celular del dueño (25/09/2026).

Corren en modo muestra: los leads salen del tablero de ejemplo de
`linear_leads` y Odoo se reemplaza por una puerta falsa. Lo que se cuida
aquí es lo que duele si se rompe: que un bloque sin fuente NO se pinte en
cero, que el candado del cron sea el secreto, que el aviso no suene un
domingo en blanco, y que el «hace cuánto» salga del mensaje del cliente y
no del `updatedAt` del issue.

Los leads de muestra con «Te toca» son LEAD-87 (Ximena, sin dueño) y
LEAD-86 (Nedjaira, de Salomón).
"""

from datetime import date, timedelta

import pytest

from app import avisos, linear_leads, resumen

LUNES = date(2026, 9, 21)
DOMINGO = date(2026, 9, 27)


@pytest.fixture(autouse=True)
def muestra_limpia(monkeypatch, db_limpia):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("TWENTY_API_KEY", raising=False)
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("RESUMEN_SECRETO", raising=False)
    monkeypatch.delenv("AVISOS_DUENO_USUARIO", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PUBLICA", raising=False)
    monkeypatch.delenv("VAPID_CLAVE_PRIVADA", raising=False)
    linear_leads.reiniciar_muestra()
    avisos.iniciar_tablas()


@pytest.fixture
def con_odoo(monkeypatch):
    """Odoo contestando: una cotización y un pago de hoy."""
    monkeypatch.setenv("ODOO_URL", "http://odoo-de-prueba")
    monkeypatch.setenv("ODOO_DB", "prueba")
    monkeypatch.setenv("ODOO_USER", "prueba")
    monkeypatch.setenv("ODOO_PASSWORD", "prueba")

    def falso(modelo, metodo, args, kw=None):
        if modelo == "sale.order":
            return [{"id": 1, "name": "S00190", "partner_id": [9, "Escuela Las Américas"],
                     "amount_total": 4200.0, "client_order_ref": "PP-ZZ001",
                     "etapa_cobro": "cotizado"}]
        if modelo == "account.payment":
            return [{"id": 5, "amount": 500.0, "partner_id": [9, "Escuela Las Américas"],
                     "vivero_orden_id": [1, "S00190"], "date": "2026-09-25",
                     "journal_id": [3, "Yappy"]}]
        return []

    monkeypatch.setattr("app.ventas._ejecutar", falso)


@pytest.fixture
def odoo_caido(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://odoo-de-prueba")
    monkeypatch.setenv("ODOO_DB", "prueba")
    monkeypatch.setenv("ODOO_USER", "prueba")
    monkeypatch.setenv("ODOO_PASSWORD", "prueba")

    def revienta(*_a, **_k):
        raise OSError("no hay ruta al host")

    monkeypatch.setattr("app.ventas._ejecutar", revienta)


@pytest.fixture
def con_avisos(monkeypatch):
    """Claves VAPID puestas y un celular activado, sin salir a la red."""
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", "publica")
    monkeypatch.setenv("VAPID_CLAVE_PRIVADA", "privada")
    avisos.guardar(resumen.usuario_dueno(),
                   {"endpoint": "https://push.example/abc",
                    "keys": {"p256dh": "llave", "auth": "secreto"}})
    mandados = []
    monkeypatch.setattr(avisos, "avisar",
                        lambda usuario, titulo, cuerpo, url="/":
                        mandados.append((usuario, titulo, cuerpo, url)))
    return mandados


# ---------------------------------------------------------------------------
# El horario: solo se usa para saber si el día estuvo cerrado
# ---------------------------------------------------------------------------

def test_el_domingo_es_el_unico_dia_cerrado():
    assert resumen.cerrado(DOMINGO) is True
    for dias in range(6):                      # lunes a sábado
        assert resumen.cerrado(LUNES + timedelta(days=dias)) is False


def test_el_horario_es_el_que_dijo_el_dueno():
    # L–V 8 a 5, sábado 8 a 12, domingo cerrado.
    assert resumen.HORARIO[0] == ("08:00", "17:00")
    assert resumen.HORARIO[5] == ("08:00", "12:00")
    assert 6 not in resumen.HORARIO


# ---------------------------------------------------------------------------
# Los cinco bloques
# ---------------------------------------------------------------------------

def test_los_leads_sin_dueno_salen_de_los_vivos():
    datos = resumen.del_dia()
    # De los 9 de muestra, 7 están vivos (Soledad está Ganada y Monica
    # Perdida), y 3 de esos no tienen Resp: (LEAD-90, LEAD-87, LEAD-85).
    assert datos["sin_resp"]["de"] == 7
    assert datos["sin_resp"]["total"] == 3
    refs = {f["ref"] for f in datos["sin_resp"]["filas"]}
    assert "LEAD-84" not in refs and "LEAD-83" not in refs   # los cerrados


def test_quien_tiene_clientes_esperando_va_agrupado():
    datos = resumen.del_dia()
    assert datos["esperando"]["total"] == 2
    por_resp = {g["resp"]: [l["ref"] for l in g["leads"]]
                for g in datos["esperando"]["por_resp"]}
    assert por_resp == {"": ["LEAD-87"], "Salomón": ["LEAD-86"]}
    # Sin dueño va primero: es el que nadie va a contestar.
    assert datos["esperando"]["por_resp"][0]["resp"] == ""


def test_sin_twenty_el_hace_cuanto_queda_vacio_y_no_inventa():
    datos = resumen.del_dia()
    for grupo in datos["esperando"]["por_resp"]:
        for lead in grupo["leads"]:
            assert lead["espera"] == ""
            assert lead["horas"] is None       # ni 0 ni un número cualquiera


def test_el_hace_cuanto_sale_del_mensaje_del_cliente(monkeypatch):
    """Nunca del `updatedAt` del issue: cualquier toque nuestro lo reinicia,
    y por eso el barrido tampoco lo usa."""
    from datetime import datetime, timezone
    hace_tres_horas = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    monkeypatch.setattr(resumen, "_ultimo_mensaje_del_cliente",
                        lambda lead: hace_tres_horas)
    datos = resumen.del_dia()
    esperando = datos["esperando"]["por_resp"][0]["leads"][0]
    assert esperando["horas"] == pytest.approx(3.0, abs=0.2)
    assert esperando["espera"] == "hace 3 horas"


@pytest.mark.parametrize("horas, texto", [
    (0.2, "hace 12 min"), (1.0, "hace 1 hora"), (2.5, "hace 2 horas"),
    (25, "hace 1 día"), (50, "hace 2 días"),
])
def test_el_hace_cuanto_se_lee_en_español(horas, texto):
    assert resumen._hace_cuanto(horas) == texto


def test_con_odoo_llegan_las_cotizaciones_y_los_pagos(con_odoo):
    datos = resumen.del_dia()
    assert datos["cotizaciones"]["total"] == 1
    assert datos["cotizaciones"]["monto"] == 4200.0
    assert datos["cotizaciones"]["filas"][0]["cliente"] == "Escuela Las Américas"
    assert datos["pagos"]["total"] == 1
    assert datos["pagos"]["monto"] == 500.0
    assert datos["pagos"]["filas"][0]["medio"] == "Yappy"
    assert datos["errores"] == []


def test_si_odoo_no_contesta_el_bloque_queda_en_blanco_no_en_cero(odoo_caido):
    datos = resumen.del_dia()
    # Ni 0 cotizaciones ni 0 pagos: "no se sabe". Un cero falso parece una
    # noticia buena, y esa es la peor mentira en un resumen.
    assert datos["cotizaciones"] is None
    assert datos["pagos"] is None
    assert any("Odoo no contestó" in e for e in datos["errores"])


def test_sin_odoo_configurado_lo_dice():
    datos = resumen.del_dia()
    assert datos["cotizaciones"] is None
    assert any("Odoo no está configurado" in e for e in datos["errores"])


def test_si_linear_no_contesta_los_tres_bloques_quedan_en_blanco(monkeypatch):
    monkeypatch.setattr(linear_leads, "listar",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("sin red")))
    datos = resumen.del_dia()
    assert datos["nuevos"] is None and datos["sin_resp"] is None
    assert datos["esperando"] is None
    assert any("Linear no contestó" in e for e in datos["errores"])


# ---------------------------------------------------------------------------
# El titular: lo único que cabe en la notificación
# ---------------------------------------------------------------------------

def test_el_titular_no_nombra_los_ceros(con_odoo):
    titular = resumen.titular(resumen.del_dia())
    assert "1 cotizado" in titular
    assert "1 pago ($500.00)" in titular
    assert "3 sin dueño" in titular
    assert "2 esperando" in titular
    assert "0 " not in titular          # un cero gasta el espacio del titular


def test_el_titular_avisa_cuando_el_resumen_tiene_huecos(odoo_caido):
    assert "con huecos" in resumen.titular(resumen.del_dia())


def test_un_dia_de_verdad_vacio_lo_dice(monkeypatch):
    monkeypatch.setattr(resumen, "_leads", lambda dia: {
        "nuevos": {"total": 0, "por_origen": [], "filas": []},
        "sin_resp": {"total": 0, "de": 0, "filas": []},
        "esperando": {"total": 0, "por_resp": []}})
    datos = resumen.del_dia(DOMINGO)
    datos["errores"] = []
    assert resumen.titular(datos) == "Día sin novedades."
    assert resumen.hay_algo(datos) is False


# ---------------------------------------------------------------------------
# Mandarlo
# ---------------------------------------------------------------------------

def test_manda_el_aviso_al_dueno_con_el_titular(con_avisos, con_odoo):
    hecho = resumen.mandar()
    assert hecho["mandado"] is True
    usuario, titulo, cuerpo, url = con_avisos[0]
    assert usuario == resumen.usuario_dueno()
    assert titulo.startswith("Resumen · ")
    assert "sin dueño" in cuerpo
    assert url.startswith("/resumen?dia=")


def test_sin_celular_activado_no_finge_que_mando(con_odoo, monkeypatch):
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", "publica")
    monkeypatch.setenv("VAPID_CLAVE_PRIVADA", "privada")
    hecho = resumen.mandar()
    assert hecho["mandado"] is False
    assert "no tiene ningún celular" in hecho["motivo"]


def test_sin_claves_vapid_no_manda():
    hecho = resumen.mandar()
    assert hecho["mandado"] is False
    assert "VAPID" in hecho["motivo"]


def test_un_domingo_en_blanco_no_le_suena_el_telefono(con_avisos, monkeypatch):
    monkeypatch.setattr(resumen, "_leads", lambda dia: {
        "nuevos": {"total": 0, "por_origen": [], "filas": []},
        "sin_resp": {"total": 0, "de": 0, "filas": []},
        "esperando": {"total": 0, "por_resp": []}})
    hecho = resumen.mandar(DOMINGO)
    assert hecho["mandado"] is False
    assert "domingo" in hecho["motivo"]
    assert con_avisos == []


def test_un_domingo_con_algo_si_suena(con_avisos):
    # El tablero de muestra tiene gente esperando: eso es "algo".
    hecho = resumen.mandar(DOMINGO)
    assert hecho["mandado"] is True


# ---------------------------------------------------------------------------
# El candado del cron
# ---------------------------------------------------------------------------

def test_sin_secreto_el_endpoint_no_corre(cliente):
    respuesta = cliente.post("/avisos/resumen")
    assert respuesta.status_code == 503
    assert "RESUMEN_SECRETO" in respuesta.json()["motivo"]


def test_con_secreto_equivocado_rebota(cliente, monkeypatch):
    monkeypatch.setenv("RESUMEN_SECRETO", "el-bueno")
    assert cliente.post("/avisos/resumen").status_code == 401
    assert cliente.post("/avisos/resumen", headers={
        "Authorization": "Bearer el-malo"}).status_code == 401


def test_con_el_secreto_bueno_corre(cliente, monkeypatch, con_avisos, con_odoo):
    monkeypatch.setenv("RESUMEN_SECRETO", "el-bueno")
    respuesta = cliente.post("/avisos/resumen",
                             headers={"Authorization": "Bearer el-bueno"})
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True and cuerpo["mandado"] is True
    assert "esperando" in cuerpo["titular"]


def test_el_endpoint_del_cron_no_pide_sesion(monkeypatch):
    """Lo llama una máquina: su candado es el secreto, no la cookie. Sin
    esto el middleware lo mandaría al login y el cron nunca correría."""
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setenv("RESUMEN_SECRETO", "el-bueno")
    sin_sesion = TestClient(app)
    respuesta = sin_sesion.post("/avisos/resumen",
                                headers={"Authorization": "Bearer el-bueno"},
                                follow_redirects=False)
    assert respuesta.status_code == 200     # ni 303 al login ni 401


# ---------------------------------------------------------------------------
# La pantalla
# ---------------------------------------------------------------------------

def test_la_pantalla_pinta_los_cinco_bloques(cliente, con_odoo):
    cuerpo = cliente.get("/resumen").text
    for titulo in ("Leads nuevos", "Cotizaciones", "Pagos registrados",
                   "Sin dueño", "Clientes esperando"):
        assert titulo in cuerpo
    assert "Escuela Las Américas" in cuerpo
    assert "$4 200.00" in cuerpo
    assert "$500.00" in cuerpo


def test_la_pantalla_dice_que_odoo_no_contesto(cliente, odoo_caido):
    cuerpo = cliente.get("/resumen").text
    assert "Hay huecos en este resumen" in cuerpo
    assert "No se pudo leer Odoo" in cuerpo
    # Y NO pinta un cero en esos bloques.
    assert "Ninguna se cotizó hoy" not in cuerpo


def test_la_pantalla_avisa_que_falta_el_secreto(cliente):
    assert "no está armado" in cliente.get("/resumen").text


def test_la_pantalla_avisa_que_no_hay_celular(cliente, monkeypatch):
    monkeypatch.setenv("RESUMEN_SECRETO", "el-bueno")
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", "publica")
    monkeypatch.setenv("VAPID_CLAVE_PRIVADA", "privada")
    cuerpo = cliente.get("/resumen").text
    assert "no tiene ningún celular" in cuerpo


def test_la_pantalla_deja_ver_el_dia_anterior(cliente):
    cuerpo = cliente.get("/resumen").text
    assert "El día anterior" in cuerpo
    ayer = (resumen.hoy() - timedelta(days=1)).isoformat()
    assert f"/resumen?dia={ayer}" in cuerpo


def test_una_fecha_mala_cae_en_hoy(cliente):
    cuerpo = cliente.get("/resumen", params={"dia": "ayer"}).text
    from app import calendario
    assert calendario.dmy(resumen.hoy().isoformat()) in cuerpo
