/* RippleDrift UI — results.json を読んで描画する。チャートは evilcharts 仕様（ECharts 同梱）。 */
"use strict";

// ---------- evilcharts ヘルパー（ネイビー＋グレー、上昇=緑／下落=赤） ----------
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const EvilCharts = {
  MONO: "ui-monospace, SFMono-Regular, Menlo, monospace",
  get TOKENS() { return { background: cssVar("--chart-bg"), foreground: cssVar("--chart-fg"), mutedForeground: cssVar("--chart-muted"), border: cssVar("--chart-border"), primary: cssVar("--navy-deep") }; },
  get NAVY() { return cssVar("--navy"); }, get BLUE_SOFT() { return cssVar("--accent"); }, get INK() { return cssVar("--chart-fg"); },
  get GRAY() { return cssVar("--chart-gray"); }, get GRAY_LIGHT() { return cssVar("--chart-gray-light"); }, get UP() { return cssVar("--up"); }, get DOWN() { return cssVar("--down"); },
  tokens() { return this.TOKENS; },
  withAlpha(color, alpha) {
    if (color.startsWith("#")) { const n = parseInt(color.slice(1), 16); return `rgba(${(n>>16)&255}, ${(n>>8)&255}, ${n&255}, ${alpha})`; }
    return color;
  },
  areaFill(color) {
    return new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: this.withAlpha(color, 0.16) }, { offset: 1, color: this.withAlpha(color, 0) }]);
  },
  grid(o = {}) { return { left: 8, right: 8, top: o.top ?? 16, bottom: o.bottom ?? 8 }; },
  xAxis(categories, o = {}) {
    const t = this.tokens();
    return { type: "category", boundaryGap: o.bar ?? false, data: categories, axisLine: { show: false },
      axisTick: { show: true, alignWithLabel: true, length: 0.5, lineStyle: { color: t.border, width: 3, cap: "round" } },
      splitLine: { show: false }, axisLabel: { color: t.mutedForeground, fontSize: 10, margin: 8, formatter: o.formatter, interval: o.interval } };
  },
  yAxis(o = {}) {
    const t = this.tokens();
    return { type: "value", axisLine: { show: false }, axisTick: { show: false }, scale: o.scale ?? false,
      splitLine: { show: true, lineStyle: { color: t.border, type: [3, 3], width: 1 } },
      axisLabel: { color: t.mutedForeground, fontSize: 10, margin: 8, formatter: o.formatter }, min: o.min, max: o.max };
  },
  tooltip(valueFormat = v => v, o = {}) {
    const t = this.tokens(); const self = this;
    return { trigger: o.trigger ?? "axis", confine: true, backgroundColor: "transparent", borderWidth: 0, padding: 0, extraCssText: "box-shadow:none;",
      axisPointer: { type: "line", lineStyle: { color: t.border, width: 0.8, type: [3, 3] } },
      formatter(params) {
        const rows = Array.isArray(params) ? params : [params];
        if (!rows.length) return "";
        const label = o.label ? o.label(rows[0]) : (rows[0].axisValueLabel ?? rows[0].name ?? "");
        const body = rows.map(p => {
          const c = typeof p.color === "string" ? p.color : self.INK;
          const v = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
          if (v === null || v === undefined || Number.isNaN(v)) return "";
          return `<div style="display:flex;align-items:center;gap:8px;"><div style="width:10px;height:10px;border-radius:3px;background:${c};flex-shrink:0;"></div>
            <div style="display:flex;flex:1;justify-content:space-between;gap:16px;line-height:1;"><span style="color:${t.mutedForeground};">${p.seriesName}</span>
            <span style="color:${t.foreground};font-family:${self.MONO};font-variant-numeric:tabular-nums;font-weight:600;">${valueFormat(v, p)}</span></div></div>`;
        }).join("");
        return `<div style="min-width:8rem;display:grid;gap:6px;border:1px solid ${self.withAlpha(t.border, 0.9)};border-radius:12px;padding:8px 12px;font-size:12px;font-family:inherit;background:${t.background};box-shadow:0 20px 25px -5px rgba(24,40,80,.12),0 8px 10px -6px rgba(24,40,80,.1);">
          <div style="font-weight:600;color:${t.primary};">${label}</div><div style="display:grid;gap:6px;">${body}</div></div>`;
      } };
  },
  legend(el, items) {
    el.innerHTML = items.map(([label, color, dashed]) => `<div class="legend-item"><div class="legend-swatch" style="background:${color};${dashed ? "height:2px;border-radius:0;width:14px;" : ""}"></div><span class="legend-label">${label}</span></div>`).join("");
  },
  lineSeries({ name, data, color, area = false, dashed = false, width = 1, z }) {
    return { name, type: "line", data, z, lineStyle: { width, color, type: dashed ? [4, 4] : "solid" }, itemStyle: { color },
      symbol: "circle", symbolSize: 0, showSymbol: false, connectNulls: false, smooth: 0.25,
      ...(area ? { areaStyle: { color: this.areaFill(color) } } : {}),
      emphasis: { focus: "series" }, blur: { lineStyle: { opacity: 0.3 }, areaStyle: { opacity: 0.1 }, itemStyle: { opacity: 0.3 } },
      animationDuration: 1000, animationEasing: "cubicOut" };
  },
  barSeries({ name, data, color, horizontal = false }) {
    return { name, type: "bar", data, itemStyle: { color, borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
      emphasis: { focus: "series" }, blur: { itemStyle: { opacity: 0.3 } }, animationDuration: 500, animationEasing: "cubicOut", animationDelay: i => i * 50 };
  },
};

// ---------- ユーティリティ ----------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = x => x !== null && x !== undefined && !Number.isNaN(x);
const pct = (x, d = 1, sign = true) => !isNum(x) ? "—" : `${sign && x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
const num = (x, d = 0) => !isNum(x) ? "—" : Number(x).toLocaleString("ja-JP", { maximumFractionDigits: d, minimumFractionDigits: d });
const yen = x => !isNum(x) ? "—" : `¥${num(x, x < 100 ? 1 : 0)}`;
const cap = x => { if (!x) return "—"; if (x >= 1e12) return `${(x / 1e12).toFixed(1)}兆円`; if (x >= 1e8) return `${Math.round(x / 1e8).toLocaleString()}億円`; return num(x); };
const pillPct = (x, d = 1) => !isNum(x) ? `<span class="pill gray">—</span>` : `<span class="pill ${x > 0.0005 ? "up" : x < -0.0005 ? "down" : "flat"}">${x > 0 ? "▲" : x < 0 ? "▼" : ""} ${(Math.abs(x) * 100).toFixed(d)}%</span>`;
const toDate = s => new Date(String(s).slice(0, 10) + "T00:00:00");
const isBiz = d => d.getDay() !== 0 && d.getDay() !== 6;
function addBusinessDays(dateStr, n) { const d = toDate(dateStr); let k = 0; while (k < n) { d.setDate(d.getDate() + 1); if (isBiz(d)) k++; } return d; }
function nextBusinessDay(d) { const x = new Date(d); do { x.setDate(x.getDate() + 1); } while (!isBiz(x)); return x; }
function bizDaysBetween(a, b) { let n = 0; const x = new Date(a); while (x < b) { x.setDate(x.getDate() + 1); if (isBiz(x)) n++; } return n; }
const fmtDate = d => `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
const fmtMD = d => `${d.getMonth() + 1}/${d.getDate()}`;
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayDate = () => { const t = new Date(); return new Date(t.getFullYear(), t.getMonth(), t.getDate()); };

const state = { data: null, charts: {}, expanded: new Set(), allOpen: false, leadlagFreq: "monthly", polling: null, excludeLowVol: false, search: "", isStatic: false };

// ---------- 候補の決定（値動きフィルタ対応）とピック ----------
function effectiveRows() { const rows = state.data.screen.rows; return state.excludeLowVol ? rows.filter(r => r.move_label !== "小") : rows; }
function candidateRows() { return effectiveRows().slice(0, state.data.screen.n_top); }
function candidateSet() { return new Set(candidateRows().map(r => r.symbol)); }
const isFresh = r => !r.rings.A.available || !isNum(r.rings.A.days_since) || r.rings.A.days_since <= 45;
const hasExp = r => r.expected && isNum(r.expected.score);
function rankedPool() {
  // 候補（合成スコア上位5%）に、期待値スコア上位20を加えたプールから、鮮度条件を満たすものを効率順に並べる
  const rows = effectiveRows();
  const topExp = rows.filter(hasExp).sort((a, b) => b.expected.score - a.expected.score).slice(0, 20);
  const pool = new Map(); candidateRows().forEach(r => pool.set(r.symbol, r)); topExp.forEach(r => pool.set(r.symbol, r));
  return Array.from(pool.values()).filter(r => isFresh(r) && hasExp(r))
    .sort((a, b) => (b.expected.score - a.expected.score) || (b.composite.score - a.composite.score));
}
function pickOne(cands) { const ranked = rankedPool(); return ranked.length ? ranked[0] : (cands.length ? cands[0] : null); }
function verdictOf(r) {
  const n = (r.composite.closed || []).length; const fresh = isFresh(r);
  const ex = r.expected; const posExp = ex && isNum(ex.drift_remaining) && ex.drift_remaining > 0.003;
  if (!posExp) return { cls: "gray", text: "見送り推奨（期待ドリフトが小さい）", level: 0 };
  if (n >= 2 && fresh) return { cls: "up", text: "ルール適合（買い候補）", level: 2 };
  if (n === 1 && fresh) return { cls: "flat", text: "補助的（1つ点灯・期待値はプラス）", level: 1 };
  return { cls: "gray", text: "見送り推奨", level: 0 };
}
const volPill = r => r.move_label ? `<span class="pill ${r.move_label === "大" ? "warn" : "gray"} vol-pill">値動き ${r.move_label}${isNum(r.vol60) ? ` ${Math.round(r.vol60 * 100)}%` : ""}</span>` : "";
const turnoverText = x => !isNum(x) ? "—" : x >= 1e8 ? `${(x / 1e8).toFixed(1)}億円/日` : `${Math.round(x / 1e6)}百万円/日`;

// ---------- リング SVG ----------
function ringSVG(ring, size, opts = {}) {
  const score = ring && ring.available ? Number(ring.score) : null;
  const r = size === "lg" ? 40 : 11.5; const sw = size === "lg" ? 8 : 4; const box = size === "lg" ? 96 : 28;
  const c = 2 * Math.PI * r; const cls = score === null ? "na" : (score >= (state.data?.screen?.close_threshold ?? 70) ? "on" : "off");
  const off = score === null ? c : c * (1 - Math.max(0, Math.min(100, score)) / 100);
  const title = opts.title ? `<title>${esc(opts.title)}</title>` : "";
  const label = size === "lg" ? (score === null ? `<text class="ring-num ring-na" x="50%" y="50%" text-anchor="middle" dominant-baseline="central">—</text>`
    : `<text class="ring-num" x="50%" y="50%" text-anchor="middle" dominant-baseline="central">${Math.round(score)}</text>`) : "";
  return `<svg class="ring ${cls}" width="${box}" height="${box}" viewBox="0 0 ${box} ${box}" role="img" aria-label="${esc(opts.title || "")}${score === null ? " データなし" : " " + Math.round(score) + "点"}">${title}
    <circle class="track" cx="${box / 2}" cy="${box / 2}" r="${r}" stroke-width="${sw}"/>
    <circle class="fill" cx="${box / 2}" cy="${box / 2}" r="${r}" stroke-width="${sw}" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"/>${label}</svg>`;
}
function animateRings(root) { requestAnimationFrame(() => $$(".ring .fill", root).forEach(el => { el.style.strokeDashoffset = el.dataset.off; })); }

// 行用スパークライン（直近 30 営業日）
function sparkSVG(points, color) {
  const vals = (points || []).slice(-30).map(p => p[1]).filter(isNum);
  if (vals.length < 3) return "";
  const w = 84, h = 30, pad = 2; const mn = Math.min(...vals), mx = Math.max(...vals); const rng = mx - mn || 1;
  const pts = vals.map((v, i) => `${(pad + i * (w - 2 * pad) / (vals.length - 1)).toFixed(1)},${(h - pad - (v - mn) / rng * (h - 2 * pad)).toFixed(1)}`);
  const last = pts[pts.length - 1].split(",");
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="2.2" fill="${color}"/></svg>`;
}
function monthChange(points) { const p = points || []; if (p.length < 22) return null; const a = p[p.length - 22][1], b = p[p.length - 1][1]; return isNum(a) && isNum(b) && a > 0 ? b / a - 1 : null; }

// ---------- 静的モード判定 ----------
async function detectStatic() {
  try { const r = await fetch("api/status", { cache: "no-store" }); if (!r.ok) throw new Error(); const j = await r.json(); return !("running" in j); }
  catch (e) { return true; }
}

// ---------- 読み込み ----------
async function load() {
  const res = await fetch("results.json", { cache: "no-store" });
  const data = await res.json();
  if (data.empty) { if (state.isStatic) { showBanner("info", "まだ計算結果が公開されていません。GitHub Actions の初回実行をお待ちください。"); return; } showBanner("info", "はじめての計算をしています。1〜2分ほどお待ちください。"); startRefresh(false); return; }
  state.data = data; render(data);
}
function showBanner(kind, html) { const b = $("#status-banner"); b.hidden = false; b.className = `notice notice-${kind}`; b.innerHTML = html; }
function hideBanner() { $("#status-banner").hidden = true; }

// ---------- 描画 ----------
function render(d) {
  hideBanner();
  disposeCharts();
  renderHeader(d); renderActions(d); renderPick(d); renderSummary(d); renderCandidates(d); renderAllTable(d); renderBacktest(d); renderGraph(d); renderDataQuality(d);
  if (d.data_quality.warnings?.some(w => w.includes("ベンチマーク") || w.includes("エラー"))) {
    showBanner("warn", "データに注意点があります。ページ下部の「データの状態」を確認してください。");
  }
}

function renderHeader(d) {
  $("#asof").textContent = `${d.as_of} の終値で計算 ・ 更新 ${d.generated_at}`;
  const gen = toDate(d.generated_at); const days = Math.round((todayDate() - gen) / 86400000);
  const pill = $("#fresh-pill"); pill.hidden = false;
  if (days <= 0) { pill.className = "pill up"; pill.textContent = "今日更新済み"; }
  else if (days <= 3) { pill.className = "pill gray"; pill.textContent = `${days}日前に更新`; }
  else { pill.className = "pill warn"; pill.textContent = `${days}日前に更新`; }
}

// ---------- 今やること（状態連動） ----------
function renderActions(d) {
  const cands = candidateRows(); const pick = pickOne(cands);
  const gen = toDate(d.generated_at); const today = todayDate(); const stale = Math.round((today - gen) / 86400000);
  const updatedToday = stale <= 0;
  const closed3 = cands.filter(r => r.composite.all_closed);
  const closed2 = cands.filter(r => (r.composite.closed || []).length === 2);
  const closed1 = cands.filter(r => (r.composite.closed || []).length === 1);
  const textMissing = cands.filter(r => !r.rings.B.available);
  const fresh = r => !r.rings.A.available || !isNum(r.rings.A.days_since) || r.rings.A.days_since <= 45;
  const entry = cands.filter(r => (r.composite.closed || []).length >= 2 && fresh(r));
  const consider = cands.filter(r => (r.composite.closed || []).length === 1 && fresh(r));
  const review = addBusinessDays(iso(today), d.params.horizon_days);
  const nextE = cands.filter(r => r.next_earnings).map(r => ({ r, dt: toDate(r.next_earnings) })).sort((a, b) => a.dt - b.dt);
  const names = arr => arr.map(r => r.name).join("、");
  const steps = [
    { title: state.isStatic ? "データは自動で更新される" : "データを更新する", when: state.isStatic ? "平日の朝 7:30 ごろ（GitHub Actions）" : "毎営業日の朝、または週に1回", done: updatedToday,
      body: state.isStatic
        ? `最終更新は <b>${d.generated_at}</b>（${d.as_of} の終値）。${updatedToday ? "今日の分は反映済みです。" : stale <= 3 ? "週末・祝日をはさむと数日前の日付になります。" : "更新が止まっている可能性があります。GitHub Actions の実行結果を確認してください。"}`
        : updatedToday ? `今日 <b>${d.generated_at.slice(11)}</b> に更新済み（${d.as_of} の終値）。株価は取引所の終値ベースなので、朝に1回で十分です。`
        : `最終更新は <b>${d.generated_at}</b>（${stale}日前）。右上の丸いボタンか「更新する」を押してください。約1分かかります。`,
      side: state.isStatic ? `<span class="pill ${stale <= 3 ? "up" : "warn"}">${stale <= 3 ? "自動更新" : "要確認"}</span>` : updatedToday ? `<span class="pill up">完了</span>` : `<button class="btn btn-primary btn-sm" data-act="refresh" type="button">いま更新する</button>` },
    { title: "候補を確認する", when: "更新のあと",
      body: `候補は <b>${cands.length} 件</b>（3つ点灯 ${closed3.length}、2つ点灯 ${closed2.length}、1つ点灯 ${closed1.length}）。${closed3.length ? `<b>${names(closed3)}</b> は3つすべて点灯しています。` : "今日は3つ点灯の銘柄はありません。"}`,
      side: `<button class="btn btn-sm" data-act="go-candidates" type="button">候補へ</button>` },
    { title: "決算説明のテキストを登録する", when: "決算発表の直後（5月・8月・11月・2月）",
      body: textMissing.length ? `候補のうち <b>${textMissing.length}/${cands.length} 件</b>が未登録（${names(textMissing.slice(0, 4))}${textMissing.length > 4 ? " ほか" : ""}）。${state.isStatic ? "GitHub のリポジトリの <b>data/transcripts/&lt;コード&gt;.T/&lt;発表日&gt;.txt</b> にテキストを置いて push すると、次回の自動更新で「経営陣の自信」が計算されます。" : "決算説明会の質疑応答や決算短信の説明文を貼ると「経営陣の自信」が計算され、3つ点灯の判定ができるようになります。"}`
        : `候補 ${cands.length} 件すべてにテキストが登録されています。次の決算後にまた登録してください。`,
      side: `<button class="btn btn-sm" data-act="go-text" type="button">登録する</button>` },
    { title: "入るかどうか決める", when: "候補を確認した日（買うのは翌営業日の寄り付き）",
      body: entry.length ? `ルールの条件（2つ以上点灯・決算から45営業日以内）を満たすのは <b>${names(entry)}</b>。入るなら <b>${fmtDate(nextBusinessDay(today))}</b> の寄り付きが目安。${pick ? `ひとつ選ぶなら <b>${esc(pick.name)}</b>（下のカード）。` : ""}損切り幅を先に決めてください。`
        : `今日は「2つ以上点灯」の銘柄が<b>ありません</b>。ルール上は<b>見送り</b>が目安です。${pick ? `それでも1つ選ぶなら <b>${esc(pick.name)}</b>（下のカードに理由と損切り目安）。` : ""}${consider.length ? `1つ点灯の <b>${names(consider.slice(0, 5))}</b> は補助的な候補です。` : ""}`,
      side: `<span class="pill ${entry.length ? "up" : "gray"}">${entry.length ? `条件クリア ${entry.length}件` : "見送り目安"}</span>` },
    { title: "保有中に見直す", when: `60営業日後（今日入るなら ${fmtDate(review)}）、または次の決算の前日`,
      body: nextE.length ? `候補の次の決算: ${nextE.slice(0, 5).map(x => `<b>${x.r.name}</b> ${fmtMD(x.dt)}`).join("、")}${nextE.length > 5 ? " ほか" : ""}。保有中に決算が来る銘柄は、<b>発表の前日まで</b>に続けるか決めてください。更新後にスコアが50を下回ったときも見直しの合図です。`
        : `更新のたびにスコアを確認し、50を下回ったら見直してください。`,
      side: `<span class="pill flat">${fmtMD(review)} 見直し</span>` },
  ];
  let nowIdx = updatedToday ? 1 : 0;
  $("#steps").innerHTML = steps.map((s, i) => `<li class="step ${s.done ? "done" : ""} ${i === nowIdx ? "now" : ""}">
    <span class="step-n">${s.done ? "✓" : i + 1}</span>
    <div><div class="step-title">${esc(s.title)}<span class="step-when">${esc(s.when)}</span></div><div class="step-body">${s.body}</div></div>
    <div class="step-side">${s.side}</div></li>`).join("");
  $("#actions-sub").textContent = `${updatedToday ? "今日更新済み" : `${stale}日前のデータ`} ・ 候補 ${cands.length} 件 ・ テキスト未登録 ${textMissing.length} 件`;
  const up = $("#upcoming");
  const soon = nextE.filter(x => (x.dt - today) / 86400000 <= 45);
  if (soon.length) { up.hidden = false; up.innerHTML = `<b>45日以内に決算がある候補:</b> ${soon.map(x => `${esc(x.r.name)}（${fmtMD(x.dt)}）`).join("、")}。決算またぎは値動きが荒くなるので、前日までに続けるか決めるのがルールです。`; }
  else up.hidden = true;
  $$("[data-act]").forEach(b => b.onclick = () => quickAction(b.dataset.act));
}

function quickAction(act) {
  const scrollTo = sel => { const el = $(sel); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 76, behavior: "smooth" }); };
  if (act === "refresh") { if (state.isStatic) { showBanner("info", "この公開版は GitHub Actions が平日の朝に自動で更新します。手動更新はローカル版（run.sh）で行えます。"); window.scrollTo({ top: 0, behavior: "smooth" }); return; } startRefresh(false); }
  else if (act === "go-candidates") scrollTo("#sec-candidates");
  else if (act === "go-evidence") scrollTo("#sec-backtest");
  else if (act === "go-text") {
    const target = state.data.screen.rows.find(r => r.candidate && !r.rings.B.available) || state.data.screen.rows.find(r => r.candidate);
    if (!target) return;
    if ($(`.crow[data-symbol="${target.symbol}"]`).getAttribute("aria-expanded") !== "true") toggleRow(target.symbol);
    setTimeout(() => { const f = $(`#form-${target.code}`); if (f) { window.scrollTo({ top: f.getBoundingClientRect().top + window.scrollY - 76, behavior: "smooth" }); $(`#text-${target.code}`)?.focus({ preventScroll: true }); } }, 80);
  }
}

// ---------- ひとつ買うなら ----------
function renderPick(d) {
  const el = $("#pick"); const cands = candidateRows(); const r = pickOne(cands);
  if (!r) { el.innerHTML = `<p class="pick-empty">候補がありません。データを更新してください。</p>`; return; }
  const labels = d.screen.ring_labels; const v = verdictOf(r); const today = todayDate();
  const entry = nextBusinessDay(today); const review = addBusinessDays(iso(today), d.params.horizon_days);
  const nextE = r.next_earnings ? toDate(r.next_earnings) : null; const inside = nextE && nextE <= review;
  const chg = monthChange(r.sparkline);
  const stop = isNum(r.stop_pct) ? r.stop_pct : 0.08; const stopPrice = isNum(r.price) ? r.price * (1 - stop) : null;
  const ex = r.expected; const lab = { 1: "弱い", 2: "ふつう", 3: "強い" };
  const reasons = [];
  if (ex) reasons.push(`過去データで同じ条件（${esc(ex.basis)}）だった ${ex.n.toLocaleString()} 件の決算は、60営業日で平均 ${pct(ex.drift_base)}${isNum(ex.t) ? `（t値 ${ex.t}）` : ""}、勝率 ${Math.round(ex.hit_rate * 100)}% でした。${ex.text_note ? ex.text_note + "。" : ""}決算から ${Math.max(0, 60 - ex.remaining_days)} 営業日たっているので、残りの期待ドリフトは ${pct(ex.drift_remaining)} と見積もります。`);
  reasons.push(...r.why.slice(0, 2));
  reasons.push(`値動きの大きさは「${r.move_label || "—"}」（年率ボラ ${isNum(r.vol60) ? Math.round(r.vol60 * 100) + "%" : "—"}、60営業日で ±${ex && isNum(ex.sigma_h) ? Math.round(ex.sigma_h * 100) : "—"}%）、売買代金 ${turnoverText(r.turnover)}。${r.move_label === "大" ? "上下ともに動きやすいので、損切り幅を必ず決めてください。" : r.move_label === "小" ? "値動きが小さいので、期待できる幅も控えめです。" : ""}`);
  const cautions = r.risks.slice(0, 2);
  if (inside) cautions.push(`保有中の ${fmtMD(nextE)} に決算があります。発表前日までに続けるか決めてください。`);
  if (ex && ex.n < 60) cautions.push(`同条件の過去データが ${ex.n} 件と少なく、期待値の信頼度は低めです。`);
  if (ex && ex.drift_remaining < 0.005) cautions.push(`残りの期待ドリフトが ${pct(ex.drift_remaining)} と小さく、手数料や値動きに埋もれる可能性があります。`);
  const ranked = rankedPool(); const alt = ranked.slice(0, 5);
  const cmpRows = alt.map((x, i) => { const e = x.expected; const c = (x.composite.closed || []).length; const ne2 = x.next_earnings ? toDate(x.next_earnings) : null;
    return `<tr class="${x.symbol === r.symbol ? "is-candidate" : ""}"><td class="num muted">${i + 1}</td><td><strong>${esc(x.name)}</strong> <span class="small muted">${esc(x.code)}</span></td><td class="r num">${pct(e.score, 2)}</td><td class="r num">${pct(e.drift_base, 2)}</td><td class="r small">${x.move_label || "—"} ±${Math.round((e.sigma_h || 0) * 100)}%</td><td class="r num">${Math.round((e.hit_rate || 0) * 100)}%<span class="muted small"> n=${e.n}</span></td><td class="r">${c}つ</td><td class="r small">${ne2 ? fmtMD(ne2) : "—"}</td></tr>`; }).join("");
  el.innerHTML = `
    <div class="pick-top">
      <div>
        <div class="pick-name">${esc(r.name)}</div>
        <div class="pick-meta">${esc(r.code)}${r.sector && r.sector !== "—" ? ` · ${esc(r.sector)}` : ""} · 時価総額 ${cap(r.market_cap)} · 合成スコア ${r.composite.score.toFixed(0)}（候補 ${cands.length} 件中 ${cands.indexOf(r) + 1} 位）</div>
        <div class="pick-price"><span class="num">${yen(r.price)}</span>${pillPct(chg)}<span class="small muted">1か月</span></div>
        <div class="pick-verdict"><span class="pill ${v.cls}">${v.text}</span>${volPill(r)}${r.composite.all_closed ? `<span class="pill up">3つ点灯</span>` : (r.composite.closed || []).length ? `<span class="pill flat">${(r.composite.closed || []).length}つ点灯</span>` : ""}</div>
      </div>
      <div class="pick-rings">${["A", "B", "C"].map(k => `<div class="pick-ring">${ringSVG(r.rings[k], "lg", { title: labels[k] })}<span>${esc(labels[k])}</span></div>`).join("")}</div>
    </div>
    <div class="pick-grid">
      <div>
        <div class="sub-title">選んだ理由</div><ul class="bullets">${reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
        ${cautions.length ? `<div class="sub-title" style="margin-top:12px">買わない理由があるとすれば</div><ul class="bullets">${cautions.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="pick-plan">
        <div class="row-kv"><b>入るなら</b><span>${fmtDate(entry)} の寄り付き</span></div>
        <div class="row-kv"><b>損切り目安</b><span>買値から −${Math.round(stop * 100)}%（${stopPrice ? yen(stopPrice) : "—"} 付近）。直近2週間の通常の値動きの2倍強</span></div>
        <div class="row-kv"><b>見直し日</b><span>${fmtDate(review)}（60営業日後）</span></div>
        <div class="row-kv"><b>次の決算</b><span>${nextE ? `${fmtDate(nextE)}${inside ? " ・ 保有中に到来" : ""}` : "未定"}</span></div>
      </div>
    </div>
    ${ex ? `<div class="pick-axis"><div class="axis-item"><div class="k">期待ドリフト（残り${ex.remaining_days}営業日分）</div><div class="v ${ex.drift_remaining >= 0 ? "up" : "down"}">${pct(ex.drift_remaining)}</div></div><div class="axis-item"><div class="k">想定変動（60営業日）</div><div class="v">±${Math.round((ex.sigma_h || 0) * 100)}%</div></div><div class="axis-item"><div class="k">選定スコア（残り期待${ex.penalties.length ? "×減点" : ""}）</div><div class="v ${ex.score >= 0 ? "up" : "down"}">${pct(ex.score, 2)}</div></div><div class="axis-item"><div class="k">根拠</div><div class="v">${ex.n.toLocaleString()}件 · 勝率 ${Math.round((ex.hit_rate || 0) * 100)}%</div></div></div>${ex.penalties.length ? `<p class="caption">減点: ${esc(ex.penalties.join("、"))}（選定スコアに ${ex.penalties.map(x => x === "保有中に決算" ? "×0.85" : "×0.9").join("・")}）</p>` : ""}` : `<p class="caption">この銘柄は決算サプライズのデータが無いため、期待値は計算していません。</p>`}
    ${cmpRows ? `<div class="sub-title" style="margin-top:14px">次点との比較（選定スコア順）</div><div class="table-wrap"><table><thead><tr><th>#</th><th>銘柄</th><th class="r">選定スコア</th><th class="r">同条件の平均</th><th class="r">値動き</th><th class="r">勝率</th><th class="r">点灯</th><th class="r">次の決算</th></tr></thead><tbody>${cmpRows}</tbody></table></div><p class="caption">選定スコア = 同条件（サプライズ十分位 × 値動きの大きさ）の過去平均ドリフトを残り期間分に按分し、決算またぎ・薄商いで減点したもの。研究どおり、ドリフトは値動きの大きい銘柄に集中しています。過去平均に基づく目安であり、個別銘柄の予測ではありません。</p>` : ""}
    <div class="pick-actions"><button class="btn btn-primary btn-sm" type="button" id="pick-open">この銘柄の詳細を開く</button><span class="small muted">${v.level === 2 ? "ルールに合う銘柄です。それでも最終判断はご自身で。" : v.level === 1 ? "点灯が1つだけなので、研究上の裏づけは弱めです。少額か見送りが無難です。" : "ルール上は見送りです。参考としてのみ表示しています。"}</span></div>`;
  animateRings(el);
  $("#pick-open").onclick = () => { const btn = $(`.crow[data-symbol="${r.symbol}"]`); if (btn && btn.getAttribute("aria-expanded") !== "true") toggleRow(r.symbol); setTimeout(() => window.scrollTo({ top: $(`#item-${r.code}`).getBoundingClientRect().top + window.scrollY - 76, behavior: "smooth" }), 60); };
}

// ---------- サマリー ----------
function renderSummary(d) {
  const s = d.screen, p = d.pead, best = p.best_condition;
  const cands = candidateRows();
  const c3 = cands.filter(r => r.composite.all_closed).length, c2 = cands.filter(r => (r.composite.closed || []).length === 2).length;
  $("#summary-sub").textContent = `${d.as_of} 終値 ・ 審査 ${s.n_screened.toLocaleString()} 銘柄の上位5%${state.excludeLowVol ? "（値動き小を除外）" : ""}`;
  $("#hero").innerHTML = `
    <div class="hero-main"><div class="hero-label">きょうの候補</div><div class="hero-num">${cands.length}<span class="unit">銘柄</span></div>
      <div class="hero-foot"><span class="pill ${c3 ? "up" : "gray"}">3つ点灯 ${c3}</span> <span class="pill ${c2 ? "flat" : "gray"}">2つ点灯 ${c2}</span> <span class="pill gray">1つ点灯 ${cands.length - c3 - c2}</span></div></div>
    <div class="stat-card"><div class="hero-label">最良条件の過去平均ドリフト</div><div class="hero-num">${best ? pct(best.mean_car) : "—"}</div><div class="hero-foot">${best ? `60営業日、n=${best.n.toLocaleString()}` : "データ不足"} ${best ? pillPct(best.mean_car) : ""}</div></div>
    <div class="stat-card"><div class="hero-label">同条件で上がっていた割合</div><div class="hero-num">${best ? Math.round(best.hit_rate * 100) : "—"}<span class="unit">%</span></div><div class="hero-foot">60営業日後にプラスだった決算の割合</div></div>`;
  $("#summary-caption").textContent = `「ドリフト」は決算発表の2営業日後から60営業日間の、市場平均（${d.params.benchmark === "1306.T" ? "TOPIX" : "日経平均"}）を引いた値動きです。上の数字は過去データの平均で、個別の銘柄がそうなる保証ではありません。`;
}

function ringStatusText(k, ring) {
  if (!ring.available) return ring.reason || "データなし";
  const th = state.data.screen.close_threshold;
  return ring.score >= th ? "点灯（強い）" : ring.score >= 55 ? "やや強い" : ring.score > 45 ? "中立" : "弱い";
}

// ---------- 候補リスト ----------
function renderCandidates(d) {
  const ul = $("#candidates");
  const tg = $("#toggle-lowvol"); tg.checked = state.excludeLowVol;
  tg.onchange = () => { state.excludeLowVol = tg.checked; state.expanded.clear(); renderActions(d); renderPick(d); renderSummary(d); renderCandidates(d); renderAllTable(d); };
  const rows = candidateRows();
  if (!rows.length) { ul.innerHTML = `<li class="empty-state">候補がありません。データを更新してください。</li>`; return; }
  ul.innerHTML = rows.map(r => rowHTML(r)).join("");
  animateRings(ul);
  $("#cand-note").textContent = `合成スコアは0〜100。70以上のシグナルを「点灯」と呼びます。折れ線は直近30営業日の株価、バッジは1か月の騰落率と値動きの大きさ（年率ボラ）。審査対象 ${d.screen.n_screened.toLocaleString()} 銘柄。`;
  $$(".crow", ul).forEach(btn => btn.addEventListener("click", () => toggleRow(btn.dataset.symbol)));
}

function rowHTML(r) {
  const labels = state.data.screen.ring_labels; const E = EvilCharts;
  const closed = r.composite.closed || [];
  const badge = r.composite.all_closed ? `<span class="badge badge-up">3つ点灯</span>` : closed.length ? `<span class="badge ${closed.length === 2 ? "" : "badge-gray"}">${closed.length}つ点灯</span>` : "";
  const chg = monthChange(r.sparkline);
  return `<li class="crow-item" id="item-${r.code}">
    <button class="crow" type="button" data-symbol="${esc(r.symbol)}" aria-expanded="false" aria-controls="detail-${r.code}">
      <span class="crow-rank">${r.rank}</span>
      <span><span class="crow-name">${esc(r.name)}</span>${badge}<div class="crow-meta">${esc(r.code)}${r.sector && r.sector !== "—" ? ` · ${esc(r.sector)}` : ""}${r.next_earnings ? ` · 次の決算 ${fmtMD(toDate(r.next_earnings))}` : ""}${volPill(r)}</div>${r.why.length ? `<div class="crow-why">${esc(r.why[0].split("。")[0])}。</div>` : ""}</span>
      <span class="crow-spark">${sparkSVG(r.sparkline, isNum(chg) && chg < 0 ? E.DOWN : E.UP)}</span>
      <span class="crow-price"><span class="num">${yen(r.price)}</span>${pillPct(chg)}</span>
      <span class="crow-rings" aria-hidden="true">${["A", "B", "C"].map(k => ringSVG(r.rings[k], "sm", { title: labels[k] })).join("")}</span>
      <span class="crow-score"><b>${r.composite.score.toFixed(0)}</b><small>スコア</small></span>
      <svg class="chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 8l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <div class="detail" id="detail-${r.code}" hidden></div>
  </li>`;
}

function toggleRow(symbol) {
  const r = state.data.screen.rows.find(x => x.symbol === symbol);
  const btn = $(`.crow[data-symbol="${symbol}"]`); const panel = $(`#detail-${r.code}`);
  if (!btn || !panel) return;
  const open = btn.getAttribute("aria-expanded") === "true";
  if (open) { btn.setAttribute("aria-expanded", "false"); panel.hidden = true; state.expanded.delete(symbol); return; }
  btn.setAttribute("aria-expanded", "true"); panel.hidden = false; state.expanded.add(symbol);
  if (!panel.dataset.built) { panel.innerHTML = detailHTML(r); panel.dataset.built = "1"; animateRings(panel); buildDetailCharts(r, panel); wireForm(r, panel); }
}

function detailHTML(r) {
  const d = state.data, labels = d.screen.ring_labels, A = r.rings.A, B = r.rings.B, C = r.rings.C;
  const today = todayDate(); const entry = nextBusinessDay(today); const review = addBusinessDays(iso(today), d.params.horizon_days);
  const nextE = r.next_earnings ? toDate(r.next_earnings) : null;
  const earningsInside = nextE && nextE <= review;
  const closed = (r.composite.closed || []).length; const freshOK = !A.available || !isNum(A.days_since) || A.days_since <= 45;
  const verdict = closed >= 2 && freshOK ? `<span class="pill up">ルールの条件をクリア</span>` : closed === 1 && freshOK ? `<span class="pill flat">補助的な候補（1つ点灯）</span>` : `<span class="pill gray">見送り目安</span>`;
  const notes = {
    A: A.available ? `${A.event_date} 発表。予想 EPS ${num(A.eps_estimate, 1)} → 実績 ${num(A.eps_actual, 1)}（${isNum(A.surprise_pct) ? (A.surprise_pct > 0 ? "+" : "") + A.surprise_pct.toFixed(1) + "%" : "—"}）。同時期の銘柄中の順位 ${Math.round(A.sue_rank * 100)}/100。${isNum(A.days_since) ? `発表から ${A.days_since} 営業日。` : ""}${A.reason ? " " + A.reason : ""}` : (A.reason || ""),
    B: B.available ? `${B.date} のテキスト（${esc(B.kind)}）。${B.summary || ""}` : "下のフォームから決算説明のテキストを貼り付けると計算されます。",
    C: C.available ? `取引先の市場調整リターン ${pct(C.cm21)}（21日）/ ${pct(C.cm63)}（63日）。この銘柄は ${pct(C.own21)} / ${pct(C.own63)}。出遅れ度 z=${C.gap_z > 0 ? "+" : ""}${C.gap_z}。` : (C.reason || ""),
  };
  return `
    <div class="plan-steps">
      <div class="plan-step"><div class="k">① 入るなら</div><div class="d">${fmtDate(entry)} 寄り付き</div><div class="t">判定: ${verdict}${A.available && isNum(A.days_since) ? `<br>決算から ${A.days_since} 営業日${A.days_since > 45 ? "（45日超のため見送り目安）" : ""}` : ""}</div></div>
      <div class="plan-step"><div class="k">② 見直し日（60営業日後）</div><div class="d">${fmtDate(review)}</div><div class="t">この日までに利確・撤退を判断。更新後にスコアが50未満なら前倒し。</div></div>
      <div class="plan-step"><div class="k">③ 次の決算</div><div class="d">${nextE ? fmtDate(nextE) : "未定（Yahoo未収録）"}</div><div class="t">${nextE ? (earningsInside ? `<span class="pill warn">保有中に決算</span> 発表前日までに続けるか決める` : "見直し日より後なので、保有期間中は決算をまたぎません") : "決算日が分かったら前日までに判断"}</div></div>
    </div>
    <div class="rings-large">
      ${["A", "B", "C"].map(k => `<div class="ring-card">${ringSVG(r.rings[k], "lg", { title: labels[k] })}<div><div class="ring-title">${esc(labels[k])}</div><div class="ring-status">${esc(ringStatusText(k, r.rings[k]))}</div><div class="ring-note">${notes[k]}</div></div></div>`).join("")}
    </div>
    <div class="two-col">
      <div><div class="sub-title">なぜ候補になったか</div>${r.why.length ? `<ul class="bullets">${r.why.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : `<p class="small muted">強いシグナルはありません。合成スコアの高さは相対的なものです。</p>`}</div>
      <div><div class="sub-title">気をつけること</div>${r.risks.length ? `<ul class="bullets">${r.risks.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : `<p class="small muted">特筆すべき注意点はありません。</p>`}</div>
    </div>
    <div class="chart-block"><div class="sub-title">株価（直近90営業日） <span class="muted small">時価総額 ${cap(r.market_cap)}</span></div><div class="chart" id="spark-${r.code}"></div>
      <p class="caption">縦の破線は直近の決算発表日。発表後の値動きが、下の「ドリフトの進み具合」に対応します。</p></div>
    ${A.available && A.car_path ? `<div class="chart-block"><div class="sub-title">ドリフトの進み具合（発表2営業日後からの市場調整リターン）</div>
      <div class="legend" id="legend-drift-${r.code}"></div><div class="chart" id="drift-${r.code}"></div>
      <p class="caption">濃い線がこの銘柄の今回。灰色の破線は、過去に同じくらいのサプライズ度（十分位 ${A.sue_decile ?? "—"}）だった決算の平均的な経路です。</p></div>` : ""}
    ${C.available ? `<div class="chart-block"><div class="sub-title">取引先の値動きと、この銘柄の値動き（市場平均を引いた値）</div>
      <div class="legend" id="legend-cust-${r.code}"></div><div class="chart" id="cust-${r.code}" style="height:${Math.max(120, 36 * (C.customers.length + 1) + 40)}px"></div>
      <p class="caption">取引先が先に上がり、この銘柄がまだ追いついていないほど「取引先の追い風」の点数が高くなります。カッコ内は売上依存度の目安。</p></div>` : ""}
    ${state.isStatic ? `<div class="form" id="form-${r.code}"><div class="sub-title">決算説明のテキストを登録する（任意）</div><p class="help">公開版ではブラウザから保存できません。リポジトリの <code>data/transcripts/${esc(r.symbol)}/${A.available ? A.event_date : d.as_of}.txt</code> にテキストを置いて push すると、次回の自動更新で反映されます。</p></div>` : `<div class="form" id="form-${r.code}">
      <div class="sub-title">決算説明のテキストを登録する（任意）</div>
      <p class="help">決算説明会の質疑応答、決算短信の「経営成績に関する説明」などを貼り付けると「経営陣の自信」が計算されます。保存後に自動で再計算します（約1分）。</p>
      <div class="form-row">
        <div><label for="date-${r.code}">対応する決算発表日</label><input type="date" id="date-${r.code}" value="${A.available ? A.event_date : d.as_of}"></div>
        <div class="small muted">日付は発表日から45日以内なら自動で紐づきます。</div>
      </div>
      <label for="text-${r.code}">テキスト</label>
      <textarea id="text-${r.code}" placeholder="ここに貼り付け（40文字以上）"></textarea>
      <div class="form-actions"><button class="btn btn-primary btn-sm" type="button" data-form="save">登録して再計算</button><button class="btn btn-sm" type="button" data-form="try">保存せずに採点だけ試す</button><span class="form-msg" id="msg-${r.code}"></span></div>
    </div>`}`;
}

function buildDetailCharts(r, panel) {
  const E = EvilCharts, A = r.rings.A, C = r.rings.C;
  const sp = r.sparkline || [];
  if (sp.length) {
    const el = $(`#spark-${r.code}`, panel); const ch = echarts.init(el); state.charts[`spark-${r.code}`] = ch;
    const dates = sp.map(x => x[0]); const vals = sp.map(x => x[1]);
    const up = vals[vals.length - 1] >= vals[0];
    const series = E.lineSeries({ name: "終値", data: vals, color: up ? E.UP : E.DOWN, area: true, width: 1.4 });
    if (A.available && A.day0 && dates.includes(A.day0)) {
      series.markLine = { symbol: "none", silent: true, lineStyle: { color: E.GRAY, type: [3, 3], width: 0.8 }, label: { show: true, formatter: "決算", color: E.GRAY, fontSize: 10, position: "insideEndTop", rotate: 0 }, data: [{ xAxis: A.day0 }] };
    }
    ch.setOption({ grid: E.grid(), tooltip: E.tooltip(v => yen(v)), xAxis: E.xAxis(dates, { formatter: v => v.slice(5), interval: Math.floor(dates.length / 5) }), yAxis: E.yAxis({ scale: true, formatter: v => num(v) }), series: [series] });
  }
  if (A.available && A.car_path) {
    const el = $(`#drift-${r.code}`, panel); const ch = echarts.init(el); state.charts[`drift-${r.code}`] = ch;
    const H = state.data.params.horizon_days; const x = Array.from({ length: H }, (_, i) => i + 1);
    const mine = x.map((_, i) => i < A.car_path.length ? A.car_path[i] : null);
    const dec = String(A.sue_decile ?? ""); const path = state.data.pead.decile_paths?.[dec]?.mean; const top = state.data.pead.decile_paths?.["10"]?.mean;
    const series = [E.lineSeries({ name: "この銘柄（今回）", data: mine, color: E.NAVY, width: 1.6, z: 3 })];
    if (path) series.push(E.lineSeries({ name: `同じサプライズ度の過去平均（十分位${dec}）`, data: path, color: E.GRAY, dashed: true }));
    if (top && dec !== "10") series.push(E.lineSeries({ name: "サプライズ最上位の過去平均", data: top, color: E.UP }));
    E.legend($(`#legend-drift-${r.code}`, panel), series.map(s => [s.name, s.itemStyle.color, s.lineStyle.type !== "solid"]));
    ch.setOption({ grid: E.grid(), tooltip: E.tooltip(v => pct(v, 2), { label: p => `${p.axisValue} 営業日目` }), xAxis: E.xAxis(x, { formatter: v => `${v}日`, interval: 9 }), yAxis: E.yAxis({ formatter: v => pct(v, 0, false) }), series });
  }
  if (C.available) {
    const el = $(`#cust-${r.code}`, panel); const ch = echarts.init(el); state.charts[`cust-${r.code}`] = ch;
    const cats = [...C.customers.map(c => `${c.name}（${Math.round(c.weight * 100)}%）`), `${r.name}（この銘柄）`];
    const r21 = [...C.customers.map(c => c.ret21), C.own21]; const r63 = [...C.customers.map(c => c.ret63), C.own63];
    const colorize = arr => arr.map(v => ({ value: v, itemStyle: { color: isNum(v) && v < 0 ? E.DOWN : E.UP, borderRadius: [0, 4, 4, 0] } }));
    const s21 = { ...E.barSeries({ name: "直近21営業日", data: r21, color: E.UP, horizontal: true }), data: colorize(r21) };
    const s63 = { ...E.barSeries({ name: "直近63営業日", data: r63, color: E.GRAY_LIGHT, horizontal: true }), data: r63.map(v => ({ value: v, itemStyle: { color: E.GRAY_LIGHT, borderRadius: [0, 4, 4, 0] } })) };
    E.legend($(`#legend-cust-${r.code}`, panel), [["直近21営業日（緑=上昇 / 赤=下落）", E.UP], ["直近63営業日", E.GRAY_LIGHT]]);
    ch.setOption({ grid: { left: 8, right: 16, top: 8, bottom: 8 }, tooltip: E.tooltip(v => pct(v, 1)),
      xAxis: { ...E.yAxis({ formatter: v => pct(v, 0, false) }), type: "value" },
      yAxis: { type: "category", data: cats, inverse: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: E.TOKENS.foreground, fontSize: 11 } },
      series: [s21, s63] });
  }
}

function wireForm(r, panel) {
  const msg = $(`#msg-${r.code}`, panel);
  $$("button[data-form]", panel).forEach(btn => btn.addEventListener("click", async () => {
    const text = $(`#text-${r.code}`, panel).value.trim(); const date = $(`#date-${r.code}`, panel).value;
    if (text.length < 40) { msg.className = "form-msg err"; msg.textContent = "40文字以上のテキストを貼り付けてください。"; return; }
    btn.disabled = true; msg.className = "form-msg"; msg.textContent = "処理中…";
    try {
      if (btn.dataset.form === "try") {
        const res = await fetch("api/score-text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
        const s = await res.json();
        msg.className = "form-msg ok"; msg.textContent = `お試し採点: 自信スコア ${s.confidence_score}/100（トーン ${s.features.tone.toFixed(2)}、確信度 ${s.features.certainty.toFixed(2)}）。保存はしていません。`;
      } else {
        const res = await fetch("api/transcripts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: r.symbol, date, text }) });
        const s = await res.json();
        if (!res.ok) throw new Error(s.detail || "保存に失敗しました");
        msg.className = "form-msg ok"; msg.textContent = `保存しました（自信スコア ${s.score.confidence_score}/100）。再計算を開始します。`;
        if (s.refresh_started) beginPolling();
      }
    } catch (e) { msg.className = "form-msg err"; msg.textContent = `エラー: ${e.message}`; }
    finally { btn.disabled = false; }
  }));
}

function renderAllTable(d) {
  const wrap = $("#all-table"); const body = $("#all-table-body"); const labels = d.screen.ring_labels;
  const cset = candidateSet();
  const draw = () => {
    const q = state.search.trim().toLowerCase();
    const rows = effectiveRows().filter(r => !q || r.name.toLowerCase().includes(q) || r.code.includes(q) || (r.sector || "").toLowerCase().includes(q));
    const shown = rows.slice(0, 400);
    body.innerHTML = `<div class="table-wrap"><table><thead><tr><th>#</th><th>銘柄</th><th class="r">${esc(labels.A)}</th><th class="r">${esc(labels.B)}</th><th class="r">${esc(labels.C)}</th><th class="r">合成</th><th>点灯</th><th class="r">値動き</th><th class="r">時価総額</th></tr></thead><tbody>
      ${shown.map(r => `<tr class="${cset.has(r.symbol) ? "is-candidate" : ""}"><td class="num muted">${r.rank}</td><td><strong>${esc(r.name)}</strong> <span class="small muted">${esc(r.code)}</span></td>
        ${["A", "B", "C"].map(k => `<td class="r num">${r.rings[k].available ? r.rings[k].score.toFixed(0) : "—"}</td>`).join("")}
        <td class="r num"><strong>${r.composite.score.toFixed(0)}</strong></td><td class="small muted">${(r.composite.closed || []).map(k => labels[k]).join("・") || "—"}</td>
        <td class="r small">${r.move_label || "—"}${isNum(r.vol60) ? ` <span class="muted">${Math.round(r.vol60 * 100)}%</span>` : ""}</td><td class="r small">${cap(r.market_cap)}</td></tr>`).join("")}
    </tbody></table></div><p class="caption">${rows.length.toLocaleString()} 銘柄中 ${shown.length.toLocaleString()} 件を表示${rows.length > shown.length ? "（検索で絞り込めます）" : ""}。「—」はその信号を計算できるデータが無いことを示します（中立の50点として扱い、信号が少ない銘柄ほど合成スコアを50に近づけています）。</p>`;
  };
  draw();
  const inp = $("#all-search"); inp.value = state.search; inp.oninput = () => { state.search = inp.value; draw(); };
  const btn = $("#btn-all");
  btn.onclick = () => { state.allOpen = !state.allOpen; wrap.hidden = !state.allOpen; btn.setAttribute("aria-expanded", String(state.allOpen)); btn.textContent = state.allOpen ? "ランキングを閉じる" : "全銘柄のランキングを見る"; };
  wrap.hidden = !state.allOpen;
}

// ---------- 検証 ----------
function renderBacktest(d) {
  const E = EvilCharts, p = d.pead, H = d.params.horizon_days;
  const el = $("#chart-decile"); const ch = echarts.init(el); state.charts.decile = ch;
  const x = Array.from({ length: H }, (_, i) => i + 1);
  const series = [];
  for (let k = 1; k <= 10; k++) {
    const g = p.decile_paths?.[String(k)]; if (!g) continue;
    const color = k === 10 ? E.UP : k === 1 ? E.DOWN : E.GRAY_LIGHT;
    series.push(E.lineSeries({ name: k === 10 ? "サプライズ最上位10%" : k === 1 ? "サプライズ最下位10%" : `十分位${k}`, data: g.mean, color, width: (k === 1 || k === 10) ? 1.6 : 0.9, dashed: k === 1, z: (k === 1 || k === 10) ? 3 : 1 }));
  }
  E.legend($("#legend-decile"), [["サプライズ最上位10%", E.UP], ["サプライズ最下位10%", E.DOWN, true], ["その他の十分位", E.GRAY_LIGHT]]);
  ch.setOption({ grid: E.grid(), tooltip: E.tooltip(v => pct(v, 2), { label: r => `${r.axisValue} 営業日目` }), xAxis: E.xAxis(x, { formatter: v => `${v}日`, interval: 9 }), yAxis: E.yAxis({ formatter: v => pct(v, 1, false) }), series });
  const ls = p.decile_summary?.long_short; const top = p.decile_summary?.["10"]; const bot = p.decile_summary?.["1"];
  $("#caption-decile").textContent = ls
    ? `過去 ${d.data_quality.n_events_complete_car.toLocaleString()} 件の決算で、サプライズが最も大きかった10%は60営業日で平均 ${pct(top?.mean)}、最も小さかった10%は ${pct(bot?.mean)}。差は ${pct(ls.mean)}（t値 ${ls.t ?? "—"}）。t値がおおむね2を超えると偶然とは考えにくい差です。`
    : "ドリフトを集計できる完了イベントがまだ足りません。";
  renderLeadLag(d);
  $$(".pill-toggle button").forEach(b => b.onclick = () => { state.leadlagFreq = b.dataset.freq; $$(".pill-toggle button").forEach(x => x.setAttribute("aria-pressed", String(x === b))); renderLeadLag(d); });
  renderCondTable(d);
  renderExpertStats(d);
}

function renderLeadLag(d) {
  const E = EvilCharts; const ll = state.leadlagFreq === "monthly" ? d.supply_chain.leadlag_monthly : d.supply_chain.leadlag_weekly;
  const unit = state.leadlagFreq === "monthly" ? "か月" : "週";
  const el = $("#chart-leadlag"); if (state.charts.leadlag) state.charts.leadlag.dispose();
  const ch = echarts.init(el); state.charts.leadlag = ch;
  const lags = ll.lags.filter(k => k >= 1); const vals = lags.map(k => ll.weighted_corr[ll.lags.indexOf(k)]);
  const data = lags.map((k, i) => ({ value: vals[i], itemStyle: { color: k === ll.best_lag ? E.NAVY : E.GRAY_LIGHT, borderRadius: vals[i] >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } }));
  const lim = Math.max(0.02, ...vals.filter(isNum).map(v => Math.abs(v))) * 1.2;
  ch.setOption({ grid: E.grid(), tooltip: E.tooltip(v => (isNum(v) ? Number(v).toFixed(3) : "—"), { label: r => `${r.axisValue}${unit}遅れ` }), xAxis: E.xAxis(lags, { bar: true, formatter: v => `${v}${unit}` }), yAxis: E.yAxis({ formatter: v => Number(v).toFixed(2), min: -lim, max: lim }),
    series: [{ ...E.barSeries({ name: "相関（売上依存度で加重）", data, color: E.GRAY_LIGHT }), data }] });
  const same = ll.weighted_corr[0]; const weak = !(ll.best_corr > 0.03);
  $("#caption-leadlag").textContent = weak
    ? `同じ期間どうしの相関は ${Number(same).toFixed(2)} ありますが、「取引先が先に動き、あとからサプライヤーが追いつく」という遅れ相関は、このユニバース（約120銘柄・2018年以降）では明確には検出できませんでした（最大でも ${Number(ll.best_corr).toFixed(3)}、${ll.best_lag}${unit}遅れ）。論文は米国の大規模サンプルで1か月遅れの効果を報告しています。「取引先の追い風」は補助的なシグナルとして扱ってください。`
    : `取引先の値動きと、その ${ll.best_lag}${unit}後のサプライヤーの値動きの相関が最も高く（${Number(ll.best_corr).toFixed(3)}）、同じ期間どうしの相関は ${Number(same).toFixed(2)} でした。値が小さくても、多数の銘柄・期間で同じ向きなら投資戦略として意味を持つ、というのが Cohen & Frazzini の指摘です。`;
}

function renderCondTable(d) {
  const p = d.pead; const tbl = p.conditional_table || []; const best = p.best_condition;
  const el = $("#cond-table"); const label = { 1: "弱い", 2: "ふつう", 3: "強い" };
  const cell = (s, t) => tbl.find(r => r.sue_tercile === s && r.txt_tercile === t);
  const cellHTML = (c, isBest, emptyNote) => (!c || !c.n) ? `<div class="cell empty">—<div class="n">${emptyNote}</div></div>`
    : `<div class="cell ${isBest ? "best" : ""}"><div class="v" style="color:${c.mean_car >= 0 ? "var(--up)" : "var(--down)"}">${pct(c.mean_car)}</div><div class="n">n=${c.n} · 勝率 ${Math.round(c.hit_rate * 100)}%</div></div>`;
  let html = `<div class="h"></div>${[1, 2, 3].map(t => `<div class="h">語り口: ${label[t]}</div>`).join("")}<div class="h">テキスト無し（全体）</div>`;
  for (const s of [3, 2, 1]) {
    html += `<div class="h">サプライズ: ${label[s]}</div>`;
    for (const t of [1, 2, 3]) html += cellHTML(cell(s, t), best && best.sue_tercile === s && best.txt_tercile === t, "テキスト未登録");
    html += cellHTML(cell(s, null), best && best.sue_tercile === s && best.txt_tercile === null, "データ不足");
  }
  el.innerHTML = html;
  const anyText = tbl.some(r => r.txt_tercile !== null && r.n > 0);
  $("#caption-cond").textContent = anyText
    ? `青枠が、過去データで最もドリフトが強かった組み合わせです。右端の列はテキストの有無にかかわらず全イベントを集計したものです。`
    : `決算説明テキストがまだ登録されていないため、語り口の区分（左3列）は空です。右端の列がサプライズ度だけで見た結果で、青枠が最もドリフトが強かった群です。テキストを登録すると左の表が埋まり、両方が「強い」の組み合わせを確認できます。`;
}

function renderExpertStats(d) {
  const p = d.pead, sc = d.supply_chain, fm = sc.fama_macbeth || {}, reg = p.regression || {}, sp = p.customer_spillover || {}, cm = sc.cm_quintile_sort || {};
  const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`; const coef = c => c ? `${c.b ?? c.mean} (t=${c.t})` : "—";
  $("#expert-stats-body").innerHTML = `
    <h4>PEAD 横断面回帰（HC1）: CAR60 ~ SUE_rank + SUE.txt + 交互作用</h4>
    <dl class="kv">${row("n", reg.n ?? "—")}${row("R²", reg.r2 ?? "—")}${Object.entries(reg.coef || {}).map(([k, v]) => row(k, coef(v))).join("")}</dl>
    <h4>十分位ロング・ショート（D10 − D1, CAR60）</h4>
    <dl class="kv">${row("平均差", pct(p.decile_summary?.long_short?.mean, 2))}${row("t", p.decile_summary?.long_short?.t ?? "—")}${row("n (D10 / D1)", `${p.decile_summary?.long_short?.n_top ?? "—"} / ${p.decile_summary?.long_short?.n_bottom ?? "—"}`)}</dl>
    <h4>顧客サプライズの波及: 供給者 CAR60 ~ 顧客 SUE（直近90日, 加重）+ 自社 SUE</h4>
    <dl class="kv">${row("n", sp.n ?? "—")}${row("β 顧客SUE", coef(sp.beta_customer_sue))}${row("β 自社SUE", coef(sp.beta_own_sue))}</dl>
    <h4>Fama–MacBeth（月次）: r_{t+1} ~ CM_t + r_t + MOM_{t−12,t−2}</h4>
    <dl class="kv">${row("期間数", fm.n_periods ?? "—")}${row("平均横断面サイズ", fm.avg_cross_section ? fm.avg_cross_section.toFixed(1) : "—")}${row("β_CM（NW t）", coef(fm.beta_cm))}${row("γ_own", coef(fm.gamma_own))}${row("δ_MOM", coef(fm.delta_mom))}</dl>
    <h4>顧客モメンタム五分位（翌月市場調整リターン）</h4>
    <dl class="kv">${[1, 2, 3, 4, 5].map(k => cm[String(k)] ? row(`Q${k}`, `${pct(cm[String(k)].mean, 2)} (t=${cm[String(k)].t})`) : "").join("")}${cm.long_short ? row("Q5 − Q1", `${pct(cm.long_short.mean, 2)} (t=${cm.long_short.t})`) : ""}</dl>
    <h4>リード・ラグ（交差自己相関, 加重平均）</h4>
    <dl class="kv">${row("月次 ρ̄(k)", (sc.leadlag_monthly.weighted_corr || []).map((v, i) => `k${i}:${isNum(v) ? v.toFixed(3) : "—"}`).join(" "))}${row("週次 best", `k*=${sc.leadlag_weekly.best_lag} (${sc.leadlag_weekly.best_corr})`)}</dl>
    <p class="small muted">サンプルは同梱ユニバース（約120銘柄・2018年以降）に限られるため、論文の米国大規模サンプルより推定値のばらつきが大きい点に注意してください。</p>`;
}

function renderGraph(d) {
  const E = EvilCharts; const g = d.supply_chain.graph; const el = $("#chart-graph");
  const ch = echarts.init(el); state.charts.graph = ch;
  const deg = {}; g.links.forEach(l => { deg[l.target] = (deg[l.target] || 0) + 1; deg[l.source] = (deg[l.source] || 0) + 1; });
  const nodes = g.nodes.filter(n => deg[n.id]).map(n => ({
    id: n.id, name: n.name, value: n.id, symbolSize: Math.min(44, 10 + 4 * Math.sqrt(deg[n.id] || 1) + (n.highlight ? 6 : 0)),
    itemStyle: { color: n.highlight ? E.NAVY : (n.screened ? cssVar("--chart-node") : cssVar("--chart-node-dim")), borderColor: n.highlight ? E.NAVY : E.GRAY, borderWidth: 1 },
    label: { show: n.highlight || (deg[n.id] || 0) >= 2, color: n.highlight ? E.NAVY : E.TOKENS.foreground, fontSize: 10, position: "right" },
    category: n.highlight ? 1 : 0,
  }));
  const links = g.links.map(l => ({ source: l.source, target: l.target, value: l.weight, lineStyle: { width: 0.6 + 3 * l.weight, color: E.GRAY_LIGHT, curveness: 0.15 }, note: l.note, provenance: l.provenance }));
  ch.setOption({
    tooltip: { trigger: "item", confine: true, backgroundColor: E.TOKENS.background, borderColor: E.TOKENS.border, borderWidth: 1, padding: [6, 10], textStyle: { color: E.INK, fontSize: 12 },
      formatter: p => p.dataType === "edge" ? `${esc(p.data.source)} → ${esc(p.data.target)}<br>売上依存度 ${Math.round(p.data.value * 100)}%<br><span style="color:${E.TOKENS.mutedForeground}">${esc(p.data.provenance === "edinet" ? "有報の主要顧客開示" : p.data.provenance === "apple" ? "Apple Supplier List" : "業界公知（推定）")}${p.data.note ? " · " + esc(p.data.note) : ""}</span>` : `<b>${esc(p.data.name)}</b> <span style="color:${E.TOKENS.mutedForeground}">${esc(p.data.id)}</span>` },
    animationDuration: 800, animationEasingUpdate: "quinticInOut",
    series: [{ type: "graph", layout: "force", roam: true, draggable: true, zoom: 0.85, data: nodes, links, edgeSymbol: ["none", "arrow"], edgeSymbolSize: 6,
      force: { repulsion: 70, edgeLength: [24, 80], gravity: 0.3, friction: 0.3 }, emphasis: { focus: "adjacency", lineStyle: { width: 3, color: E.INK } }, lineStyle: { opacity: 0.9 },
      label: { show: true }, labelLayout: { hideOverlap: true }, categories: [{ name: "銘柄" }, { name: "候補" }] }],
  });
}

function renderDataQuality(d) {
  const q = d.data_quality; const ed = q.edinet || {};
  const edinetText = ed.enabled === true ? `有効（${ed.docs_scanned} 書類を解析、顧客エッジ ${ed.edges_added} 件、MD&A テキスト ${ed.texts_added} 件）`
    : ed.enabled === "cache" ? "前回の取得結果を利用" : "未設定（同梱の取引先データで動作中。EDINET の API キーを置くと有報から自動補強します）";
  const u = q.universe || {};
  const uniText = u.mode === "broad"
    ? `同梱のサプライチェーン銘柄 ${u.n_seed} ＋ Yahoo Finance スクリーナー ${u.n_dynamic} 銘柄（東証・時価総額 ${Math.round((u.mcap_min || 0) / 1e8)} 億円以上・売買代金 ${Math.round((u.min_turnover || 0) / 1e8)} 億円/日以上）。日本語社名は EDINET 提出者リスト ${u.n_jp_names ? u.n_jp_names.toLocaleString() + " 社" : "未取得（英語名で表示）"}。`
    : `同梱のリスト ${u.n_seed} 銘柄のみ（RIPPLE_UNIVERSE=broad で拡張）`;
  const rows = [
    ["対象銘柄", uniText],
    ["株価データ", `${q.n_symbols_priced.toLocaleString()} 銘柄（${d.as_of} 時点、取得 ${(q.price_fetched_at || "").replace("T", " ").slice(0, 16)}）`],
    ["決算イベント", `${q.n_events.toLocaleString()} 件（うちアナリスト予想あり ${q.n_events_analyst_sue.toLocaleString()} 件、60営業日の検証が完了 ${q.n_events_complete_car.toLocaleString()} 件）`],
    ["決算説明テキスト", `${q.n_texts} 件登録（イベントに紐づいたもの ${q.n_events_with_text} 件、推定器: ${q.text_model === "logit" ? `ロジスティック回帰 n=${q.text_model_n}` : "事前重み（学習データ不足）"}）`],
    ["取引先の関係", `${q.n_edges} 本のつながり、${q.n_suppliers} 銘柄に取引先情報あり`],
    ["EDINET（有報）", edinetText],
    ["計算時刻", `${d.generated_at}（${d.elapsed_sec} 秒）`],
  ];
  const warn = (q.warnings || []).concat(ed.errors || []);
  $("#data-quality").innerHTML = `<dl class="dq">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${warn.length ? `<div class="notice notice-warn" style="margin-top:12px"><strong>注意</strong><ul class="bullets">${warn.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}`;
}

function disposeCharts() { Object.values(state.charts).forEach(c => { try { c.dispose(); } catch (e) { /* noop */ } }); state.charts = {}; }
window.addEventListener("resize", () => Object.values(state.charts).forEach(c => c.resize()));

// ---------- 更新ジョブ ----------
async function startRefresh(force = false) {
  const btn = $("#btn-refresh"); btn.disabled = true; btn.classList.add("spinning");
  try {
    const res = await fetch("api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force }) });
    await res.json();
  } catch (e) { btn.disabled = false; btn.classList.remove("spinning"); showBanner("err", "サーバに接続できません。run.sh を実行してください。"); return; }
  beginPolling();
}
function beginPolling() {
  const btn = $("#btn-refresh"); btn.disabled = true; btn.classList.add("spinning"); $("#progress").hidden = false;
  const sub = $("#actions-sub");
  if (state.polling) clearInterval(state.polling);
  const t0 = Date.now();
  state.polling = setInterval(async () => {
    try {
      const st = await (await fetch("api/status", { cache: "no-store" })).json();
      $("#progress-bar").style.width = `${st.pct || 0}%`;
      if (st.running) sub.textContent = `更新中… ${st.message || ""}（${Math.round(st.pct || 0)}%）`;
      if (!st.running) {
        clearInterval(state.polling); state.polling = null; btn.disabled = false; btn.classList.remove("spinning");
        setTimeout(() => { $("#progress").hidden = true; $("#progress-bar").style.width = "0"; }, 600);
        if (st.error) showBanner("err", `更新に失敗しました: ${esc(st.error)}`);
        else if (st.finished && (Date.now() - t0 > 500 || st.has_results)) { const keep = new Set(state.expanded); await load(); keep.forEach(s => { if ($(`.crow[data-symbol="${s}"]`)) toggleRow(s); }); }
      }
    } catch (e) { /* 一時的な通信エラーは無視 */ }
  }, 1500);
}
$("#btn-refresh").addEventListener("click", () => startRefresh(false));

// ---------- テーマ（システム / ライト / ダーク） ----------
const THEME_ORDER = ["auto", "light", "dark"];
const THEME_LABEL = { auto: "テーマ: システム設定に従う", light: "テーマ: ライト", dark: "テーマ: ダーク" };
function currentTheme() { try { const t = localStorage.getItem("ripple-theme"); return THEME_ORDER.includes(t) ? t : "auto"; } catch (e) { return "auto"; } }
function applyTheme(mode, rerender = true) {
  if (mode === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", mode);
  try { localStorage.setItem("ripple-theme", mode); } catch (e) { /* noop */ }
  const b = $("#btn-theme"); b.dataset.mode = mode; b.title = THEME_LABEL[mode]; b.setAttribute("aria-label", THEME_LABEL[mode]);
  if (rerender && state.data) { const keep = new Set(state.expanded); render(state.data); keep.forEach(sym => { if ($(`.crow[data-symbol="${sym}"]`)) toggleRow(sym); }); }
}
$("#btn-theme").addEventListener("click", () => applyTheme(THEME_ORDER[(THEME_ORDER.indexOf(currentTheme()) + 1) % THEME_ORDER.length]));
applyTheme(currentTheme(), false);
if (window.matchMedia) { window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (currentTheme() === "auto" && state.data) applyTheme("auto"); }); }

// ---------- ヘッダーのスクロール影 ----------
const topbar = $("#topbar");
const onScroll = () => topbar.classList.toggle("scrolled", window.scrollY > 4);
window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

// ---------- 起動 ----------
(async () => {
  state.isStatic = await detectStatic();
  if (state.isStatic) { $("#btn-refresh").hidden = true; const q = $('.quick button[data-act="refresh"]'); if (q) { q.querySelector(".qlabel").textContent = "自動更新"; } }
  await load();
  if (!state.isStatic) { try { const st = await (await fetch("api/status")).json(); if (st.running) beginPolling(); } catch (e) { /* noop */ } }
})().catch(e => showBanner("err", `読み込みに失敗しました: ${esc(e.message)}`));
