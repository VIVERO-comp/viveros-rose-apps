# PLAN-ORQUESTA.md

**Proyecto:** Orquesta (inventario.plantaspanama.com) · Vivero Rose / Plantas Panamá
**Versión:** 0.2 · corregida con la auditoría M0 · 1 de octubre de 2026
**Estado:** esperando M0.5. **No se programa nada hasta que Korto apruebe este documento.**

**Cómo leer las marcas**

| Marca | Significa |
|---|---|
| **[KORTO]** | Regla de negocio decidida por Korto |
| **[CÓDIGO]** | Verificado contra el commit 5d244f8 (1/10/2026), el desplegado en producción |
| **[ODOO-STD]** | Así funciona Odoo de fábrica. Falta confirmar en la configuración real |
| **[REC]** | Recomendación técnica, pendiente de aprobación |
| **[PENDIENTE ODOO]** | No lo puedo ver. Lo resuelve el script `verificar_odoo.py` (solo lectura) |
| **[VERIFICADO M0]** | Comprobado en el Odoo real o el código real el 1/10, con la evidencia corta al lado |
| **[PENDIENTE M0.5]** | Lo responde el experimento M0.5 |

---

## 0. Principio

> Si un empleado necesita saber cómo funciona Odoo para completar una tarea, la interfaz está mal diseñada. [KORTO]

- Cada pantalla responde una sola pregunta: **¿qué tiene que hacer esta persona ahora?**
- Una acción principal por pantalla. Las demás acciones van escondidas en "Más opciones".
- Palabras de la pantalla: Cobrar, Programar, Entregar, Recoger, Aceptar, Cambiar fecha, Ver cliente, Ver pedido.
- Palabras que nunca aparecen: pedido de venta, factura borrador, picking, diario, payment state, backorder, conciliar.

---

## 1. Contradicciones detectadas (hay que resolverlas antes de programar)

| # | Choque | Por qué choca | Propuesta |
|---|---|---|---|
| C1 | **D15 "no reservar stock al pagar"** vs **D2 "el pedido nace al confirmar dentro del cobro"** | Confirmar un pedido en Odoo crea la salida de almacén y **reserva el stock** en ese momento. Está medido: `reservation_method = at_confirm` — confirmar SÍ aparta stock. [VERIFICADO M0] | Cambiar el tipo de operación de entrega a **reserva manual**. Al confirmar se crea la salida, pero no aparta nada. El stock se reserva o se descuenta solo al entregar. [REC] El REC queda, pendiente del experimento M0.5-A y decisión de Korto (**P11**). [PENDIENTE M0.5] |
| C2 | **"Autorizar entrega sin pago"** vs **D2 "el pedido solo nace al cobrar"** | Si no se cobra, el pedido no existe en Odoo y no hay salida de almacén que validar. | La autorización de admin ejecuta "confirmar sin cobrar": crea el pedido sin pago y lo marca **Entregado, debe $X**. Es la única forma de tener un pedido sin pago. [REC] |
| C3 | **"Entregar sin pago"** vs **"no manejamos crédito"** | Entregar sin cobrar es crédito, aunque sea excepcional. | Aceptarlo como excepción explícita: solo admin, con motivo, aviso y chip rojo hasta que se cobre. [REC] |
| C4 | **Abono 50%** y **pago parcial**: dos mecanismos para lo mismo | Para el empleado, "abono" y "pagó menos" son la misma situación. En Odoo serían dos cosas distintas: una factura de anticipo, o un pago parcial sobre una factura completa. | **Aprobada con esta forma [KORTO 1/10]:** un solo mecanismo, "Cobrar otro monto", que vive escondido en Más opciones y **NO muestra un 50% pre-escrito** (el monto arranca vacío). Si todavía no hay factura, el monto entra como anticipo. Así existe una sola forma de deber dinero en todo el sistema. Requiere producto de anticipo, que HOY NO está configurado (`sale.default_deposit_product_id = False` [VERIFICADO M0]) → [PENDIENTE M0.5] |
| C5 | Estado financiero "Abono → **Saldo pendiente** → Pagado" | "Abono" y "Saldo pendiente" describen el mismo momento. | Tres estados: **Sin pago · Debe $X · Pagado**, más el rojo **Entregado, debe $X**. [REC] |
| C6 | **Factura final "al pagar el saldo"** + **"se puede entregar con saldo"** | Mientras no se cobra el saldo, Odoo no registra esa deuda como cuenta por cobrar del cliente: la ve como "falta facturar" en el pedido. Los reportes contables de Odoo no la muestran como deuda. | Aceptable. Orquesta muestra la deuda leyendo "por facturar + por cobrar" del pedido. Si quieres que el contador la vea, la factura final tiene que salir al entregar (decisión **P3**). [REC] |
| C7 | **Tarjeta como método principal** | No hay integración de tarjeta para ventas en el vivero o por teléfono. PagueloFacil es solo para la tienda web y está bloqueado (error 615). [CÓDIGO / doc pagos 23/09] Además, **no existe ningún diario de Tarjeta en Odoo**. [VERIFICADO M0] | Tarjeta = registro manual del cobro hecho en el POS del banco, con comprobante. El diario de Tarjeta lo crea Korto SOLO si confirma que hay POS físico del banco (**P5** sigue abierta). |
| C8 | **Efectivo "secundario"** | Hoy Vender solo ofrece **Yappy y Efectivo**. [CÓDIGO venta_cobrar.html:42-52] Los diarios reales de Odoo son **Yappy, Banco General y Efectivo** [VERIFICADO M0]: la transferencia SÍ existe (Banco General). El diario «Ventas Super Extra» sigue activo y queda fuera de Orquesta. | Orden en pantalla: Yappy · Transferencia · Tarjeta; Efectivo dentro de "Más opciones". [REC] |
| C9 | **Alquiler con salida y regreso** | Odoo 19 Community no trae la app de Alquiler: es de la versión de pago. [ODOO-STD] `sale_renting` NO existe en esta instalación y no hay ubicación «Alquiladas» (solo WH/Existencias). [VERIFICADO M0] | El alquiler NO parte de cero: Vender ya tiene el tipo **«Alquiler / Eventos»** (28/09) con selector de cobro total/planta-por-planta y sección de plantas que se llevan y regresan. El REC **SE SUMA** a eso: una ubicación de stock "Alquiladas" con dos movimientos internos (Vivero → "Alquiladas" al llevar, y "Alquiladas" → Vivero al recoger) y dos peticiones; fechas y responsables se guardan en el addon `vivero_rose_pedidos`. [REC] |
| C10 | **Peticiones a varias personas (D9 nuevo)** vs **un solo responsable** | En alquiler hay dos responsables: uno lleva y otro recoge. | Una petición por tarea. Alquiler = dos tareas, Llevar y Recoger, cada una con su propio aceptante. [REC] |
| C11 | **Cambios después del pago "sin bloquear"** | Si baja el monto, hay que devolver dinero o dejarlo a favor. Eso es salida de dinero. | Cualquiera puede **subir** (se cobra la diferencia). Solo admin puede **bajar** (devolver o dejar a favor). [REC] (**P6**) |
| C13 | **Seis pestañas de escritorio** vs **cinco prioridades en el celular** | En el celular piden Pendientes, que no es una pestaña de escritorio. | El celular tiene su propio menú (sección 9). El escritorio mantiene las seis pestañas. [REC] |
| C14 | **Retiro sin día fijo** y **petición obligatoria** | Un retiro no lo lleva nadie: no tiene sentido pedir que alguien acepte "llevarlo". | El retiro no tiene petición. Tiene un responsable de **preparar**, y el cliente avisa cuándo pasa. [REC] |

C12 eliminada: el tablero de Proyectos se borró el 24/09/2026.

---

## 2. Reglas de negocio (versión final propuesta)

### Venta y cobro
1. Sin pago no hay pedido. [KORTO]
2. Lo normal es cobrar el 100%. El abono es excepción, por pedido del cliente o por ser proyecto o servicio. [KORTO]
3. Abono = cualquier monto menor al total, **SIN 50% sugerido en pantalla** (el monto arranca vacío). Entra como anticipo en Odoo. [KORTO 1/10, ver C4] [PENDIENTE M0.5]
4. Yappy y transferencia piden **comprobante obligatorio** (foto). Tarjeta pide número de autorización. Efectivo no pide nada. [KORTO + REC]
5. Pueden registrar pagos: Korto, Rubén y Mary. [KORTO]
6. Cotización sin pago a los **14 días** = **Vencida**. No se borra. Se puede reactivar. Se guarda "¿por qué no compró?". [KORTO 1/10] Vencida es un **atributo de la cotización en Odoo**; el estado del lead sigue siendo el del embudo de Linear (el barrido de 14 días → Perdido, contado desde el último WhatsApp del cliente, ya existe y sigue igual). Mismo reloj, dos cosas distintas.

### Programación y entrega
7. Sin pago o abono no se programa ni se entrega. [KORTO]
8. Excepción: **Autorizar entrega sin pago**, solo admin, con motivo, registrada y avisada. [KORTO]
9. Domicilio: se programa con petición. Lo acepta el primero de los elegidos; el admin puede cambiar el responsable después. [KORTO]
10. Retiro en vivero: cualquier día, sin petición, con responsable de preparar. [KORTO + REC]
11. Días de entrega a domicilio: **[DECISIÓN PENDIENTE de Korto]**. Hoy el checkout web vigente (29/09) funciona así: moto = mismo día con corte 1 p.m. (pagado viernes o sábado → lunes); carro/pickup = la fecha la pone Korto desde /admin. La regla "solo martes y miércoles" no está registrada en ningún documento del proyecto; si Korto la confirma, hay que decidir si aplica también a la tienda web.
12. Entrega parcial permitida. Lo que falta queda como "entrega pendiente" y se programa aparte. Si la falla fue nuestra, ese envío puede ser gratis. [KORTO]
13. Cliente rechaza la entrega: el pedido no desaparece, queda "Por resolver". Puede cobrarse el envío. [KORTO]

### Cambios
14. Se puede cambiar cantidad o precio después de cobrar. Subir = cobrar la diferencia. Bajar = devolver o dejar a favor, solo admin. [KORTO + REC]
15. Todo cambio importante guarda: qué cambió, antes, después, quién, fecha y hora. [KORTO]

### Datos
16. Odoo manda en productos, stock, pedidos, facturas, pagos y entregas. Orquesta relee; nunca guarda un "pagado" propio. [KORTO]
17. SQLite de Orquesta solo guarda: auditoría, peticiones, disponibilidad de días, preferencias, y datos que Odoo no tiene (En camino, quién aceptó). [KORTO]

---

## 3. Estados

Tres ejes que conviven en la misma tarjeta. [KORTO + REC]

| Eje | Estados que ve el empleado | De dónde salen |
|---|---|---|
| **Comercial** | El embudo real de 9 estados de Linear (tabla abajo) — la base a la que el plan se adapta [KORTO 1/10] | Linear: el único tablero del lead |
| **Dinero** | Sin pago · Debe $X · Pagado · 🔴 Entregado, debe $X | Odoo: facturas + lo que falta facturar |
| **Entrega** | Sin programar · Esperando respuesta · Programado · En camino · Parcial · Entregado · Rechazada | Odoo (fecha, salida validada) + Orquesta (aceptación, En camino) |
| **Alquiler** | Por llevar · Con el cliente · Por recoger · Regresó | Movimientos internos de stock + fechas del addon |
| **Cobro técnico** | Cobro incompleto (solo si falló a mitad) | Orquesta: paso alcanzado del motor |

**El eje Comercial es el embudo real de 9 estados de Linear** [KORTO 1/10]:

| Estado | Lo mueve |
|---|---|
| Nuevo | solo, al nacer; nace con «Te toca» |
| Hablando | solo, con NUESTRA primera respuesta |
| Cotizado | solo, al generar la cotización |
| Por agendar | solo, con el pago real en Odoo (vía `lead_ref` + `lead_real` del addon); pone también la etiqueta Abono 50% / Pagado 100% / Cobrar saldo |
| Agendado | solo, al crear la actividad |
| Entregado | 1 toque: «Hecha» en el calendario |
| Ganado | solo: entregado + saldo 0 |
| Recordatorio | a mano, con motivo; exento del barrido |
| Perdido | barrido de 14 días o a mano; revive a Hablando si el cliente escribe |

La escalera no degrada. «Vencida» NO es estado del lead (ver regla 6); «Pedido» se expresa con Por agendar/Agendado.

Traducción a Odoo, que el empleado no ve:

| Orquesta | Odoo |
|---|---|
| Pagado | Todas las facturas en `paid` o `in_payment`, y nada por facturar. `in_payment` cuenta como pagado solo si se confirma la configuración del diario. [PENDIENTE M0.5: experimento B] Dato: hoy solo han existido `paid` y `not_paid`. [VERIFICADO M0] |
| Debe $X | X = por facturar del pedido + saldo de facturas publicadas |
| Sin pago | Cotización sin facturas pagadas |
| Programado | `commitment_date` del pedido + petición aceptada |
| Entregado | «Hecha» en el calendario sigue siendo el gatillo; en M2, al marcar Hecha, Orquesta además valida la salida (`done`) en Odoo para que los dos coincidan. [KORTO 1/10] |

### Etiquetas de tipo: una sola lista [KORTO 1/10]

**Regla:** una sola lista de etiquetas de tipo de servicio/interés, igual en Linear, Odoo (cotizaciones y contactos) y WhatsApp. **Manda la lista de Linear**; Odoo y WhatsApp copian los mismos nombres. **Alquiler = Evento = Boda → una sola etiqueta: Eventos.**

**La lista canónica (grupo Interés de Linear, hoy):** Plantas · Eventos · Paisajismo · Mantenimiento · Mayorista. (Los 11 tipos del negocio se traducen a estos 5 solo para la etiqueta — `LABEL_INTERES`; el vocabulario largo sigue en chips y selectores.)

**Inventario medido el 1/10 [VERIFICADO M0]:**

| Dónde | Etiquetas que existen | Uso |
|---|---|---|
| `crm.tag` (leads y órdenes) | LOCAL · RETAIL VENTA · RENTAL · MANTENIMIENTO · BODA · SERVICIO · PROYECTO | RETAIL VENTA 30 leads · SERVICIO 5 leads · RENTAL 3 leads + 1 orden · LOCAL 5 órdenes · PROYECTO 1 orden · MANTENIMIENTO y BODA 0 |
| `res.partner.category` (contactos) | Retail · Renta/Alquiler · Mantenimiento · Proyecto · Boda · CLIENTE · Tienda web · Repartidor · Admin · + 3 de Super Extra (Supermercado 39, Ventas a Supermercados, Admin supermercado) | Retail 6 · Proyecto 3 · Tienda web 4 · Renta/Alquiler 1 · Repartidor 1 · Admin 1 · resto 0 |
| Linear (equipo LEAD, grupo Interés) | Plantas · Eventos · Paisajismo · Mantenimiento · Mayorista | la lista que manda |
| WhatsApp (familia Interés, verde) | Plantas · Eventos · Paisajismo · Mantenimiento · Mayorista | ya coincide con Linear |

**Quién las crea/pone [VERIFICADO M0, código 5d244f8]:** `cotizaciones.py` define `etiqueta_orden` por tipo de servicio (RENTAL · BODA · EVENTO · MANTENIMIENTO · PAISAJISMO · PROYECTO · INSTALACION) y `_etiquetar_orden()` la escribe en la orden (`:341`), en la oportunidad (`:346`) y la categoría en el contacto (`:330`) — y **si el `crm.tag` no existe, LO CREA (`:335-336`)**: lo contrario de la regla de Linear («las etiquetas nunca se crean solas»). `ventas.py:1132,1230` pone `VENTA_TAG_LOCAL` (LOCAL) a las ventas locales. `colores.py:172-192` pinta los chips.

**Mapeo propuesto viejo → nuevo** (nada se cambia ahora; el cambio va en **M2, primero en pruebas, código y Odoo juntos**):

| Viejo (Odoo) | Nuevo (lista de Linear) |
|---|---|
| RENTAL · BODA · EVENTO · Renta/Alquiler · Boda | **Eventos** |
| RETAIL VENTA · Retail | **Plantas** |
| PAISAJISMO · PROYECTO (tag de interés) | **Paisajismo** |
| MANTENIMIENTO · Mantenimiento | **Mantenimiento** |
| (ninguna hoy) | **Mayorista** |
| SERVICIO (5 leads) | repartir a mano entre los 5 al migrar (son 5 leads) |
| INSTALACION | según el mapeo de `LABEL_INTERES` (leerlo del frontend en M2); sugerencia: Plantas |
| LOCAL · Tienda web | NO son interés: son **canal de venta** — se quedan aparte con ese papel (decisión al margen si se renombran) |
| Proyecto (categoría de contacto) | se queda: es la etiqueta de C12 que agrupa, no un interés |
| CLIENTE (0 usos) | se borra en M2 |
| Supermercado · Ventas a Supermercados · Admin supermercado | salen con el retiro de Super Extra (archivar, no borrar) |
| Repartidor · Admin | son de ACCESO, no de interés: se quedan |

**Regla nueva para M2 [REC]:** Odoo pasa a la misma disciplina de Linear — el código **solo busca** la etiqueta por nombre; si falta, error en el log y se sigue sin ella. Las crea Korto.


---

## 4. Permisos

| Acción | Korto (admin) | Rubén | Mary |
|---|---|---|---|
| Cotizar, editar cotización | ✔ | ✔ | ✔ |
| Cobrar (total, otro monto) | ✔ | ✔ | ✔ |
| Subir cantidad o precio después de cobrar | ✔ | ✔ | ✔ |
| Bajar monto, devolver, dejar a favor | ✔ | — | — |
| Anular un pago | ✔ | — | — |
| Enviar petición | ✔ | ✔ | ✔ |
| Aceptar petición | por cualquiera | las suyas | las suyas |
| Cambiar responsable después de aceptado | ✔ | — | — |
| Cambiar fecha | ✔ | ✔ si es su pedido | ✔ si es su pedido |
| Marcar en camino, entregado, rechazado | todo | lo suyo | lo suyo |
| Autorizar entrega sin pago | ✔ | — | — |
| Ver Registro completo | ✔ | lo suyo | lo suyo |

Odoo no puede aplicar esta tabla: la app entra a Odoo con **un solo usuario**. [CÓDIGO ventas.py:89-98] La aplica Orquesta, y el Registro guarda el nombre real.

En Odoo, Rubén y Mary comparten la cuenta MARY/RUBEN [VERIFICADO M0]: allá son indistinguibles. La tabla la aplica Orquesta y el Registro guarda el nombre real (ya dicho). Rubén es además admin de Control desde el 1/10.

---

## 5. Flujos

### 5.1 Cobro (100%)
```
[COBRAR $850] → elige Yappy / Transferencia / Tarjeta → foto del comprobante
Orquesta, por detrás:
  confirmar pedido → crear factura → publicar → registrar pago → releer Odoo
  ✔ "Pago registrado"        ✖ "No se pudo cobrar: <motivo en palabras simples>" + [Reintentar]
```
- Si falla a mitad: tarjeta en **Cobro incompleto**. Reintentar sigue desde donde quedó y no duplica nada. El mecanismo de pasos con reintento ya existe [VERIFICADO M0: ventas.py:1377-1407, cada paso sellado en SQLite]; el rótulo «Cobro incompleto» es NUEVO — hoy las etiquetas son por paso («Confirmada · factura pendiente», etc.).
- Ya no valida la entrega. [KORTO D1]

### 5.2 Otro monto (abono o pago menor)
```
[COBRAR $1,000] → Más opciones → ☐ Cobrar otro monto → [$ ___ ] (arranca vacío, sin 50% pre-escrito)
Orquesta: confirmar → factura de anticipo $500 → publicar → pago → releer
Tarjeta: "Debe $500"
```
Saldo, en cualquier momento (antes, al entregar o después): `[COBRAR $500]` → factura final ($1,000 − $500 anticipo) → publicar → pago → **Pagado**. [ODOO-STD] [PENDIENTE M0.5: producto de anticipo + comportamiento de la factura final]

### 5.3 Entrega a domicilio
```
Pedido pagado o con abono → [PROGRAMAR] → fecha (días por decidir, ver regla 11) + ☐Korto ☐Rubén ☐Mary → [ENVIAR PETICIÓN]
→ el primero que toca Aceptar (en la app o en el correo) queda como responsable → Programado
→ [SALIR] En camino → [ENTREGAR] → foto opcional → valida la salida en Odoo → Entregado
```

### 5.4 Retiro en vivero
```
Pedido pagado o con abono → Retiro → responsable de preparar → Listo para retirar
→ Mary ve el teléfono y la nota del cliente → cliente llega → [ENTREGAR] → valida la salida → Entregado
```

### 5.5 Entrega parcial
```
[ENTREGAR] → "¿Entregaste todo?" → No → cantidades entregadas (10 → 7)
→ Odoo: valida 7 y deja una salida pendiente con 3
→ tarjeta "Parcial 7/10" + [PROGRAMAR LO PENDIENTE] (☐ envío gratis: fue nuestro error)
```
El tipo de operación tiene `create_backorder = ask` y el asistente existe [VERIFICADO M0]; el detalle de qué pregunta y qué crea queda [PENDIENTE M0.5-C]. Hoy el código siempre entrega todo y nunca ve la pregunta de entrega parcial de Odoo [VERIFICADO M0: ventas.py:1300-1318]. Hay que programarla.

### 5.6 Cliente rechaza
```
En camino → Más opciones → [Cliente rechazó] → motivo + hora (quién = el que entrega)
→ la salida NO se valida, el stock vuelve a quedar disponible → "Rechazada · por resolver"
→ el admin decide: reprogramar · devolver dinero (menos el envío) · dejar a favor
```

### 5.7 Alquiler (Alquiler = Evento = Boda)
```
Cotizar alquiler: cliente, plantas, Desde, Hasta, hora de llevar, hora de recoger (todo obligatorio)
→ Cobrar (100% o abono)
→ dos peticiones: LLEVAR (fecha Desde) y RECOGER (fecha Hasta), cada una con su aceptante
→ LLEVAR hecho: las plantas pasan de Vivero a "Alquiladas" (stock interno, no se venden)
→ RECOGER hecho: vuelven a Vivero; si falta o llega dañada alguna, se anota → cargo extra opcional
```
Requiere en Odoo una ubicación de stock "Alquiladas" y campos de fechas en el addon. [REC] (No existe hoy: solo WH/Existencias [VERIFICADO M0]; ver C9 — Vender ya tiene el tipo «Alquiler / Eventos» y esto se suma.)

### 5.8 El cliente pide más (mismo día o antes de entregar)
Caso real: el cliente ya pagó y pide agregar algo antes de que salga el pedido. [KORTO]

```
Pedido → [+ AGREGAR AL PEDIDO] → busca producto → cantidad → revisa stock
→ "Ahora el pedido es $970. Falta cobrar $120"
→ [COBRAR $120] (o "Cobrar al entregar" si el admin lo permite)
→ misma entrega, misma fecha, mismo responsable; al responsable le llega un aviso "Se agregó: 1 maceta"
```

| Situación | Qué hace Orquesta | Qué pasa en Odoo |
|---|---|---|
| **Aún no se entregó** | Agrega al **mismo pedido** | Línea nueva en el pedido; Odoo la suma a la misma salida de almacén pendiente. Se factura y cobra solo lo agregado; lo ya pagado no se toca. [ODOO-STD] [PENDIENTE M0.5-D: que se sume a la misma salida] |
| **Pedido con abono** | Agrega al mismo pedido | Sube el saldo ("Debe $X" + lo agregado). Se cobra junto con el saldo. |
| **Ya se entregó** | **Venta nueva** del mismo cliente, enlazada al pedido anterior | Pedido nuevo con su propia entrega. Si es el mismo día y el carro no ha vuelto, se ofrece "llevar en la misma ruta". |
| **Ya salió (En camino)** | Pregunta: "¿Lo lleva ahora o en otra entrega?" | Si va ahora: se agrega y se entrega todo junto. Si no: queda como entrega pendiente. |

**Reglas:**
- Agregar lo puede hacer cualquiera (Korto, Rubén, Mary), porque sube el monto. Quitar sigue siendo solo de admin (C11).
- Lo agregado se cobra antes de salir, igual que todo (regla 7). Excepción: la autorización de admin.
- Si lo agregado supera lo que lleva el vehículo (moto hasta 4 plantas, carro hasta 10), avisa: "No cabe en moto. Cambiar a carro +$X". Los topes moto 4 / carro 10 viven en la tabla `limite_vehiculo` (editables sin deploy).
- Caso vivido (Ilayda, 1/10): un cliente Ganado que pide más — hoy la solución vigente es lead nuevo tras la ventana de 2 horas; este flujo convive con esa ventana.
- Queda en el Historial: "Mary agregó 1 maceta ($120) · 14:51".
- El responsable ya aceptó: no se le pide aceptar de nuevo, solo se le avisa. Si cambió el vehículo, sí se le vuelve a pedir.

### 5.9 Cobrar desde Pedidos, entregar desde Vender, y alertas
[KORTO 1/10]

**Las dos acciones están en las dos pestañas.** Una venta es una sola tarjeta. Pedidos muestra el lado de la entrega y Vender el lado del dinero, pero desde cualquiera de las dos se puede:
- **COBRAR** (en Pedidos, si la tarjeta dice Debe o Sin pago)
- **ENTREGAR** (en Vender, si está pagada o abonada y no entregada)

Las dos usan el mismo motor, así que da igual dónde se toque: el resultado en Odoo y en las dos pestañas es el mismo.

**Alertas** (chip rojo o dorado en la tarjeta + aviso en "Hoy" al admin):

| Alerta | Cuándo | Acción que ofrece |
|---|---|---|
| 🔴 Entregado, debe $X | Se entregó y queda saldo | COBRAR $X |
| 🟡 Pagado sin programar | Pagado hace 2 días o más y sin fecha | PROGRAMAR |
| 🟡 Programado sin cobrar | Fecha en 1 día o menos y sin pago (no debería pasar) | COBRAR · avisar al admin |
| 🟡 Atrasado | Pasó la fecha programada y no se marcó entregado | ENTREGAR o CAMBIAR FECHA |
| ⚠️ Odoo dice otra cosa | Al releer, Odoo no coincide con lo último que hizo Orquesta (por ejemplo, alguien cobró o anuló directo en Odoo) | VER DIFERENCIA (solo admin) |
| ⚠️ Cobro incompleto | El cobro se cortó a mitad | REINTENTAR |
| ↩️ Alquiler sin regresar | Pasó la fecha de recoger y las plantas no volvieron | RECOGER |

Cada alerta desaparece sola cuando Odoo muestra que se resolvió. No hay botón de "descartar alerta".

### 5.10 Cancelación
- **Sin pago:** se cancela la cotización → Perdido con motivo.
- **Con pago:** solo admin. Opciones: devolver · dejar a favor · retener (necesita política escrita). En todos los casos: nota de crédito en Odoo, salida cancelada, calendario limpio, Linear en Perdido. [REC] (**P1**)

### 5.11 Entrega sin pago (excepción)
```
Admin → Más opciones → ⚠️ Autorizar entrega sin pago → motivo → [CONFIRMAR]
→ Orquesta crea el pedido sin cobro y queda en el Registro (quién, cuándo, motivo, pedido, cliente)
→ chip rojo "Entregado, debe $X" en Vender y Pedidos + aviso diario al admin hasta que se cobre
```

### 5.12 Cambios después de cobrar
- **Sube** (5 → 6 plantas): Odoo calcula la diferencia → `[COBRAR $20]`.
- **Baja** (5 → 4): solo admin → nota de crédito → devolver o dejar a favor.
- **Precio:** si solo hay anticipo, cambia la factura final. Si hay factura completa, nota de crédito o cobro de la diferencia. Nunca se edita una factura publicada. [ODOO-STD]
- Si su Odoo bloquea editar pedidos confirmados, esto no funciona. [PENDIENTE ODOO]

---

## 6. Auditoría (Registro)

Cada cambio importante guarda una fila:

| Campo | Ejemplo |
|---|---|
| Qué | Fecha de entrega |
| Antes / Después | 15/10/2026 → 17/10/2026 |
| Quién | Mary |
| Cuándo | 01/10/2026 14:32 |
| Pedido, cliente | S00081 · María G. |
| Respuesta de Odoo | OK / error en palabras simples |

Se registra: fecha, responsable, cantidades, precio, entrega, pago, estado, asignaciones, autorizaciones, rechazos.
Se ve en dos lugares: dentro de cada pedido ("Historial") y en el menú de admin (todo, con filtros).

---

## 7. Odoo como fuente de verdad

| Dato | Manda | Orquesta |
|---|---|---|
| Cliente, producto, precio, stock | Odoo | Lee |
| Pedido, factura, pago, entrega | Odoo | Escribe por el motor y relee |
| Fecha de entrega | Odoo (`commitment_date`) | Escribe al aceptar |
| Responsable | Orquesta decide | Escribe en la salida (`user_id`) y en Linear |
| Etapa comercial temprana | Linear | Lee y escribe |
| Aceptación, En camino | SQLite | Propio |
| Auditoría | SQLite | Propio |

**Regla de oro:** si Orquesta y Odoo dicen cosas distintas, manda Odoo y Orquesta relee. Puntos de riesgo encontrados:

- **Ya existe una copia del estado de venta en SQLite** (`ventas_locales.estado`). [CÓDIGO ventas.py:38] Solo puede usarse para saber dónde reintentar, nunca para mostrar.
- Hay dos tableros: Linear (el único del lead) y el Flujo del CRM de Odoo, que sigue vivo como registro manual de Korto (hoy: Cotizado 6 · Facturado 0 · Abono 1 · Pagado 4 leads [VERIFICADO M0]); qué pasa con él tras migrar es P13. Retail y Proyectos se borraron el 24/09.
- La paleta: un estado nuevo sin su entrada en `paleta.json` revienta el import de `linear_leads` y tumba la app entera. Cualquier estado nuevo de M2 entra primero a la paleta.
- El caché del stock-proxy puede atrasar la disponibilidad 1 a 2 minutos.
- Pagos hechos directo en Odoo no tienen comprobante ni persona: Orquesta los muestra como "registrado en Odoo".

---

## 8. Arquitectura

```
            Pantallas (celular y escritorio)
                         │
       ┌─────────────────┼──────────────────┐
  Motor de cobro    Motor de entrega   Motor de agenda
  (cotizar, cobrar,  (entregar, parcial, (petición, aceptar,
  otro monto, saldo, rechazo, alquiler)  fecha, calendario)
  devolver)
       └─────────────────┼──────────────────┘
               Puerta única a Odoo (XML-RPC)   +   Registro (SQLite)
                         │
         Odoo · Linear · Google (espejo) · Resend (correos)
```

- **Un solo motor de cobro** para venta rápida, CRM, servicios y alquiler. Hoy existe dentro de `ventas.py` y se extrae. [CÓDIGO + KORTO]
- **Venta rápida** = cotizar + cobrar + entregar, seguidos.
- Las cotizaciones de servicio dejan de tener su tabla y su camino aparte (`cotizaciones_servicio`) y usan el motor. [CÓDIGO]
- **Se reutiliza:** la conexión a Odoo, los pasos con reintento seguro, confirmar, facturar (reusa la factura si ya existe), pagar (no paga dos veces), el espejo en Linear y Twenty, los cargos de envío e instalación, la búsqueda de cliente por teléfono y el vencimiento con `validity_date`. [CÓDIGO]
- **Se elimina:**
  - validar la entrega dentro del cobro [CÓDIGO];
  - el chat completo dentro de la tarjeta → en M2, cuando exista CRM → Todos; el autor de cada mensaje se sigue guardando en Twenty [KORTO 1/10];
  - la pestaña Control tal como está → se absorbe en CRM → Todos en M2; la vista por empleado que vuelve el 5/10 sigue, y en M2 pasa a ser "Agrupar por persona" [KORTO 1/10];
  - la regla "Pagado no se pone solo" → se refiere al Flujo de Odoo: el embudo de Linear YA se mueve solo con el pago desde el 28/09 [VERIFICADO M0].
  - (El kanban Retail y el tablero de Proyectos ya se borraron el 24/09: no queda nada que eliminar ahí.)

---

## 9. UX

### Patrones (de las referencias)
- **POS:** carrito a la izquierda, total grande, **un solo botón de cobrar con el monto escrito**, métodos como botones grandes. Referencias: [Open Source POS](https://github.com/opensourcepos/opensourcepos), [InfoShop](https://github.com/NifrasUsanar/InfoShop), [tableros POS UI en Pinterest](https://www.pinterest.com/sophapum/pos-ui/).
- **CRM kanban:** tarjetas con nombre, monto y como mucho dos chips; el detalle se abre en un panel lateral. Referencia: [Frappe CRM](https://github.com/frappe/crm), que usa el mismo patrón que Twenty, el CRM que ya tienen.
- **Entregas:** lista del día en orden, un botón grande por parada (Salir → Entregar), foto al final. Referencias: [Delivery Management System](https://github.com/maruffahmed/Delivery-management-system) y el tema [driver-app](https://github.com/topics/driver-app).

No pude ver las imágenes de Pinterest, solo los títulos de los tableros. Los patrones vienen de los proyectos de GitHub y de diseño de POS conocido, no de imágenes copiadas.

### Reglas de interfaz
1. El botón principal dice la acción **y el monto o la fecha**: "COBRAR $850", "PROGRAMAR MAR 13".
2. Chips por color: verde Pagado, dorado Debe, rojo Entregado-debe o Rechazada, azul Programado, gris Sin programar.
3. "Más opciones" esconde: otro monto, efectivo, entrega sin pago, cambiar responsable, cliente rechazó.
4. Formularios de 3 campos o menos por pantalla. Si hacen falta más, se dividen en pasos.
5. Errores en palabras simples, con qué hacer: "No hay stock de Monstera. Quedan 2. Cambia la cantidad o espera."
6. Toda pantalla nueva de Orquesta usa el estilo vigente de Control: fondo blanco, piel Origin blanco y negro, UN solo botón negro (la acción primaria) por pantalla [KORTO 1/10] — esto corrige también la referencia del PROMPT-M1 a «azul noche, dorado, tarjetas crema».

### Celular (prioridad, una mano, menú abajo)
| Pestaña | Qué muestra |
|---|---|
| **Hoy** | Lo que me toca: peticiones por aceptar, entregas de hoy, cobros pendientes |
| **Pedidos** | Lista con chips; filtro Mío / Todos |
| **Cobrar** | Vender estilo POS + cobros pendientes |
| **Calendario** | Agenda por día |
| **Clientes** | Buscador por nombre o teléfono |

### Pedidos: dos vistas
- **Tablero:** Sin programar · Programado · En camino · Entregado. Se abre primero en el escritorio.
- **Agenda:** lista agrupada por día (Hoy, Mañana, Martes 6…, Sin fecha). Cada fila tiene hora, cliente, qué lleva, vehículo, responsable, chip de dinero y un solo botón con el paso que toca. Íconos: 🚚 domicilio, 🏠 retiro, ↩️ recoger alquiler. Filtros Mío / Todos y Hoy / Semana. Se abre primero en el celular.
- Calendario es distinto: muestra todo el negocio por mes (entregas, conteos, compras, tareas, disponibilidad).

Esta sección de Pedidos (dos vistas) es la versión que se construye; la pestaña de tiquetes borrada el 1/10 no se resucita. [KORTO 1/10]

### Vender: tablero de dinero
Cotizada · Debe · Pagado · ⚠️ Entregado, debe. "Facturado sin pago" solo aparece en ventas viejas, en la revisión de migración.

### Escritorio
Calendario · Stock · CRM (Mío / Todos) · Pedidos · Vender · Compras. Registro va en el menú de admin.

### Pantallas a diseñar (wireframes en el artefacto, pestaña ORQ-W)
W1 Hoy · W2 Tarjeta de pedido · W11 Agregar al pedido · W3 Cobrar · W4 Otro monto · W5 Programar y petición · W6 Aceptar (app y correo) · W7 Entregar y entrega parcial · W8 Alquiler · W9 Autorizar entrega sin pago · W10 Historial del pedido.

### Componentes
Tarjeta (nombre, número, monto, hasta dos chips, iniciales) · Chip de estado · Botón principal con monto · Hoja "Más opciones" · Selector de personas · Selector de fecha que solo habilita días válidos · Cámara o subida de comprobante · Paso de confirmación ("¿Seguro? Esto devuelve $100") · Línea de historial.

---

## 10. Migración: ventas que ya existen

> **Primero reconciliar. Después automatizar.** [KORTO]

Orquesta no empieza vacía. Antes de activar cualquier botón que escriba en Odoo, lee lo que existe y lo clasifica. No crea, no cobra y no entrega nada histórico por su cuenta.

**El universo medido el 1/10 [VERIFICADO M0]:** 21 cotizaciones draft · 3 confirmadas (una es S00084 "Prueba Flujo OST", dato de prueba que Korto va a cancelar) · 3 canceladas · 30 facturas publicadas (29 Super Extra impagas + 1 nuestra pagada, $158.75) · 1 pago en account.payment · salidas: 1 reservada, 11 hechas, 7 canceladas · SQLite: ventas_locales 4, cotizaciones_servicio 21, venta_borrador 3.

### 10.1 Dónde está hoy la información de "Pedido · Abono · Pagado"

| Fuente | Qué dice | ¿Es verdad? |
|---|---|---|
| **Facturas y pagos en Odoo** | Cuánto se facturó y cuánto se pagó de verdad | **Sí, manda** |
| **Etapas del Flujo en el CRM de Odoo** (Cotizado · Facturado · Abono · Pagado) | El código mueve solo hasta Facturado; Abono y Pagado los mueves tú a mano [CÓDIGO ventas.py:1411-1431] | Información externa: sirve para comparar |
| **Ventas de la pestaña Vender** (tabla `ventas_locales` en SQLite) | Hasta qué paso llegó cada cobro de la app | Información externa |
| **Etiquetas en Linear** | Las reales son **Abono 50% · Pagado 100% · Cobrar saldo** (automáticas desde Odoo); "Facturado" no existe y "Pedido completado" es etiqueta de WhatsApp, no de Linear [VERIFICADO M0/bitácora 24-28/09] | Información externa |
| **Lo que sabes tú** | Pagos por Yappy o transferencia que nunca se registraron | Se carga a mano en la revisión |

La migración compara la primera fila con las demás. Si no coinciden: ⚠️ **Diferencia detectada**.

### 10.2 Clasificación automática (solo lectura)

**Alcance: el diario "Ventas Super Extra" queda FUERA de la clasificación** (29 facturas impagas por $3,109.85, de la relación cortada el 18/09): se listan aparte como "fuera de alcance", no se clasifican ni se tocan [KORTO 1/10].

| Clase | Cómo la reconoce | Se muestra |
|---|---|---|
| A · Cotizada | Cotización sin confirmar, menos de 14 días | 🟡 Cotizada |
| B · Vencida | Cotización sin pago, 14 días o más | ⚪ Vencida |
| C · Debe | Facturas con pago parcial, o anticipo pagado y algo por facturar | 🟡 Debe $X |
| D · Pagada | Todo facturado y pagado, entrega no validada | 🟢 Pagado · ⚠️ Entrega por confirmar |
| E · Pagada y entregada | Todo pagado y salida de almacén validada | 🟢 Pagado · Entregado (sin botones) |
| F · Pago fuera de Odoo | Odoo dice $0 pagado, pero el Flujo dice Abono o Pagado, o Vender dice "pagado" | ⚠️ Pago pendiente de registrar |
| G · Entrega sin confirmar | Pagada, salida no validada, pero el Flujo o Linear dicen entregado | ⚠️ Entrega pendiente de confirmar |
| H · Revisar | Cualquier otra combinación, o datos que chocan | ⚠️ Revisar |

Regla: **si no se puede determinar, H.** Nunca se inventa el estado.

### 10.3 Pantalla "Ventas a revisar" (solo admin)
```
VENTAS A REVISAR
🔴 3 con diferencias   🟡 5 pagos fuera del sistema   🟡 2 entregas por confirmar
🟢 18 pagadas          🟢 14 entregadas
[ REVISAR ]
```
Al abrir una venta: lo que dice Odoo, al lado lo que dicen las otras fuentes, y tres botones:
- **Registrar pago en Odoo**: pide monto, método, fecha real y comprobante si lo hay. Usa el mismo motor de cobro, pero con la **fecha real** y marcado como "Regularización". No es un cobro de hoy.
- **Confirmar entrega**: valida la salida con la fecha real.
- **Dejar como está**: guarda una nota.

### 10.4 Antes de crear cualquier cosa: buscar
Cliente (teléfono) + pedido + factura + pago + referencia. **Si ya existe, no se crea otro.** Esto vale para la migración y también para siempre: el motor de cobro ya reutiliza una factura existente y no paga dos veces [CÓDIGO ventas.py:785, 822].

### 10.5 Registro de migración
Cada regularización guarda: cliente, pedido, estado anterior, acción, monto, quién, fecha real del pago y fecha en que se regularizó.

### 10.6 Después de la puesta en marcha
Todas las ventas nuevas pasan por Orquesta. Las etapas manuales Abono y Pagado del Flujo se dejan de usar: Orquesta las calcula de las facturas. El Flujo queda solo de lectura, o se apaga (decisión **P13**).

### 10.7 Pruebas obligatorias antes de producción (en odoo-pruebas)
1. Cotización nueva → 100% → entrega.
2. Cotización nueva → abono → entrega → saldo → pago.
3. Venta existente pagada y entregada → no aparece ningún botón.
4. Venta existente con abono fuera del sistema y ya entregada → regularizar.
5. Factura existente con pago fuera de Odoo → regularizar.
6. Venta existente con pago desconocido → Revisar.
7. Venta existente entregada → no se puede programar otra vez.
8. Cliente pide más antes de la entrega → se cobra solo la diferencia.

---

## 11. Preguntas pendientes para Korto

| # | Pregunta | Por qué importa |
|---|---|---|
| P1 | Cliente cancela después de pagar: ¿devolver, dejar a favor o retener? | Define notas de crédito y reembolsos |
| P2 | **Respondida [KORTO 1/10]:** sí al mecanismo único "Cobrar otro monto" (C4), con esta forma: escondido en Más opciones y sin 50% pre-escrito (el monto arranca vacío) | Simplifica todo el sistema |
| P3 | Factura del saldo: ¿al cobrarlo (tu decisión) aunque el contador no vea la deuda en Odoo hasta entonces? ¿O al entregar? | Contabilidad (C6) |
| P4 | Días de entrega a domicilio: **[DECISIÓN PENDIENTE]** (ver regla 11) | Selector de fechas |
| P5 | Tarjeta: ¿tienen POS físico del banco? ¿Qué se registra como comprobante? | Método de pago (C7) |
| P6 | ¿Rubén y Mary pueden **bajar** montos y devolver dinero, o solo tú? | Permisos (C11) |
| P8 | Alquiler: si una planta vuelve dañada o no vuelve, ¿se cobra? ¿Cuánto? | Flujo 5.7 |
| P9 | Cliente rechaza: ¿el envío se cobra siempre o lo decides caso a caso? | Flujo 5.6 |
| P10 | ¿Quién recibe el aviso diario de "Entregado, debe"? ¿Solo tú? | Notificaciones |
| P12 | Cliente pide más con el pedido ya en camino: ¿se puede agregar en la ruta, o siempre va en otra entrega? | Flujo 5.8 |
| P13 | Flujo del CRM de Odoo después de migrar: ¿solo lectura o se apaga? | 10.6 |
| P14 | Pagos fuera del sistema: ¿tienes registro propio (capturas, estado de cuenta) para comparar? | 10.1 |
| P11 | Reserva manual de stock en Odoo (C1): se prueba en M0.5-A y Korto decide con los datos. Afecta también a la tienda web: lo cobrado no se aparta. | Stock |

P7 eliminada (el tablero de Proyectos se borró el 24/09/2026; ver C12).

**Ya verificado en Odoo el 1/10 [VERIFICADO M0]:** versión 19.0-20260723 · diarios (Yappy, Banco General, Efectivo; «Ventas Super Extra» activo y fuera de alcance; sin Tarjeta) · política de facturación MIXTA: 100 productos por entregado / 16 por pedido / 14 servicios por pedido · sin producto de anticipo (`sale.default_deposit_product_id = False`) · reserva `at_confirm` · backorder `ask` · `ship_only` · campo `locked` con 0 pedidos bloqueados · módulos instalados · ubicaciones de stock · usuarios activos.

**Sigue pendiente de verificar:** grupo de auto-bloqueo de pedidos confirmados (ilegible por XML-RPC) · comportamiento de `in_payment` · producto de anticipo (comportamiento al configurarlo) · que una línea agregada se sume a la misma salida.

---

## 12. Orden propuesto

**M0 → corregir PLAN → M1 → revisar → M2 en odoo-pruebas → pruebas completas → migración real → producción**

| Fase | Qué | ¿Escribe en Odoo? |
|---|---|---|
| **M0** | **HECHO (1/10/2026; informe A–J entregado en el chat)** | No |
| **M0.5** | Prueba real en **odoo-pruebas**: pedido $1,000 → anticipo $500 → entrega → saldo; y confirmar un pedido sin que aparte stock. Solo con tu OK | Solo en pruebas |
| **M1** | Clasificación de ventas existentes + pantalla "Ventas a revisar" + consultar stock, clientes y pedidos | No |
| **M2** | Motor de cobro, entrega, programar, regularizar. Primero en odoo-pruebas | Pruebas, después producción |
| **Migración** | Regularizar las ventas históricas una por una | Sí, con tu OK |
| **Producción** | Primero lectura (M1); cobro y entrega solo después de pasar las 8 pruebas | Sí |

Antes de producción tienen que estar definidas: cancelación después de pagar, devolución, rechazo, planta dañada en alquiler, entrega sin pago, quién baja montos, tarjeta, pagos fuera de Odoo, agregar productos a un pedido que ya salió.

**Tareas programadas de Odoo a resolver antes de M2** (medidas el 1/10 [VERIFICADO M0]; nada se toca sin decisión de Korto):

1. **«Send invoices automatically»** (activa, diaria, `_cron_account_move_send()`): solo procesa las facturas que alguien dejó EN COLA con «Enviar e imprimir» — hoy la cola está en 0, así que con la configuración actual **no manda nada a nadie**. Propuesta: no hace falta apagarla; si Korto quiere cero riesgo de un envío accidental, se apaga sin perder nada. [KORTO decide]
2. **«Vivero: desactivar leads inactivos en Nuevo»** (activa, diaria 07:00 UTC, del addon): es el barrido viejo, neutralizado por `vivero.barrido_leads_activo = 0` pero todavía programado. Propuesta: **apagarla** (`active = False`), la reemplaza el barrido de 14 días de Linear. [KORTO decide]
3. **«CRM: enrich leads (IAP)»** (activa, cada 24 h): enriquece leads con el servicio de pago IAP de Odoo — nadie lo pidió. Propuesta: **apagarla**. [KORTO decide]


**Fin del documento. Esperando M0.5.**
