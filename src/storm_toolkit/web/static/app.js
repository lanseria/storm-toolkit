// Storm Toolkit 前端逻辑：活跃列表 + 关注切换 + 实况/多源预测表
// 每 5 分钟自动刷新活跃列表；关注切换立即生效并乐观更新

const ACTIVE_REFRESH_MS = 5 * 60 * 1000;
const $ = (id) => document.getElementById(id);

const SOURCE_LABEL = {
  "zoom-earth": "zoom.earth",
  "cma": "中国气象局",
  "jma": "日本气象厅",
  "jtwc": "美国联合台风警报中心",
  "cwa": "台湾中央气象署",
  "hko": "香港天文台",
  "kma": "韩国气象厅",
};

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

function srcTagClass(source) {
  if (source === "other" || source.startsWith("other-")) return "other";
  return source;
}

function srcLabel(source) {
  return SOURCE_LABEL[source] || source;
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
  $("storm-grid").innerHTML = storms.map((s) => {
    const tags = (s.sources || []).map((src) =>
      `<span class="src-tag ${srcTagClass(src)}">${escapeHtml(srcLabel(src))}</span>`
    ).join("");
    return `
    <div class="card ${s.kind === "disturbance" ? "disturbance" : ""}">
      <div class="title-row">
        <div>
          <h3>${escapeHtml(s.id)}</h3>
          <span class="id-tag">点击「详情」获取实时数据</span>
        </div>
      </div>
      <div class="tags">${tags}</div>
      <div class="actions">
        <button type="button" onclick="loadDetail('${escapeHtml(s.id)}')">详情</button>
        ${s.watched
          ? `<button type="button" class="danger" onclick="unwatch('${escapeHtml(s.id)}')">取消关注</button>`
          : `<button type="button" class="primary" onclick="watch('${escapeHtml(s.id)}')">关注</button>`}
      </div>
    </div>
  `}).join("");
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
      const totalForecastPts = (d.forecasts || []).reduce((n, b) => n + (b.points?.length || 0), 0);
      card.querySelector(".id-tag").textContent =
        `${escapeHtml(d.type || "")} · ${escapeHtml(d.agencies || "")} · `
        + `实况 ${d.track.length} 点 + 预测 ${totalForecastPts} 点`;
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

function renderTrackRows(points) {
  return points.slice().reverse().map((p, i) => `
    <tr class="${i === 0 ? "latest" : ""}">
      <td>${escapeHtml(formatTime(p.date))}</td>
      <td class="mono">${p.lng.toFixed(1)}, ${p.lat.toFixed(1)}</td>
      <td>${p.wind}</td>
      <td>${p.pressure}</td>
      <td><span class="code-badge ${codeClass(p.code)}">${escapeHtml(p.code || "-")}</span></td>
      <td>${escapeHtml(p.description)}</td>
    </tr>
  `).join("");
}

const TRACK_TABLE_HEAD = `
  <thead>
    <tr>
      <th>时间 (BJT)</th>
      <th>经纬度</th>
      <th>风速 (kt)</th>
      <th>气压 (hPa)</th>
      <th>等级</th>
      <th>描述</th>
    </tr>
  </thead>`;

function pickLatestBatchPerSource(forecasts) {
  const bySrc = new Map();
  for (const b of forecasts || []) {
    if (!b.points || b.points.length === 0) continue;
    const cur = bySrc.get(b.source);
    if (!cur || b.issued_at > cur.issued_at) bySrc.set(b.source, b);
  }
  return [...bySrc.values()].sort((a, b) => a.source.localeCompare(b.source));
}

function renderStormBlock(t) {
  const history = t.track_history || [];
  const info = t.info || {};
  const last = history[history.length - 1] || {};
  const latestBatches = pickLatestBatchPerSource(t.forecasts);

  const srcTags = latestBatches.map((b) =>
    `<span class="src-tag ${srcTagClass(b.source)}">${escapeHtml(srcLabel(b.source))}</span>`
  ).join(" ");

  const forecastSections = latestBatches.map((b) => `
    <div class="sub-section">
      <div class="sub-title">
        <span class="src-tag ${srcTagClass(b.source)}">${escapeHtml(srcLabel(b.source))}</span>
        <span>预测路径</span>
        <span class="issued-at">· 发布于 ${escapeHtml(formatTime(b.issued_at))} · ${b.points.length} 点</span>
      </div>
      <div class="scroll-wrap short">
        <table>
          ${TRACK_TABLE_HEAD}
          <tbody>${renderTrackRows(b.points)}</tbody>
        </table>
      </div>
    </div>
  `).join("");

  const cmaTag = info.cma_tfid
    ? `<span class="info-extra">CMA 编号: ${escapeHtml(info.cma_tfid)}</span>`
    : "";

  return `
    <div class="storm-block">
      <div class="header-row">
        <div>
          <div class="info">
            <h3>${escapeHtml(info.title || t.id)}</h3>
            <span class="info-extra">
              ${escapeHtml(info.type || "")} · ${escapeHtml(info.agencies || "")}
              · ${escapeHtml(info.season || "")}
              · 实况 ${history.length} 点
            </span>
            ${cmaTag}
            <span>${srcTags}</span>
          </div>
          <div class="last-updated">
            最后更新：${escapeHtml(formatTime(t.last_updated))} ·
            最新实况：${escapeHtml(formatTime(last.date))} ·
            首次抓取：${escapeHtml(formatTime(history[0]?.first_seen))}
          </div>
        </div>
        <div class="actions">
          <button type="button" class="danger" onclick="unwatch('${escapeHtml(t.id)}')">取消关注</button>
        </div>
      </div>
      <div class="sub-section">
        <div class="sub-title">
          <span class="src-tag zoom-earth">zoom.earth</span>
          <span>实况路径</span>
        </div>
        <div class="scroll-wrap">
          <table>
            ${TRACK_TABLE_HEAD}
            <tbody>${renderTrackRows(history)}</tbody>
          </table>
        </div>
      </div>
      ${forecastSections}
    </div>
  `;
}

// ── 历史归档 ────────────────────────────────────────────────────────────
const HISTORY_DETAIL_CACHE = new Map(); // id → track object（展开后缓存）

async function loadHistory() {
  try {
    const data = await fetchJson("/api/history");
    const items = data.history || [];
    $("history-count").textContent = items.length;
    if (items.length === 0) {
      $("history-container").innerHTML =
        '<div class="empty">暂无归档。台风消亡后将自动从关注列表移除并归档至此。</div>';
      return;
    }
    $("history-container").innerHTML = items
      .sort((a, b) => (b.archived_at || "").localeCompare(a.archived_at || ""))
      .map((h) => renderHistorySummary(h)).join("");
  } catch (e) {
    $("history-container").innerHTML =
      `<div class="empty">加载历史归档失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderHistorySummary(h) {
  const info = h.info || {};
  const title = info.title || h.id;
  return `
    <div class="storm-block">
      <div class="header-row">
        <div>
          <div class="info">
            <h3>${escapeHtml(title)}</h3>
            <span class="info-extra">
              ${escapeHtml(info.type || "")} · ${escapeHtml(info.season || "")}
              · 实况 ${h.track_count} 点
              · 归档于 ${escapeHtml(formatTime(h.archived_at))}
            </span>
          </div>
        </div>
        <div class="actions">
          <button type="button" onclick="toggleHistoryDetail('${escapeHtml(h.id)}', this)">展开路径</button>
        </div>
      </div>
      <div class="history-detail" id="history-detail-${escapeHtml(h.id)}" hidden></div>
    </div>
  `;
}

async function toggleHistoryDetail(stormId, btn) {
  const slot = document.getElementById(`history-detail-${stormId}`);
  if (!slot) return;
  if (!slot.hidden) {
    slot.hidden = true;
    btn.textContent = "展开路径";
    return;
  }
  btn.textContent = "加载中...";
  try {
    let h = HISTORY_DETAIL_CACHE.get(stormId);
    if (!h) {
      h = await fetchJson(`/api/history/${encodeURIComponent(stormId)}`);
      HISTORY_DETAIL_CACHE.set(stormId, h);
    }
    const rows = renderTrackRows(h.track_history || []);
    slot.innerHTML = `
      <div class="sub-section">
        <div class="sub-title">
          <span class="src-tag zoom-earth">zoom.earth</span>
          <span>实况路径（历史）</span>
        </div>
        <div class="scroll-wrap short">
          <table>
            ${TRACK_TABLE_HEAD}
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;
    slot.hidden = false;
    btn.textContent = "收起路径";
  } catch (e) {
    btn.textContent = "展开路径";
    slot.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
    slot.hidden = false;
  }
}

$("refresh-btn").addEventListener("click", () => {
  loadActiveStorms();
  loadWatched();
  loadHistory();
});

loadActiveStorms();
loadWatched();
loadHistory();
setInterval(loadActiveStorms, ACTIVE_REFRESH_MS);
setInterval(loadWatched, ACTIVE_REFRESH_MS);
setInterval(loadHistory, ACTIVE_REFRESH_MS);
