# WAHA — el puente de etiquetas de WhatsApp (Fase W)

**Qué es.** Un segundo puente de WhatsApp, aparte de OpenWA, para lo que
OpenWA no sabe hacer: **crear y poner etiquetas**. OpenWA corre con motor
`baileys` y responde `501 getLabels`. Este usa **GOWS** (whatsmeow, en Go),
que sí las maneja y no necesita un Chromium adentro.

**Al número del NEGOCIO, no al personal.** Desde el 25/09/2026 la sesión
`vivero` está vinculada a **+507 6099-1459** (el mismo de OpenWA, «Plantas
Panama»). El personal de Abraham (+507 6567-3062) **ya no está vinculado** a
ninguna sesión. (Hasta el 24/09 era el personal; si leés una versión vieja de
este archivo que diga eso, está desactualizada.)

**Convive con OpenWA y no lo estorba.** Son dos dispositivos vinculados a la
misma cuenta de WhatsApp, que admite varios. Verificado el 25/09: con WAHA
andando, OpenWA siguió recibiendo sin perder un mensaje. **OpenWA no se toca
desde aquí** — lo lleva otra sesión.

## El almacén: por qué hay un cron que lo limpia

Al vincular, WhatsApp ofrece bajar la conversación vieja, y eso llena el
disco sin darnos nada: el sincronizador **no lee mensajes**, solo pone
etiquetas y pregunta si un número existe.

Dos cosas crecen, y el `limpiar_almacen.sh` (cron, cada hora al minuto 17)
se encarga de las dos:

1. **`gows_messages`** — los mensajes. Se borran los de más de un día.
2. **`gows.db-wal`** — el log de SQLite. Si nadie lo consolida llega a 60 MB
   *sin que haya datos nuevos*. El 25/09 el almacén marcaba 71 MB y **61 eran
   puro WAL**: un cierre limpio lo bajó solo a 9 MB. El cron le hace
   `wal_checkpoint(TRUNCATE)`.

**Lo sano es ~2 MB.** El diario queda en `~/waha/almacen.log`, y si pasa de
20 MB lo dice ahí en voz alta.

**Los seis límites de historial están en `1`, no en `0`.** Para whatsmeow un
`0` no es «nada», es «sin fijar»: manda el valor por defecto del servidor. El
24/09 se vinculó con `0` y bajaron **20 584 mensajes** (120 MB). El mínimo que
de verdad limita es `1`. No los pongas en 0.

**Nunca se borra una tabla `whatsmeow_*`.** Ahí viven la identidad del
dispositivo y las claves de cifrado: borrarlas obliga a escanear el QR otra
vez, con el teléfono del negocio en la mano. El cron solo toca `gows_messages`.

## Cómo se llega

Solo por `127.0.0.1:3001` dentro del droplet; desde fuera está cerrado aun con
la key. Para verlo desde tu computadora, un túnel:

    ssh -L 3001:127.0.0.1:3001 hermes@67.205.136.209

- Panel: http://localhost:3001/dashboard — usuario `rose`
- Swagger: http://localhost:3001/ — usuario `rose`

Las claves están en `~/waha/.env` (chmod 600) y **nunca se imprimen**: para un
script, `SECRETO="$(sed -n 's/^WAHA_API_KEY=//p' ~/waha/.env | head -1)"`.

## Mando diario

    cd ~/waha
    docker compose ps            # estado
    docker compose logs -f       # qué está haciendo
    docker compose up -d         # levantar / aplicar cambios
    docker compose stop          # bajar (consolida el WAL de paso)
    ~/waha/limpiar_almacen.sh    # limpiar el almacén a mano

La sesión sobrevive en `.sessions/`: parar y levantar **no** pide QR. Está
probado (25/09, al limpiar el almacén: volvió `WORKING` sola).

## Dónde vive este código

En el repo **`viveros-rose-apps`**, carpeta `waha/`. Los archivos del droplet
(`~/waha/`) son la copia que corre; la del repo es la que se respalda y se
revisa. Al cambiar uno, copiar al otro — hoy se hace a mano, con `scp`.

Lo que **no** está en el repo, a propósito: `~/waha/.env` (secretos) y
`.sessions/` (la identidad del dispositivo de WhatsApp).

## Lo que está andando

- ✅ **Las 17 etiquetas** de la cuenta: 4 de representante (Mary, Ruben,
  Salomón, Abraham — sin el prefijo «Resp:», que es de Linear), 5 de interés,
  `🔴 Responder`, 6 de estado y `Equipo`. Los colores los ajustó Abraham a
  mano en el teléfono; **no reasignarlos** (y de todos modos el `PUT` no
  deja recolorear: valida el nombre como si fuera nuevo y responde 422).
- ✅ **El sincronizador** (`sincronizador.py`), cada 2 minutos por cron: lee
  los leads vivos de Linear y el teléfono de Twenty, y deja en cada chat las
  etiquetas que tocan. Al 25/09: **19 chats de leads** y **3 internos**, 0
  errores. Converge — tres pasadas seguidas dan «0 chats, 21 sin cambio».
- ✅ **El endpoint de sincronización inmediata** (`endpoint.py`, puerto 3002,
  protegido con `SINCRO_SECRET`), que llama Control al asignar o mover un
  lead. Solo alcanzable desde el droplet de apps por la red privada; `ufw`
  permite 3002 únicamente desde `10.116.0.2`.
- ✅ **Almacén controlado**: límites de historial en 1 y un cron que lo
  limpia cada hora. Se mantiene en ~2 MB.
- ✅ **El webhook del «autor de mensajes»** registrado (ver el final).

## La regla que costó un día: las etiquetas van al `@lid`

**WhatsApp lee las etiquetas del `chatId` `@lid`, no de
`<número>@c.us`.** Lo que hace peligroso el error es que **no avisa**: WAHA
acepta el `@c.us`, responde 200, guarda las etiquetas y te las devuelve al
leerlas. Ningún error en ningún log, y nada en el teléfono. Así quedaron 19
chats etiquetados en el vacío.

El `@lid` se resuelve con la ruta **global**, con la sesión como parámetro:

    GET /api/contacts/check-exists?phone=<solo dígitos>&session=vivero
    → {"numberExists": true, "chatId": "165042775396449@lid",
       "pn": "50762531562@c.us"}

La variante de la sesión **no sirve**: `/api/vivero/contacts/check-exists`
toma «check-exists» como el id del contacto y devuelve basura.

Dos reglas que salieron de ahí:

- **Leer y escribir por el MISMO id.** El bug sobrevivió porque se escribía
  con un id y se leía armando el mapa al revés (una vuelta por etiqueta
  mirando sus chats), así que la comparación nunca cayó en la contradicción.
- **Al mudar etiquetas de un id a otro: primero vaciar el viejo, después
  escribir el nuevo** — el `PUT` reemplaza la lista completa. Al revés, si
  falla el segundo paso quedan etiquetas en los dos lados y nadie sabe cuál
  manda. `mudar_al_lid.py` es el script que hizo esa mudanza.

## Los números internos y la etiqueta «Equipo»

Los teléfonos del equipo (6108-7413 Mary, 6675-2380 el Jefe, 6567-3062
Abraham) **no son clientes**. Su chat lleva `Equipo` y **nada más**: ni
representante, ni interés, ni estado, ni `Responder`. La etiqueta existe
para lo contrario de las otras — para reconocer de un vistazo que ese chat
no es un lead.

Estos números nunca llegan a ser leads (el receptor del frontend los descarta
antes de crear lead, Person o copia de chat), así que el sincronizador no los
ve por el camino normal: los recorre aparte, en `etiquetar_equipo()`.

**Cuidado con la lista.** Hoy sale de `NUMEROS_INTERNOS` en `~/waha/.env`,
que es una **copia** de la que administra Abraham en Ajustes. Si mañana
agrega un número ahí, su chat **no** recibiría `Equipo` — nadie copia la
lista solo. El código ya prefiere el order-api si está `ORDER_API_KEY` en el
`.env`; con esa key puesta, esto sigue a Ajustes sin que nadie copie nada.

## Si hay que borrar todo

    cd ~/waha && docker compose down -v && cd ~ && rm -rf ~/waha

Eso desvincula el dispositivo del lado del servidor; en el teléfono conviene
además sacarlo desde *Dispositivos vinculados*. Hay respaldos de la sesión en
`~/backups/waha-sesion-*.tar.gz`.

## El webhook de la sesión del «autor de mensajes» — NO TOCARLO

Otra sesión va a registrar en WAHA un webhook **`message.any`** apuntando a
control-stock (**`/wa/autor`**), para averiguar de qué dispositivo salió cada
mensaje. Ese webhook **no es nuestro y no se quita ni se pisa**.

Qué lo protege hoy, y qué habría que cuidar:

- El **sincronizador tiene lista blanca**: solo puede llamar a los endpoints de
  etiquetas y a `check-exists`, y del recurso de sesión solo el `GET`. No puede
  escribir la configuración de la sesión ni por error — `_waha()` rechaza la
  ruta antes de salir a la red.
- **`limpiar_almacen.sh` no lo toca**: solo borra filas de `gows_messages` y
  consolida el WAL. La configuración de la sesión vive en
  `.sessions/gows/vivero/.waha.session.config.json`, que no se abre.
- **Cuidado con dos cosas** si alguna vez hace falta: un `PUT
  /api/sessions/vivero` con una configuración propia **reemplaza** la lista de
  webhooks (hay que leerla primero y devolverla completa), y `docker compose
  down -v` borraría volúmenes (hoy `.sessions` es un *bind mount*, así que
  sobrevive, pero no conviene confiarse).
**Al 25/09/2026 quedó registrado** con HMAC (el secreto sale de
`WAHA_WEBHOOK_SECRET` del `.env`, y no se imprime nunca). Se confirmó después
que el almacén siguió en ~2 MB y que **OpenWA siguió recibiendo**.
