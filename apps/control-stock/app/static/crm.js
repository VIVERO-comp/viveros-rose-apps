/* El único JS de la pestaña CRM: el arrastre de tarjetas. Solo las listas
   con data-estado aceptan soltar (En conversación, Pedido pendiente y
   Ganado — decisión del dueño, 23/09/2026); las demás columnas ni se
   iluminan. Soltar POSTea /crm/mover y la página vuelve pintada por el
   servidor, que mueve el issue en Linear y espeja Twenty. */
(function () {
  'use strict';

  // La conversación abre mostrando lo último, como el panel del admin.
  var chat = document.querySelector('.crm-conversacion');
  if (chat) chat.scrollTop = chat.scrollHeight;

  document.querySelectorAll('.crm-tarjeta[draggable]').forEach(function (tarjeta) {
    tarjeta.addEventListener('dragstart', function (ev) {
      ev.dataTransfer.setData('text/plain', tarjeta.getAttribute('data-lead'));
      ev.dataTransfer.effectAllowed = 'move';
      tarjeta.classList.add('arrastrando');
    });
    tarjeta.addEventListener('dragend', function () {
      tarjeta.classList.remove('arrastrando');
    });
  });

  document.querySelectorAll('.ret-lista[data-estado]').forEach(function (lista) {
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
      var lead = ev.dataTransfer.getData('text/plain');
      if (!lead) return;
      var form = document.createElement('form');
      form.method = 'post';
      form.action = '/crm/mover';
      var campoLead = document.createElement('input');
      campoLead.type = 'hidden'; campoLead.name = 'lead'; campoLead.value = lead;
      var campoEstado = document.createElement('input');
      campoEstado.type = 'hidden'; campoEstado.name = 'estado';
      campoEstado.value = lista.getAttribute('data-estado');
      form.appendChild(campoLead); form.appendChild(campoEstado);
      document.body.appendChild(form);
      form.submit();
    });
  });
})();
