/* JS del prototipo aprobado, adaptado a datos reales (window.DATOS) y a los
   POSTs de la app. Pestañas, buscador, panel de alertas, modal de ajuste. */

const DATOS = window.DATOS || { plantas: [], umbral: 3, alertas: [] };
const plantas = DATOS.plantas;
const UMBRAL = DATOS.umbral;
let catActiva = "Todas";
let editando = null;
let guardando = false;

function estado(p) {
  // Físico negativo: error de datos a corregir ya — pesa más que todo.
  if (p.f < 0) return ["Negativo", "b-critico"];
  if (p.q <= 0) return ["Agotada", "b-agotado"];
  if (p.q < UMBRAL) return ["Crítico", "b-critico"];
  if (p.q < UMBRAL * 2) return ["Bajo", "b-bajo"];
  return ["OK", "b-ok"];
}

function normalizar(texto) {
  return (texto || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

// El precio de venta de Odoo tal cual, igual que en la tienda online.
// Viene ya formateado del servidor (p.po).
function lineaPrecio(p) {
  return p.po ? `<b>${p.po}</b>` : "Precio pendiente";
}

// Si la foto de Cloudinary falla (URL rota, CDN con 404 cacheado), se
// intenta UNA vez la foto de Odoo (/stock/foto) antes de caer al emoji.
function fotoRespaldo(img, sku) {
  if (!img.dataset.respaldo && !img.src.startsWith(location.origin + "/stock/foto/")) {
    img.dataset.respaldo = "1";
    img.src = "/stock/foto/" + sku;
  } else {
    img.remove();
  }
}

function pintar() {
  const t = normalizar(document.getElementById("busca").value);
  const l = document.getElementById("lista");
  // De menor a mayor cantidad, estricto: los negativos (por su fisico real)
  // primero, luego las agotadas en 0, luego 1, 2, 3... Asi lo mas urgente
  // siempre queda arriba.
  const cantidad = p => (p.f < 0 ? p.f : p.q);
  // "Solo con alerta": negativos, criticos y bajos (lo mismo que alerta la
  // campanita); deja fuera las agotadas en 0 y las que estan OK.
  const esAlerta = p => p.f < 0 || (p.q > 0 && p.q < UMBRAL * 2);
  // "En 0": las que ya no tienen nada que vender (incluye fisico negativo).
  const pasaCategoria = p =>
    catActiva === "Todas" ? true :
    catActiva === "__alerta__" ? esAlerta(p) :
    catActiva === "__cero__" ? p.q <= 0 :
    p.c === catActiva;
  l.innerHTML = plantas
    .filter(p => pasaCategoria(p) && normalizar(p.n).includes(t))
    .sort((a, b) => cantidad(a) - cantidad(b))
    .map(p => {
      const [et, cl] = estado(p);
      const negativo = p.f < 0;
      // La foto se pinta ENCIMA del emoji: si no carga, fotoRespaldo prueba
      // la de Odoo y recien despues queda el emoji; el layout no se mueve.
      const foto = p.img ? `<img src="${p.img}" alt="" loading="lazy" onerror="fotoRespaldo(this,'${p.sku}')">` : "";
      const precio = lineaPrecio(p);
      return `<div class="planta ${!negativo && p.q <= 0 ? "agotada" : ""}" id="planta-${p.sku}" data-planta="${p.sku}">
        <div class="foto">${p.e}${foto}</div>
        <div class="info"><b>${p.n}</b><span>${p.c}</span><span class="precio">${precio}</span></div>
        <div class="qty"><b>${negativo ? p.f : p.q}</b><span class="badge ${cl}">${et}</span></div>
      </div>`;
    }).join("") || '<p style="color:var(--texto-suave);font-size:13px;text-align:center;padding:30px 0">Sin resultados</p>';
}
function filtrar() { pintar(); }

function chip(el, c) {
  document.querySelectorAll(".chip").forEach(x => x.classList.remove("on"));
  el.classList.add("on");
  catActiva = c;
  pintar();
}

function tab(id, btn) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("activa"));
  document.getElementById("tab-" + id).classList.add("activa");
  document.querySelectorAll("nav button").forEach(b => b.classList.remove("on"));
  btn.classList.add("on");
}

function irStock(cat) {
  tab("stock", document.querySelectorAll("nav button")[1]);
  document.querySelectorAll(".chip").forEach(x => {
    x.classList.toggle("on", x.textContent === cat);
  });
  catActiva = cat;
  pintar();
}

function togglePanel() {
  document.getElementById("panel").classList.toggle("abierto");
}

function irProducto(sku) {
  document.getElementById("panel").classList.remove("abierto");
  document.getElementById("busca").value = "";
  irStock("Todas");
  document.querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x.textContent === "Todas"));
  setTimeout(() => {
    const el = document.getElementById("planta-" + sku);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("resaltada");
      setTimeout(() => el.classList.remove("resaltada"), 2600);
    }
  }, 120);
}

/* ---------- modal modificar stock ---------- */
function abrirEditar(sku) {
  const p = plantas.find(x => x.sku === sku);
  if (!p) return;
  editando = p;
  document.getElementById("edit-nombre").textContent = p.n;
  const foto = document.getElementById("edit-foto");
  foto.textContent = p.e;
  if (p.img) {
    const img = document.createElement("img");
    img.src = p.img;
    img.alt = "";
    img.onerror = () => fotoRespaldo(img, p.sku);
    foto.appendChild(img);
  }
  document.getElementById("edit-precio").innerHTML = lineaPrecio(p);
  // El conteo y el ajuste trabajan sobre lo FISICO (es lo que Odoo fija con
  // el ajuste de inventario); el disponible se muestra aparte porque es lo
  // que ve la tienda.
  document.getElementById("edit-actual").textContent =
    "Físico: " + p.f + " · disponible para vender: " + p.q + " · " + p.c;
  document.getElementById("edit-input").value = Math.max(p.f, 0);
  mostrarErrorEdicion("");
  document.getElementById("modal-editar").classList.add("abierto");
}
function cerrarEditar() {
  document.getElementById("modal-editar").classList.remove("abierto");
  editando = null;
}
function cambiarQty(d) {
  const inp = document.getElementById("edit-input");
  inp.value = Math.max(0, (parseInt(inp.value) || 0) + d);
}
function mostrarErrorEdicion(mensaje) {
  const el = document.getElementById("edit-error");
  el.textContent = mensaje;
  el.classList.toggle("visible", Boolean(mensaje));
}
function toast(mensaje) {
  const el = document.getElementById("toast");
  el.textContent = mensaje;
  el.classList.add("visible");
  setTimeout(() => el.classList.remove("visible"), 2600);
}

async function guardarStock() {
  if (!editando || guardando) return;
  const nueva = parseInt(document.getElementById("edit-input").value);
  if (isNaN(nueva) || nueva < 0) {
    mostrarErrorEdicion("Escribe una cantidad válida (0 o más).");
    return;
  }
  guardando = true;
  const boton = document.getElementById("btn-guardar");
  boton.textContent = "Guardando…";
  try {
    const respuesta = await fetch("/ajustar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `esperada` es lo FISICO que el empleado tiene en pantalla: el candado
      // para que nadie pise a ciegas un stock que cambió en el medio (Odoo
      // compara contra lo físico, no contra el disponible).
      body: JSON.stringify({ sku: editando.sku, cantidad: nueva, esperada: editando.f }),
    });
    const r = await respuesta.json();
    if (!respuesta.ok) {
      mostrarErrorEdicion(r.mensaje || "No se pudo guardar. Intenta de nuevo.");
      return;
    }
    if (r.resultado === "conflicto") {
      editando.f = r.anterior;
      document.getElementById("edit-actual").textContent =
        "Físico: " + r.anterior + " · " + editando.c;
      mostrarErrorEdicion("El stock cambió en Odoo: ahora hay " + r.anterior +
        " físicas. Revisa la cantidad y guarda de nuevo.");
      pintar();
      return;
    }
    if (r.resultado === "no_existe") {
      mostrarErrorEdicion("Este producto ya no existe en Odoo. Actualiza la lista.");
      return;
    }
    if (r.resultado !== "aplicado" && r.resultado !== "sin_cambio") {
      // odoo_error u otro resultado desconocido: NADA se escribió en Odoo.
      // Nunca celebrar un ajuste que falló (pasó con un producto consumible:
      // el toast decía "ajustado" y el stock seguía igual).
      mostrarErrorEdicion("Odoo rechazó el ajuste" +
        (r.detalle ? ": " + r.detalle : ". Intenta de nuevo o avisa al encargado."));
      return;
    }
    // aplicado o sin_cambio: recargar trae el stock fresco de Odoo y
    // recalcula score y alertas en el servidor. La URL conserva pestaña,
    // categoría y búsqueda para volver exactamente donde estaba el empleado.
    sessionStorage.setItem("toast-pendiente",
      "✓ " + editando.n + " ajustado a " + nueva + " en Odoo");
    const destino = new URLSearchParams({ refrescar: "1" });
    const tabActiva = document.querySelector(".tab.activa");
    if (tabActiva) destino.set("tab", tabActiva.id.replace("tab-", ""));
    if (catActiva !== "Todas") destino.set("cat", catActiva);
    const busqueda = document.getElementById("busca").value.trim();
    if (busqueda) destino.set("q", busqueda);
    location.href = "/?" + destino.toString();
  } catch (e) {
    mostrarErrorEdicion("Sin conexión. Intenta de nuevo.");
  } finally {
    guardando = false;
    boton.textContent = "Guardar en Odoo";
  }
}

/* ---------- modal foto de producto (ver, descargar, cambiar) ---------- */
let fotoSku = null;
let subiendoFoto = false;

function pintarFotoGrande(p) {
  const caja = document.getElementById("foto-grande");
  caja.textContent = p.e;
  if (p.imgG) {
    const img = document.createElement("img");
    img.src = p.imgG;
    img.alt = p.n;
    // Si la grande falla, se intenta la miniatura; si tampoco, queda el emoji.
    img.onerror = () => {
      if (p.img && img.src !== p.img) { img.src = p.img; } else { img.remove(); }
    };
    caja.appendChild(img);
  }
  const descargar = document.getElementById("btn-descargar");
  descargar.hidden = !p.imgD;
  if (p.imgD) descargar.href = p.imgD;
}

function abrirFoto(sku) {
  const p = plantas.find(x => x.sku === sku);
  if (!p) return;
  fotoSku = sku;
  document.getElementById("foto-nombre").textContent = p.n;
  // Sin credenciales de Cloudinary en el servidor no hay pincel: el modal
  // queda solo de zoom y descarga.
  document.getElementById("btn-pincel").hidden = !DATOS.puedeSubir;
  document.getElementById("foto-nota").hidden = !DATOS.puedeSubir;
  mostrarErrorFoto("");
  pintarFotoGrande(p);
  document.getElementById("modal-foto").classList.add("abierto");
}
function cerrarFoto() {
  if (subiendoFoto) return; // no cerrar a mitad de subida
  document.getElementById("modal-foto").classList.remove("abierto");
  fotoSku = null;
}
function mostrarErrorFoto(mensaje) {
  const el = document.getElementById("foto-error");
  el.textContent = mensaje;
  el.classList.toggle("visible", Boolean(mensaje));
}

async function subirFoto(input) {
  const archivo = input.files && input.files[0];
  input.value = ""; // permite volver a elegir el mismo archivo
  if (!archivo || !fotoSku || subiendoFoto) return;
  const p = plantas.find(x => x.sku === fotoSku);
  if (!p) return;
  subiendoFoto = true;
  const boton = document.getElementById("btn-pincel");
  const texto = boton.innerHTML;
  boton.disabled = true;
  boton.textContent = "Subiendo…";
  mostrarErrorFoto("");
  try {
    const cuerpo = new FormData();
    cuerpo.append("archivo", archivo);
    const respuesta = await fetch("/fotos/" + encodeURIComponent(fotoSku), {
      method: "POST",
      body: cuerpo,
    });
    const r = await respuesta.json();
    if (!respuesta.ok) {
      mostrarErrorFoto(r.mensaje || "No se pudo subir la foto. Intenta de nuevo.");
      return;
    }
    p.img = r.img;
    p.imgG = r.grande;
    p.imgD = r.descarga;
    pintarFotoGrande(p);
    pintar();
    toast("✓ Foto de " + p.n + " actualizada");
  } catch (e) {
    mostrarErrorFoto("Sin conexión. Intenta de nuevo.");
  } finally {
    subiendoFoto = false;
    boton.disabled = false;
    boton.innerHTML = texto;
  }
}

/* ---------- modal agregar planta (sugerencia por WhatsApp) ---------- */
// Solo un link wa.me con el mensaje pre-armado: no toca Odoo ni el servidor.
const WHATSAPP_NEGOCIO = "50765673062";

function abrirAgregar() {
  document.getElementById("agregar-nombre").value = "";
  document.getElementById("agregar-cantidad").value = "1";
  document.getElementById("agregar-comentario").value = "";
  mostrarErrorAgregar("");
  document.getElementById("modal-agregar").classList.add("abierto");
}
function cerrarAgregar() {
  document.getElementById("modal-agregar").classList.remove("abierto");
}
function mostrarErrorAgregar(mensaje) {
  const el = document.getElementById("agregar-error");
  el.textContent = mensaje;
  el.classList.toggle("visible", Boolean(mensaje));
}
function enviarAgregar() {
  const nombre = document.getElementById("agregar-nombre").value.trim();
  const cantidad = parseInt(document.getElementById("agregar-cantidad").value);
  const comentario = document.getElementById("agregar-comentario").value.trim();
  if (!nombre) {
    mostrarErrorAgregar("Escribe el nombre de la planta.");
    return;
  }
  if (isNaN(cantidad) || cantidad < 1) {
    mostrarErrorAgregar("Escribe una cantidad válida (1 o más).");
    return;
  }
  let texto = `Nueva planta sugerida: ${nombre}, cantidad ${cantidad}.`;
  if (comentario) texto += ` ${comentario}`;
  window.open(`https://wa.me/${WHATSAPP_NEGOCIO}?text=${encodeURIComponent(texto)}`, "_blank");
  cerrarAgregar();
  toast("✓ Se abrió WhatsApp con la sugerencia");
}

/* ---------- animación de inicio (una vez por sesión) ---------- */
(function () {
  const intro = document.getElementById("intro");
  if (!intro) return;
  if (sessionStorage.getItem("intro-vista")) { intro.remove(); return; }
  sessionStorage.setItem("intro-vista", "1");
  intro.hidden = false;
  const brote = document.getElementById("intro-brote");
  const texto = document.getElementById("intro-texto");

  setTimeout(() => brote.classList.add("viva"), 150);
  const letras = "VIVERO ROSE".split("");
  setTimeout(() => {
    texto.innerHTML = letras.map((l, i) =>
      `<span style="animation-delay:${i * 0.05}s">${l}</span>`).join("");
  }, 1650);
  setTimeout(() => {
    intro.classList.add("fuera");
    setTimeout(() => intro.remove(), 550);
  }, 1650 + letras.length * 50 + 800);
})();

/* ---------- eventos por delegación (más confiable en móvil) ---------- */
document.getElementById("lista").addEventListener("click", e => {
  const fila = e.target.closest(".planta");
  if (!fila) return;
  // Tocar la FOTO abre el modal de foto (ver en grande / cambiar con el
  // pincel); tocar el resto de la fila sigue abriendo el ajuste de stock.
  if (e.target.closest(".foto")) {
    abrirFoto(fila.dataset.planta);
  } else {
    abrirEditar(fila.dataset.planta);
  }
});
document.getElementById("alertas-lista").addEventListener("click", e => {
  if (e.target.closest("form")) return; // el botón Atendida hace su POST
  const item = e.target.closest("[data-ir]");
  if (item) irProducto(item.dataset.ir);
});
document.getElementById("panel").addEventListener("click", e => {
  if (e.target.id === "panel") togglePanel();
});
document.getElementById("modal-editar").addEventListener("click", e => {
  if (e.target.id === "modal-editar") cerrarEditar();
});
document.getElementById("modal-foto").addEventListener("click", e => {
  if (e.target.id === "modal-foto") cerrarFoto();
});
document.getElementById("modal-agregar").addEventListener("click", e => {
  if (e.target.id === "modal-agregar") cerrarAgregar();
});

const toastPendiente = sessionStorage.getItem("toast-pendiente");
if (toastPendiente) {
  sessionStorage.removeItem("toast-pendiente");
  toast(toastPendiente);
}

// Los enlaces de la página /venta y la recarga tras guardar un ajuste
// vuelven con ?tab= (y opcionalmente cat= y q=) para aterrizar exactamente
// donde estaba el empleado (las pestañas son 100% del navegador).
const parametros = new URLSearchParams(location.search);
const tabPedida = parametros.get("tab");
if (tabPedida === "stock" || tabPedida === "inv") {
  tab(tabPedida, document.querySelectorAll("nav button")[tabPedida === "stock" ? 1 : 2]);
}
const catPedida = parametros.get("cat");
if (catPedida) {
  catActiva = catPedida;
  document.querySelectorAll(".chip").forEach(x =>
    x.classList.toggle("on", x.dataset.cat === catPedida));
}
const buscaPedida = parametros.get("q");
if (buscaPedida) document.getElementById("busca").value = buscaPedida;

pintar();
