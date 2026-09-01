import { useState, useMemo, useEffect } from "react";
import { Truck, Undo2, Repeat, History, Search, Plus, ChevronRight, ArrowLeft, Check, X, Leaf, PackageCheck, Sprout, AlertTriangle } from "lucide-react";
import logoSrc from "./assets/logo.jpg";
import iconoSrc from "./assets/icono.png";

/* =====================================================================
   CAPA DE DATOS (simula Odoo). Luego se reemplaza por llamadas a la API.
   ===================================================================== */
const EMPLEADO = { id: "emp-01", nombre: "Génesis" };

const CATALOGO = [
  ["VR-001", "Hierba Buena VR", 1.75], ["VR-002", "Romero VR", 1.75], ["VR-003", "Menta VR", 1.75], ["VR-004", "Ruda VR", 1.75],
  ["VR-005", "Chavelitas VR", 1.5], ["VR-006", "Mini Jade VR", 2.0], ["VR-007", "Jade VR", 3.2], ["VR-008", "Fitonia Roja VR", 2.25],
  ["VR-009", "Cactus Variados Pequeño VR", 1.95], ["VR-010", "Suculentas Variadas Pequeñas VR", 1.95], ["VR-011", "Phothus VR", 1.8],
  ["VR-012", "Millonaria Samiocula VR", 6.0], ["VR-013", "Coronita de Cristo VR", 2.25], ["VR-014", "Marigold VR", 1.98],
  ["VR-015", "Novio Chino VR", 2.25], ["VR-016", "Cielito Azul VR", 1.95], ["VR-017", "Zamioculca VR", 5.5], ["VR-018", "Sansevieria VR", 4.25],
  ["VR-019", "Calathea VR", 3.75], ["VR-020", "Fitonia Blanca VR", 2.25], ["VR-021", "Peperomia VR", 2.5], ["VR-022", "Echeveria VR", 1.95],
].map(([sku, nombre, precio]) => ({ sku, nombre, precio }));
const bySku = Object.fromEntries(CATALOGO.map((p) => [p.sku, p]));
const linea = (sku, enviado) => ({ sku, nombre: bySku[sku].nombre, precio: bySku[sku].precio, enviado });

// `factura` es la referencia que ve el empleado. Los demás IDs son técnicos (Odoo) y no se muestran.
// Los números de factura NO son consecutivos ni se generan aquí: vienen tal cual desde Odoo.
const ORDENES_ODOO = [
  {
    factura: "774", odooId: 10774, pedido: "S00774", reserva: "RES-2231", transferencia: "WH/OUT/00512",
    cliente: "Super Xtra", sucursal: "Villalobos", fecha: "2026-06-18", estado: "en_ruta",
    lineas: [linea("VR-001", 2), linea("VR-002", 3), linea("VR-003", 2), linea("VR-004", 3), linea("VR-005", 6), linea("VR-006", 4),
      linea("VR-007", 4), linea("VR-008", 4), linea("VR-009", 3), linea("VR-010", 2), linea("VR-011", 2), linea("VR-012", 4),
      linea("VR-013", 2), linea("VR-014", 3), linea("VR-015", 4), linea("VR-016", 2)],
  },
  {
    factura: "781", odooId: 10781, pedido: "S00781", reserva: "RES-2240", transferencia: "WH/OUT/00519",
    cliente: "Riba Smith", sucursal: "Bella Vista", fecha: "2026-06-18", estado: "en_ruta",
    lineas: [linea("VR-007", 4), linea("VR-012", 3), linea("VR-008", 4), linea("VR-002", 3), linea("VR-003", 2), linea("VR-005", 6),
      linea("VR-006", 4), linea("VR-009", 3), linea("VR-010", 2), linea("VR-011", 2), linea("VR-014", 3), linea("VR-016", 2)],
  },
  {
    factura: "770", odooId: 10770, pedido: "S00770", reserva: "RES-2225", transferencia: "WH/OUT/00508",
    cliente: "Super 99", sucursal: "Costa del Este", fecha: "2026-06-17", estado: "en_ruta",
    lineas: [linea("VR-017", 3), linea("VR-018", 2), linea("VR-019", 4), linea("VR-005", 6), linea("VR-013", 2), linea("VR-015", 3)],
  },
];
const fechaBonita = (iso) => { const [y, m, d] = iso.split("-"); return `${d}/${m}/${y}`; };
const SUCURSALES = [["Super Xtra", "Villalobos"], ["Riba Smith", "Bella Vista"], ["Super 99", "Costa del Este"], ["Supermercados Rey", "Vía España"]];

// Interfaz que mañana implementará la API real de Odoo.
const odooApi = {
  fetchOrdenes: async () => ORDENES_ODOO,
  fetchCatalogo: async () => CATALOGO,
  confirmarRecepcion: async (payload) => ({ ok: true, payload }),   // {ordenId, lineas:[{sku, aceptado, devuelto}], empleadoId, fechaHora}
  confirmarRegreso: async (payload) => ({ ok: true, payload }),     // {ordenId, lineas:[{sku, cantidad}], empleadoId, fechaHora}
  crearIntercambio: async (payload) => ({ ok: true, payload }),     // {cliente, sucursal, lineas:[{sku, danadas}], empleadoId, fechaHora}
  completarIntercambio: async (payload) => ({ ok: true, payload }), // {intercambioId, empleadoId, fechaHora}
};

/* ===================================================================== */
const C = {
  page: "#FDD671", bg: "#FFF7E3", card: "#FFFDF6", ink: "#5A4634", muted: "#9C8A74", line: "#F2E4C4",
  gold: "#F7C74E", sand: "#E9B36B", brown: "#6B5443",
  green: "#4F8F4A", greenSoft: "#E7F2E3", blue: "#4C7BB3", blueSoft: "#E4ECF7",
  orange: "#DD7E1F", orangeSoft: "#FCEBD5", red: "#C9614C",
};
const money = (n) => `B/.${n.toFixed(2)}`;
const ahora = () => new Date().toLocaleTimeString("es-PA", { hour: "numeric", minute: "2-digit" });
const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
const unidades = (n) => `${n} unidad${n === 1 ? "" : "es"}`;

/* ---------- UI ---------- */
function Btn({ children, onClick, color = C.brown, outline, disabled, small }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{ width: "100%", padding: small ? "14px 16px" : "19px 16px", borderRadius: 14, fontSize: small ? 17 : 19, fontWeight: 700, border: outline ? `2px solid ${C.sand}` : "none", background: outline ? "transparent" : color, color: outline ? C.ink : "#fff", opacity: disabled ? 0.4 : 1, cursor: disabled ? "default" : "pointer", boxShadow: outline || disabled ? "none" : "0 4px 0 rgba(90,70,52,.18)" }}>{children}</button>
  );
}
function Counter({ value, max, onChange, color = C.brown }) {
  const b = (label, fn, off) => (
    <button onClick={fn} disabled={off} style={{ width: 48, height: 48, borderRadius: 14, border: "none", background: off ? C.line : color, color: off ? C.muted : "#fff", fontSize: 28, fontWeight: 700, cursor: off ? "default" : "pointer", lineHeight: 1 }}>{label}</button>
  );
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      {b("−", () => onChange(value - 1), value <= 0)}
      <div style={{ width: 44, textAlign: "center", fontSize: 26, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {b("+", () => onChange(value + 1), value >= max)}
    </div>
  );
}
function Card({ children, style }) { return <div style={{ background: C.card, borderRadius: 18, padding: 16, border: `1px solid ${C.line}`, ...style }}>{children}</div>; }
function Pill({ children, color, soft }) { return <span style={{ background: soft, color, borderRadius: 999, padding: "5px 12px", fontSize: 13, fontWeight: 800, whiteSpace: "nowrap" }}>{children}</span>; }
function BackBtn({ onClick }) { return <button onClick={onClick} aria-label="Volver" style={{ width: 46, height: 46, borderRadius: 14, border: "none", background: C.card, color: C.brown, fontSize: 22, fontWeight: 800, cursor: "pointer", display: "grid", placeItems: "center", boxShadow: "0 3px 0 rgba(90,70,52,.18), 0 0 0 2px " + C.line, lineHeight: 1, flexShrink: 0 }}><ArrowLeft size={22} strokeWidth={2.5} /></button>; }
function Header({ title, sub, onBack, right }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
      {onBack && <BackBtn onClick={onBack} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, margin: 0, lineHeight: 1.15 }}>{title}</h1>
        {sub && <div style={{ fontSize: 15, color: C.muted, marginTop: 2 }}>{sub}</div>}
      </div>
      {right}
    </div>
  );
}
function Exito({ titulo, sub, lineas, onDone, done }) {
  return (
    <div style={{ textAlign: "center", paddingTop: 60 }}>
      <div style={{ width: 110, height: 110, borderRadius: "50%", background: C.green, margin: "0 auto 24px", display: "grid", placeItems: "center", boxShadow: `0 0 0 10px ${C.greenSoft}` }}><Check size={60} strokeWidth={3} color="#fff" /></div>
      <h1 style={{ fontSize: 28, fontWeight: 800, margin: "0 0 6px" }}>{titulo}</h1>
      <p style={{ fontSize: 18, color: C.muted, margin: "0 0 24px" }}>{sub}</p>
      <Card style={{ textAlign: "left" }}>{lineas.map(([k, v, col], i) => <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", fontSize: 18, borderBottom: i < lineas.length - 1 ? `1px solid ${C.line}` : "none" }}><span style={{ color: C.muted }}>{k}</span><span style={{ fontWeight: 800, color: col || C.ink }}>{v}</span></div>)}</Card>
      <div style={{ marginTop: 28 }}><Btn onClick={onDone}>{done}</Btn></div>
    </div>
  );
}
function Ola({ flip }) {
  return (
    <svg viewBox="0 0 420 160" preserveAspectRatio="none" style={{ position: "absolute", left: 0, right: 0, width: "100%", height: 150, pointerEvents: "none", zIndex: 0, ...(flip ? { bottom: 0, transform: "scaleX(-1)" } : { top: 0, transform: "rotate(180deg)" }) }}>
      <path d="M0 160 C 90 160, 110 60, 210 60 S 330 20, 420 0 L420 160 Z" fill={C.gold} opacity=".55" />
      <path d="M0 160 C 100 160, 120 100, 220 100 S 340 60, 420 40 L420 160 Z" fill={C.sand} opacity=".6" />
    </svg>
  );
}
function Buscador({ value, onChange, placeholder }) {
  return (
    <div style={{ position: "relative", marginBottom: 12 }}>
      <span style={{ position: "absolute", left: 14, top: 14, color: C.muted, display: "flex" }}><Search size={20} /></span>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} style={{ width: "100%", boxSizing: "border-box", padding: "14px 44px", borderRadius: 14, border: `2px solid ${C.line}`, background: C.card, fontSize: 17, color: C.ink, outline: "none" }} />
      {value && <button onClick={() => onChange("")} style={{ position: "absolute", right: 10, top: 9, width: 32, height: 32, borderRadius: 10, border: "none", background: C.line, color: C.ink, cursor: "pointer", display: "grid", placeItems: "center" }}><X size={16} /></button>}
    </div>
  );
}
function Resumen({ filas }) {
  return (
    <Card style={{ padding: "12px 16px" }}>
      {filas.map(([k, v, col, big], i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "5px 0", fontSize: big ? 19 : 16 }}>
          <span style={{ color: C.muted }}>{k}</span><span style={{ fontWeight: 800, color: col || C.ink, fontVariantNumeric: "tabular-nums" }}>{v}</span>
        </div>
      ))}
    </Card>
  );
}


/* ---------- Logo Plantas Panamá (imagen real de la marca) ---------- */
const LOGO_SRC = logoSrc;
const ICONO_SRC = iconoSrc;

function Logo({ size = 220, icon = false }) {
  if (icon) {
    return <img src={ICONO_SRC} alt="Plantas Panamá" width={size} height={size} style={{ display: "block", borderRadius: size * 0.23, boxShadow: "0 3px 0 rgba(90,70,52,.2)" }} />;
  }
  return (
    <div style={{ width: size, height: size, borderRadius: "50%", overflow: "hidden", boxShadow: `0 0 0 6px ${C.bg}, 0 0 0 9px ${C.sand}, 0 18px 40px rgba(90,70,52,.25)` }}>
      <img src={LOGO_SRC} alt="Plantas Panamá por Vivero Rose" style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
    </div>
  );
}

function Splash({ onDone }) {
  useEffect(() => { const t = setTimeout(onDone, 2400); return () => clearTimeout(t); }, []);
  return (
    <div onClick={onDone} style={{ position: "absolute", inset: 0, zIndex: 20, background: C.gold, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", cursor: "pointer", animation: "splashOut .5s ease 1.9s forwards" }}>
      <style>{`
        @keyframes logoIn { 0% { transform: scale(.6) rotate(-8deg); opacity: 0 } 60% { transform: scale(1.06) rotate(1deg); opacity: 1 } 100% { transform: scale(1) rotate(0) } }
        @keyframes textIn { from { transform: translateY(14px); opacity: 0 } to { transform: none; opacity: 1 } }
        @keyframes splashOut { to { opacity: 0; visibility: hidden } }
        @keyframes leaf { 0%,100% { transform: translateY(0) rotate(0) } 50% { transform: translateY(-6px) rotate(4deg) } }
        @media (prefers-reduced-motion: reduce) { * { animation-duration: .01ms !important } }
      `}</style>
      <div style={{ animation: "logoIn .9s cubic-bezier(.2,.9,.3,1.2) both" }}><Logo size={240} /></div>
      <div style={{ marginTop: 26, textAlign: "center", animation: "textIn .6s ease .7s both" }}>
        <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: 3, color: C.brown }}>APP DE ENTREGAS</div>
        <div style={{ fontSize: 14, color: C.brown, opacity: .7, marginTop: 6 }}>Cargando tus entregas…</div>
      </div>
      <div style={{ position: "absolute", bottom: 34, display: "flex", gap: 8, animation: "textIn .6s ease 1s both" }}>
        {[0, 1, 2].map((i) => <span key={i} style={{ width: 8, height: 8, borderRadius: 4, background: C.brown, opacity: .5, animation: `leaf 1s ease ${i * .15}s infinite` }} />)}
      </div>
    </div>
  );
}

/* ---------- Cálculos de una orden ---------- */
const calc = (orden, aceptado) => {
  let env = 0, acep = 0, tOrig = 0, tAcep = 0, dif = [];
  for (const l of orden.lineas) {
    const a = aceptado[l.sku];
    env += l.enviado; acep += a; tOrig += l.enviado * l.precio; tAcep += a * l.precio;
    if (a !== l.enviado) dif.push({ ...l, aceptado: a, devuelto: l.enviado - a });
  }
  return { env, acep, dev: env - acep, tOrig, tAcep, tDev: tOrig - tAcep, dif };
};

/* ===================================================================== */
export default function App() {
  const [ordenes, setOrdenes] = useState(ORDENES_ODOO);
  const [devoluciones, setDevoluciones] = useState([]); // {ordenId, cliente, sucursal, lineas:[{sku,nombre,cantidad,precio}]}
  const [intercambios, setIntercambios] = useState([]); // {id, cliente, sucursal, lineas:[{sku,nombre,danadas,precio}], estado, hora}
  const [historial, setHistorial] = useState([]);
  const [tab, setTab] = useState("pendientes");
  const [pantalla, setPantalla] = useState("home");
  const [splash, setSplash] = useState(true);

  const [ordenId, setOrdenId] = useState(null);
  const [aceptado, setAceptado] = useState({});
  const [resultado, setResultado] = useState(null);

  // intercambio
  const [sucSel, setSucSel] = useState(null);
  const [busq, setBusq] = useState("");
  const [danadas, setDanadas] = useState({}); // sku -> cantidad

  const orden = ordenes.find((o) => o.factura === ordenId);
  const [busqFact, setBusqFact] = useState("");
  const coincide = (o) => { const q = norm(busqFact.trim()); return !q || o.factura.includes(q) || norm(o.cliente).includes(q) || norm(o.sucursal).includes(q); };
  // Orden por fecha (más reciente primero); nunca por número de factura.
  const pendientes = ordenes.filter((o) => o.estado === "en_ruta").filter(coincide).sort((a, b) => (a.fecha < b.fecha ? 1 : a.fecha > b.fecha ? -1 : 0));
  const intPend = intercambios.filter((i) => i.estado === "pendiente");

  const abrirOrden = (id) => {
    const o = ordenes.find((x) => x.factura === id);
    setOrdenId(id);
    setAceptado(Object.fromEntries(o.lineas.map((l) => [l.sku, l.enviado]))); // ACEPTADO = ENVIADO
    setPantalla("orden");
  };
  const restablecer = () => setAceptado(Object.fromEntries(orden.lineas.map((l) => [l.sku, l.enviado])));

  const confirmarRecepcion = async () => {
    const r = calc(orden, aceptado);
    await odooApi.confirmarRecepcion({ odooId: orden.odooId, factura: orden.factura, lineas: orden.lineas.map((l) => ({ sku: l.sku, aceptado: aceptado[l.sku], devuelto: l.enviado - aceptado[l.sku] })), empleadoId: EMPLEADO.id, fechaHora: new Date().toISOString() });
    setOrdenes((l) => l.map((o) => (o.factura === orden.factura ? { ...o, estado: "entregada", aceptado: { ...aceptado } } : o)));
    if (r.dev > 0) setDevoluciones((d) => [...d, { ordenId: orden.factura, odooId: orden.odooId, cliente: orden.cliente, sucursal: orden.sucursal, lineas: r.dif.map((l) => ({ sku: l.sku, nombre: l.nombre, cantidad: l.devuelto, precio: l.precio })) }]);
    setHistorial((h) => [{ tipo: "entrega", hora: ahora(), ordenId: orden.factura, cliente: orden.cliente, sucursal: orden.sucursal, acep: r.acep, dev: r.dev, tAcep: r.tAcep, regreso: r.dev === 0 }, ...h]);
    setResultado({ orden, ...r });
    setPantalla("entrega-ok");
  };

  const confirmarRegreso = async (ordenIdDev) => {
    const d = devoluciones.find((x) => x.ordenId === ordenIdDev);
    await odooApi.confirmarRegreso({ odooId: d.odooId, factura: d.ordenId, lineas: d.lineas.map((l) => ({ sku: l.sku, cantidad: l.cantidad })), empleadoId: EMPLEADO.id, fechaHora: new Date().toISOString() });
    setDevoluciones((l) => l.filter((x) => x.ordenId !== ordenIdDev));
    setHistorial((h) => h.map((x) => (x.tipo === "entrega" && x.ordenId === ordenIdDev ? { ...x, regreso: true } : x)));
  };

  // --- intercambio ---
  const limpiarInt = () => { setSucSel(null); setBusq(""); setDanadas({}); };
  const lineasInt = Object.entries(danadas).filter(([, q]) => q > 0).map(([sku, q]) => ({ sku, nombre: bySku[sku].nombre, precio: bySku[sku].precio, danadas: q }));
  const totalInt = lineasInt.reduce((t, l) => t + l.danadas, 0);
  const confirmarIntercambio = async () => {
    const nuevo = { id: `I-${Date.now()}`, cliente: sucSel[0], sucursal: sucSel[1], lineas: lineasInt, estado: "pendiente", hora: ahora() };
    await odooApi.crearIntercambio({ cliente: nuevo.cliente, sucursal: nuevo.sucursal, lineas: lineasInt.map((l) => ({ sku: l.sku, danadas: l.danadas })), empleadoId: EMPLEADO.id, fechaHora: new Date().toISOString() });
    setIntercambios((l) => [...l, nuevo]);
    setResultado(nuevo);
    setPantalla("intercambio-ok");
  };
  const completarIntercambio = async (id) => {
    await odooApi.completarIntercambio({ intercambioId: id, empleadoId: EMPLEADO.id, fechaHora: new Date().toISOString() });
    setIntercambios((l) => l.map((i) => (i.id === id ? { ...i, estado: "completado" } : i)));
    const i = intercambios.find((x) => x.id === id);
    setHistorial((h) => [{ tipo: "intercambio", hora: ahora(), cliente: i.cliente, sucursal: i.sucursal, total: i.lineas.reduce((t, l) => t + l.danadas, 0), valor: i.lineas.reduce((t, l) => t + l.danadas * l.precio, 0) }, ...h]);
  };

  let contenido;

  /* ---------- HOME ---------- */
  if (pantalla === "home") {
    contenido = (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 1.5, color: C.brown }}>PLANTAS PANAMÁ</div>
            <h1 style={{ fontSize: 24, fontWeight: 800, margin: "2px 0 0" }}>Hola, {EMPLEADO.nombre}</h1>
          </div>
          <Logo icon size={44} />
        </div>

        {tab === "pendientes" && (
          <>
            <Buscador value={busqFact} onChange={setBusqFact} placeholder="Buscar factura, súper o sucursal…" />
            <h2 style={{ fontSize: 15, fontWeight: 700, color: C.muted, margin: "0 0 10px" }}>Entregas pendientes · {pendientes.length}</h2>
            {pendientes.length === 0 && <Card style={{ textAlign: "center", color: C.muted, padding: 30 }}>{busqFact ? `No hay entregas pendientes con “${busqFact}”.` : "No hay entregas pendientes. Las nuevas llegan desde Odoo."}</Card>}
            <div style={{ display: "grid", gap: 8 }}>
              {pendientes.map((o) => (
                <button key={o.factura} onClick={() => abrirOrden(o.factura)} style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", textAlign: "left", cursor: "pointer", padding: "12px 12px 12px 14px", borderRadius: 16, border: `1px solid ${C.line}`, borderLeft: `5px solid ${C.blue}`, background: C.card, color: C.ink }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                      <span style={{ fontSize: 19, fontWeight: 800, color: C.blue }}>#{o.factura}</span>
                      <span style={{ fontSize: 12, color: C.muted }}>{fechaBonita(o.fecha)}</span>
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 700, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{o.cliente} · {o.sucursal}</div>
                    <div style={{ fontSize: 13, color: C.muted, marginTop: 2 }}>{o.lineas.length} productos · {money(o.lineas.reduce((t, l) => t + l.enviado * l.precio, 0))}</div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8, flexShrink: 0 }}>
                    <Pill color={C.blue} soft={C.blueSoft}>En ruta</Pill>
                    <span style={{ fontSize: 13, fontWeight: 800, color: C.brown, display: "flex", alignItems: "center" }}>Abrir <ChevronRight size={16} strokeWidth={3} /></span>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {tab === "devoluciones" && (
          <>
            <h2 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 2px" }}>Devoluciones</h2>
            <p style={{ fontSize: 14, color: C.muted, margin: "0 0 12px" }}>Plantas que un súper rechazó y van de regreso al vivero.</p>
            {devoluciones.length === 0 && (
              <div style={{ textAlign: "center", color: C.muted, padding: "24px 12px" }}>
                <div style={{ marginBottom: 8, color: C.sand, display: "flex", justifyContent: "center" }}><Undo2 size={44} /></div>
                <p style={{ fontSize: 16, margin: 0 }}>Nada por regresar. Cuando registres una entrega y bajes alguna cantidad, esas plantas aparecen aquí para confirmar su regreso al vivero.</p>
              </div>
            )}
            <div style={{ display: "grid", gap: 12 }}>
              {devoluciones.map((d) => (
                <Card key={d.ordenId} style={{ borderLeft: `6px solid ${C.orange}` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                    <div><div style={{ fontSize: 20, fontWeight: 800 }}>{d.cliente} · {d.sucursal}</div><div style={{ fontSize: 15, color: C.muted }}>Factura #{d.ordenId}</div></div>
                    <Pill color={C.orange} soft={C.orangeSoft}>Devolución pendiente</Pill>
                  </div>
                  <div style={{ margin: "12px 0 14px" }}>
                    {d.lineas.map((l) => <div key={l.sku} style={{ display: "flex", justifyContent: "space-between", fontSize: 17, padding: "6px 0", borderBottom: `1px solid ${C.line}` }}><span>{l.nombre}</span><span style={{ fontWeight: 800, color: C.orange }}>{unidades(l.cantidad)}</span></div>)}
                  </div>
                  <Btn color={C.orange} onClick={() => confirmarRegreso(d.ordenId)}>Confirmar regreso</Btn>
                </Card>
              ))}
            </div>
          </>
        )}

        {tab === "intercambios" && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "0 0 12px" }}>
              <div><h2 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Intercambios</h2><p style={{ fontSize: 14, color: C.muted, margin: "2px 0 0" }}>Plantas dañadas que recoges y reemplazas.</p></div>
              <button onClick={() => { limpiarInt(); setPantalla("int-sucursal"); }} aria-label="Nuevo intercambio" style={{ width: 46, height: 46, borderRadius: 14, border: "none", background: C.brown, color: "#fff", cursor: "pointer", display: "grid", placeItems: "center", boxShadow: "0 4px 0 rgba(90,70,52,.18)" }}><Plus size={26} strokeWidth={3} /></button>
            </div>
            {intercambios.length === 0 && (
              <div style={{ textAlign: "center", color: C.muted, padding: "24px 12px" }}>
                <div style={{ marginBottom: 8, color: C.sand, display: "flex", justifyContent: "center" }}><Repeat size={44} /></div>
                <p style={{ fontSize: 16, margin: "0 0 18px" }}>Cuando recojas plantas dañadas en un súper, regístralas aquí para llevar el reemplazo.</p>
                <Btn onClick={() => { limpiarInt(); setPantalla("int-sucursal"); }}>+ Nuevo intercambio</Btn>
              </div>
            )}
            <div style={{ display: "grid", gap: 12 }}>
              {intercambios.slice().reverse().map((i) => {
                const n = i.lineas.reduce((t, l) => t + l.danadas, 0);
                return (
                  <Card key={i.id} style={{ borderLeft: `6px solid ${i.estado === "pendiente" ? C.orange : C.green}` }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                      <div><div style={{ fontSize: 20, fontWeight: 800 }}>{i.cliente} · {i.sucursal}</div><div style={{ fontSize: 15, color: C.muted }}>{i.hora} · {unidades(n)} dañadas</div></div>
                      {i.estado === "pendiente" ? <Pill color={C.orange} soft={C.orangeSoft}>Pendiente de devolver</Pill> : <Pill color={C.green} soft={C.greenSoft}>Completado</Pill>}
                    </div>
                    <div style={{ margin: "10px 0 0" }}>
                      {i.lineas.map((l) => <div key={l.sku} style={{ display: "flex", justifyContent: "space-between", fontSize: 15, padding: "5px 0", borderBottom: `1px solid ${C.line}`, color: C.muted }}><span>{l.nombre}</span><span style={{ fontWeight: 800, color: C.ink }}>{l.danadas}</span></div>)}
                    </div>
                    {i.estado === "pendiente" && <div style={{ marginTop: 14 }}><Btn small onClick={() => completarIntercambio(i.id)}>Confirmar reemplazo entregado</Btn></div>}
                  </Card>
                );
              })}
            </div>
          </>
        )}

        {tab === "historial" && (
          <>
            <h2 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 12px" }}>Historial</h2>
            {historial.length === 0 && <Card style={{ textAlign: "center", color: C.muted, padding: 30 }}>Aún no hay movimientos hoy.</Card>}
            <div style={{ display: "grid", gap: 10 }}>
              {historial.map((h, idx) => (
                <Card key={idx} style={{ padding: "12px 16px", borderLeft: `6px solid ${C.green}` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                    <div>
                      <div style={{ fontSize: 18, fontWeight: 800, display: "flex", alignItems: "center", gap: 6 }}><Check size={18} strokeWidth={3} color={C.green} /> {h.cliente} · {h.sucursal}</div>
                      <div style={{ fontSize: 14, color: C.muted }}>{h.hora}{h.tipo === "entrega" ? ` · Factura #${h.ordenId}` : " · Intercambio"}</div>
                    </div>
                    {h.tipo === "entrega"
                      ? (h.regreso ? <Pill color={C.green} soft={C.greenSoft}>Completada</Pill> : <Pill color={C.orange} soft={C.orangeSoft}>Regreso pendiente</Pill>)
                      : <Pill color={C.green} soft={C.greenSoft}>Reemplazado</Pill>}
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 15 }}>
                    {h.tipo === "entrega"
                      ? <><span>{h.acep} aceptadas{h.dev > 0 && <span style={{ color: C.orange }}> · {h.dev} devuelta{h.dev === 1 ? "" : "s"}</span>}</span><span style={{ fontWeight: 800 }}>{money(h.tAcep)}</span></>
                      : <><span>{unidades(h.total)} reemplazadas</span><span style={{ fontWeight: 800 }}>{money(h.valor)}</span></>}
                  </div>
                </Card>
              ))}
            </div>
          </>
        )}
      </>
    );
  }

  /* ---------- ORDEN ---------- */
  if (pantalla === "orden" && orden) {
    const r = calc(orden, aceptado);
    contenido = (
      <>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <BackBtn onClick={() => setPantalla("home")} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 20, fontWeight: 800, lineHeight: 1.15 }}>{orden.cliente}</div>
            <div style={{ fontSize: 15, color: C.muted }}>{orden.sucursal}</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, background: C.blueSoft, borderRadius: 14, padding: "10px 14px", marginBottom: 10 }}>
          <div><span style={{ fontSize: 18, fontWeight: 800, color: C.blue }}>Factura #{orden.factura}</span><span style={{ fontSize: 13, color: C.muted, marginLeft: 8 }}>{fechaBonita(orden.fecha)}</span></div>
          <span style={{ fontSize: 12, fontWeight: 800, color: C.blue }}>Pendiente</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, background: r.dif.length ? C.orangeSoft : C.greenSoft, color: r.dif.length ? C.orange : C.green, borderRadius: 14, padding: "9px 12px", marginBottom: 10, fontSize: 14, fontWeight: 700 }}>
          <span style={{ display: "flex", flexShrink: 0 }}>{r.dif.length ? <AlertTriangle size={20} /> : <Check size={20} strokeWidth={3} />}</span>
          <span style={{ flex: 1 }}>{r.dif.length ? `${r.dif.length} producto${r.dif.length > 1 ? "s tienen" : " tiene"} diferencias` : "Todo aceptado. Baja solo lo que el súper no recibió."}</span>
          {r.dif.length > 0 && <button onClick={restablecer} style={{ border: "none", background: C.card, color: C.orange, borderRadius: 10, padding: "6px 10px", fontSize: 13, fontWeight: 800, cursor: "pointer" }}>Restablecer</button>}
        </div>

        <div style={{ display: "grid", gap: 8 }}>
          {orden.lineas.map((l) => {
            const a = aceptado[l.sku], dev = l.enviado - a, ok = dev === 0;
            return (
              <div key={l.sku} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 10px 10px 14px", borderRadius: 16, background: ok ? C.card : C.orangeSoft, border: `2px solid ${ok ? C.line : C.orange}` }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.2 }}>{l.nombre.replace(/ VR$/, "")}</div>
                  <div style={{ fontSize: 13, color: C.muted, marginTop: 3 }}>{ok ? `Enviado ${l.enviado}` : <span style={{ color: C.orange, fontWeight: 800 }}>{dev} devuelta{dev === 1 ? "" : "s"} de {l.enviado}</span>}</div>
                </div>
                <Counter value={a} max={l.enviado} onChange={(v) => setAceptado({ ...aceptado, [l.sku]: v })} color={ok ? C.green : C.orange} />
              </div>
            );
          })}
        </div>

        <div style={{ position: "sticky", bottom: 0, background: C.bg, paddingTop: 12, marginTop: 10, zIndex: 2 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <div style={{ flex: 1, background: C.greenSoft, borderRadius: 14, padding: "8px 12px" }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.green }}>Aceptado</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: C.green, lineHeight: 1.1 }}>{r.acep} <span style={{ fontSize: 13, color: C.muted, fontWeight: 700 }}>/ {r.env}</span></div>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.green }}>{money(r.tAcep)}</div>
            </div>
            <div style={{ flex: 1, background: r.dev ? C.orangeSoft : C.card, borderRadius: 14, padding: "8px 12px", border: r.dev ? "none" : `1px solid ${C.line}` }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: r.dev ? C.orange : C.muted }}>Devuelto</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: r.dev ? C.orange : C.muted, lineHeight: 1.1 }}>{r.dev}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: r.dev ? C.orange : C.muted }}>{money(r.tDev)}</div>
            </div>
          </div>
          <Btn color={C.green} onClick={() => setPantalla("orden-confirm")}>Confirmar recepción</Btn>
        </div>
      </>
    );
  }

  if (pantalla === "orden-confirm" && orden) {
    const r = calc(orden, aceptado);
    contenido = (
      <>
        <Header title={r.dif.length ? "Revisa las diferencias" : "Todo aceptado"} sub={`${orden.cliente} · ${orden.sucursal} · Factura #${orden.factura}`} onBack={() => setPantalla("orden")} />
        {r.dif.length === 0 ? (
          <Card style={{ textAlign: "center", padding: 28 }}>
            <div style={{ color: C.green, display: "flex", justifyContent: "center" }}><PackageCheck size={48} /></div>
            <p style={{ fontSize: 19, fontWeight: 700, margin: "8px 0 4px" }}>El supermercado aceptó toda la mercancía.</p>
            <p style={{ fontSize: 17, color: C.muted, margin: 0 }}>{r.acep} de {r.env} unidades aceptadas · {money(r.tAcep)}</p>
          </Card>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {r.dif.map((l) => (
              <Card key={l.sku} style={{ borderLeft: `6px solid ${C.orange}`, padding: "12px 16px" }}>
                <div style={{ fontSize: 17, fontWeight: 800, textTransform: "uppercase" }}>{l.nombre}</div>
                <div style={{ display: "flex", gap: 16, marginTop: 6, fontSize: 16 }}>
                  <span style={{ color: C.muted }}>Enviado <b style={{ color: C.ink }}>{l.enviado}</b></span>
                  <span style={{ color: C.muted }}>Aceptado <b style={{ color: C.green }}>{l.aceptado}</b></span>
                  <span style={{ color: C.muted }}>Devuelto <b style={{ color: C.orange }}>{l.devuelto}</b></span>
                </div>
              </Card>
            ))}
            <div style={{ marginTop: 6 }}>
              <Resumen filas={[["Enviado", r.env], ["Aceptado", r.acep, C.green], ["Devuelto", r.dev, C.orange], ["Valor devuelto", money(r.tDev), C.orange, true]]} />
            </div>
          </div>
        )}
        <div style={{ display: "grid", gap: 12, marginTop: 24 }}>
          <Btn color={C.green} onClick={confirmarRecepcion}>{r.dif.length ? "Confirmar recepción" : "Confirmar"}</Btn>
          <Btn outline onClick={() => setPantalla("orden")}>{r.dif.length ? "Volver a revisar" : "Cancelar"}</Btn>
        </div>
      </>
    );
  }

  if (pantalla === "entrega-ok") {
    const r = resultado;
    contenido = <Exito titulo="Entrega registrada" sub={`${r.orden.cliente} - ${r.orden.sucursal} · Factura #${r.orden.factura}`} done="Volver a entregas" onDone={() => { setPantalla("home"); setTab(r.dev > 0 ? "devoluciones" : "historial"); }}
      lineas={[["Aceptadas", unidades(r.acep), C.green], ["Devueltas", unidades(r.dev), r.dev ? C.orange : C.muted], ["Aceptado", money(r.tAcep), C.green], ["Devuelto", money(r.tDev), r.dev ? C.orange : C.muted]]} />;
  }

  /* ---------- INTERCAMBIO ---------- */
  if (pantalla === "int-sucursal") {
    contenido = (
      <>
        <Header title="¿En qué súper estás?" onBack={() => setPantalla("home")} />
        <div style={{ display: "grid", gap: 12 }}>
          {SUCURSALES.map(([c, s]) => (
            <button key={c + s} onClick={() => { setSucSel([c, s]); setPantalla("int-plantas"); }} style={{ padding: "20px", borderRadius: 18, border: `1px solid ${C.line}`, background: C.card, textAlign: "left", cursor: "pointer", color: C.ink }}>
              <div style={{ fontSize: 21, fontWeight: 800 }}>{c}</div><div style={{ fontSize: 16, color: C.muted }}>{s}</div>
            </button>
          ))}
        </div>
      </>
    );
  }

  if (pantalla === "int-plantas") {
    const resultados = busq.trim() ? CATALOGO.filter((p) => norm(p.nombre).includes(norm(busq)) && !(danadas[p.sku] > 0)).slice(0, 8) : [];
    contenido = (
      <>
        <Header title="¿Qué plantas se dañaron?" sub={`${sucSel[0]} · ${sucSel[1]}`} onBack={() => setPantalla("int-sucursal")} />
        <div style={{ position: "sticky", top: 0, background: C.bg, zIndex: 2 }}>
          <Buscador value={busq} onChange={setBusq} placeholder="Escribe el nombre de la planta…" />
          {busq.trim() && (
            <div style={{ background: C.card, border: `2px solid ${C.line}`, borderRadius: 16, overflow: "hidden", marginTop: -4, marginBottom: 14, boxShadow: "0 8px 24px rgba(90,70,52,.12)" }}>
              {resultados.length === 0 && <div style={{ padding: 16, color: C.muted, textAlign: "center" }}>No encontré “{busq}”.</div>}
              {resultados.map((p) => (
                <button key={p.sku} onClick={() => { setDanadas({ ...danadas, [p.sku]: 1 }); setBusq(""); }} style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", textAlign: "left", cursor: "pointer", padding: "14px 16px", border: "none", borderBottom: `1px solid ${C.line}`, background: "transparent", color: C.ink }}>
                  <span style={{ flex: 1, fontSize: 17, fontWeight: 700 }}>{p.nombre}</span>
                  <span style={{ width: 34, height: 34, borderRadius: 10, display: "grid", placeItems: "center", background: C.brown, color: "#fff" }}><Plus size={20} strokeWidth={3} /></span>
                </button>
              ))}
            </div>
          )}
        </div>
        {lineasInt.length === 0 ? (
          <div style={{ textAlign: "center", padding: "40px 16px", color: C.muted }}><div style={{ marginBottom: 10, color: C.sand, display: "flex", justifyContent: "center" }}><Sprout size={48} /></div><p style={{ fontSize: 17, margin: 0 }}>Busca cada planta dañada y tócala para agregarla. Luego ajusta cuántas.</p></div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {lineasInt.map((l) => (
              <div key={l.sku} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px 10px 14px", borderRadius: 16, background: C.orangeSoft, border: `2px solid ${C.orange}` }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.2 }}>{l.nombre}</div>
                  <button onClick={() => { const d = { ...danadas }; delete d[l.sku]; setDanadas(d); }} style={{ border: "none", background: "none", color: C.orange, fontSize: 13, fontWeight: 800, padding: 0, marginTop: 4, cursor: "pointer" }}>Quitar</button>
                </div>
                <Counter value={l.danadas} max={99} color={C.orange} onChange={(v) => setDanadas({ ...danadas, [l.sku]: v })} />
              </div>
            ))}
          </div>
        )}
        <div style={{ position: "sticky", bottom: 0, background: C.bg, paddingTop: 14, marginTop: 10, zIndex: 2 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 18, marginBottom: 10 }}><span style={{ color: C.muted }}>Total dañadas</span><span style={{ fontWeight: 800 }}>{totalInt}</span></div>
          <Btn disabled={totalInt === 0} onClick={() => setPantalla("int-confirm")}>{totalInt === 0 ? "Agrega al menos una planta" : "Revisar intercambio"}</Btn>
        </div>
      </>
    );
  }

  if (pantalla === "int-confirm") {
    const valor = lineasInt.reduce((t, l) => t + l.danadas * l.precio, 0);
    contenido = (
      <>
        <Header title="Confirmar intercambio" sub={`${sucSel[0]} · ${sucSel[1]}`} onBack={() => setPantalla("int-plantas")} />
        <Card>
          {lineasInt.map((l) => <div key={l.sku} style={{ display: "flex", justifyContent: "space-between", fontSize: 17, padding: "9px 0", borderBottom: `1px solid ${C.line}` }}><span>{l.nombre}</span><span style={{ fontWeight: 800, color: C.orange }}>{l.danadas}</span></div>)}
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 20, fontWeight: 800, paddingTop: 12 }}><span>Total dañadas</span><span>{totalInt}</span></div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 16, color: C.muted, paddingTop: 4 }}><span>Valor a reemplazar</span><span style={{ fontWeight: 700 }}>{money(valor)}</span></div>
        </Card>
        <p style={{ fontSize: 14, color: C.muted, textAlign: "center", margin: "16px 0 0" }}>Las dañadas se registran y el reemplazo queda pendiente hasta que lo entregues en el súper.</p>
        <div style={{ display: "grid", gap: 12, marginTop: 16 }}>
          <Btn onClick={confirmarIntercambio}>Confirmar intercambio</Btn>
          <Btn outline onClick={() => setPantalla("int-plantas")}>Volver a revisar</Btn>
        </div>
      </>
    );
  }

  if (pantalla === "intercambio-ok") {
    const i = resultado, n = i.lineas.reduce((t, l) => t + l.danadas, 0);
    contenido = <Exito titulo="Intercambio registrado" sub={`${i.cliente} · ${i.sucursal}`} done="Volver a intercambios" onDone={() => { setPantalla("home"); setTab("intercambios"); }}
      lineas={[["Dañadas recogidas", unidades(n), C.orange], ["Reemplazo", "Pendiente de entregar", C.orange]]} />;
  }

  const conOlas = ["entrega-ok", "intercambio-ok", "int-sucursal"].includes(pantalla);
  const NAV = [
    ["pendientes", "Entregas", Truck, pendientes.length],
    ["devoluciones", "Devolver", Undo2, devoluciones.length],
    ["intercambios", "Cambios", Repeat, intPend.length],
    ["historial", "Historial", History, 0],
  ];
  return (
    <div style={{ minHeight: "100dvh", background: C.page, display: "flex", justifyContent: "center", fontFamily: "Montserrat, -apple-system, 'Segoe UI', Roboto, system-ui, sans-serif", color: C.ink }}>
      <div style={{ position: "relative", width: "100%", maxWidth: 480, height: "100dvh", background: C.bg, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {splash && <Splash onDone={() => setSplash(false)} />}
        {conOlas && <Ola />}{conOlas && <Ola flip />}
        <div style={{ position: "relative", zIndex: 1, flex: 1, overflowY: "auto", padding: "8px 16px 20px" }}>{contenido}</div>
        {pantalla === "home" && (
          <nav style={{ flexShrink: 0, display: "flex", background: C.card, borderTop: `1px solid ${C.line}`, padding: "8px 6px 14px", boxShadow: "0 -8px 24px rgba(90,70,52,.08)" }}>
            {NAV.map(([k, label, Ico, n]) => {
              const on = tab === k;
              return (
                <button key={k} onClick={() => { setTab(k); setBusqFact(""); }} style={{ flex: 1, border: "none", background: "transparent", cursor: "pointer", display: "flex", flexDirection: "column", alignItems: "center", gap: 3, padding: "4px 0", color: on ? C.brown : C.muted }}>
                  <span style={{ position: "relative", width: 52, height: 32, borderRadius: 16, background: on ? C.gold : "transparent", display: "grid", placeItems: "center", transition: "background .15s" }}>
                    <Ico size={22} strokeWidth={on ? 2.5 : 2} />
                    {n > 0 && <span style={{ position: "absolute", top: -4, right: 2, background: C.orange, color: "#fff", borderRadius: 999, fontSize: 10, fontWeight: 800, minWidth: 17, height: 17, display: "grid", placeItems: "center", padding: "0 4px", boxShadow: `0 0 0 2px ${C.card}` }}>{n}</span>}
                  </span>
                  <span style={{ fontSize: 11, fontWeight: on ? 800 : 600 }}>{label}</span>
                </button>
              );
            })}
          </nav>
        )}
      </div>
    </div>
  );
}
