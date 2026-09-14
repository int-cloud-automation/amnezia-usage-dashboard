async function load() {
  const data = await api("/api/overview");
  document.getElementById("error").hidden = !data.collector.last_error;
  if (data.collector.last_error) {
    document.getElementById("error").textContent = data.collector.last_error;
  }
  document.getElementById("body").innerHTML = data.clients
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((c) => {
      const status = statusBadge(c);
      const action = c.disabled
        ? `<button class="btn sm" data-act="enable" data-key="${esc(c.public_key)}">Enable</button>`
        : `<button class="btn sm danger" data-act="disable" data-key="${esc(c.public_key)}">Disable</button>`;
      return `<tr>
        <td>${esc(c.name)}</td>
        <td>${status}</td>
        <td class="muted" style="font-family:var(--mono);font-size:12px">${esc(c.allowed_ips) || "—"}</td>
        <td>${fmtBytes(c.lifetime_tx)}</td>
        <td>${fmtBytes(c.lifetime_rx)}</td>
        <td class="muted">${handshakeAgo(c.handshake)}</td>
        <td>${action}</td>
      </tr>`;
    })
    .join("");
}

function showError(err) {
  const box = document.getElementById("error");
  box.hidden = false;
  box.textContent = err.message || String(err);
}

document.getElementById("btn-refresh").addEventListener("click", async () => {
  try {
    await api("/api/scrape", { method: "POST" });
    await load();
  } catch (e) {
    showError(e);
  }
});

document.getElementById("body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  btn.disabled = true;
  try {
    await api(`/api/peers/${btn.dataset.act}`, {
      method: "POST",
      body: JSON.stringify({ public_key: btn.dataset.key }),
    });
    await load();
  } catch (err) {
    showError(err);
    btn.disabled = false;
  }
});

document.addEventListener("DOMContentLoaded", load);
