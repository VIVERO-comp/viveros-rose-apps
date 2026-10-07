"""Finanzas — la pantalla de quien lleva la plata (BLOQUES 20 y 22, esqueleto).

ESQUELETO NAVEGABLE (BLOQUE 25, 6/10/2026): números REALES en solo
lectura; lo que todavía no funciona es un botón APAGADO con «Todavía
no». Regla dura de Abraham: nada que parezca funcionar y no guarde —
acá no hay ni un POST.

Qué es — y qué no:

- **LA REGLA DEL BLOQUE 59.2 (7/10/2026): los cuatro números se calculan
  SOLO sobre VENTAS CONFIRMADAS.** Una cotización o un borrador no cuenta
  en ninguno de los cuatro. Hasta hoy «Vendido» y «Por cobrar» sumaban
  TODAS las órdenes no canceladas, borradores incluidos: en el Odoo de
  pruebas eso eran $38.809 de cotizaciones que nadie compró metidas en
  «Por cobrar» (de $41.068 a $2.259 al aplicar la regla). Los cuatro eran
  correctos para lo que cada uno sumaba; el problema era que **ninguno
  decía de qué universo hablaba** — y eso es lo que arregla el `hint` de
  cada tarjeta, que ahora se calcula acá y dice la frontera con palabras.
  El sí/no de «confirmada» NO se deduce de la clase A–H (F, G y H caen de
  los dos lados): viaja en el contrato del informe.
- **EL INVARIANTE, de CINCO términos: Vendido = Cobrado y confirmado +
  Pagos por confirmar + Por cobrar + Diferencia a revisar.** Es la
  identidad `total = pagado + debe` de cada venta confirmada, repartida:
  lo pagado se parte entre lo que alguien ya dio por bueno y lo que
  todavía nadie revisó. **Con la quinta adentro la igualdad cuadra
  SIEMPRE, por construcción** (decisión de Abraham, 7/10/2026) — lo
  anómalo queda con nombre, con cuenta de casos y con LISTA, en vez de
  ser un resto que alguien tenga que explicar.
  **La lista es parte del contrato, no un adorno**: el monto de la quinta
  línea es la SUMA de sus casos (se enumera primero y se suma después), y
  si el cierre de la identidad no se agota con los casos que la casa sabe
  nombrar, lo que sobra entra a la lista como un caso que dice que no se
  identificó. Así es imposible meter plata en «Diferencia» sin poder
  enumerarla. La línea NUNCA desaparece: con la lista vacía dice $0.00 en
  0 casos.
  Hoy, con los datos del Odoo de pruebas, la única manera de producir una
  diferencia es haber dado por buena MÁS plata de la que el sistema tiene
  en esa venta — y no hay ninguna ($0.00 en 0 casos).
- **Lo que queda fuera del universo también se dice, aparte**: la plata ya
  dada por buena sobre ventas que esta foto no cuenta (`confirmado_fuera`)
  y los pagos informados sobre cotizaciones sin confirmar (`cola_fuera`).
  Ninguno de los dos entra en los cuatro números — se MUESTRAN, que es la
  filosofía de `reconciliacion._sospecha_duplicado`.
- **Los cuatro números de arriba** salen de los MOTORES de la casa, tal
  cual: el informe de /revisar (pagos_confirmar._informe →
  reconciliacion.informe_datos), la cola real (pagos_confirmar.cola) y
  las confirmaciones humanas (pagos_confirmar.sumas_confirmadas). CERO
  cálculo paralelo: aquí solo se FILTRA y se SUMA lo que esos motores ya
  dicen. `_leer_universo()` no se toca: el filtro vive acá, así que
  /revisar y Pagos por confirmar siguen viendo TODO — un pago sobre una
  cotización sin confirmar es justo lo que esas pantallas existen para
  mostrar.
- **«Cómo pagaron»** (BLOQUE 59.4) es el desglose de «Cobrado y
  confirmado» por la evidencia que vio quien confirmó, sobre el MISMO
  universo, y su suma es exactamente esa tarjeta — lo afirma la misma
  prueba de cuadre. Mientras nadie haya confirmado, dice lo mismo que la
  tarjeta: que nadie ha confirmado todavía.
- **La cola de «Pagos por confirmar» es LA MISMA** de
  /pagos-por-confirmar, listada en solo lectura, y cada fila dice ahora
  DE CUÁNDO es la venta, CÓMO llegó la plata y QUIÉN la marcó (item 4,
  7/10/2026; los tres los arma `pagos_confirmar.cola` y los tres saben
  decir «todavía no se sabe» en vez de rellenar). El botón «Confirmar»
  va APAGADO con **«Todavía no»** a secas (item 5): confirmar es del
  asiento **system manager** y vive en /pagos-por-confirmar — no es un
  permiso pendiente, y decir que lo era mandaba a preguntarle a quien no
  decide.
- **«Cifras a revisar»** son las dos cifras a propósito de cifras.py
  (pending_revenue / delivered_revenue), con su rótulo provisional tal
  cual — nada se re-etiqueta ni se redondea distinto.
- **Las palabras son de negocio** (item 7): ni el nombre de una ruta, ni
  el de un archivo, ni el de un sistema que el lector no administra. Lo
  que el número significa, sí.
- **Nada se inventa**: si el informe trae huecos (Odoo caído o sin
  configurar), los números dependientes salen «sin dato» y el hueco se
  dice — jamás un $0 fingido (la regla del resumen de las 7 p.m.).
- **Reportes**: botones APAGADOS, «Todavía no». Gastos y ganancias no
  van (faltan costos confiables, BLOQUE 20).
- SOLO LECTURA hacia Odoo/Linear/Twenty y hacia lo local.

Nota de costo — YA PESÓ, y está arreglado (7/10/2026). Esta función
llamaba al informe UNA vez para las tarjetas y otra DENTRO de cola(), y
el precio no era teórico: medido con la puerta a Odoo instrumentada,
`/finanzas` hacía **24 viajes a Odoo y 2 lecturas de Linear** por
pintada, exactamente el doble que `/revisar` (12 y 1), con el mismo
motor. **No había ningún N+1**: ningún modelo se pide por fila, cada
viaje trae su lote (60 órdenes, 38 clientes, 29 facturas, 42
oportunidades). Era, literalmente, pedir dos veces lo mismo.

El arreglo es `cola(informe)`: se lee una vez y se le pasa la foto. No es
una caché —no hay dato viejo posible— y de paso las tarjetas y la lista
quedan CONSISTENTES, que antes no estaba garantizado. La caché de
`pedidos.plata()` sigue siendo la otra herramienta, para cuando lo que
haya que evitar sea leer en pintadas DISTINTAS; acá sobraba.
"""

from datetime import datetime

from . import calculos, cifras, pagos_confirmar
from .datos import ZONA_PANAMA

# La misma frontera de centavos de la cola y de Pedidos.
_CENTAVO = 0.009

# Los reportes del diseño (pantalla jordan-finanzas): hoy solo nombre y
# botón apagado. Cuando existan, cada uno tendrá su motor.
REPORTES = ("Ventas del mes", "Cobros por método", "Ventas por persona",
            "Cuentas por cobrar")

# Item 5 (7/10/2026): el botón decía «Todavía no — falta el sí de Jay», y
# ese permiso ya no es el motivo. Confirmar un pago es del asiento SYSTEM
# MANAGER y se hace en /pagos-por-confirmar: acá, que es solo lectura, el
# botón queda apagado y nada más. Decir un motivo que ya no aplica es peor
# que no decir ninguno — manda a preguntarle a quien no decide.
TEXTO_BOTON_CONFIRMAR = "Todavía no"

# BLOQUE 59.2, condición textual de Abraham: con el botón de confirmar
# apagado el número es estructuralmente $0.00, y un $0.00 sin explicación
# se lee como «no cobré nada». La línea tiene que decir la verdad: nadie
# ha confirmado, no es que no haya entrado plata.
TEXTO_NADIE_CONFIRMO = "Nadie ha confirmado pagos todavía."

# Caso aparte: SÍ hay marcas, pero ninguna cae en estas ventas (están
# sobre cotizaciones, canceladas u órdenes que ya no existen). Decir
# «nadie ha confirmado» ahí sería falso; el aviso de arriba cuenta cuánto.
TEXTO_NADA_DE_ESTAS = ("Todavía nadie ha dado por buena plata de estas "
                       "ventas.")

# La identidad que sostiene la pantalla, escrita para que se pueda leer.
# Son CINCO términos, no cuatro (decisión de Abraham, 7/10/2026): con la
# «Diferencia a revisar» adentro la igualdad cuadra SIEMPRE por
# construcción, y lo anómalo queda con nombre y con lista en vez de ser un
# resto que alguien tenga que explicar.
TEXTO_REGLA = ("Vendido = cobrado y confirmado + pagos por confirmar + "
               "por cobrar + diferencia a revisar.")

# La quinta línea. Dos textos, porque dicen cosas distintas: con 0 casos la
# noticia es que todo calza; con casos hay que decir QUÉ es una diferencia.
TEXTO_DIF_VACIA = ("Cada dólar de esas ventas está en uno de los tres "
                   "números de la izquierda.")
TEXTO_DIF_HAY = ("Plata dada por buena que no calza con lo que el sistema "
                 "tiene anotado en esa venta.")

# El caso de borde que impide que vuelva a existir un resto sin explicar:
# si el cierre de la identidad no se agota con los casos enumerados, lo que
# sobra ENTRA A LA LISTA como un caso más, diciendo que no se identificó.
# Así las dos condiciones de la prueba (la suma cuadra, y cada caso contado
# está en la lista) se cumplen por construcción y no por suerte.
TEXTO_DIF_SIN_IDENTIFICAR = ("No se pudo identificar de qué venta sale "
                             "esta diferencia.")

# Con la foto incompleta la lista también sale vacía, pero eso NO significa
# que todo calce: significa que no se sabe. Decir «cada dólar está en su
# lugar» ahí sería la misma mentira que un $0 fingido.
TEXTO_DIF_SIN_FOTO = ("No se pudo revisar si todo calza: arriba dice qué "
                      "falta.")


def ventas_del_universo(informe):
    """EL universo de los cinco términos, en UN SOLO lugar.

    **EL CRITERIO, escrito completo** (son TRES condiciones, y dos de
    ellas se aplican antes de llegar acá):

    1. **No es utilería de la app** — `client_order_ref` que empiece con
       «VISTA PREVIA» o sea «MUESTRA-PDF». Lo filtra `_leer_universo()`
       con `_es_ref_interna`, así que estas órdenes nunca llegan a esta
       función. **Es la condición que costó el caso de los $990**
       (7/10/2026): producción tiene DOS órdenes de vista previa
       —S00137 $190 y S00093 $800— y una medición hecha con el motor
       desplegado (que es anterior a ese filtro) las contaba. De ahí el
       desvío de $990,00 clavados en «Vendido» y en «Por cobrar» a la
       vez. Las dos son `draft`, así que el universo de abajo no cambia;
       pero la orden de vista previa es FIJA y se reusa, y confirmada
       entraría a «Vendido» — hay prueba que lo fija.
    2. **No está cancelada** — `clase != CANCELADA`.
    3. **Está confirmada** — `state` en `sale`/`done`, que viaja como
       `confirmada` en el contrato del informe.

    Está suelta a propósito. **A18 (abrir «Por cobrar» por antigüedad)
    tiene que leer exactamente esta lista**, no una copia: si cada
    pantalla filtra por su cuenta, el día que cambie la frontera una de
    las dos se queda vieja sin que nada avise — que es justo el bug que
    este bloque vino a cerrar.

    Ojo con A18, que mide por el OTRO lado: sus facturas abiertas de
    producción son $5.176,35, de los cuales **$3.109,85 en 29 facturas
    del diario «Ventas Super Extra» no tienen orden ninguna**, así que no
    pueden entrar acá (y el motor además excluye ese diario del dinero).
    Dentro del alcance quedan $2.066,50 en 3 facturas, mientras este
    «Por cobrar» da $2.069,00: los **$2,50** de diferencia son S00084, la
    venta en línea `VR-549312`, **confirmada y todavía sin factura** — se
    ve por el lado de la orden y no por el de la factura. Los dos números
    están bien; miden cosas distintas.

    Devuelve (confirmadas, sin_confirmar)."""
    vivas = [v for v in (informe.get("ventas") or [])
             if (v.get("clase") or "").strip().upper() != "CANCELADA"]
    return ([v for v in vivas if v.get("confirmada")],
            [v for v in vivas if not v.get("confirmada")])


def _tarjeta(titulo, monto, hint, n=None, rojo=False):
    """Una tarjeta de arriba, ya decidida: monto None = «sin dato» (el
    hueco viaja aparte y se pinta en la franja)."""
    return {
        "titulo": titulo,
        "monto": monto,
        "texto": (calculos.dinero(monto) if monto is not None else "sin dato"),
        "hint": hint,
        "n": n,
        "rojo": bool(rojo) and monto is not None and monto > _CENTAVO,
    }


def resumen():
    """Todo lo que la plantilla pinta, ya decidido (regla 10)."""
    # UNA SOLA lectura del motor por pintada (7/10/2026). Antes esta
    # función pedía la foto dos veces —una para las tarjetas y otra
    # adentro de cola()— y eso eran 24 viajes a Odoo y 2 lecturas de
    # Linear, el doble que /revisar, medido en el 8095. Ahora se lee una
    # vez y se le PASA a la cola. Además de la mitad del tiempo, gana
    # consistencia: las tarjetas y la lista salen de la MISMA foto, no de
    # dos lecturas de Odoo que podían diferir.
    try:
        informe = pagos_confirmar._informe()
    except Exception as fallo:
        informe = {"ventas": [],
                   "huecos": [f"El informe de plata no contestó: {fallo}"]}
    huecos = list(informe.get("huecos") or [])
    try:
        pendientes, _huecos_cola = pagos_confirmar.cola(informe)
    except Exception as fallo:
        pendientes = []
        huecos.append(f"La cola de pagos no contestó: {fallo}")

    # LA FRONTERA (BLOQUE 59.2): solo ventas CONFIRMADAS. El sí/no llega
    # decidido en el contrato del informe — acá no se adivina por clase —
    # y el filtro vive en UNA función, para que A18 lo herede.
    ventas, sin_confirmar = ventas_del_universo(informe)
    vivas = ventas + sin_confirmar
    ids = {v.get("orden_id") for v in ventas}
    # Con huecos y sin ventas no hay foto: los números del informe salen
    # «sin dato», nunca $0 (un cero fingido se celebra o se cobra mal).
    # Ojo: la foto puede ser buena y no tener NI UNA venta confirmada —
    # eso es un $0.00 verdadero, no un hueco. Por eso se mira `vivas`.
    con_datos = bool(vivas) or not huecos

    vendido = round(sum(float(v.get("total") or 0) for v in ventas), 2) \
        if con_datos else None
    por_cobrar = round(sum(float(v.get("debe") or 0) for v in ventas), 2) \
        if con_datos else None
    n_deben = sum(1 for v in ventas
                  if float(v.get("debe") or 0) > _CENTAVO)

    # Lo confirmado por humanos: tabla local, siempre legible. Se parte en
    # dos — lo que cae DENTRO del universo (y es la tarjeta) y lo que cae
    # fuera (una confirmación sobre una cotización sin confirmar, una
    # cancelada o una orden que ya no está). Lo de fuera NO se suma a
    # nada: se muestra aparte, porque repartirlo falsearía el invariante.
    sumas = pagos_confirmar.sumas_confirmadas()
    confirmado = round(sum(m for oid, m in sumas.items() if oid in ids), 2)
    fuera_monto = round(sum(m for oid, m in sumas.items()
                            if oid not in ids), 2)
    confirmado_fuera = ({"n": sum(1 for oid in sumas if oid not in ids),
                         "monto": fuera_monto}
                        if abs(fuera_monto) > _CENTAVO else None)

    # Lo por confirmar: la suma de la plata NUEVA de cada fila de la cola
    # (monto_nuevo — la misma cifra que confirma el system manager), SOLO
    # de las ventas confirmadas. La lista de abajo sigue completa.
    de_confirmadas = [p for p in pendientes if p.get("confirmada")]
    otros_pendientes = [p for p in pendientes if not p.get("confirmada")]
    por_confirmar = round(sum(float(p.get("monto_nuevo") or 0)
                              for p in de_confirmadas), 2) \
        if con_datos else None
    cola_fuera_monto = round(sum(float(p.get("debe") or 0)
                                 for p in otros_pendientes), 2)
    cola_fuera = ({"n": len(otros_pendientes), "monto": cola_fuera_monto}
                  if otros_pendientes else None)

    # «Cómo pagaron» (BLOQUE 59.4): el desglose de la tarjeta de arriba,
    # mismo universo y misma suma — lo afirma la prueba de cuadre.
    desglose = pagos_confirmar.confirmado_por_metodo(ids)
    n_confirmaciones = sum(d["n"] for d in desglose)
    # Tres estados, no dos: nadie confirmó nunca · hay marcas pero ninguna
    # de estas ventas · hay marcas de estas ventas. Los tres dicen algo
    # distinto y el de en medio no puede salir como el primero.
    hay_marcas_dentro = any(oid in ids for oid in sumas)
    if hay_marcas_dentro:
        linea_confirmado = (f"Plata de esas ventas que alguien del equipo "
                            f"ya dio por buena, con su nombre y la fecha "
                            f"({n_confirmaciones} marcas).")
    elif sumas:
        linea_confirmado = TEXTO_NADA_DE_ESTAS
    else:
        linea_confirmado = TEXTO_NADIE_CONFIRMO

    tarjetas = [
        # Item 7 (7/10/2026): las ayudas decían «informe de /revisar
        # (canceladas fuera)» y «confirmaciones humanas». Quien lee esta
        # pantalla no sabe qué es /revisar ni por qué una confirmación
        # sería «humana»: se dice qué hay adentro del número, no de qué
        # archivo salió. BLOQUE 59.2: y ahora dice también DE QUÉ UNIVERSO
        # habla, que era el problema de fondo — los cuatro eran correctos
        # y ninguno decía qué sumaba.
        _tarjeta("Vendido", vendido,
                 f"Las {len(ventas)} ventas que el cliente ya confirmó. "
                 "Lo cotizado y lo cancelado no cuenta.",
                 n=len(ventas)),
        _tarjeta("Cobrado y confirmado", confirmado, linea_confirmado,
                 n=n_confirmaciones),
        _tarjeta("Pagos por confirmar", por_confirmar,
                 (f"Plata que ya entró en {len(de_confirmadas)} de esas "
                  "ventas y todavía nadie ha revisado.") if con_datos else
                 "La lista no se pudo leer.", n=len(de_confirmadas)),
        _tarjeta("Por cobrar", por_cobrar,
                 f"Lo que falta cobrar en {n_deben} de esas ventas. "
                 "Una cotización no debe nada: no cuenta acá.",
                 n=n_deben, rojo=True),
    ]

    # ---------------------------------------------------------------
    # LA QUINTA LÍNEA: «Diferencia a revisar», con nombre y con LISTA.
    #
    # Se arma al revés de como estaba antes: primero se ENUMERAN los casos
    # y después el monto es la suma de esa lista. Así «$X en N casos»
    # nunca puede mentir — y si el cierre de la identidad no se agota con
    # los casos que la casa sabe nombrar, lo que sobra entra a la lista
    # como un caso más que dice que no se identificó. Meter plata en
    # «Diferencia» sin poder enumerarla es justo la trampa que esto cierra.
    # ---------------------------------------------------------------
    casos = []
    if con_datos and vendido is not None:
        for v in ventas:
            marcado = sumas.get(v.get("orden_id"), 0.0)
            pagado_v = round(float(v.get("pagado") or 0), 2)
            if marcado > pagado_v + _CENTAVO:
                casos.append({
                    "orden": v.get("nombre") or "",
                    "cliente": v.get("cliente") or "",
                    # Signo: negativo = se dio por buena MÁS plata de la
                    # que el sistema tiene. Es el signo que cierra la
                    # identidad, no una elección de estilo.
                    "monto": round(pagado_v - marcado, 2),
                    "motivo": (f"Se dieron por buenos "
                               f"{calculos.dinero(marcado)} y en el "
                               f"sistema hay {calculos.dinero(pagado_v)}."),
                })
        cierre = round(vendido - (confirmado + (por_confirmar or 0)
                                  + (por_cobrar or 0)), 2)
        sobra = round(cierre - sum(c["monto"] for c in casos), 2)
        if abs(sobra) > _CENTAVO:
            casos.append({"orden": "", "cliente": "", "monto": sobra,
                          "motivo": TEXTO_DIF_SIN_IDENTIFICAR})
    hay_foto = con_datos and vendido is not None
    diferencia = {
        # Con la foto incompleta el monto es «sin dato», como los otros
        # números que dependen del informe: un $0.00 ahí diría «todo
        # calza» cuando lo cierto es que no se sabe.
        "monto": (round(sum(c["monto"] for c in casos), 2)
                  if hay_foto else None),
        "n": len(casos),
        "casos": casos,
        # La línea NUNCA desaparece: con la lista vacía dice $0.00 en 0
        # casos. Una línea que a veces está y a veces no es peor que una
        # que siempre está en cero — nadie sabe si falta o si está bien.
        # Tres textos, porque son tres noticias distintas: hay casos · no
        # hay ninguno · no se pudo revisar.
        "hint": (TEXTO_DIF_SIN_FOTO if not hay_foto else
                 (TEXTO_DIF_HAY if casos else TEXTO_DIF_VACIA)),
    }

    hoy = datetime.now(ZONA_PANAMA).date()
    entregado_mes = cifras.delivered_revenue(hoy.replace(day=1), hoy)
    pendiente = cifras.pending_revenue()
    cifras_revisar = [
        {"texto": (f"Plata cobrada de trabajos sin entregar: "
                   f"${pendiente['monto']:,.2f} en {pendiente['n']} ventas"
                   + (f" ({pendiente['sin_monto']} sin monto conocido)"
                      if pendiente["sin_monto"] else "")),
         "rotulo": pendiente["rotulo"]},
        {"texto": (f"Entregado del 1 al {hoy.day} de este mes: "
                   f"${entregado_mes['monto']:,.2f} en "
                   f"{entregado_mes['n']} entregas"
                   + (f" ({entregado_mes['sin_monto']} sin monto conocido)"
                      if entregado_mes["sin_monto"] else "")),
         "rotulo": entregado_mes["rotulo"]},
    ]

    return {
        "tarjetas": tarjetas,
        # La cola de abajo sigue COMPLETA (es la misma de Pagos por
        # confirmar), pero partida: las de ventas confirmadas, que son las
        # que suman, y las otras, que se ven con su aviso de que no cuentan.
        "pendientes": de_confirmadas,
        "otros_pendientes": otros_pendientes,
        "huecos": huecos,
        "con_datos": con_datos,
        "diferencia": diferencia,
        "regla": TEXTO_REGLA,
        "confirmado_fuera": confirmado_fuera,
        "cola_fuera": cola_fuera,
        "desglose": desglose,
        "sin_confirmar": len(sin_confirmar),
        "nadie_confirmo": not hay_marcas_dentro,
        # El texto del desglose vacío es el MISMO de la tarjeta: si no hay
        # nada que repartir, las dos cosas dicen lo mismo y por el mismo
        # motivo (condición 3 del BLOQUE 59.4).
        "texto_nadie_confirmo": linea_confirmado,
        "cifras": cifras_revisar,
        "reportes": list(REPORTES),
        "boton_confirmar": TEXTO_BOTON_CONFIRMAR,
    }
