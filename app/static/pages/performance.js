const HTOP_MS = 1000;
let htopTimer;
let htopBusy = false;

function meterBar(pct, width = 24) {
  const n = Math.max(0, Math.min(100, Number(pct) || 0));
  const filled = Math.round((n / 100) * width);
  const mark = n >= 90 ? "#" : n >= 70 ? "=" : "|";
  return `[${mark.repeat(filled)}${" ".repeat(Math.max(0, width - filled))}]`;
}

function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `${Number(n).toFixed(1)}%`;
}

function fmtLoad(load) {
  if (!load || !load.length) return "—";
  return load.map((x) => Number(x).toFixed(2)).join(" ");
}

function renderHtop(data) {
  const box = document.getElementById("htop");
  if (!box) return;
  const memPct = data.mem_pct;
  const awg = data.amnezia || {};
  const procs = data.processes || [];
  const cores = data.cpus && data.cpus.length
    ? data.cpus
    : [{ id: "avg", pct: data.cpu_pct }];
  const cpuLines = cores
    .map((c) => {
      const label = typeof c.id === "number" ? `CPU${c.id}` : "CPU";
      return `<div><span class="htop-k">${esc(label)}</span> <span class="htop-meter">${meterBar(c.pct)}</span> <span class="htop-n">${fmtPct(c.pct)}</span></div>`;
    })
    .join("");
  const memBar = meterBar(memPct);
  const swapPct =
    data.swap_total_bytes > 0
      ? (100 * Number(data.swap_used_bytes || 0)) / Number(data.swap_total_bytes)
      : 0;
  const swapBar = meterBar(swapPct, 16);
  const memLine =
    data.mem_used_bytes != null && data.mem_total_bytes != null
      ? `${fmtBytes(data.mem_used_bytes)}/${fmtBytes(data.mem_total_bytes)}`
      : "—";
  const awgMem =
    awg.mem_used_bytes != null ? fmtBytes(awg.mem_used_bytes) : "—";
  const rows = procs
    .map((p) => {
      const cls = p.highlight ? ' class="htop-hi"' : "";
      return `<tr${cls}>
        <td>${esc(p.pid)}</td>
        <td>${esc(p.user)}</td>
        <td>${esc(p.state)}</td>
        <td>${fmtPct(p.cpu_pct)}</td>
        <td>${fmtPct(p.mem_pct)}</td>
        <td class="htop-cmd">${esc(p.command)}</td>
      </tr>`;
    })
    .join("");

  box.innerHTML = `
    <div class="htop-meters">
      ${cpuLines}
      <div><span class="htop-k">Avg</span> <span class="htop-meter">${meterBar(data.cpu_pct)}</span> <span class="htop-n">${fmtPct(data.cpu_pct)}</span> <span class="muted">${esc(data.cpu_count || cores.length)} cores</span></div>
      <div><span class="htop-k">Mem</span> <span class="htop-meter">${memBar}</span> <span class="htop-n">${esc(memLine)}</span> <span class="muted">${fmtPct(memPct)}</span></div>
      <div><span class="htop-k">Swp</span> <span class="htop-meter">${swapBar}</span> <span class="htop-n">${data.swap_total_bytes ? `${fmtBytes(data.swap_used_bytes)}/${fmtBytes(data.swap_total_bytes)}` : "0K/0K"}</span></div>
      <div><span class="htop-k">Load</span> <span class="htop-n">${esc(fmtLoad(data.load))}</span></div>
      <div class="htop-awg"><span class="htop-k">AmneziaWG</span> <span class="htop-n">${fmtPct(awg.cpu_pct)}</span> CPU · <span class="htop-n">${esc(awgMem)}</span> · <span class="muted">${esc(awg.container || "")}</span></div>
    </div>
    <div class="htop-table-wrap">
      <table class="htop-table">
        <thead>
          <tr>
            <th>PID</th>
            <th>USER</th>
            <th>S</th>
            <th>CPU%</th>
            <th>MEM%</th>
            <th>Command</th>
          </tr>
        </thead>
        <tbody>
          ${rows || `<tr><td colspan="6" class="muted">No processes</td></tr>`}
        </tbody>
      </table>
    </div>`;
}

async function loadHtop() {
  if (document.hidden || htopBusy) return;
  htopBusy = true;
  try {
    const data = await api("/api/htop");
    renderHtop(data || {});
    const meta = document.getElementById("htop-meta");
    if (meta) meta.textContent = data && data.error ? data.error : "live 1s";
  } catch (e) {
    console.error(e);
  } finally {
    htopBusy = false;
  }
}

function startHtop() {
  if (htopTimer) clearInterval(htopTimer);
  loadHtop();
  htopTimer = setInterval(loadHtop, HTOP_MS);
}

function stopHtop() {
  if (htopTimer) clearInterval(htopTimer);
  htopTimer = null;
}

document.addEventListener("DOMContentLoaded", startHtop);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopHtop();
  else startHtop();
});
