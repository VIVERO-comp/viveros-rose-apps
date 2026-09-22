"""Que los PDF se BAJEN al celular en vez de abrirse en el visor.

Abraham (18/09/2026): "cuando pongo descargar el PDF en celular me lleva a
una pantalla y no puedo hacer nada". La hoja de conteo se servía "inline", o
sea pidiéndole al navegador que la mostrara: en el teléfono eso abre el visor
de PDF, que tapa la app, no siempre trae botón de guardar y no deja volver.
Estas pruebas dejan clavado que todo PDF sale como descarga.
"""

from app.main import cabeceras_descarga


def test_el_pdf_se_baja_no_se_previsualiza():
    cabeceras = cabeceras_descarga("propuesta-S00079.pdf")
    assert cabeceras["Content-Disposition"].startswith("attachment;")
    assert "inline" not in cabeceras["Content-Disposition"]
    # Sin esto el navegador puede olfatear el contenido e ignorar el attachment.
    assert cabeceras["X-Content-Type-Options"] == "nosniff"


def test_un_nombre_con_acentos_viaja_entero_y_con_repuesto():
    cabeceras = cabeceras_descarga("cotización-niña.pdf")
    disposicion = cabeceras["Content-Disposition"]
    # El nombre real, porcentaje-codificado (RFC 5987), para los navegadores
    # que lo entienden...
    assert "filename*=UTF-8''cotizaci%C3%B3n-ni%C3%B1a.pdf" in disposicion
    # ...y un filename ASCII de repuesto para los que no, sin romper la
    # cabecera con bytes que no son latin-1.
    assert 'filename="cotizacin-nia.pdf"' in disposicion
    disposicion.encode("latin-1")


def test_un_nombre_sin_nada_ascii_no_deja_la_cabecera_coja():
    cabeceras = cabeceras_descarga("完成.pdf")
    assert 'filename="documento.pdf"' in cabeceras["Content-Disposition"]


def test_la_hoja_de_conteo_se_descarga(cliente, con_inventario):
    """El camino real: generar la hoja desde la pantalla y bajarla."""
    respuesta = cliente.post("/conteos/pdf", follow_redirects=False)
    assert respuesta.status_code == 303
    destino = respuesta.headers["location"]

    hoja = cliente.get(destino)
    assert hoja.status_code == 200
    assert hoja.headers["content-type"] == "application/pdf"
    assert hoja.headers["content-disposition"].startswith("attachment;")
    assert hoja.content[:4] == b"%PDF"


def test_los_enlaces_de_pdf_abren_en_otra_pestana(cliente, con_inventario):
    """Aunque la descarga falle, la pantalla de la app tiene que seguir viva
    detrás: por eso los PDF abren en otra pestaña."""
    cliente.post("/conteos/pdf", follow_redirects=False)
    pagina = cliente.get("/?tab=stock").text
    for trozo in pagina.split("<a ")[1:]:
        enlace = trozo.split(">")[0]
        if "/pdf" in enlace:
            assert 'target="_blank"' in enlace, f"PDF sin nueva pestaña: {enlace}"
