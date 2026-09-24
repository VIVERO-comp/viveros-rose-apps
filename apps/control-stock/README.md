# Control de Stock (Control Viverorose)

La app interna del equipo del vivero, en `inventario.plantaspanama.com`.
Nació para el inventario y hoy es la app de operar el negocio: **Calendario**
(la pestaña de entrada), **Stock**, **Vender**, **Retail**, **Fichas** y
**Ajustes** — y además sirve la **cara CRM**
(`/crm/calendario`), el mismo calendario con piel de Twenty que se ve como
pestaña dentro del Twenty real. En producción desde septiembre 2026.

Menú vigente (22/09/2026): sin "Inicio" y con Calendario de primero — entrar
a la app (`/`) abre `/calendario`; el tablero del home vive solo como lienzo
de `/?tab=stock` y `/?tab=ajustes`.

## Propósito original (Stock)

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

## Usuarios y acceso

El acceso vigente es **login con Google + invitaciones**:

- `/auth/google` — el botón "Continuar con Google" del login.
- `/invitacion/{token}` — link compartible de un solo uso; se crea desde la
  pestaña Ajustes (`/ajustes/invitar`) y se revoca con `/ajustes/revocar`.
- `AJUSTES_ADMINS` (emails por coma) decide quién ve Ajustes e invita.
- En desarrollo local, `SIN_LOGIN=<usuario>` salta la pantalla de login
  (la variable no existe en el droplet).

El esquema viejo de usuario+contraseña por consola sigue disponible como
respaldo (`python -m app.usuarias crear|clave|desactivar|lista`; en
producción con `docker compose exec control-stock …`).

## Pestañas que no son Stock

- **Calendario** (`/calendario`, `app/calendario.py`) — el calendario del
  equipo. Linear es el dueño de los datos (proyecto CALENDARIO ROSE, equipo
  Viverorose): cada actividad es un issue, con los 13 tipos del grupo "Tipo
  de actividad". Vista principal: semana por horas; también día, mes y lista,
  y una vista de celular propia (un día a la vez). Se crea, se abre, se
  termina, se mueve y se cancela desde la pantalla (cancelar nunca borra el
  issue). El menú lateral lleva el log "Leads de servicio por agendar"
  (equipo LEAD de Linear, solo etiquetas de servicio). Sync con teléfonos:
  feed ICS por empleada (`/calendario.ics?t=<token>`, `app/calendario_ics.py`)
  y empuje a Google Calendar (`app/calendario_google.py`, conexión por
  empleada en Ajustes). Sin `LINEAR_API_KEY` corre en modo muestra.
  Desde la **Fase 4** (24/09/2026) el calendario además CIERRA el embudo:
  ver la sección propia abajo.
- **Retail** (`/retail`, `app/retail.py`) — kanban de leads retail/mayorista
  amarrado a las ventas de la app. Fuera del menú con `RETAIL_EN_MENU=0`.
- **Vender** y **Cotizaciones de servicio** — abajo tienen sección propia.

## El embudo de leads y la Fase 4 (`app/linear_leads.py`, `app/agenda.py`)

El estado de un lead vive en **un solo tablero: el equipo LEAD de Linear**.
Twenty es la ficha del cliente, Odoo es solo dinero y el calendario son solo
fechas. Esta app no guarda estado de leads en ninguna tabla.

### Los 8 estados

| # | Estado | Lo mueve |
|---|---|---|
| 1 | Nuevo | solo, al nacer el lead |
| 2 | Hablando | solo, cuando **el cliente** escribe |
| 3 | Cotizado | solo, al generar la cotización |
| 4 | Por agendar | solo, cuando entra un **pago real** en Odoo |
| 5 | Agendado | **esta app**: al crear la actividad del calendario |
| 6 | Entregado | **esta app**: un toque en «Hecha» |
| 7 | Ganado | **esta app**: entregado + saldo 0 |
| — | Perdido | barrido de 14 días, o a mano con motivo |

`app/linear_leads.py` es la única puerta a ese tablero: lee los issues con su
estado, sus etiquetas y sus comentarios, mueve el estado, intercambia la
etiqueta `Resp:` y comenta firmado. Reglas que respeta y no se negocian:

- **Las etiquetas nunca se crean solas**: `_label_id()` solo busca. Si el
  nombre no existe, queda el aviso en el log y el issue va sin ella.
- **El responsable va por etiqueta `Resp: <nombre>`, nunca por `assignee`**
  (para no pagar un asiento de Linear por empleado). Sumar a alguien al
  equipo es crear su etiqueta en Linear: sin tocar código.
- **La escalera no degrada**: un movimiento automático solo avanza. Ir hacia
  atrás exige `manual=True` **con motivo**, que queda como comentario firmado
  en el issue.
- El interruptor de escritura es el MISMO del calendario
  (`CALENDARIO_ESCRITURA`): es la misma cuenta de Linear y la misma pregunta.

### Fase 4: el calendario cierra el embudo (`app/agenda.py`)

- El bloque **«Por agendar»** del calendario lista los leads que ya pagaron,
  con su etiqueta de pago y **el saldo que trae Odoo** (`sale.order.
  saldo_pendiente`, el campo de la Fase 3b).
- **«Agendar»** pide fecha · tipo · responsable. Los 5 tipos son etiquetas que
  YA existen en Linear: Entrega · Instalación (el «montaje») · Mantenimiento ·
  Visita · Recogida (el «retiro»). Al guardar nace la actividad amarrada al
  lead y el lead pasa a **Agendado**. El responsable se **sugiere** del lead y
  es editable.
- El amarre actividad↔lead viaja en la marca de la descripción del issue,
  junto a la hora: `<!-- rose hora=10:00|dur=60|lugar=…|lead=LEAD-91|resp=Ruben -->`.
  Sin tabla nueva, y sobrevive a mover, editar y reprogramar.
- En la actividad, **el saldo va arriba del botón**, nunca escondido: quien
  entrega lo ve antes de marcar «Hecha».
- **«Hecha»** manda el lead a **Entregado**. Si el saldo quedó en cero, sigue
  solo a **Ganado**; si entregó debiendo, se queda en Entregado con la
  etiqueta «Cobrar saldo» y sale en la vista Cobrar de Linear. Cuando entra el
  pago que salda, `cerrar_los_que_ya_pagaron()` lo pasa a Ganado (corre en
  fondo al abrir el calendario: el addon de Odoo solo empuja hasta «Por
  agendar», y a un Entregado la escalera no lo degrada).
- **Una Recogida no mueve el estado**: retirar las plantas de alquiler después
  del evento es solo una actividad, el lead ya se entregó.
- **Reprogramar** mueve la fecha sin tocar el estado.
- **Cualquier empleado con acceso al calendario puede marcar «Hecha»**, no
  solo el responsable.
- **El saldo no se inventa**: si Odoo no contesta, la pantalla lo dice en vez
  de mostrar $0 y mentir — un $0 falso hace que alguien entregue sin cobrar.
  Y sin saldo confiable, un lead nunca pasa a Ganado.

## La cara CRM (`/crm/calendario`)

El MISMO calendario, con la piel de Twenty, para verse como pestaña dentro
del Twenty real (`crm.plantaspanama.com`): el nginx del droplet CRM proxya
`/crm/` hacia esta app y `chats-nav.js` (repo `viveros-rose-crm`) lo abre
como iframe, igual que la pestaña Chats.

- Rutas: `GET /crm/calendario` (semana/día/mes/lista + ficha de actividad,
  ficha de lead y formulario, todo server-rendered), `POST
  /crm/calendario/actividad` (crear) y `POST
  /crm/calendario/actividad/{id}/estado` (terminar/reabrir/cancelar) — las
  escrituras van por las mismas funciones de `app/calendario.py`.
- Colores: los de los **labels de Twenty** (retail/entrega verde, alquiler
  rojo, mantenimiento azul), traducidos por `app/crm_twenty.py::repintar()`
  sin tocar la paleta del calendario de inventario.
- La **ficha del lead** se abre como un registro de Twenty: teléfono, fecha
  de llegada y la conversación de WhatsApp completa, leídos del Twenty real
  por su API REST (`TWENTY_URL` + `TWENTY_API_KEY`, solo lectura y
  fail-soft: sin clave la ficha muestra lo que da Linear).
- Login propio de esa cara: `/crm/login` + `/crm/auth/google*` (el OAuth no
  corre dentro de un iframe: el botón sale con `target=_top`). El callback
  `CRM_PUBLIC_BASE_URL/crm/auth/google/callback` debe estar autorizado en el
  OAuth Client de Google.

## Flujo (Stock)

- **Tablero** (`/?tab=stock`): score (100 − 6 por crítico − 2 por bajo − 15
  si el conteo quincenal está vencido, >15 días), totales y tarjetas por
  categoría.
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
fpdf2, Excel con openpyxl, `psycopg` para las fichas en la base `tienda` e
`icalendar` para el feed ICS. Sin Node y sin build. El diseño es el del
prototipo aprobado (blanco y dorado en escritorio, azul noche en el
teléfono); el CSS/JS vive en `app/static/`. Los clientes de Linear, Twenty,
Google y Odoo son módulos propios (`app/calendario.py`, `app/crm_twenty.py`,
`app/calendario_google.py`, `app/ventas.py`), todos con httpx/stdlib.

## Desarrollo local

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --reload --port 8092
```

Sin `.env` la app corre con datos de prueba, ajustes simulados y el
calendario en modo muestra. Con `SIN_LOGIN=<usuario>` no hay pantalla de
login (solo local). Pruebas: `.venv/bin/pytest`.

## Variables de entorno

**La referencia completa y comentada es [.env.example](.env.example)** (unas
25 variables). Por grupos:

- Lecturas/escrituras de stock: `STOCK_PROXY_URL` + `STOCK_API_KEY`,
  `ORDER_API_URL` + `ORDER_API_KEY`, `CATALOGO_PUBLICADO_URL`.
- App: `COOKIE_SEGURA`, `CONTROL_STOCK_DB`, `CONTROL_STOCK_ARCHIVOS`,
  `PUBLIC_BASE_URL`.
- Acceso: `GOOGLE_CLIENT_ID/SECRET`, `AJUSTES_ADMINS`, `SIN_LOGIN` (solo local).
- Vender/Odoo (XML-RPC): `ODOO_URL/DB/USER/PASSWORD`,
  `VENTA_DIARIO_YAPPY/EFECTIVO`, `VENTA_TAG_LOCAL`, `VENTA_CLIENTE_LOCAL`,
  `VENTA_FOTOS_DIR`.
- Fotos internas: `CLOUDINARY_*`.
- Fichas: `TIENDA_DSN`, `FICHAS_EDITORES`.
- Calendario: `LINEAR_API_KEY`, `LINEAR_TEAM_CALENDARIO_ID`,
  `LINEAR_PROJECT_CALENDARIO_ID`, `CALENDARIO_ESCRITURA`.
- Espejo de ventas al CRM: `CRM_LEADS_URL`, `CRM_LEADS_SECRETO`.
- Cara CRM: `CRM_PUBLIC_BASE_URL`, `TWENTY_URL`, `TWENTY_API_KEY`.

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
- **Linear (GraphQL)** — el calendario entero (proyecto CALENDARIO ROSE) y
  el log de leads de servicio (equipo LEAD). Lecturas con caché y refresco
  en fondo; escrituras solo con `CALENDARIO_ESCRITURA=1`.
- **Twenty (REST, solo lectura)** — la ficha del lead de la cara CRM
  (`app/crm_twenty.py`): leadWeb por `linearIssueId` → Person → mensajes de
  WhatsApp. Fail-soft: sin `TWENTY_API_KEY` la ficha muestra lo de Linear.
- **Google Calendar API** — el empuje del calendario al Google Calendar de
  cada empleada (`app/calendario_google.py`), one-way: Linear manda.
- **Puente CRM del frontend** — `POST /api/crm/lead-inventario`
  (`app/crm_leads.py`, con `CRM_LEADS_SECRETO`): las ventas/cotizaciones
  hechas aquí nacen también como lead en Linear/Twenty.
- La app **nunca** toca Odoo ni su base directamente, con UNA excepción
  aprobada por el dueño (08/09/2026): la página **Crear Venta** (abajo).

## Crear Venta (página /venta, pestaña "Vender")

Ventas locales del vivero (el flujo de supermercados quedó retirado con el
corte de Super Extra, 18/09/2026): la
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
rsync -a --exclude .venv --exclude datos --exclude .env --exclude archivos \
      --exclude 'control-stock.db*' --exclude '__pycache__' \
      -e "ssh -p 2222" . hermes@143.244.167.222:control-stock/
ssh -p 2222 hermes@143.244.167.222 'cd control-stock && docker compose up -d --build'
```

Mejor todavía: rsyncar desde un checkout limpio del commit a desplegar
(`git worktree add /tmp/deploy-apps <commit>`), para no arrastrar trabajo a
medias del árbol local. El `.env` del droplet no se pisa nunca (por eso el
`--exclude .env`); las variables nuevas se agregan a mano allá.

Retail y CRM están **fuera del menú** en producción desde el 24/09/2026
(`RETAIL_EN_MENU=0` y `CRM_EN_MENU=0` en el `.env` del droplet; pedido del
dueño: "que no se vea en el inventario pero dejalo activo"). Es solo
visibilidad — distinto de `COMPRAS_ACTIVAS` / `PROYECTOS_ACTIVOS`, que sí
apagan las rutas: `/retail` y `/crm` siguen vivos y funcionando para quien
entre con la URL, y Control quedó encendido en el menú.

La cara CRM necesita además, en el `.env` del droplet: `CRM_PUBLIC_BASE_URL`,
`TWENTY_URL` y `TWENTY_API_KEY`, y el callback
`https://crm.plantaspanama.com/crm/auth/google/callback` autorizado en el
OAuth Client de Google (hecho el 22/09/2026).

Las fotos de las tarjetas salen de Cloudinary con el mapa sku → hash de
`app/datos_fotos/fotos.json` (copia del `src/data/fotos.json` de
viveros-rose-frontend): una foto nueva del catálogo aparece en inventario
recién en el siguiente deploy. El precio que se muestra es el `list_price`
de Odoo tal cual (igual que la tienda), formateado en el servidor
(`calculos.precio_online`).

Rollback: `docker compose down` (la base y los PDFs quedan en `./datos`).
**Ningún deploy sin el OK explícito del dueño.**

## Estado actual

**En producción** en `inventario.plantaspanama.com`, con todas las pestañas
del menú y la cara CRM dentro de Twenty (22/09/2026).

## Próximos pasos

- Aviso diario por correo/WhatsApp con el resumen de críticos.
- Umbral por producto.
- Usuario de Odoo dedicado para los ajustes (`ODOO_STOCK_USERNAME` /
  `ODOO_STOCK_PASSWORD` en el `.env` del order-api). Hoy los ajustes usan el
  usuario general de Odoo; el dedicado dejaría los movimientos de inventario
  a nombre propio y con permisos mínimos. Mejora posterior, no bloqueante.
- Vigilancia mínima de insumos (prefijo `IN-`): solo un aviso si un insumo
  se va a negativo por ventas al súper, **sin** meterlos al score ni al
  catálogo B2B. Hoy los insumos quedan del todo fuera de Control de Stock.
