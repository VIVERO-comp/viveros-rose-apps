# Viveros Rose Apps

Aplicaciones internas de **Vivero Rose** para operaciones y empleados,
server-rendered en Python (FastAPI + Jinja2), desplegadas en el droplet
detrás de nginx.

## Principios

- **Odoo es el sistema central** de operaciones e inventario. Las apps de este
  repositorio no lo reemplazan ni duplican su lógica: son interfaces
  especializadas para tareas concretas (por ejemplo, agendar y operar el
  calendario del equipo, o crear una venta local desde el vivero).
- Las apps consumen datos a través de APIs internas (order-api, stock-proxy).
  La excepción aprobada es Control de Stock, que además habla XML-RPC directo
  con Odoo para Vender y las fichas (ver su README).
- Cada aplicación vive **aislada en su carpeta dentro de `apps/`**, con su
  propio `pyproject.toml`, su README y su ciclo de vida. Son Python puro:
  sin Node ni build (el único JS es el mínimo de navegador ya servido).
- El código compartido irá en `packages/` **solo cuando exista una razón real
  para compartirlo**. Hoy no la hay, así que `packages/` no existe todavía.

## Aplicaciones

El repositorio funciona también como **índice de todas las apps internas**,
incluidas las que hoy viven en otros repositorios:

| App | Dónde vive | Estado | Usuarios | Tecnología |
| --- | --- | --- | --- | --- |
| Control de Stock (Control Viverorose) | `apps/control-stock/` (este repo) · [inventario.plantaspanama.com](https://inventario.plantaspanama.com) | **Producción** | Todo el equipo del vivero | Python (FastAPI + Jinja2) |
| Recepción de Supermercados | `apps/supermercado-recepcion/` (este repo) · super.plantaspanama.com | **En retiro** — el negocio cortó con Super Extra el 18/09/2026; se conserva por las facturas ya emitidas | (histórico) | Python (FastAPI + Jinja2), PWA |
| Panel de administración | [`viveros-rose-frontend`](https://github.com/VIVERO-comp/viveros-rose-frontend) → ruta `/admin` | Producción | Dueño / administración | Astro + funciones serverless en Vercel |
| Portal del repartidor | [`viveros-rose-frontend`](https://github.com/VIVERO-comp/viveros-rose-frontend) → ruta `/repartidor` | Producción | Repartidores | Astro + funciones serverless en Vercel (PWA instalable) |

- **Control de Stock** — es la app grande del equipo. Sus pestañas (Calendario
  de primero; sin "Inicio"): **Calendario** (espejo del proyecto CALENDARIO
  ROSE de Linear, con sync a Google Calendar y feed ICS), **Stock** (score,
  alertas, ajustes, publicación en la tienda), **Vender** (ventas locales y
  cotizaciones de servicio contra Odoo), **Retail** (kanban de leads),
  **Fichas** y **Ajustes** (login con Google e invitaciones). Proyectos y
  Compras/Gastos se retiraron el 24/09/2026, con el rediseno del CRM. Además sirve la **cara CRM**: `/crm/calendario`, el
  mismo calendario con piel de Twenty que se ve como pestaña dentro del
  Twenty real (`crm.plantaspanama.com`). Ver su
  [README](apps/control-stock/README.md).
- **Recepción de Supermercados** — comparaba el pedido confirmado en Odoo
  contra lo aceptado por el supermercado (devoluciones e intercambios). En
  retiro tras el corte con Super Extra; no desarrollar nada nuevo aquí. Ver su
  [README](apps/supermercado-recepcion/README.md).
- **Panel de administración** — resumen del negocio, pedidos, validación de
  pagos y tableros CRM. Vive dentro del sitio de la tienda porque sus
  funciones serverless guardan la clave del order-api fuera del navegador.
- **Portal del repartidor** — entregas del día, ganancias, foto de entrega y
  avisos Web Push. Mismo caso que el panel de administración.

El detalle histórico de cada una está en [`docs/apps.md`](docs/apps.md)
(ojo: ese doc y `docs/architecture.md` describen una etapa anterior del repo;
la referencia vigente es el README de cada app).

## Sistemas relacionados (no son apps de este repositorio)

| Sistema | Repositorio | Qué es |
| --- | --- | --- |
| Tienda online plantaspanama.com | `viveros-rose-frontend` | Sitio público en Astro (además aloja `/admin` y `/repartidor`) |
| Order API | `vivero-rose-order-api` | Backend Python (FastAPI) de pagos y pedidos; expone también los endpoints JSON que consumen el panel y el portal |
| Stock proxy | `vivero-rose-stock-proxy` | Backend Python (FastAPI) de disponibilidad de stock, lee la base de Odoo en solo lectura |
| CRM (Twenty + OpenWA) | `viveros-rose-crm` | Twenty self-hosted en el droplet crm-twenty, con las pestañas inyectadas Chats y Calendario |
| Configuración de Odoo | `viveros-rose-odoo-config` | Addon `vivero_rose_pedidos` y scripts sobre Odoo 19 Community |
| Infraestructura | `vivero-rose-infra` | Documentación, nginx, DNS, respaldos |
| Herramienta de fotos de producto | scripts dentro de `viveros-rose-frontend` | Flujo interno de emparejar y revisar fotos; se queda donde está |

## Crear una nueva aplicación

1. Crear la carpeta con un nombre descriptivo en minúsculas:
   `apps/<nombre-descriptivo>/`.
2. La app debe ser autocontenida: su propio `pyproject.toml`, sin depender de
   rutas fuera de su carpeta. Todo en Python (FastAPI + Jinja2, server-rendered).
3. Escribir su `README.md` con las secciones: Propósito, Usuarios, Flujo,
   Tecnología, Desarrollo local, Variables de entorno, Integraciones,
   Despliegue, Estado actual y Próximos pasos.
4. Agregarla a la tabla de aplicaciones de este README.
5. Nunca subir secretos: las claves van en `.env` (ignorado por git) y se
   documentan con un `.env.example` con marcadores `CLAVE_N`.
