/* El único JS de la pestaña Retail: el arrastre entre columnas. Todo lo
   demás (columnas, tarjetas, ficha, fechas) lo arma el servidor. Soltar
   una tarjeta manda un formulario POST /retail/mover y la página vuelve
   pintada por el servidor. */
(function () {
  'use strict';

  document.querySelectorAll('.ret-tarjeta').forEach(function (tarjeta) {
    tarjeta.addEventListener('dragstart', function (ev) {
      ev.dataTransfer.setData('text/plain', tarjeta.getAttribute('data-ref'));
      ev.dataTransfer.effectAllowed = 'move';
      tarjeta.classList.add('arrastrando');
    });
    tarjeta.addEventListener('dragend', function () {
      tarjeta.classList.remove('arrastrando');
    });
  });

  document.querySelectorAll('.ret-lista').forEach(function (lista) {
    lista.addEventListener('dragover', function (ev) {
      ev.preventDefault();
      lista.classList.add('dropok');
    });
    lista.addEventListener('dragleave', function () {
      lista.classList.remove('dropok');
    });
    lista.addEventListener('drop', function (ev) {
      ev.preventDefault();
      lista.classList.remove('dropok');
      var ref = ev.dataTransfer.getData('text/plain');
      if (!ref) return;
      var form = document.createElement('form');
      form.method = 'post';
      form.action = '/retail/mover';
      form.innerHTML =
        '<input type="hidden" name="ref" value="' + ref + '">' +
        '<input type="hidden" name="etapa" value="' + lista.getAttribute('data-etapa') + '">';
      document.body.appendChild(form);
      form.submit();
    });
  });
})();
