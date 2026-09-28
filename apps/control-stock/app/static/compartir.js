// Compartir un PDF o una foto con la hoja nativa de compartir del
// teléfono (Web Share API con archivos).
//
// El nombre del archivo SIEMPRE lo calcula Python y viaja en
// `data-nombre` (para un PDF, la misma `ventas.nombre_de_pdf()` que ya
// arma el `Content-Disposition` del servidor; para la foto del modal de
// producto, el slug de la planta que arma app.js). Este script no arma
// nombres ni decide nada de negocio — solo hace lo único que ningún
// servidor puede hacer: pedirle al navegador que abra su hoja de
// compartir (pedido del dueño, 28/09/2026).
//
// El botón nace OCULTO en el HTML (`hidden`), porque no todos los
// navegadores saben compartir ARCHIVOS — `navigator.share` existe en más
// sitios de los que aceptan `files` — y se muestra recién al confirmar
// que sí puede, nunca al revés: así uno que no puede nunca ve un botón
// que le falla al primer toque.

(function () {
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

  // Un solo listener delegado: sirve igual para los botones que ya
  // estaban en la página al cargar (Vender, la ficha de Control) y para
  // los que cambian de archivo en vivo (el modal de foto de producto).
  document.addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-compartir]");
    if (!boton || boton.hidden) return;
    evento.preventDefault();
    compartirArchivo(boton);
  });

  document.addEventListener("DOMContentLoaded", () => activarBotonesCompartir());

  // Para el modal de foto de producto (app.html): sus data-atributos
  // cambian cada vez que se abre una foto distinta, así que necesita
  // reactivarse a mano después de pintar la nueva — no hay un solo
  // "cargó la página" que alcance para eso. Ver pintarFotoGrande() en
  // app.js.
  window.activarBotonesCompartir = activarBotonesCompartir;
})();
