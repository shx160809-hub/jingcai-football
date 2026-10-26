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
  --bg: #0a0e1a;
  --bg-grad: radial-gradient(1200px 600px at 20% -10%, rgba(99,102,241,.18), transparent 60%),
             radial-gradient(1000px 500px at 100% 0%, rgba(168,85,247,.14), transparent 55%),
             #0a0e1a;
  --card: #1b2236;
  --card-2: #232c46;
  --text: #e8ecf6;
  --muted: #8b94ad;
  --border: #2a334d;
  --accent: #6366f1;
  --accent-2: #a855f7;
  --gold: #f59e0b;
  --cyan: #22d3ee;
  --shadow: 0 4px 16px rgba(0,0,0,.35);
  --shadow-lg: 0 12px 40px rgba(0,0,0,.5);
  --glow: 0 0 0 1px rgba(99,102,241,.35), 0 8px 30px rgba(99,102,241,.15);
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg-grad); background-attachment:fixed; color:var(--text); line-height:1.6; }

/* 顶部菜单栏 */
.navbar {
  position: sticky; top:0; z-index:100;
  background:rgba(13,17,30,.82);
  backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid rgba(99,102,241,.25);
  box-shadow:0 2px 20px rgba(0,0,0,.4);
}
.navbar-inner { max-width:1080px; margin:0 auto; padding:0 16px; display:flex; align-items:center; justify-content:space-between; height:60px; gap:12px; }
.brand { color:#fff; font-size:17px; font-weight:800; letter-spacing:.5px; text-decoration:none; display:flex; align-items:center; gap:9px; white-space:nowrap; }
.brand .logo { width:30px; height:30px; background:linear-gradient(135deg,#6366f1,#a855f7); border-radius:9px; display:flex; align-items:center; justify-content:center; font-size:16px; box-shadow:0 0 16px rgba(124,58,237,.5); }
.nav-links { display:flex; gap:2px; overflow-x:auto; scrollbar-width:none; }
.nav-links::-webkit-scrollbar { display:none; }
.nav-links a { color:#a7b0c8; text-decoration:none; font-size:13px; padding:8px 12px; border-radius:9px; transition:all .2s; white-space:nowrap; }
.nav-links a:hover { background:rgba(99,102,241,.16); color:#fff; }
.nav-links a.active { background:linear-gradient(135deg,rgba(99,102,241,.9),rgba(168,85,247,.85)); color:#fff; font-weight:600; box-shadow:0 2px 12px rgba(99,102,241,.4); }

.banner { background:linear-gradient(90deg,rgba(245,158,11,.12),rgba(245,158,11,.06)); border-bottom:1px solid rgba(245,158,11,.25); padding:10px 16px; font-size:12px; line-height:1.6; text-align:center; color:#fcd34d; }

.container { max-width:1080px; margin:0 auto; padding:28px 16px 56px; }

/* 页头 */
.page-hero { text-align:center; padding:30px 0 22px; }
.page-hero h1 { font-size:28px; font-weight:800; letter-spacing:.5px; background:linear-gradient(135deg,#a5b4fc,#c4b5fd,#22d3ee); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.page-hero .sub { color:var(--muted); font-size:13px; margin-top:8px; }

/* 概览统计 */
.overview { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:6px; }
.stat { background:linear-gradient(180deg,var(--card-2),var(--card)); border-radius:14px; padding:18px 10px; text-align:center; box-shadow:var(--shadow); border:1px solid var(--border); }
.stat-num { font-size:26px; font-weight:800; color:var(--accent); text-shadow:0 0 20px rgba(99,102,241,.4); }
.stat-label { font-size:11px; color:var(--muted); margin-top:4px; }
.stat-note { font-size:11px; color:#6b748c; text-align:center; margin-bottom:18px; }

/* 分区标题 */
.section-title { display:flex; align-items:center; gap:10px; font-size:17px; font-weight:700; margin:28px 0 14px; color:var(--text); }
.section-title .bar { width:4px; height:20px; background:linear-gradient(180deg,#6366f1,#a855f7); border-radius:2px; box-shadow:0 0 12px rgba(99,102,241,.6); }
.section-sub { font-size:12px; font-weight:400; color:var(--muted); }

/* 卡片 */
.card { background:linear-gradient(180deg,var(--card-2),var(--card)); border-radius:16px; padding:18px; margin-bottom:14px; box-shadow:var(--shadow); border:1px solid var(--border); transition:transform .18s, box-shadow .18s, border-color .18s; }
.card:hover { transform:translateY(-3px); box-shadow:var(--glow); border-color:rgba(99,102,241,.5); }
.card-top { border:1px solid rgba(245,158,11,.55); background:linear-gradient(180deg,rgba(245,158,11,.12),var(--card)); box-shadow:0 0 20px rgba(245,158,11,.15); }
.top-badge { display:inline-flex; align-items:center; gap:4px; background:linear-gradient(135deg,#f59e0b,#f97316); color:#fff; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.league { color:var(--muted); font-size:12px; margin-bottom:10px; display:flex; align-items:center; flex-wrap:wrap; gap:6px; }
.time { color:#6b748c; }
.teams { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
.team { flex:1; text-align:center; }
.team-name { font-size:17px; font-weight:700; color:var(--text); }
.vs { color:#3d4763; font-size:12px; font-weight:700; }
.probs { margin-bottom:8px; }
.prob { display:flex; align-items:center; margin:7px 0; font-size:12px; }
.prob-label { width:38px; color:var(--muted); flex-shrink:0; }
.prob-bar-wrap { flex:1; background:#121828; border-radius:6px; height:14px; overflow:hidden; margin:0 10px; }
.prob-bar { height:100%; border-radius:6px; transition:width .5s ease; }
.prob-val { width:50px; text-align:right; font-weight:700; flex-shrink:0; color:var(--text); }
.score { color:var(--muted); font-size:12px; margin:8px 0; }
.score b { color:var(--text); }
.odds { color:var(--muted); font-size:12px; }
.odds b { color:var(--accent-2); }
.divergence { font-size:12px; color:var(--muted); margin-top:8px; padding-top:8px; border-top:1px dashed var(--border); }
.disclaimer-sm { color:#f87171; font-size:11px; margin-top:10px; }
.tag { font-size:11px; padding:2px 8px; border-radius:10px; }
.tag-dc { background:rgba(99,102,241,.18); color:#a5b4fc; }
.tag-odds { background:rgba(245,158,11,.16); color:#fcd34d; }
.tag-prior { background:rgba(239,68,68,.16); color:#fca5a5; }
.conf { font-size:11px; padding:2px 8px; border-radius:10px; background:rgba(34,197,94,.16); color:#4ade80; }

/* 比分置信度 */
.score-conf { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:2px 8px; border-radius:10px; background:rgba(99,102,241,.18); color:#a5b4fc; }
.card-score { border:1px solid rgba(99,102,241,.55); background:linear-gradient(180deg,rgba(99,102,241,.14),var(--card)); box-shadow:0 0 20px rgba(99,102,241,.18); }
.score-badge { display:inline-flex; align-items:center; gap:4px; background:linear-gradient(135deg,#6366f1,#8b5cf6); color:#fff; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.score-hero { display:flex; align-items:baseline; justify-content:center; gap:12px; margin:12px 0; }
.score-hero .s { font-size:40px; font-weight:800; background:linear-gradient(135deg,#a5b4fc,#c4b5fd); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.score-hero .p { font-size:18px; font-weight:700; color:#a5b4fc; }
.score-detail { text-align:center; color:var(--muted); font-size:12px; }

/* 每日比分推荐卡片（每场 2 个结果） */
.rec-card { border:1px solid rgba(34,197,94,.55); background:linear-gradient(180deg,rgba(34,197,94,.12),var(--card)); box-shadow:0 0 20px rgba(34,197,94,.16); }
.rec-badge { display:inline-flex; align-items:center; gap:4px; background:linear-gradient(135deg,#22c55e,#16a34a); color:#fff; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.rec-rank { display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; background:linear-gradient(135deg,#f59e0b,#f97316); color:#fff; font-size:12px; font-weight:700; border-radius:50%; margin-right:6px; }
.rec-scores { display:flex; justify-content:center; gap:16px; margin:14px 0; }
.rec-score { display:flex; flex-direction:column; align-items:center; gap:4px; min-width:90px; padding:10px 14px; background:#121828; border-radius:12px; border:1px solid rgba(34,197,94,.35); }
.rec-score-num { font-size:30px; font-weight:800; color:#4ade80; letter-spacing:2px; }
.rec-score-p { font-size:13px; font-weight:700; color:#86efac; }

/* 推荐回顾卡片 */
.rec-review-scores { display:flex; flex-wrap:wrap; gap:10px; margin:12px 0; justify-content:center; }
.rec-review-score { padding:8px 14px; background:#121828; border-radius:10px; border:1px solid rgba(99,102,241,.35); font-size:13px; color:var(--muted); }
.rec-review-score b { color:#a5b4fc; font-size:15px; }
.rec-review-actual { text-align:center; color:var(--muted); font-size:13px; margin-top:4px; }
.rec-review-actual b { color:#fca5a5; font-size:16px; }

/* 赛果比分展示 */
.finished-score, .live-score { display:flex; align-items:center; justify-content:center; gap:14px; margin:12px 0; }
.finished-score .team-label, .live-score .team-label { font-size:15px; font-weight:700; flex:1; text-align:right; color:var(--text); }
.finished-score .team-label.away, .live-score .team-label.away { text-align:left; }
.finished-score .score-num, .live-score .score-num { font-size:42px; font-weight:800; letter-spacing:2px; background:linear-gradient(135deg,#e8ecf6,#a5b4fc); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.finished-score .score-colon, .live-score .score-colon { font-size:24px; color:#3d4763; font-weight:700; }
.live-score .score-num { font-size:40px; }
.live-score .score-num.live-num { background:linear-gradient(135deg,#f87171,#ef4444); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
.result-badge { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:3px 10px; border-radius:20px; margin-right:6px; font-weight:600; }
.result-badge.home { background:rgba(239,68,68,.18); color:#fca5a5; }
.result-badge.draw { background:rgba(148,163,184,.16); color:#cbd5e1; }
.result-badge.away { background:rgba(34,197,94,.18); color:#4ade80; }
.result-badge.pending { background:rgba(245,158,11,.16); color:#fcd34d; }
.finished-score .score-num.pending-score { background:linear-gradient(135deg,#6b748c,#3d4763); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }

/* 结算标记 */
.result-hit { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:2px 8px; border-radius:10px; }
.result-hit.hit { background:rgba(34,197,94,.18); color:#4ade80; }
.result-hit.miss { background:rgba(239,68,68,.18); color:#fca5a5; }

/* 结算卡片命中/未命中整卡着色（左色条 + 强边框 + 底色） */
.card-hit {
  border:1px solid rgba(34,197,94,.65);
  border-left:5px solid #22c55e;
  background:linear-gradient(180deg,rgba(34,197,94,.16),rgba(34,197,94,.04) 45%,var(--card));
  box-shadow:0 0 22px rgba(34,197,94,.22);
}
.card-hit:hover { border-color:rgba(34,197,94,.9); box-shadow:0 0 30px rgba(34,197,94,.3); }
.card-miss {
  border:1px solid rgba(239,68,68,.65);
  border-left:5px solid #ef4444;
  background:linear-gradient(180deg,rgba(239,68,68,.18),rgba(239,68,68,.05) 45%,var(--card));
  box-shadow:0 0 22px rgba(239,68,68,.22);
}
.card-miss:hover { border-color:rgba(239,68,68,.9); box-shadow:0 0 30px rgba(239,68,68,.3); }

/* 结算卡片置信度 chip */
.settle-conf { display:inline-flex; align-items:center; gap:4px; font-size:11px; padding:2px 8px; border-radius:10px; background:rgba(99,102,241,.22); color:#c4b5fd; font-weight:600; }

/* 未命中原因说明（彻底分析块） */
.miss-reason {
  margin-top:12px; padding:12px 14px; border-radius:12px;
  font-size:12px; line-height:1.7;
  background:rgba(239,68,68,.10);
  border:1px solid rgba(239,68,68,.45);
  border-left:4px solid #ef4444;
  color:#fecaca;
}
.miss-reason-title { font-size:13px; font-weight:700; color:#f87171; margin-bottom:8px; }
.miss-reason .mr-line { margin:4px 0; }
.miss-reason .mr-line b { color:#fca5a5; font-weight:600; }
.miss-reason .mr-line em { color:#fca5a5; font-style:normal; font-weight:600; }

/* 命中标注（命中=红，未命中=绿） */
.finish-verdict { display:inline-flex; align-items:center; gap:4px; font-size:12px; padding:3px 12px; border-radius:20px; font-weight:700; }
.finish-verdict.hit { background:rgba(239,68,68,.18); color:#f87171; border:1px solid rgba(239,68,68,.35); }
.finish-verdict.miss { background:rgba(34,197,94,.18); color:#4ade80; border:1px solid rgba(34,197,94,.35); }
.finish-meta { display:flex; align-items:center; justify-content:center; flex-wrap:wrap; gap:8px; margin:10px 0 4px; font-size:12px; color:var(--muted); }
.finish-meta .conf-chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; background:rgba(99,102,241,.18); color:#a5b4fc; font-weight:600; }
.finish-meta .truth-chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; background:#121828; color:#cbd5e1; }

/* 今日赛果（实时看板） */
.live-state { display:inline-flex; align-items:center; gap:5px; font-size:11px; padding:3px 10px; border-radius:20px; font-weight:700; margin-right:6px; }
.live-state.live { background:rgba(239,68,68,.18); color:#f87171; }
.live-state.live::before { content:""; width:7px; height:7px; border-radius:50%; background:#ef4444; animation:live-pulse 1.2s infinite; }
@keyframes live-pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
.live-state.finished { background:rgba(99,102,241,.18); color:#a5b4fc; }
.live-state.not_started { background:#121828; color:#6b748c; }
.live-half { text-align:center; color:var(--muted); font-size:11px; }
.live-note { text-align:center; color:#6b748c; font-size:11px; margin-top:8px; }
.live-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }
.live-grid .card { margin-bottom:0; }
@media (max-width:640px) { .live-grid { grid-template-columns:1fr; } }

/* 图表容器 */
.chart-wrap { background:linear-gradient(180deg,var(--card-2),var(--card)); border-radius:16px; padding:20px; box-shadow:var(--shadow); border:1px solid var(--border); margin-bottom:16px; }
.chart-title { font-size:15px; font-weight:700; margin-bottom:12px; color:var(--text); }

/* 空状态 */
.empty { text-align:center; color:var(--muted); padding:60px 20px; }
.empty .icon { font-size:40px; margin-bottom:12px; }

.footer { text-align:center; color:#6b748c; font-size:12px; padding:24px 16px 40px; line-height:1.8; }

@media (max-width:640px) {
  .overview { grid-template-columns:repeat(2,1fr); }
  .nav-links a { font-size:12px; padding:7px 9px; }
  .team-name { font-size:15px; }
  .navbar-inner { height:56px; }
}
"""


def _nav(active: str) -> str:
    """渲染顶部菜单栏。items 为 (href, label, 是否外链新窗口)。"""
    items = [
        ("index.html", "首页", False),
        ("matches.html", "今日场次", False),
        ("scores.html", "比分推荐", False),
        ("live.html", "今日赛果", False),
        ("recommend.html", "推荐回顾", False),
        ("settled.html", "结算场次", False),
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
    if src in ("market_implied", "market_implied+dc"):
        return '<span class="tag tag-odds">市场概率</span>'
    if src in ("dixon_coles", "dixon_coles+elo"):
        return '<span class="tag tag-dc">Dixon-Coles</span>'
    if src == "odds_implied":
        return '<span class="tag tag-odds">市场隐含概率</span>'
    return '<span class="tag tag-prior">均匀先验</span>'


def _conf_tag(m: dict) -> str:
    conf = m.get("confidence_score")
    if conf is None:
        return ""
    try:
        # 防御 NaN（float('nan') 不是 None，但乘 100 会显示成 nan%）
        if conf != conf:  # NaN 自比较为 False
            return ""
        return f'<span class="conf">置信度 {conf*100:.0f}%</span>'
    except (TypeError, ValueError):
        return ""


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


def _miss_reason(s: dict) -> str:
    """根据结算数据生成未命中原因的彻底分析（含置信度、概率分布、爆冷程度、赔率佐证）。"""
    truth = s.get("truth", "")
    p_home = s.get("p_home")
    p_draw = s.get("p_draw")
    p_away = s.get("p_away")

    # 无预测场次（概率缺失）
    if p_home is None and p_draw is None and p_away is None:
        return (
            '<div class="mr-line">该场次赛前无有效预测概率（模型未覆盖该联赛/数据缺失），'
            "无法参与命中判定。</div>"
        )

    try:
        p_h = float(p_home or 0)
        p_d = float(p_draw or 0)
        p_a = float(p_away or 0)
    except (TypeError, ValueError):
        return '<div class="mr-line">预测概率数据异常，无法还原预测方向。</div>'

    pred = {"home": "主胜", "draw": "平局", "away": "客胜"}
    prob_map = {"home": p_h, "draw": p_d, "away": p_a}
    pred_key = max(prob_map, key=prob_map.get)
    pred_prob = prob_map[pred_key]

    truth_cn = pred.get(truth, "")
    home = s.get("home", "")
    away = s.get("away", "")

    # 实际比分
    hg = s.get("home_goals", "")
    ag = s.get("away_goals", "")
    score_str = ""
    try:
        if hg is not None and ag is not None and str(hg) != "" and str(ag) != "":
            score_str = f"{int(float(hg))}:{int(float(ag))}"
    except (TypeError, ValueError):
        score_str = ""

    # 实际结果在模型概率分布中的位置（爆冷程度）
    actual_prob = prob_map.get(truth, 0.0)
    if actual_prob >= 0.5:
        upset = "实际结果本就属于概率前三的高频区间，属正常兑现，模型方向判断偏差。"
    elif actual_prob >= 0.3:
        upset = f"实际结果「{truth_cn}」在模型中仅占 {actual_prob*100:.0f}%，属中等冷门。"
    elif actual_prob >= 0.15:
        upset = f"实际结果「{truth_cn}」在模型中仅占 {actual_prob*100:.0f}%，属明显冷门。"
    else:
        upset = f"实际结果「{truth_cn}」在模型中仅占 {actual_prob*100:.0f}%，属大幅爆冷，市场与模型均未料及。"

    # 按预测方向置信度归因
    if pred_prob >= 0.6:
        core = (
            f'模型对「{pred[pred_key]}」给出 <b>{pred_prob*100:.0f}%</b> 的高置信度，'
            f"但实际结果是「{truth_cn}」，属典型的<em>强队未兑现 / 爆冷</em>。"
        )
    elif pred_prob >= 0.4:
        core = (
            f'预测方向「{pred[pred_key]}」概率仅 <b>{pred_prob*100:.0f}%</b>，'
            f"与其余选项差距有限，属<em>中等置信下的方向性偏差</em>。"
        )
    else:
        core = (
            f'最高概率项「{pred[pred_key]}」仅 <b>{pred_prob*100:.0f}%</b>，'
            f"三选项接近均势，赛果落在次选，属<em>胶着局面的正常波动</em>。"
        )

    # 市场赔率佐证
    mkt_note = ""
    try:
        had_w = float(s.get("had_win") or 0)
        had_d = float(s.get("had_draw") or 0)
        had_l = float(s.get("had_lose") or 0)
        # 预测方向对应赔率
        odd_map = {"home": had_w, "draw": had_d, "away": had_l}
        pred_odd = odd_map.get(pred_key, 0)
        if pred_odd > 0 and pred_odd < 1.5:
            mkt_note = f"市场同样高度看好{pred[pred_key]}（低赔 {pred_odd:.2f}），属一致看好的冷门。"
        elif pred_odd >= 2.0:
            mkt_note = f"市场对{pred[pred_key]}并非一边倒（赔率 {pred_odd:.2f}），模型与市场判断分歧有限。"
    except (TypeError, ValueError):
        pass

    # 组装 HTML（多行、分块，更彻底）
    lines = []
    lines.append(
        f'<div class="mr-line"><b>预测方向：</b>{home} vs {away} 看好「{pred[pred_key]}」'
        f'（{pred_prob*100:.0f}%）</div>'
    )
    lines.append(
        f'<div class="mr-line"><b>概率分布：</b>主胜 {p_h*100:.0f}% / 平局 {p_d*100:.0f}% / 客胜 {p_a*100:.0f}%</div>'
    )
    lines.append(f'<div class="mr-line"><b>实际赛果：</b>「{truth_cn}」{("　比分 " + score_str) if score_str else ""}</div>')
    lines.append(f'<div class="mr-line"><b>归因：</b>{core}</div>')
    lines.append(f'<div class="mr-line"><b>爆冷程度：</b>{upset}</div>')
    if mkt_note:
        lines.append(f'<div class="mr-line"><b>赔率佐证：</b>{mkt_note}</div>')

    return "".join(lines)


def _match_card(m: dict, top: bool = False, settled_meta: dict | None = None, day: str = "") -> str:
    """渲染单场比赛卡片。settled_meta 用于已结算页（含命中标记）。"""
    # p_home 等可能为 None（无预测场次），做安全兜底避免 NaN/None 计算报错
    p_h = (m["p_home"] or 0) * 100
    p_d = (m["p_draw"] or 0) * 100
    p_a = (m["p_away"] or 0) * 100
    scores = m.get("top_scores", [])
    top_str = "　".join(f"{s['score']} {s['prob']*100:.1f}%" for s in scores[:3])

    # 结算标记（命中/未命中整卡着色 + 原因说明 + 预测把握）
    settle_html = ""
    card_settle_cls = ""
    miss_reason_html = ""
    if settled_meta is not None:
        hit = settled_meta.get("pred_hit")
        truth_cn = {"home": "主胜", "draw": "平局", "away": "客胜"}.get(
            settled_meta.get("truth", ""), ""
        )
        if hit is True:
            cls = "hit"
            label = "✓ 命中"
            card_settle_cls = " card-hit"
        elif hit is False:
            cls = "miss"
            label = "✗ 未命中"
            card_settle_cls = " card-miss"
            miss_reason_html = _miss_reason(settled_meta)
        else:
            # pred_hit 为 None（无预测场次）
            cls = "miss"
            label = "— 无预测"
            card_settle_cls = " card-miss"
            miss_reason_html = _miss_reason(settled_meta)

        # 预测把握 = 预测方向（最高概率项）的概率，直观表达"模型当时有多大把握"
        conf_chip = ""
        try:
            _ph = float(settled_meta.get("p_home") or 0)
            _pd = float(settled_meta.get("p_draw") or 0)
            _pa = float(settled_meta.get("p_away") or 0)
            _pk = {"home": "主胜", "draw": "平局", "away": "客胜"}
            _pm = {"home": _ph, "draw": _pd, "away": _pa}
            _top_key = max(_pm, key=_pm.get)
            _top_prob = _pm[_top_key]
            if (_ph + _pd + _pa) > 0:
                conf_chip = (
                    f'<span class="settle-conf">预测把握 {_pk[_top_key]} {_top_prob*100:.0f}%</span>'
                )
        except (TypeError, ValueError):
            conf_chip = ""

        settle_html = (
            f'<span class="result-hit {cls}">{label}</span>'
            f'<span class="tag" style="background:#121828;color:#cbd5e1">实际：{truth_cn}</span>'
            f"{conf_chip}"
        )

    # 未命中原因块（含置信度行）
    if miss_reason_html:
        miss_reason_html = f'<div class="miss-reason"><div class="miss-reason-title">📉 未命中原因分析</div>{miss_reason_html}</div>'

    # 分歧
    divergence_html = ""
    if m.get("divergence") is not None:
        gap = m["model_market_gap"]
        arrow = "↑" if gap > 0 else "↓"
        color = "#f87171" if abs(gap) > 0.08 else "#8b94ad"
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
<div class="card{card_settle_cls}{' card-top' if top else ''}">
  <div class="league">{top_badge}{m.get('league','')} <span class="time">{_fmt_time(m, day)}</span> {_tag(m)} {'' if settled_meta is not None else _conf_tag(m)} {settle_html}</div>
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
  {miss_reason_html}
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


def _score_rec_card(m: dict, rank: int = 0, day: str = "") -> str:
    """渲染「每日比分推荐」卡片：每场突出 2 个推荐比分结果。

    取 top_scores 前 2 个作为推荐比分，各自标注概率；额外给 3 个比分参考。
    """
    top = m.get("top_scores") or []
    if len(top) < 2:
        return ""
    s1, s2 = top[0], top[1]
    rank_badge = f'<span class="rec-rank">#{rank}</span>' if rank else ""
    ref = "　".join(f"{s['score']} {s['prob']*100:.1f}%" for s in top[2:5])
    return f"""
<div class="card card-score rec-card">
  <div class="league">{rank_badge}<span class="rec-badge">比分推荐</span>{m.get('league','')} <span class="time">{_fmt_time(m, day)}</span> {_tag(m)}</div>
  <div class="teams">
    <div class="team"><div class="team-name">{m['home']}</div></div>
    <div class="vs">VS</div>
    <div class="team"><div class="team-name">{m['away']}</div></div>
  </div>
  <div class="rec-scores">
    <div class="rec-score">
      <span class="rec-score-num">{s1['score']}</span>
      <span class="rec-score-p">{s1['prob']*100:.1f}%</span>
    </div>
    <div class="rec-score">
      <span class="rec-score-num">{s2['score']}</span>
      <span class="rec-score-p">{s2['prob']*100:.1f}%</span>
    </div>
  </div>
  <div class="score-detail">参考比分：{ref}</div>
  <div class="disclaimer-sm">模型参考值，不构成投注建议</div>
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
    """读取结算记录（含置信度、命中标记）。

    数据源优先级：match_results.csv（统一赛果数据库，归档最全，含回填的
    市场去水概率命中标记）为主，settlement.csv（开赛前预测快照，含置信度）
    兜底补全置信度字段。两者按 (date, match_num) 去重合并，避免命中数据
    因预测文件丢失而缺失展示。
    """
    import pandas as pd

    db_path = data_dir("results") / "match_results.csv"
    sp = data_dir("results") / "settlement.csv"

    if not db_path.exists() and not sp.exists():
        return []

    frames = []
    if db_path.exists():
        db = pd.read_csv(db_path)
        if not db.empty:
            frames.append(db)
    if sp.exists():
        st = pd.read_csv(sp)
        if not st.empty:
            frames.append(st)

    if not frames:
        return []

    # 统一 date 为字符串，避免 int/str 混合导致日期比较/去重失效
    for f in frames:
        if "date" in f.columns:
            f["date"] = f["date"].astype(str)

    # 合并：match_results 为主，settlement 用其置信度字段补全
    if len(frames) == 1:
        df = frames[0]
    else:
        db, st = frames[0], frames[1]
        # 以 match_results 为主表，settlement 补 confidence_score
        cols_to_fill = [c for c in st.columns if c in db.columns and c not in ("date", "match_num")]
        df = db.copy()
        if "confidence_score" in cols_to_fill:
            # 按 (date, match_num) 用 settlement 的置信度补全
            conf_map = st.dropna(subset=["confidence_score"]).set_index(["date", "match_num"])["confidence_score"].to_dict()
            df["confidence_score"] = df.apply(
                lambda r: conf_map.get((r["date"], r["match_num"]), r.get("confidence_score")), axis=1
            )
        # 去重：match_results 优先，settlement 里 match_results 没有的场次也保留
        db_keys = set(zip(df["date"], df["match_num"]))
        extra = st[~st.apply(lambda r: (r["date"], r["match_num"]) in db_keys, axis=1)]
        if not extra.empty:
            df = pd.concat([df, extra], ignore_index=True)

    # pred_hit 统一为 bool（None/NaN 除外，保留为 None 表示"无预测"）
    if "pred_hit" in df.columns:
        df["pred_hit"] = df["pred_hit"].map(lambda v: bool(v) if pd.notna(v) else None)

    # 先转成 records，再在 Python 层面把 NaN 转 None。
    # 直接在 DataFrame 上赋值会被 pandas 反复做 dtype 推断，None 又被转回 NaN。
    records = df.to_dict("records")
    for r in records:
        for col in ("confidence_score", "p_home", "p_draw", "p_away"):
            if col in r:
                try:
                    if pd.isna(r[col]):
                        r[col] = None
                except (TypeError, ValueError):
                    pass
    return records


# ============================================================
# 各页面渲染
# ============================================================

def render_index(results: list[dict], day: str) -> Path:
    """主页：每日比分推荐（4-5 场 × 2 结果）+ 胜平负 Top5（剔除已完赛场次）。

    推荐场数规则：当日竞彩比赛超过 5 场取 5 场，不足 5 场取当日全部。
    （top_n_by_confidence / top_n_by_score_confidence 的切片天然满足 min(5, 全部)。）
    """
    from src.output.confidence import top_n_by_confidence, top_n_by_score_confidence

    # 剔除已开球场次
    results = _filter_upcoming(results)

    # 胜平负 Top5：置信度最高的 min(5, 当日未开赛场次数)
    top5 = top_n_by_confidence(results, 5)
    # 比分推荐候选：有比分分布场次中比分置信度最高的 min(5, 有分布场次数)
    top_score = top_n_by_score_confidence(results, 5)

    sections = []

    # 每日比分推荐（置顶，每场 2 个结果；需至少 2 个比分）
    score_candidates = [m for m in top_score if len(m.get("top_scores") or []) >= 2]
    if score_candidates:
        score_cards = "".join(
            _score_rec_card(m, rank=i + 1, day=day)
            for i, m in enumerate(score_candidates[:5])
        )
        sections.append(f"""
<div class="section-title"><span class="bar"></span>每日比分推荐 <span class="section-sub">（{len(score_candidates[:5])} 场 · 每场 2 个比分结果）</span></div>
{score_cards}""")

    # 胜平负 Top5
    if top5:
        cards = "".join(_match_card(m, top=True, day=day) for m in top5)
        sections.append(f"""
<div class="section-title"><span class="bar"></span>胜平负 Top5 <span class="section-sub">（模型观点最明确的 {len(top5)} 场）</span></div>
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
    # 「模型覆盖」= 有 DC 比分分布/期望进球的场次（source 含 dc 或 model_p_* 存在）
    n_dc = sum(
        1 for m in results
        if "dc" in m.get("source", "") or m.get("model_p_home") is not None
    )
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
    """比分推荐子页：全部可算比分分布的未开赛场次，按比分置信度降序。"""
    results = _filter_upcoming(results)
    scored = [
        m for m in results
        if m.get("score_confidence") is not None
    ]
    scored.sort(key=lambda m: m["score_confidence"], reverse=True)

    overview = f"""
<div class="page-hero">
  <h1>比分推荐</h1>
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
    out.write_text(_page("比分推荐", "scores.html", body, day), encoding="utf-8")
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


def _rec_score_hit(r: dict) -> bool | None:
    """比分推荐命中判定：实际比分是否命中推荐的 2 个比分之一。"""
    hg = r.get("home_goals")
    ag = r.get("away_goals")
    if hg is None or ag is None:
        return None  # 无赛果
    try:
        actual = f"{int(hg)}-{int(ag)}"
    except (TypeError, ValueError):
        return None
    s1 = str(r.get("score_1") or "")
    s2 = str(r.get("score_2") or "")
    return actual in (s1, s2)


def _rec_top5_hit(r: dict) -> bool | None:
    """胜平负 Top5 命中判定：实际方向是否等于预测最高概率方向。"""
    truth = r.get("truth")
    if not truth:
        # 尝试从比分反推
        hg = r.get("home_goals")
        ag = r.get("away_goals")
        if hg is None or ag is None:
            return None
        truth = "home" if hg > ag else ("away" if hg < ag else "draw")
    ph = r.get("pred_home")
    pd_ = r.get("pred_draw")
    pa = r.get("pred_away")
    if ph is None or pd_ is None or pa is None:
        return None
    probs = {"home": ph, "draw": pd_, "away": pa}
    pred = max(probs, key=probs.get)
    return pred == truth


def _rec_card(r: dict) -> str:
    """渲染单条推荐回顾卡片（含推荐内容 + 实际赛果 + 命中标记）。"""
    rec_type = r.get("rec_type", "")
    home = r.get("home", "")
    away = r.get("away", "")
    date = str(r.get("date", ""))

    # 命中判定
    if rec_type == "score":
        hit = _rec_score_hit(r)
    else:
        hit = _rec_top5_hit(r)

    # 命中标记
    if hit is None:
        verdict = '<span class="result-hit miss" style="background:rgba(148,163,184,.16);color:#cbd5e1">⏳ 待赛果</span>'
        card_cls = ""
    elif hit:
        verdict = '<span class="result-hit hit">✓ 命中</span>'
        card_cls = " card-hit"
    else:
        verdict = '<span class="result-hit miss">✗ 未命中</span>'
        card_cls = " card-miss"

    # 推荐内容
    if rec_type == "score":
        s1 = str(r.get("score_1") or "")
        s2 = str(r.get("score_2") or "")
        rec_content = (
            f'<div class="rec-review-scores">'
            f'<span class="rec-review-score">推荐 <b>{s1}</b>（{_pct(r.get("score_1_prob"))}）</span>'
            f'<span class="rec-review-score">推荐 <b>{s2}</b>（{_pct(r.get("score_2_prob"))}）</span>'
            f'</div>'
        )
        badge = '<span class="rec-badge">比分推荐</span>'
    else:
        probs = {"主胜": r.get("pred_home"), "平局": r.get("pred_draw"), "客胜": r.get("pred_away")}
        # 排除 None/NaN 后取最高概率方向
        valid = {k: v for k, v in probs.items() if v is not None and v == v}
        if valid:
            pred_key = max(valid, key=valid.get)
            pred_prob = valid[pred_key]
        else:
            pred_key, pred_prob = "—", None
        rec_content = f'<div class="rec-review-scores"><span class="rec-review-score">预测方向 <b>{pred_key}</b>（{_pct(pred_prob)}）</span></div>'
        badge = '<span class="rec-badge" style="background:linear-gradient(135deg,#6366f1,#8b5cf6)">胜平负 Top5</span>'

    # 实际赛果
    hg = r.get("home_goals")
    ag = r.get("away_goals")
    if hg is None or ag is None:
        actual = "—"
    else:
        actual = f"{int(hg)}:{int(ag)}"

    return f"""
<div class="card{card_cls}">
  <div class="league">{badge}{r.get('league','')} <span class="time">{date}</span> {verdict}</div>
  <div class="teams">
    <div class="team"><div class="team-name">{home}</div></div>
    <div class="vs">VS</div>
    <div class="team"><div class="team-name">{away}</div></div>
  </div>
  {rec_content}
  <div class="rec-review-actual">实际比分：<b>{actual}</b></div>
</div>"""


def _pct(v) -> str:
    """安全格式化百分比（None/NaN 返回 '—'）。"""
    try:
        if v is None:
            return "—"
        f = float(v)
        if f != f:  # NaN
            return "—"
        return f"{f*100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def render_recommend(day: str) -> Path:
    """推荐回顾子页：近 7 日推荐的历史赛果 + 命中标记 + 累计命中统计。"""
    from src.strategy.recommend import load_recent

    recs = load_recent(day, days=7)

    if not recs:
        body = f"""
<div class="page-hero">
  <h1>推荐回顾</h1>
  <div class="sub">近 7 日推荐赛果与命中情况</div>
</div>
<div class="empty"><div class="icon">🎯</div>暂无推荐记录<br><span style="font-size:12px">每日生成推荐后自动累积</span></div>"""
        out = Path("docs") / "recommend.html"
        out.write_text(_page("推荐回顾", "recommend.html", body, day), encoding="utf-8")
        return out

    # 统计（只统计已有赛果的推荐）
    settled = [r for r in recs if r.get("home_goals") is not None and r.get("away_goals") is not None]
    score_recs = [r for r in settled if r.get("rec_type") == "score"]
    top5_recs = [r for r in settled if r.get("rec_type") == "top5"]

    def _hit_rate(items, hit_fn):
        if not items:
            return None
        hits = sum(1 for r in items if hit_fn(r) is True)
        return hits / len(items)

    score_rate = _hit_rate(score_recs, _rec_score_hit)
    top5_rate = _hit_rate(top5_recs, _rec_top5_hit)

    overview = f"""
<div class="page-hero">
  <h1>推荐回顾</h1>
  <div class="sub">近 7 日 · 比分推荐 {len(score_recs)} 场 · 胜平负 Top5 {len(top5_recs)} 场</div>
</div>
<div class="overview">
  <div class="stat"><div class="stat-num">{len(recs)}</div><div class="stat-label">近7日推荐</div></div>
  <div class="stat"><div class="stat-num">{_pct(score_rate)}</div><div class="stat-label">比分命中率</div></div>
  <div class="stat"><div class="stat-num">{_pct(top5_rate)}</div><div class="stat-label">胜平负命中率</div></div>
  <div class="stat"><div class="stat-num">{len(settled)}</div><div class="stat-label">已出赛果</div></div>
</div>
<div class="stat-note">* 命中率是模型表现统计，不代表未来结果、不构成投注建议。</div>"""

    cards = "".join(_rec_card(r) for r in recs)
    body = overview + f"""
<div class="section-title"><span class="bar"></span>近 7 日推荐 <span class="section-sub">（{len(recs)} 条，按日期倒序）</span></div>
{cards}"""

    out = Path("docs") / "recommend.html"
    out.write_text(_page("推荐回顾", "recommend.html", body, day), encoding="utf-8")
    return out



def render_settled(day: str) -> Path:
    """已结算场次子页：场次详情（含命中标记），仅显示近 7 日。"""
    settled = _load_settlement()
    if not settled:
        body = f"""
<div class="page-hero">
  <h1>结算场次</h1>
  <div class="sub">赛后自动对账，记录模型表现 · 近 7 日</div>
</div>
<div class="empty"><div class="icon">📊</div>暂无结算数据<br><span style="font-size:12px">每日赛后自动结算后显示</span></div>"""
        out = Path("docs") / "settled.html"
        out.write_text(_page("结算场次", "settled.html", body, day), encoding="utf-8")
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
    # 命中率分母排除"无预测"场次（pred_hit 为 None）
    scored = [s for s in recent if s.get("pred_hit") is not None]
    rate = hits / len(scored) if scored else 0
    overview = f"""
<div class="page-hero">
  <h1>结算场次</h1>
  <div class="sub">近 7 日 · 共 {total} 场 · 命中 {hits} 场 · 命中率 {rate*100:.1f}%</div>
</div>
<div class="overview">
  <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">近7日场次</div></div>
  <div class="stat"><div class="stat-num">{hits}</div><div class="stat-label">命中场次</div></div>
  <div class="stat"><div class="stat-num" style="color:#4ade80">{rate*100:.1f}%</div><div class="stat-label">近7日命中率</div></div>
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
    out.write_text(_page("结算场次", "settled.html", body, day), encoding="utf-8")
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
    # 命中率分母应排除"无预测"场次（pred_hit 为 None，无法判定命中与否）
    scored = [s for s in settled if s.get("pred_hit") is not None]
    overall = hits / len(scored) if scored else 0

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
  <div class="stat"><div class="stat-num" style="color:#4ade80">{overall*100:.1f}%</div><div class="stat-label">累计命中率</div></div>
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
    <div class="prob-val">{b['hit_rate']:.1f}% <span style="color:#8b94ad;font-weight:400">({b['n']}场)</span></div>
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
            conclusion = '<div class="chart-wrap" style="border-left:4px solid #22c55e"><div class="chart-title" style="color:#4ade80">✓ 置信度与命中率呈正相关</div><div style="font-size:13px;color:var(--muted)">模型置信度越高，命中率越高，说明置信度评分有效。</div></div>'
        else:
            conclusion = '<div class="chart-wrap" style="border-left:4px solid #f59e0b"><div class="chart-title" style="color:#fcd34d">△ 置信度与命中率关系不稳定</div><div style="font-size:13px;color:var(--muted)">样本较少或置信度评分需进一步校准。</div></div>'

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
    outs.append(render_recommend(day))
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
