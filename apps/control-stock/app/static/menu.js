/* El botón hamburguesa del menú lateral (pedido del dueño, 22/09/2026:
   "como el de Google Calendar"). Esto es de lo poco que el servidor no
   puede hacer: esconder/mostrar el menú al instante, sin recargar. La
   preferencia viaja en la cookie `menu` y es el SERVIDOR quien pinta la
   página ya colapsada en la próxima carga (base.html / calendario.html /
   retail.html leen la cookie); aquí solo se voltea la clase y se guarda. */
(function () {
  'use strict';
  var botones = document.querySelectorAll('.hamb');
  if (!botones.length) return;
  botones.forEach(function (boton) {
    boton.addEventListener('click', function () {
      var oculto = document.body.classList.toggle('menu-oculto');
      document.cookie = 'menu=' + (oculto ? '0' : '1') +
        ';path=/;max-age=31536000;samesite=lax';
      botones.forEach(function (b) {
        b.setAttribute('aria-expanded', String(!oculto));
      });
    });
  });
})();
