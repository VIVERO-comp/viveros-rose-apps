/* Compras/Gastos: el JS solo PINTA.
   Qué es válido, qué cuenta como destino y qué sube al inventario se decide
   en Python (app/compras.py y app/main.py); aquí solo se muestran y ocultan
   cajas, se piden resultados ya calculados al servidor y se arman las filas
   del formulario. */
(function () {
  "use strict";

  // ---- chips de radio (categoría y destino): marcar el elegido ----
  document.querySelectorAll(".chips-form").forEach(function (grupo) {
    grupo.addEventListener("change", function () {
      grupo.querySelectorAll("label.chip").forEach(function (etiqueta) {
        var radio = etiqueta.querySelector("input");
        etiqueta.classList.toggle("on", radio && radio.checked);
      });
      if (grupo.id === "chips-destino") pintarDestino();
    });
  });

  // ---- destino: solo se ve la caja del destino elegido ----
  function destinoElegido() {
    var marcado = document.querySelector('input[name="destino"]:checked');
    return marcado ? marcado.value : "vivero";
  }

  function pintarDestino() {
    var destino = destinoElegido();
    document.querySelectorAll(".destino-caja").forEach(function (caja) {
      caja.style.display = caja.dataset.destino === destino ? "" : "none";
    });
    // Una compra de proyecto no toca el inventario (regla del negocio, también
    // validada en el servidor y en el modelo de Odoo).
    var inventario = document.getElementById("caja-inventario");
    var titulo = document.getElementById("seccion-inventario");
    if (inventario) inventario.style.display = destino === "proyecto" ? "none" : "";
    if (titulo) titulo.style.display = destino === "proyecto" ? "none" : "";
  }
  pintarDestino();

  // ---- buscador de ventas ----
  var campoVenta = document.getElementById("busca-venta-compra");
  var cajaVentas = document.getElementById("resultados-ventas");
  var idVenta = document.getElementById("orden-id");
  if (campoVenta && cajaVentas) {
    campoVenta.addEventListener("input", retrasar(function () {
      var texto = campoVenta.value.trim();
      fetch("/compras/ventas?q=" + encodeURIComponent(texto))
        .then(function (r) { return r.json(); })
        .then(function (datos) {
          cajaVentas.innerHTML = "";
          (datos.ventas || []).forEach(function (venta) {
            var boton = document.createElement("button");
            boton.type = "button";
            boton.className = "resultado-compra";
            boton.innerHTML = "<b>" + venta.ref + "</b><small>" + venta.cliente + "</small>";
            boton.addEventListener("click", function () {
              idVenta.value = venta.id;
              campoVenta.value = venta.ref + " · " + venta.cliente;
              cajaVentas.innerHTML = "";
            });
            cajaVentas.appendChild(boton);
          });
        });
    }, 180));
  }

  // ---- buscador de plantas e insumos que entran al inventario ----
  var campoProducto = document.getElementById("busca-producto-compra");
  var cajaProductos = document.getElementById("resultados-productos");
  var cajaLineas = document.getElementById("lineas-compra");
  var cuantas = 0;

  if (campoProducto && cajaProductos && cajaLineas) {
    campoProducto.addEventListener("input", retrasar(function () {
      var texto = campoProducto.value.trim();
      if (!texto) { cajaProductos.innerHTML = ""; return; }
      fetch("/compras/productos?q=" + encodeURIComponent(texto))
        .then(function (r) { return r.json(); })
        .then(function (datos) {
          cajaProductos.innerHTML = "";
          (datos.productos || []).forEach(function (producto) {
            var boton = document.createElement("button");
            boton.type = "button";
            boton.className = "resultado-compra";
            boton.innerHTML = "<b>" + producto.nombre + "</b><small>" +
              producto.sku + " · hoy hay " + producto.hay + "</small>";
            boton.addEventListener("click", function () {
              agregarLinea(producto);
              cajaProductos.innerHTML = "";
              campoProducto.value = "";
            });
            cajaProductos.appendChild(boton);
          });
        });
    }, 180));
  }

  function agregarLinea(producto) {
    cuantas += 1;
    var n = cuantas;
    var fila = document.createElement("div");
    fila.className = "linea-producto";
    fila.innerHTML =
      '<div class="med"><b>' + producto.nombre + "</b><small>" + producto.sku +
      " · hoy hay " + producto.hay + "</small></div>" +
      '<input type="hidden" name="producto_' + n + '" value="' + producto.id + '">' +
      '<input class="cant-compra" name="cantidad_' + n + '" inputmode="numeric" value="1" aria-label="Cantidad">' +
      '<input class="costo-compra" name="costo_' + n + '" inputmode="decimal" placeholder="c/u" aria-label="Costo por unidad">' +
      '<button type="button" class="quitar-linea" aria-label="Quitar">×</button>';
    fila.querySelector(".quitar-linea").addEventListener("click", function () {
      fila.remove();
    });
    cajaLineas.appendChild(fila);
  }

  // ---- buscador de la lista: filtra en vivo lo que ya está en pantalla ----
  var campoLista = document.getElementById("busca-compra");
  if (campoLista) {
    campoLista.addEventListener("input", function () {
      var texto = campoLista.value.trim().toLowerCase();
      document.querySelectorAll(".compra-item, .tarjeta-cuenta").forEach(function (item) {
        var visible = !texto || item.textContent.toLowerCase().indexOf(texto) !== -1;
        item.style.display = visible ? "" : "none";
      });
    });
  }

  function retrasar(fn, ms) {
    var reloj = null;
    return function () {
      clearTimeout(reloj);
      reloj = setTimeout(fn, ms);
    };
  }
})();
