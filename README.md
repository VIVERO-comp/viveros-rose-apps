# Viveros Rose Apps

Aplicaciones internas de **Vivero Rose** para operaciones, empleados,
supermercados y otros procesos del negocio.

## Principios

- **Odoo es el sistema central** de operaciones e inventario. Las apps de este
  repositorio no lo reemplazan ni duplican su lógica: son interfaces
  especializadas para tareas concretas (por ejemplo, registrar en el celular
  lo que un supermercado aceptó de una entrega).
- Las apps consumen datos a través de APIs internas (order-api, stock-proxy)
  o, en el futuro, de una API de integración con Odoo. Nunca contra la base de
  datos directamente.
- Cada aplicación vive **aislada en su carpeta dentro de `apps/`**, con su
  propio `package.json`, su README y su ciclo de vida.
- El código compartido irá en `packages/` **solo cuando exista una razón real
  para compartirlo**. Hoy no la hay, así que `packages/` no existe todavía
  (ver `docs/architecture.md`).

## Aplicaciones

El repositorio funciona también como **índice de todas las apps internas**,
incluidas las que hoy viven en otros repositorios:

| App | Dónde vive | Estado | Usuarios | Tecnología |
| --- | --- | --- | --- | --- |
| Recepción de Supermercados | `apps/supermercado-recepcion/` (este repo) · [super.plantaspanama.com](https://super.plantaspanama.com) | Desarrollo (prototipo con datos simulados) | Empleados que entregan a supermercados | React 18 + Vite (PWA) |
| Panel de administración | [`viveros-rose-frontend`](https://github.com/VIVERO-comp/viveros-rose-frontend) → ruta `/admin` | Producción | Dueño / administración | Astro + funciones serverless en Vercel |
| Portal del repartidor | [`viveros-rose-frontend`](https://github.com/VIVERO-comp/viveros-rose-frontend) → ruta `/repartidor` | Producción | Repartidores | Astro + funciones serverless en Vercel (PWA instalable) |

- **Recepción de Supermercados** — compara una factura creada en Odoo contra
  las cantidades realmente aceptadas por un supermercado, calcula las
  devoluciones (`devuelto = enviado − aceptado`) y registra intercambios de
  plantas dañadas. Cómo ejecutarla: ver su
  [README](apps/supermercado-recepcion/README.md); en corto,
  `cd apps/supermercado-recepcion && npm install && npm run dev`.
- **Panel de administración** — resumen del negocio, lista de pedidos,
  validación de pagos y consulta de stock. Vive dentro del sitio de la tienda
  porque sus funciones serverless guardan la clave del order-api fuera del
  navegador y reutiliza el catálogo y el layout del sitio.
- **Portal del repartidor** — entregas del día, ganancias, foto de entrega y
  avisos Web Push. Mismo caso que el panel de administración.

El detalle de cada una (pantallas, endpoints, credenciales) está en
[`docs/apps.md`](docs/apps.md). Por qué el panel y el portal viven en el
frontend y qué implicaría separarlos: [`docs/architecture.md`](docs/architecture.md)
y la sección "Migración futura" de `docs/apps.md`.

## Sistemas relacionados (no son apps de este repositorio)

| Sistema | Repositorio | Qué es |
| --- | --- | --- |
| Tienda online plantaspanama.com | `viveros-rose-frontend` | Sitio público en Astro (además aloja `/admin` y `/repartidor`) |
| Order API | `vivero-rose-order-api` | Backend Python (FastAPI) de pagos y pedidos; expone también los endpoints JSON que consumen el panel y el portal |
| Stock proxy | `vivero-rose-stock-proxy` | Backend Python (FastAPI) de disponibilidad de stock, lee la base de Odoo en solo lectura |
| Configuración de Odoo | `viveros-rose-odoo-config` | Personalizaciones sobre Odoo 19 Community |
| Infraestructura | `vivero-rose-infra` | Documentación, nginx, DNS |
| Herramienta de fotos de producto | scripts dentro de `viveros-rose-frontend` | Flujo interno de emparejar y revisar fotos; se queda donde está |

## Crear una nueva aplicación

1. Crear la carpeta con un nombre descriptivo en minúsculas:
   `apps/<nombre-descriptivo>/`.
2. La app debe ser autocontenida: su propio `package.json` (o equivalente),
   sin depender de rutas fuera de su carpeta.
3. Escribir su `README.md` con las secciones: Propósito, Usuarios, Flujo,
   Tecnología, Desarrollo local, Variables de entorno, Integraciones,
   Estado actual y Próximos pasos.
4. Agregarla a la tabla de aplicaciones de este README y describirla en
   `docs/apps.md`.
5. Nunca subir secretos: las claves van en `.env` (ignorado por git) y se
   documentan con un `.env.example` con marcadores.
