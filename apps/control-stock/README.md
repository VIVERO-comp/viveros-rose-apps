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
- **Stock**: dos vistas del mismo inventario (18/09/2026), con buscador,
  filtros, detalle y modal de ajuste compartidos:
  - **Stock online** — solo las plantas que el cliente ve hoy en
    plantaspanama.com. Es un **espejo** del sitio, no un interruptor: cada
    build del frontend deja en `/catalogo-publicado.json` los SKU que
    salieron publicados y la app los lee de ahí (`datos.obtener_publicados`,
    caché de 10 min, degradación al último valor bueno). Si el sitio no
    responde y no hay valor previo, la pestaña lo dice y muestra el global en
    vez de fingir que no hay nada publicado.
  - **Stock global** — todas las plantas activas de Odoo (el
    `GET /v1/inventario` de siempre). Las que no están en la tienda llevan la
    marca "No está en la tienda"; es la única vista donde esa marca aparece,
    porque es donde la distinción importa. Los insumos `IN-` y los servicios
    `SV-` siguen fuera.

  Quién está online lo decide el servidor: `main.py` le pone la marca `on` a
  cada planta antes de mandarla a la pantalla. Desde Inicio (score, totales,
  alertas) siempre se aterriza en el global, porque esos números se calculan
  sobre todo el inventario.
- **Crear planta** (botón flotante, 18/09/2026): reemplaza a la sugerencia
  por WhatsApp, que no creaba nada. Nombre, referencia (se propone sola desde
  el nombre, `datos.sku_sugerido`), categoría, cantidad física inicial,
  precio de venta, **costo**, altura de/a, "no viaja en moto" y **nombre
  secundario + nombre científico**. Los dos nombres se escriben en las notas
  internas de la ficha (`description`) con el bloque
  `<h3>nombre segundario: …<br>nombre cientifico: …</h3>`, que es la
  convención del catálogo (ortografía del dueño, respetada tal cual); sin
  ninguno de los dos no se escribe un bloque vacío. La idea es que la planta
  quede indistinguible de una creada a mano en Odoo. La foto queda fuera a
  propósito: la imagen de perfil la maneja el dueño desde los adjuntos, y la
  foto de la tarjeta se sube con el modal de foto de siempre. Crea el producto en Odoo por el
  order-api (`POST /api/productos`) y **después** aplica la cantidad con el
  ajuste de siempre; si el ajuste falla, la planta ya creada NO se repite y
  la pantalla avisa que el stock quedó en 0. La planta nace solo en Odoo:
  para salir en la tienda necesita foto y regenerar el catálogo del sitio,
  así que aparece en Stock global y no en online.
- **Stock (lista)**: buscador + filtros, lista de menor a mayor. Tocar un producto
  abre su **vista de detalle** (`/?producto=SKU`); tocar la foto abre el
  modal de foto. En computadora la lista es un grid de tarjetas a pantalla
  completa con −/+ de físico en cada tarjeta: el número solo cambia en
  pantalla y aparece "Guardar en Odoo" para confirmar (mismo candado
  `esperada` del modal).
- **Detalle de producto** (16/09/2026, reemplaza a la pestaña Fichas):
  foto grande (abre el modal de foto), datos de Odoo en solo lectura
  (precio, disponible, físico, estado), botón Modificar stock (el modal de
  siempre) y la ficha editable — altura, descripción y guía de cuidado. La
  **altura** (de ⬚ a ⬚ cm, 17/09/2026) se guarda en Odoo por el order-api,
  no en la base de la tienda: con una sola medida se llena la primera casilla
  y con las dos vacías el producto no muestra altura en la tienda. En
  computadora va a dos columnas; en el teléfono, una. Volver/atrás regresa
  a la lista con filtro y búsqueda intactos.
- **Publicar / no publicar** (18/09/2026, dentro del detalle): el interruptor
  de la tienda. Marca o desmarca la casilla "Publicada en la tienda" del
  producto en Odoo (campo `publicado` del módulo, escrito por el order-api
  con `PUT /api/productos/{sku}/publicacion`), y el build del frontend deja
  fuera del sitio lo que esté desmarcado.
  **Desmarcarlo no borra ni archiva NADA**: la planta sigue en el inventario,
  en Crear Venta, en las cotizaciones, con su ficha y con todas sus fotos —
  las del catálogo en Cloudinary y las internas de las apps—, así que volver
  a publicarla es un toque. Es a propósito lo contrario de archivar en Odoo,
  que sí se la lleva de todas partes.
  El renglón bajo el interruptor cuenta lo que pasa **de verdad**, que no
  siempre es lo que dice la casilla: `pub` es la casilla de Odoo (la
  intención) y `on` es el espejo del sitio (lo que el cliente ve hoy). Con la
  casilla marcada y `on` en falso, la planta todavía no sale porque le falta
  su foto y su entrada en el catálogo del frontend, que es un paso aparte y a
  mano; la pantalla lo dice en vez de fingir que ya está publicada. Ni
  publicar ni despublicar cambian el sitio al instante: hace falta la
  siguiente reconstrucción del frontend.
- **Alertas**: campanita con contador; cada alerta lleva al producto y se
  marca atendida (o se cierra sola si el stock se recupera). El umbral se
  cambia desde el mismo panel. Historial en SQLite.
- **Inventario**: hoja PDF, plantilla/import de Excel con pantalla de
  diferencias y confirmación, historial de conteos. Desde el 11/09/2026
  está fuera del menú (pedido del dueño); todo sigue vivo en `/?tab=inv`.
- **Fichas** (dentro del detalle; `FICHAS_EDITORES=*`: todos los usuarios;
  una lista por coma la limita, y sin permiso la ficha se ve en solo
  lectura): descripción y guía de cuidado (luz, riego, dificultad, nota)
  por producto, precargadas con lo que hoy dice el sitio
  (`app/datos_fichas/catalogo.json`, copia que refresca
  `scripts/actualizar_catalogo.py` antes de cada deploy). Guardar escribe
  en la tabla `fichas_producto` de la base `tienda` (Postgres del droplet,
  `TIENDA_DSN`, migración 015 del order-api; sin la variable cae al SQLite
  local, solo desarrollo). El sitio público toma las fichas cuando el dueño
  corre `scripts/traer_fichas.py` + `generar_catalogo.py` en el frontend:
  guardar aquí NO cambia la tienda al instante. Estreno: correr una vez
  `docker compose exec control-stock python -m app.sembrar_fichas` para
  sembrar la tabla con los textos actuales del sitio (idempotente; sin eso,
  una regeneración del catálogo con la tabla vacía dejaría placeholders).

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
  de Odoo, disponible, físico, altura y `published` (la casilla "Publicada en
  la tienda", que la ficha pinta como interruptor). Exige `X-API-Key`. Una
  planta despublicada sigue viniendo aquí con todo su stock: solo queda fuera
  del sitio público.
- **order-api** `PUT /api/productos/{sku}/altura` — la altura de la planta
  (en cm) que se edita en la ficha; vive en Odoo, no en la base de la tienda,
  porque es un dato del producto. Exige `X-API-Key`.
- **order-api** `PUT /api/productos/{sku}/publicacion` — el interruptor de la
  tienda desde la ficha. Escribe ESA casilla en Odoo y nada más: no archiva,
  no toca el stock y no borra ni desasocia una sola foto, así que reponer la
  planta en el sitio es marcarla otra vez. Exige `X-API-Key`.
- **order-api** `POST /api/productos` — el alta de una planta del formulario
  "Crear planta": crea el `product.template` en Odoo con la forma de las
  plantas del catálogo (consu + almacenable, categoría del sitio, factura por
  pedido) y **sin impuestos**, porque las plantas van exentas de ITBMS. No
  pone stock y no publica nada en la tienda. Exige `X-API-Key`.
- **plantaspanama.com** `GET /catalogo-publicado.json` — qué SKU están
  publicados hoy en la tienda; es lo que separa "Stock online" de "Stock
  global". Es el único lugar del que la app lee sin clave (es público) y el
  único que no es Odoo. Se puede apuntar a otro sitio con
  `CATALOGO_PUBLICADO_URL`.
- **order-api** `POST /api/stock/ajustes` — la otra escritura: ajustes de
  inventario absolutos en Odoo (`stock.quant` + `action_apply_inventory`),
  con candado de cantidad esperada y auditoría en la base tienda
  (migración 012). Exige `X-API-Key`.
- La app **nunca** toca Odoo ni su base directamente, con UNA excepción
  aprobada por el dueño (08/09/2026): la página **Crear Venta** (abajo).

## Crear Venta (página /venta, pestaña "Vender")

Ventas locales del vivero, aparte por completo del flujo Super Extra: la
empleada busca plantas PL- (nombre o SKU, con foto `image_128` de Odoo
cacheada 24h en disco), arma un carrito y elige entre **Generar cotización**
(`sale.order` borrador) o **Pagado y confirmar pedido** (elige
Yappy/Efectivo y la app corre en Odoo: confirmar → validar la entrega →
facturar → publicar → registrar el pago). Usa el diario de ventas normal
(nunca el EXTRA), la etiqueta LOCAL y el contacto "Cliente Local" si no dan
nombre; los precios y totales siempre los pone Odoo. Vive en
`app/ventas.py`, habla XML-RPC directo (la excepción de arriba) y registra
cada venta en SQLite (`ventas_locales`) con estado por pasos: si Odoo falla
a la mitad, el historial muestra hasta dónde llegó y Reintentar retoma sin
duplicar. Los PDF (cotización y factura estándar de Odoo) salen por
`/report/pdf/...` con sesión web: eso exige que `ODOO_PASSWORD` sea una
contraseña real de login, no una API key (las API keys solo sirven para
XML-RPC). Como los productos facturan por cantidad entregada, validar la
salida es obligatorio antes de facturar — y además deja el stock correcto:
la venta local se lleva las plantas en el momento.

## Cotizaciones de servicio (Alquiler, Boda, Evento, Mantenimiento,
Paisajismo, Proyecto, Instalación)

Botones por tipo junto a "+ NUEVA VENTA", cada uno con su mini-formulario
(`app/cotizaciones.py`): cliente (nombre y celular), las plantas y
materiales del catálogo cuando el tipo los lleva, y **Servicios** —
renglones repetibles donde la empleada describe el trabajo en sus palabras
(un párrafo que crece al escribir) y le pone su monto, con "+ Añadir otro
servicio" para sumar los que haga falta. Reemplaza a los campos de monto
con etiqueta fija que había hasta el 17/09/2026 (la etiqueta enlatada no
describía el trabajo real). Cada renglón sale como una línea del producto
de servicio del tipo (SV-ALQUILER, SV-INSTALACION…) con el párrafo como
descripción; un renglón sin párrafo hereda el nombre del producto y un
monto en 0 escrito a mano crea igual su línea (servicio incluido sin
cargo). **Sin mínimo de plantas**: una cotización puede ir solo de
servicio, con 0 plantas. Lo digitado se guarda en el borrador del servidor
(`venta_borrador.servicios`), así que agregar o quitar una planta —que
recarga la página— no borra los párrafos, y un error de validación vuelve
a pintar el formulario en vez de redirigir.

**Personalizado** (`/venta/servicio-personalizada`) es todo a mano: los
renglones se escriben con su descripción, cantidad y precio
(`renglon_texto[]`, `renglon_cantidad[]`, `renglon_precio[]`) y salen con
el producto de servicio **SV-PERSONALIZADO**, que no viene del addon: se
resuelve por su código y se crea la primera vez que hace falta
(`_id_producto_personalizado`), así funciona igual en el Odoo real y en
odoo-pruebas. El buscador del catálogo sigue disponible para las plantas
reales, que van con el precio de Odoo.

**Datos del cliente** (17/09/2026): las tres pantallas comparten el bloque
`app/plantillas/_cliente.html` — nombre, celular, empresa, RUC, cédula,
correo y dirección, en dos columnas compactas y todo opcional menos el
nombre. El RUC va al Tax ID (`vat`) y, si no hay RUC, ahí va la cédula (en
Panamá es el mismo dato); la cédula queda además en `ref`, la empresa en
`company_name`, el correo en `email` y la dirección en `street`. A un
cliente que ya existe solo se le llenan los campos vacíos: Odoo es la
fuente de verdad.

## Despliegue

`inventario.plantaspanama.com` → nginx del droplet → `127.0.0.1:8092`.
Compose con proyecto y servicio **`control-stock`** (nombres únicos a
propósito: un alias genérico compartido entre pruebas y producción ya causó
fallos intermitentes en el checkout). La instancia de pruebas se llama
`control-stock-pruebas` y va en la red de pruebas.

```bash
./scripts/actualizar_fotos.sh   # refresca la copia del mapa de fotos del catálogo
rsync -a --exclude .venv --exclude datos . hermes@143.244.167.222:control-stock/
ssh hermes@143.244.167.222 'cd control-stock && docker compose up -d --build'
```

Las fotos de las tarjetas salen de Cloudinary con el mapa sku → hash de
`app/datos_fotos/fotos.json` (copia del `src/data/fotos.json` de
viveros-rose-frontend): una foto nueva del catálogo aparece en inventario
recién en el siguiente deploy. El precio que se muestra es el `list_price`
de Odoo tal cual (igual que la tienda), formateado en el servidor
(`calculos.precio_online`).

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
