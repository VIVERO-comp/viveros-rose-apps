/* Pedidos: el tablero se actualiza solo, sin molestar. (30/09/2026)
 *
 * Es el ÚNICO JavaScript propio de la pantalla —todo lo demás lo decide
 * Python y llega pintado— y existe porque Abraham lo pidió así: «que se
 * actualice solo», para que el tablero sirva de pizarra del día en el
 * teléfono de Mary y de los repartidores.
 *
 * Las cuatro reglas de "sin molestar", que son el motivo de cada línea:
 *
 *   1. Solo si la pestaña está VISIBLE. Recargar una pestaña de fondo gasta
 *      Linear y Odoo para nadie.
 *   2. Nunca con el formulario de la fecha abierto (el .modal) ni con el
 *      teclado dentro de un campo: recargar ahí borra lo que la persona
 *      estaba escribiendo.
 *   3. Conserva el scroll. Regla del proyecto: volver a una lista nunca
 *      manda al tope.
 *   4. Recarga a /pedidos LIMPIO, sin el ?aviso= de la acción anterior: así
 *      el banner no se queda pegado para siempre.
 */
(function () {
  'use strict';

  var CADA_MS = 60000;     // el minuto que se le prometió al dueño
  var LATIDO_MS = 15000;   // cada cuánto se pregunta si ya toca
  var LLAVE = 'pedidos-scroll';

  var zona = document.getElementById('tiq-zona');
  var nacio = Date.now();

  function guardarScroll() {
    try {
      // Los dos: en computadora scrollea la zona, en el teléfono la página.
      sessionStorage.setItem(LLAVE, JSON.stringify({
        zona: zona ? zona.scrollTop : 0,
        pagina: window.scrollY || window.pageYOffset || 0
      }));
    } catch (e) {
      /* modo privado, almacenamiento bloqueado: el refresco sigue valiendo */
    }
  }

  function restaurarScroll() {
    var guardado = null;
    try {
      guardado = JSON.parse(sessionStorage.getItem(LLAVE) || 'null');
      sessionStorage.removeItem(LLAVE);
    } catch (e) {
      return;
    }
    if (!guardado) return;
    if (zona && guardado.zona) zona.scrollTop = guardado.zona;
    if (guardado.pagina) window.scrollTo(0, guardado.pagina);
  }

  function estaEscribiendo() {
    var foco = document.activeElement;
    if (!foco || !foco.tagName) return false;
    return /^(INPUT|SELECT|TEXTAREA)$/.test(foco.tagName) || foco.isContentEditable;
  }

  function sePuede() {
    if (document.visibilityState !== 'visible') return false;
    if (document.querySelector('.modal')) return false;
    return !estaEscribiendo();
  }

  function intentar() {
    if (Date.now() - nacio < CADA_MS) return;
    if (!sePuede()) return;
    guardarScroll();
    // replace y no assign: el refresco automático no deja historial, así
    // que el botón "atrás" sigue llevando a donde la persona venía.
    window.location.replace('/pedidos');
  }

  restaurarScroll();
  setInterval(intentar, LATIDO_MS);
  // Al volver a la pestaña, si ya se pasó el minuto se refresca de una en
  // vez de esperar el próximo latido.
  document.addEventListener('visibilitychange', intentar);
})();
