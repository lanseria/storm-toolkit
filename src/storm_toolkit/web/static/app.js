// Storm Toolkit 前端逻辑：活跃列表 + 关注切换 + 路径历史表格
// 每 5 分钟自动刷新活跃列表；关注切换立即生效并乐观更新

const ACTIVE_REFRESH_MS = 5 * 60 * 1000;
const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function codeClass(code) {
  const cls = {
    D: "code-d", S: "code-s",
    "1": "code-1", "2": "code-2", "3": "code-3", "4": "code-4", "5": "code-5",
  }[code];
  return cls || "";
}

async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

async function loadActiveStorms() {
  try {
    const data = await fetchJson("/api/storms/active");
    const storms = data.storms || [];
    $("last-updated").textContent =
      `活跃列表更新于 ${formatTime(data.fetched_at)}（共 ${storms.length} 个）`;
    $("active-count").textContent = storms.length;
    renderStormGrid(storms);
  } catch (e) {
    $("last-updated").textContent = `加载失败：${e.message}`;
    $("storm-grid").innerHTML = `<div class="empty">加载活跃列表失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderStormGrid(storms) {
  if (storms.length === 0) {
    $("storm-grid").innerHTML = '<div class="empty">当前无活跃台风。</div>';
    return;
  }
  $("storm-grid").innerHTML = storms.map((s) => `
    <div class="card ${s.kind === "disturbance" ? "disturbance" : ""}">
      <div class="title-row">
        <div>
          <h3>${escapeHtml(s.id)}</h3>
          <span class="id-tag">点击「详情」获取实时数据</span>
        </div>
      </div>
      <div class="tags">
        <span class="tag ${s.kind === "storm" ? "kind-storm" : "kind-disturbance"}">
          ${s.kind === "storm" ? "命名风暴" : "扰动"}
        </span>
      </div>
      <div class="actions">
        <button type="button" onclick="loadDetail('${escapeHtml(s.id)}')">详情</button>
        ${s.watched
          ? `<button type="button" class="danger" onclick="unwatch('${escapeHtml(s.id)}')">取消关注</button>`
          : `<button type="button" class="primary" onclick="watch('${escapeHtml(s.id)}')">关注</button>`}
      </div>
    </div>
  `).join("");
}

async function loadDetail(stormId) {
  const card = Array.from(document.querySelectorAll(".card")).find((c) =>
    c.textContent.includes(stormId)
  );
  if (card) {
    card.querySelector(".id-tag").textContent = "加载中...";
  }
  try {
    const d = await fetchJson(`/api/storms/${encodeURIComponent(stormId)}`);
    if (card) {
      card.querySelector(".id-tag").textContent =
        `${escapeHtml(d.type || "")} · ${escapeHtml(d.agencies || "")} · ${d.track.length} 点`;
    }
  } catch (e) {
    if (card) {
      card.querySelector(".id-tag").textContent = `获取失败：${e.message}`;
    }
  }
}

async function watch(stormId) {
  try {
    await fetchJson(`/api/watchlist/${encodeURIComponent(stormId)}`, { method: "POST" });
    await Promise.all([loadActiveStorms(), loadWatched()]);
  } catch (e) {
    alert(`关注失败：${e.message}`);
  }
}

async function unwatch(stormId) {
  try {
    await fetchJson(`/api/watchlist/${encodeURIComponent(stormId)}`, { method: "DELETE" });
    await Promise.all([loadActiveStorms(), loadWatched()]);
  } catch (e) {
    alert(`取消关注失败：${e.message}`);
  }
}

async function loadWatched() {
  try {
    const data = await fetchJson("/api/watchlist");
    const tracks = data.tracks || [];
    $("watched-count").textContent = data.watchlist.length;
    if (tracks.length === 0) {
      $("watched-container").innerHTML =
        '<div class="empty">尚未关注任何台风，或关注后尚未抓取路径。</div>';
      return;
    }
    $("watched-container").innerHTML = tracks.map(renderStormBlock).join("");
  } catch (e) {
    $("watched-container").innerHTML =
      `<div class="empty">加载关注列表失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderStormBlock(t) {
  const history = t.track_history || [];
  const info = t.info || {};
  const last = history[history.length - 1] || {};
  const points = history.slice().reverse().map((p, i) => `
    <tr class="${p.forecast ? "forecast" : ""} ${i === 0 ? "latest" : ""}">
      <td>${escapeHtml(formatTime(p.date))}</td>
      <td class="mono">${p.lng.toFixed(1)}, ${p.lat.toFixed(1)}</td>
      <td>${p.wind}</td>
      <td>${p.pressure}</td>
      <td>${escapeHtml(p.basin)}</td>
      <td><span class="code-badge ${codeClass(p.code)}">${escapeHtml(p.code || "-")}</span></td>
      <td>${escapeHtml(p.description)}</td>
    </tr>
  `).join("");

  return `
    <div class="storm-block">
      <div class="header-row">
        <div>
          <div class="info">
            <h3>${escapeHtml(info.title || t.id)}</h3>
            <span class="info-extra">
              ${escapeHtml(info.type || "")} · ${escapeHtml(info.agencies || "")}
              · ${escapeHtml(info.season || "")}
              · ${history.length} 个路径点
            </span>
          </div>
          <div class="last-updated">
            最后更新：${escapeHtml(formatTime(t.last_updated))} ·
            最新点：${escapeHtml(formatTime(last.date))} ·
            首次抓取：${escapeHtml(formatTime(history[0]?.first_seen))}
          </div>
        </div>
        <div class="actions">
          <button type="button" class="danger" onclick="unwatch('${escapeHtml(t.id)}')">取消关注</button>
        </div>
      </div>
      <div class="scroll-wrap">
        <table>
          <thead>
            <tr>
              <th>时间 (BJT)</th>
              <th>经纬度</th>
              <th>风速 (kt)</th>
              <th>气压 (hPa)</th>
              <th>海盆</th>
              <th>等级</th>
              <th>描述</th>
            </tr>
          </thead>
          <tbody>${points}</tbody>
        </table>
      </div>
    </div>
  `;
}

$("refresh-btn").addEventListener("click", () => {
  loadActiveStorms();
  loadWatched();
});

loadActiveStorms();
loadWatched();
setInterval(loadActiveStorms, ACTIVE_REFRESH_MS);
setInterval(loadWatched, ACTIVE_REFRESH_MS);
