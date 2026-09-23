# Plan: colores unificados de chips y etiquetas

Todo el ecosistema (panel /admin, chats, apps internas, Linear, calendarios)
usa las familias de color de Twenty CRM v2.39.5 (crm.plantaspanama.com) como
unica referencia. La fuente de verdad es `paleta.json` (copias identicas en
`viveros-rose-frontend/src/data/` y `viveros-rose-apps/apps/control-stock/app/`);
`scripts/verificar_paleta.py` de viveros-rose-apps comprueba que nada se
desalinee.

## Fases

1. **Fase 1 — Paleta unica compartida.** — HECHA (23/09/2026)
   `paleta.json` con familias exactas de twenty-ui v2.39.5 + asignaciones;
   tokens CSS generados (`src/styles/paleta.css`); `chips-lead.ts` habla en
   familias; mueren las paletas duplicadas de admin/chats y las copias de
   tonos; en las apps nace `app/colores.py` y muere `repintar()`.
2. **Fase 2 — Panel /admin.** — HECHA (23/09/2026)
   Chips de tipo, estado, etapa y motivo con la familia correcta (las
   `asignaciones` de paleta.json); los puntos de columna (CRM, CRM pedidos,
   rail del Equipo, Inactivo/Sin estado) usan el tono fuerte de la MISMA
   familia que el chip pastel.
3. **Fase 3 — Apps internas.** Calendario (los tipos que tienen label en
   Twenty cambian, los tipos internos se quedan como estan), Retail
   (Mayorista pasa a naranja), Vender/Compras/Proyectos (los tipos de
   servicio estrenan su color), calendario con piel Twenty (3 chips
   corregidos).
4. **Fase 4 — Linear.** — HECHA (23/09/2026)
   Las labels del team LEAD llevan el solido_hex de su familia: las 11 de
   tipo repintadas por API (compartian un solo verde) y las etapas
   Cotizado/Facturado/Pagado en azul/morado/verde como en Twenty
   (script vivero-rose-crm/scripts/pintar_labels_linear.py, idempotente).
   El pipeline (tarjeta.ts) y el addon de Odoo ya crean las que falten con
   su color; el addon se despliega en su proxima tanda (solo cambia el
   color de creacion, sin logica).
5. **Fase 5 — Repartidor y Pedidos.** Mismos colores de estado en el panel
   y en la app del repartidor (hoy "En camino" es ambar en uno y verde en
   el otro).

Cada fase se despliega por separado y con el OK de Abraham.
