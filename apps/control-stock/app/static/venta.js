/* Buscador en vivo de la página Crear Venta: filtra mientras se escribe,
   igual que el buscador de la pestaña Stock (nadie presiona Enter). Los
   resultados llegan de /venta/buscar (precios ya formateados en el
   servidor) y agregar al carrito sigue siendo un POST normal. */

const entradaBusca = document.getElementById("busca-venta");
const contenedorResultados = document.getElementById("resultados-venta");
let temporizadorBusca = null;

// El proyecto elegido en "Cotizar Proyecto" tiene que viajar con cada POST
// del carrito (agregar, quitar, cambiar cantidad): esos formularios llevan
// su campo oculto y aquí se les pone el valor que está elegido ahora, para
// que agregar una planta no saque la cotización de su proyecto.
const selectorProyecto = document.getElementById("cliente-proyecto");
if (selectorProyecto) {
  selectorProyecto.addEventListener("change", () => {
    const valor = selectorProyecto.value;
    for (const campo of document.querySelectorAll('input[type="hidden"][name="proyecto"]')) {
      campo.value = valor;
    }
    if (contenedorResultados) {
      contenedorResultados.dataset.proyecto = valor;
    }
  });
}

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
  // El proyecto de la cotización viaja con el POST de agregar: sin esto,
  // tocar una planta del buscador en vivo devolvería a la pantalla sin su
  // proyecto y la cotización nacería suelta.
  const proyecto = (contenedorResultados.dataset.proyecto || "");
  const campoProyecto = proyecto
    ? `<input type="hidden" name="proyecto" value="${escaparHtml(proyecto)}">`
    : "";
  if (!lista.length) {
    contenedorResultados.innerHTML =
      '<p class="nada-venta">Nada con "' + escaparHtml(q) + '". Prueba otro nombre o el SKU.</p>';
    return;
  }
  contenedorResultados.innerHTML = lista.map(p => `
    <form method="post" action="/venta/carrito/agregar" class="sin-margen">
      <input type="hidden" name="producto_id" value="${p.id}">
      <input type="hidden" name="cantidad" value="1">
      <input type="hidden" name="volver" value="${escaparHtml(volver)}">
      ${campoProyecto}
      <button class="planta planta-boton" type="submit">
        <div class="foto">🪴<img src="/venta/foto/${p.id}" alt="" loading="lazy" onerror="this.remove()"></div>
        <div class="info"><b>${escaparHtml(p.nombre)}</b><span>${escaparHtml(p.sku)}</span>
          <span class="precio"><b>${escaparHtml(p.precio)}</b></span></div>
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

/* El desglose de Nueva venta (23/09/2026): mientras se escribe un cargo,
   su renglón aparece y el total lo suma — el mismo desglose que va a
   imprimir el PDF. Lo inicial lo pinta el servidor (con el borrador); el
   monto que manda es el que confirma Odoo al crear la orden. */
const desglose = document.getElementById("desglose");
function pintarDesglose() {
  if (!desglose) return;
  let total = parseFloat(desglose.dataset.plantas) || 0;
  for (const clave of ["envio", "instalacion"]) {
    const campo = document.querySelector(`.dato-cliente[name="${clave}"]`);
    const fila = document.getElementById("linea-" + clave);
    const monto = campo ? Math.max(parseFloat(campo.value) || 0, 0) : 0;
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
  for (const clave of ["envio", "instalacion"]) {
    const campo = document.querySelector(`.dato-cliente[name="${clave}"]`);
    if (campo) campo.addEventListener("input", pintarDesglose);
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
