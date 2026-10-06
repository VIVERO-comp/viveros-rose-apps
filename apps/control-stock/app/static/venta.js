/* Buscador en vivo de la página Crear Venta: filtra mientras se escribe,
   igual que el buscador de la pestaña Stock (nadie presiona Enter). Los
   resultados llegan de /venta/buscar (precios ya formateados en el
   servidor) y agregar al carrito sigue siendo un POST normal. */

const entradaBusca = document.getElementById("busca-venta");
const contenedorResultados = document.getElementById("resultados-venta");
let temporizadorBusca = null;

function escaparHtml(texto) {
  return String(texto)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function pintarResultados(lista, q) {
  // El mismo carrito y el mismo buscador sirven a Nueva Venta y a los
  // mini-formularios de cotización de servicio (app/cotizaciones.py): cada
  // pantalla marca a dónde debe volver el POST de agregar con
  // data-volver en #resultados-venta.
  const volver = (contenedorResultados.dataset.volver || "/venta/nueva");
  if (!lista.length) {
    contenedorResultados.innerHTML =
      '<p class="nada-venta">Nada con "' + escaparHtml(q) + '". Prueba otro nombre o el SKU.</p>';
    return;
  }
  // El stock junto al precio (28/09/2026, mínimo indispensable de JS: el
  // servidor ya pintó esto mismo en la carga inicial, y aquí solo se
  // repite para que escribir en vivo diga lo mismo). `disponible` puede
  // venir null (Odoo no contestó ahora mismo) — nunca se dice "0" sin
  // saberlo.
  function stockHtml(p) {
    if (p.disponible === null || p.disponible === undefined) {
      return '<span class="stock-mini">stock: no disponible ahora</span>';
    }
    const agotado = p.disponible <= 0 ? " stock-agotado" : "";
    return `<span class="stock-mini${agotado}">${p.disponible} en stock</span>`;
  }
  contenedorResultados.innerHTML = lista.map(p => `
    <form method="post" action="/venta/carrito/agregar" class="sin-margen">
      <input type="hidden" name="producto_id" value="${p.id}">
      <input type="hidden" name="cantidad" value="1">
      <input type="hidden" name="volver" value="${escaparHtml(volver)}">
      <button class="planta planta-boton" type="submit">
        <div class="foto">🪴<img src="/venta/foto/${p.id}" alt="" loading="lazy" onerror="this.remove()"></div>
        <div class="info"><b>${escaparHtml(p.nombre)}</b><span>${escaparHtml(p.sku)}</span>
          <span class="precio"><b>${escaparHtml(p.precio)}</b></span>
          ${stockHtml(p)}</div>
        <div class="agregar-venta">+</div>
      </button>
    </form>`).join("");
}

if (entradaBusca) {
  entradaBusca.addEventListener("input", () => {
    clearTimeout(temporizadorBusca);
    const q = entradaBusca.value.trim();
    if (!q) {
      contenedorResultados.innerHTML = "";
      return;
    }
    temporizadorBusca = setTimeout(async () => {
      try {
        const respuesta = await fetch("/venta/buscar?q=" + encodeURIComponent(q));
        const datos = await respuesta.json();
        if (entradaBusca.value.trim() !== q) return; // ya escribió otra cosa
        if (datos.error) {
          contenedorResultados.innerHTML =
            '<p class="nada-venta">' + escaparHtml(datos.error) + "</p>";
          return;
        }
        pintarResultados(datos.resultados, q);
      } catch (e) {
        contenedorResultados.innerHTML =
          '<p class="nada-venta">Sin conexión. Intenta de nuevo.</p>';
      }
    }, 300);
  });
  // El Enter del teclado sigue funcionando (el form hace GET con ?q=...),
  // pero ya nadie lo necesita.
}

/* Nombre y celular del cliente: se copian a los campos ocultos del form de
   acciones (para que viajen con Cotizar/Pagar) y se guardan como borrador
   en el servidor, para sobrevivir a los reloads de agregar/quitar plantas. */

const entradaNombre = document.getElementById("cliente-nombre");
const entradaCelular = document.getElementById("cliente-celular");
let temporizadorBorrador = null;

const contenedorServicios = document.getElementById("servicios");
const contenedorRenglones = document.getElementById("renglones");
const camposCliente = document.getElementById("campos-cliente");

function renglonesDe(contenedor, nombres) {
  // [{texto, ...}] con lo escrito en cada renglón repetible del contenedor.
  return [...contenedor.querySelectorAll(".servicio")].map(renglon => {
    const datos = {};
    for (const [clave, nombre] of Object.entries(nombres)) {
      const campo = renglon.querySelector(`[name="${nombre}"]`);
      datos[clave] = campo ? campo.value : "";
    }
    return datos;
  });
}

function sincronizarCliente() {
  // La pantalla de EDITAR marca sus contenedores con data-sin-borrador:
  // el borrador es el de una cotización nueva y guardarlo desde aquí lo
  // pisaría con los renglones de la que se está editando.
  if ((contenedorServicios && contenedorServicios.dataset.sinBorrador) ||
      (contenedorRenglones && contenedorRenglones.dataset.sinBorrador)) return;
  const nombre = entradaNombre ? entradaNombre.value : "";
  const celular = entradaCelular ? entradaCelular.value : "";
  clearTimeout(temporizadorBorrador);
  temporizadorBorrador = setTimeout(() => {
    const datos = new FormData();
    datos.append("cliente", nombre);
    datos.append("celular", celular);
    // Los datos opcionales del cliente (RUC, cédula, correo, dirección),
    // los cargos (envío, instalación) y los renglones ya escritos viajan
    // también: agregar o quitar una planta recarga la página y sin esto se
    // perderían. Se recorren TODOS los .dato-cliente de la página, no solo
    // los del bloque Cliente: los cargos viven en su propia sección.
    for (const campo of document.querySelectorAll(".dato-cliente")) {
      // Radios (opción de envío) y casillas (las del PDF): solo viaja
      // lo marcado — un checkbox sin marcar no manda nada, como un form.
      if ((campo.type === "radio" || campo.type === "checkbox") && !campo.checked) continue;
      datos.append(campo.name, campo.value);
    }
    if (contenedorServicios) {
      datos.append("servicios", "1");
      for (const s of renglonesDe(contenedorServicios,
                                  {texto: "servicio_texto", monto: "servicio_monto",
                                   descripcion: "servicio_descripcion"})) {
        datos.append("servicio_texto", s.texto);
        datos.append("servicio_monto", s.monto);
        datos.append("servicio_descripcion", s.descripcion);
      }
    }
    if (contenedorRenglones) {
      datos.append("renglones", "1");
      for (const r of renglonesDe(contenedorRenglones,
                                  {texto: "renglon_texto", cantidad: "renglon_cantidad",
                                   precio: "renglon_precio",
                                   descripcion: "renglon_descripcion"})) {
        datos.append("renglon_texto", r.texto);
        datos.append("renglon_cantidad", r.cantidad);
        datos.append("renglon_precio", r.precio);
        datos.append("renglon_descripcion", r.descripcion);
      }
    }
    fetch("/venta/borrador", { method: "POST", body: datos }).catch(() => {});
  }, 400);
}

for (const entrada of [entradaNombre, entradaCelular]) {
  if (entrada) entrada.addEventListener("input", sincronizarCliente);
}
for (const campo of document.querySelectorAll(".dato-cliente")) {
  campo.addEventListener("input", sincronizarCliente);
}

/* Los datos de factura vienen ABIERTOS del servidor. En computadora se
   quedan así; en el teléfono se pliegan al cargar, salvo que ya tengan
   algo escrito (pedido del dueño, 23/09/2026). */
const pliegueFactura = document.querySelector("details.pliegue");
if (pliegueFactura && !pliegueFactura.dataset.lleno &&
    window.matchMedia("(max-width: 899px)").matches) {
  pliegueFactura.open = false;
}

/* El lead elegido (23/09/2026): al escogerlo se llenan el nombre y el
   celular (el amarre real lo hace el servidor con lead_ref al crear la
   venta). La lista sale del embudo de Linear; antes salia del kanban
   Retail, que murio en la Fase 5. */
const leadCrm = document.getElementById("lead-crm");
if (leadCrm) {
  leadCrm.addEventListener("change", () => {
    const opcion = leadCrm.selectedOptions[0];
    if (!opcion || !opcion.value) return;
    if (entradaNombre && opcion.dataset.nombre) entradaNombre.value = opcion.dataset.nombre;
    if (entradaCelular) entradaCelular.value = opcion.dataset.cel || "";
    sincronizarCliente();
  });
}

/* El desglose de Nueva venta (23/09/2026): mientras se escribe un cargo,
   su renglón aparece y el total lo suma — el mismo desglose que va a
   imprimir el PDF. Lo inicial lo pinta el servidor (con el borrador); el
   monto que manda es el que confirma Odoo al crear la orden. */
const desglose = document.getElementById("desglose");

/* El envío en vivo espeja a ventas.resolver_envio del servidor (que es
   quien decide de verdad al generar): la opción marcada pone su precio,
   el monto escrito manda, «Sin envío» cobra 0 aunque haya monto. */
function envioEnVivo() {
  const marcado = document.querySelector('input[name="envio_opcion"]:checked');
  const campo = document.querySelector('.dato-cliente[name="envio"]');
  const escrito = campo ? Math.max(numeroDe(campo, 0), 0) : 0;
  if (!marcado) return { monto: escrito, nombre: "Envío a domicilio" };
  if (!marcado.value) return { monto: 0, nombre: "Envío a domicilio" };
  if (marcado.value === "personalizado") {
    const nota = document.querySelector('.dato-cliente[name="envio_nota"]');
    const texto = nota && nota.value.trim();
    return { monto: escrito, nombre: texto ? "Envío · " + texto : "Envío a domicilio" };
  }
  const precio = parseFloat(marcado.dataset.precio) || 0;
  return { monto: escrito > 0 ? escrito : precio,
           nombre: marcado.dataset.nombre || "Envío a domicilio" };
}

function pintarDesglose() {
  if (!desglose) return;
  // Plantas del catálogo + los renglones libres de "planta personalizada"
  // (28/09/2026): estos últimos solo cambian con un reload del servidor
  // (se agregan de a uno, por POST), así que su total ya viene fijo en el
  // dataset; lo que se recalcula en vivo aquí son los cargos.
  // El ITBMS del carrito (F2, 6/10/2026) también viene fijo del servidor:
  // solo cambia al agregar/quitar productos, que recargan la página.
  let total = (parseFloat(desglose.dataset.plantas) || 0)
    + (parseFloat(desglose.dataset.itbms) || 0)
    + (parseFloat(desglose.dataset.personalizada) || 0);
  for (const clave of ["envio", "instalacion"]) {
    const fila = document.getElementById("linea-" + clave);
    let monto;
    if (clave === "envio") {
      const envio = envioEnVivo();
      monto = envio.monto;
      if (fila) fila.querySelector("span").textContent = envio.nombre;
    } else {
      const campo = document.querySelector(`.dato-cliente[name="${clave}"]`);
      monto = campo ? Math.max(numeroDe(campo, 0), 0) : 0;
    }
    if (fila) {
      fila.style.display = monto > 0 ? "" : "none";
      fila.querySelector("b").textContent = "$" + monto.toFixed(2);
    }
    total += monto;
  }
  const totalFinal = document.getElementById("total-final");
  if (totalFinal) totalFinal.textContent = "$" + total.toFixed(2);
}
if (desglose) {
  const campos = ['.dato-cliente[name="envio"]', '.dato-cliente[name="instalacion"]',
                  '.dato-cliente[name="envio_nota"]', 'input[name="envio_opcion"]'];
  for (const selector of campos) {
    for (const campo of document.querySelectorAll(selector)) {
      campo.addEventListener("input", pintarDesglose);
      if (campo.type === "radio") campo.addEventListener("change", pintarDesglose);
    }
  }
}

/* Renglones repetibles: los servicios de una cotización de servicio
   (párrafo + monto) y los renglones libres de la personalizada (párrafo +
   cantidad + precio). En los dos el párrafo crece con lo que se escribe,
   el botón "+ Añadir" clona un renglón vacío y el subtotal se recalcula en
   vivo; todo se guarda en el borrador del servidor con sincronizarCliente. */

function crecerTexto(area) {
  area.style.height = "auto";
  area.style.height = area.scrollHeight + "px";
}

function numeroDe(campo, defecto) {
  if (!campo) return defecto;
  const valor = parseFloat(campo.value.replace(",", "."));
  return isNaN(valor) ? defecto : valor;
}

function activarRenglones(contenedor, idBoton, idSubtotal, importeDe) {
  function alternarQuitar() {
    // Con un solo renglón no hay nada que quitar: el botón sobra.
    const renglones = contenedor.querySelectorAll(".servicio");
    for (const renglon of renglones) {
      renglon.querySelector(".quitar-servicio").hidden = renglones.length === 1;
    }
  }

  function pintarSubtotal() {
    const salida = document.getElementById(idSubtotal);
    if (!salida) return;
    let total = 0;
    for (const renglon of contenedor.querySelectorAll(".servicio")) {
      total += importeDe(renglon);
    }
    salida.textContent = "$" + total.toFixed(2);
  }

  function nuevoRenglon() {
    const copia = contenedor.querySelector(".servicio").cloneNode(true);
    for (const campo of copia.querySelectorAll("textarea, input")) campo.value = "";
    // Todas las áreas (título y descripción): sin el alto heredado del clon.
    for (const area of copia.querySelectorAll("textarea")) {
      area.removeAttribute("style");
    }
    contenedor.appendChild(copia);
    alternarQuitar();
    for (const area of copia.querySelectorAll("textarea")) crecerTexto(area);
    copia.querySelector("textarea").focus();
  }

  for (const area of contenedor.querySelectorAll("textarea")) crecerTexto(area);
  pintarSubtotal();
  alternarQuitar();

  contenedor.addEventListener("input", evento => {
    if (evento.target.tagName === "TEXTAREA") crecerTexto(evento.target);
    if (evento.target.tagName === "INPUT") pintarSubtotal();
    sincronizarCliente();
  });

  contenedor.addEventListener("click", evento => {
    const boton = evento.target.closest(".quitar-servicio");
    // El último renglón no se quita (su botón está oculto): siempre queda
    // dónde escribir.
    if (!boton || contenedor.querySelectorAll(".servicio").length === 1) return;
    boton.closest(".servicio").remove();
    alternarQuitar();
    pintarSubtotal();
    sincronizarCliente();
  });

  const boton = document.getElementById(idBoton);
  if (boton) boton.addEventListener("click", nuevoRenglon);
}

if (contenedorServicios) {
  activarRenglones(contenedorServicios, "anadir-servicio", "subtotal-servicios",
                   renglon => Math.max(
                     numeroDe(renglon.querySelector('[name="servicio_monto"]'), 0), 0));
}

if (contenedorRenglones) {
  activarRenglones(contenedorRenglones, "anadir-renglon", "subtotal-renglones",
                   renglon => Math.max(
                     numeroDe(renglon.querySelector('[name="renglon_cantidad"]'), 1), 0) *
                     Math.max(
                       numeroDe(renglon.querySelector('[name="renglon_precio"]'), 0), 0));
}

/* ---------------------------------------------------------------------
   Pantalla de EDITAR una cotización (30/09/2026): las plantas se pueden
   quitar con una X (nunca hace falta bajar la cantidad a 0 a mano) y se
   pueden AÑADIR con un buscador propio -ids distintos de busca-venta/
   resultados-venta de arriba, a propósito: ese buscador agrega al
   carrito compartido por empleada de Nueva venta, y esta pantalla edita
   UNA orden que ya existe, de un tirón. Agregar acá arma la fila con JS
   (clonando <template id="plantilla-planta">) en vez de recargar la
   página: un reload perdería cualquier otro cambio a medio escribir,
   porque esta pantalla no guarda borrador (ver sincronizarCliente más
   arriba). La cuenta de la derecha (el total y "qué cambió") también se
   recalcula acá, con los mismos números con los que Python la pintó. --- */

const listaPlantas = document.getElementById("listaPlantas");
const cuentaEditar = document.getElementById("cuenta-editar");

function pintarSubtotalPlanta(fila) {
  const cant = Math.max(numeroDe(fila.querySelector("[data-planta-cant]"), 0), 0);
  const campoPrecio = fila.querySelector("[data-planta-precio]");
  const precio = campoPrecio ? Math.max(numeroDe(campoPrecio, 0), 0) : 0;
  const importe = cant * precio;
  const salida = fila.querySelector("[data-planta-sub]");
  if (salida) salida.textContent = "$" + importe.toFixed(2);
  return importe;
}

function avisoSinPlantas() {
  // El mensaje "búscala abajo" solo tiene sentido con la lista vacía.
  const aviso = document.getElementById("plantas-vacio");
  if (aviso && listaPlantas) aviso.hidden = listaPlantas.children.length > 0;
}

function recalcularCuentaEditar() {
  if (!cuentaEditar) return;
  let plantas = 0;
  if (listaPlantas) {
    for (const fila of listaPlantas.querySelectorAll(".servicio")) {
      plantas += pintarSubtotalPlanta(fila);
    }
  }
  const subtotalPlantas = document.getElementById("subtotal-plantas");
  if (subtotalPlantas) subtotalPlantas.textContent = "$" + plantas.toFixed(2);

  let servicios = 0;
  if (contenedorServicios) {
    for (const s of contenedorServicios.querySelectorAll(".servicio")) {
      servicios += Math.max(numeroDe(s.querySelector('[name="servicio_monto"]'), 0), 0);
    }
  }
  let renglones = 0;
  if (contenedorRenglones) {
    for (const r of contenedorRenglones.querySelectorAll(".servicio")) {
      renglones += Math.max(
        numeroDe(r.querySelector('[name="renglon_cantidad"]'), 1), 0) *
        Math.max(numeroDe(r.querySelector('[name="renglon_precio"]'), 0), 0);
    }
  }
  // El mismo desglose de envío que ya usa Nueva venta (envioEnVivo, arriba
  // en este archivo): no se reescribe, solo se suma acá también.
  const envio = typeof envioEnVivo === "function" ? envioEnVivo().monto : 0;
  const instalacion = Math.max(
    numeroDe(document.querySelector('.dato-cliente[name="instalacion"]'), 0), 0);
  const mantenimiento = Math.max(
    numeroDe(document.querySelector('.dato-cliente[name="mantenimiento"]'), 0), 0);
  const cargos = envio + instalacion + mantenimiento;
  const total = plantas + servicios + renglones + cargos;

  const pintar = (id, valor) => {
    const el = document.getElementById(id);
    if (el) el.textContent = "$" + valor.toFixed(2);
  };
  pintar("cuenta-plantas", plantas);
  pintar("cuenta-servicios", servicios);
  pintar("cuenta-renglones", renglones);
  pintar("cuenta-cargos", cargos);
  pintar("cuenta-total", total);

  const cambio = document.getElementById("cuenta-cambio");
  if (cambio) {
    const original = parseFloat(cuentaEditar.dataset.original) || 0;
    const diferencia = total - original;
    if (Math.abs(diferencia) < 0.005) {
      cambio.textContent = "Sin cambios todavía. Así está guardada en Odoo.";
    } else {
      const signo = diferencia > 0 ? "+" : "−";
      cambio.innerHTML = "Cambia de <b>$" + original.toFixed(2) + "</b> a <b>$" +
        total.toFixed(2) + "</b> · " + signo + "$" + Math.abs(diferencia).toFixed(2);
    }
  }
}

function quitarPlanta(fila) {
  fila.remove();
  avisoSinPlantas();
  recalcularCuentaEditar();
}

function agregarPlantaEditar(producto) {
  if (!listaPlantas) return;
  // Si ya está en la lista, sube la cantidad en vez de duplicar la fila.
  const existente = [...listaPlantas.querySelectorAll(".servicio")].find(
    fila => fila.querySelector('[name="planta_id"]').value === String(producto.id));
  if (existente) {
    const campoCant = existente.querySelector("[data-planta-cant]");
    campoCant.value = Math.max(numeroDe(campoCant, 0), 0) + 1;
    recalcularCuentaEditar();
    existente.scrollIntoView({block: "center", behavior: "smooth"});
    return;
  }
  const plantilla = document.getElementById("plantilla-planta");
  if (!plantilla) return;
  const fila = plantilla.content.firstElementChild.cloneNode(true);
  fila.querySelector('[name="planta_id"]').value = producto.id;
  fila.querySelector('[name="planta_nombre"]').value = producto.nombre;
  fila.querySelector("[data-planta-nombre]").textContent = producto.nombre;
  const campoPrecio = fila.querySelector("[data-planta-precio]");
  if (campoPrecio) campoPrecio.value = producto.precio_num.toFixed(2);
  listaPlantas.appendChild(fila);
  avisoSinPlantas();
  recalcularCuentaEditar();
  fila.scrollIntoView({block: "center", behavior: "smooth"});
}

if (listaPlantas) {
  listaPlantas.addEventListener("click", evento => {
    const boton = evento.target.closest(".quitar-servicio");
    if (boton) quitarPlanta(boton.closest(".servicio"));
  });
}

if (cuentaEditar) {
  // Delegado en document (no en cada contenedor): servicios, renglones,
  // plantas y los cargos (envío/instalación/mantenimiento, plegados) son
  // secciones distintas y todas mueven el total.
  document.addEventListener("input", recalcularCuentaEditar);
  document.addEventListener("change", recalcularCuentaEditar);
  recalcularCuentaEditar();
}

const entradaBuscaPlanta = document.getElementById("busca-planta-editar");
const resultadosPlanta = document.getElementById("resultados-planta-editar");
let temporizadorBuscaPlanta = null;

function pintarResultadosPlanta(lista, q) {
  if (!lista.length) {
    resultadosPlanta.innerHTML =
      '<p class="nada-venta">Nada con "' + escaparHtml(q) + '". Prueba otro nombre o el SKU.</p>';
    return;
  }
  resultadosPlanta.innerHTML = lista.map(p => `
    <button class="planta planta-boton" type="button"
            data-id="${p.id}" data-nombre="${escaparHtml(p.nombre)}" data-precio="${p.precio_num}">
      <div class="foto">🪴<img src="/venta/foto/${p.id}" alt="" loading="lazy" onerror="this.remove()"></div>
      <div class="info"><b>${escaparHtml(p.nombre)}</b><span>${escaparHtml(p.sku)}</span>
        <span class="precio"><b>${escaparHtml(p.precio)}</b></span></div>
      <div class="agregar-venta">+</div>
    </button>`).join("");
}

if (entradaBuscaPlanta && resultadosPlanta) {
  entradaBuscaPlanta.addEventListener("input", () => {
    clearTimeout(temporizadorBuscaPlanta);
    const q = entradaBuscaPlanta.value.trim();
    if (!q) {
      resultadosPlanta.innerHTML = "";
      return;
    }
    temporizadorBuscaPlanta = setTimeout(async () => {
      try {
        // solo_plantas=1 (F2, 6/10/2026): la pantalla de editar arma su
        // cuenta en el navegador y no sabe de ITBMS, así que no ofrece
        // macetas ni insumos — si algún día los suma, que sume también
        // el impuesto o el total le mentiría a la empleada.
        const respuesta = await fetch("/venta/buscar?solo_plantas=1&q=" + encodeURIComponent(q));
        const datos = await respuesta.json();
        if (entradaBuscaPlanta.value.trim() !== q) return; // ya escribió otra cosa
        if (datos.error) {
          resultadosPlanta.innerHTML = '<p class="nada-venta">' + escaparHtml(datos.error) + "</p>";
          return;
        }
        pintarResultadosPlanta(datos.resultados, q);
      } catch (e) {
        resultadosPlanta.innerHTML = '<p class="nada-venta">Sin conexión. Intenta de nuevo.</p>';
      }
    }, 300);
  });
  resultadosPlanta.addEventListener("click", evento => {
    const boton = evento.target.closest("[data-id]");
    if (!boton) return;
    agregarPlantaEditar({
      id: boton.dataset.id, nombre: boton.dataset.nombre,
      precio_num: parseFloat(boton.dataset.precio) || 0,
    });
    entradaBuscaPlanta.value = "";
    resultadosPlanta.innerHTML = "";
    entradaBuscaPlanta.focus();
  });
}
