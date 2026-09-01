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
GET  catálogo de productos              → IMPLEMENTADO: stock-proxy /v1/catalogo
GET  sucursales                         → IMPLEMENTADO: stock-proxy /v1/sucursales
POST resultado de recepción             → recibe confirmarRecepcion()
POST devolución confirmada (regreso)    → recibe confirmarRegreso()
POST intercambio creado / completado    → recibe crearIntercambio() / completarIntercambio()
```

Las lecturas de **sucursales y catálogo ya son reales**: viven en
`vivero-rose-stock-proxy` (`GET /v1/sucursales` y `GET /v1/catalogo`, con
`X-API-Key` obligatoria). Los clientes supermercado se configuran con
`SUPERMERCADOS_REFS` y el catálogo con `CATALOGO_FILTRO` (prefijos de SKU
separados por coma; el catálogo de supermercado usa el prefijo `PLT-`,
separado del `PL-` de la tienda online y con precios propios). La app las
consume con degradación en cadena (caché → último valor bueno → datos de
prueba) vía `STOCK_PROXY_URL`/`STOCK_API_KEY`.

Las escrituras (`POST …`) siguen siendo **diseño propuesto, pendiente de
implementación** en el order-api (router `supermercado`).

> **Deuda técnica**: el stock-proxy lee la base de Odoo por SQL directo
> (esquema fijado a Odoo 19). Cuando conectemos las facturas hay que evaluar
> migrar estas lecturas a la API de Odoo (JSON-RPC) para no depender del
> esquema de tablas en cada actualización de Odoo.

## Sucursales: vienen de Odoo, nunca hardcodeadas

El prototipo trae una constante `SUCURSALES` con 4 entradas fijas (incluye
cadenas a las que no se vende): son **solo datos de prueba** y no pueden
quedar así al conectar la API.

Cómo está modelado en Odoo:

- El único cliente supermercado es **Super Extra** (los datos de prueba del
  prototipo lo escriben "Super Xtra"), con 38 sucursales.
- **Padre**: un `res.partner` empresa (`is_company=True`),
  `name="Super Extra"`, `ref="SUPER-EXTRA"`, `customer_rank=1`.
- **Sucursales**: un `res.partner` hijo por sucursal, con `parent_id` al
  padre, `type="delivery"`, `name="Super Extra <Sucursal>"` y `ref` = código
  CL del ERP de origen (CL-0001, CL-0002…). La **identidad de una sucursal
  es su `ref`**, no su nombre.
- Estos registros los crea y mantiene
  `viveros-rose-odoo-config/scripts/crear_sucursales.py` (idempotente por
  `ref`, dry run por defecto).

**`GET sucursales`** (diseño propuesto / pendiente de implementación):
devuelve los contactos hijos del partner con `ref="SUPER-EXTRA"`,
aproximadamente:

```text
partners:
    partner_id
    ref                  ← código CL (la identidad estable de la sucursal)
    branch_name          ← nombre del contacto
    address
```

Alimentará el `fetchSucursales()` que reemplazará a la constante
`SUCURSALES` de la app.

En `fetchOrdenes()`, por lo mismo: `cliente` = el partner **padre**
("Super Extra") y `sucursal` = el contacto **hijo** al que se facturó.

Si en el futuro entra otra cadena, se le crea su propio partner padre (con
su `ref`) y sus sucursales como hijos; el endpoint pasa a devolver las
sucursales del conjunto de padres supermercado, indicando a qué cadena
pertenece cada una.

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
