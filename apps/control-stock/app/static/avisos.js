// Activar los avisos en ESTE celular.
//
// Lo único que el servidor no puede hacer por su cuenta: pedirle permiso
// al navegador y recoger la suscripción que él genera. Nada más vive aquí
// — a quién le toca el aviso, qué dice y a dónde lleva lo decide
// app/avisos.py en Python; esto solo manda el sobre con la dirección del
// celular a /avisos/suscribir y recarga la pantalla.

(function () {
  const boton = document.getElementById("activar-avisos");
  if (!boton) return;

  const soportado = "serviceWorker" in navigator && "PushManager" in window;
  if (!soportado) {
    boton.disabled = true;
    boton.textContent = "Este navegador no puede";
    return;
  }

  // La clave VAPID pública viaja en base64url; el navegador la quiere en
  // bytes.
  function aBytes(base64url) {
    const relleno = "=".repeat((4 - (base64url.length % 4)) % 4);
    const base64 = (base64url + relleno).replace(/-/g, "+").replace(/_/g, "/");
    const crudo = atob(base64);
    return Uint8Array.from([...crudo].map((c) => c.charCodeAt(0)));
  }

  boton.addEventListener("click", async () => {
    const original = boton.textContent;
    boton.disabled = true;
    boton.textContent = "Activando…";
    try {
      const permiso = await Notification.requestPermission();
      if (permiso !== "granted") {
        boton.textContent = "Permiso denegado";
        return;
      }
      const registro = await navigator.serviceWorker.register("/sw-avisos.js");
      await navigator.serviceWorker.ready;
      const suscripcion =
        (await registro.pushManager.getSubscription()) ||
        (await registro.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: aBytes(boton.dataset.clave),
        }));
      const respuesta = await fetch("/avisos/suscribir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(suscripcion.toJSON()),
      });
      if (!respuesta.ok) throw new Error("el servidor no la guardó");
      location.href = "/?tab=ajustes&aviso=avisos-activados";
    } catch (error) {
      boton.disabled = false;
      boton.textContent = "No se pudo · intenta de nuevo";
      setTimeout(() => (boton.textContent = original), 4000);
    }
  });
})();
