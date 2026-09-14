let chart;

async function load() {
  const days = Number(document.getElementById("days").value);
  const data = await api(`/api/history?days=${days}`);
  document.getElementById("note").textContent =
    data.note || "Period = recorded by this panel. Lifetime = total since the peer was created.";

  if (chart) chart.destroy();
  chart = trafficChart(document.getElementById("chart"), data.series || [], {
    trimLeading: true,
  });

  const rows = data.by_client || [];
  const grand =
    rows.reduce((s, r) => s + Number(r.rx || 0) + Number(r.tx || 0), 0) || 1;
  document.getElementById("body").innerHTML =
    rows
      .map((r) => {
        const total = Number(r.rx || 0) + Number(r.tx || 0);
        const pct = Math.min(100, (total / grand) * 100);
        return `<tr>
        <td>${esc(r.name)}</td>
        <td>${fmtBytes(r.tx)}</td>
        <td>${fmtBytes(r.rx)}</td>
        <td>${fmtBytes(total)}</td>
        <td>${fmtBytes(r.lifetime_tx)}</td>
        <td>${fmtBytes(r.lifetime_rx)}</td>
        <td><div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div></td>
      </tr>`;
      })
      .join("") || `<tr><td colspan="7" class="muted">No clients yet</td></tr>`;
}

document.getElementById("days").addEventListener("change", load);
document.addEventListener("DOMContentLoaded", load);
