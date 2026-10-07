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

  /* EL BUSCADOR ÚNICO de «Actividad nueva», sin recargar (BLOQUE 59).

     LA REGLA 10 QUEDA INTACTA, igual que en panel.js: el pedazo lo arma
     el SERVIDOR (main._buscador_persona_contexto, la misma función que usa
     la página entera). Este bloque no escribe una etiqueta de HTML, no
     decide quién casa con qué y no guarda ningún estado: le pide al
     servidor ESE MISMO pedazo y lo mete en su caja.

     Lo único que hace de más es LLEVARSE lo ya escrito: el form GET del
     buscador vive fuera del formulario, así que sus campos escondidos
     traen lo que el servidor pintó, no lo que la persona acaba de teclear.
     Antes de buscar se copian los valores vivos de los campos del mismo
     nombre — es plomería, no una decisión: es lo que el navegador haría
     solo si los dos forms fueran uno, y sin esto buscar a alguien perdería
     la fecha, la hora y la nota.

     SIN JS: el form GET se manda como siempre y la página recarga con el
     mismo formulario abierto; elegir una fila es un <a href> de verdad.
     Nada de esto es un requisito para que el buscador funcione. */
  var busCaja = document.querySelector('[data-buscador]');
  var busForm = document.getElementById('f-buscar-persona');
  var busFormularioNuevo = busCaja ? busCaja.closest('form') : null;
  var busFuente = busCaja ? busCaja.getAttribute('data-buscador-fuente') : '';
  if (busCaja && busForm && busFuente && window.fetch) {
    var busUltimo = 0;

    // Los campos escondidos se ponen al día con lo que hay escrito ahora
    // en el formulario de la actividad (mismo `name`, mismo dato).
    var busSincronizar = function () {
      if (!busFormularioNuevo) return;
      var ocultos = busForm.querySelectorAll('input[type="hidden"]');
      Array.prototype.forEach.call(ocultos, function (oculto) {
        var vivo = busFormularioNuevo.elements[oculto.name];
        if (vivo && typeof vivo.value === 'string') oculto.value = vivo.value;
      });
    };

    busForm.addEventListener('submit', function (evento) {
      busSincronizar();
      // `qp` ya viene en el FormData: la caja de escribir declara
      // `form="f-buscar-persona"`, así que pertenece a este form aunque
      // se pinte adentro del otro.
      var consulta = new URLSearchParams(new FormData(busForm)).toString();
      evento.preventDefault();
      var mio = ++busUltimo;
      fetch(busFuente + '?' + consulta, {credentials: 'same-origin'})
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.text();
        })
        .then(function (html) {
          if (mio !== busUltimo) return;
          busCaja.outerHTML = html;
          // La caja se reemplazó entera: se vuelve a tomar y se devuelve el
          // foco a donde estaba la mano.
          busCaja = document.querySelector('[data-buscador]');
          var nuevo = busCaja && busCaja.querySelector('#qp');
          if (nuevo) nuevo.focus();
        })
        .catch(function () {
          if (mio !== busUltimo) return;
          // El camino de siempre: que el form se mande y la página
          // recargue. Mejor una recarga que un buscador que no busca.
          busForm.submit();
        });
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
