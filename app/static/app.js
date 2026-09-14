const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

// Client names come from Amnezia clientsTable and may contain device-supplied text.
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

function statusBadge(c) {
  if (c.disabled) return `<span class="badge off">disabled</span>`;
  const st = c.status || (c.online ? "online" : "offline");
  if (st === "online") return `<span class="badge on">online</span>`;
  if (st === "idle") return `<span class="badge idle">idle</span>`;
  return `<span class="badge">offline</span>`;
}

function fmtBytes(n) {
  n = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return i === 0 ? `${Math.round(n)} ${u[i]}` : `${n.toFixed(1)} ${u[i]}`;
}

function fmtRate(bps) {
  const n = Number(bps) || 0;
  if (n <= 0) return "—";
  // bits per second for network feel
  const bits = n * 8;
  if (bits < 1000) return `${Math.round(bits)} bit/s`;
  if (bits < 1_000_000) return `${(bits / 1000).toFixed(1)} Kbit/s`;
  if (bits < 1_000_000_000) return `${(bits / 1_000_000).toFixed(2)} Mbit/s`;
  return `${(bits / 1_000_000_000).toFixed(2)} Gbit/s`;
}

function handshakeAgo(ts) {
  if (!ts) return "never";
  const delta = Math.floor(Date.now() / 1000) - ts;
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = data.detail || message;
    } catch (e) {
      /* non-JSON error body */
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

function chartDefaults() {
  Chart.defaults.color = "#8b9bb0";
  Chart.defaults.borderColor = "#2c3a50";
  Chart.defaults.font.family = "'IBM Plex Sans', sans-serif";
}

// The server already returns a gap-free series in the panel's timezone,
// so days must not be recomputed here.
function trimSeries(series) {
  const out = (series || []).map((s) => ({
    day: s.day,
    rx: Number(s.rx) || 0,
    tx: Number(s.tx) || 0,
    active_peers: Number(s.active_peers) || 0,
  }));
  const first = out.findIndex((s) => s.rx > 0 || s.tx > 0);
  if (first > 0) return out.slice(first);
  if (first < 0) return out.slice(-7);
  return out;
}

function trafficChart(canvas, series, { stacked = true, trimLeading = false } = {}) {
  chartDefaults();
  const points = trimLeading ? trimSeries(series) : (series || []);
  const labels = points.map((s) => String(s.day).slice(5));
  // Server RX is what clients uploaded, server TX is what they downloaded.
  const upBytes = points.map((s) => Number(s.rx) || 0);
  const downBytes = points.map((s) => Number(s.tx) || 0);
  const active = points.map((s) => Number(s.active_peers) || 0);
  const maxVal = Math.max(...upBytes, ...downBytes, 0);
  const maxPeers = Math.max(...active, 0);
  const useMb = maxVal > 0 && maxVal < 1024 ** 3;
  const div = useMb ? 1024 ** 2 : 1024 ** 3;
  const unit = useMb ? "MB" : "GB";
  const pointRadius = points.length > 45 ? 0 : points.length > 1 ? 2 : 4;
  return new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: `Downloaded ${unit}`,
          data: downBytes.map((v) => v / div),
          borderColor: "#3ecf8e",
          backgroundColor: "rgba(62,207,142,0.12)",
          fill: stacked,
          tension: 0.25,
          pointRadius,
          pointHoverRadius: 4,
          borderWidth: 2,
          yAxisID: "y",
        },
        {
          label: `Uploaded ${unit}`,
          data: upBytes.map((v) => v / div),
          borderColor: "#f5a524",
          backgroundColor: "rgba(245,165,36,0.15)",
          fill: stacked,
          tension: 0.25,
          pointRadius,
          pointHoverRadius: 4,
          borderWidth: 2,
          yAxisID: "y",
        },
        {
          label: "Active clients",
          data: active,
          borderColor: "#7aa2f7",
          backgroundColor: "transparent",
          fill: false,
          tension: 0.2,
          pointRadius: 0,
          pointHoverRadius: 3,
          borderWidth: 1.5,
          borderDash: [4, 3],
          yAxisID: "yPeers",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: true, position: "bottom" } },
      scales: {
        x: {
          grid: { color: "#2c3a50" },
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
        },
        y: {
          beginAtZero: true,
          suggestedMax: maxVal === 0 ? 1 : undefined,
          grid: { color: "#2c3a50" },
          title: { display: true, text: unit },
        },
        yPeers: {
          beginAtZero: true,
          suggestedMax: maxPeers < 2 ? 2 : undefined,
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { stepSize: 1, precision: 0 },
          title: { display: true, text: "clients" },
        },
      },
    },
  });
}
