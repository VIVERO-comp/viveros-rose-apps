// El service worker de los avisos de Control Viverorose.
//
// A propósito NO tiene manejador de `fetch`: no cachea ni intercepta nada
// de la app (igual que el sw-avisos.js del sitio). Solo enseña el aviso
// que manda app/avisos.py — el texto ya viene armado desde Python: aquí
// no se decide nada — y abre la pantalla del chat al tocarlo.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (evento) => evento.waitUntil(self.clients.claim()));

self.addEventListener("push", (evento) => {
  let datos = {};
  try {
    datos = evento.data ? evento.data.json() : {};
  } catch (e) {
    datos = {};
  }
  const url = datos.url || "/control";
  evento.waitUntil(
    self.registration.showNotification(datos.titulo || "Control Viverorose", {
      body: datos.cuerpo || "",
      icon: "/static/logo.jpg",
      badge: "/static/logo.jpg",
      tag: url, // un mismo chat reemplaza su aviso viejo en vez de apilarlo
      renotify: true,
      data: { url: url },
    })
  );
});

self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  const url = (evento.notification.data && evento.notification.data.url) || "/control";
  evento.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((abiertas) => {
      for (const ventana of abiertas) {
        if ("focus" in ventana) {
          ventana.navigate && ventana.navigate(url);
          return ventana.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
