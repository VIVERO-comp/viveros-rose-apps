"""EL punto único de escritura de stock de la app (review del Arquitecto,
5/10/2026, obligatorio).

TODO cambio de cantidad que salga de esta app —el modal de Modificar
stock, el alta de planta con cantidad inicial, el conteo quincenal y la
vista plana del rol Inventario— pasa por `escribir_stock()`, de cualquier
rol. Nadie más llama a `datos.ajustar_en_odoo` (hay una prueba que lee el
código y lo vigila). El orden es FIJO:

1. el candado lo puso la ruta (sesión, permisos, cantidad legible);
2. se escribe en Odoo por el order-api, que lee el «antes» DE ODOO en ese
   mismo momento (stock.quant fresco) y lo devuelve como `anterior` —
   jamás se registra el «antes» de un hidden del formulario;
3. se anota en `stock_cambio` (sku, antes, despues, tipo_operacion, por,
   en_epoch). Si el INSERT falla, Odoo YA quedó escrito y eso no se
   deshace: la función lo devuelve en `registro_fallo` y la ruta está
   OBLIGADA a decirlo en pantalla (error ruidoso, nunca silencio).

La única subida de stock que NO pasa por aquí es la recepción de compras
(`compra_odoo.recibir`): esa no fija una cantidad absoluta, valida el
picking de Odoo y queda auditada allá (y lo dañado en la app). Decisión
anotada en el plan.

`en_epoch` va en epoch UTC (segundos, de un datetime aware): nada que
compare horas entre máquinas usa texto; quien muestra decide la zona.
"""

import logging
import re
from datetime import datetime, timezone

from . import datos

# El texto del error ruidoso, uno solo para todas las pantallas.
AVISO_REGISTRO = ("El stock quedó guardado en Odoo, pero NO se pudo anotar "
                  "en la bitácora local (stock_cambio). Avísale al "
                  "encargado para que lo revise.")


def iniciar_tablas():
    with datos._db() as con:
        con.executescript("""
        -- La bitácora de la spec de Korto: quién, cuándo, cuánto había y
        -- cuánto quedó, por cada cambio de cantidad hecho desde la app.
        -- `antes` puede ser NULL solo si Odoo no devolvió el valor previo
        -- (no debería pasar: el order-api siempre manda `anterior`).
        CREATE TABLE IF NOT EXISTS stock_cambio (
            n INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            antes INTEGER,
            despues INTEGER NOT NULL,
            tipo_operacion TEXT NOT NULL,
            por TEXT NOT NULL,
            en_epoch INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stock_cambio_sku
            ON stock_cambio (sku, n);
        """)


def escribir_stock(ajustes, empleado, motivo):
    """Escribe cantidades ABSOLUTAS en Odoo y registra cada aplicado.

    `ajustes`: [{sku, cantidad, esperada}] — `esperada` es el candado
    optimista de siempre (si el stock se movió en el medio, el order-api
    devuelve `conflicto` con el valor fresco y nada se escribe). `motivo`
    viaja a Odoo y queda como `tipo_operacion` en la bitácora: es el
    mismo vocabulario (ajuste_rapido · alta_de_planta · conteo_quincenal
    · conteo_inventario).

    Devuelve la respuesta del order-api con una llave más:
    `registro_fallo` = [skus aplicados en Odoo cuyo INSERT local falló].
    La ruta que la reciba DEBE mostrarla; callársela sería mentir.
    Un error de conexión sube como datos.SinConexion, igual que siempre.
    """
    respuesta = datos.ajustar_en_odoo(ajustes, empleado, motivo)
    fallidos = []
    for r in respuesta.get("resultados", []):
        if r.get("resultado") != "aplicado":
            # sin_cambio no cambió nada; conflicto/no_existe/odoo_error no
            # escribieron: la bitácora solo anota lo que de verdad cambió.
            continue
        try:
            _registrar(r["sku"], r.get("anterior"), r["cantidad"],
                       motivo, empleado)
        except Exception as fallo:
            logging.getLogger("control_stock").error(
                "stock_cambio NO registrado para %s (%s por %s): %r",
                r["sku"], motivo, empleado, fallo)
            fallidos.append(r["sku"])
    respuesta["registro_fallo"] = fallidos
    return respuesta


def _registrar(sku, antes, despues, tipo_operacion, por):
    with datos._db() as con:
        con.execute(
            "INSERT INTO stock_cambio (sku, antes, despues, tipo_operacion, "
            "por, en_epoch) VALUES (?,?,?,?,?,?)",
            (sku, antes, despues, tipo_operacion, por,
             int(datetime.now(timezone.utc).timestamp())))


def cambios_recientes(sku="", limite=200):
    """La bitácora para la pantalla de admins, lo más nuevo primero."""
    with datos._db() as con:
        if sku:
            filas = con.execute(
                "SELECT sku, antes, despues, tipo_operacion, por, en_epoch "
                "FROM stock_cambio WHERE sku=? ORDER BY n DESC LIMIT ?",
                (sku, limite))
        else:
            filas = con.execute(
                "SELECT sku, antes, despues, tipo_operacion, por, en_epoch "
                "FROM stock_cambio ORDER BY n DESC LIMIT ?", (limite,))
        return [dict(f) for f in filas]


def cantidad_contada(valor):
    """(cantidad, error) de lo contado en la vista plana del conteo.

    La CANTIDAD ABSOLUTA (lo que se contó), con la regla del lote de
    formularios (2/10/2026): vacío, texto, negativo o decimales se
    rechazan con su aviso — jamás se vuelven 0 en silencio. El 0 sí vale:
    contar cero plantas es un conteo."""
    crudo = str(valor if valor is not None else "").strip()
    if not crudo:
        return None, "Escribe la cantidad contada (0 si no queda ninguna)."
    if not re.fullmatch(r"\d{1,6}", crudo):
        return None, (f"«{crudo}» no se entiende como cantidad: escribe un "
                      "número entero sin signo ni decimales, como 0, 3 o 12.")
    return int(crudo), ""
