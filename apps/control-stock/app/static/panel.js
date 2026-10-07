/* EL PANEL DE UNA TARJETA, SIN RECARGAR LA PÁGINA (A5 del BLOQUE 53).

   Antes, tocar una tarjeta del CRM o de Vender navegaba a `?abrir=…`: la
   página entera se volvía a pintar y el tablero volvía al principio —
   quien venía desplazado hasta la columna «Perdido» la perdía de vista a
   cada toque, y lo mismo al cerrar.

   LA REGLA 10 QUEDA INTACTA: el panel lo sigue armando el SERVIDOR. Este
   archivo no escribe ni una etiqueta de HTML, no decide nada y no guarda
   NINGÚN estado: cuando se toca un enlace marcado con `data-panel-liga`,
   le pide al servidor ESE MISMO pedazo ya armado —la query del enlace,
   tal cual, al endpoint que la caja declara en `data-panel-fuente`— y lo
   mete en la caja. Cerrar es la misma operación: la URL de cerrar no
   lleva `?abrir=`, así que el servidor contesta vacío y la caja se queda
   vacía. Un solo camino para abrir y para cerrar.

   Y los candados tampoco cambian: lo que el servidor no manda, acá no
   hay de dónde sacarlo. El panel de UN lead se pide cuando se toca ese
   lead, con la misma sesión y los mismos `puede_ver_plata` /
   `puede_tocar` que aplica la página entera (main._panel_lead_contexto es
   literalmente la misma función para los dos caminos). Nada viaja «por si
   acaso» para todas las tarjetas.

   LA DIRECCIÓN ACOMPAÑA (history.pushState): recargar o compartir el
   enlace sigue abriendo el mismo panel, y «Atrás» funciona — `popstate`
   vuelve a pedirle al servidor el pedazo de la URL nueva.

   SIN JS, O SI EL PEDIDO FALLA: el enlace es un <a href> de verdad y la
   página navega como siempre. Nada de esto es un requisito para que la
   pantalla funcione. */
(function () {
  'use strict';

  var caja = document.querySelector('[data-panel]');
  if (!caja || !window.fetch || !window.history || !history.pushState) return;
  var fuente = caja.getAttribute('data-panel-fuente');
  if (!fuente) return;

  // El número del último pedido: si llegan dos respuestas desordenadas
  // (dos toques rápidos), solo pinta la del toque más nuevo.
  var ultimo = 0;

  function queryDe(href) {
    var i = href.indexOf('?');
    if (i < 0) return '';
    var resto = href.slice(i + 1);
    var h = resto.indexOf('#');
    return h < 0 ? resto : resto.slice(0, h);
  }

  function esperando(si) {
    // La única señal del momento: el cursor de «ya va». No es estado de
    // la app — es lo que el navegador le debe al dedo que acaba de tocar.
    if (si) document.documentElement.setAttribute('data-panel-pidiendo', '1');
    else document.documentElement.removeAttribute('data-panel-pidiendo');
  }

  function pedir(href, empujar) {
    var mio = ++ultimo;
    esperando(true);
    fetch(fuente + '?' + queryDe(href), {credentials: 'same-origin'})
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        if (mio !== ultimo) return;
        esperando(false);
        caja.innerHTML = html;
        if (empujar) history.pushState({panel: 1}, '', href);
      })
      .catch(function () {
        if (mio !== ultimo) return;
        esperando(false);
        // El camino de siempre: que navegue. Mejor una recarga que una
        // tarjeta que no abre.
        window.location.href = href;
      });
  }

  document.addEventListener('click', function (ev) {
    if (ev.defaultPrevented || ev.button !== 0) return;
    // Abrir en otra pestaña (⌘/Ctrl/mayúsculas) o guardar (Alt) sigue
    // siendo cosa del navegador: ahí no se intercepta nada.
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var destino = ev.target;
    var liga = destino && destino.closest
      ? destino.closest('[data-panel-liga]') : null;
    if (!liga) return;
    var href = liga.getAttribute('href');
    if (!href) return;
    ev.preventDefault();
    pedir(href, true);
  });

  window.addEventListener('popstate', function () {
    // Atrás y Adelante: la URL ya cambió, así que se le pide al servidor
    // el panel de ESA dirección (vacío si no lleva ?abrir=).
    pedir(window.location.pathname + window.location.search, false);
  });
})();
