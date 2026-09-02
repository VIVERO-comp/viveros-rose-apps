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

function pintar() {
  const t = normalizar(document.getElementById("busca").value);
  const l = document.getElementById("lista");
  // Lo accionable primero: negativos (error de datos), luego criticos y
  // bajos de menor a mayor, y las agotadas al final (con 100+ en cero,
  // enterraban a las que se estan acabando).
  const rango = p => (p.f < 0 ? 0 : (p.q > 0 ? 1 : 2));
  l.innerHTML = plantas
    .filter(p => (catActiva === "Todas" || p.c === catActiva) && normalizar(p.n).includes(t))
    .sort((a, b) => rango(a) - rango(b) || a.q - b.q)
    .map(p => {
      const [et, cl] = estado(p);
      const negativo = p.f < 0;
      return `<div class="planta ${!negativo && p.q <= 0 ? "agotada" : ""}" id="planta-${p.sku}" data-planta="${p.sku}">
        <div class="foto">${p.e}</div>
        <div class="info"><b>${p.n}</b><span>${p.c}</span></div>
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
    // aplicado o sin_cambio: recargar trae el stock fresco de Odoo y
    // recalcula score y alertas en el servidor.
    sessionStorage.setItem("toast-pendiente",
      "✓ " + editando.n + " ajustado a " + nueva + " en Odoo");
    location.href = "/?refrescar=1";
  } catch (e) {
    mostrarErrorEdicion("Sin conexión. Intenta de nuevo.");
  } finally {
    guardando = false;
    boton.textContent = "Guardar en Odoo";
  }
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
  if (fila) abrirEditar(fila.dataset.planta);
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

const toastPendiente = sessionStorage.getItem("toast-pendiente");
if (toastPendiente) {
  sessionStorage.removeItem("toast-pendiente");
  toast(toastPendiente);
}

pintar();
