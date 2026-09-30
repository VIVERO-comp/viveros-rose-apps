// La hoja nativa de compartir del teléfono (Web Share API con archivos),
// en dos caminos:
//
// 1. `[data-compartir]` — el botón «Compartir» de la foto del modal de
//    producto (app.html), el ÚNICO que queda desde que los PDF pasaron a
//    un solo control (dueño, 30/09/2026). Su nombre viaja en
//    `data-nombre` (el slug de la planta, calculado en Python).
// 2. `a[data-pdf]` — los enlaces «Descargar / Compartir» de PDF: solo en
//    iPhone/iPad se interceptan para abrir la hoja; en el resto siguen
//    como descarga normal en pestaña nueva.
//
// Este script no arma nombres ni decide nada de negocio — solo hace lo
// único que ningún servidor puede hacer: pedirle al navegador que abra
// su hoja de compartir.
//
// El botón de la foto nace OCULTO en el HTML (`hidden`), porque no todos
// los navegadores saben compartir ARCHIVOS — `navigator.share` existe en
// más sitios de los que aceptan `files` — y se muestra recién al
// confirmar que sí puede, nunca al revés: así uno que no puede nunca ve
// un botón que le falla al primer toque.

(function () {
  // iPhone/iPad (la segunda mitad es iPadOS en modo escritorio, que se
  // presenta como Mac pero tiene pantalla táctil). Solo el navegador
  // puede saber esto — por eso vive acá y no en Python.
  const ES_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  const PUEDE_COMPARTIR_ARCHIVOS = (() => {
    try {
      const prueba = new File([""], "prueba.pdf", { type: "application/pdf" });
      return Boolean(navigator.share && navigator.canShare
        && navigator.canShare({ files: [prueba] }));
    } catch {
      return false;
    }
  })();

  function activarBotonesCompartir(raiz) {
    if (!PUEDE_COMPARTIR_ARCHIVOS) return;
    (raiz || document).querySelectorAll("[data-compartir]").forEach((boton) => {
      boton.hidden = false;
    });
  }

  async function compartirArchivo(boton) {
    const url = boton.dataset.compartir;
    const nombre = boton.dataset.nombre || "archivo";
    try {
      const respuesta = await fetch(url);
      if (!respuesta.ok) throw new Error("descarga fallida");
      const blob = await respuesta.blob();
      const archivo = new File([blob], nombre, { type: blob.type });
      await navigator.share({ files: [archivo], title: nombre });
    } catch (error) {
      if (error && error.name === "AbortError") return; // canceló: sin aviso
      alert('No se pudo compartir. Usa "Descargar" y compártelo desde ahí.');
    }
  }

  // En iPhone/iPad, «Descargar / Compartir» un PDF abre la hoja nativa
  // (dueño, 30/09/2026): iOS Safari ignora el attachment+download y
  // pinta el PDF a pantalla completa, dejando al usuario trabado. La
  // hoja nativa sí funciona: desde ahí se guarda en Archivos o se manda
  // por WhatsApp. Qué enlace es un PDF lo decide Python: cada plantilla
  // marca sus enlaces de descarga de PDF con `data-pdf`, y el nombre del
  // archivo ya viaja en el atributo `download` (calculado en Python) —
  // este script no adivina por la URL ni arma nombres. Si algo falla
  // (menos cancelar), cae al comportamiento de siempre: pestaña nueva.
  async function descargarPdfConHoja(enlace) {
    const nombre = enlace.getAttribute("download") || "documento.pdf";
    try {
      const respuesta = await fetch(enlace.href);
      if (!respuesta.ok) throw new Error("descarga fallida");
      const blob = await respuesta.blob();
      const archivo = new File([blob], nombre,
        { type: blob.type || "application/pdf" });
      await navigator.share({ files: [archivo], title: nombre });
    } catch (error) {
      if (error && error.name === "AbortError") return; // canceló: sin aviso
      window.open(enlace.href, "_blank", "noopener");
    }
  }

  // Un solo listener delegado para los dos caminos: el botón de la foto
  // del modal (cuyos data-atributos cambian en vivo) y los enlaces de
  // PDF marcados con data-pdf.
  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-compartir]");
    if (boton && !boton.hidden) {
      evento.preventDefault();
      compartirArchivo(boton);
      return;
    }
    // Solo iOS con Web Share de archivos: en Android y computadora la
    // descarga normal funciona y no se toca; un iOS viejo sin Web Share
    // de archivos se queda con el comportamiento actual (peor sería un
    // botón muerto).
    if (!ES_IOS || !PUEDE_COMPARTIR_ARCHIVOS) return;
    const enlace = evento.target.closest("a[data-pdf]");
    if (!enlace) return;
    evento.preventDefault();
    descargarPdfConHoja(enlace);
  });

  document.addEventListener("DOMContentLoaded", () => activarBotonesCompartir());

  // Para el modal de foto de producto (app.html): sus data-atributos
  // cambian cada vez que se abre una foto distinta, así que necesita
  // reactivarse a mano después de pintar la nueva — no hay un solo
  // "cargó la página" que alcance para eso. Ver pintarFotoGrande() en
  // app.js.
  window.activarBotonesCompartir = activarBotonesCompartir;
})();
