let chart;
let timer;
const REFRESH_MS = 30000;

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

    const online = data.clients.filter((c) => c.online);
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
      .sort(
        (a, b) =>
          Number(b.lifetime_rx || 0) +
          Number(b.lifetime_tx || 0) -
          (Number(a.lifetime_rx || 0) + Number(a.lifetime_tx || 0))
      )
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
