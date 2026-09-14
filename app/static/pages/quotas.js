function showError(err) {
  const box = document.getElementById("error");
  box.hidden = false;
  box.textContent = err.message || String(err);
}

const PERIOD_LABELS = {
  day: "Today",
  week: "Last 7 days",
  month: "Calendar month",
  total: "Lifetime",
};

async function load() {
  const data = await api("/api/quotas");
  const sel = document.getElementById("peer");
  const current = sel.value;
  sel.innerHTML = (data.peers || [])
    .map((p) => `<option value="${esc(p.public_key)}">${esc(p.name)}</option>`)
    .join("");
  if (current) sel.value = current;

  document.getElementById("body").innerHTML =
    (data.quotas || [])
      .map((q) => {
        const pct = Math.min(100, (q.used_bytes / q.limit_bytes) * 100);
        const status = q.disabled
          ? `<span class="badge off">disabled</span>`
          : pct >= 100
            ? `<span class="badge off">over</span>`
            : `<span class="badge on">ok</span>`;
        return `<tr>
        <td>${esc(q.name)}</td>
        <td>${esc(PERIOD_LABELS[q.period] || q.period)}</td>
        <td>${fmtBytes(q.used_bytes)}</td>
        <td>${fmtBytes(q.limit_bytes)}</td>
        <td><div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div></td>
        <td>${status}</td>
        <td><button class="btn sm danger" data-del="${esc(q.public_key)}">Delete</button></td>
      </tr>`;
      })
      .join("") || `<tr><td colspan="7" class="muted">No quotas yet</td></tr>`;
}

document.getElementById("quota-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  document.getElementById("error").hidden = true;
  try {
    await api("/api/quotas", {
      method: "POST",
      body: JSON.stringify({
        public_key: document.getElementById("peer").value,
        limit_gb: Number(document.getElementById("limit").value),
        period: document.getElementById("period").value,
        auto_disable: document.getElementById("auto").value === "1",
        enabled: true,
      }),
    });
    await load();
  } catch (err) {
    showError(err);
  }
});

document.getElementById("body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-del]");
  if (!btn) return;
  try {
    await api("/api/quotas", {
      method: "DELETE",
      body: JSON.stringify({ public_key: btn.dataset.del }),
    });
    await load();
  } catch (err) {
    showError(err);
  }
});

document.addEventListener("DOMContentLoaded", load);
