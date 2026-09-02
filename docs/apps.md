# Aplicaciones internas — detalle

Índice: [Recepción de Supermercados](#recepción-de-supermercados) ·
[Control de Stock](#control-de-stock) ·
[Panel de administración](#panel-de-administración-admin) ·
[Portal del repartidor](#portal-del-repartidor-repartidor) ·
[Avisos Web Push compartidos](#avisos-web-push-compartidos) ·
[Migración futura de admin/repartidor](#migración-futura-de-adminrepartidor)

---

## Recepción de Supermercados

**Vive en:** `apps/supermercado-recepcion/` (este repo), en producción en
[super.plantaspanama.com](https://super.plantaspanama.com) · **Estado:**
lecturas de Odoo en vivo (pedidos, sucursales, catálogo vía stock-proxy);
las confirmaciones aún se guardan localmente · **Usuarios:** empleadas que
entregan a supermercados · **Tecnología:** Python (FastAPI + Jinja2 + SQLite), con login propio; el prototipo React quedó como referencia congelada.

Registra desde el celular lo que pasó con cada entrega a un supermercado. La
referencia de la empleada es el **número del pedido sin prefijo** ("Pedido
00774"; por dentro viaja completo, S00774), con la "Ref. súper"
(`client_order_ref`) debajo cuando existe; los números no son consecutivos y
la app nunca infiere que falte uno por un salto.

Cuatro pestañas con navegación inferior y contadores:

- **Entregas**: pendientes ordenadas por fecha (nunca por número de factura),
  buscador por factura/súper/sucursal. Al abrir una factura todo arranca como
  aceptado (`aceptado = enviado`); el empleado solo baja lo rechazado. Reglas
  duras: `0 ≤ aceptado ≤ enviado`, `devuelto = enviado − aceptado` calculado
  por la app. Resumen en vivo en B/. (ej.: #774 original B/.116.99; si el
  súper rechaza 1 Novio Chino → aceptado B/.114.74, devuelto B/.2.25).
  Confirmación en dos pasos mostrando solo las diferencias.
- **Devolver**: lo rechazado, agrupado por factura, hasta que el empleado lo
  deja físicamente en el vivero ("Confirmar regreso" → a futuro repone stock
  en Odoo).
- **Cambios**: intercambios de plantas dañadas recogidas en el súper — elegir
  cliente y sucursal (las 38 reales, con buscador), buscar cada planta con su
  disponible en vivo, indicar
  cantidades, confirmar; queda "Pendiente de devolver" hasta entregar el
  reemplazo.
- **Historial**: entregas (con regreso pendiente/completada) e intercambios
  completados, con montos.

Integración: lecturas por el stock-proxy (`/v1/entregas`, `/v1/sucursales`,
`/v1/catalogo`); las escrituras a Odoo esperan el router `supermercado` del
order-api — ver [`odoo-integration.md`](odoo-integration.md). Autenticación
propia por empleada (PBKDF2 + sesiones en SQLite, altas por consola).

---

## Control de Stock

**Vive en:** `apps/control-stock/` (este repo); irá a
inventario.plantaspanama.com · **Estado:** fase 1 construida (falta la
validación contra la instancia de pruebas y el deploy) · **Usuarios:** el
encargado de stock (stockmaster) más Génesis y Rubén · **Tecnología:**
Python (FastAPI + Jinja2 + SQLite), fpdf2 (PDF) y openpyxl (Excel); el
prototipo HTML aprobado es la referencia visual y su CSS/JS vive casi
intacto en `app/static/`.

Que el vivero no se quede sin stock sin darse cuenta. Tres pestañas:

- **Inicio**: score de salud 0–100 (100 − 6 por producto crítico − 2 por
  bajo − 15 con el conteo quincenal vencido, >15 días), totales (unidades,
  con stock, agotadas) y tarjetas por categoría.
- **Stock**: buscador y filtros por categoría, lista de menor a mayor
  cantidad. Tocar un producto abre el modal "Modificar stock" (−/+ y
  cantidad a mano); guardar aplica el ajuste **absoluto** en Odoo vía
  order-api. El ajuste viaja con la cantidad `esperada` que el empleado veía:
  si Odoo cambió en el medio, vuelve `conflicto` con el valor fresco y nada
  se escribe.
- **Inventario**: hoja de conteo en PDF (solo revisión), ciclo quincenal con
  Excel (plantilla protegida → contar → importar → pantalla de diferencias →
  confirmar) e historial de conteos.

Alertas: campanita con contador; se crea una por producto crítico
(disponible < umbral, 3 por defecto y configurable desde el panel), lleva al
producto, se marca atendida y se cierra sola si el stock se recupera.
Historial en SQLite. El universo de productos es solo `PL-` (los insumos no
llevan ese código y quedan fuera).

Integración: lecturas por el stock-proxy (`/v1/inventario`); la única
escritura es `POST /api/stock/ajustes` del order-api — ver
[`odoo-integration.md`](odoo-integration.md). Autenticación propia por
empleada, idéntica a Recepción.

---

## Panel de administración (`/admin`)

**Vive en:** `viveros-rose-frontend` → `src/pages/admin.astro` (~2.100
líneas) · **Estado:** producción, en plantaspanama.com/admin (noindex, fuera
del sitemap) · **Usuarios:** dueño / administración · **Tecnología:** página
Astro con JS inline + funciones serverless en Vercel + `src/lib/admin.ts`
(ayudas puras con tests).

Móvil primero, "se siente app": cuatro pantallas con navegación fija abajo.

- **INICIO** — resumen del período (hoy / semana / mes): pedidos generados,
  plantas vendidas, valor, estado de la calle (sin tomar / en camino / con
  retraso) y tiempos de entrega. Desde aquí se activan los avisos Web Push de
  pedido nuevo (el order-api guarda la suscripción con rol "dueño"; un aviso
  tocado abre `/admin?pantalla=validar`).
- **PEDIDOS** — lista completa: por validar arriba, luego con retraso, en
  proceso y terminados con "Ver más" (paginados por cursor en el order-api).
  Detalle con historial. Solo consulta.
- **VALIDAR** — marcar un pedido como pagado (`accion_marcar_pagado` en
  Odoo), con confirmación explícita y constancia de quién lo hizo. Las
  acciones de pago (validar, capturar, reversar) viven únicamente aquí.
- **STOCK** — buscar una planta y ver su disponibilidad vía stock-proxy. El
  nombre y la foto salen del catálogo local del sitio (`products.ts`); al
  order-api solo viajan los SKUs.

**Credencial**: el enlace personal `...?clave=...` de la etiqueta "Admin" en
Odoo. La clave se guarda en el navegador, se limpia de la URL con
`replaceState` y viaja **siempre** en el header `X-Clave-Admin` —
`peticionAdmin` (en `lib/admin.ts`) lanza un error si detecta la clave en una
URL, para que nunca quede en logs. El order-api la valida contra Odoo en cada
petición: rotarla o quitar la etiqueta revoca el acceso al instante.

**Funciones serverless que consume** (en `src/pages/api/admin/`, proxies al
order-api que guardan `ORDER_API_KEY` fuera del navegador):
`resumen`, `pedidos`, `pedidos/terminados`, `pedidos/[numero]` (detalle),
`pedidos/[numero]/validar-pago`, `.../capturar-pago`, `.../reversar-pago`,
`stock`, `avisos`; más las compartidas `push/clave-publica` y
`push/suscribir`.

---

## Portal del repartidor (`/repartidor`)

**Vive en:** `viveros-rose-frontend` → `src/pages/repartidor.astro` (~2.000
líneas) · **Estado:** producción (noindex, fuera del sitemap) · **Usuarios:**
repartidores · **Tecnología:** página Astro con JS inline + funciones
serverless en Vercel + `src/lib/repartidor.ts`; **PWA instalable**
(`manifest-repartidor.webmanifest`, icono propio, service worker
`sw-avisos.js`) con pull-to-refresh táctil.

Tres pantallas con navegación fija abajo:

- **INICIO** — saludo y tarjeta de ganancias de hoy (pagos sellados en Odoo +
  propinas registradas al calificar), pendientes y recientes.
- **PEDIDOS** — mis entregas, disponibles y los de otros; detalle → agarrar →
  WhatsApp del cliente → foto de entrega.
- **AVISOS** — historial de avisos agrupado por día, con el activador de Web
  Push arriba.

**Credencial**: enlace personal `...?clave=...` generado por Odoo, guardado
en el navegador. La página es estática y los datos llegan por fetch a sus
funciones de servidor. Detalle fino: si alguien pega una clave de **admin**
en `/repartidor`, la página lo detecta (prueba `/api/admin/resumen` con la
clave solo en el header) y lo redirige a `/admin`.

**Funciones serverless que consume** (en `src/pages/api/repartidor/`):
`resumen`, `pedidos`, `pedidos/[numero]`, `pedidos/[numero]/agarrar`,
`pedidos/[numero]/foto`, `avisos`; más `push/clave-publica` y
`push/suscribir`.

---

## Avisos Web Push compartidos

`public/sw-avisos.js` (70 líneas) es el service worker común de `/admin` y
`/repartidor`. A propósito **no** tiene manejador de `fetch` (no cachea ni
intercepta nada del sitio): solo muestra las notificaciones que manda el
order-api (payload `{titulo, cuerpo, url}` desde `push.py`), mantiene el
globito rojo del icono instalado con `setAppBadge` (la cuenta son las
notificaciones sin atender; iOS 16.4+ con la página instalada) y abre la URL
del aviso al tocarlo. Se actualiza con `skipWaiting` + `clients.claim`.

---

## Migración futura de admin/repartidor

Diseño de referencia por si algún día se separan del frontend hacia este
repositorio. **Nada de esto está ejecutado ni decidido**; hoy la decisión es
que se quedan en `viveros-rose-frontend` (ver
[`architecture.md`](architecture.md)).

Separarlas implicaría, como mínimo:

1. **Dominio propio** (p. ej. `admin.plantaspanama.com`), porque dejarían de
   ser mismo-origen con el sitio: DNS en GoDaddy + certificados.
2. **Proyecto Vercel propio** (o servicio equivalente) que aloje las
   funciones serverless con `ORDER_API_URL` / `ORDER_API_KEY`, hoy parte del
   proyecto del sitio.
3. **CORS en el order-api** para el origen nuevo, o replicar el patrón proxy
   completo en el proyecto nuevo.
4. **Catálogo**: `src/data/products.ts` se regenera desde Odoo; el proyecto
   separado tendría que regenerarlo con el mismo script (nunca mantener una
   copia editada a mano).
5. **Layout**: duplicar lo necesario de `Base.astro` o extraer un paquete
   compartido (`packages/ui`), que hoy no existe.
6. **PWA**: mover manifests, iconos y `sw-avisos.js`, y re-registrar el
   service worker en el origen nuevo (las instalaciones existentes de los
   repartidores quedarían apuntando al origen viejo).
7. **URLs**: los enlaces personales `...?clave=...` que los empleados ya
   tienen guardados/instalados cambiarían; habría que regenerarlos y
   redirigir los viejos.
