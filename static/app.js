"use strict";
// Claude Code 作業履歴ビューア(フロントエンド)。外部ライブラリは使わない。

// ---------- 定数 ----------
const STATUS = {
  running: { label: "作業中", order: 0 },
  waiting: { label: "入力待ち", order: 1 },
  interrupted: { label: "中断", order: 2 },
  error: { label: "エラー", order: 3 },
  ended: { label: "終了", order: 4 },
  unknown: { label: "不明(Mac未接続)", order: 5 },
};
const LAST_KIND = {
  prompt: "ユーザーの発言を受信(応答待ち)",
  tool_result: "ツールの実行結果を受信",
  assistant_tool_use: "ツールを実行中",
  assistant_end: "応答を完了",
  interrupt: "ユーザーが中断",
  api_error: "APIエラーで停止",
};
const WORK_TYPES = ["実装", "調査", "実行・検証", "会話"];
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
const COLOR_BY = {
  workType: "作業種別",
  project: "プロジェクト",
  status: "状態",
  branch: "ブランチ",
  session: "セッション",
};
const ISSUE_KIND = { tool_error: "ツールエラー", interrupt: "中断", api_error: "APIエラー" };
const DAY_MS = 86400000;
// カレンダーの帯の最小の高さ(px)。短い作業でも題名と所要時間が読めるようにする
const MIN_BAND_PX = 20;
// 短い作業区間は、近くにあるもの同士を 1 本の帯にまとめる(基準は state.shortSegMin / mergeGapMin、画面から変更可)
const HOUR_PX_MIN = 16, HOUR_PX_MAX = 160;
// 保存した表示設定の形式。上げると古い拡大率を捨てて新しい既定値を使う
const PREF_VERSION = 2;
const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

// ---------- 状態 ----------
const state = {
  sessions: new Map(),
  view: "dashboard",
  sessionMode: "calendar",
  colorBy: "workType",
  statusFilter: new Set(),
  issueFilter: new Set(),
  taskScope: "all",
  sort: "recent",
  project: "",
  reportPeriod: "day",
  reportDate: null,
  reportPrompts: true,
  reportFiles: true,
  notify: false,
  search: "",
  weekStart: startOfWeek(new Date()),
  mobileDay: null,
  hourPx: 60,
  mergeShort: true,
  showMemory: false,
  shortSegMin: 2,   // この分数未満の作業を「極短」とする
  mergeGapMin: 3,   // 極短の作業をまとめる間隔(分)
  projectRules: {}, // プロジェクトごとの「極短を隠す」設定: { path: "hide" | "show" }(未設定は自動判定)
  panel: null,
  groupDay: null,
  selected: null,
  monthStart: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  groupTitle: "",
  groupIds: null, // まとめ帯から開いた一覧の session id
  logOpen: false,
  serverOffset: 0,
  recentlyUpdated: new Set(),
  colorSlots: {},
  meta: {},
};

// 表示設定はブラウザに保存(失敗しても動くようにする)
const PREF_KEYS = ["view", "sessionMode", "colorBy", "hourPx", "mergeShort", "showMemory", "shortSegMin", "mergeGapMin", "projectRules", "taskScope", "sort", "project", "reportPeriod", "reportPrompts", "reportFiles", "notify"];
try {
  const saved = JSON.parse(localStorage.getItem("cchv-prefs") || "{}");
  if (saved.v !== PREF_VERSION) delete saved.hourPx;
  for (const k of PREF_KEYS) if (saved[k] !== undefined) state[k] = saved[k];
} catch (_) { /* 保存領域が使えなくても既定値で動く */ }
function savePrefs() {
  try {
    localStorage.setItem("cchv-prefs", JSON.stringify({ v: PREF_VERSION, ...Object.fromEntries(PREF_KEYS.map((k) => [k, state[k]])) }));
  } catch (_) { /* 無視 */ }
}

// ---------- 小道具 ----------
function $(sel, root = document) { return root.querySelector(sel); }
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function now() { return Date.now() / 1000 + state.serverOffset; }
function startOfWeek(d) {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const wd = (x.getDay() + 6) % 7; // 月曜始まり
  x.setDate(x.getDate() - wd);
  return x;
}
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function pad(n) { return String(n).padStart(2, "0"); }
function fmtTime(ts) { if (!ts) return "-"; const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
function fmtDate(ts) { if (!ts) return "-"; const d = new Date(ts * 1000); return `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAYS[d.getDay()]})`; }
function fmtDateTime(ts) { return ts ? `${fmtDate(ts)} ${fmtTime(ts)}` : "-"; }
function fmtDur(sec) {
  sec = Math.round(sec || 0);
  if (sec < 60) return `${sec}秒`;
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}分`;
  return `${Math.floor(m / 60)}時間${m % 60 ? (m % 60) + "分" : ""}`;
}
function fmtAgo(ts) {
  if (!ts) return "-";
  const s = now() - ts;
  if (s < 60) return "たった今";
  if (s < 3600) return `${Math.floor(s / 60)}分前`;
  if (s < 86400) return `${Math.floor(s / 3600)}時間前`;
  return `${Math.floor(s / 86400)}日前`;
}
function fmtTok(n) {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function sum(arr, f = (x) => x) { return arr.reduce((a, b) => a + f(b), 0); }
function statusBadge(s) { return `<span class="status ${s.status}">${STATUS[s.status].label}${s.statusSource === "transcript" && s.status === "running" ? "(推定)" : ""}</span>`; }
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// ---------- 色分け ----------
// 色は「対象の実体」に固定する(絞り込みで色が入れ替わらないよう、開始順で割り当てて保持する)
function colorKey(s, by = state.colorBy) {
  switch (by) {
    case "workType": return s.workType;
    case "project": return s.projectName || "(不明)";
    case "status": return s.status;
    case "branch": return s.branch || "(なし)";
    default: return s.id;
  }
}
function computeColorSlots() {
  const slots = {};
  const byStart = [...state.sessions.values()].sort((a, b) => (a.start || 0) - (b.start || 0));
  for (const by of ["project", "branch", "session"]) {
    const map = new Map();
    for (const s of byStart) {
      const k = colorKey(s, by);
      if (!map.has(k)) map.set(k, map.size < SERIES.length ? `var(${SERIES[map.size]})` : "var(--other)");
    }
    slots[by] = map;
  }
  slots.workType = new Map(WORK_TYPES.map((w, i) => [w, `var(${SERIES[i]})`]));
  slots.status = new Map([
    ["running", "var(--good)"], ["waiting", "var(--warning)"], ["interrupted", "var(--serious)"],
    ["error", "var(--critical)"], ["ended", "var(--other)"], ["unknown", "var(--other)"],
  ]);
  state.colorSlots = slots;
}
function colorOf(s, by = state.colorBy) {
  return state.colorSlots[by]?.get(colorKey(s, by)) || "var(--other)";
}
function legendHtml(list) {
  const by = state.colorBy;
  const counts = new Map();
  for (const s of list) counts.set(colorKey(s), (counts.get(colorKey(s)) || 0) + 1);
  let keys = [...state.colorSlots[by].keys()].filter((k) => counts.has(k));
  if (by === "session" || keys.length > 12) {
    const shown = keys.filter((k) => state.colorSlots[by].get(k) !== "var(--other)").slice(0, 8);
    const rest = sum(keys.filter((k) => !shown.includes(k)), (k) => counts.get(k));
    keys = shown;
    const items = keys.map((k) => {
      const s = list.find((x) => colorKey(x) === k);
      const label = by === "session" ? (s?.title || k).slice(0, 18) : k;
      return `<span><i class="swatch" style="--c:${state.colorSlots[by].get(k)}"></i>${esc(label)}</span>`;
    });
    if (rest) items.push(`<span><i class="swatch" style="--c:var(--other)"></i>その他 ${rest}</span>`);
    return `<div class="legend">${items.join("")}</div>`;
  }
  return `<div class="legend">${keys.map((k) => {
    const label = by === "status" ? STATUS[k].label : k;
    return `<span><i class="swatch" style="--c:${state.colorSlots[by].get(k)}"></i>${esc(label)} <span class="num muted">${counts.get(k)}</span></span>`;
  }).join("")}${by === "workType" ? `<span class="muted">※作業種別はツール使用回数からの推定</span>` : ""}</div>`;
}

// ---------- 絞り込み ----------
function matchesSearch(s) {
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  const hay = [s.title, s.firstPrompt, s.project, s.branch, s.id, ...(s.prompts || []).map((p) => p.text)].join("\n").toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w));
}
function matchesProject(s) {
  return !state.project || s.project === state.project;
}
// 極短セッションを隠す対象のプロジェクトを決める。
// 自動判定: フォルダ名が memory / 3 件以上あって全部が極短(動作確認用の一時フォルダなど)。手動の設定が優先。
function shortSec() { return state.shortSegMin * 60; }
function projectStats() {
  const per = new Map();
  for (const s of state.sessions.values()) {
    const e = per.get(s.project) || { project: s.project, name: s.projectName, n: 0, short: 0 };
    e.n++;
    if (s.end - s.start < shortSec()) e.short++;
    per.set(s.project, e);
  }
  for (const e of per.values()) {
    const rule = state.projectRules[e.project];
    if (rule === "hide") { e.hide = true; e.reason = "手動で隠す"; }
    else if (rule === "show") { e.hide = false; e.reason = "手動で表示"; }
    else if (e.name.toLowerCase() === "memory") { e.hide = true; e.reason = "memory フォルダ(自動)"; }
    else if (e.n >= 3 && e.short === e.n) { e.hide = true; e.reason = "全セッションが極短(自動)"; }
    else { e.hide = false; e.reason = ""; }
  }
  return per;
}
function noiseProjects() {
  return new Set([...projectStats().values()].filter((e) => e.hide).map((e) => e.project));
}
function isNoise(s, np) { return np.has(s.project) && s.end - s.start < shortSec(); }
function visibleSessions({ useStatus = true } = {}) {
  const np = state.showMemory ? new Set() : noiseProjects();
  return [...state.sessions.values()].filter(
    (s) => !isNoise(s, np) && matchesProject(s) && matchesSearch(s) && (!useStatus || !state.statusFilter.size || state.statusFilter.has(s.status))
  );
}
// 上部のプロジェクト選択。作業時間の長い順に並べ、同じフォルダ名が複数あるときはパスも出す
function renderProjectSelect() {
  const mem = $("#showMemory");
  if (mem) {
    mem.checked = state.showMemory;
    const np = noiseProjects();
    const n = [...state.sessions.values()].filter((s) => isNoise(s, np)).length;
    $("#memoryCount").textContent = n ? `(${n})` : "";
  }
  const sel = $("#project");
  if (!sel || document.activeElement === sel) return;
  const stats = new Map();
  for (const s of state.sessions.values()) {
    const e = stats.get(s.project) || { name: s.projectName, n: 0, sec: 0 };
    e.n++; e.sec += s.activeSec;
    stats.set(s.project, e);
  }
  const nameCount = {};
  for (const e of stats.values()) nameCount[e.name] = (nameCount[e.name] || 0) + 1;
  const items = [...stats.entries()].sort((a, b) => b[1].sec - a[1].sec);
  if (state.project && !stats.has(state.project)) items.push([state.project, { name: state.project, n: 0, sec: 0 }]);
  const html = `<option value="">すべてのプロジェクト(${state.sessions.size})</option>` + items.map(([path, e]) =>
    `<option value="${esc(path)}" ${path === state.project ? "selected" : ""}>${esc(nameCount[e.name] > 1 ? path : e.name)}(${e.n})</option>`).join("");
  if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
  sel.value = state.project;
  sel.classList.toggle("active", !!state.project);
}
function effectiveEnd(s) {
  // 作業中のセッションは現在時刻まで伸ばして表示する
  return s.status === "running" ? Math.max(s.end || 0, now()) : s.end;
}

// ---------- データ取得・リアルタイム更新 ----------
async function loadState() {
  const res = await fetch("/api/state");
  if (res.status === 401) { location.href = "/login"; return; }
  const data = await res.json();
  state.serverOffset = data.serverTime - Date.now() / 1000;
  applyMeta(data);
  state.sessions = new Map(data.sessions.map((s) => [s.id, s]));
  computeColorSlots();
  render();
}

function connectEvents() {
  const live = $("#live");
  const es = new EventSource("/api/events");
  const setLive = (on, text) => {
    live.className = "live " + (on ? "on" : "off");
    live.querySelector("span").textContent = text;
    live.title = `リアルタイム更新: ${text}`;
  };
  es.addEventListener("hello", () => setLive(true, "リアルタイム更新中"));
  es.addEventListener("meta", (ev) => applyMeta(JSON.parse(ev.data)));
  es.addEventListener("update", (ev) => {
    const data = JSON.parse(ev.data);
    state.serverOffset = data.serverTime - Date.now() / 1000;
    applyMeta(data);
    for (const s of data.sessions) {
      const prev = state.sessions.get(s.id);
      state.sessions.set(s.id, s);
      state.recentlyUpdated.add(s.id);
      if (prev) maybeNotify(prev, s);
    }
    computeColorSlots();
    setLive(true, `更新 ${fmtTime(data.serverTime)}`);
    render({ keepScroll: true });
    if (state.logOpen && data.sessions.some((s) => s.id === state.selected)) loadLog(state.selected);
    setTimeout(() => { for (const s of data.sessions) state.recentlyUpdated.delete(s.id); }, 1500);
  });
  es.onerror = () => {
    setLive(false, "再接続中…");
    // ログインの有効期限切れなどで拒否されている場合はログイン画面へ
    fetch("/api/state").then((r) => { if (r.status === 401) location.href = "/login"; }).catch(() => {});
  };
}

// hub モードでは、Mac から最後に届いた時刻と接続状態を右上に出す
function applyMeta(m) {
  if (!m || !m.mode) return;
  state.meta = { ...state.meta, ...m };
  renderSync();
}
function renderSync() {
  const m = state.meta;
  const el = $("#sync");
  $("#logout").classList.toggle("hidden", !m.auth);
  if (m.mode !== "hub") { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.className = "sync " + (m.online ? "on" : "off");
  const who = m.clientName ? `${m.clientName}: ` : "";
  el.textContent = m.lastPush ? `${who}${m.online ? "Mac接続中" : "Mac未接続"}・最終同期 ${fmtAgo(m.lastPush)}` : "Macからの同期はまだありません";
  el.title = m.lastPush ? `最終同期 ${fmtDateTime(m.lastPush)}` : "";
}

// ---------- 描画 ----------
function render({ keepScroll = false } = {}) {
  const main = $("#main");
  const scroll = main.scrollTop;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  const views = { dashboard: renderDashboard, sessions: renderSessions, tasks: renderTasks, issues: renderIssues, changes: renderChanges, report: renderReport };
  renderProjectSelect();
  main.innerHTML = views[state.view]();
  // 開いたとき・画面や週を切り替えたときはページの先頭から表示する。自動更新では位置を保つ
  main.scrollTop = keepScroll ? scroll : 0;
  renderDetail();
}

function statusChips(list) {
  const counts = {};
  for (const s of list) counts[s.status] = (counts[s.status] || 0) + 1;
  return Object.keys(STATUS).filter((k) => k !== "unknown" || counts[k]).map((k) =>
    `<button class="chip ${state.statusFilter.has(k) ? "active" : ""}" data-status="${k}"><span class="status ${k}">${STATUS[k].label}</span><span class="n">${counts[k] || 0}</span></button>`
  ).join("");
}

// ----- ダッシュボード -----
function renderDashboard() {
  const all = visibleSessions({ useStatus: false });
  if (!all.length) return emptyState();
  const t = now();
  const weekAgo = t - 7 * 86400;
  const running = all.filter((s) => s.status === "running");
  const waiting = all.filter((s) => s.status === "waiting");
  const weekActive = sum(all, (s) => sum(s.segments, ([a, b]) => (b > weekAgo ? Math.max(60, b - Math.max(a, weekAgo)) : 0)));
  const outTok = sum(all, (s) => s.tokens.output);
  const issues7 = sum(all, (s) => s.issues.filter((i) => i.ts > weekAgo).length);
  const commits7 = sum(all, (s) => s.commits.filter((c) => c.ts > weekAgo).length);

  const kpi = (label, value, sub = "") => `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  const activeList = [...running, ...waiting].sort((a, b) => (b.end || 0) - (a.end || 0));
  const recentIssues = all.flatMap((s) => s.issues.map((i) => ({ ...i, s }))).sort((a, b) => b.ts - a.ts).slice(0, 8);
  const recentSessions = [...all].sort((a, b) => (b.end || 0) - (a.end || 0)).slice(0, 8);

  return `
    <div class="kpis">
      ${kpi("セッション", all.length, `全期間`)}
      ${kpi("作業中", running.length, `入力待ち ${waiting.length}`)}
      ${kpi("直近7日の作業時間", fmtDur(weekActive), "記録の間隔10分以内を連続とみなす")}
      ${kpi("直近7日のコミット", commits7, "Bashでのgit commit")}
      ${kpi("直近7日の課題", issues7, "ツールエラー・中断・APIエラー")}
      ${kpi("出力トークン", fmtTok(outTok), `入力 ${fmtTok(sum(all, (s) => s.tokens.input + s.tokens.cacheRead + s.tokens.cacheCreate))}`)}
    </div>
    <div class="cards">
      <div class="card"><h3>日別の作業時間 <span class="muted">直近14日・作業種別</span></h3>${dailyChart(all)}</div>
      <div class="card"><h3>稼働中のセッション <span class="muted">${activeList.length}件</span></h3>
        ${activeList.length ? activeList.map(sessionRow).join("") : `<div class="empty">稼働中のセッションはありません</div>`}
      </div>
      <div class="card"><h3>ツール使用回数 <span class="muted">上位10</span></h3>${toolBars(all)}</div>
      <div class="card"><h3>プロジェクト別の作業時間</h3>${projectBars(all)}</div>
      <div class="card"><h3>最近のセッション</h3>${recentSessions.map(sessionRow).join("")}</div>
      <div class="card"><h3>最近の課題</h3>
        ${recentIssues.length ? recentIssues.map((i) => `<div class="rowlink" data-open="${i.s.id}">
          <span class="tag">${ISSUE_KIND[i.kind] || i.kind}</span><span class="grow">${esc(i.label)}: ${esc(i.text)}</span><span class="muted">${fmtAgo(i.ts)}</span></div>`).join("")
        : `<div class="empty">課題はありません</div>`}
      </div>
    </div>`;
}
function sessionRow(s) {
  return `<div class="rowlink" data-open="${s.id}">
    <i class="swatch" style="--c:${colorOf(s, "workType")}"></i>
    <span class="grow">${esc(s.title)}</span>${statusBadge(s)}<span class="muted num">${fmtAgo(s.end)}</span></div>`;
}

function dailyChart(list) {
  const days = 14;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const startDay = addDays(today, -(days - 1));
  const data = Array.from({ length: days }, (_, i) => ({ day: addDays(startDay, i), v: Object.fromEntries(WORK_TYPES.map((w) => [w, 0])) }));
  for (const s of list) {
    for (const [a, b0] of s.segments) {
      const b = Math.max(b0, a + 60);
      for (let i = 0; i < days; i++) {
        const d0 = data[i].day.getTime() / 1000, d1 = d0 + 86400;
        const ov = Math.min(b, d1) - Math.max(a, d0);
        if (ov > 0) data[i].v[s.workType] += ov / 3600;
      }
    }
  }
  const max = Math.max(0.5, ...data.map((d) => sum(WORK_TYPES, (w) => d.v[w])));
  const W = 560, H = 180, L = 30, B = 20, bw = (W - L) / days;
  const y = (h) => (H - B) - (h / max) * (H - B - 6);
  const ticks = niceTicks(max);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="日別の作業時間">`;
  for (const tv of ticks) svg += `<line x1="${L}" x2="${W}" y1="${y(tv)}" y2="${y(tv)}" stroke="var(--grid)"/><text x="${L - 4}" y="${y(tv) + 4}" text-anchor="end">${tv}h</text>`;
  data.forEach((d, i) => {
    const x = L + i * bw + 3, w = bw - 6;
    let acc = 0;
    const parts = WORK_TYPES.filter((wt) => d.v[wt] > 0);
    parts.forEach((wt, j) => {
      const h0 = acc, h1 = acc + d.v[wt];
      acc = h1;
      const top = y(h1), bot = y(h0) - (j > 0 ? 2 : 0); // 積み上げの間に 2px の隙間
      const hgt = Math.max(1, bot - top);
      const r = j === parts.length - 1 ? 3 : 0;
      svg += `<path d="${roundedTop(x, top, w, hgt, r)}" fill="${state.colorSlots.workType.get(wt)}"/>`;
    });
    const total = sum(WORK_TYPES, (wt) => d.v[wt]);
    const tip = `${d.day.getMonth() + 1}/${d.day.getDate()}(${WEEKDAYS[d.day.getDay()]}) 合計 ${fmtDur(total * 3600)}<br>` +
      WORK_TYPES.filter((wt) => d.v[wt] > 0).map((wt) => `<i class="swatch" style="--c:${state.colorSlots.workType.get(wt)}"></i> ${wt} ${fmtDur(d.v[wt] * 3600)}`).join("<br>");
    svg += `<rect x="${L + i * bw}" y="0" width="${bw}" height="${H - B}" fill="transparent" data-tip="${esc(tip)}"/>`;
    if (i % 2 === (days - 1) % 2) svg += `<text x="${x + w / 2}" y="${H - 5}" text-anchor="middle">${d.day.getMonth() + 1}/${d.day.getDate()}</text>`;
  });
  svg += `<line x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}" stroke="var(--axis)"/></svg>`;
  const legend = `<div class="legend" style="margin-top:6px">${WORK_TYPES.map((w) => `<span><i class="swatch" style="--c:${state.colorSlots.workType.get(w)}"></i>${w}</span>`).join("")}</div>`;
  return `<div class="chart">${svg}</div>${legend}`;
}
function roundedTop(x, y, w, h, r) {
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function niceTicks(max) {
  const steps = [0.5, 1, 2, 3, 4, 6, 8, 12];
  const step = steps.find((s) => max / s <= 4) || Math.ceil(max / 4);
  const out = [];
  for (let v = 0; v <= max + 1e-9; v += step) out.push(+v.toFixed(2));
  return out;
}
function hbars(rows, fmt = (v) => v) {
  if (!rows.length) return `<div class="empty">データがありません</div>`;
  const max = Math.max(...rows.map((r) => r[1]));
  return rows.map(([name, v]) => `<div class="hbar" data-tip="${esc(name)}: ${esc(fmt(v))}"><span class="name">${esc(name)}</span>
    <span><div class="bar" style="width:${(v / max) * 100}%"></div></span><span class="v">${esc(fmt(v))}</span></div>`).join("");
}
function toolBars(list) {
  const c = {};
  for (const s of list) for (const [k, v] of Object.entries(s.tools)) c[k] = (c[k] || 0) + v;
  return hbars(Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 10));
}
function projectBars(list) {
  const c = {};
  for (const s of list) c[s.projectName] = (c[s.projectName] || 0) + s.activeSec;
  return hbars(Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 8), fmtDur);
}
function emptyState() {
  return `<div class="empty">表示できるセッションがありません。Claude Code の履歴(projects/*.jsonl)が見つからないか、検索条件に一致しません。</div>`;
}

// ----- セッション -----
function renderSessions() {
  const base = visibleSessions({ useStatus: false });
  const list = visibleSessions();
  const toolbar = `
    <div class="toolbar">
      <div class="seg">
        <button data-mode="calendar" class="${state.sessionMode === "calendar" ? "active" : ""}">時間帯</button>
        <button data-mode="month" class="${state.sessionMode === "month" ? "active" : ""}">カレンダー</button>
        <button data-mode="list" class="${state.sessionMode === "list" ? "active" : ""}">一覧</button>
      </div>
      <span class="sep"></span>${statusChips(base)}
      <span class="sep"></span>
      <label>色分け <select id="colorBy">${Object.entries(COLOR_BY).map(([k, v]) => `<option value="${k}" ${k === state.colorBy ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      ${state.sessionMode === "list" ? `<label>並び順 <select id="sort">
        <option value="recent" ${state.sort === "recent" ? "selected" : ""}>最終活動が新しい順</option>
        <option value="start" ${state.sort === "start" ? "selected" : ""}>開始が新しい順</option>
        <option value="active" ${state.sort === "active" ? "selected" : ""}>作業時間が長い順</option>
        <option value="status" ${state.sort === "status" ? "selected" : ""}>状態順</option></select></label>` : ""}
    </div>`;
  if (state.sessionMode === "list") return toolbar + legendHtml(list) + sessionTable(list);
  if (state.sessionMode === "month") return toolbar + monthHtml(list);
  return toolbar + calendarHtml(list);
}

// 月表示のカレンダー: 日ごとに、プロジェクト別の件数と作業時間を出す
function monthHtml(list) {
  const first = state.monthStart;
  const gridStart = startOfWeek(first);
  const next = new Date(first.getFullYear(), first.getMonth() + 1, 1);
  const weeks = Math.ceil(((next - gridStart) / 86400000) / 7);
  const todayKey = new Date().toDateString();
  const days = [];
  for (let i = 0; i < weeks * 7; i++) {
    const d = addDays(gridStart, i);
    const t0 = d.getTime() / 1000, t1 = addDays(d, 1).getTime() / 1000;
    const byProject = new Map();
    for (const s of list) {
      const sec = overlapSec(s.segments, t0, t1);
      if (!sec) continue;
      const e = byProject.get(s.project) || { project: s.project, name: s.projectName, sec: 0, ids: [] };
      e.sec += sec; e.ids.push(s.id);
      byProject.set(s.project, e);
    }
    const rows = [...byProject.values()].sort((a, b) => b.sec - a.sec);
    days.push({ d, t0, t1, rows, inMonth: d.getMonth() === first.getMonth() });
  }
  // プロジェクトの色は、その月の作業時間が長い順に割り当てる(グラフとマスで共通)
  const secByProject = new Map();
  for (const day of days) if (day.inMonth) for (const r of day.rows) secByProject.set(r.project, (secByProject.get(r.project) || 0) + r.sec);
  const ranked = [...secByProject.entries()].sort((a, b) => b[1] - a[1]);
  const colorMap = new Map(ranked.map(([p], i) => [p, i < SERIES.length - 1 ? `var(${SERIES[i]})` : "var(--other)"]));
  const nameOf = new Map(days.flatMap((day) => day.rows.map((r) => [r.project, r.name])));

  let html = `<div class="toolbar">
    <button class="btn" data-month="today">今月</button>
    <button class="btn" data-month="-1" aria-label="前の月">◀</button>
    <button class="btn" data-month="1" aria-label="次の月">▶</button>
    <strong class="num">${first.getFullYear()}年${first.getMonth() + 1}月</strong>
    <span class="muted note-long">日をクリック = その日の一覧 / プロジェクト行をクリック = そのプロジェクトだけ / ダブルクリック = その週の時間帯表示</span>
  </div>${monthChart(days, colorMap, nameOf, ranked)}<div class="month">`;
  for (const w of ["月", "火", "水", "木", "金", "土", "日"]) html += `<div class="month-head">${w}</div>`;
  for (const day of days) {
    const { d, t0, rows } = day;
    const ids = rows.flatMap((r) => r.ids);
    const total = sum(rows, (r) => r.sec);
    const attr = (ids2, title) => `data-group="${ids2.join(",")}" data-grouptitle="${esc(title)}" data-groupday="${t0}"`;
    html += `<div class="month-cell ${day.inMonth ? "" : "other"} ${d.toDateString() === todayKey ? "today" : ""} ${ids.length ? "has" : ""}" data-day="${t0}" ${ids.length ? attr(ids, `${fmtYMD(d)} のセッション`) : ""}>
      <div class="mc-top"><span class="mc-date">${d.getDate()}</span>${ids.length ? `<span class="mc-total">${ids.length}件 ${fmtDur(total)}</span>` : ""}</div>
      ${rows.slice(0, 4).map((r) => `<div class="mc-row" ${attr(r.ids, `${fmtYMD(d)} ・ ${r.name}`)} style="--c:${colorMap.get(r.project) || "var(--other)"}"><i class="swatch"></i><span class="clamp">${esc(r.name)}</span><span class="n">${r.ids.length}件・${fmtDur(r.sec)}</span></div>`).join("")}
      ${rows.length > 4 ? `<div class="mc-more muted">ほか ${rows.length - 4} プロジェクト</div>` : ""}
    </div>`;
  }
  return html + `</div>`;
}
// 日別・プロジェクト別の積み上げ棒グラフ(その月の日だけ)
function monthChart(days, colorMap, nameOf, ranked) {
  const mdays = days.filter((x) => x.inMonth);
  const top = ranked.slice(0, SERIES.length - 1).map(([p]) => p);
  const seg = (day) => {
    const m = new Map();
    for (const r of day.rows) { const k = top.includes(r.project) ? r.project : "__other"; m.set(k, (m.get(k) || 0) + r.sec); }
    return m;
  };
  const totals = mdays.map((day) => sum(day.rows, (r) => r.sec));
  const max = Math.max(1, ...totals);
  if (!ranked.length) return "";
  const W = 720, H = 110, B = 18, bw = W / mdays.length;
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="mchart" preserveAspectRatio="none" role="img" aria-label="日別の作業時間">`;
  mdays.forEach((day, i) => {
    let y = H - B;
    for (const k of [...top, "__other"]) {
      const v = seg(day).get(k);
      if (!v) continue;
      const h = (v / max) * (H - B - 4);
      y -= h;
      const name = k === "__other" ? "その他" : nameOf.get(k);
      svg += `<rect x="${i * bw + 1}" y="${y}" width="${Math.max(1, bw - 2)}" height="${h}" fill="${k === "__other" ? "var(--other)" : colorMap.get(k)}" data-tip="${esc(`${fmtMD(day.d)} ${name}: ${fmtDur(v)}`)}"/>`;
    }
    if (i % Math.ceil(mdays.length / 16) === 0 || mdays.length <= 16) svg += `<text x="${i * bw + bw / 2}" y="${H - 4}" text-anchor="middle" class="axis">${day.d.getDate()}</text>`;
  });
  svg += `</svg>`;
  const legend = ranked.slice(0, SERIES.length - 1).map(([p, sec]) => `<span><i class="swatch" style="--c:${colorMap.get(p)}"></i>${esc(nameOf.get(p))} <span class="num muted">${fmtDur(sec)}</span></span>`).join("");
  return `<div class="box mchart-box"><div class="legend">${legend}</div>${svg}</div>`;
}

function sessionTable(list) {
  const sorters = {
    recent: (a, b) => (b.end || 0) - (a.end || 0),
    start: (a, b) => (b.start || 0) - (a.start || 0),
    active: (a, b) => b.activeSec - a.activeSec,
    status: (a, b) => STATUS[a.status].order - STATUS[b.status].order || (b.end || 0) - (a.end || 0),
  };
  const rows = [...list].sort(sorters[state.sort] || sorters.recent);
  if (!rows.length) return emptyState();
  return `<table class="list sessions"><thead><tr>
    <th>状態</th><th>セッション</th><th>種別</th><th>プロジェクト</th><th>開始</th><th class="r">作業時間</th>
    <th class="r">発言</th><th class="r">ツール</th><th>タスク</th><th class="r">課題</th><th class="r">出力tok</th><th>最終活動</th></tr></thead><tbody>
    ${rows.map((s) => {
      const done = s.tasks.filter((t) => t.status === "completed").length;
      return `<tr data-open="${s.id}" class="${state.selected === s.id ? "selected" : ""}">
        <td>${statusBadge(s)}</td>
        <td><div class="title-cell"><i class="swatch" style="--c:${colorOf(s)}"></i><div><div class="clamp">${esc(s.title)}</div><div class="muted">${esc(s.branch)}</div></div></div></td>
        <td><span class="tag">${esc(s.workType)}</span></td>
        <td>${esc(s.projectName)}</td>
        <td class="num">${fmtDateTime(s.start)}</td>
        <td class="r">${fmtDur(s.activeSec)}</td>
        <td class="r">${s.promptCount}</td>
        <td class="r">${sum(Object.values(s.tools))}</td>
        <td>${s.tasks.length ? `<span class="progress"><i style="width:${(done / s.tasks.length) * 100}%"></i></span> <span class="num muted">${done}/${s.tasks.length}</span>` : `<span class="muted">-</span>`}</td>
        <td class="r">${s.issues.length || ""}</td>
        <td class="r">${fmtTok(s.tokens.output)}</td>
        <td class="muted">${fmtAgo(s.end)}</td></tr>`;
    }).join("")}</tbody></table>`;
}

// 狭い画面(スマホ)では、週カレンダーを 1 日だけ表示する
const MOBILE_MQ = window.matchMedia("(max-width: 700px)");
function isMobile() { return MOBILE_MQ.matches; }
function mobileDayIndex(ws) {
  const diff = Math.round((new Date().setHours(0, 0, 0, 0) - ws.getTime()) / 86400000);
  const todayIn = diff >= 0 && diff < 7 ? diff : 0;
  const v = state.mobileDay;
  return v === null || v === undefined || v < 0 || v > 6 ? todayIn : v;
}
function calendarHtml(list) {
  const ws = state.weekStart;
  const we = addDays(ws, 7);
  const wsT = ws.getTime() / 1000, weT = we.getTime() / 1000;
  const inWeek = list.filter((s) => s.segments.some(([a]) => a < weT) && effectiveEnd(s) >= wsT);
  const hp = state.hourPx;
  const H = hp * 24;
  const todayKey = new Date().toDateString();

  const mobile = isMobile();
  const di = mobile ? mobileDayIndex(ws) : -1;
  const dayIdxs = mobile ? [di] : [0, 1, 2, 3, 4, 5, 6];
  let html = `<div class="toolbar">
    <button class="btn" data-week="today">今週</button>
    <button class="btn" data-week="-1" aria-label="前の週">◀</button>
    <button class="btn" data-week="1" aria-label="次の週">▶</button>
    <strong class="num">${ws.getFullYear()}/${ws.getMonth() + 1}/${ws.getDate()} 〜 ${addDays(ws, 6).getMonth() + 1}/${addDays(ws, 6).getDate()}</strong>
    <span class="muted">${inWeek.length} セッション</span>
    <span class="sep"></span>
    <label class="muted"><input type="checkbox" id="mergeShort" ${state.mergeShort ? "checked" : ""}> 極短の作業をまとめる</label>
    ${state.mergeShort ? `<label class="muted">極短の基準 <select id="shortSegMin">${[1,2,5,10].map((m) => `<option value="${m}" ${m === state.shortSegMin ? "selected" : ""}>${m}分</option>`).join("")}</select>未満</label>
    <label class="muted">間隔 <select id="mergeGapMin">${[1,3,5,10].map((m) => `<option value="${m}" ${m === state.mergeGapMin ? "selected" : ""}>${m}分</option>`).join("")}</select>以内</label>` : ""}
    <button class="btn" data-zoom="-1" aria-label="縮小">−</button><button class="btn" data-zoom="1" aria-label="拡大">＋</button>
    <span class="muted note-long">帯 = 作業区間(記録の間隔が10分を超えたら分割、短い作業も読める高さで表示)・斜線 = 作業中</span>
  </div>`;
  if (mobile) {
    html += `<div class="daystrip" role="tablist">${[0, 1, 2, 3, 4, 5, 6].map((i) => {
      const d = addDays(ws, i);
      return `<button data-mday="${i}" class="${i === di ? "active" : ""} ${d.toDateString() === todayKey ? "today" : ""}"><span>${WEEKDAYS[d.getDay()]}</span><b>${d.getDate()}</b></button>`;
    }).join("")}</div>`;
  }
  html += legendHtml(inWeek);

  html += `<div class="cal ${mobile ? "single" : ""}"><div class="cal-head"></div>`;
  for (const i of dayIdxs) {
    const d = addDays(ws, i);
    html += `<div class="cal-head ${d.toDateString() === todayKey ? "today" : ""}">${WEEKDAYS[d.getDay()]} ${d.getMonth() + 1}/${d.getDate()}</div>`;
  }
  html += `<div class="cal-hours" style="height:${H}px">${Array.from({ length: 23 }, (_, h) => `<div style="top:${(h + 1) * hp}px">${h + 1}:00</div>`).join("")}</div>`;

  for (const i of dayIdxs) {
    const d = addDays(ws, i);
    const d0 = d.getTime() / 1000, d1 = addDays(d, 1).getTime() / 1000;
    const isToday = d.toDateString() === todayKey;
    let col = `<div class="cal-day ${isToday ? "today" : ""}" style="height:${H}px">`;
    for (let h = 1; h < 24; h++) col += `<div class="cal-line" style="top:${h * hp}px"></div>`;
    if (hp >= 60) for (let h = 0; h < 24; h++) col += `<div class="cal-line half" style="top:${(h + 0.5) * hp}px"></div>`;

    // この日の帯を集めて、重なるものを横に並べる
    const items = [];
    for (const s of inWeek) {
      const segs = s.segments.map(([a, b]) => [a, b]);
      if (s.status === "running" && segs.length) segs[segs.length - 1][1] = effectiveEnd(s);
      for (const [a, b] of segs) {
        const st = Math.max(a, d0), en = Math.min(Math.max(b, a + 60), d1);
        // 見た目の終わり: 短い作業も最小の高さで描くので、重なり判定もその高さで行う
        if (en > st) items.push({ s, st, en: Math.max(en, st + (MIN_BAND_PX / hp) * 3600), a, b });
      }
    }
    const shown = state.mergeShort ? mergeShortItems(items, hp) : items;
    layoutLanes(shown);
    for (const it of shown) {
      if (it.group) { col += mergedBandHtml(it, d0, hp); continue; }
      const top = ((it.st - d0) / 3600) * hp;
      const height = ((it.en - it.st) / 3600) * hp;
      const left = (it.lane / it.lanes) * 100, width = 100 / it.lanes;
      const s = it.s;
      const cls = ["band", s.status === "running" ? "running" : "", state.selected === s.id ? "selected" : "", state.recentlyUpdated.has(s.id) ? "flash" : ""].join(" ");
      const tip = `<b>${esc(s.title)}</b><br>${STATUS[s.status].label} ・ ${esc(s.workType)} ・ ${esc(s.projectName)}<br>${fmtTime(it.a)}〜${fmtTime(it.b)}(${fmtDur(it.b - it.a)})`;
      const dur = `<b class="d">${fmtDur(Math.max(60, it.b - it.a))}</b>`;
      const body = height >= 34
        ? `<span class="t">${esc(s.title)}</span><span class="t sub">${fmtTime(it.a)}〜${fmtTime(it.b)} ${dur}</span>`
        : `<span class="t">${dur} ${esc(s.title)}</span>`;
      col += `<div class="${cls}" data-open="${s.id}" data-tip="${esc(tip)}" style="--c:${colorOf(s)};top:${top}px;height:${height}px;left:calc(${left}% + 1px);width:calc(${width}% - 2px)">${body}</div>`;
    }
    if (isToday) {
      const n = new Date();
      col += `<div class="cal-now" style="top:${(n.getHours() + n.getMinutes() / 60) * hp}px"></div>`;
    }
    html += col + `</div>`;
  }
  return html + `</div>`;
}
// 極短の作業区間を、プロジェクトごとに、帯として重なる(または一定間隔以内に続く)ものを 1 つの帯へまとめる
function mergeShortItems(items, hp) {
  const minSec = (MIN_BAND_PX / hp) * 3600;
  const isShort = (it) => it.b - it.a < shortSec();
  const out = items.filter((it) => !isShort(it));
  const byProject = new Map();
  for (const it of items.filter(isShort)) {
    if (!byProject.has(it.s.project)) byProject.set(it.s.project, []);
    byProject.get(it.s.project).push(it);
  }
  for (const list of byProject.values()) {
    list.sort((x, y) => x.st - y.st);
    let cur = null, curEnd = -Infinity;
    const flush = () => {
      if (!cur) return;
      if (cur.length === 1) out.push(cur[0]);
      else {
        const st = cur[0].st, en = Math.max(...cur.map((c) => c.en));
        out.push({ group: cur, st, en: Math.max(en, st + minSec), a: Math.min(...cur.map((c) => c.a)), b: Math.max(...cur.map((c) => c.b)) });
      }
      cur = null;
    };
    for (const it of list) {
      if (cur && it.st - curEnd > state.mergeGapMin * 60) flush();
      if (!cur) cur = [];
      cur.push(it);
      curEnd = Math.max(curEnd, it.en);
    }
    flush();
  }
  return out;
}
function mergedBandHtml(it, d0, hp) {
  const top = ((it.st - d0) / 3600) * hp;
  const height = ((it.en - it.st) / 3600) * hp;
  const left = (it.lane / it.lanes) * 100, width = 100 / it.lanes;
  const n = it.group.length;
  const proj = it.group[0].s.projectName;
  const ids = new Set(it.group.map((g) => g.s.id));
  const lines = it.group.slice(0, 10).map((g) => `${fmtTime(g.a)} ${esc(g.s.title)}`).join("<br>");
  const more = n > 10 ? `<br>…ほか ${n - 10} 件` : "";
  const tip = `<b>${esc(proj)} の極短の作業 ${n} 件</b>(${fmtTime(it.a)}〜${fmtTime(it.b)})<br>${lines}${more}<br><span class="muted">クリックで全件を一覧表示</span>`;
  const sel = [...ids].includes(state.selected) ? "selected" : "";
  const all = it.group.map((g) => g.s.id).join(",");
  return `<div class="band merged ${sel}" data-group="${all}" data-tip="${esc(tip)}" style="top:${top}px;height:${height}px;left:calc(${left}% + 1px);width:calc(${width}% - 2px)"><span class="t"><b class="d">${n}件</b> ${esc(proj)}</span></div>`;
}
function layoutLanes(items) {
  // 重なりのまとまり(クラスタ)ごとに、空いている最小の列へ割り当てる
  items.sort((x, y) => x.st - y.st || y.en - x.en);
  let cluster = [], clusterEnd = -Infinity;
  const flush = () => {
    const lanes = Math.max(1, ...cluster.map((c) => c.lane + 1));
    cluster.forEach((c) => (c.lanes = lanes));
    cluster = [];
  };
  const laneEnds = [];
  for (const it of items) {
    if (it.st >= clusterEnd && cluster.length) { flush(); laneEnds.length = 0; }
    let lane = laneEnds.findIndex((e) => e <= it.st);
    if (lane < 0) { lane = laneEnds.length; laneEnds.push(it.en); } else laneEnds[lane] = it.en;
    it.lane = lane;
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.en);
  }
  if (cluster.length) flush();
}

// ----- タスク -----
function renderTasks() {
  let list = visibleSessions({ useStatus: false });
  if (state.taskScope === "active") list = list.filter((s) => s.status === "running" || s.status === "waiting");
  const cols = { pending: [], in_progress: [], completed: [] };
  for (const s of list) for (const t of s.tasks) (cols[t.status] || cols.pending).push({ t, s });
  for (const k in cols) cols[k].sort((a, b) => (b.t.ts || 0) - (a.t.ts || 0));
  const total = sum(Object.values(cols), (c) => c.length);
  const toolbar = `<div class="toolbar">
    <div class="seg"><button data-taskscope="all" class="${state.taskScope === "all" ? "active" : ""}">全セッション</button>
    <button data-taskscope="active" class="${state.taskScope === "active" ? "active" : ""}">稼働中のみ</button></div>
    <label>色分け <select id="colorBy">${Object.entries(COLOR_BY).map(([k, v]) => `<option value="${k}" ${k === state.colorBy ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <span class="muted">Claude が TaskCreate/TaskUpdate・TodoWrite で管理したタスク(各セッションの最新の状態)</span></div>`;
  if (!total) return toolbar + `<div class="empty">タスクの記録はありません。</div>`;
  const col = (key, label) => `<div class="column"><h3><span>${label}</span><span class="num muted">${cols[key].length}</span></h3>
    ${cols[key].map(({ t, s }) => `<div class="tcard" data-open="${s.id}" style="--c:${colorOf(s)}">
      <div>${esc(t.content)}</div><div class="meta">${statusBadge(s)} ・ ${esc(s.title)}</div></div>`).join("")}</div>`;
  return toolbar + `<div class="board">${col("pending", "未着手")}${col("in_progress", "進行中")}${col("completed", "完了")}</div>`;
}

// ----- 課題 -----
function renderIssues() {
  const list = visibleSessions({ useStatus: false });
  const all = list.flatMap((s) => s.issues.map((i) => ({ ...i, s })));
  const counts = {};
  for (const i of all) counts[i.kind] = (counts[i.kind] || 0) + 1;
  const rows = all.filter((i) => !state.issueFilter.size || state.issueFilter.has(i.kind)).sort((a, b) => b.ts - a.ts);
  const toolbar = `<div class="toolbar">${Object.entries(ISSUE_KIND).map(([k, v]) =>
    `<button class="chip ${state.issueFilter.has(k) ? "active" : ""}" data-issue="${k}">${v}<span class="n">${counts[k] || 0}</span></button>`).join("")}
    <span class="muted">履歴から機械的に抽出(ツール結果の is_error、ユーザーによる中断、APIエラー)</span></div>`;
  if (!rows.length) return toolbar + `<div class="empty">課題はありません。</div>`;
  return toolbar + `<table class="list"><thead><tr><th>日時</th><th>種類</th><th>ツール等</th><th>内容</th><th>セッション</th></tr></thead><tbody>
    ${rows.slice(0, 500).map((i) => `<tr data-open="${i.s.id}">
      <td class="num">${fmtDateTime(i.ts)}</td><td><span class="tag">${ISSUE_KIND[i.kind] || i.kind}</span></td>
      <td>${esc(i.label)}${i.detail ? `<div class="muted clamp">${esc(i.detail)}</div>` : ""}</td>
      <td><div class="clamp">${esc(i.text)}</div></td>
      <td><div class="title-cell"><i class="swatch" style="--c:${colorOf(i.s)}"></i><span class="clamp">${esc(i.s.title)}</span></div></td></tr>`).join("")}
    </tbody></table>`;
}

// ----- 変更履歴 -----
function renderChanges() {
  const list = visibleSessions({ useStatus: false });
  const commits = list.flatMap((s) => s.commits.map((c) => ({ ...c, s }))).sort((a, b) => b.ts - a.ts);
  const files = new Map();
  for (const s of list) for (const f of s.files) {
    const e = files.get(f.path) || { path: f.path, edits: 0, last: 0, sessions: new Set() };
    e.edits += f.edits; e.last = Math.max(e.last, f.last || 0); e.sessions.add(s.id);
    files.set(f.path, e);
  }
  const fileRows = [...files.values()].sort((a, b) => b.last - a.last).slice(0, 300);
  return `<div class="cards">
    <div class="card"><h3>コミット <span class="muted">${commits.length}件(Bash で git commit が成功したもの)</span></h3>
      ${commits.length ? `<table class="list"><thead><tr><th>日時</th><th>ハッシュ</th><th>メッセージ</th><th>セッション</th></tr></thead><tbody>
      ${commits.map((c) => `<tr data-open="${c.s.id}"><td class="num">${fmtDateTime(c.ts)}</td><td class="num">${c.hash ? esc(c.hash) : '<span class="muted">不明</span>'}<div class="muted">${esc(c.branch)}</div></td>
        <td style="min-width:120px">${esc(c.message || "-")}</td><td><span class="clamp">${esc(c.s.title)}</span></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">コミットの記録はありません。</div>`}</div>
    <div class="card"><h3>変更したファイル <span class="muted">${files.size}件(Edit/Write等)</span></h3>
      ${fileRows.length ? `<table class="list"><thead><tr><th>ファイル</th><th class="r">編集</th><th class="r">セッション</th><th>最終</th></tr></thead><tbody>
      ${fileRows.map((f) => `<tr data-open="${[...f.sessions][0]}"><td style="overflow-wrap:anywhere">${esc(f.path)}</td><td class="r">${f.edits}</td><td class="r">${f.sessions.size}</td><td class="muted">${fmtAgo(f.last)}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">ファイル変更の記録はありません。</div>`}</div></div>`;
}


// ----- レポート(日報・週報) -----
// 履歴にある事実だけを期間で切り出して文章にする。推測で補わない。
function reportRange() {
  const base = state.reportDate || new Date();
  const start = state.reportPeriod === "week" ? startOfWeek(base) : new Date(base.getFullYear(), base.getMonth(), base.getDate());
  return [start, addDays(start, state.reportPeriod === "week" ? 7 : 1)];
}
function overlapSec(segs, t0, t1) {
  return sum(segs, ([a, b]) => Math.max(0, Math.min(Math.max(b, a + 60), t1) - Math.max(a, t0)));
}
function fmtYMD(d) { return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}(${WEEKDAYS[d.getDay()]})`; }
function fmtMD(d) { return `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAYS[d.getDay()]})`; }
function oneLine(text, limit = 150) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  return t.length > limit ? t.slice(0, limit - 1) + "…" : t;
}
function relPath(path, project) {
  return project && path.startsWith(project + "/") ? path.slice(project.length + 1) : path;
}
function buildReport() {
  const [start, end] = reportRange();
  const t0 = start.getTime() / 1000, t1 = end.getTime() / 1000;
  const inT = (ts) => ts != null && ts >= t0 && ts < t1;
  const rows = [];
  for (const s of visibleSessions({ useStatus: false })) {
    const segs = s.segments.filter(([a, b]) => Math.max(b, a + 60) > t0 && a < t1);
    const sec = overlapSec(segs, t0, t1);
    if (sec <= 0) continue;
    rows.push({
      s, sec,
      from: Math.max(segs[0][0], t0),
      to: Math.min(Math.max(segs[segs.length - 1][1], segs[segs.length - 1][0] + 60), t1),
      prompts: (s.prompts || []).filter((p) => inT(p.ts)),
      commits: s.commits.filter((c) => inT(c.ts)),
      tasks: s.tasks.filter((t) => t.status === "completed" && inT(t.ts)),
      files: s.files.map((f) => ({ path: f.path, n: (f.times || []).filter(inT).length })).filter((f) => f.n > 0),
      issues: s.issues.filter((i) => inT(i.ts)),
    });
  }
  rows.sort((a, b) => a.from - b.from);
  return { start, end, t0, t1, rows };
}
function reportMarkdown(r) {
  const week = state.reportPeriod === "week";
  const L = [];
  const title = week ? `作業レポート(週報) ${fmtYMD(r.start)}〜${fmtMD(addDays(r.end, -1))}` : `作業レポート(日報) ${fmtYMD(r.start)}`;
  L.push(`# ${title}`, "");
  const proj = state.project ? (state.sessions.size && [...state.sessions.values()].find((s) => s.project === state.project)?.projectName) || state.project : "すべてのプロジェクト";
  L.push(`- 対象: ${proj}${state.search.trim() ? `(検索語「${state.search.trim()}」で絞り込み)` : ""}`);
  L.push(`- 作成: ${fmtDateTime(now())}(Claude Code の作業履歴から自動集計)`, "");
  if (!r.rows.length) {
    L.push("この期間の作業記録はありません。");
    return L.join("\n");
  }
  const total = sum(r.rows, (x) => x.sec);
  const commits = r.rows.flatMap((x) => x.commits.map((c) => ({ ...c, s: x.s })));
  const files = new Set(r.rows.flatMap((x) => x.files.map((f) => f.path)));
  const tasks = sum(r.rows, (x) => x.tasks.length);
  const issues = r.rows.flatMap((x) => x.issues);
  L.push("## 概要", "");
  L.push(`- セッション: ${r.rows.length}件`);
  L.push(`- 作業時間: ${fmtDur(total)}(記録の間隔が10分以内を連続した作業とみなした合計)`);
  L.push(`- コミット: ${commits.length}件`);
  L.push(`- 編集したファイル: ${files.size}件`);
  L.push(`- 完了したタスク: ${tasks}件`);
  L.push(`- 課題(ツールエラー・中断・APIエラー): ${issues.length}件`, "");

  if (week) {
    L.push("## 日別の作業時間", "");
    for (let i = 0; i < 7; i++) {
      const d0 = addDays(r.start, i), a = d0.getTime() / 1000, b = addDays(d0, 1).getTime() / 1000;
      const daySec = sum(r.rows, (x) => overlapSec(x.s.segments, a, b));
      const n = r.rows.filter((x) => overlapSec(x.s.segments, a, b) > 0).length;
      L.push(`- ${fmtMD(d0)}: ${daySec ? `${fmtDur(daySec)}(${n}セッション)` : "作業なし"}`);
    }
    L.push("");
  }
  if (!state.project) {
    const byProj = new Map();
    for (const x of r.rows) {
      const e = byProj.get(x.s.project) || { name: x.s.projectName, sec: 0, n: 0 };
      e.sec += x.sec; e.n++;
      byProj.set(x.s.project, e);
    }
    if (byProj.size > 1) {
      L.push("## プロジェクト別の作業時間", "");
      for (const e of [...byProj.values()].sort((a, b) => b.sec - a.sec)) L.push(`- ${e.name}: ${fmtDur(e.sec)}(${e.n}セッション)`);
      L.push("");
    }
  }

  L.push("## セッション", "");
  r.rows.forEach((x, i) => {
    const s = x.s;
    L.push(`### ${i + 1}. ${oneLine(s.title, 80)}`, "");
    const when = week ? `${fmtMD(new Date(x.from * 1000))} ${fmtTime(x.from)}〜${fmtTime(x.to)}` : `${fmtTime(x.from)}〜${fmtTime(x.to)}`;
    L.push(`- 時間: ${when}(作業 ${fmtDur(x.sec)})`);
    L.push(`- プロジェクト: ${s.projectName}${s.branch ? ` / ブランチ: ${s.branch}` : ""}`);
    if (state.reportPrompts && x.prompts.length) {
      L.push(`- 依頼(${x.prompts.length}件${x.prompts.length > 5 ? "、最初の5件" : ""}):`);
      for (const p of x.prompts.slice(0, 5)) L.push(`  - ${oneLine(p.text)}`);
    }
    if (x.tasks.length) L.push(`- 完了したタスク: ${x.tasks.map((t) => oneLine(t.content, 60)).join("、")}`);
    for (const c of x.commits) L.push(`- コミット: ${c.hash ? `\`${c.hash}\`` : "(ハッシュ不明)"} ${oneLine(c.message || "(メッセージなし)", 100)}`);
    if (state.reportFiles && x.files.length) {
      const shown = x.files.slice(0, 15).map((f) => `${relPath(f.path, s.project)}(${f.n}回)`);
      L.push(`- 編集したファイル: ${shown.join("、")}${x.files.length > 15 ? ` ほか${x.files.length - 15}件` : ""}`);
    }
    if (x.issues.length) {
      const c = {};
      for (const it of x.issues) c[it.kind] = (c[it.kind] || 0) + 1;
      L.push(`- 課題: ${x.issues.length}件(${Object.entries(c).map(([k, v]) => `${ISSUE_KIND[k] || k} ${v}`).join("、")})`);
    }
    L.push("");
  });

  if (commits.length) {
    L.push("## コミット一覧", "");
    for (const c of commits.sort((a, b) => a.ts - b.ts)) L.push(`- ${week ? fmtMD(new Date(c.ts * 1000)) + " " : ""}${fmtTime(c.ts)} ${c.hash ? `\`${c.hash}\`` : "(ハッシュ不明)"} ${oneLine(c.message || "(メッセージなし)", 100)}`);
    L.push("");
  }
  return L.join("\n").replace(/\n+$/, "\n");
}
// 上の関数が出す限られた書式(見出し・2段までの箇条書き・`コード`)だけを HTML にする
function mdToHtml(md) {
  const inline = (t) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", depth = 0;
  const close = (to) => { while (depth > to) { html += depth === 2 ? "</li></ul>" : "</li></ul>"; depth--; } };
  for (const line of md.split("\n")) {
    const m = line.match(/^( *)- (.*)$/);
    if (m) {
      const level = m[1].length >= 2 ? 2 : 1;
      if (level > depth) { html += "<ul><li>"; depth++; if (level > depth) { html += "<ul><li>"; depth++; } }
      else { close(level); html += "</li><li>"; }
      html += inline(m[2]);
      continue;
    }
    close(0);
    const h = line.match(/^(#{1,3}) (.*)$/);
    if (h) html += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  close(0);
  return html;
}
function renderReport() {
  const r = buildReport();
  const md = reportMarkdown(r);
  state.reportText = md;
  const week = state.reportPeriod === "week";
  const label = week ? `${fmtYMD(r.start)} 〜 ${fmtMD(addDays(r.end, -1))}` : fmtYMD(r.start);
  return `<div class="toolbar">
      <div class="seg">
        <button data-rperiod="day" class="${!week ? "active" : ""}">日報</button>
        <button data-rperiod="week" class="${week ? "active" : ""}">週報</button>
      </div>
      <button class="btn" data-rnav="-1" aria-label="前へ">◀</button>
      <button class="btn" data-rnav="today">${week ? "今週" : "今日"}</button>
      <button class="btn" data-rnav="1" aria-label="次へ">▶</button>
      <strong class="num">${label}</strong>
      <span class="sep"></span>
      <label><input type="checkbox" id="reportPrompts" ${state.reportPrompts ? "checked" : ""}> 依頼内容</label>
      <label><input type="checkbox" id="reportFiles" ${state.reportFiles ? "checked" : ""}> 編集したファイル</label>
      <span class="sep"></span>
      <button class="btn" data-rcopy>Markdownをコピー</button>
      <button class="btn" data-rsave>.md で保存</button>
      <span id="rmsg" class="muted"></span>
    </div>
    <div class="card report">${mdToHtml(md)}</div>
    <div class="note">上部のプロジェクト選択と検索欄の条件で絞り込まれます。作業時間は、記録の間隔が10分以内を連続した作業とみなした合計です。
      完了したタスクは「期間内に最後に更新され、完了になっているもの」です。</div>`;
}
async function copyReport() {
  const text = state.reportText || "";
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {
    // 古いブラウザや http の場合の代わりの方法
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    ta.remove();
  }
  const m = $("#rmsg");
  if (m) m.textContent = ok ? "コピーしました" : "コピーできませんでした";
}
function saveReport() {
  const [start] = reportRange();
  // 日本語のファイル名はブラウザによって「download」に置き換わることがあるため英数字にする
  const name = `claude-report_${state.reportPeriod === "week" ? "weekly" : "daily"}_${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}.md`;
  const url = URL.createObjectURL(new Blob([state.reportText || ""], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ----- 詳細パネル -----
function renderGroupList(box) {
  const rows = state.groupIds.map((id) => state.sessions.get(id)).filter(Boolean).sort((a, b) => a.start - b.start);
  state.groupDay = state.groupDay ? Number(state.groupDay) : null;
  box.classList.remove("hidden");
  box.innerHTML = `
    <button class="close" data-close aria-label="閉じる">×</button>
    ${state.groupDay ? `<button class="btn" data-gotoweek="${state.groupDay}">この週を時間帯表示で見る</button>` : ""}
    <h2>${state.groupTitle ? esc(state.groupTitle) : (new Set(rows.map((r) => r.project)).size === 1 ? esc(rows[0].projectName) + " の" : "") + "短時間の作業"} ${rows.length} 件</h2>
    <table class="list"><thead><tr><th>開始</th><th>${state.groupDay ? "この日の作業" : "作業時間"}</th><th>セッション</th></tr></thead><tbody>
    ${rows.map((s) => `<tr data-open="${s.id}"><td class="num">${fmtDateTime(s.start)}</td><td class="num">${fmtDur(Math.max(60, state.groupDay ? overlapSec(s.segments, state.groupDay, state.groupDay + 86400) : s.activeSec))}</td>
      <td><div class="title-cell"><i class="swatch" style="--c:${colorOf(s)}"></i><span class="clamp">${esc(s.title)}</span></div><div class="muted clamp">${esc(s.projectName)}</div></td></tr>`).join("")}
    </tbody></table>`;
}

function goToWeek(ts) {
  state.weekStart = startOfWeek(new Date(ts * 1000));
  state.mobileDay = Math.round((new Date(ts * 1000).setHours(0, 0, 0, 0) - state.weekStart.getTime()) / 86400000);
  state.sessionMode = "calendar";
  state.groupIds = null; state.selected = null; state.panel = null;
  savePrefs(); render();
}
function setRule(project, hide) {
  // 手動の設定なしの自動判定結果を調べ、それと同じ値なら設定を残さない
  const saved = state.projectRules;
  state.projectRules = {};
  const auto = !!projectStats().get(project)?.hide;
  state.projectRules = { ...saved };
  if (hide === auto) delete state.projectRules[project];
  else state.projectRules[project] = hide ? "hide" : "show";
  savePrefs(); render({ keepScroll: true });
}
function renderHiddenPanel(box) {
  const rows = [...projectStats().values()].filter((e) => e.short > 0).sort((a, b) => b.short - a.short);
  box.classList.remove("hidden");
  box.innerHTML = `
    <button class="close" data-close aria-label="閉じる">×</button>
    <h2>極短セッションを隠す設定</h2>
    <p class="muted">開始から最終活動まで ${state.shortSegMin} 分未満のセッションだけが対象です。チェックしたプロジェクトでは、それらを一覧・カレンダー・集計から外します。
    自動判定は「フォルダ名が memory」「3 件以上あって全部が極短」です。チェックを変えると手動の設定として保存されます。</p>
    <table class="list"><thead><tr><th>隠す</th><th>プロジェクト</th><th class="r">極短 / 全体</th><th>判定</th></tr></thead><tbody>
    ${rows.map((e) => `<tr><td><input type="checkbox" id="ruleHide" data-project="${esc(e.project)}" ${e.hide ? "checked" : ""}></td>
      <td><div class="clamp">${esc(e.name)}</div><div class="muted clamp">${esc(e.project)}</div></td>
      <td class="r num">${e.short} / ${e.n}</td><td class="muted">${esc(e.reason || "-")}</td></tr>`).join("")}
    </tbody></table>`;
}

function renderDetail() {
  const box = $("#detail");
  const s = state.selected && state.sessions.get(state.selected);
  if (!s && state.groupIds) { renderGroupList(box); return; }
  if (!s && state.panel === "hidden") { renderHiddenPanel(box); return; }
  if (!s) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const logScroll = $("#log", box)?.scrollTop;
  const done = s.tasks.filter((t) => t.status === "completed").length;
  const tools = Object.entries(s.tools).sort((a, b) => b[1] - a[1]);
  const mark = { completed: "☑", in_progress: "◐", pending: "☐" };
  box.innerHTML = `
    <button class="close" data-close aria-label="閉じる">×</button>
    ${state.groupIds ? `<button class="btn" data-groupback>← まとめた一覧(${state.groupIds.length}件)に戻る</button>` : ""}
    <h2>${esc(s.title)}</h2>
    <div class="toolbar">${statusBadge(s)}<span class="tag"><i class="swatch" style="--c:${colorOf(s, "workType")}"></i>${esc(s.workType)}</span>
      <span class="muted">${fmtAgo(s.end)}に最終活動</span></div>

    <div class="box"><h4>セッションの状態</h4><dl class="meta">
      <dt>状態</dt><dd>${STATUS[s.status].label}</dd>
      <dt>判定の根拠</dt><dd>${{ process: "起動中のClaude Codeプロセスの状態", transcript: "履歴の末尾から推定(プロセス情報なし)", hub: "Macからの同期が途絶えているため不明(最後に届いた時点では作業中/入力待ち)" }[s.statusSource] || "-"}</dd>
      <dt>最後の出来事</dt><dd>${esc(LAST_KIND[s.lastKind] || "-")}</dd>
      <dt>タスク</dt><dd>${s.tasks.length ? `${done}/${s.tasks.length} 完了` : "なし"}</dd>
      <dt>課題</dt><dd>${s.issues.length} 件</dd>
    </dl></div>

    <div class="box"><h4>概要</h4><dl class="meta">
      <dt>プロジェクト</dt><dd>${esc(s.project)}</dd>
      <dt>ブランチ</dt><dd>${esc(s.branch || "-")}</dd>
      <dt>開始</dt><dd class="num">${fmtDateTime(s.start)}</dd>
      <dt>最終活動</dt><dd class="num">${fmtDateTime(s.end)}</dd>
      <dt>作業時間</dt><dd>${fmtDur(s.activeSec)}(${s.segments.length}区間)</dd>
      <dt>モデル</dt><dd>${esc(Object.keys(s.models).join(", ") || "-")}</dd>
      <dt>トークン</dt><dd class="num">出力 ${fmtTok(s.tokens.output)} / 入力 ${fmtTok(s.tokens.input)} / キャッシュ読込 ${fmtTok(s.tokens.cacheRead)} / キャッシュ作成 ${fmtTok(s.tokens.cacheCreate)}</dd>
      <dt>応答数</dt><dd>${s.assistantMessages}${s.subagentRecords ? `(サブエージェントの記録 ${s.subagentRecords} 件を含む)` : ""}</dd>
      <dt>起動元</dt><dd>${esc(s.entrypoint || "-")} ・ v${esc(s.version || "-")}</dd>
      <dt>ID</dt><dd class="muted">${esc(s.id)}</dd>
    </dl></div>

    ${s.tasks.length ? `<div class="box"><h4>タスク(${esc(s.taskSource)})</h4><ul class="plain">
      ${s.tasks.map((t) => `<li><span class="task-st" title="${esc(t.status)}">${mark[t.status] || "☐"}</span> ${esc(t.content)}</li>`).join("")}</ul></div>` : ""}

    ${s.commits.length ? `<div class="box"><h4>コミット ${s.commits.length}件</h4><ul class="plain">
      ${s.commits.map((c) => `<li><span class="num">${c.hash ? esc(c.hash) : "(ハッシュ不明)"}</span> ${esc(c.message)} <span class="muted">${fmtTime(c.ts)}</span></li>`).join("")}</ul></div>` : ""}

    ${s.files.length ? `<div class="box"><h4>変更したファイル ${s.files.length}件</h4><ul class="plain">
      ${s.files.slice(0, 40).map((f) => `<li>${esc(f.path)} <span class="muted">×${f.edits}</span></li>`).join("")}</ul></div>` : ""}

    <div class="box"><h4>ユーザーの発言 ${s.promptCount}件</h4><ul class="plain">
      ${s.prompts.slice(-30).reverse().map((p) => `<li><span class="muted num">${fmtDateTime(p.ts)}</span><br>${esc(p.text)}</li>`).join("") || `<li class="muted">なし</li>`}</ul></div>

    ${tools.length ? `<div class="box"><h4>ツール使用回数</h4>${hbars(tools.slice(0, 12))}</div>` : ""}

    ${s.issues.length ? `<div class="box"><h4>課題 ${s.issues.length}件</h4><ul class="plain">
      ${s.issues.slice(-20).reverse().map((i) => `<li><span class="tag">${ISSUE_KIND[i.kind] || i.kind}</span> ${esc(i.label)} <span class="muted">${fmtTime(i.ts)}</span><br>${esc(i.text)}</li>`).join("")}</ul></div>` : ""}

    <div class="box"><h4>会話ログ <button class="btn" data-log style="float:right">${state.logOpen ? "閉じる" : "表示"}</button></h4>
      <div id="log" class="log" style="max-height:480px;overflow:auto">${state.logOpen ? (state.logHtml || "読み込み中…") : ""}</div></div>`;
  if (logScroll !== undefined && $("#log", box)) $("#log", box).scrollTop = logScroll;
}

async function loadLog(id) {
  const res = await fetch(`/api/session/${encodeURIComponent(id)}/log?limit=300`);
  if (res.status === 401) { location.href = "/login"; return; }
  const data = await res.json();
  if (state.selected !== id) return;
  const label = { prompt: "ユーザー", text: "Claude", tool_use: "ツール実行", tool_result: "ツール結果", error: "ツールエラー" };
  state.logHtml = data.events.map((e) => `<div class="ev ${e.kind} ${e.side ? "side" : ""}"><span class="h">${fmtTime(e.ts)} ${label[e.kind] || e.kind}${e.tool ? " " + esc(e.tool) : ""}${e.side ? "(サブエージェント)" : ""}</span>${esc(e.text)}</div>`).join("") || "記録がありません";
  const log = $("#log");
  if (log) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.innerHTML = state.logHtml;
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
}

function openSession(id) {
  if (state.selected !== id) { state.logOpen = false; state.logHtml = ""; }
  state.selected = id;
  render({ keepScroll: true });
}


// ---------- 通知(ブラウザ) ----------
// ページを開いている間(裏のタブでも可)、セッションが「作業中 → 入力待ち/エラー/応答完了」に変わったら通知する。
// サーバー側の ntfy 通知と同じ条件にしている。
function notifyEvent(prev, s) {
  if (prev.status !== "running" || s.status === "running") return null;
  if (s.status === "waiting") return "入力待ちになりました";
  if (s.status === "error") return "エラーで止まりました";
  if (s.status === "ended" && s.lastKind === "assistant_end") return "応答が終わりました";
  return null;
}
function notifySupported() { return "Notification" in window && window.isSecureContext; }
function renderNotifyButton() {
  const b = $("#notify");
  if (!b) return;
  let text = "🔔 オフ", cls = "";
  if (!notifySupported()) { text = "🔔 非対応"; b.title = "このブラウザ、または https でない接続では通知を使えません"; }
  else if (Notification.permission === "denied") { text = "🔔 ブロック中"; b.title = "ブラウザの設定でこのサイトの通知が拒否されています"; }
  else if (state.notify && Notification.permission === "granted") { text = "🔔 オン"; cls = "on"; }
  b.textContent = text;
  b.className = "notify " + cls;
}
async function toggleNotify() {
  if (!notifySupported()) { renderNotifyButton(); return; }
  if (state.notify && Notification.permission === "granted") {
    state.notify = false;
  } else {
    const perm = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
    state.notify = perm === "granted";
    if (state.notify) showNotification("通知をオンにしました", "セッションが入力待ち・エラーになるとお知らせします", null);
  }
  savePrefs();
  renderNotifyButton();
}
async function showNotification(title, body, sid) {
  const opts = { body, tag: sid || "cchv", data: { session: sid }, icon: "/static/icons/icon-192.png" };
  try {
    // Android の Chrome などは Service Worker 経由でしか通知を出せないので、そちらを優先する
    const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : null;
    if (reg) { await reg.showNotification(title, opts); return; }
    const n = new Notification(title, opts);
    n.onclick = () => { window.focus(); if (sid) openSession(sid); n.close(); };
  } catch (err) {
    console.warn("通知を表示できませんでした", err);
  }
}
function maybeNotify(prev, s) {
  if (!state.notify || !notifySupported() || Notification.permission !== "granted") return;
  const ev = notifyEvent(prev, s);
  // 画面を見ている最中(前面で操作中)は通知しない
  if (!ev || (document.visibilityState === "visible" && document.hasFocus())) return;
  showNotification(`Claude Code: ${ev}`, `${s.title}(${s.projectName})`, s.id);
}
// アプリとして追加できるよう Service Worker を登録する。通知をクリックしたときの画面の切り替えもここで受ける
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("Service Worker を登録できませんでした", err));
  navigator.serviceWorker.addEventListener("message", (ev) => {
    if (ev.data && ev.data.type === "open-session" && ev.data.session) openSession(ev.data.session);
  });
}
// 通知から開いたとき(#session=ID)は、そのセッションの詳細を開く
function openFromHash() {
  const m = location.hash.match(/session=([^&]+)/);
  if (m && state.sessions.has(decodeURIComponent(m[1]))) {
    if (state.view !== "sessions") state.view = "sessions";
    openSession(decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname);
  }
}
window.addEventListener("hashchange", openFromHash);

// ---------- 操作 ----------
document.addEventListener("click", (e) => {
  const t = e.target.closest("button, [data-open], [data-group], [data-hidesettings]");
  if (!t) return;
  const d = t.dataset;
  if (d.view) { state.view = d.view; savePrefs(); render(); return; }
  if (d.mode) { state.sessionMode = d.mode; savePrefs(); render(); return; }
  if (d.status) { state.statusFilter.has(d.status) ? state.statusFilter.delete(d.status) : state.statusFilter.add(d.status); render({ keepScroll: true }); return; }
  if (d.issue) { state.issueFilter.has(d.issue) ? state.issueFilter.delete(d.issue) : state.issueFilter.add(d.issue); render({ keepScroll: true }); return; }
  if (d.taskscope) { state.taskScope = d.taskscope; savePrefs(); render(); return; }
  if (d.rperiod) { state.reportPeriod = d.rperiod; savePrefs(); render(); return; }
  if (d.rnav) {
    const base = state.reportDate || new Date();
    state.reportDate = d.rnav === "today" ? null : addDays(base, Number(d.rnav) * (state.reportPeriod === "week" ? 7 : 1));
    render(); return;
  }
  if (d.rcopy !== undefined) { copyReport(); return; }
  if (d.rsave !== undefined) { saveReport(); return; }
  if (d.mday !== undefined) { state.mobileDay = Number(d.mday); render({ keepScroll: true }); return; }
  if (d.week) {
    state.mobileDay = null;
    state.weekStart = d.week === "today" ? startOfWeek(new Date()) : addDays(state.weekStart, 7 * Number(d.week));
    render(); return;
  }
  if (d.zoom) { state.hourPx = Math.min(HOUR_PX_MAX, Math.max(HOUR_PX_MIN, state.hourPx + Number(d.zoom) * 12)); savePrefs(); render({ keepScroll: true }); return; }
  if (d.close !== undefined) { state.panel = null; state.groupIds = null; state.selected = null; state.logOpen = false; render({ keepScroll: true }); return; }
  if (d.log !== undefined) { state.logOpen = !state.logOpen; renderDetail(); if (state.logOpen) loadLog(state.selected); return; }
  if (d.month) {
    const m = state.monthStart;
    state.monthStart = d.month === "today" ? new Date(new Date().getFullYear(), new Date().getMonth(), 1) : new Date(m.getFullYear(), m.getMonth() + Number(d.month), 1);
    render(); return;
  }
  if (d.group) { state.groupTitle = d.grouptitle || ""; state.groupDay = d.groupday ? Number(d.groupday) : null; state.groupIds = [...new Set(d.group.split(","))]; state.selected = null; state.logOpen = false; render({ keepScroll: true }); return; }
  if (d.hidesettings !== undefined) { state.panel = "hidden"; state.selected = null; state.groupIds = null; render({ keepScroll: true }); return; }
  if (d.gotoweek) { goToWeek(Number(d.gotoweek)); return; }
  if (d.groupback !== undefined) { state.selected = null; state.logOpen = false; render({ keepScroll: true }); return; }
  if (d.open) openSession(d.open);
});
document.addEventListener("change", (e) => {
  if (e.target.id === "colorBy") { state.colorBy = e.target.value; savePrefs(); render({ keepScroll: true }); }
  if (e.target.id === "showMemory") { state.showMemory = e.target.checked; savePrefs(); render({ keepScroll: true }); }
  if (e.target.id === "shortSegMin" || e.target.id === "mergeGapMin") { state[e.target.id] = Number(e.target.value); savePrefs(); render({ keepScroll: true }); }
  if (e.target.id === "ruleHide") { setRule(e.target.dataset.project, e.target.checked); return; }
  if (e.target.id === "mergeShort") { state.mergeShort = e.target.checked; savePrefs(); render({ keepScroll: true }); }
  if (e.target.id === "sort") { state.sort = e.target.value; savePrefs(); render({ keepScroll: true }); }
  if (e.target.id === "reportPrompts" || e.target.id === "reportFiles") { state[e.target.id] = e.target.checked; savePrefs(); render({ keepScroll: true }); }
});
$("#notify").addEventListener("click", toggleNotify);
renderNotifyButton();
$("#search").addEventListener("input", (e) => { state.search = e.target.value; render({ keepScroll: true }); });
$("#project").addEventListener("change", (e) => { state.project = e.target.value; savePrefs(); render(); });
document.addEventListener("dblclick", (e) => {
  const c = e.target.closest(".month-cell");
  if (c && c.dataset.day) goToWeek(Number(c.dataset.day));
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && (state.selected || state.groupIds || state.panel)) { state.panel = null; state.groupIds = null; state.selected = null; state.logOpen = false; render({ keepScroll: true }); }
});
// Ctrl+ホイールでカレンダーを拡大縮小
$("#main").addEventListener("wheel", (e) => {
  if (!e.ctrlKey || state.view !== "sessions" || state.sessionMode !== "calendar") return;
  e.preventDefault();
  state.hourPx = Math.min(HOUR_PX_MAX, Math.max(HOUR_PX_MIN, state.hourPx + (e.deltaY < 0 ? 12 : -12)));
  savePrefs(); render({ keepScroll: true });
}, { passive: false });

// ツールチップ
const tip = $("#tooltip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.classList.add("hidden"); return; }
  tip.innerHTML = t.dataset.tip;
  tip.classList.remove("hidden");
  const r = tip.getBoundingClientRect();
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
});

// 「〜分前」や現在時刻線を進めるため、30秒ごとに描き直す(サーバーへの問い合わせはしない)
setInterval(() => {
  renderSync();
  if (document.activeElement?.tagName === "SELECT") return; // 選択中のプルダウンを閉じないため
  render({ keepScroll: true });
}, 30000);

loadState().then(() => { openFromHash(); connectEvents(); }).catch((err) => {
  $("#main").innerHTML = `<div class="empty">サーバーからデータを取得できませんでした: ${esc(err)}</div>`;
});
