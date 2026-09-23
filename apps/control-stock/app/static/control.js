/* El único JS de la pestaña Control: el arrastre entre columnas. Todo lo
   demás (columnas, tarjetas, ficha, modal de motivo) lo arma el servidor.
   Soltar una tarjeta manda un formulario POST /control/mover y la página
   vuelve pintada por el servidor; soltar en Inactivo redirige al modal
   del motivo (lo decide el servidor, no este script). */
(function () {
  'use strict';

  // La conversación de la ficha abre mostrando lo último, como el panel.
  var chat = document.querySelector('.crm-conversacion');
  if (chat) chat.scrollTop = chat.scrollHeight;

  document.querySelectorAll('.ctl-tarjeta').forEach(function (tarjeta) {
    tarjeta.addEventListener('dragstart', function (ev) {
      ev.dataTransfer.setData('text/plain', tarjeta.getAttribute('data-chat'));
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
      var chat = ev.dataTransfer.getData('text/plain');
      if (!chat) return;
      var form = document.createElement('form');
      form.method = 'post';
      form.action = '/control/mover';
      var campoChat = document.createElement('input');
      campoChat.type = 'hidden'; campoChat.name = 'chat'; campoChat.value = chat;
      var campoCol = document.createElement('input');
      campoCol.type = 'hidden'; campoCol.name = 'columna';
      campoCol.value = lista.getAttribute('data-columna');
      form.appendChild(campoChat); form.appendChild(campoCol);
      document.body.appendChild(form);
      form.submit();
    });
  });
})();
