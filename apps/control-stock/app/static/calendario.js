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

  // Cancelar una actividad no tiene marcha atrás cómoda: se pregunta con el
  // riesgo escrito en el propio formulario (lo redacta la plantilla).
  document.querySelectorAll('form[data-confirmar]').forEach(function (form) {
    form.addEventListener('submit', function (evento) {
      if (!window.confirm(form.getAttribute('data-confirmar') + '\n\n¿Seguimos?')) {
        evento.preventDefault();
      }
    });
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

  // Al mandar un formulario, el botón avisa "Guardando…" y se apaga: un
  // toque doble (fácil en el teléfono) no crea la actividad dos veces.
  document.querySelectorAll('button[data-guardando]').forEach(function (boton) {
    var form = boton.closest('form');
    if (!form) return;
    form.addEventListener('submit', function (evento) {
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
  });
})();
