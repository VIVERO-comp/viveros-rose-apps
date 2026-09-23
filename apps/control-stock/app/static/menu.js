/* El menú de la app, en sus dos caras:

   - Computadora: el botón .hamb colapsa el sidebar (pedido del dueño,
     22/09/2026: "como el de Google Calendar"). La preferencia viaja en la
     cookie `menu` y el SERVIDOR pinta la página ya colapsada en la
     próxima carga; aquí solo se voltea la clase y se guarda.
   - Teléfono (dueño, 23/09/2026): el botón flotante .hamb-movil abre el
     menú como cajón desde la izquierda (body.menu-abierto). Es un estado
     del momento, no una preferencia: sin cookie. Tocar el fondo oscuro o
     cualquier opción del menú lo cierra. */
(function () {
  'use strict';

  var botones = document.querySelectorAll('.hamb');
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

  var flotante = document.querySelector('.hamb-movil');
  if (!flotante) return;

  flotante.addEventListener('click', function (ev) {
    ev.stopPropagation();
    var abierto = document.body.classList.toggle('menu-abierto');
    flotante.setAttribute('aria-expanded', String(abierto));
  });

  // El fondo oscuro es un ::before del body: cualquier toque fuera del
  // cajón lo cierra, y una opción tocada dentro también (navega igual,
  // pero las pestañas del Inicio cambian sin recargar y el cajón no debe
  // quedarse abierto encima).
  document.addEventListener('click', function (ev) {
    if (!document.body.classList.contains('menu-abierto')) return;
    if (flotante.contains(ev.target)) return;
    document.body.classList.remove('menu-abierto');
    flotante.setAttribute('aria-expanded', 'false');
  });
})();
