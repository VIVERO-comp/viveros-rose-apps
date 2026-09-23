"""Números de WhatsApp de coworkers: chats internos que nunca se vuelven leads.

Abraham chatea a veces con el jefe desde el celular del trabajo (23/09/2026)
y esos mensajes entraban al CRM como si fueran de clientes. Los números de
esta lista los descarta el receptor de WhatsApp del frontend ANTES de crear
lead, Person o copia de chat: son gente del negocio, no clientes.

La lista vive en la base `tienda` (Postgres del droplet, tabla
numero_coworker — migración 018 del order-api), que el receptor consulta vía
GET /api/crm/numeros-coworkers del order-api. Esta app la administra desde la
sección "Números de coworkers" de Ajustes (solo admins). Mismo patrón de
almacenamiento que fichas.py: Postgres con TIENDA_DSN en el droplet, SQLite
local como respaldo de desarrollo.
"""

import os
import re

from . import datos

SOLO_DIGITOS = re.compile(r"^\d{7,15}$")


def normalizar(crudo):
    """El número como dígitos pelados ('6675-2380' → '66752380'), o '' si lo
    escrito no parece un teléfono. El receptor compara por los últimos 8
    dígitos, así que da igual escribirlo con o sin el 507."""
    numero = re.sub(r"[\s\-.()+]", "", str(crudo or ""))
    return numero if SOLO_DIGITOS.match(numero) else ""


# ---------------------------------------------------------------------------
# Almacenamiento: Postgres (tienda) o SQLite local como respaldo de desarrollo
# ---------------------------------------------------------------------------

def _dsn():
    return os.environ.get("TIENDA_DSN")


def _con_postgres():
    # Import perezoso: psycopg solo hace falta donde hay TIENDA_DSN (el
    # droplet); las pruebas y el desarrollo local usan SQLite.
    import psycopg

    return psycopg.connect(_dsn())


def _asegurar_tabla_sqlite(con):
    # Espejo local de la migración 018 del order-api, solo para desarrollo.
    con.execute("""
        CREATE TABLE IF NOT EXISTS numero_coworker (
            numero TEXT PRIMARY KEY,
            nota TEXT NOT NULL DEFAULT '',
            agregado_por TEXT NOT NULL DEFAULT '',
            creado_en TEXT NOT NULL
        )""")


def listar():
    """[{numero, nota}] de todos los números guardados, para Ajustes."""
    consulta = "SELECT numero, nota FROM numero_coworker ORDER BY creado_en, numero"
    if _dsn():
        with _con_postgres() as con:
            filas = con.execute(consulta).fetchall()
            return [{"numero": f[0], "nota": f[1]} for f in filas]
    with datos._db() as con:
        _asegurar_tabla_sqlite(con)
        return [dict(f) for f in con.execute(consulta).fetchall()]


_UPSERT = """
    INSERT INTO numero_coworker (numero, nota, agregado_por, creado_en)
    VALUES ({p}, {p}, {p}, {ahora})
    ON CONFLICT (numero) DO UPDATE SET nota = EXCLUDED.nota
"""


def agregar(numero, nota, usuario):
    """Guarda el número (ya normalizado). Volver a agregar uno que existe
    solo le actualiza la nota."""
    if _dsn():
        with _con_postgres() as con:
            con.execute(_UPSERT.format(p="%s", ahora="now()"),
                        (numero, nota, usuario))
    else:
        with datos._db() as con:
            _asegurar_tabla_sqlite(con)
            con.execute(_UPSERT.format(p="?", ahora="?"),
                        (numero, nota, usuario, datos.ahora_iso()))


def quitar(numero):
    consulta = "DELETE FROM numero_coworker WHERE numero = {p}"
    if _dsn():
        with _con_postgres() as con:
            con.execute(consulta.format(p="%s"), (numero,))
    else:
        with datos._db() as con:
            _asegurar_tabla_sqlite(con)
            con.execute(consulta.format(p="?"), (numero,))
