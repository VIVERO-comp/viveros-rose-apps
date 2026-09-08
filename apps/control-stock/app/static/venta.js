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
  // El Enter del teclado sigue funcionando (el form hace GET /venta?q=...),
  // pero ya nadie lo necesita.
}
