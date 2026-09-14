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
    const ok = data.collector.last_ok_at
      ? new Date(data.collector.last_ok_at).toLocaleString()
      : "never";
    document.getElementById("collector-meta").textContent =
      `auto ${REFRESH_MS / 1000}s · last ${ok} · days in ${data.collector.timezone}`;

    const online = data.clients.filter((c) => c.online);
    const list = document.getElementById("online-list");
    list.innerHTML = online.length
      ? online
          .map(
            (c) =>
              `<li><span><span class="dot on"></span>${esc(c.name)}</span><span class="muted">${handshakeAgo(c.handshake)}</span></li>`
          )
          .join("")
      : `<li class="muted">Nobody online</li>`;

    document.getElementById("clients-body").innerHTML = data.clients
      .slice()
      .sort(
        (a, b) =>
          b.lifetime_rx + b.lifetime_tx - (a.lifetime_rx + a.lifetime_tx)
      )
      .map((c) => {
        const status = c.disabled
          ? `<span class="badge off">disabled</span>`
          : c.online
            ? `<span class="badge on">online</span>`
            : `<span class="badge">offline</span>`;
        const down = c.online ? fmtRate(c.down_bps) : "—";
        const up = c.online ? fmtRate(c.up_bps) : "—";
        return `<tr>
          <td>${esc(c.name)}</td>
          <td>${status}</td>
          <td>${down}</td>
          <td>${up}</td>
          <td>${fmtBytes(c.lifetime_tx)}</td>
          <td>${fmtBytes(c.lifetime_rx)}</td>
          <td class="muted">${handshakeAgo(c.handshake)}</td>
        </tr>`;
      })
      .join("");

    if (chart) chart.destroy();
    chart = trafficChart(
      document.getElementById("traffic-chart"),
      data.series || []
    );
  } catch (e) {
    console.error(e);
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
