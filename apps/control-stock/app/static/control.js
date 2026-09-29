/* El único JS de la pestaña Control: el arrastre entre columnas. Todo lo
   demás (columnas, tarjetas, ficha, modal del motivo) lo arma el servidor.

   Soltar una tarjeta manda un formulario POST y la página vuelve pintada
   por el servidor. A dónde va ese POST y cómo se llama el campo lo dice la
   propia columna (data-destino / data-campo), no este script: en la vista
   "por empleado" cambia la etiqueta Resp: y en la vista "por estado" abre
   el modal que pide el motivo. Quién puede mover qué lo decide el
   servidor; aquí solo se arrastra lo que el servidor marcó arrastrable. */
(function () {
  'use strict';

  // El ref de la tarjeta que se está arrastrando AHORA, o '' si lo que se
  // arrastra no es una tarjeta. El drop lee esto y no el dataTransfer
  // (29/09/2026): todo <a> es arrastrable por naturaleza, y al arrastrar
  // el enlace de adentro de una tarjeta no movible el dataTransfer trae
  // la URL del enlace — el drop mandaba ESO como ref y el servidor
  // contestaba «ya no está en Linear» sin que hubiera ningún lead borrado.
  var refEnArrastre = '';

  document.querySelectorAll('.ctl-tarjeta[draggable="true"]').forEach(function (tarjeta) {
    tarjeta.addEventListener('dragstart', function (ev) {
      refEnArrastre = tarjeta.getAttribute('data-ref') || '';
      ev.dataTransfer.setData('text/plain', refEnArrastre);
      ev.dataTransfer.effectAllowed = 'move';
      tarjeta.classList.add('arrastrando');
    });
    tarjeta.addEventListener('dragend', function () {
      refEnArrastre = '';
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
      // Sin tarjeta en arrastre no hay POST: soltar cualquier otra cosa
      // (un enlace, un texto, algo de otra ventana) se ignora en silencio.
      var ref = refEnArrastre;
      var destino = lista.getAttribute('data-destino');
      var campo = lista.getAttribute('data-campo');
      if (!ref || !destino || !campo) return;

      var form = document.createElement('form');
      form.method = 'post';
      form.action = destino + '?vista=' + encodeURIComponent(
        lista.getAttribute('data-vista') || '');
      [['ref', ref], [campo, lista.getAttribute('data-columna')]].forEach(
        function (par) {
          var input = document.createElement('input');
          input.type = 'hidden';
          input.name = par[0];
          input.value = par[1];
          form.appendChild(input);
        });
      document.body.appendChild(form);
      form.submit();
    });
  });
})();
