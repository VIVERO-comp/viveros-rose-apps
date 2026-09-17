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
  contenedorResultados.innerHTML = lista.map(p => `
    <form method="post" action="/venta/carrito/agregar" class="sin-margen">
      <input type="hidden" name="producto_id" value="${p.id}">
      <input type="hidden" name="cantidad" value="1">
      <input type="hidden" name="q" value="${escaparHtml(q)}">
      <input type="hidden" name="volver" value="${escaparHtml(volver)}">
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

function sincronizarCliente() {
  const nombre = entradaNombre ? entradaNombre.value : "";
  const celular = entradaCelular ? entradaCelular.value : "";
  const ocultoNombre = document.getElementById("post-cliente");
  const ocultoCelular = document.getElementById("post-celular");
  if (ocultoNombre) ocultoNombre.value = nombre;
  if (ocultoCelular) ocultoCelular.value = celular;
  clearTimeout(temporizadorBorrador);
  temporizadorBorrador = setTimeout(() => {
    const datos = new FormData();
    datos.append("cliente", nombre);
    datos.append("celular", celular);
    // En las cotizaciones de servicio viajan también los renglones ya
    // escritos: agregar o quitar una planta recarga la página y sin esto
    // se perderían los párrafos.
    if (contenedorServicios) {
      datos.append("servicios", "1");
      for (const renglon of contenedorServicios.querySelectorAll(".servicio")) {
        datos.append("servicio_texto", renglon.querySelector("textarea").value);
        datos.append("servicio_monto",
                     renglon.querySelector('input[name="servicio_monto"]').value);
      }
    }
    fetch("/venta/borrador", { method: "POST", body: datos }).catch(() => {});
  }, 400);
}

for (const entrada of [entradaNombre, entradaCelular]) {
  if (entrada) entrada.addEventListener("input", sincronizarCliente);
}

/* Renglones de servicio (cotizaciones de servicio): el párrafo crece con
   lo que se escribe, "+ Añadir otro servicio" clona un renglón vacío y el
   subtotal se recalcula en vivo. Todo lo digitado se guarda en el
   borrador del servidor con el mismo sincronizarCliente() de arriba. */

function crecerTexto(area) {
  area.style.height = "auto";
  area.style.height = area.scrollHeight + "px";
}

function pintarSubtotalServicios() {
  const salida = document.getElementById("subtotal-servicios");
  if (!salida || !contenedorServicios) return;
  let total = 0;
  for (const campo of contenedorServicios.querySelectorAll('input[name="servicio_monto"]')) {
    const monto = parseFloat(campo.value.replace(",", ""));
    if (!isNaN(monto) && monto > 0) total += monto;
  }
  salida.textContent = "$" + total.toFixed(2);
}

function nuevoRenglonServicio() {
  const primero = contenedorServicios.querySelector(".servicio");
  const copia = primero.cloneNode(true);
  copia.querySelector("textarea").value = "";
  copia.querySelector("textarea").removeAttribute("style");
  copia.querySelector('input[name="servicio_monto"]').value = "";
  contenedorServicios.appendChild(copia);
  const area = copia.querySelector("textarea");
  crecerTexto(area);
  area.focus();
}

if (contenedorServicios) {
  for (const area of contenedorServicios.querySelectorAll("textarea")) crecerTexto(area);
  pintarSubtotalServicios();

  contenedorServicios.addEventListener("input", evento => {
    if (evento.target.tagName === "TEXTAREA") crecerTexto(evento.target);
    if (evento.target.name === "servicio_monto") pintarSubtotalServicios();
    sincronizarCliente();
  });

  contenedorServicios.addEventListener("click", evento => {
    const boton = evento.target.closest(".quitar-servicio");
    if (!boton) return;
    const renglones = contenedorServicios.querySelectorAll(".servicio");
    if (renglones.length === 1) {
      // El último no se quita: se vacía, para que siempre haya dónde
      // escribir.
      const area = renglones[0].querySelector("textarea");
      area.value = "";
      crecerTexto(area);
      renglones[0].querySelector('input[name="servicio_monto"]').value = "";
    } else {
      boton.closest(".servicio").remove();
    }
    pintarSubtotalServicios();
    sincronizarCliente();
  });

  const botonAnadir = document.getElementById("anadir-servicio");
  if (botonAnadir) botonAnadir.addEventListener("click", nuevoRenglonServicio);
}
