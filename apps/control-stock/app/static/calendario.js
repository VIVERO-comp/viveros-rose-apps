/* Lo único que el calendario necesita del navegador.
   Todo lo demás (qué días entran en la vista, dónde va cada bloque, quién
   puede tocar qué, qué está atrasado) lo calcula app/calendario.py y llega
   pintado desde Jinja2. Aquí solo quedan dos cosas que el servidor no puede
   hacer: filtrar mientras se escribe lo que YA está en pantalla, y preguntar
   antes de una acción sin marcha atrás. */
(function () {
  'use strict';

  // Buscador en vivo: esconde lo que no coincide sin recargar. El formulario
  // sigue funcionando con Enter (ahí filtra el servidor y quedan los chips,
  // el mini calendario y los números del día al día con la búsqueda).
  var caja = document.getElementById('buscar');
  if (caja) {
    caja.addEventListener('input', function () {
      var texto = caja.value.trim().toLowerCase();
      document.querySelectorAll('[data-busca]').forEach(function (el) {
        var coincide = !texto || el.getAttribute('data-busca').indexOf(texto) !== -1;
        el.style.visibility = coincide ? '' : 'hidden';
      });
      document.querySelectorAll('.lista-caja .act').forEach(function (el) {
        var coincide = !texto || el.textContent.toLowerCase().indexOf(texto) !== -1;
        el.hidden = !coincide;
      });
    });
  }

  // La recogida solo aplica a Alquiler (29/09/2026): el select de tipo la
  // muestra o la esconde. Presentación pura — el estado inicial ya viene
  // decidido del servidor y la REGLA vive en Python (con otro tipo, la
  // recogida se descarta en el POST aunque llegue escrita).
  var tipo = document.getElementById('tipo');
  var cajaRecogida = document.getElementById('caja-recogida');
  if (tipo && cajaRecogida) {
    var pintaRecogida = function () {
      cajaRecogida.hidden = tipo.value !== 'alquiler';
    };
    tipo.addEventListener('change', pintaRecogida);
    pintaRecogida();
  }

  // Elegir un lead rellena Cliente si estaba vacío (29/09/2026):
  // presentación pura — el dato que manda es el select, y lo resuelve el
  // servidor (con lead elegido, el cliente de la actividad es el del lead).
  var leadSel = document.getElementById('lead');
  var clienteCampo = document.getElementById('cliente');
  if (leadSel && clienteCampo) {
    leadSel.addEventListener('change', function () {
      var opcion = leadSel.options[leadSel.selectedIndex];
      if (!clienteCampo.value.trim() && opcion) {
        clienteCampo.value = opcion.getAttribute('data-nombre') || '';
      }
    });
  }

  // Cancelar una actividad no tiene marcha atrás cómoda: se pregunta con el
  // riesgo escrito en el propio formulario (lo redacta la plantilla).
  //
  // DELEGADO en el document (A5 del BLOQUE 53, y `submit` burbujea): el
  // panel del lead ahora puede llegar como pedazo del servidor, así que
  // estos dos guardias —el confirm y el anti-doble-toque de abajo— tienen
  // que valer también para los formularios que entran DESPUÉS de cargar
  // la página. Un listener por nodo solo ataba los que había al cargar, y
  // los botones del panel pedido se habrían quedado sin «Guardando…» y
  // sin candado contra el toque doble, sin que nada avisara.
  document.addEventListener('submit', function (evento) {
    var form = evento.target;
    if (!form || form.tagName !== 'FORM') return;
    var aviso = form.getAttribute('data-confirmar');
    if (aviso && !window.confirm(aviso + '\n\n¿Seguimos?')) {
      evento.preventDefault();
      return;
    }
    // Al mandar un formulario, el botón avisa "Guardando…" y se apaga: un
    // toque doble (fácil en el teléfono) no crea la actividad dos veces.
    // `submitter` dice qué botón lo mandó; si el navegador no lo trae, el
    // del propio formulario.
    var boton = evento.submitter;
    if (!boton || !boton.hasAttribute || !boton.hasAttribute('data-guardando')) {
      boton = form.querySelector('button[data-guardando]');
    }
    if (!boton) return;
    if (evento.defaultPrevented) return;
    if (form.dataset.mandado) { evento.preventDefault(); return; }
    form.dataset.mandado = '1';
    boton.dataset.texto = boton.textContent;
    boton.textContent = boton.getAttribute('data-guardando');
    boton.classList.add('guardando');
    // disabled recién después de que el envío salga: un botón apagado
    // en el mismo evento haría que el navegador no mande el submit.
    setTimeout(function () { boton.disabled = true; }, 0);
  });

  // Al volver con Atrás, el navegador puede restaurar la página con un
  // botón todavía en "Guardando…": se revive aquí.
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('button[data-guardando]').forEach(function (boton) {
      var form = boton.closest('form');
      if (form) delete form.dataset.mandado;
      boton.disabled = false;
      boton.classList.remove('guardando');
      if (boton.dataset.texto) boton.textContent = boton.dataset.texto;
    });
  });

})();
