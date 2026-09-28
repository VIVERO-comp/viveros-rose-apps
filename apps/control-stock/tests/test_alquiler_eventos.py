"""Alquiler, Evento y Boda eran lo mismo con tres botones (dueño,
28/09/2026): queda UN botón «+ Alquiler / Eventos» sobre el tipo "renta",
que era el más completo — el único con selector de cobro (total del evento
o planta por planta) y con la sección informativa de plantas que se llevan
y regresan. Boda y evento quedan RETIRADOS: sin botón, pero sus
cotizaciones viejas siguen abriendo, editando y descargando igual (por eso
se quedan en TIPOS). SV-BODA y SV-EVENTO no se borran de Odoo; solo dejan
de usarse en cotizaciones nuevas."""

from app import cotizaciones


def test_un_solo_boton_para_alquiler_y_eventos():
    assert "renta" in cotizaciones.ORDEN_TIPOS
    assert "boda" not in cotizaciones.ORDEN_TIPOS
    assert "evento" not in cotizaciones.ORDEN_TIPOS
    assert cotizaciones.etiqueta_para_cotizar("renta") == "Alquiler / Eventos"


def test_el_unificado_conserva_lo_mejor_de_renta():
    """Lo que hizo ganar a "renta": el selector de cobro y la sección de
    plantas que regresan. Y nada de Odoo cambió de nombre: mismo producto,
    misma plantilla, mismas etiquetas de orden y de cliente."""
    meta = cotizaciones.TIPOS["renta"]
    assert meta["cobro_elegible"] is True
    assert meta["etiqueta_orden"] == "RENTAL"
    assert meta["etiqueta_cliente"] == "Renta / Alquiler"
    assert meta["plantilla"] == "vivero_rose_pedidos.plantilla_servicio_renta"
    assert (meta["secciones"][0]["servicios"]["producto"]
            == "vivero_rose_pedidos.producto_sv_alquiler_evento")
    assert any(s.get("catalogo") == "informativo" for s in meta["secciones"])


def test_boda_y_evento_siguen_en_tipos_para_lo_viejo():
    """Retirados pero NO borrados: una cotización vieja de Boda o Evento
    necesita su entrada en TIPOS para abrir, editar y descargar."""
    for tipo in ("boda", "evento"):
        meta = cotizaciones.TIPOS[tipo]
        assert meta.get("retirado") is True
        # Todo lo que el releer/editar/PDF necesita sigue en su sitio.
        assert meta["plantilla"] and meta["etiqueta_orden"] and meta["secciones"]


def test_la_pantalla_ofrece_solo_el_boton_unificado(cliente, monkeypatch):
    monkeypatch.setenv("ODOO_URL", "http://odoo-de-prueba:8069")
    monkeypatch.setenv("ODOO_DB", "pruebas")
    monkeypatch.setenv("ODOO_USER", "prueba")
    monkeypatch.setenv("ODOO_PASSWORD", "prueba")
    r = cliente.get("/venta")
    assert r.status_code == 200
    assert "+ Alquiler / Eventos" in r.text
    assert "+ Boda" not in r.text
    assert "+ Evento" not in r.text
