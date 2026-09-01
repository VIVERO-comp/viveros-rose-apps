// Interacciones de la app. Sin frameworks: lo que el navegador trae.

// ---------- Splash (1 segundo, solo la primera vez por sesión) ----------
(function () {
  const splash = document.getElementById("splash");
  if (!splash) return;
  let visto = false;
  try {
    visto = sessionStorage.getItem("splash") === "1";
  } catch (e) { /* modo privado sin storage: se muestra siempre */ }
  if (visto) {
    splash.hidden = true;
    return;
  }
  const cerrar = () => {
    splash.hidden = true;
    try { sessionStorage.setItem("splash", "1"); } catch (e) {}
  };
  splash.addEventListener("click", cerrar);
  setTimeout(cerrar, 1200); // 1 s de splash + el desvanecido de CSS
})();

// ---------- Normalización de texto (igual que el prototipo) ----------
function normalizar(texto) {
  return texto.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

// ---------- Buscador de entregas (filtra al teclear, sin red) ----------
(function () {
  const entrada = document.getElementById("buscador");
  if (!entrada) return;
  const limpiar = document.getElementById("limpiar-buscador");
  const lista = document.getElementById("lista-entregas");
  const cuenta = document.getElementById("cuenta-pendientes");
  const vacio = document.getElementById("vacio-entregas");
  const vacioTexto = document.getElementById("vacio-texto");
  const tarjetas = lista ? Array.from(lista.children) : [];

  const filtrar = () => {
    const q = normalizar(entrada.value.trim());
    let visibles = 0;
    for (const tarjeta of tarjetas) {
      const coincide = !q || normalizar(tarjeta.dataset.buscar).includes(q);
      tarjeta.hidden = !coincide;
      if (coincide) visibles++;
    }
    cuenta.textContent = visibles;
    limpiar.hidden = !entrada.value;
    const sinResultados = visibles === 0;
    vacio.hidden = !sinResultados;
    if (sinResultados) {
      vacioTexto.textContent = q
        ? `No hay entregas pendientes con “${entrada.value.trim()}”.`
        : "No hay entregas pendientes. Las nuevas llegan desde Odoo.";
    }
  };

  entrada.addEventListener("input", filtrar);
  limpiar.addEventListener("click", () => {
    entrada.value = "";
    filtrar();
    entrada.focus();
  });
})();

const dinero = (n) => "B/." + n.toFixed(2);

// ---------- Contadores de la orden (todo en vivo, sin red) ----------
(function () {
  const lista = document.getElementById("lineas-orden");
  if (!lista) return;
  const filas = Array.from(lista.querySelectorAll(".linea-producto"));
  const aviso = document.getElementById("aviso-orden");
  const avisoOk = document.getElementById("aviso-ok");
  const avisoMal = document.getElementById("aviso-mal");
  const avisoTexto = document.getElementById("aviso-texto");
  const restablecer = document.getElementById("restablecer");
  const cajaDevuelto = document.getElementById("caja-devuelto");

  const valorDe = (fila) => parseInt(fila.querySelector(".valor").textContent, 10);

  function pintarFila(fila) {
    const enviado = parseInt(fila.dataset.enviado, 10);
    const valor = valorDe(fila);
    const devueltas = enviado - valor;
    const ok = devueltas === 0;
    fila.classList.toggle("difiere", !ok);
    const contador = fila.querySelector(".contador");
    contador.classList.toggle("verde", ok);
    contador.classList.toggle("naranja", !ok);
    fila.querySelector(".menos").disabled = valor <= 0;
    fila.querySelector(".mas").disabled = valor >= enviado;
    fila.querySelector(".estado").textContent = ok
      ? `Enviado ${enviado}`
      : `${devueltas} devuelta${devueltas === 1 ? "" : "s"} de ${enviado}`;
    fila.querySelector("input[type=hidden]").value = valor;
  }

  function actualizarTotales() {
    let env = 0, acep = 0, tOrig = 0, tAcep = 0, dif = 0;
    for (const fila of filas) {
      const enviado = parseInt(fila.dataset.enviado, 10);
      const precio = parseFloat(fila.dataset.precio);
      const valor = valorDe(fila);
      env += enviado; acep += valor;
      tOrig += enviado * precio; tAcep += valor * precio;
      if (valor !== enviado) dif++;
    }
    const dev = env - acep;
    document.getElementById("caja-acep").textContent = acep;
    document.getElementById("caja-env").textContent = env;
    document.getElementById("caja-acep-monto").textContent = dinero(tAcep);
    document.getElementById("caja-dev").textContent = dev;
    document.getElementById("caja-dev-monto").textContent = dinero(tOrig - tAcep);
    cajaDevuelto.classList.toggle("naranja", dev > 0);
    cajaDevuelto.classList.toggle("neutra", dev === 0);
    aviso.classList.toggle("ok", dif === 0);
    aviso.classList.toggle("difiere", dif > 0);
    avisoOk.hidden = dif > 0;
    avisoMal.hidden = dif === 0;
    restablecer.hidden = dif === 0;
    avisoTexto.textContent = dif
      ? `${dif} producto${dif > 1 ? "s tienen" : " tiene"} diferencias`
      : "Todo aceptado. Baja solo lo que el súper no recibió.";
  }

  function cambiar(fila, delta) {
    const enviado = parseInt(fila.dataset.enviado, 10);
    const nuevo = Math.max(0, Math.min(valorDe(fila) + delta, enviado));
    fila.querySelector(".valor").textContent = nuevo;
    pintarFila(fila);
    actualizarTotales();
  }

  for (const fila of filas) {
    fila.querySelector(".menos").addEventListener("click", () => cambiar(fila, -1));
    fila.querySelector(".mas").addEventListener("click", () => cambiar(fila, +1));
    pintarFila(fila);
  }
  restablecer.addEventListener("click", () => {
    for (const fila of filas) {
      fila.querySelector(".valor").textContent = fila.dataset.enviado;
      pintarFila(fila);
    }
    actualizarTotales();
  });
  actualizarTotales();
})();

// ---------- Intercambio: filtro de sucursales (38 opciones, sin red) ----------
(function () {
  const entrada = document.getElementById("buscar-sucursal");
  if (!entrada) return;
  const limpiar = document.getElementById("limpiar-sucursal");
  const lista = document.getElementById("lista-sucursales");
  const vacio = document.getElementById("vacio-sucursales");
  const opciones = Array.from(lista.children);

  const filtrar = () => {
    const q = normalizar(entrada.value.trim());
    let visibles = 0;
    for (const opcion of opciones) {
      const coincide = !q || normalizar(opcion.dataset.buscar).includes(q);
      opcion.hidden = !coincide;
      if (coincide) visibles++;
    }
    limpiar.hidden = !entrada.value;
    vacio.hidden = visibles > 0;
  };

  entrada.addEventListener("input", filtrar);
  limpiar.addEventListener("click", () => { entrada.value = ""; filtrar(); entrada.focus(); });
})();

// ---------- Intercambio: buscar plantas dañadas y ajustar cantidades ----------
(function () {
  const entrada = document.getElementById("buscar-planta");
  if (!entrada) return;
  const catalogo = JSON.parse(document.getElementById("catalogo-json").textContent);
  const iniciales = JSON.parse(document.getElementById("danadas-json").textContent);
  const porSku = Object.fromEntries(catalogo.map((p) => [p.sku, p]));
  const limpiar = document.getElementById("limpiar-planta");
  const resultados = document.getElementById("resultados");
  const listaDanadas = document.getElementById("lista-danadas");
  const vacio = document.getElementById("vacio-danadas");
  const total = document.getElementById("total-danadas");
  const botonRevisar = document.getElementById("boton-revisar");
  const formulario = document.getElementById("form-int");
  const plantilla = document.getElementById("plantilla-danada");
  const danadas = {}; // sku -> cantidad

  function actualizar() {
    const cuantas = Object.values(danadas).reduce((t, n) => t + n, 0);
    total.textContent = cuantas;
    vacio.hidden = cuantas > 0;
    botonRevisar.disabled = cuantas === 0;
    botonRevisar.textContent = cuantas === 0 ? "Agrega al menos una planta" : "Revisar intercambio";
    // Los campos ocultos d_<sku> viajan con el form al revisar.
    formulario.querySelectorAll("input[name^='d_']").forEach((i) => i.remove());
    for (const [sku, cantidad] of Object.entries(danadas)) {
      const campo = document.createElement("input");
      campo.type = "hidden";
      campo.name = "d_" + sku;
      campo.value = cantidad;
      formulario.appendChild(campo);
    }
  }

  function agregarLinea(sku, cantidad) {
    danadas[sku] = cantidad;
    const nodo = plantilla.content.cloneNode(true).firstElementChild;
    nodo.dataset.sku = sku;
    nodo.querySelector(".nombre").textContent = porSku[sku].nombre;
    nodo.querySelector(".valor").textContent = cantidad;
    const pintar = () => {
      nodo.querySelector(".valor").textContent = danadas[sku];
      nodo.querySelector(".menos").disabled = danadas[sku] <= 0;
      nodo.querySelector(".mas").disabled = danadas[sku] >= 99;
    };
    nodo.querySelector(".menos").addEventListener("click", () => {
      danadas[sku] = Math.max(0, danadas[sku] - 1); pintar(); actualizar();
    });
    nodo.querySelector(".mas").addEventListener("click", () => {
      danadas[sku] = Math.min(99, danadas[sku] + 1); pintar(); actualizar();
    });
    nodo.querySelector(".quitar").addEventListener("click", () => {
      delete danadas[sku]; nodo.remove(); actualizar();
    });
    listaDanadas.appendChild(nodo);
    pintar();
  }

  function buscar() {
    const q = normalizar(entrada.value.trim());
    limpiar.hidden = !entrada.value;
    if (!q) { resultados.hidden = true; resultados.innerHTML = ""; return; }
    const encontradas = catalogo
      .filter((p) => normalizar(p.nombre).includes(q) && !(danadas[p.sku] > 0))
      .slice(0, 8);
    resultados.innerHTML = "";
    if (encontradas.length === 0) {
      const nada = document.createElement("div");
      nada.className = "nada";
      nada.textContent = `No encontré “${entrada.value.trim()}”.`;
      resultados.appendChild(nada);
    }
    for (const p of encontradas) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "resultado-planta";
      boton.innerHTML = '<span class="datos"><span class="nombre"></span></span><span class="agregar">+</span>';
      boton.querySelector(".nombre").textContent = p.nombre;
      // Disponibilidad real (viene del stock-proxy). Con datos de prueba es
      // null y no se muestra. Cero avisa en naranja pero NO bloquea: la
      // planta dañada se recoge igual y el reemplazo puede ir después.
      if (p.disponible !== null && p.disponible !== undefined) {
        const info = document.createElement("span");
        info.className = "info" + (p.disponible <= 0 ? " agotado" : "");
        info.textContent = p.disponible <= 0
          ? "Disponible: 0 — sin stock para el reemplazo"
          : `Disponible: ${p.disponible}`;
        boton.querySelector(".datos").appendChild(info);
      }
      boton.addEventListener("click", () => {
        agregarLinea(p.sku, 1);
        entrada.value = "";
        buscar();
        actualizar();
      });
      resultados.appendChild(boton);
    }
    resultados.hidden = false;
  }

  entrada.addEventListener("input", buscar);
  limpiar.addEventListener("click", () => { entrada.value = ""; buscar(); entrada.focus(); });
  for (const [sku, cantidad] of Object.entries(iniciales)) agregarLinea(sku, cantidad);
  actualizar();
})();
