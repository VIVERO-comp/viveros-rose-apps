"""URLs de Cloudinary a partir de la copia local de fotos.json."""

import json

import pytest

from app import fotos


@pytest.fixture(autouse=True)
def cache_limpia():
    fotos.reiniciar_cache_fotos()
    yield
    fotos.reiniciar_cache_fotos()


@pytest.fixture
def mapa(tmp_path, monkeypatch):
    ruta = tmp_path / "fotos.json"
    ruta.write_text(json.dumps({
        "cloud": "demo123",
        "porSku": {
            "PL-ROMERO": ["abc111", "def222"],
            "PL-RECORTADA": ["fff333"],
        },
        "recortesPorHash": {"fff333": [0.1, 0.2, 0.9, 0.8]},
    }))
    monkeypatch.setattr(fotos, "RUTA_FOTOS", ruta)
    monkeypatch.setattr(fotos, "RUTA_FOTOS_APPS", tmp_path / "sin-apps.json")


@pytest.fixture
def mapa_apps(mapa, tmp_path, monkeypatch):
    ruta = tmp_path / "fotos-apps.json"
    ruta.write_text(json.dumps({
        "cloud": "demo123",
        "porSku": {"PL-ROMERO": ["aaa999", "bbb888"]},
    }))
    monkeypatch.setattr(fotos, "RUTA_FOTOS_APPS", ruta)


def test_url_foto_usa_el_primer_hash(mapa):
    assert fotos.url_foto("PL-ROMERO") == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160/productos/PL-ROMERO/abc111")


def test_url_foto_antepone_el_recorte_manual(mapa):
    # El recorte decidido en la página de revisión del catálogo va como
    # c_crop antes del resto, igual que en fotos.ts del frontend.
    assert fotos.url_foto("PL-RECORTADA") == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "c_crop,x_0.1,y_0.2,w_0.9,h_0.8/"
        "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160/productos/PL-RECORTADA/fff333")


def test_url_foto_sin_foto_es_none(mapa):
    assert fotos.url_foto("PL-SIN-FOTO") is None


def test_url_foto_sin_archivo_es_none(tmp_path, monkeypatch):
    # Sin la copia del json (deploy sin el paso de fotos) nada revienta:
    # todas las tarjetas caen al emoji.
    monkeypatch.setattr(fotos, "RUTA_FOTOS", tmp_path / "no-existe.json")
    assert fotos.url_foto("PL-ROMERO") is None


def test_la_foto_solo_apps_gana_sobre_la_del_catalogo(mapa_apps):
    # Fotos internas que el dueño no quiere en la tienda: namespace apps/.
    assert fotos.url_foto("PL-ROMERO") == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160/apps/PL-ROMERO/aaa999")


def test_sin_foto_solo_apps_se_usa_la_del_catalogo(mapa_apps):
    assert "productos/PL-RECORTADA/fff333" in fotos.url_foto("PL-RECORTADA")


def test_la_foto_subida_desde_la_app_gana_sobre_todo(mapa_apps, monkeypatch):
    # El puntero de la base (el pincel del modal) manda sobre los dos json.
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo123")
    assert fotos.url_foto("PL-ROMERO", hash_subido="cafe00000000") == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "f_auto,q_auto,c_fill,g_auto,ar_1:1,w_160/apps/PL-ROMERO/cafe00000000")


def test_info_foto_trae_grande_y_descarga(mapa):
    info = fotos.info_foto("PL-ROMERO")
    # La grande no recorta a cuadrado (se ve la foto entera) y la descarga
    # fuerza el attachment con el sku como nombre de archivo.
    assert info["grande"] == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "f_auto,q_auto,c_limit,w_900/productos/PL-ROMERO/abc111")
    assert info["descarga"] == (
        "https://res.cloudinary.com/demo123/image/upload/"
        "fl_attachment:PL-ROMERO/productos/PL-ROMERO/abc111")


def test_la_firma_de_subida_es_la_de_cloudinary(monkeypatch):
    # Mismo esquema del pipeline del catálogo: sha1 de los params ordenados
    # más el secreto, y el public_id por sha1 del contenido (12 hex).
    import hashlib

    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo123")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "clave")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secreto")
    capturado = {}

    class RespuestaFalsa:
        status_code = 200
        text = "{}"

    def post_falso(url, data=None, files=None, timeout=None):
        capturado.update({"url": url, "data": data})
        return RespuestaFalsa()

    monkeypatch.setattr(fotos.httpx, "post", post_falso)
    contenido = b"foto-de-prueba"
    hash_foto = fotos.subir_foto(contenido, "PL-ROMERO")
    assert hash_foto == hashlib.sha1(contenido).hexdigest()[:12]
    assert capturado["url"].endswith("/demo123/image/upload")
    assert capturado["data"]["public_id"] == f"apps/PL-ROMERO/{hash_foto}"
    base = (f"public_id=apps/PL-ROMERO/{hash_foto}"
            f"&timestamp={capturado['data']['timestamp']}")
    assert capturado["data"]["signature"] == hashlib.sha1(
        (base + "secreto").encode()).hexdigest()


def test_el_mapa_solo_apps_del_repo_es_valido():
    datos = json.loads(fotos.RUTA_FOTOS_APPS.read_text())
    assert datos["cloud"]
    assert isinstance(datos["porSku"], dict) and datos["porSku"]


def test_la_copia_del_repo_es_valida():
    # La copia versionada en app/datos_fotos debe poder cargarse siempre.
    datos = json.loads(fotos.RUTA_FOTOS.read_text())
    assert datos["cloud"]
    assert isinstance(datos["porSku"], dict) and datos["porSku"]


# ---------------------------------------------------------------------------
# El pincel del modal: POST /fotos/{sku}
# ---------------------------------------------------------------------------

@pytest.fixture
def subida_lista(monkeypatch):
    """Credenciales falsas y una subida a Cloudinary que no sale a la red."""
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo123")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "clave")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secreto")
    subidas = []

    def subir(contenido, sku):
        subidas.append((sku, contenido))
        return "cafe00000000"

    monkeypatch.setattr(fotos, "subir_foto", subir)
    return subidas


def test_cambiar_foto_sube_y_guarda_el_puntero(cliente, subida_lista):
    from app import datos as capa_datos

    r = cliente.post("/fotos/PL-ROMERO",
                     files={"archivo": ("planta.jpg", b"bytes-de-foto", "image/jpeg")})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["resultado"] == "aplicada"
    assert "apps/PL-ROMERO/cafe00000000" in cuerpo["img"]
    assert "fl_attachment:PL-ROMERO" in cuerpo["descarga"]
    assert subida_lista == [("PL-ROMERO", b"bytes-de-foto")]
    # El puntero queda en la base: la próxima pantalla muestra esta foto.
    assert capa_datos.fotos_subidas() == {"PL-ROMERO": "cafe00000000"}


def test_cambiar_foto_sin_credenciales_es_503(cliente, monkeypatch):
    for variable in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
                     "CLOUDINARY_API_SECRET"):
        monkeypatch.delenv(variable, raising=False)
    r = cliente.post("/fotos/PL-ROMERO",
                     files={"archivo": ("planta.jpg", b"foto", "image/jpeg")})
    assert r.status_code == 503


def test_cambiar_foto_rechaza_lo_que_no_es_imagen(cliente, subida_lista):
    r = cliente.post("/fotos/PL-ROMERO",
                     files={"archivo": ("datos.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400
    assert subida_lista == []


def test_cambiar_foto_rechaza_sku_invalido(cliente, subida_lista):
    # El sku viaja en el public_id de Cloudinary: nada fuera del alfabeto
    # de los SKUs reales (un slash codificado ni siquiera llega: 404 del
    # router).
    r = cliente.post("/fotos/PL..raro",
                     files={"archivo": ("planta.jpg", b"foto", "image/jpeg")})
    assert r.status_code == 400
    assert subida_lista == []


def test_cambiar_foto_reporta_el_error_de_cloudinary(cliente, subida_lista, monkeypatch):
    from app import datos as capa_datos

    def falla(contenido, sku):
        raise RuntimeError("Cloudinary respondió 401")

    monkeypatch.setattr(fotos, "subir_foto", falla)
    r = cliente.post("/fotos/PL-ROMERO",
                     files={"archivo": ("planta.jpg", b"foto", "image/jpeg")})
    assert r.status_code == 502
    assert "401" in r.json()["mensaje"]
    # Nada quedó apuntado: la pantalla sigue con la foto anterior.
    assert capa_datos.fotos_subidas() == {}
