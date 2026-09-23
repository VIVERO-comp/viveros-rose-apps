"""Espejo de las ventas de Vender en el CRM (Twenty + Linear + Odoo).

Decisión de Abraham (22/09/2026): toda venta o cotización hecha en la
pestaña Vender vive también en Linear como un lead normal — misma tarjeta
y misma label de tipo — más la label "Hecha desde inventario"; y si el
cliente ya existía (llegó antes por WhatsApp), no se duplica: se casa por
celular y se reutiliza su lead.

El puente es la ruta /api/crm/lead-inventario del frontend en Vercel (el
único lugar donde nacen los leads); este módulo solo la llama. Es un paso
best-effort: sin CRM_LEADS_URL/CRM_LEADS_SECRETO en el entorno, o si la
ruta falla, la venta sigue igual y solo se pierde el espejo (fail-soft).

La respuesta trae el codigoRef PP-XXXXX (con él se amarra la oportunidad
de Odoo vía lead_ref), el identifier del issue (la llave del kanban de la
pestaña Retail) y si el lead ya existía.
"""
import os

import httpx

TIEMPO_MAXIMO = 8  # segundos: la venta no puede quedarse esperando al CRM


def _var(nombre):
    valor = (os.environ.get(nombre) or "").strip()
    return valor or None


def configurado():
    return bool(_var("CRM_LEADS_URL") and _var("CRM_LEADS_SECRETO"))


def _llamar(datos, contexto):
    """POST al puente. Devuelve el cuerpo si vino ok, o None (fail-soft)."""
    if not configurado():
        return None
    try:
        respuesta = httpx.post(
            _var("CRM_LEADS_URL").rstrip("/") + "/api/crm/lead-inventario",
            json={"secreto": _var("CRM_LEADS_SECRETO"), **datos},
            timeout=TIEMPO_MAXIMO)
        cuerpo = respuesta.json() if respuesta.status_code == 200 else {}
        if cuerpo.get("ok"):
            return cuerpo
        print(f"crm_leads: {contexto} rechazado "
              f"({respuesta.status_code}: {respuesta.text[:200]})", flush=True)
    except Exception as error:  # el espejo jamás tumba la venta
        print(f"crm_leads: {contexto} falló: {error!r}", flush=True)
    return None


def espejar_venta(nombre, celular, tipo, orden, total, empleada, issue=""):
    """Espeja una venta/cotización recién creada. Devuelve el dict de la
    ruta ({"codigoRef", "identifier", "url", "existente"}) o None si el
    espejo no está configurado o falló — el que llama sigue sin él.

    `issue` (LEAD-NN): si la empleada venía de la ficha de un lead concreto
    del kanban Retail (el lead "pendiente"), el puente reutiliza ESE issue
    en vez de resolver por celular — así no se duplica el lead."""
    cuerpo = _llamar({"nombre": (nombre or "").strip(),
                      "celular": (celular or "").strip(),
                      "tipo": tipo, "orden": orden, "total": total or 0,
                      "empleada": (empleada or "").strip(),
                      "issue": (issue or "").strip()},
                     f"espejo de {orden}")
    return cuerpo if cuerpo and cuerpo.get("codigoRef") else None


def marcar_odoo(codigo_ref, etapa=""):
    """Le pone al issue del lead la label "Odoo" (la señal de que su
    oportunidad del Flujo ya quedó sincronizada) y, con `etapa`
    (COTIZADO/FACTURADO/PAGADO), registra el avance de venta: la label de
    etapa en Linear y el espejo etapaVenta en Twenty — es lo que pintan
    los chips de la lista de Chats (23/09/2026). Se llama después de
    amarrar por lead_ref (etapa COTIZADO) y al cobrar (FACTURADO).
    Best-effort, como todo el espejo."""
    if not (codigo_ref or "").strip():
        return
    datos = {"accion": "odoo", "codigoRef": codigo_ref.strip()}
    if (etapa or "").strip():
        datos["etapa"] = etapa.strip().upper()
    _llamar(datos, f"label Odoo de {codigo_ref}")
