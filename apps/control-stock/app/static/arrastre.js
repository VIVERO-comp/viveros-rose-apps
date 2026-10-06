/* El arrastre como GESTO DE NAVEGACIÓN (BLOQUE 40), compartido por
   Vender y Pedidos. LA REGLA DE ORO: SOLTAR NUNCA ESCRIBE — el drop
   solo NAVEGA a la pantalla que ya existe (el panel de cobro de Vender,
   el editor de fecha de la ficha en Pedidos). Cerrar esa pantalla sin
   tocar nada deja todo como estaba: no hay nada que revertir, porque
   nada se escribió.

   Qué tarjeta se arrastra, a qué columnas se puede soltar y a qué URL
   navega el drop lo decidió PYTHON al armar la plantilla
   (data-arrastre-url y data-arrastre-destinos en la tarjeta,
   data-arrastre-destino en la columna): aquí no hay estado, no hay
   fetch, no hay reglas — solo el gesto.

   Clonado del patrón de control.js: el drop lee la variable del módulo
   y no el dataTransfer (29/09/2026: todo <a> es arrastrable por
   naturaleza, y al arrastrar el enlace de adentro de una tarjeta el
   dataTransfer trae la URL del enlace — por eso además los <a> internos
   van con draggable="false" en la plantilla). */
(function () {
  'use strict';

  // La URL y los destinos de la tarjeta que se arrastra AHORA, o vacío
  // si lo que se arrastra no es una tarjeta (un enlace, un texto, algo
  // de otra ventana): eso se ignora en silencio.
  var urlEnArrastre = '';
  var destinosEnArrastre = [];
  var columnaOrigen = null;

  function columnas() {
    return document.querySelectorAll('[data-arrastre-destino]');
  }

  function acepta(columna) {
    return destinosEnArrastre.indexOf(
      columna.getAttribute('data-arrastre-destino')) !== -1;
  }

  // Mientras algo se arrastra, la columna que NO acepta se pinta gris
  // (dropno): el no-drop se VE, no solo se siente en el cursor. La
  // columna de origen queda neutra — de ahí salió, no es un destino.
  function marcar(arrastrando) {
    columnas().forEach(function (columna) {
      columna.classList.remove('dropok');
      columna.classList.toggle(
        'dropno',
        arrastrando && columna !== columnaOrigen && !acepta(columna));
    });
  }

  document.querySelectorAll('[data-arrastre-url]').forEach(function (tarjeta) {
    tarjeta.addEventListener('dragstart', function (ev) {
      urlEnArrastre = tarjeta.getAttribute('data-arrastre-url') || '';
      destinosEnArrastre = (tarjeta.getAttribute('data-arrastre-destinos') || '')
        .split(' ').filter(Boolean);
      columnaOrigen = tarjeta.closest('[data-arrastre-destino]');
      ev.dataTransfer.setData('text/plain', urlEnArrastre);
      ev.dataTransfer.effectAllowed = 'move';
      tarjeta.classList.add('arrastrando');
      marcar(true);
    });
    tarjeta.addEventListener('dragend', function () {
      urlEnArrastre = '';
      destinosEnArrastre = [];
      columnaOrigen = null;
      tarjeta.classList.remove('arrastrando');
      marcar(false);
    });
  });

  columnas().forEach(function (columna) {
    columna.addEventListener('dragover', function (ev) {
      // Sin preventDefault el navegador muestra su no-drop: una columna
      // que no está en los destinos de ESTA tarjeta no acepta nada.
      if (!urlEnArrastre || !acepta(columna)) return;
      ev.preventDefault();
      columna.classList.add('dropok');
    });
    columna.addEventListener('dragleave', function () {
      columna.classList.remove('dropok');
    });
    columna.addEventListener('drop', function (ev) {
      ev.preventDefault();
      columna.classList.remove('dropok');
      if (!urlEnArrastre || !acepta(columna)) return;
      // Navegar, jamás escribir: la decisión de verdad (pagar, poner la
      // fecha) la toma la persona en la pantalla que se abre.
      window.location = urlEnArrastre;
    });
  });
})();
