# Control de Stock

App interna del empleado encargado del inventario del vivero. Hermana de
[Recepción de Supermercados](../supermercado-recepcion/): misma
arquitectura, mismo login, mismo despliegue.

## Propósito

Que el vivero no vuelva a quedarse sin stock sin darse cuenta:

1. **Ver de un vistazo cómo está el inventario** — score de salud de 0 a 100
   con totales y desglose por categoría.
2. **Enterarse a tiempo** — lista ordenada de menor a mayor cantidad y
   alertas cuando algo baja del umbral (3 por defecto, configurable).
3. **Corregir el stock al momento** — desde la alerta se llega al producto y
   "Modificar stock" aplica el ajuste en Odoo (vía order-api); la tienda y
   Recepción leen de Odoo, así que se actualizan solas.
4. **Mantener el inventario cuadrado** — hoja PDF semanal para revisar
   caminando el vivero, y ciclo quincenal con Excel (plantilla → contar →
   importar → revisar diferencias → confirmar).

## Usuarios

Los mismos de Recepción (Génesis, Rubén) más el encargado de stock
(stockmaster). Altas por consola:

```bash
python -m app.usuarias crear <usuario> "<Nombre>"    # pide la contraseña
python -m app.usuarias clave <usuario>               # cambiarla
python -m app.usuarias desactivar <usuario>          # revocar acceso
python -m app.usuarias lista
```

En producción: `docker compose exec control-stock python -m app.usuarias …`

## Flujo

- **Inicio**: score (100 − 6 por crítico − 2 por bajo − 15 si el conteo
  quincenal está vencido, >15 días), totales y tarjetas por categoría.
- **Stock**: buscador + filtros por categoría, lista de menor a mayor. Tocar
  un producto abre el modal de ajuste con −/+ y cantidad editable.
- **Alertas**: campanita con contador; cada alerta lleva al producto y se
  marca atendida (o se cierra sola si el stock se recupera). El umbral se
  cambia desde el mismo panel. Historial en SQLite.
- **Inventario**: hoja PDF, plantilla/import de Excel con pantalla de
  diferencias y confirmación, historial de conteos.

El ajuste trabaja sobre la cantidad **física** (lo que se cuenta caminando
el vivero; el disponible para vender se muestra aparte) y manda también la
física que el empleado tenía en pantalla (`esperada`): si Odoo ya cambió
(una venta en el medio), el order-api responde `conflicto`, nada se escribe
y la app muestra el valor fresco. **Ningún ajuste se aplica sin que el
empleado revise y confirme.**

## Tecnología

FastAPI + Jinja2 + SQLite (stdlib) + httpx, servida con uvicorn; PDF con
fpdf2 y Excel con openpyxl. Sin Node y sin build. El diseño es el del
prototipo aprobado (azul noche + dorado, tarjetas crema, intro animada de la
rosa): el CSS/JS del prototipo vive casi intacto en `app/static/`.

## Desarrollo local

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --reload --port 8092
```

Sin `.env` la app corre con datos de prueba y ajustes simulados. Pruebas:
`.venv/bin/pytest`.

## Variables de entorno

Ver [.env.example](.env.example): `STOCK_PROXY_URL` + `STOCK_API_KEY`
(lecturas), `ORDER_API_URL` + `ORDER_API_KEY` (ajustes), `COOKIE_SEGURA`,
`CONTROL_STOCK_DB`, `CONTROL_STOCK_ARCHIVOS`.

## Integraciones

- **stock-proxy** `GET /v1/inventario` — inventario completo con categoría
  de Odoo, disponible y físico. Exige `X-API-Key`.
- **order-api** `POST /api/stock/ajustes` — la única escritura: ajustes de
  inventario absolutos en Odoo (`stock.quant` + `action_apply_inventory`),
  con candado de cantidad esperada y auditoría en la base tienda
  (migración 012). Exige `X-API-Key`.
- La app **nunca** toca Odoo ni su base directamente.

## Despliegue

`inventario.plantaspanama.com` → nginx del droplet → `127.0.0.1:8092`.
Compose con proyecto y servicio **`control-stock`** (nombres únicos a
propósito: un alias genérico compartido entre pruebas y producción ya causó
fallos intermitentes en el checkout). La instancia de pruebas se llama
`control-stock-pruebas` y va en la red de pruebas.

```bash
rsync -a --exclude .venv --exclude datos . hermes@143.244.167.222:control-stock/
ssh hermes@143.244.167.222 'cd control-stock && docker compose up -d --build'
```

Rollback: `docker compose down` (la base y los PDFs quedan en `./datos`).
**Ningún deploy sin el OK explícito del dueño.**

## Estado actual

Fase 1 completa contra la instancia de pruebas. Pendiente fase 2:
recordatorio semanal, aviso diario fuera de la app, umbral por producto.

## Próximos pasos

- Recordatorio semanal (día configurable) y notificación quincenal.
- Aviso diario por correo/WhatsApp con el resumen de críticos.
- Umbral por producto.
- Usuario de Odoo dedicado para los ajustes (`ODOO_STOCK_USERNAME` /
  `ODOO_STOCK_PASSWORD` en el `.env` del order-api). Hoy los ajustes usan el
  usuario general de Odoo; el dedicado dejaría los movimientos de inventario
  a nombre propio y con permisos mínimos. Mejora posterior, no bloqueante.
- Vigilancia mínima de insumos (prefijo `IN-`): solo un aviso si un insumo
  se va a negativo por ventas al súper, **sin** meterlos al score ni al
  catálogo B2B. Hoy los insumos quedan del todo fuera de Control de Stock.
