# Recepción de Supermercados

App interna con la que la empleada que reparte a supermercados registra
desde el celular lo que pasó con cada entrega. Server-rendered en Python;
el prototipo React que le dio el diseño vive congelado en
[`prototipo-react/`](prototipo-react/).

## Propósito

Los pedidos se crean y confirman **antes** en Odoo. Esta app no crea pedidos
ni maneja inventario: compara lo enviado contra lo que el súper aceptó,
calcula las devoluciones (`devuelto = enviado − aceptado`, la empleada nunca
lo escribe) y registra intercambios de plantas dañadas. La referencia en
pantalla es el número del pedido sin prefijo ("Pedido 00774"); por dentro
viaja completo (S00774) y, si el pedido trae "referencia de cliente" en
Odoo, se muestra debajo como "Ref. súper".

## Usuarios

Empleadas de Vivero Rose con usuario y contraseña propios de la app
(ver Autenticación).

## Flujo

Cuatro pestañas: **Entregas** (pedidos confirmados a sucursales de
supermercado, buscador en vivo, contadores −/+ acotados a `0 ≤ aceptado ≤
enviado`, resumen en B/. y confirmación en dos pasos mostrando solo las
diferencias), **Devolver** (lo rechazado hasta dejarlo en el vivero),
**Cambios** (intercambios: cliente → sucursal con buscador → plantas con
"Disponible: N", 0 avisa en naranja sin bloquear) e **Historial**.

## Tecnología

FastAPI + Jinja2 + SQLite (stdlib), `httpx` para el stock-proxy y ~200
líneas de JS vanilla para contadores y buscadores. Sin build, sin Node.
PWA instalable (manifest; sin service worker todavía).

## Desarrollo local

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.usuarias crear genesis "Génesis"
set -a; source .env; set +a    # opcional: proxy real (ver Variables)
.venv/bin/uvicorn app.main:app --reload --port 8091
.venv/bin/python -m pytest
```

## Variables de entorno

Ver `.env.example`. `STOCK_PROXY_URL` + `STOCK_API_KEY` conectan sucursales,
catálogo y pedidos reales, con degradación en cadena (caché → último valor
bueno → fallback); sin ellas la app arranca con sucursales/catálogo de
prueba y **cero entregas** (no se inventan pedidos). `COOKIE_SEGURA=1` en
producción; `RECEPCION_DB` mueve el SQLite.

## Autenticación

Usuarios y contraseña por empleada, propios de la app: PBKDF2-SHA256 en
SQLite, sesión con cookie HttpOnly (deslizante, 30 días), toda ruta exige
sesión salvo `/login` y `/static`. Altas y mantenimiento por consola:
`python -m app.usuarias crear|clave|desactivar|lista`. Las contraseñas se
entregan en persona y no se apuntan en ningún sistema.

**Opción de fase 2 (decidido el 03/09/2026, no construir todavía):** alta por
enlace con clave de un solo uso, validado contra el contacto de Odoo
SUPERMERCADO ADMIN (ref `SUPER-ADMIN`, id 66). Abraham dio por cubierto el
acceso de Génesis con el usuario/contraseña por consola; el enlace queda
anotado solo como mejora futura.

## Integraciones

Lecturas por el stock-proxy (`/v1/entregas`, `/v1/sucursales`,
`/v1/catalogo`, con X-API-Key). Las escrituras hacia Odoo (recepción,
regreso, intercambios) siguen siendo locales (SQLite) detrás de las
funciones `odoo_*` de `app/datos.py`, a la espera del router `supermercado`
del order-api — ver `docs/odoo-integration.md`.

## Deploy (droplet)

`https://super.plantaspanama.com`: contenedor propio (Dockerfile +
docker-compose.yml de esta carpeta) escuchando solo en `127.0.0.1:8091`,
detrás del nginx del host con Let's Encrypt y `limit_req` en `/login`
(config versionada en `vivero-rose-infra/nginx/super.plantaspanama.conf`).
Le habla al stock-proxy por la red interna de Docker
(`http://stock-proxy-stock-proxy-1:8100/v1`). DNS: registro A `super` →
143.244.167.222 (GoDaddy, cuenta kortostocks).

Despliegue: rsync de esta carpeta a `~/super-recepcion`, llenar `.env`,
`docker compose up -d --build`, activar el server block y
`certbot --nginx -d super.plantaspanama.com`. Empleadas:
`docker compose exec app python -m app.usuarias crear <usuario> "<Nombre>"`.
El SQLite vive en `./datos/recepcion.db` (volumen) y entra al respaldo
diario del droplet. Rollback: `docker compose down`.

## Estado actual

En producción con datos reales de Odoo para lecturas; confirmaciones,
devoluciones e intercambios se guardan localmente (SQLite) hasta que exista
la escritura a Odoo. El catálogo y las sucursales de prueba solo quedan como
fallback de desarrollo.

## Próximos pasos

- Router `supermercado` en el order-api para escribir en Odoo lo confirmado
  (SQLite pasa a ser caché/cola local).
- Agregar Riba Smith a `SUPERMERCADOS_REFS` cuando exista en Odoo.
- Service worker para la PWA.
