# Recepción de Supermercados

App móvil (web) para que el empleado que hace las entregas a supermercados
registre desde el celular lo que pasó con cada factura. Conocida también como
"App de entregas · Plantas Panamá".

## Propósito

Las reservas, pedidos y facturas se crean **antes** en Odoo. Esta app no crea
pedidos ni maneja inventario: es la interfaz con la que el empleado, parado en
el supermercado, compara lo enviado contra lo que el súper aceptó y deja
registradas las diferencias. La referencia que ve el empleado es siempre el
**número de factura** (#774, #781…). Los números **no** son consecutivos y la
app nunca asume que un salto signifique una factura faltante; los IDs técnicos
de Odoo (`odooId`, pedido, reserva, transferencia) se guardan internamente y
no se muestran.

## Usuarios

Empleados de Vivero Rose que reparten a supermercados (Super Xtra, Riba
Smith, Super 99, Supermercados Rey…).

## Flujo

Pestañas de la navegación inferior (con contadores):

1. **Entregas** — lista de entregas pendientes que vienen de Odoo, ordenadas
   por fecha (la más reciente arriba, nunca por número de factura), con
   buscador por factura, súper o sucursal. Al abrir una factura **todo viene
   marcado como aceptado** (`aceptado = enviado`); el empleado solo baja con −
   las plantas que el súper no recibió. Reglas: `0 ≤ aceptado ≤ enviado` y
   `devuelto = enviado − aceptado`, calculado por la app (el empleado nunca
   escribe la devolución). Resumen en vivo de unidades y montos en B/. y botón
   "Restablecer". Antes de confirmar se muestran solo las diferencias.
2. **Devolver** — plantas rechazadas, agrupadas por factura, pendientes de
   regresar al vivero. "Confirmar regreso" es lo que a futuro avisará a Odoo
   que vuelven al stock.
3. **Cambios (Intercambios)** — plantas dañadas que el empleado recoge en el
   súper y hay que reemplazar: elegir súper/sucursal → buscar cada planta en
   el catálogo → indicar cuántas → confirmar. Queda "Pendiente de devolver"
   hasta pulsar "Confirmar reemplazo entregado".
4. **Historial** — entregas confirmadas (con estado "Regreso pendiente" o
   "Completada") e intercambios completados, con montos.

Además: splash animado con el logo al abrir y pantallas de éxito tras cada
confirmación.

## Tecnología

- React 18 + Vite 5. Íconos con `lucide-react`. Estilos inline, estado local
  con `useState`; sin router, sin TypeScript, sin backend propio.
- Toda la lógica y la UI viven en `src/App.jsx` (un solo archivo, heredado
  del prototipo). Partirlo en componentes queda como paso futuro.
- Tipografía Montserrat cargada desde Google Fonts en `index.html`.

## Desarrollo local

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # genera dist/
```

## Deploy

La app se publica en **https://super.plantaspanama.com** (Vercel). Es una
PWA instalable (`public/manifest.webmanifest`, sin service worker por
ahora); `vercel.json` reescribe todas las rutas a `/index.html` (SPA).

Pasos (los ejecuta Abraham):

1. **Vercel**: proyecto nuevo importando el repo
   `VIVERO-comp/viveros-rose-apps` · Root Directory:
   `apps/supermercado-recepcion` · Framework Preset: **Vite** (build
   `npm run build`, salida `dist/`, sin variables de entorno).
2. **Dominio**: en el proyecto de Vercel, agregar
   `super.plantaspanama.com`.
3. **GoDaddy** (cuenta kortostocks, zona `plantaspanama.com`): registro
   CNAME `super` → `cname.vercel-dns.com`.

> **Pendiente importante**: la app **no tiene autenticación** — cualquiera
> con la URL la abre. Con datos simulados es aceptable, pero **debe tener
> autenticación antes de conectar Odoo** (el candidato natural es el patrón
> de enlaces personales `...?clave=...` validados contra Odoo que ya usan
> `/admin` y `/repartidor`).

## Variables de entorno

Ninguna por ahora: la app trabaja con datos simulados. Cuando se conecte la
API real de Odoo se agregará un `.env.example` con la URL y credenciales
necesarias.

## Integraciones

Todo el acceso a datos pasa por el objeto `odooApi` al inicio de
`src/App.jsx`. Hoy sus seis funciones devuelven datos simulados; para
conectar la app solo hay que implementarlas contra la API real. Payloads
exactos del código:

| Función | Envía / recibe |
| --- | --- |
| `fetchOrdenes()` | Devuelve las órdenes completas: `factura`, `odooId`, `pedido`, `reserva`, `transferencia`, `cliente`, `sucursal`, `fecha`, `estado`, `lineas[{sku, nombre, precio, enviado}]` |
| `fetchCatalogo()` | Devuelve el catálogo `[{sku, nombre, precio}]` (para intercambios) |
| `confirmarRecepcion(p)` | `{odooId, factura, lineas: [{sku, aceptado, devuelto}], empleadoId, fechaHora}` |
| `confirmarRegreso(p)` | `{odooId, factura, lineas: [{sku, cantidad}], empleadoId, fechaHora}` |
| `crearIntercambio(p)` | `{cliente, sucursal, lineas: [{sku, danadas}], empleadoId, fechaHora}` |
| `completarIntercambio(p)` | `{intercambioId, empleadoId, fechaHora}` |

El diseño conceptual de los endpoints del lado Odoo está en
[`docs/odoo-integration.md`](../../docs/odoo-integration.md) (propuesto,
pendiente de implementación).

## Estado actual

- **Prototipo funcional con datos simulados**: facturas de prueba #774
  (Super Xtra · Villalobos, 16 productos, B/.116.99), #781 (Riba Smith ·
  Bella Vista) y #770 (Super 99 · Costa del Este). Catálogo de 22 SKUs.
- La constante `SUCURSALES` está **hardcodeada con datos de prueba** (4
  sucursales fijas, incluyendo cadenas a las que no se vende). En la
  realidad las sucursales viven en Odoo como contactos hijos del cliente
  "Super Extra", identificadas por su `ref` (código CL); ver
  `docs/odoo-integration.md`.
- **Todo el estado vive en memoria**: al recargar la página se pierde lo
  registrado.
- El empleado está fijo en el código (`EMPLEADO`, "Génesis"): no hay login.
- Cambios respecto del prototipo original (`vivero-app.jsx` de Claude.ai),
  verificados por diff:
  1. El logo y el icono, que iban incrustados en base64, ahora son archivos
     en `src/assets/` (mismos bytes decodificados del prototipo). Los iconos
     512/192/180 de `public/` se generaron a partir del original de 1024 px.
  2. **Se eliminó el marco de teléfono falso** del prototipo (el borde oscuro
     con notch simulado que dibujaba un celular de 390×800 dentro de la
     página): la app ahora ocupa la pantalla completa del dispositivo
     (`100dvh`, ancho máximo 480 px centrado en escritorio).

## Próximos pasos

- Implementar `odooApi` contra la API real (ver `docs/odoo-integration.md`).
- Reemplazar la constante `SUCURSALES` por un `fetchSucursales()` en
  `odooApi` que traiga las sucursales reales desde Odoo (los contactos
  hijos del partner "Super Extra", identificados por su `ref`).
- Identificación real del empleado (hoy hardcodeado).
- Persistencia: que una recepción confirmada sobreviva a recargar la página.
- Partir `src/App.jsx` en componentes cuando la app crezca.
- Autenticación de la empleada (obligatoria antes de conectar Odoo; ver
  "Deploy").
- Agregar un service worker (el manifest PWA ya existe), como ya lo tiene el
  portal del repartidor.
