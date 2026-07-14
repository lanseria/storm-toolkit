// Storm Toolkit 前端逻辑：活跃列表 + 关注切换 + 实况/多源预测表
// 每 5 分钟自动刷新活跃列表；关注切换立即生效并乐观更新

const ACTIVE_REFRESH_MS = 5 * 60 * 1000;
const $ = (id) => document.getElementById(id);

const SOURCE_LABEL = {
  "jma": "日本气象厅",
  "cma": "中国气象局",
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
  const nameCNPrefix = info.name_cn ? `${escapeHtml(info.name_cn)} · ` : "";

  return `
    <div class="storm-block">
      <div class="header-row">
        <div>
          <div class="info">
            <h3>${nameCNPrefix}${escapeHtml(info.title || t.id)}</h3>
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
          <button type="button" onclick="generateSatellite('${escapeHtml(t.id)}', 'track', this)">生成卫星图</button>
          <button type="button" class="danger" onclick="unwatch('${escapeHtml(t.id)}')">取消关注</button>
        </div>
      </div>
      <div class="sub-section">
        <div class="sub-title">
          <span class="src-tag jtwc">美国联合台风警报中心</span>
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
          <button type="button" onclick="generateSatellite('${escapeHtml(h.id)}', 'history', this)">生成卫星图</button>
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
          <span class="src-tag jtwc">美国联合台风警报中心</span>
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

// ── 卫星图生成 ──────────────────────────────────────────────────────────
// 弹窗暂存上下文：点击「开始生成」时使用
let _satCtx = null;

function generateSatellite(stormId, source, btn) {
  // 打开配置弹窗，暂存触发上下文
  _satCtx = { stormId, source, btn, originalText: btn ? btn.textContent : "" };
  const title = $("sat-modal-title");
  title.textContent = `生成卫星图 · ${stormId}`;
  openSatModal();
}

function openSatModal() {
  // 每次打开重置为默认值（方形 1080）
  $("sat-width").value = 1080;
  $("sat-height").value = 1080;
  $("sat-font-scale").value = 1;
  $("sat-font-scale-val").textContent = "1.0×";
  $("sat-show-boundaries").checked = true;
  $("sat-show-cities").checked = true;
  syncPresetActive();
  $("sat-modal").hidden = false;
}

function closeSatModal() {
  $("sat-modal").hidden = true;
  _satCtx = null;
}

function syncPresetActive() {
  const w = $("sat-width").value;
  const h = $("sat-height").value;
  document.querySelectorAll(".preset-btns button").forEach((b) => {
    b.classList.toggle("active", String(b.dataset.w) === String(w) && String(b.dataset.h) === String(h));
  });
}

// 预设按钮：点击后填入宽高
document.querySelectorAll(".preset-btns button").forEach((b) => {
  b.addEventListener("click", () => {
    $("sat-width").value = b.dataset.w;
    $("sat-height").value = b.dataset.h;
    syncPresetActive();
  });
});

// 手动改宽高时，清除预设高亮（除非恰好匹配）
$("sat-width").addEventListener("input", syncPresetActive);
$("sat-height").addEventListener("input", syncPresetActive);

// 字号滑块实时显示
$("sat-font-scale").addEventListener("input", (e) => {
  $("sat-font-scale-val").textContent = `${parseFloat(e.target.value).toFixed(1)}×`;
});

$("sat-cancel").addEventListener("click", closeSatModal);
$("sat-modal").querySelector(".modal-backdrop").addEventListener("click", closeSatModal);

$("sat-confirm").addEventListener("click", async () => {
  const ctx = _satCtx;
  if (!ctx) return;
  const params = new URLSearchParams();
  params.set("source", ctx.source);
  params.set("width", parseInt($("sat-width").value, 10) || 1080);
  params.set("height", parseInt($("sat-height").value, 10) || 1080);
  params.set("show_boundaries", $("sat-show-boundaries").checked ? "true" : "false");
  params.set("show_cities", $("sat-show-cities").checked ? "true" : "false");
  params.set("city_font_scale", $("sat-font-scale").value);
  closeSatModal();

  const { btn, originalText, stormId } = ctx;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "启动中...";
  }
  try {
    const r = await fetchJson(
      `/api/satellite/${encodeURIComponent(stormId)}?${params}`,
      { method: "POST" },
    );
    if (r.cached) {
      // 归档命中缓存，直接下载
      downloadZip(stormId, r.size_sig);
      if (btn) {
        btn.textContent = "已下载缓存";
        setTimeout(() => { btn.disabled = false; btn.textContent = originalText; }, 2000);
      }
      return;
    }
    if (r.task_id) {
      pollSatelliteTask(r.task_id, stormId, btn, originalText);
      return;
    }
    throw new Error("服务器未返回任务 ID");
  } catch (e) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
    alert(`生成卫星图失败：${e.message}`);
  }
});

function pollSatelliteTask(taskId, stormId, btn, originalText) {
  const interval = setInterval(async () => {
    try {
      const t = await fetchJson(`/api/satellite/tasks/${taskId}`);
      if (t.status === "running") {
        if (btn) {
          btn.textContent = `生成中 ${t.current}/${t.total || "?"}`;
        }
        return;
      }
      clearInterval(interval);
      if (t.status === "done") {
        if (btn) {
          btn.textContent = "已完成，下载中...";
        }
        downloadZip(stormId, t.size_sig);
        if (btn) {
          setTimeout(() => { btn.disabled = false; btn.textContent = originalText; }, 2500);
        }
      } else {
        if (btn) {
          btn.disabled = false;
          btn.textContent = originalText;
        }
        alert(`生成卫星图失败：${t.error || "未知错误"}`);
      }
    } catch (e) {
      clearInterval(interval);
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
      alert(`查询进度失败：${e.message}`);
    }
  }, 1500);
}

function downloadZip(stormId, sizeSig) {
  const sig = sizeSig || "1080x1080";
  window.location.href = `/api/satellite/${encodeURIComponent(stormId)}.zip?size_sig=${sig}`;
}

$("refresh-btn").addEventListener("click", () => {
  loadActiveStorms();
  loadWatched();
  loadHistory();
});

$("clear-sat-cache-btn").addEventListener("click", async () => {
  if (!confirm("确认清除所有已生成的卫星图缓存（data/satellite/*.zip）？")) return;
  try {
    const r = await fetchJson("/api/satellite/cache", { method: "DELETE" });
    alert(`已清除 ${r.removed} 个卫星图 zip 缓存`);
  } catch (e) {
    alert(`清除失败：${e.message}`);
  }
});

$("purge-btn").addEventListener("click", async () => {
  const msg = "确认清空运行数据？\n\n"
    + "将删除：\n• storms_active.json（活跃列表缓存）\n• tracks/*.json（所有关注台风的实况与预测）\n\n"
    + "保留：\n• watchlist.json（关注列表，下次定时同步会重新抓取）\n• history/（已归档台风）";
  if (!confirm(msg)) return;
  try {
    const r = await fetchJson("/api/data", { method: "DELETE" });
    alert(`已清空：${r.tracks_removed} 个 tracks 文件${r.active_cleared ? " + 活跃列表缓存" : ""}`);
    await Promise.all([loadActiveStorms(), loadWatched(), loadHistory()]);
  } catch (e) {
    alert(`清空失败：${e.message}`);
  }
});

loadActiveStorms();
loadWatched();
loadHistory();
setInterval(loadActiveStorms, ACTIVE_REFRESH_MS);
setInterval(loadWatched, ACTIVE_REFRESH_MS);
setInterval(loadHistory, ACTIVE_REFRESH_MS);
