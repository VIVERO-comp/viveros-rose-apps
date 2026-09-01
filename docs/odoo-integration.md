# Integración con Odoo

## Estado

**Diseño propuesto / pendiente de implementación.** Para la app de Recepción
de Supermercados **no existe hoy ningún endpoint**: la app trabaja con datos
simulados detrás del objeto `odooApi` de `src/App.jsx`. Nada de lo que sigue
debe leerse como una API existente.

Como referencia de patrones que **sí** están en producción para otras apps:
el panel `/admin` y el portal `/repartidor` hablan con Odoo a través del
**order-api** (que valida las claves contra Odoo en cada petición y ejecuta
acciones como marcar un pedido pagado), y el stock se consulta vía
**stock-proxy** (lectura de la base de Odoo con usuario de solo lectura). Lo
natural es que esta integración siga el mismo camino: un servicio Python
(FastAPI) intermedio, nunca la app contra Odoo directamente.

## Operaciones necesarias (conceptuales)

```text
GET  entregas pendientes                → alimenta fetchOrdenes()
GET  detalle de factura                 → incluido hoy en fetchOrdenes()
GET  catálogo de productos              → alimenta fetchCatalogo() (intercambios)
POST resultado de recepción             → recibe confirmarRecepcion()
POST devolución confirmada (regreso)    → recibe confirmarRegreso()
POST intercambio creado / completado    → recibe crearIntercambio() / completarIntercambio()
```

Todas: **diseño propuesto, pendiente de implementación.**

## Datos que la app necesita recibir (por entrega)

```text
invoice_number        ← "factura" en la app (la referencia del empleado; NO consecutivo)
odoo_id               ← "odooId"
customer              ← "cliente"   (ej. Super Xtra)
branch                ← "sucursal"  (ej. Villalobos)
date                  ← "fecha"

products:
    product_id / sku  ← "sku" (VR-001…)
    name              ← "nombre"
    sent_quantity     ← "enviado"
    unit_price        ← "precio"
```

El prototipo guarda además `pedido` (S00774), `reserva` (RES-2231) y
`transferencia` (WH/OUT/00512) como IDs técnicos que no se muestran; el
mapeo real (¿`sale_order_id`? ¿`picking_id`?) se definirá al revisar la
integración con Odoo.

## Datos que la app devolverá al confirmar una recepción

```text
invoice_number
odoo_id

products:
    product_id / sku
    sent_quantity
    accepted_quantity      ← editado por el empleado (0 ≤ aceptado ≤ enviado)
    returned_quantity      ← siempre enviado − aceptado, calculado por la app

confirmed_by               ← "empleadoId"
confirmed_at               ← "fechaHora" (ISO)
```

Es casi 1:1 con lo que el código ya envía hoy (en español):
`confirmarRecepcion({odooId, factura, lineas: [{sku, aceptado, devuelto}],
empleadoId, fechaHora})`.

## Resto de operaciones, tal como el código ya las emite

| Operación | Payload actual del prototipo |
| --- | --- |
| Devolución confirmada (las plantas regresaron físicamente al vivero; debería reponer stock) | `{odooId, factura, lineas: [{sku, cantidad}], empleadoId, fechaHora}` |
| Intercambio creado (dañadas recogidas en el súper) | `{cliente, sucursal, lineas: [{sku, danadas}], empleadoId, fechaHora}` |
| Intercambio completado (reemplazo entregado) | `{intercambioId, empleadoId, fechaHora}` |

Preguntas abiertas para cuando se diseñe la API real: cómo modela Odoo la
devolución (¿nota de crédito?, ¿devolución de picking?), contra qué documento
se registra un intercambio (hoy no referencia ninguna factura), y cómo se
autentica el empleado (el patrón existente de enlaces personales
`...?clave=...` validados contra Odoo es el candidato natural).
