# Arquitectura

## Principio central

**Odoo es la fuente principal de datos operativos** (catálogo, precios,
stock, pedidos, facturas). Las aplicaciones internas son **clientes
especializados**, no sistemas paralelos de inventario: cada una resuelve una
tarea concreta para un tipo de usuario y devuelve sus resultados a Odoo a
través de APIs.

```text
                ┌───────────────┐
                │     ODOO      │
                │ fuente central│
                └───────┬───────┘
                        │
                     API (order-api, stock-proxy,
                        │  integración futura)
            ┌───────────┼───────────────┐
            │           │               │
            ▼           ▼               ▼
   App Supermercados  Panel /admin   Portal /repartidor
            │           │               │
            ▼           ▼               ▼
        Empleados     Dueño         Repartidores
```

Ninguna app habla con la base de datos de Odoo directamente. Los caminos
existentes son:

- **order-api** (`vivero-rose-order-api`, FastAPI): pedidos, pagos, y los
  endpoints JSON de `/api/admin/*` y `/api/repartidor/*`. Valida las claves
  de admin y repartidor contra Odoo en cada petición y ejecuta acciones como
  marcar un pedido pagado.
- **stock-proxy** (`vivero-rose-stock-proxy`, FastAPI): disponibilidad de
  stock, leyendo la base de Odoo con un usuario de solo lectura.
- **Integración futura** para la app de supermercados: hoy no existe; su
  diseño propuesto está en [`odoo-integration.md`](odoo-integration.md).

## Por qué las apps internas viven hoy en dos repositorios

| Repo | Apps internas | Motivo |
| --- | --- | --- |
| `viveros-rose-apps` (este) | Recepción de Supermercados | Repo oficial para apps internas nuevas; cada app aislada en `apps/` |
| `viveros-rose-frontend` | Panel `/admin` y portal `/repartidor` | Nacieron como páginas del sitio de la tienda y dependen de su despliegue |

El panel y el portal **no** se copiaron a este repositorio porque están
integrados al proyecto Astro del sitio de tres maneras que un copy no puede
preservar:

1. **Funciones serverless mismo-origen**: sus datos llegan por rutas
   `src/pages/api/*` desplegadas como funciones de Vercel del mismo proyecto,
   que guardan `ORDER_API_KEY` fuera del navegador. Separarlas exigiría otro
   proyecto Vercel, otro dominio y CORS en el order-api.
2. **Catálogo compartido**: usan `src/data/products.ts`, el catálogo que se
   regenera desde Odoo. Duplicarlo en otro repo crearía una segunda copia que
   podría desincronizarse (regla del proyecto: el catálogo se regenera, no se
   edita a mano).
3. **Layout y estilos compartidos**: ambas usan el `Base.astro` del sitio
   (modo `chrome="bare"`, noindex) y sus manifests/service worker viven en el
   `public/` del sitio.

La decisión (sept. 2026) fue dejarlas donde funcionan y documentarlas aquí
como apps internas de pleno derecho — este repo es el **índice** de todas.
Qué implicaría separarlas algún día: sección "Migración futura de
admin/repartidor" en [`apps.md`](apps.md).

## Estructura de este repositorio

```text
viveros-rose-apps/
├── README.md            índice de apps internas + convenciones
├── apps/
│   └── supermercado-recepcion/   React + Vite, autocontenida
└── docs/
    ├── architecture.md  este documento
    ├── apps.md          detalle de cada app
    └── odoo-integration.md   diseño propuesto de la integración
```

`packages/` (posibles `ui/`, `odoo-client/`, `shared/`) **no existe todavía**:
con una sola app en el repo no hay código compartido real y no se crean
carpetas vacías por arquitectura. Se creará el día que dos apps necesiten el
mismo código. Tampoco hay workspace de monorepo (pnpm/Turborepo/Nx): cada app
con su propio `package.json` es suficiente. Simplicidad antes que
arquitectura innecesaria.
