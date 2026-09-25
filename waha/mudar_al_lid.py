#!/usr/bin/env python3
"""Muda las etiquetas del @c.us (donde no se ven) al @lid (donde sí).

Uso:  ./mudar_al_lid.py LEAD-80 [LEAD-xx ...]
      ./mudar_al_lid.py --todos

Dos pasos por chat, en este orden:
  1. dejar VACIA la lista del @c.us  (el PUT reemplaza la lista completa)
  2. escribir la correcta en el @lid

El orden importa: si se escribiera primero el @lid y algo fallara en el
paso 2, quedarian etiquetas en los dos lados y nadie sabria cual manda.
"""
import sys
sys.path.insert(0, "/home/hermes/waha")
import sincronizador as s

argumentos = sys.argv[1:]
if not argumentos:
    print(__doc__)
    sys.exit(1)

etiquetas = {n: i for n, i in s.etiquetas_de_whatsapp().items()
             if n not in s.DE_WHATSAPP}
leads = s.leads_del_crm()
if "--todos" not in argumentos:
    pedidos = {a.upper() for a in argumentos}
    leads = [l for l in leads if l["ref"].upper() in pedidos]

print("MUDANDO %d chat(s) del @c.us al @lid" % len(leads))
print()
hechos, errores = 0, []
for lead in sorted(leads, key=lambda l: l["ref"]):
    if not lead["telefono"]:
        print("%-9s %-22s sin telefono: nada que mudar" % (lead["ref"], lead["nombre"][:20]))
        continue
    viejo = s.solo_digitos(lead["telefono"]) + "@c.us"
    nuevo = s.chat_id_real(lead["telefono"])
    if not nuevo:
        print("%-9s %-22s el numero no tiene WhatsApp" % (lead["ref"], lead["nombre"][:20]))
        continue
    quiere, faltan = s.deseadas(lead, etiquetas)

    print("%-9s %s" % (lead["ref"], lead["nombre"]))
    print("          viejo (@c.us): %s" % viejo)
    print("          nuevo (@lid):  %s" % nuevo)
    antes_viejo = sorted(s.etiquetas_del_chat(viejo))
    antes_nuevo = sorted(s.etiquetas_del_chat(nuevo))
    print("          antes · @c.us: %s" % (", ".join(antes_viejo) or "(ninguna)"))
    print("          antes · @lid:  %s" % (", ".join(antes_nuevo) or "(ninguna)"))
    try:
        if antes_viejo:
            s._waha("/api/%s/labels/chats/%s" % (s.SESION, viejo),
                    datos={"labels": []}, metodo="PUT")
        s._waha("/api/%s/labels/chats/%s" % (s.SESION, nuevo),
                datos={"labels": [{"id": etiquetas[n]} for n in quiere]},
                metodo="PUT")
        hechos += 1
    except Exception as fallo:
        errores.append((lead["ref"], str(fallo)[:90]))
        print("          ERROR: %s" % str(fallo)[:80])
        continue
    print("          despues · @c.us: %s" % (
        ", ".join(sorted(s.etiquetas_del_chat(viejo))) or "(vacia, como debe)"))
    print("          despues · @lid:  %s" % (
        ", ".join(sorted(s.etiquetas_del_chat(nuevo))) or "(NINGUNA — revisar)"))
    if faltan:
        print("          ojo, no existen en WhatsApp: %s" % ", ".join(faltan))
    print()

print("=" * 70)
print("MUDADOS: %d · errores: %d" % (hechos, len(errores)))
for ref, motivo in errores:
    print("   ERROR %s: %s" % (ref, motivo))
