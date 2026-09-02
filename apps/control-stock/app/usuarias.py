"""Altas y mantenimiento de empleadas, por consola en el servidor.

    python -m app.usuarias crear <usuario> "<Nombre>"   pide la contraseña
    python -m app.usuarias clave <usuario>              cambia la contraseña
    python -m app.usuarias desactivar <usuario>         revoca el acceso
    python -m app.usuarias lista

La contraseña se pide con getpass (no queda en el historial de la shell) y
solo se guarda su hash.
"""

import getpass
import sys

from . import datos, seguridad


def _pedir_contrasena():
    contrasena = getpass.getpass("Contraseña: ")
    if len(contrasena) < 8:
        sys.exit("La contraseña debe tener al menos 8 caracteres.")
    if contrasena != getpass.getpass("Repite la contraseña: "):
        sys.exit("No coinciden.")
    return contrasena


def main(argumentos):
    datos.iniciar_db()
    if len(argumentos) >= 2 and argumentos[0] == "crear":
        usuario = argumentos[1]
        nombre = argumentos[2] if len(argumentos) > 2 else usuario.capitalize()
        seguridad.crear_empleada(usuario, nombre, _pedir_contrasena())
        print(f"Empleada {usuario} ({nombre}) creada.")
    elif len(argumentos) == 2 and argumentos[0] == "clave":
        seguridad.cambiar_contrasena(argumentos[1], _pedir_contrasena())
        print(f"Contraseña de {argumentos[1]} cambiada; sus sesiones quedaron cerradas.")
    elif len(argumentos) == 2 and argumentos[0] == "desactivar":
        seguridad.desactivar(argumentos[1])
        print(f"{argumentos[1]} desactivada y sin sesiones.")
    elif argumentos == ["lista"]:
        for e in seguridad.listar():
            print(f"{e['usuario']:<20} {e['nombre']:<30} {'activa' if e['activa'] else 'INACTIVA'}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
