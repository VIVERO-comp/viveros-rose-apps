/* JS del prototipo aprobado, adaptado a datos reales (window.DATOS) y a los
   POSTs de la app. Pestañas, buscador, panel de alertas, modal de ajuste. */

/* El respaldo lleva los mismos campos que manda el servidor, para que nada
   de abajo tenga que preguntar si existen: `destinoTrasCrear` es la recarga
   de Stock de siempre (Python manda la de verdad, que puede ser otra). */
const DATOS = window.DATOS || {
  plantas: [], umbral: 3, alertas: [],
  destinoTrasCrear: "/?refrescar=1&tab=stock&vista=global",
  volverTrasCrear: "",
};
const plantas = DATOS.plantas;
const UMBRAL = DATOS.umbral;
let catActiva = "Todas";
// Vista del stock: "online" son solo las plantas publicadas hoy en
// plantaspanama.com (p.on, espejo de /catalogo-publicado.json del sitio);
// "global" son todas las plantas activas de Odoo. Arranca en online, que es
// lo que ve el cliente. Si no se pudo leer el catálogo del sitio
// (DATOS.sinPublicados), no se adivina: se muestra el global y se avisa.
let vistaStockActiva = DATOS.sinPublicados ? "global" : "online";
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

// Para meter un texto del servidor dentro de un atributo sin romper el
// HTML (un nombre con comillas partía la etiqueta en dos).
function enAtributo(texto) {
  return String(texto == null ? "" : texto)
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function pintar() {
  // Repintar borra las filas: lo que esté a medio guardar se manda AHORA,
  // antes de que el nodo con el número tecleado deje de existir.
  volcarPendientes();
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
  // En "online" solo entran las que el sitio publica hoy; en "global", todas.
  const pasaVista = p => vistaStockActiva === "global" || p.on === true;
  const pasaCategoria = p =>
    catActiva === "Todas" ? true :
    catActiva === "__alerta__" ? esAlerta(p) :
    catActiva === "__cero__" ? p.q <= 0 :
    p.c === catActiva;
  l.innerHTML = plantas
    .filter(p => pasaVista(p) && pasaCategoria(p) && normalizar(p.n).includes(t))
    .sort((a, b) => cantidad(a) - cantidad(b))
    .map(p => {
      const [et, cl] = estado(p);
      const negativo = p.f < 0;
      // La foto se pinta ENCIMA del emoji: si no carga, fotoRespaldo prueba
      // la de Odoo y recien despues queda el emoji; el layout no se mueve.
      const foto = p.img ? `<img src="${p.img}" alt="" loading="lazy" onerror="fotoRespaldo(this,'${p.sku}')">` : "";
      const precio = lineaPrecio(p);
      // Extras de la tarjeta de computadora (.solo-pc, ocultos en el
      // telefono para no tocar la lista movil aprobada): el extracto de la
      // descripcion y el pie con − cantidad + para ajustar el fisico sin
      // salir de la lista: el numero se teclea o se toca, y se guarda solo
      // un momento despues del ultimo toque (A3/A4 del BLOQUE 53).
      const extracto = descripcionDe(p.sku);
      // Solo en Stock global: ahí conviven las publicadas y las que no, y
      // saber cuál es cuál es justo el motivo de tener las dos vistas.
      const marca = (vistaStockActiva === "global" && p.on === false)
        ? '<span class="fuera-linea">No está en la tienda</span>' : "";
      return `<div class="planta ${!negativo && p.q <= 0 ? "agotada" : ""}" id="planta-${p.sku}" data-planta="${p.sku}">
        <div class="foto">${p.e}${foto}</div>
        <div class="info"><b>${p.n}</b><span>${p.c}</span>${marca}${extracto ? `<span class="extracto solo-pc">${extracto}</span>` : ""}<span class="precio">${precio}</span></div>
        <div class="qty"><b>${negativo ? p.f : p.q}</b><span class="badge ${cl}">${et}</span></div>
        <div class="card-pie solo-pc">
          <span class="pie-etiqueta">Físico</span>
          <button class="qty-btn pie-btn" data-paso="-1" aria-label="Quitar uno a ${enAtributo(p.n)}">−</button>
          <input class="pie-valor" type="text" inputmode="numeric" autocomplete="off"
                 value="${enAtributo(p.f)}" aria-label="Cantidad física de ${enAtributo(p.n)}">
          <button class="qty-btn pie-btn" data-paso="1" aria-label="Sumar uno a ${enAtributo(p.n)}">+</button>
          <span class="pie-estado" role="status" hidden></span>
        </div>
      </div>`;
    }).join("") || `<p style="color:var(--texto-suave);font-size:13px;text-align:center;padding:30px 0">Sin resultados${
      vistaStockActiva === "online" ? " en la tienda. Prueba en Stock global." : ""}</p>`;
}
function filtrar() { pintar(); }

const NOTA_VISTA = {
  online: "Las plantas que el cliente ve hoy en plantaspanama.com.",
  global: "Todas las plantas activas en Odoo, estén o no en la tienda.",
};

function vistaStock(v, btn) {
  vistaStockActiva = v;
  document.querySelectorAll("#sub-stock .sub").forEach(b => b.classList.remove("on"));
  (btn || document.querySelector(`#sub-stock .sub[data-vista="${v}"]`)).classList.add("on");
  pintarNotaVista();
  pintar();
  irArriba();
}

function pintarNotaVista() {
  const nota = document.getElementById("sub-nota");
  if (!nota) return;
  // Sin el catálogo del sitio no se puede decir qué está online: se avisa
  // en vez de pintar una lista incompleta como si fuera la buena.
  nota.textContent = DATOS.sinPublicados
    ? `No se pudo saber qué hay publicado en la tienda (${DATOS.sinPublicados}); esto es el stock global.`
    : NOTA_VISTA[vistaStockActiva];
}

function chip(el, c) {
  // Solo los chips de Stock: la pestaña Fichas tiene los suyos propios.
  document.querySelectorAll("#tab-stock .chip").forEach(x => x.classList.remove("on"));
  el.classList.add("on");
  catActiva = c;
  pintar();
  // Lista distinta: se muestra desde el principio. Conservar el scroll aquí
  // dejaría al empleado a media lista de algo que no había visto.
  irArriba();
}

function irArriba() {
  const actual = document.querySelector(".tab.activa");
  if (actual) scrollPorTab[actual.id.replace("tab-", "")] = 0;
  elMain().scrollTop = 0;
}

/* ---------- memoria de scroll por pestaña ----------
   El scroll vive en <main> (la .phone es de altura fija), y es UNO solo
   compartido por todas las pestañas: sin esto, abrir una planta y volver
   —o pasar por Inicio y regresar— deja la lista arriba del todo y hay que
   buscar a mano dónde se estaba. Con 133 plantas eso es inaceptable. Se
   guarda la posición de la pestaña que se deja y se restaura la de la que
   se entra. */
const scrollPorTab = {};

function elMain() { return document.querySelector("main"); }

function recordarScroll() {
  const actual = document.querySelector(".tab.activa");
  if (actual) scrollPorTab[actual.id.replace("tab-", "")] = elMain().scrollTop;
}

function restaurarScroll(id) {
  // En el frame siguiente: si la sección todavía no se pintó, el navegador
  // recorta el scrollTop al alto viejo y queda arriba igual.
  requestAnimationFrame(() => { elMain().scrollTop = scrollPorTab[id] || 0; });
}

function tab(id, btn) {
  const seccion = document.getElementById("tab-" + id);
  if (!seccion) return;
  recordarScroll();
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("activa"));
  seccion.classList.add("activa");
  // "Crear producto" es de STOCK y de nadie más (7/10/2026). Hasta hoy la
  // condición era `id === "detalle"`, así que el único botón negro de la
  // app también salía flotando en Inicio y —el que se vio— en AJUSTES, una
  // pantalla que no crea nada. La regla es la misma que ya cumple su gemelo
  // de la barra del teléfono (#bm-crear) tres renglones abajo: solo Stock.
  document.getElementById("fab-agregar").hidden = id !== "stock";
  // La barra del teléfono: el título acompaña a la pestaña y Crear producto
  // (el único botón negro) solo se ve en Stock. En el detalle la barra se
  // esconde entera: ahí mandan ← Volver y Guardar ficha de .det-top.
  const NOMBRE = { home: "Inicio", stock: "Stock", inv: "Inventario",
                   ajustes: "Ajustes" };
  const barra = document.getElementById("barra-m");
  if (barra) {
    barra.classList.toggle("oculta", id === "detalle");
    document.getElementById("bm-titulo").textContent = NOMBRE[id] || "";
    document.getElementById("bm-crear").style.display = id === "stock" ? "" : "none";
  }
  // El encabezado compartido de computadora (BLOQUE 54, G1). El título lo
  // pinta el SERVIDOR según con qué pestaña se entró (main._titulo_pestana),
  // así que un enlace o un favorito ya llega bien; acá solo se refresca al
  // cambiar de pestaña SIN recargar.
  //
  // Se busca por CLASE a propósito: `_cabecera.html` pinta
  // `<h1 class="cab-titulo">` y NO le pone id, así que la búsqueda POR ID
  // que había acá devolvía null siempre y el título nunca se tocaba — de
  // ahí el «Inicio» encima de Ajustes. Y sin un error en consola: una
  // búsqueda por id que no encuentra nada se calla.
  // (El literal de esa búsqueda vieja no se escribe en este comentario:
  //  la prueba que lo prohíbe es TEXTUAL y nombrarlo la pondría roja.)
  //
  // Solo se escribe cuando la pestaña TIENE nombre: en el detalle de una
  // planta (id "detalle") NOMBRE no tiene entrada, y escribir "" dejaría
  // el encabezado sin título. Ahí se queda el de Stock, que es donde esa
  // ficha vive.
  const cabTitulo = document.querySelector("header.cab .cab-titulo");
  if (cabTitulo && NOMBRE[id]) cabTitulo.textContent = NOMBRE[id];
  document.querySelectorAll("nav button").forEach(b => b.classList.remove("on"));
  // Inventario ya no tiene botón en el menú pero su pestaña sigue viva
  // (?tab=inv): en ese caso el menú queda sin selección y ya.
  const boton = btn || document.querySelector(`nav button[data-tab="${id}"]`);
  if (boton) boton.classList.add("on");
  restaurarScroll(id);
}

function irStock(cat) {
  // Llegar desde Inicio a una categoría es empezar una lista nueva.
  scrollPorTab.stock = 0;
  tab("stock");
  // Desde Inicio siempre al global: el score, los totales y las alertas se
  // calculan sobre todo el inventario, así que la planta buscada puede no
  // estar publicada y en online no aparecería.
  vistaStock("global");
  document.querySelectorAll("#tab-stock .chip").forEach(x => {
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
  document.querySelectorAll("#tab-stock .chip").forEach(x => x.classList.toggle("on", x.textContent === "Todas"));
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
  const entrada = document.getElementById("edit-input");
  entrada.value = Math.max(p.f, 0);
  mostrarErrorEdicion("");
  document.getElementById("modal-editar").classList.add("abierto");
  // Directo a escribir con el teclado de números (dueño, 23/09/2026: "que
  // todos se puedan editar con teclado de números para hacerlo más
  // rápido"): el campo llega enfocado y con el valor seleccionado, así
  // teclear lo reemplaza sin borrar a mano. Los − / + siguen ahí.
  requestAnimationFrame(() => { entrada.focus(); entrada.select(); });
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
    // r.aviso: la bitácora local falló con Odoo ya escrito (lo decide y
    // redacta Python); se muestra en vez de celebrar en silencio.
    sessionStorage.setItem("toast-pendiente", r.aviso ? "⚠️ " + r.aviso :
      "✓ " + editando.n + " ajustado a " + nueva + " en Odoo");
    location.href = "/?" + parametrosDeEstado().toString();
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

  // Compartir (28/09/2026): mismo candado que Descargar (nace oculto sin
  // foto), y además solo se destapa si el navegador sabe compartir
  // ARCHIVOS — eso lo decide compartir.js, no acá. El nombre lo calculó
  // Python (p.nombreCompartir); este script solo lo pasa.
  const compartir = document.getElementById("btn-compartir-foto");
  compartir.hidden = true;
  if (p.imgD) {
    compartir.dataset.compartir = p.imgD;
    compartir.dataset.nombre = p.nombreCompartir;
    if (window.activarBotonesCompartir) window.activarBotonesCompartir();
  } else {
    delete compartir.dataset.compartir;
    delete compartir.dataset.nombre;
  }
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

/* ---------- modal crear planta ----------
   Reemplaza a la sugerencia por WhatsApp, que no creaba nada. Aquí el POST
   a /productos/nuevo crea el producto en Odoo (por el order-api) y aplica
   el stock inicial. Toda la validación de verdad vive en Python
   (main.crear_producto y el order-api); lo de aquí solo evita el viaje
   obvio y arma el SKU sugerido mientras se escribe el nombre. */
let skuTocado = false;
let creandoPlanta = false;

/* Ya no existe un abrirAgregar(): el formulario llega ABIERTO desde
   "Crear producto" → Planta (/?tab=stock&crear=planta) y la clase la pone el
   servidor en la plantilla. Los campos nacen vacíos con la página y las
   categorías las pinta Jinja, así que no queda nada que reiniciar aquí. */
function cerrarAgregar() {
  document.getElementById("modal-agregar").classList.remove("abierto");
}
function mostrarErrorAgregar(mensaje) {
  const el = document.getElementById("agregar-error");
  el.textContent = mensaje;
  el.classList.toggle("visible", Boolean(mensaje));
}

/* El mismo PL-NOMBRE-DE-LA-PLANTA que arma datos.sku_sugerido en el
   servidor: aquí solo para que se vea mientras se escribe. El SKU que vale
   es el que valida y guarda Python; si el empleado lo edita a mano
   (skuTocado), deja de proponerse. */
function sugerirSku() {
  if (skuTocado) return;
  const nombre = document.getElementById("agregar-nombre").value;
  const limpio = nombre.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/ñ/g, "n").replace(/Ñ/g, "N").toUpperCase();
  const partes = limpio.split(/[^A-Z0-9]+/).filter(Boolean);
  document.getElementById("agregar-sku").value =
    partes.length ? ("PL-" + partes.join("-")).slice(0, 79).replace(/-+$/, "") : "";
}

function centavos(valor) {
  const n = parseFloat(String(valor).replace(",", "."));
  return isNaN(n) ? 0 : Math.round(n * 100);
}
function entero(valor) {
  const n = parseInt(valor, 10);
  return isNaN(n) ? 0 : n;
}

async function crearPlanta() {
  if (creandoPlanta) return;
  const nombre = document.getElementById("agregar-nombre").value.trim();
  const sku = document.getElementById("agregar-sku").value.trim().toUpperCase();
  if (!nombre) { mostrarErrorAgregar("Escribe el nombre de la planta."); return; }
  if (!sku.startsWith("PL-") || sku.length < 4) {
    mostrarErrorAgregar("La referencia debe empezar por PL-.");
    return;
  }
  // La categoría arranca sin elegir (dueño, 30/09/2026): el servidor
  // rebota igual, esto solo evita el viaje.
  if (!document.getElementById("agregar-categoria").value) {
    mostrarErrorAgregar("Elige la categoría de la planta.");
    return;
  }
  const boton = document.getElementById("btn-crear-planta");
  creandoPlanta = true;
  boton.disabled = true;
  boton.textContent = "Creando en Odoo…";
  mostrarErrorAgregar("");
  try {
    const r = await fetch("/productos/nuevo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nombre, sku,
        categoria: document.getElementById("agregar-categoria").value,
        precioCentavos: centavos(document.getElementById("agregar-precio").value),
        costoCentavos: centavos(document.getElementById("agregar-costo").value),
        nombreSecundario: document.getElementById("agregar-secundario").value.trim(),
        nombreCientifico: document.getElementById("agregar-cientifico").value.trim(),
        cantidad: entero(document.getElementById("agregar-cantidad").value),
        alturaMin: entero(document.getElementById("agregar-hmin").value),
        alturaMax: entero(document.getElementById("agregar-hmax").value),
        sinMoto: document.getElementById("agregar-sinmoto").checked,
        /* Quién está esperando esta planta (hoy, una compra en curso). Lo
           decide Python y acá solo se reenvía: el servidor es el que la
           agrega como línea. */
        volver: DATOS.volverTrasCrear,
      }),
    });
    const datos = await r.json();
    if (!r.ok) {
      mostrarErrorAgregar(datos.mensaje || "No se pudo crear la planta.");
      return;
    }
    cerrarAgregar();
    // El stock inicial puede fallar con la planta ya creada: se dice, en vez
    // de cantar un éxito que dejaría al empleado creyendo que hay existencias.
    toast((datos.stock === "falló"
      ? `✓ ${datos.nombre} creada, pero el stock quedó en 0: ajústalo a mano`
      : `✓ ${datos.nombre} creada en Odoo`)
      + (datos.registroAviso ? ` ⚠️ ${datos.registroAviso}` : ""));
    /* A dónde ir ahora lo decide PYTHON (DATOS.destinoTrasCrear): la
       recarga de Stock de siempre —la planta nueva tiene que entrar a la
       lista con su stock— o la pantalla que mandó a crearla, con la planta
       ya agregada. Aquí no se elige nada: antes esta URL estaba escrita a
       mano y por eso la planta no podía volver a su compra. */
    setTimeout(() => location.assign(DATOS.destinoTrasCrear), 1400);
  } catch (e) {
    mostrarErrorAgregar("No hay conexión con el servidor.");
  } finally {
    creandoPlanta = false;
    boton.disabled = false;
    boton.textContent = "Crear en Odoo";
  }
}

/* La animación de inicio se quitó (dueño, 22/09/2026): frenaba el cambio
   de pestaña ~3 segundos, sobre todo con las pestañas pre-renderizadas. */

/* ---------- vista de detalle de producto ----------
   Reemplaza a la pestaña Fichas (pedido del dueño, 16/09/2026): en
   computadora, apretar una tarjeta del Stock abre esta vista con los datos
   de Odoo en solo lectura y la descripción + guía de cuidado editables
   (POST /fichas/{sku}, misma tabla de siempre). La URL lleva ?producto=SKU
   para que atrás/recargar vuelvan a donde estaba el empleado. */
let detalleSku = null;

function fichaDe(sku) { return (DATOS.fichas || {})[sku] || null; }
function referenciaDe(sku) { return (DATOS.referencias || {})[sku] || null; }

function escaparHtml(texto) {
  return String(texto).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// El extracto que se ve en la tarjeta: la ficha curada manda; si no hay,
// la referencia del sitio. Escapado porque viene de texto libre.
function descripcionDe(sku) {
  const f = fichaDe(sku) || referenciaDe(sku);
  return f && f.descripcion ? escaparHtml(f.descripcion) : "";
}

function contarDescripcion() {
  const largo = document.getElementById("ficha-descripcion").value.trim().length;
  document.getElementById("ficha-desc-largo").textContent = "· " + largo + " caracteres";
}

// "Altura 70 cm – 110 cm", o una sola medida, o vacío si la planta no tiene
// altura en Odoo: la misma regla que usa la ficha pública de la tienda.
function textoAltura(p) {
  if (!p.hmin) return "";
  if (p.hmax && p.hmax > p.hmin) return `Altura ${p.hmin} cm – ${p.hmax} cm`;
  return `Altura ${p.hmin} cm`;
}

function pintarFotoDetalle(p) {
  const caja = document.getElementById("det-foto");
  caja.textContent = p.e;
  if (p.img) {
    const img = document.createElement("img");
    img.src = p.imgG || p.img;
    img.alt = p.n;
    img.onerror = () => {
      if (p.img && img.src !== p.img) { img.src = p.img; } else { img.remove(); }
    };
    caja.appendChild(img);
  }
}

function abrirDetalle(sku, empujarHistoria = true) {
  const p = plantas.find(x => x.sku === sku);
  if (!p) return;
  detalleSku = sku;
  document.getElementById("det-nombre").textContent = p.n;
  document.getElementById("det-miga-nombre").textContent = p.n;
  document.getElementById("det-sub").textContent = sku + " · " + p.c;
  // Sin Cloudinary configurado el modal no ofrece el pincel: el botón
  // promete solo lo que puede cumplir.
  document.getElementById("det-btn-foto-texto").textContent =
    DATOS.puedeSubir ? "Cambiar foto" : "Ver foto";
  document.getElementById("det-precio").textContent = p.po || "Pendiente";
  document.getElementById("det-disponible").textContent = p.q;
  document.getElementById("det-fisico").textContent = p.f;
  const [et, cl] = estado(p);
  document.getElementById("det-estado").innerHTML = `<span class="badge ${cl}">${et}</span>`;
  pintarPublicacion(p);
  pintarFotoDetalle(p);

  const ficha = fichaDe(sku) || referenciaDe(sku) ||
    { descripcion: "", luz: "", riego: "", dificultad: "", nota: "" };
  const form = document.getElementById("ficha-descripcion");
  if (form) {
    // Editores: el formulario de la ficha.
    form.value = ficha.descripcion || "";
    document.getElementById("ficha-luz").value = ficha.luz || "";
    document.getElementById("ficha-riego").value = ficha.riego || "";
    document.getElementById("ficha-dificultad").value = ficha.dificultad || "Media";
    document.getElementById("ficha-nota").value = ficha.nota || "";
    // La altura viene de Odoo (no de la ficha curada); 0 se muestra vacío.
    document.getElementById("ficha-altura-min").value = p.hmin || "";
    document.getElementById("ficha-altura-max").value = p.hmax || "";
    // Entrega en línea: el servidor ya decidió si aplica (p.veh es null si
    // la planta no está publicada o el dato no se pudo leer de Odoo); aquí
    // solo se destapa la sección y se marcan las casillas.
    const cajaVeh = document.getElementById("ficha-veh");
    if (cajaVeh) {
      cajaVeh.hidden = !p.veh;
      if (p.veh) {
        document.getElementById("ficha-veh-moto").checked = !!p.veh.moto;
        document.getElementById("ficha-veh-carro").checked = !!p.veh.carro;
        document.getElementById("ficha-veh-pickup").checked = !!p.veh.pickup;
      }
    }
    contarDescripcion();
    mostrarErrorFicha("");
    pintarEstadoFicha(sku);
  } else {
    // Sin permiso de edición: la ficha en solo lectura.
    document.getElementById("det-altura").textContent =
      textoAltura(p) || "Sin altura todavía.";
    document.getElementById("det-descripcion").textContent =
      ficha.descripcion || "Sin descripción todavía.";
    const partes = [];
    if (ficha.luz) partes.push("Luz: " + ficha.luz);
    if (ficha.riego) partes.push("Riego: " + ficha.riego);
    if (ficha.dificultad) partes.push("Dificultad: " + ficha.dificultad);
    if (ficha.nota) partes.push(ficha.nota);
    document.getElementById("det-guia").textContent =
      partes.join(" · ") || "Sin guía de cuidado todavía.";
  }

  // Un producto recién abierto se lee desde arriba, no desde donde quedó el
  // anterior; la lista de atrás sí conserva su posición.
  scrollPorTab.detalle = 0;
  tab("detalle");
  if (empujarHistoria) {
    history.pushState({ producto: sku }, "", "/?producto=" + encodeURIComponent(sku));
  }
}

function cerrarDetalle() {
  if (history.state && history.state.producto) {
    history.back(); // el popstate hace el resto
  } else {
    detalleSku = null;
    history.replaceState(null, "", "/?tab=stock");
    tab("stock");
  }
}

window.addEventListener("popstate", () => {
  const sku = new URLSearchParams(location.search).get("producto");
  if (sku) {
    abrirDetalle(sku, false);
  } else if (detalleSku) {
    detalleSku = null;
    tab("stock");
  }
});

function pintarEstadoFicha(sku) {
  const guardada = fichaDe(sku);
  document.getElementById("ficha-estado").textContent = guardada
    ? "Última edición: " + (guardada.actualizado_por || "") + " · " + (guardada.actualizado_en || "").slice(0, 10)
    : (referenciaDe(sku) ? "Precargada con lo que hoy dice el sitio." : "Producto sin textos todavía.");
}

function mostrarErrorFicha(mensaje) {
  const el = document.getElementById("ficha-error");
  el.textContent = mensaje;
  el.classList.toggle("visible", Boolean(mensaje));
}

async function guardarFicha() {
  if (!detalleSku) return;
  const boton = document.getElementById("det-guardar");
  boton.disabled = true;
  boton.textContent = "Guardando…";
  mostrarErrorFicha("");
  try {
    const cuerpoFicha = {
      descripcion: document.getElementById("ficha-descripcion").value,
      luz: document.getElementById("ficha-luz").value,
      riego: document.getElementById("ficha-riego").value,
      dificultad: document.getElementById("ficha-dificultad").value,
      nota: document.getElementById("ficha-nota").value,
      altura_min: document.getElementById("ficha-altura-min").value,
      altura_max: document.getElementById("ficha-altura-max").value,
    };
    // Entrega en línea: el marcador tiene_vehiculo distingue en el servidor
    // «vino sin la sección» de «desmarcó todo». Solo viaja si la sección
    // está destapada (producto publicado y dato leído de Odoo).
    const cajaVeh = document.getElementById("ficha-veh");
    if (cajaVeh && !cajaVeh.hidden) {
      cuerpoFicha.tiene_vehiculo = 1;
      cuerpoFicha.viaja_moto = document.getElementById("ficha-veh-moto").checked;
      cuerpoFicha.viaja_carro = document.getElementById("ficha-veh-carro").checked;
      cuerpoFicha.viaja_pickup = document.getElementById("ficha-veh-pickup").checked;
    }
    const respuesta = await fetch("/fichas/" + encodeURIComponent(detalleSku), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpoFicha),
    });
    const cuerpo = await respuesta.json();
    if (!respuesta.ok) {
      mostrarErrorFicha(cuerpo.mensaje || "No se pudo guardar. Intenta de nuevo.");
      return;
    }
    DATOS.fichas[detalleSku] = cuerpo.ficha;
    // La altura vive en Odoo: se refleja en la planta que tenemos en
    // memoria para que la pantalla no muestre el valor viejo hasta recargar.
    const planta = plantas.find(x => x.sku === detalleSku);
    if (planta) {
      planta.hmin = cuerpo.altura_min;
      planta.hmax = cuerpo.altura_max;
      // Los vehículos también viven en Odoo: si este guardado los tocó,
      // la planta en memoria se pone al día (null = no se tocaron).
      if (cuerpo.vehiculos) planta.veh = cuerpo.vehiculos;
    }
    toast("Ficha guardada 🌿");
    pintarEstadoFicha(detalleSku);
    pintar(); // refresca el extracto de la tarjeta
  } catch {
    mostrarErrorFicha("Sin conexión con el servidor. Intenta de nuevo.");
  } finally {
    boton.disabled = false;
    boton.textContent = "Guardar ficha";
  }
}

/* ---------- pie de tarjeta: − cantidad + que se guarda solo (A3/A4) ------
   BLOQUE 53, lista de Korto del 7/10/2026:

   A3 — el número SE TECLEA. El `<b>` de antes es un campo de texto; − y +
   siguen ahí para el toque rápido, y escribir vale igual.

   A4 — ya no hay "Guardar en Odoo" ni "Cancelar": el cambio se manda SOLO
   un momento después del último toque, con un aviso chico de "Guardado" y
   un "Deshacer" al lado. Y la fila NO cambia de tamaño en ningún estado:
   el pie mide siempre lo mismo (campo de ancho fijo) y el renglón del
   aviso/error cuelga fuera del flujo, debajo del campo.

   NADA SE PIERDE si el navegador se cierra entre el último toque y el
   guardado. Tres disparos, no uno:
     1. el temporizador (ESPERA_GUARDADO desde el último toque);
     2. el campo al perder el foco (focusout);
     3. la página al irse — `visibilitychange` a hidden y `pagehide`, que
        son los dos eventos que SÍ llegan cuando se cierra la pestaña.
   Todas las peticiones salen con `keepalive: true`: el navegador las
   termina aunque el documento ya esté muerto. Mandar dos veces no hace
   daño: el candado `esperada` hace que la segunda vuelva `conflicto` y
   Odoo no se toca.

   Lo válido y lo que se guarda lo decide PYTHON: el campo viaja como
   texto crudo en `cantidadTexto`, y el aviso de un número ilegible es el
   que redacta el servidor — se pinta DEBAJO del campo y lo tecleado se
   queda donde está (regla del lote de formularios: nunca se vuelve 0). */

const ESPERA_GUARDADO = 900;   // ms sin tocar nada antes de mandar

// Todo el estado del navegador, y es a propósito un mapa flaco:
// sku → { temporizador, esperada }. El VALOR no vive aquí: vive en el
// campo de la fila, y se lee al mandarlo.
const pendientes = new Map();

function pieEstado(fila, texto, clase) {
  const caja = fila.querySelector(".pie-estado");
  if (!caja) return null;
  clearTimeout(Number(caja.dataset.reloj || 0));
  caja.className = "pie-estado" + (clase ? " " + clase : "");
  caja.textContent = texto;
  caja.hidden = !texto;
  // El campo también queda marcado (como en la vista plana): el aviso se
  // va de la pantalla, el borde rojo se queda hasta que se corrija.
  const campo = fila.querySelector(".pie-valor");
  if (campo) campo.classList.toggle("con-error", clase === "mal");
  return caja;
}

// El "✓ Guardado" se va solo: el avisito cuelga sobre la fila de abajo y
// dejarlo para siempre llenaría la lista de globitos. El ERROR no se va —
// ese se queda hasta que la persona corrija (y no tapa a nadie: no recibe
// clics, lo dice su CSS).
function pieSeVa(caja, ms) {
  caja.dataset.reloj = String(setTimeout(() => {
    caja.hidden = true;
    caja.textContent = "";
  }, ms));
}

const DURA_GUARDADO = 7000;    // ms que se queda el "✓ Guardado"

// El "Deshacer" NO borra nada: manda OTRO cambio por el mismo punto único
// de escritura (POST /ajustar → stock_escritura.escribir_stock), que lo
// anota en la bitácora con su quién/cuándo/antes/después. Deshacer un
// ajuste es un ajuste.
function pieGuardado(fila, p, anterior) {
  const caja = pieEstado(fila, "✓ Guardado", "ok");
  if (!caja) return;
  // Deshacer solo si de verdad cambió algo (un `sin_cambio` no tiene qué
  // deshacer).
  if (anterior !== undefined && anterior !== null && anterior !== p.f) {
    const deshacer = document.createElement("button");
    deshacer.type = "button";
    deshacer.className = "pie-deshacer";
    deshacer.textContent = "Deshacer";
    deshacer.addEventListener("click", () => {
      const campo = fila.querySelector(".pie-valor");
      if (campo) campo.value = anterior;
      mandarPie(fila, p);
    });
    caja.append(" · ", deshacer);
  }
  pieSeVa(caja, DURA_GUARDADO);
}

// Manda lo que hay en el campo AHORA. `esperada` es lo que Odoo tenía
// cuando empezó la edición: el candado de la casa.
function mandarPie(fila, p) {
  const campo = fila.querySelector(".pie-valor");
  if (!campo) return;
  const pendiente = pendientes.get(p.sku);
  if (pendiente) clearTimeout(pendiente.temporizador);
  pendientes.delete(p.sku);
  const esperada = pendiente ? pendiente.esperada : p.f;
  pieEstado(fila, "Guardando…", "");
  fetch("/ajustar", {
    method: "POST",
    keepalive: true,                       // sobrevive al cierre de la pestaña
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sku: p.sku, cantidadTexto: campo.value, esperada }),
  })
    .then(respuesta => respuesta.json().then(r => [respuesta.ok, r]))
    .then(([ok, r]) => contestoElPie(fila, p, ok, r))
    .catch(() => pieEstado(fila, "Sin conexión. Vuelve a intentarlo.", "mal"));
}

function contestoElPie(fila, p, ok, r) {
  if (!ok) {
    // Lo ilegible (texto, negativo, decimales) y cualquier otro rechazo:
    // el aviso lo escribe Python y se pinta debajo del campo. El valor
    // tecleado NO se toca.
    pieEstado(fila, r.mensaje || "No se pudo guardar. Intenta de nuevo.", "mal");
    return;
  }
  if (r.resultado === "conflicto") {
    // Alguien movió el stock en el medio: NADA se escribió. Se muestra lo
    // que hay de verdad en Odoo y la persona decide.
    ponerFisico(p, r.anterior);
    const campo = fila.querySelector(".pie-valor");
    if (campo) campo.value = r.anterior;
    refrescarFila(fila, p);
    pieEstado(fila, "Cambió en Odoo: ahora hay " + r.anterior + ". Revísalo.", "mal");
    return;
  }
  if (r.resultado === "no_existe") {
    pieEstado(fila, "Ya no existe en Odoo. Actualiza la lista.", "mal");
    return;
  }
  if (r.resultado !== "aplicado" && r.resultado !== "sin_cambio") {
    // NADA se escribió: nunca celebrar un ajuste que falló.
    pieEstado(fila, "Odoo rechazó el ajuste" +
      (r.detalle ? ": " + r.detalle : ". Avisa al encargado."), "mal");
    return;
  }
  if (r.aviso) {
    // Odoo quedó escrito y la bitácora local no: error RUIDOSO, sin fingir
    // que todo salió bien. El texto lo decide Python.
    toast("⚠️ " + r.aviso);
  }
  const anterior = r.anterior;
  ponerFisico(p, r.cantidad);
  refrescarFila(fila, p);
  if (r.resultado === "aplicado") quitarAlerta(p.sku);
  pieGuardado(fila, p, anterior);
}

// El servidor cierra la alerta pendiente del producto en cuanto el stock
// cambia de verdad (`atender_alerta`, solo con "aplicado"). Antes la
// recarga traía la campana al día; ahora no hay recarga, así que se
// quita acá la fila que el servidor ya no pintaría y el número de la
// campana se vuelve a CONTAR de las que quedan — nunca se adivina.
function quitarAlerta(sku) {
  const lista = document.getElementById("alertas-lista");
  const punto = document.querySelector(".bell .dot");
  if (!lista || !punto) return;
  const item = lista.querySelector(`[data-ir="${CSS.escape(sku)}"]`);
  if (!item) return;
  item.remove();
  const quedan = lista.querySelectorAll("[data-ir]").length;
  punto.textContent = quedan;
  punto.style.display = quedan ? "" : "none";
}

// Mueve el físico de la planta en memoria, y con él el DISPONIBLE: lo
// reservado no cambió, así que los dos se corren lo mismo. Sin esto el
// número grande de la tarjeta seguiría diciendo lo de la carga anterior
// hasta que alguien recargue — mintiendo justo al lado del número nuevo.
// Un `cantidad` que no venga (no debería) deja el físico donde estaba:
// mejor quedarse quieto que inventar un NaN.
function ponerFisico(p, fisico) {
  if (typeof fisico !== "number") return;
  p.q += fisico - p.f;
  p.f = fisico;
}

// Pone al día SOLO el número grande y su etiqueta de esta fila. No repinta
// la lista: repintar movía la tarjeta de lugar bajo el dedo.
function refrescarFila(fila, p) {
  const [et, cl] = estado(p);
  const numero = fila.querySelector(".qty b");
  const insignia = fila.querySelector(".qty .badge");
  if (numero) numero.textContent = p.f < 0 ? p.f : p.q;
  if (insignia) { insignia.textContent = et; insignia.className = "badge " + cl; }
  fila.classList.toggle("agotada", p.f >= 0 && p.q <= 0);
}

// Lo que no se ha mandado todavía, mandado YA. Lo llaman el repintado de
// la lista y la salida de la página.
function volcarPendientes() {
  for (const sku of [...pendientes.keys()]) {
    const fila = document.getElementById("planta-" + sku);
    const p = plantas.find(x => x.sku === sku);
    if (fila && p) mandarPie(fila, p);
    else pendientes.delete(sku);
  }
}

// Los dos eventos que SÍ llegan cuando se cierra la pestaña o se cambia de
// app en el teléfono. Con ellos (más el focusout y el temporizador) un
// cambio no se pierde aunque la pestaña muera a los 2 segundos del toque.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") volcarPendientes();
});
window.addEventListener("pagehide", volcarPendientes);

// La URL con la que se recarga tras un ajuste: pestaña, vista, filtro y
// búsqueda (y el producto abierto, si el ajuste salió desde el detalle).
// Guardar en Odoo recarga la página entera, así que sin esto el empleado
// vuelve al tope de la lista después de cada ajuste.
function parametrosDeEstado() {
  const destino = new URLSearchParams({ refrescar: "1" });
  if (detalleSku) {
    destino.set("producto", detalleSku);
  } else {
    const tabActiva = document.querySelector(".tab.activa");
    if (tabActiva) destino.set("tab", tabActiva.id.replace("tab-", ""));
  }
  destino.set("vista", vistaStockActiva);
  if (catActiva !== "Todas") destino.set("cat", catActiva);
  const busqueda = document.getElementById("busca").value.trim();
  if (busqueda) destino.set("q", busqueda);
  // El scroll no cabe en la URL sin ensuciarla: viaja por sessionStorage y
  // lo consume el arranque, una sola vez.
  sessionStorage.setItem("scroll-pendiente", String(elMain().scrollTop));
  return destino;
}

// Un toque o una tecla: se anota el pendiente y se reinicia la cuenta.
// Nada más. Lo que valga ese número lo dirá el servidor al mandarlo.
function anotarPendiente(fila, p) {
  const pendiente = pendientes.get(p.sku) || { esperada: p.f };
  clearTimeout(pendiente.temporizador);
  pendiente.temporizador = setTimeout(() => mandarPie(fila, p), ESPERA_GUARDADO);
  pendientes.set(p.sku, pendiente);
  pieEstado(fila, "", "");
}

function manejarPie(e, fila, p) {
  const paso = e.target.closest("[data-paso]");
  if (!paso) return;
  const campo = fila.querySelector(".pie-valor");
  // El − y el + mueven el número que se ve, sea el de Odoo o el tecleado.
  const ahora = parseInt(campo.value, 10);
  campo.value = Math.max(0, (isNaN(ahora) ? p.f : ahora) + parseInt(paso.dataset.paso, 10));
  anotarPendiente(fila, p);
}

/* ---------- eventos por delegación (más confiable en móvil) ---------- */
// Teclear en el campo del pie cuenta como un toque: reinicia la cuenta.
document.getElementById("lista").addEventListener("input", e => {
  const campo = e.target.closest(".pie-valor");
  const fila = e.target.closest(".planta");
  if (!campo || !fila) return;
  const p = plantas.find(x => x.sku === fila.dataset.planta);
  if (p) anotarPendiente(fila, p);
});
// Salir del campo manda lo que haya sin esperar al temporizador: si el
// empleado se va a otra cosa, su número ya está en camino.
document.getElementById("lista").addEventListener("focusout", e => {
  const campo = e.target.closest(".pie-valor");
  const fila = e.target.closest(".planta");
  if (!campo || !fila) return;
  const p = plantas.find(x => x.sku === fila.dataset.planta);
  if (p && pendientes.has(p.sku)) mandarPie(fila, p);
});
// Enter manda ya mismo (y no envía ningún formulario: no hay).
document.getElementById("lista").addEventListener("keydown", e => {
  if (e.key !== "Enter" || !e.target.closest(".pie-valor")) return;
  e.preventDefault();
  e.target.blur();
});
document.getElementById("lista").addEventListener("click", e => {
  const fila = e.target.closest(".planta");
  if (!fila) return;
  const p = plantas.find(x => x.sku === fila.dataset.planta);
  if (!p) return;
  // El pie − / + (solo computadora) se maneja aparte y no abre nada.
  if (e.target.closest(".card-pie")) {
    manejarPie(e, fila, p);
    return;
  }
  // Tocar la FOTO abre el modal de foto (ver en grande / cambiar con el
  // pincel), igual que siempre.
  if (e.target.closest(".foto")) {
    abrirFoto(p.sku);
    return;
  }
  // Tocar el NÚMERO abre Modificar stock de una (dueño, 23/09/2026: "si
  // toco el número que modifique el stock de una"); la hoja llega con el
  // nombre de la planta y el campo listo para teclear.
  if (e.target.closest(".qty")) {
    abrirEditar(p.sku);
    return;
  }
  // La tarjeta (o la fila, en el teléfono) abre la vista de detalle; el
  // ajuste rápido vive adentro, en el botón Modificar stock.
  abrirDetalle(p.sku);
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
// Detalle: la foto grande abre el modal de foto de siempre; el botón
// Modificar stock abre el modal de ajuste de siempre. El contador de la
// descripción solo existe para los editores.
/* ---------- interruptor "Publicada en la tienda" ----------
   p.pub es la CASILLA de Odoo (lo que el dueno decidio) y p.on es lo que de
   verdad se ve hoy en plantaspanama.com (espejo de catalogo-publicado.json).
   No siempre coinciden: una planta recien creada puede estar marcada y no
   salir todavia porque le falta entrar al catalogo del sitio, y una recien
   desmarcada sigue viendose hasta la proxima reconstruccion. La casilla
   pinta lo primero y el renglon de abajo cuenta lo segundo, sin adornos.

   El texto lo arma aqui el navegador porque depende de dos datos que ya
   viajaron en window.DATOS; la decision de que es "publicado" vive en el
   servidor (datos.py / main.py), no aqui. */
function notaDePublicacion(p) {
  if (p.pub === false) {
    return p.on === true
      ? "Todavía se ve en el sitio: sale en la próxima reconstrucción."
      : "Fuera del sitio.";
  }
  if (p.on === true) return "Se ve hoy en plantaspanama.com.";
  if (p.on === false) {
    return "Marcada, pero aún no sale: le falta entrar al catálogo del " +
           "sitio con su foto. Avísale a Abraham.";
  }
  return "No se pudo comprobar qué se ve hoy en el sitio.";
}

function pintarPublicacion(p) {
  const casilla = document.getElementById("det-publicada");
  if (!casilla) return;
  casilla.checked = p.pub !== false;
  document.getElementById("det-publicacion-nota").textContent = notaDePublicacion(p);
  mostrarErrorPublicacion("");
}

function mostrarErrorPublicacion(mensaje) {
  const caja = document.getElementById("det-publicacion-error");
  if (!caja) return;
  caja.textContent = mensaje;
  caja.classList.toggle("visible", Boolean(mensaje));
}

async function cambiarPublicacion(casilla) {
  const p = plantas.find(x => x.sku === detalleSku);
  if (!p) return;
  const quiere = casilla.checked;
  casilla.disabled = true;
  mostrarErrorPublicacion("");
  try {
    const r = await fetch(`/productos/${encodeURIComponent(p.sku)}/publicacion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ publicado: quiere }),
    });
    const datos = await r.json();
    if (!r.ok) {
      // Que la casilla no mienta: si Odoo no lo guardo, vuelve a su sitio.
      casilla.checked = !quiere;
      mostrarErrorPublicacion(datos.mensaje || "No se pudo guardar.");
      return;
    }
    p.pub = quiere;
    document.getElementById("det-publicacion-nota").textContent = notaDePublicacion(p);
    pintar(); // la tarjeta de la lista lleva el mismo dato
    toast(quiere
      ? "✓ Marcada para la tienda: entra en la próxima reconstrucción"
      : "✓ Fuera de la tienda: sale en la próxima reconstrucción");
  } catch (e) {
    casilla.checked = !quiere;
    mostrarErrorPublicacion("No hay conexión con el servidor.");
  } finally {
    casilla.disabled = false;
  }
}

const casillaPublicada = document.getElementById("det-publicada");
if (casillaPublicada) {
  casillaPublicada.addEventListener("change", () => cambiarPublicacion(casillaPublicada));
}

document.getElementById("det-foto").addEventListener("click", () => {
  if (detalleSku) abrirFoto(detalleSku);
});
document.getElementById("det-btn-foto").addEventListener("click", () => {
  if (detalleSku) abrirFoto(detalleSku);
});
document.getElementById("det-ajustar").addEventListener("click", () => {
  if (detalleSku) abrirEditar(detalleSku);
});
const campoDescripcion = document.getElementById("ficha-descripcion");
if (campoDescripcion) campoDescripcion.addEventListener("input", contarDescripcion);

const toastPendiente = sessionStorage.getItem("toast-pendiente");
if (toastPendiente) {
  sessionStorage.removeItem("toast-pendiente");
  toast(toastPendiente);
}

// Ajustes: en computadora las secciones van abiertas de una; en el
// teléfono quedan como lista de entradas que se abren al tocar
// (artefacto aprobado, 23/09/2026).
if (matchMedia("(min-width: 900px)").matches) {
  document.querySelectorAll("#tab-ajustes details.ajuste-d").forEach(d => { d.open = true; });
}

// Los enlaces de la página /venta y la recarga tras guardar un ajuste
// vuelven con ?tab= (y opcionalmente cat= y q=) para aterrizar exactamente
// donde estaba el empleado (las pestañas son 100% del navegador).
const parametros = new URLSearchParams(location.search);
const tabPedida = parametros.get("tab");
if (tabPedida === "stock" || tabPedida === "inv" || tabPedida === "ajustes") {
  tab(tabPedida); // tab() encuentra el botón por data-tab (inv ya no tiene)
}
const catPedida = parametros.get("cat");
if (catPedida) {
  catActiva = catPedida;
  document.querySelectorAll("#tab-stock .chip").forEach(x =>
    x.classList.toggle("on", x.dataset.cat === catPedida));
}
const buscaPedida = parametros.get("q");
if (buscaPedida) document.getElementById("busca").value = buscaPedida;

// ?vista=global vuelve a la vista que el empleado tenía (la recarga tras
// crear una planta aterriza ahí: la planta nueva todavía no está en línea).
const vistaPedida = parametros.get("vista");
vistaStock(vistaPedida === "online" && !DATOS.sinPublicados ? "online" :
  vistaPedida === "global" ? "global" : vistaStockActiva);

pintar();

// Volver justo donde estaba tras la recarga de un ajuste (ver
// parametrosDeEstado). Se consume una sola vez: una recarga a mano o un
// link compartido tienen que abrir arriba.
const scrollPendiente = sessionStorage.getItem("scroll-pendiente");
if (scrollPendiente) {
  sessionStorage.removeItem("scroll-pendiente");
  const tabAhora = document.querySelector(".tab.activa");
  if (tabAhora) scrollPorTab[tabAhora.id.replace("tab-", "")] = parseInt(scrollPendiente) || 0;
  requestAnimationFrame(() => { elMain().scrollTop = parseInt(scrollPendiente) || 0; });
}

// ?producto=SKU (recarga tras un ajuste desde el detalle, o un link
// compartido): reabrir el detalle sin apilar otra entrada en el historial.
const productoPedido = parametros.get("producto");
if (productoPedido) {
  history.replaceState({ producto: productoPedido }, "", location.href);
  abrirDetalle(productoPedido, false);
}

// Ajustes: copiar el link de una invitación (para mandarlo por WhatsApp).
function copiarLink(btn) {
  const link = btn.dataset.link;
  const listo = () => toast("Link copiado");
  // http local, navegador viejo o permiso denegado: el truco del textarea.
  const respaldo = () => {
    const caja = document.createElement("textarea");
    caja.value = link;
    document.body.appendChild(caja);
    caja.select();
    document.execCommand("copy");
    caja.remove();
    listo();
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(link).then(listo).catch(respaldo);
  } else {
    respaldo();
  }
}
