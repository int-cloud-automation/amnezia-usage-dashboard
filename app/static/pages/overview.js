let chart;
let timer;
const REFRESH_MS = 30000;

function setBar(el, pct) {
  if (!el) return;
  const n = Math.max(0, Math.min(100, Number(pct) || 0));
  el.style.width = `${n.toFixed(1)}%`;
  el.classList.toggle("warn", n >= 70 && n < 90);
  el.classList.toggle("hot", n >= 90);
}

function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `${Number(n).toFixed(1)}%`;
}

function fmtMemPair(used, total) {
  if (used == null || total == null) return "—";
  return `${fmtBytes(used)} / ${fmtBytes(total)}`;
}

function renderPerformance(perf) {
  const host = (perf && perf.host) || {};
  const awg = (perf && perf.amnezia) || {};
  document.getElementById("perf-host-cpu").textContent = fmtPct(host.cpu_pct);
  document.getElementById("perf-host-mem").textContent = fmtMemPair(
    host.mem_used_bytes,
    host.mem_total_bytes
  );
  setBar(document.getElementById("perf-host-cpu-bar"), host.cpu_pct);
  setBar(document.getElementById("perf-host-mem-bar"), host.mem_pct);

  document.getElementById("perf-awg-cpu").textContent = fmtPct(awg.cpu_pct);
  document.getElementById("perf-awg-mem").textContent =
    awg.mem_used_bytes != null ? fmtBytes(awg.mem_used_bytes) : "—";
  setBar(document.getElementById("perf-awg-cpu-bar"), awg.cpu_pct);
  setBar(document.getElementById("perf-awg-mem-bar"), awg.mem_pct);
  const cap = document.getElementById("perf-awg-caption");
  if (cap) {
    cap.textContent = awg.container
      ? `container ${awg.container}`
      : "container";
  }
}

async function load() {
  if (document.hidden) return;
  try {
    const data = await api("/api/overview");
    document.getElementById("error").hidden = !data.collector.last_error;
    if (data.collector.last_error) {
      document.getElementById("error").textContent = data.collector.last_error;
    }
    document.getElementById("m-online").textContent = data.online;
    document.getElementById("m-today").textContent = fmtBytes(data.today_bytes);
    document.getElementById("m-week").textContent = fmtBytes(data.week_bytes);
    document.getElementById("m-month").textContent = fmtBytes(data.month_bytes);
    document.getElementById("m-total").textContent = fmtBytes(data.total_bytes);
    renderPerformance(data.performance);

    const byStatus = (a, b) => {
      const rank = (c) => {
        if (c.disabled) return 3;
        const st = c.status || (c.online ? "online" : "offline");
        if (st === "online") return 0;
        if (st === "idle") return 1;
        return 2;
      };
      const traffic = (c) =>
        Number(c.lifetime_rx || 0) + Number(c.lifetime_tx || 0);
      return (
        rank(a) - rank(b) ||
        traffic(b) - traffic(a) ||
        String(a.name || "").localeCompare(String(b.name || ""))
      );
    };

    const online = data.clients.filter((c) => c.online).sort(byStatus);
    const list = document.getElementById("online-list");
    list.innerHTML = online.length
      ? online
          .map(
            (c) =>
              `<li><span><span class="dot ${c.status === "idle" ? "idle" : "on"}"></span>${esc(c.name)}</span><span class="muted">${c.status === "idle" ? "idle · " : ""}${handshakeAgo(c.handshake)}</span></li>`
          )
          .join("")
      : `<li class="muted">Nobody online</li>`;

    const rows = data.clients
      .slice()
      .sort(byStatus)
      .map((c) => {
        const down = c.status === "online" ? fmtRate(c.down_bps) : "—";
        const up = c.status === "online" ? fmtRate(c.up_bps) : "—";
        return `<tr>
          <td>${esc(c.name)}</td>
          <td>${statusBadge(c)}</td>
          <td>${down}</td>
          <td>${up}</td>
          <td>${fmtBytes(c.lifetime_tx)}</td>
          <td>${fmtBytes(c.lifetime_rx)}</td>
          <td class="muted">${handshakeAgo(c.handshake)}</td>
        </tr>`;
      })
      .join("");
    document.getElementById("clients-body").innerHTML =
      rows || `<tr><td colspan="7" class="muted">No clients yet</td></tr>`;

    try {
      if (chart) chart.destroy();
      chart = trafficChart(
        document.getElementById("traffic-chart"),
        data.series || []
      );
    } catch (chartErr) {
      console.error(chartErr);
    }
  } catch (e) {
    console.error(e);
    const box = document.getElementById("error");
    if (box) {
      box.hidden = false;
      box.textContent = e.message || String(e);
    }
  }
}

function startTimer() {
  if (timer) clearInterval(timer);
  timer = setInterval(load, REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", () => {
  load();
  startTimer();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load();
});
