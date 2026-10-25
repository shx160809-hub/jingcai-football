"""输出层：多页面静态 HTML 展示（含合规声明）。

页面结构：
  - index.html     主页：菜单栏 + 模型置信度 Top5
  - matches.html   今日场次（全部覆盖场次详情）
  - settled.html   已结算场次（场次详情）
  - accuracy.html  累计命中率（命中率 vs 置信度关系）

纯前端（内联 CSS），无后端依赖，可直接托管 GitHub Pages。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.common import data_dir, load_config

DISCLAIMER_HTML = (
    "本工具为<b>体育数据分析工具</b>，所有输出（胜/平/负概率、预期比分分布、模型参考值）"
    "仅用于<u>统计分析</u>，<b>不构成任何投注建议或中奖预测</b>。不保证任何结果，不展示胜率，"
    "不接入投注/支付/资金通道。请理性看待数据，遵守中国法律法规，远离非法博彩。"
)

# ============================================================
# 共享样式与组件
# ============================================================

CSS = """
:root {
  --bg: #f4f5f9;
  --card: #ffffff;
  --text: #1e293b;
  --muted: #64748b;
  --border: #e8ebf1;
  --accent: #4f46e5;
  --accent-2: #7c3aed;
  --gold: #f59e0b;
  --shadow: 0 1px 3px rgba(15,23,42,.06), 0 1px 2px rgba(15,23,42,.04);
  --shadow-lg: 0 10px 30px rgba(15,23,42,.10);
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); line-height:1.6; }

/* 顶部菜单栏 */
.navbar {
  position: sticky; top:0; z-index:100;
  background:linear-gradient(135deg,#1e1b4b,#312e81);
  box-shadow:0 2px 10px rgba(0,0,0,.15);
}
.navbar-inner { max-width:960px; margin:0 auto; padding:0 16px; display:flex; align-items:center; justify-content:space-between; height:60px; }
.brand { color:#fff; font-size:17px; font-weight:700; letter-spacing:.5px; text-decoration:none; display:flex; align-items:center; gap:8px; }
.brand .logo { width:28px; height:28px; background:linear-gradient(135deg,#6366f1,#a855f7); border-radius:8px; display:flex; align-items:center; justify-content:center; font-size:15px; }
.nav-links { display:flex; gap:4px; }
.nav-links a { color:#c7d2fe; text-decoration:none; font-size:14px; padding:8px 14px; border-radius:8px; transition:all .2s; white-space:nowrap; }
.nav-links a:hover { background:rgba(255,255,255,.1); color:#fff; }
.nav-links a.active { background:rgba(255,255,255,.16); color:#fff; font-weight:600; }

.banner { background:linear-gradient(90deg,#fff7ed,#fef3c7); border-bottom:1px solid #fde68a; padding:10px 16px; font-size:12px; line-height:1.6; text-align:center; color:#92400e; }

.container { max-width:960px; margin:0 auto; padding:24px 16px 48px; }

/* 页头 */
.page-hero { text-align:center; padding:28px 0 20px; }
.page-hero h1 { font-size:26px; font-weight:800; letter-spacing:.5px; background:linear-gradient(135deg,#312e81,#7c3aed); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.page-hero .sub { color:var(--muted); font-size:13px; margin-top:6px; }

/* 概览统计 */
.overview { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:6px; }
.stat { background:var(--card); border-radius:14px; padding:18px 10px; text-align:center; box-shadow:var(--shadow); border:1px solid var(--border); }
.stat-num { font-size:24px; font-weight:800; color:var(--accent); }
.stat-label { font-size:11px; color:var(--muted); margin-top:4px; }
.stat-note { font-size:11px; color:#94a3b8; text-align:center; margin-bottom:18px; }

/* 分区标题 */
.section-title { display:flex; align-items:center; gap:10px; font-size:17px; font-weight:700; margin:26px 0 14px; }
.section-title .bar { width:4px; height:20px; background:linear-gradient(180deg,#6366f1,#a855f7); border-radius:2px; }
.section-sub { font-size:12px; font-weight:400; color:var(--muted); }

/* 卡片 */
.card { background:var(--card); border-radius:16px; padding:18px; margin-bottom:14px; box-shadow:var(--shadow); border:1px solid var(--border); transition:transform .15s, box-shadow .15s; }
.card:hover { transform:translateY(-2px); box-shadow:var(--shadow-lg); }
.card-top { border:2px solid var(--gold); background:linear-gradient(180deg,#fffbeb,#fff); }
.top-badge { display:inline-flex; align-items:center; gap:4px; background:linear-gradient(135deg,#f59e0b,#f97316); color:#fff; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.league { color:var(--muted); font-size:12px; margin-bottom:10px; display:flex; align-items:center; flex-wrap:wrap; gap:6px; }
.time { color:#94a3b8; }
.teams { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
.team { flex:1; text-align:center; }
.team-name { font-size:17px; font-weight:700; }
.vs { color:#cbd5e1; font-size:12px; font-weight:700; }
.probs { margin-bottom:8px; }
.prob { display:flex; align-items:center; margin:7px 0; font-size:12px; }
.prob-label { width:38px; color:var(--muted); flex-shrink:0; }
.prob-bar-wrap { flex:1; background:#f1f5f9; border-radius:6px; height:14px; overflow:hidden; margin:0 10px; }
.prob-bar { height:100%; border-radius:6px; transition:width .5s ease; }
.prob-val { width:50px; text-align:right; font-weight:700; flex-shrink:0; }
.score { color:var(--muted); font-size:12px; margin:8px 0; }
.score b { color:var(--text); }
.odds { color:var(--muted); font-size:12px; }
.odds b { color:var(--accent-2); }
.divergence { font-size:12px; color:var(--muted); margin-top:8px; padding-top:8px; border-top:1px dashed var(--border); }
.disclaimer-sm { color:#dc2626; font-size:11px; margin-top:10px; }
.tag { font-size:11px; padding:2px 8px; border-radius:10px; }
.tag-dc { background:#eef2ff; color:#4338ca; }
.tag-odds { background:#fef3c7; color:#92400e; }
.tag-prior { background:#fee2e2; color:#991b1b; }
.conf { font-size:11px; padding:2px 8px; border-radius:10px; background:#f0fdf4; color:#15803d; }

/* 比分置信度 */
.score-conf { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:2px 8px; border-radius:10px; background:#eef2ff; color:#4338ca; }
.card-score { border:2px solid #6366f1; background:linear-gradient(180deg,#eef2ff,#fff); }
.score-badge { display:inline-flex; align-items:center; gap:4px; background:linear-gradient(135deg,#6366f1,#8b5cf6); color:#fff; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.score-hero { display:flex; align-items:baseline; justify-content:center; gap:12px; margin:12px 0; }
.score-hero .s { font-size:40px; font-weight:800; background:linear-gradient(135deg,#312e81,#7c3aed); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.score-hero .p { font-size:18px; font-weight:700; color:#4338ca; }
.score-detail { text-align:center; color:var(--muted); font-size:12px; }

/* 已完赛结果 */
.finished-score { display:flex; align-items:center; justify-content:center; gap:14px; margin:12px 0; }
.finished-score .team-label { font-size:15px; font-weight:700; flex:1; text-align:right; }
.finished-score .team-label.away { text-align:left; }
.finished-score .score-num { font-size:42px; font-weight:800; letter-spacing:2px; background:linear-gradient(135deg,#0f172a,#334155); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.finished-score .score-colon { font-size:24px; color:#94a3b8; font-weight:700; }
.result-badge { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.result-badge.home { background:#fee2e2; color:#b91c1c; }
.result-badge.draw { background:#f1f5f9; color:#475569; }
.result-badge.away { background:#dcfce7; color:#15803d; }
.result-badge.pending { background:#fef9c3; color:#a16207; }
.finished-score .score-num.pending-score { background:linear-gradient(135deg,#94a3b8,#cbd5e1); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }

/* 结算标记 */
.result-hit { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:2px 8px; border-radius:10px; }
.result-hit.hit { background:#dcfce7; color:#15803d; }
.result-hit.miss { background:#fee2e2; color:#b91c1c; }

/* 已完赛命中标注（命中=红，未命中=绿） */
.finish-verdict { display:inline-flex; align-items:center; gap:4px; font-size:12px; padding:3px 12px; border-radius:20px; font-weight:700; }
.finish-verdict.hit { background:#fee2e2; color:#b91c1c; border:1px solid #fecaca; }
.finish-verdict.miss { background:#dcfce7; color:#15803d; border:1px solid #bbf7d0; }
.finish-meta { display:flex; align-items:center; justify-content:center; flex-wrap:wrap; gap:8px; margin:10px 0 4px; font-size:12px; color:var(--muted); }
.finish-meta .conf-chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; background:#eef2ff; color:#4338ca; font-weight:600; }
.finish-meta .truth-chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; background:#f1f5f9; color:#475569; }

/* 今日赛果（实时看板） */
.live-state { display:inline-flex; align-items:center; gap:5px; font-size:11px; padding:3px 10px; border-radius:20px; font-weight:700; margin-right:6px; }
.live-state.live { background:#fee2e2; color:#b91c1c; }
.live-state.live::before { content:""; width:7px; height:7px; border-radius:50%; background:#ef4444; animation:live-pulse 1.2s infinite; }
@keyframes live-pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
.live-state.finished { background:#e0e7ff; color:#3730a3; }
.live-state.not_started { background:#f1f5f9; color:#64748b; }
.live-score { display:flex; align-items:center; justify-content:center; gap:14px; margin:12px 0; }
.live-score .team-label { font-size:15px; font-weight:700; flex:1; text-align:right; }
.live-score .team-label.away { text-align:left; }
.live-score .score-num { font-size:40px; font-weight:800; letter-spacing:2px; background:linear-gradient(135deg,#0f172a,#334155); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.live-score .score-colon { font-size:22px; color:#94a3b8; font-weight:700; }
.live-score .score-num.live-num { background:linear-gradient(135deg,#dc2626,#ef4444); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.live-half { text-align:center; color:var(--muted); font-size:11px; }
.live-note { text-align:center; color:#94a3b8; font-size:11px; margin-top:8px; }
.live-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }
.live-grid .card { margin-bottom:0; }
@media (max-width:640px) { .live-grid { grid-template-columns:1fr; } }

/* 图表容器 */
.chart-wrap { background:var(--card); border-radius:16px; padding:20px; box-shadow:var(--shadow); border:1px solid var(--border); margin-bottom:16px; }
.chart-title { font-size:15px; font-weight:700; margin-bottom:12px; }

/* 空状态 */
.empty { text-align:center; color:var(--muted); padding:60px 20px; }
.empty .icon { font-size:40px; margin-bottom:12px; }

.footer { text-align:center; color:#94a3b8; font-size:12px; padding:24px 16px 40px; line-height:1.8; }

@media (max-width:640px) {
  .overview { grid-template-columns:repeat(2,1fr); }
  .nav-links a { font-size:13px; padding:7px 10px; }
  .team-name { font-size:15px; }
  .navbar-inner { height:56px; }
}
"""


def _nav(active: str) -> str:
    """渲染顶部菜单栏。items 为 (href, label, 是否外链新窗口)。"""
    items = [
        ("index.html", "首页", False),
        ("live.html", "今日赛果", False),
        ("matches.html", "今日场次", False),
        ("scores.html", "比分", False),
        ("finished.html", "已完赛", False),
        ("settled.html", "已结算场次", False),
        ("accuracy.html", "累计命中率", False),
        ("feedback.html", "数据回馈", False),
        ("calculator.html", "官方计算器", False),
    ]
    links = []
    for href, label, external in items:
        if external:
            links.append(
                f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label} ↗</a>'
            )
        else:
            links.append(
                f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
            )
    links = "".join(links)
    return f"""
<nav class="navbar">
  <div class="navbar-inner">
    <a href="index.html" class="brand"><span class="logo">⚽</span>竞彩概率分析</a>
    <div class="nav-links">{links}</div>
  </div>
</nav>
<div class="banner">⚠️ {DISCLAIMER_HTML}</div>"""


def _tag(m: dict) -> str:
    src = m.get("source", "dixon_coles")
    if src == "dixon_coles":
        return '<span class="tag tag-dc">Dixon-Coles</span>'
    if src == "odds_implied":
        return '<span class="tag tag-odds">市场隐含概率</span>'
    return '<span class="tag tag-prior">均匀先验</span>'


def _conf_tag(m: dict) -> str:
    conf = m.get("confidence_score")
    if conf is None:
        return ""
    return f'<span class="conf">置信度 {conf*100:.0f}%</span>'


def _fmt_time(m: dict, day: str = "") -> str:
    """格式化开球时间：'2026-10-25 13:00:00' -> '10-25 13:00'。

    相对比赛日 day（YYYYMMDD）判断是否跨到次日凌晨，跨日时标注「次日」。
    """
    t = m.get("match_time") or m.get("kickoff") or ""
    s = str(t).strip()
    if not s:
        return ""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
            return dt.strftime("%m-%d")
        except ValueError:
            return s
    # 相对比赛日判断跨日
    if day and len(day) == 8:
        try:
            anchor = datetime.strptime(day, "%Y%m%d")
            if dt.date() > anchor.date():
                return f"{dt.strftime('%m-%d %H:%M')} 次日"
        except ValueError:
            pass
    return dt.strftime("%m-%d %H:%M")


def _kickoff_ts(m: dict):
    """解析场次开球时间为 aware datetime，失败返回 None。"""
    s = m.get("kickoff") or m.get("match_time") or ""
    s = str(s).strip()
    if not s:
        return None
    from zoneinfo import ZoneInfo

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except ValueError:
            continue
    return None


def _is_finished(m: dict, now=None) -> bool:
    """判断场次是否已开球（完赛/进行中）。kickoff <= 当前时间视为已完赛剔除。"""
    ts = _kickoff_ts(m)
    if ts is None:
        return False
    if now is None:
        now = datetime.now().astimezone()
    return ts <= now


def _filter_upcoming(results: list[dict]) -> list[dict]:
    """过滤掉已开球的场次，仅保留未开赛场次。"""
    return [m for m in results if not _is_finished(m)]


def _match_card(m: dict, top: bool = False, settled_meta: dict | None = None, day: str = "") -> str:
    """渲染单场比赛卡片。settled_meta 用于已结算页（含命中标记）。"""
    p_h = m["p_home"] * 100
    p_d = m["p_draw"] * 100
    p_a = m["p_away"] * 100
    scores = m.get("top_scores", [])
    top_str = "　".join(f"{s['score']} {s['prob']*100:.1f}%" for s in scores[:3])

    # 结算标记
    settle_html = ""
    if settled_meta is not None:
        hit = settled_meta.get("pred_hit")
        truth_cn = {"home": "主胜", "draw": "平局", "away": "客胜"}.get(
            settled_meta.get("truth", ""), ""
        )
        cls = "hit" if hit else "miss"
        label = "✓ 命中" if hit else "✗ 未命中"
        settle_html = (
            f'<span class="result-hit {cls}">{label}</span>'
            f'<span class="tag" style="background:#f1f5f9;color:#475569">实际：{truth_cn}</span>'
        )

    # 分歧
    divergence_html = ""
    if m.get("divergence") is not None:
        gap = m["model_market_gap"]
        arrow = "↑" if gap > 0 else "↓"
        color = "#e11d48" if abs(gap) > 0.08 else "#64748b"
        divergence_html = (
            f'<div class="divergence">模型 vs 市场分歧 {m["divergence"]*100:.1f}%　'
            f'<span style="color:{color}">模型对「{m["model_higher_on"]}」更乐观 {arrow}{abs(gap)*100:.1f}%</span></div>'
        )

    odds = ""
    if m.get("had_win") is not None:
        odds = (
            f'<div class="odds">官方赔率 胜 <b>{m["had_win"]}</b> / 平 <b>{m["had_draw"]}</b> '
            f'/ 负 <b>{m["had_lose"]}</b></div>'
        )

    if m.get("expected_home_goals") is not None:
        score_line = (
            f'<div class="score">预期比分 <b>{m["expected_home_goals"]} : {m["expected_away_goals"]}</b>'
            f'　高频 {top_str}</div>'
        )
    else:
        score_line = '<div class="score">该联赛暂无历史数据，概率来自官方赔率去水</div>'

    top_badge = '<span class="top-badge">★ 置信度 Top5</span>' if top else ""

    return f"""
<div class="card{' card-top' if top else ''}">
  <div class="league">{top_badge}{m.get('league','')} <span class="time">{_fmt_time(m, day)}</span> {_tag(m)} {_conf_tag(m)} {settle_html}</div>
  <div class="teams">
    <div class="team"><div class="team-name">{m['home']}</div></div>
    <div class="vs">VS</div>
    <div class="team"><div class="team-name">{m['away']}</div></div>
  </div>
  <div class="probs">
    <div class="prob"><div class="prob-label">主胜</div><div class="prob-bar-wrap"><div class="prob-bar" style="width:{p_h:.0f}%;background:linear-gradient(90deg,#f43f5e,#e11d48)"></div></div><div class="prob-val">{p_h:.1f}%</div></div>
    <div class="prob"><div class="prob-label">平局</div><div class="prob-bar-wrap"><div class="prob-bar" style="width:{p_d:.0f}%;background:linear-gradient(90deg,#cbd5e1,#94a3b8)"></div></div><div class="prob-val">{p_d:.1f}%</div></div>
    <div class="prob"><div class="prob-label">客胜</div><div class="prob-bar-wrap"><div class="prob-bar" style="width:{p_a:.0f}%;background:linear-gradient(90deg,#10b981,#059669)"></div></div><div class="prob-val">{p_a:.1f}%</div></div>
  </div>
  {score_line}
  {odds}
  {divergence_html}
  <div class="disclaimer-sm">模型参考值，不构成投注建议</div>
</div>"""


def _score_card(m: dict, top: bool = False, day: str = "") -> str:
    """渲染单场比分分析卡片：突出最可能比分及其概率。"""
    top = m.get("top_scores") or []
    if not top:
        return ""
    most = top[0]
    # 前 3 个高频比分
    detail = "　".join(f"{s['score']} {s['prob']*100:.1f}%" for s in top[:3])
    badge = '<span class="score-badge">比分置信度 Top3</span>' if top else ""
    return f"""
<div class="card card-score">
  <div class="league">{badge}{m.get('league','')} <span class="time">{_fmt_time(m, day)}</span> {_tag(m)} <span class="score-conf">比分置信度 {most['prob']*100:.1f}%</span></div>
  <div class="teams">
    <div class="team"><div class="team-name">{m['home']}</div></div>
    <div class="vs">VS</div>
    <div class="team"><div class="team-name">{m['away']}</div></div>
  </div>
  <div class="score-hero">
    <span class="s">{most['score']}</span>
    <span class="p">{most['prob']*100:.1f}%</span>
  </div>
  <div class="score-detail">高频比分分布：{detail}</div>
  <div class="disclaimer-sm">模型参考值，不构成投注建议</div>
</div>"""


def _finished_card(s: dict) -> str:
    """渲染已完赛场次的结果卡片（实际比分 + 命中标注 + 开赛前置信度）。"""
    hg = s.get("home_goals")
    ag = s.get("away_goals")

    date_str = str(s.get("date", ""))

    def _int(v):
        """把 float 比分（如 2.0）转为整数显示。"""
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    if hg is None or ag is None or (
        isinstance(hg, float) and hg != hg
    ) or (isinstance(ag, float) and ag != ag):
        # 无比分：进行中 / 待结算占位
        return f"""
<div class="card">
  <div class="league"><span class="result-badge pending">待更新</span>{s.get('league','')} <span class="time">{date_str}</span></div>
  <div class="finished-score">
    <div class="team-label">{s['home']}</div>
    <div class="score-num pending-score">-</div>
    <div class="score-colon">:</div>
    <div class="score-num pending-score">-</div>
    <div class="team-label away">{s['away']}</div>
  </div>
  <div class="disclaimer-sm">比分尚未获取，赛后自动更新</div>
</div>"""

    hg, ag = _int(hg), _int(ag)

    if hg > ag:
        badge = '<span class="result-badge home">主胜</span>'
        truth_cn = "主胜"
    elif hg < ag:
        badge = '<span class="result-badge away">客胜</span>'
        truth_cn = "客胜"
    else:
        badge = '<span class="result-badge draw">平局</span>'
        truth_cn = "平局"

    # 命中标注：命中=红，未命中=绿
    verdict_html = ""
    hit = s.get("pred_hit")
    if hit is not None:
        if hit:
            verdict_html = '<span class="finish-verdict hit">✓ 命中</span>'
        else:
            verdict_html = '<span class="finish-verdict miss">✗ 未命中</span>'

    # 开赛前置信度
    conf_html = ""
    conf = s.get("confidence_score")
    if conf is not None:
        conf_html = f'<span class="conf-chip">开赛前置信度 {conf*100:.0f}%</span>'

    # 预测的最高概率选项（用于辅助说明）
    pred_label = ""
    ph = s.get("p_home")
    pd = s.get("p_draw")
    pa = s.get("p_away")
    if ph is not None and pd is not None and pa is not None:
        mx = max(ph, pd, pa)
        if mx == ph:
            pred_label = "模型预测：主胜"
        elif mx == pa:
            pred_label = "模型预测：客胜"
        else:
            pred_label = "模型预测：平局"

    meta_html = ""
    if conf_html or pred_label:
        meta_html = f'<div class="finish-meta">{verdict_html}{conf_html}<span class="truth-chip">实际：{truth_cn}</span><span class="truth-chip">{pred_label}</span></div>'
    elif verdict_html:
        meta_html = f'<div class="finish-meta">{verdict_html}<span class="truth-chip">实际：{truth_cn}</span></div>'

    return f"""
<div class="card">
  <div class="league">{badge}{s.get('league','')} <span class="time">{date_str}</span></div>
  <div class="finished-score">
    <div class="team-label">{s['home']}</div>
    <div class="score-num">{hg}</div>
    <div class="score-colon">:</div>
    <div class="score-num">{ag}</div>
    <div class="team-label away">{s['away']}</div>
  </div>
  {meta_html}
  <div class="disclaimer-sm">以上为实际赛果，不构成投注建议</div>
</div>"""


def _page(title: str, active: str, body: str, day: str) -> str:
    """组装完整 HTML 页面。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - 竞彩概率分析</title>
<style>{CSS}</style>
</head>
<body>
{_nav(active)}
<div class="container">
{body}
</div>
<div class="footer">
  {DISCLAIMER_HTML}<br>
  历史回测表现不代表未来结果 · 本工具不构成任何形式的投注建议
</div>
</body>
</html>"""


# ============================================================
# 数据读取
# ============================================================

def _load_predict(day: str) -> list[dict]:
    jp = data_dir("results") / f"predict_{day}.json"
    if not jp.exists():
        return []
    return json.loads(jp.read_text(encoding="utf-8"))["matches"]


def _load_settlement() -> list[dict]:
    """读取结算记录（含置信度、命中标记）。"""
    sp = data_dir("results") / "settlement.csv"
    if not sp.exists():
        return []
    import pandas as pd

    df = pd.read_csv(sp)
    return df.to_dict("records")


# ============================================================
# 各页面渲染
# ============================================================

def render_index(results: list[dict], day: str) -> Path:
    """主页：菜单栏 + 模型置信度 Top5 + 比分置信度 Top3（剔除已完赛场次）。"""
    from src.output.confidence import top_n_by_confidence, top_n_by_score_confidence

    # 剔除已开球场次
    results = _filter_upcoming(results)

    top5 = top_n_by_confidence(results, 5)
    top3_score = top_n_by_score_confidence(results, 3)

    sections = []

    # 比分置信度 Top3（置顶）
    if top3_score:
        score_cards = "".join(_score_card(m, top=True, day=day) for m in top3_score)
        sections.append(f"""
<div class="section-title"><span class="bar"></span>比分置信度 Top3 <span class="section-sub">（模型对比分预测最确定的 3 场）</span></div>
{score_cards}""")

    # 模型置信度 Top5
    if top5:
        cards = "".join(_match_card(m, top=True, day=day) for m in top5)
        sections.append(f"""
<div class="section-title"><span class="bar"></span>模型置信度 Top5 <span class="section-sub">（模型观点最明确的 5 场）</span></div>
{cards}""")

    if sections:
        body = f"""
<div class="page-hero">
  <h1>今日重点关注</h1>
  <div class="sub">数据日期 {day} · 非推荐、不代表命中率</div>
</div>
{''.join(sections)}"""
    else:
        body = f"""
<div class="page-hero">
  <h1>今日重点关注</h1>
  <div class="sub">数据日期 {day}</div>
</div>
<div class="empty"><div class="icon">📭</div>暂无预测数据</div>"""

    out = Path("docs") / "index.html"
    out.write_text(_page("首页", "index.html", body, day), encoding="utf-8")
    return out


def render_matches(results: list[dict], day: str) -> Path:
    """今日场次子页：全部未开赛场次详情（剔除已完赛）。"""
    results = _filter_upcoming(results)
    n_dc = sum(1 for m in results if m.get("source") == "dixon_coles")
    overview = f"""
<div class="page-hero">
  <h1>今日场次</h1>
  <div class="sub">数据日期 {day} · 共 {len(results)} 场 · 模型覆盖 {n_dc} 场</div>
</div>"""

    if results:
        cards = "".join(_match_card(m, day=day) for m in results)
        body = overview + f"""
<div class="section-title"><span class="bar"></span>全部场次 <span class="section-sub">（{len(results)} 场）</span></div>
{cards}"""
    else:
        body = overview + '<div class="empty"><div class="icon">📭</div>暂无今日场次数据</div>'

    out = Path("docs") / "matches.html"
    out.write_text(_page("今日场次", "matches.html", body, day), encoding="utf-8")
    return out


def render_scores(results: list[dict], day: str) -> Path:
    """比分子页：全部可算比分分布的未开赛场次，按比分置信度降序。"""
    results = _filter_upcoming(results)
    scored = [
        m for m in results
        if m.get("score_confidence") is not None
    ]
    scored.sort(key=lambda m: m["score_confidence"], reverse=True)

    overview = f"""
<div class="page-hero">
  <h1>比分分析</h1>
  <div class="sub">数据日期 {day} · 可算比分分布 {len(scored)} 场 · 按比分置信度降序</div>
</div>"""

    if scored:
        cards = "".join(_score_card(m, day=day) for m in scored)
        body = overview + f"""
<div class="section-title"><span class="bar"></span>全部比分分析 <span class="section-sub">（{len(scored)} 场）</span></div>
{cards}"""
    else:
        body = overview + '<div class="empty"><div class="icon">⚽</div>暂无比分分布数据<br><span style="font-size:12px">仅有历史数据覆盖的联赛可计算比分分布</span></div>'

    out = Path("docs") / "scores.html"
    out.write_text(_page("比分", "scores.html", body, day), encoding="utf-8")
    return out


def render_calculator(day: str) -> Path:
    """官方计算器子页：iframe 内嵌体彩官方胜平负计算器，并适配桌面显示比例。

    官方页面（m.sporttery.cn/mjc/jsq/zqspf/）为移动端设计，直接新窗口打开会显得
    过窄、比例失调。这里用 iframe 以手机宽度（约 480px）居中嵌入，让其在桌面
    浏览器中保持合理显示比例；同时提供「新窗口打开」按钮兜底（若 iframe 被
    对方策略拦截时仍可跳转官方页面）。
    """
    CALC_URL = "https://m.sporttery.cn/mjc/jsq/zqspf/"

    body = f"""
<div class="page-hero">
  <h1>官方计算器</h1>
  <div class="sub">体彩官方胜平负计算器 · 内容来自中国体彩官网</div>
</div>
<div class="calc-toolbar">
  <a class="calc-open-btn" href="{CALC_URL}" target="_blank" rel="noopener noreferrer">在新窗口打开 ↗</a>
</div>
<div class="calc-frame-wrap">
  <iframe class="calc-frame" src="{CALC_URL}" title="体彩官方胜平负计算器"
    loading="lazy" scrolling="auto"></iframe>
</div>
<div class="disclaimer-sm" style="text-align:center;padding:8px 0 4px">计算器为体彩官方工具，本站仅作展示入口，不构成投注建议。</div>
"""
    # 注入计算器专属样式（追加到页面 CSS）
    extra_css = """
.calc-toolbar { max-width:480px; margin:0 auto 10px; text-align:center; }
.calc-open-btn { display:inline-block; padding:8px 20px; border-radius:20px; background:linear-gradient(135deg,#6366f1,#a855f7); color:#fff; font-size:13px; font-weight:600; text-decoration:none; box-shadow:var(--shadow); }
.calc-open-btn:hover { opacity:.9; }
.calc-frame-wrap { max-width:480px; margin:0 auto; border-radius:12px; overflow:hidden; box-shadow:var(--shadow-lg); border:1px solid var(--border); background:#fff; }
.calc-frame { display:block; width:100%; height:78vh; min-height:560px; border:0; }
@media (max-width:520px) { .calc-frame { height:80vh; min-height:480px; } }
"""

    html = _page("官方计算器", "calculator.html", body, day)
    # 把专属样式插入到 </style> 之前
    html = html.replace("</style>", extra_css + "</style>", 1)

    out = Path("docs") / "calculator.html"
    out.write_text(html, encoding="utf-8")
    return out


def render_finished(day: str) -> Path:
    """已完赛子页：显示「当前比赛日已完赛」+「上一比赛日」的实际比分结果。

    上一比赛日 = date == day-1 的结算记录（结算的 date 即按 11:00 切分的比赛日）。
    今日已完赛 = predict_{day}.json 中 kickoff <= 当前时间 的场次，比分来自结算记录
    或赛果数据源（本地 CSV + football-data 兜底），未取得时显示「待更新」。
    """
    from datetime import timedelta

    try:
        anchor = datetime.strptime(day, "%Y%m%d")
        prev_day = (anchor - timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        prev_day = ""

    settled = _load_settlement()

    def _has_score(s: dict) -> bool:
        """判断结算记录是否含有效比分（排除 NaN / None）。"""
        hg, ag = s.get("home_goals"), s.get("away_goals")
        try:
            return hg is not None and ag is not None and not (
                float(hg) != float(hg) or float(ag) != float(ag)
            )
        except (TypeError, ValueError):
            return False

    # ---------- 1. 上一自然日（昨日）结果 ----------
    prev_finished = [
        s for s in settled
        if str(s.get("date", "")) == prev_day and _has_score(s)
    ]
    prev_finished = sorted(prev_finished, key=lambda s: str(s.get("match_num", "")), reverse=False)

    # ---------- 2. 今日已完赛（已开球）场次 ----------
    today_finished = _today_finished(day, settled)

    prev_disp = f"{prev_day[:4]}-{prev_day[4:6]}-{prev_day[6:]}" if len(prev_day) == 8 else prev_day
    today_disp = f"{day[:4]}-{day[4:6]}-{day[6:]}" if len(day) == 8 else day

    sections = []

    # 今日已完赛区块
    if today_finished:
        cards = "".join(_finished_card(s) for s in today_finished)
        sections.append(f"""
<div class="page-hero">
  <h1>已完赛</h1>
  <div class="sub">今日 {today_disp} 已完赛 {len(today_finished)} 场 · 实际比分结果</div>
</div>
<div class="section-title"><span class="bar"></span>今日已完赛 <span class="section-sub">（{len(today_finished)} 场）</span></div>
{cards}""")
    else:
        sections.append(f"""
<div class="page-hero">
  <h1>已完赛</h1>
  <div class="sub">今日 {today_disp} 暂无已完赛场次</div>
</div>""")

    # 上一自然日区块
    if prev_finished:
        cards = "".join(_finished_card(s) for s in prev_finished)
        sections.append(f"""
<div class="section-title"><span class="bar"></span>上一比赛日 {prev_disp} <span class="section-sub">（{len(prev_finished)} 场）</span></div>
{cards}""")

    if not today_finished and not prev_finished:
        sections.append(f"""
<div class="empty"><div class="icon">🏁</div>暂无比分结果<br><span style="font-size:12px">赛后结算完成后显示</span></div>""")

    body = "".join(sections)

    out = Path("docs") / "finished.html"
    out.write_text(_page("已完赛", "finished.html", body, day), encoding="utf-8")
    return out


def _today_finished(day: str, settled: list[dict]) -> list[dict]:
    """获取「当前比赛日」已完赛场次（含比分、命中标记、开赛前置信度）。

    数据来源优先级：
      1. settlement.csv 中 date == day 且含有效比分的记录（已结算，字段最全）
      2. predict_{day}.json 中 kickoff <= 当前时间、但尚未结算的场次（显示「待更新」）

    返回格式与结算记录一致（home_goals/away_goals 可能为 None）。
    """
    from src.backtest.settle import load_results

    def _has_score(s: dict) -> bool:
        hg, ag = s.get("home_goals"), s.get("away_goals")
        try:
            return hg is not None and ag is not None and not (
                float(hg) != float(hg) or float(ag) != float(ag)
            )
        except (TypeError, ValueError):
            return False

    out: list[dict] = []
    seen_nums: set[str] = set()

    # 1. 今日已结算记录（字段最全：pred_hit / confidence_score / 比分）
    for s in settled:
        if str(s.get("date", "")) != day:
            continue
        if not _has_score(s):
            continue
        num = str(s.get("match_num", ""))
        seen_nums.add(num)
        out.append(s)

    # 2. predict 里已开球但未结算的场次（待更新占位）
    predictions = _load_predict(day)
    now = datetime.now().astimezone()
    finished_preds = [m for m in predictions if _is_finished(m, now=now)]
    for m in finished_preds:
        num = str(m.get("match_num", ""))
        if num in seen_nums:
            continue
        # 尝试从赛果兜底（本地 CSV / 官方接口 / football-data）
        extra = None
        try:
            for r in load_results(day):
                if str(r.get("match_num", "")) == num:
                    extra = r
                    break
        except Exception:  # noqa: BLE001
            extra = None

        if extra is not None and _has_score(extra):
            hg, ag = int(extra["home_goals"]), int(extra["away_goals"])
            p_home, p_draw, p_away = m.get("p_home"), m.get("p_draw"), m.get("p_away")
            if hg > ag:
                truth = "home"
                hit = p_home is not None and p_home >= (p_draw or -1) and p_home >= (p_away or -1)
            elif hg < ag:
                truth = "away"
                hit = p_away is not None and p_away >= (p_home or -1) and p_away >= (p_draw or -1)
            else:
                truth = "draw"
                hit = p_draw is not None and p_draw >= (p_home or -1) and p_draw >= (p_away or -1)
            out.append(
                {
                    "date": day,
                    "match_num": num,
                    "league": m.get("league", ""),
                    "home": m["home"],
                    "away": m["away"],
                    "home_goals": hg,
                    "away_goals": ag,
                    "truth": truth,
                    "pred_hit": hit,
                    "p_home": p_home,
                    "p_draw": p_draw,
                    "p_away": p_away,
                    "confidence_score": m.get("confidence_score"),
                    "source": m.get("source", ""),
                }
            )
        else:
            # 无比分：占位展示
            out.append(
                {
                    "date": day,
                    "match_num": num,
                    "league": m.get("league", ""),
                    "home": m["home"],
                    "away": m["away"],
                    "home_goals": None,
                    "away_goals": None,
                }
            )

    # 按 match_num 排序（中文编号"周日019"按字符串排序可能乱，但数量少可接受）
    return out


def render_settled(day: str) -> Path:
    """已结算场次子页：场次详情（含命中标记），仅显示近 7 日。"""
    settled = _load_settlement()
    if not settled:
        body = f"""
<div class="page-hero">
  <h1>已结算场次</h1>
  <div class="sub">赛后自动对账，记录模型表现 · 近 7 日</div>
</div>
<div class="empty"><div class="icon">📊</div>暂无结算数据<br><span style="font-size:12px">每日赛后自动结算后显示</span></div>"""
        out = Path("docs") / "settled.html"
        out.write_text(_page("已结算场次", "settled.html", body, day), encoding="utf-8")
        return out

    # 近 7 日过滤：结算日期在 [day-6, day] 之间
    from datetime import timedelta

    try:
        anchor = datetime.strptime(day, "%Y%m%d")
    except ValueError:
        anchor = datetime.now()
    cutoff = (anchor - timedelta(days=6)).strftime("%Y%m%d")

    recent = [s for s in settled if str(s.get("date", "")) >= cutoff]
    recent = sorted(recent, key=lambda s: s.get("date", ""), reverse=True)

    # 近 7 日概览
    total = len(recent)
    hits = sum(1 for s in recent if s.get("pred_hit"))
    rate = hits / total if total else 0
    overview = f"""
<div class="page-hero">
  <h1>已结算场次</h1>
  <div class="sub">近 7 日 · 共 {total} 场 · 命中 {hits} 场 · 命中率 {rate*100:.1f}%</div>
</div>
<div class="overview">
  <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">近7日场次</div></div>
  <div class="stat"><div class="stat-num">{hits}</div><div class="stat-label">命中场次</div></div>
  <div class="stat"><div class="stat-num" style="color:#15803d">{rate*100:.1f}%</div><div class="stat-label">近7日命中率</div></div>
  <div class="stat"><div class="stat-num">{len(settled)}</div><div class="stat-label">累计场次</div></div>
</div>
<div class="stat-note">* 命中率是模型表现统计，不代表未来结果、不构成投注建议。</div>"""

    cards = ""
    for s in recent:
        m = {
            "home": s["home"],
            "away": s["away"],
            "league": s.get("league", ""),
            "p_home": s["p_home"],
            "p_draw": s["p_draw"],
            "p_away": s["p_away"],
            "confidence_score": s.get("confidence_score"),
            "source": s.get("source", "dixon_coles"),
            "match_time": s.get("date", ""),
        }
        cards += _match_card(m, settled_meta=s)

    if cards:
        body = overview + f"""
<div class="section-title"><span class="bar"></span>近 7 日场次 <span class="section-sub">（{total} 场，按日期倒序）</span></div>
{cards}"""
    else:
        body = overview + '<div class="empty"><div class="icon">📊</div>近 7 日暂无结算数据</div>'

    out = Path("docs") / "settled.html"
    out.write_text(_page("已结算场次", "settled.html", body, day), encoding="utf-8")
    return out


def render_accuracy(day: str) -> Path:
    """累计命中率子页：命中率 + 命中率 vs 置信度关系。"""
    settled = _load_settlement()

    if not settled:
        body = f"""
<div class="page-hero">
  <h1>累计命中率</h1>
  <div class="sub">命中率与置信度关系分析</div>
</div>
<div class="empty"><div class="icon">📈</div>暂无结算数据</div>"""
        out = Path("docs") / "accuracy.html"
        out.write_text(_page("累计命中率", "accuracy.html", body, day), encoding="utf-8")
        return out

    total = len(settled)
    hits = sum(1 for s in settled if s.get("pred_hit"))
    overall = hits / total if total else 0

    # 按置信度分桶，计算各桶命中率
    import math

    buckets = []
    for lo in range(0, 50, 10):
        hi = lo + 10
        sub = [
            s for s in settled
            if s.get("confidence_score") is not None and lo <= s["confidence_score"] * 100 < hi
        ]
        if sub:
            h = sum(1 for s in sub if s.get("pred_hit"))
            buckets.append(
                {
                    "bin": f"{lo}-{hi}%",
                    "n": len(sub),
                    "hit_rate": h / len(sub) * 100,
                }
            )

    # 概览
    overview = f"""
<div class="page-hero">
  <h1>累计命中率</h1>
  <div class="sub">命中率 = 模型最高概率选项与实际赛果一致的比率</div>
</div>
<div class="overview">
  <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">已结算场次</div></div>
  <div class="stat"><div class="stat-num">{hits}</div><div class="stat-label">命中场次</div></div>
  <div class="stat"><div class="stat-num" style="color:#15803d">{overall*100:.1f}%</div><div class="stat-label">累计命中率</div></div>
  <div class="stat"><div class="stat-num">{len(buckets)}</div><div class="stat-label">置信度分档</div></div>
</div>
<div class="stat-note">* 命中率是模型表现统计，不代表未来结果、不构成投注建议。</div>"""

    # 置信度 vs 命中率 关系（横向条形图，纯 CSS）
    chart_rows = ""
    for b in buckets:
        chart_rows += f"""
  <div class="prob">
    <div class="prob-label">置信度<br>{b['bin']}</div>
    <div class="prob-bar-wrap"><div class="prob-bar" style="width:{b['hit_rate']:.0f}%;background:linear-gradient(90deg,#22c55e,#16a34a)"></div></div>
    <div class="prob-val">{b['hit_rate']:.1f}% <span style="color:#94a3b8;font-weight:400">({b['n']}场)</span></div>
  </div>"""

    chart = f"""
<div class="chart-wrap">
  <div class="chart-title">命中率 vs 模型置信度</div>
  <div class="sub" style="font-size:12px;color:var(--muted);margin-bottom:12px">验证置信度是否与命中率正相关（置信度越高，命中率应越高）</div>
  {chart_rows}
</div>"""

    # 结论提示
    conclusion = ""
    if len(buckets) >= 2:
        # 简单判断单调性
        rates = [b["hit_rate"] for b in buckets]
        if rates == sorted(rates):
            conclusion = '<div class="chart-wrap" style="border-left:4px solid #22c55e"><div class="chart-title" style="color:#15803d">✓ 置信度与命中率呈正相关</div><div style="font-size:13px;color:var(--muted)">模型置信度越高，命中率越高，说明置信度评分有效。</div></div>'
        else:
            conclusion = '<div class="chart-wrap" style="border-left:4px solid #f59e0b"><div class="chart-title" style="color:#92400e">△ 置信度与命中率关系不稳定</div><div style="font-size:13px;color:var(--muted)">样本较少或置信度评分需进一步校准。</div></div>'

    body = overview + chart + conclusion

    out = Path("docs") / "accuracy.html"
    out.write_text(_page("累计命中率", "accuracy.html", body, day), encoding="utf-8")
    return out


# ============================================================
# 今日赛果（实时看板）
# ============================================================

def _load_live_board(day: str) -> list[dict]:
    """读取今日赛果看板数据（data/raw/live_board_{day}.json）。"""
    lp = data_dir("raw") / f"live_board_{day}.json"
    if not lp.exists():
        return []
    return json.loads(lp.read_text(encoding="utf-8")).get("matches", [])


def _live_card(m: dict) -> str:
    """渲染单场「今日赛果」卡片：按状态显示实时/完赛比分或未开球时间。"""
    state = m.get("state", "not_started")
    league = m.get("league", "")
    num = m.get("match_num", "")
    kickoff = m.get("kickoff", "") or m.get("match_date", "")
    half = m.get("half_score", "")

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    if state == "not_started":
        # 未开球：显示开球时间
        badge = '<span class="live-state not_started">未开赛</span>'
        time_disp = kickoff if kickoff else "时间待定"
        score_html = f"""
  <div class="finished-score">
    <div class="team-label">{m.get('home','')}</div>
    <div class="score-num pending-score" style="font-size:20px">vs</div>
    <div class="team-label away">{m.get('away','')}</div>
  </div>
  <div class="live-note">开球 {time_disp}</div>"""
        return f"""
<div class="card">
  <div class="league">{badge}<span class="time">{league} · {num}</span></div>
  {score_html}
</div>"""

    hg = _int(m.get("home_goals"))
    ag = _int(m.get("away_goals"))
    has_score = hg is not None and ag is not None and hg != "" and ag != ""

    if state == "live":
        badge = '<span class="live-state live">进行中</span>'
    else:
        # 完赛，附加胜负方向
        wf = m.get("win_flag", "")
        wf_cn = {"H": "主胜", "D": "平", "A": "客胜"}.get(wf, "")
        badge = f'<span class="live-state finished">已完赛</span>'
        if wf_cn:
            result_badge = {"H": "home", "D": "draw", "A": "away"}.get(wf, "draw")
            badge += f'<span class="result-badge {result_badge}">{wf_cn}</span>'

    if has_score:
        num_cls = "live-num" if state == "live" else ""
        score_html = f"""
  <div class="live-score">
    <div class="team-label">{m.get('home','')}</div>
    <div class="score-num {num_cls}">{hg}</div>
    <div class="score-colon">:</div>
    <div class="score-num {num_cls}">{ag}</div>
    <div class="team-label away">{m.get('away','')}</div>
  </div>"""
        half_html = f'<div class="live-half">半场 {half}</div>' if half and ":" in str(half) else ""
    else:
        score_html = f"""
  <div class="finished-score">
    <div class="team-label">{m.get('home','')}</div>
    <div class="score-num pending-score">-</div>
    <div class="score-colon">:</div>
    <div class="score-num pending-score">-</div>
    <div class="team-label away">{m.get('away','')}</div>
  </div>"""
        half_html = ""

    return f"""
<div class="card">
  <div class="league">{badge}<span class="time">{league} · {num}</span></div>
  {score_html}
  {half_html}
</div>"""


def render_live(day: str) -> Path:
    """今日赛果子页：实时显示当日竞彩全部赛事（进行中/已完赛/未开球）。

    数据来自 fetch_live_board 落盘的 live_board_{day}.json（体彩官方两路接口合并），
    简洁实时：进行中红点闪烁 + 实时比分、已完赛显示比分+胜负、未开球显示开球时间。
    """
    matches = _load_live_board(day)

    live = [m for m in matches if m.get("state") == "live"]
    finished = [m for m in matches if m.get("state") == "finished"]
    not_started = [m for m in matches if m.get("state") == "not_started"]

    day_disp = f"{day[:4]}-{day[4:6]}-{day[6:]}" if len(day) == 8 else day

    sections = []
    hero = f"""
<div class="page-hero">
  <h1>今日赛果</h1>
  <div class="sub">数据日期 {day_disp} · 共 {len(matches)} 场 · 进行中 {len(live)} · 已完赛 {len(finished)} · 未开赛 {len(not_started)}</div>
</div>"""
    sections.append(hero)

    # 进行中（最优先展示）
    if live:
        cards = "".join(_live_card(m) for m in live)
        sections.append(f"""
<div class="section-title"><span class="bar" style="background:linear-gradient(180deg,#ef4444,#dc2626)"></span>进行中 <span class="section-sub">（{len(live)} 场 · 实时比分）</span></div>
{cards}""")

    # 已完赛
    if finished:
        cards = "".join(_live_card(m) for m in finished)
        sections.append(f"""
<div class="section-title"><span class="bar"></span>已完赛 <span class="section-sub">（{len(finished)} 场）</span></div>
<div class="live-grid">{cards}</div>""")

    # 未开球
    if not_started:
        cards = "".join(_live_card(m) for m in not_started)
        sections.append(f"""
<div class="section-title"><span class="bar" style="background:linear-gradient(180deg,#94a3b8,#cbd5e1)"></span>未开赛 <span class="section-sub">（{len(not_started)} 场）</span></div>
<div class="live-grid">{cards}</div>""")

    if not matches:
        sections.append('<div class="empty"><div class="icon">📭</div>暂今日赛果数据<br><span style="font-size:12px">请先运行数据采集</span></div>')

    body = "".join(sections)

    out = Path("docs") / "live.html"
    out.write_text(_page("今日赛果", "live.html", body, day), encoding="utf-8")
    return out


# ============================================================
# 主入口
# ============================================================

def write_json(results: list[dict], day: str) -> Path:
    cfg = load_config()
    payload = {
        "date": day,
        "generated_at": datetime.now().isoformat(),
        "disclaimer": cfg["output"]["disclaimer"],
        "matches": results,
    }
    out = data_dir("results") / f"predict_{day}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def render(results: list[dict], day: str) -> list[Path]:
    """输出层主入口：生成全部页面。"""
    outs = []
    outs.append(write_json(results, day))
    outs.append(render_index(results, day))
    outs.append(render_live(day))
    outs.append(render_matches(results, day))
    outs.append(render_scores(results, day))
    outs.append(render_calculator(day))
    outs.append(render_finished(day))
    outs.append(render_settled(day))
    outs.append(render_accuracy(day))
    return outs


if __name__ == "__main__":
    import sys

    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    jp = data_dir("results") / f"predict_{day}.json"
    if not jp.exists():
        print(f"未找到预测结果 {jp}，请先运行预测")
        sys.exit(1)
    results = json.loads(jp.read_text(encoding="utf-8"))["matches"]
    outs = render(results, day)
    for o in outs:
        print(o)
