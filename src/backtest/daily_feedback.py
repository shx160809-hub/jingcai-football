"""每日数据回馈闭环：归档赛果 → 回写历史 → 重拟合 → 校准 → 回测对比。

每天 11:00（前一比赛日全部结束）自动执行：
  1. 归档前一比赛日赛果到统一赛果数据库 match_results.csv。
  2. 把中文赛果回写历史库，扩展训练数据。
  3. 拟合概率校准映射（用截至当前的全部结算样本）。
  4. 跑 walk-forward 回测，得到「重拟合后」的命中率与 Log Loss。
  5. 与历史基线对比，输出命中率变化报告（落盘 docs/feedback.html + JSON）。

合规：本模块只做统计与模型评估，输出命中率变化供参考，不构成投注建议。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd

from src.common import data_dir, load_config


def _yesterday(day: str | None = None) -> str:
    """返回前一比赛日（YYYYMMDD）。"""
    anchor = datetime.strptime(day, "%Y%m%d") if day else datetime.now()
    return (anchor - timedelta(days=1)).strftime("%Y%m%d")


def archive_previous_day(day: str | None = None, sync_history: bool = True) -> dict:
    """归档前一比赛日的赛果（每天 11:00 调用）。

    sync_history 默认 True：把中文赛果（亚洲联赛）回写历史库，扩展训练数据。
    """
    prev = _yesterday(day)
    from src.backtest.results_db import archive_day

    return archive_day(prev, sync_history=sync_history)


def refit_and_backtest() -> dict:
    """用最新历史库重拟合模型，跑 walk-forward 回测，返回指标。"""
    from src.backtest.walk_forward import walk_forward_backtest

    hist_path = data_dir("raw") / "history.csv"
    if not hist_path.exists():
        return {"error": "无历史数据"}
    hist = pd.read_csv(hist_path, parse_dates=["date"])
    metrics = walk_forward_backtest(hist, verbose=False)
    return metrics


def check_unmapped_teams(day: str | None = None) -> list[dict]:
    """扫描今日预测，列出「有历史数据联赛但队名映射失败」的场次。

    队名映射失败会导致 Dixon-Coles 模型用中文名查英文参数而静默失效，
    这些场次会退化为 market_implied（无 DC 比分分布）。列出后便于批量补映射。

    返回 [{"league", "home", "away", "home_mapped", "away_mapped"}] 列表。
    """
    import json

    from src.common import ROOT

    day = day or datetime.now().strftime("%Y%m%d")
    team_map = json.loads(
        (ROOT / "config" / "team_name_map.json").read_text(encoding="utf-8")
    )

    # 读取今日预测（若无则跳过）
    pred_path = data_dir("results") / f"predict_{day}.json"
    if not pred_path.exists():
        return []
    preds = json.loads(pred_path.read_text(encoding="utf-8")).get("matches", [])

    # 有历史数据覆盖的联赛（这些联赛才需要英文名映射）
    cfg = load_config()
    covered_codes = set(cfg["collect"].get("league_name_map", {}).values())

    unmapped = []
    for p in preds:
        league_cn = p.get("league", "")
        code = cfg["collect"].get("league_name_map", {}).get(league_cn)
        # 只关注有历史数据覆盖的联赛（无覆盖的联赛本来就走去水，无需映射）
        if not code or code not in covered_codes:
            continue
        league_team_map = team_map.get(league_cn, {})
        home_mapped = p.get("home", "") in league_team_map
        away_mapped = p.get("away", "") in league_team_map
        if not (home_mapped and away_mapped):
            unmapped.append({
                "league": league_cn,
                "home": p.get("home", ""),
                "away": p.get("away", ""),
                "home_mapped": home_mapped,
                "away_mapped": away_mapped,
            })
    return unmapped


def _load_baseline() -> dict | None:
    """读取上次回测基线（若存在）。"""
    bp = data_dir("results") / "backtest_baseline.json"
    if not bp.exists():
        return None
    return json.loads(bp.read_text(encoding="utf-8"))


def _save_baseline(metrics: dict) -> None:
    """保存当前回测指标为新的基线。"""
    bp = data_dir("results") / "backtest_baseline.json"
    payload = {
        "updated_at": datetime.now().isoformat(),
        "metrics": {k: v for k, v in metrics.items() if k != "calibration"},
    }
    bp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_daily_feedback(day: str | None = None) -> dict:
    """执行完整每日闭环，返回报告摘要。"""
    print("===== 每日数据回馈闭环启动 =====")

    # 0. 先结算（生成 settlement.csv 开赛前预测快照），确保归档时能匹配到预测记录。
    #    否则 match_results.csv 的 pred_hit 会因缺预测记录而全为 nan。
    print("[0/5] 结算昨日与今日已完赛场次（生成对账快照）...")
    try:
        from src.backtest.settle import settle_recent

        settled = settle_recent()
        print(f"      结算 {len(settled)} 场")
    except Exception as e:  # noqa: BLE001
        print(f"      ⚠️ 结算失败：{e}")

    # 1. 归档前一比赛日赛果
    print("[1/5] 归档前一比赛日赛果...")
    archive = archive_previous_day(day)
    print(f"      归档 {archive.get('n_archived', 0)} 场")

    # 2. 拟合概率校准
    print("[2/5] 拟合概率校准...")
    from src.backtest.calibrate import load_calibration, _evaluate_calibration_gain

    calib = load_calibration()
    print(f"      校准方向 {len(calib)} 个（{list(calib.keys())}）")

    # 校准带来的命中率提升（out-of-sample 验证）
    calib_gain = _evaluate_calibration_gain(calib) if calib else None
    if calib_gain:
        print(
            f"      校准前后 Top-1：{calib_gain['before']*100:.2f}% → "
            f"{calib_gain['after']*100:.2f}%（{calib_gain['delta']*100:+.2f}%）"
        )

    # 3. 重拟合 + walk-forward 回测
    print("[3/5] 重拟合模型并回测...")
    metrics = refit_and_backtest()
    if "error" in metrics:
        print(f"      回测失败：{metrics['error']}")
        return {"status": "error", "error": metrics["error"]}

    print(f"      Log Loss {metrics['log_loss']:.4f} · Top-1 {metrics['top1_accuracy']*100:.1f}%")

    # 4. 与基线对比
    baseline = _load_baseline()
    delta = {}
    if baseline:
        bm = baseline["metrics"]
        for k in ["log_loss", "rps", "brier_score", "top1_accuracy"]:
            if k in bm and k in metrics:
                delta[k] = {
                    "baseline": round(float(bm[k]), 4),
                    "current": round(float(metrics[k]), 4),
                    "change": round(float(metrics[k]) - float(bm[k]), 4),
                }
    else:
        print("      无基线，本次作为初始基线")

    # 4.5 队名映射告警（列出 DC 模型静默失效的场次）
    unmapped = check_unmapped_teams(day)
    if unmapped:
        print(f"      ⚠️ 队名映射缺失 {len(unmapped)} 场（DC 模型会静默失效）：")
        for u in unmapped:
            miss = []
            if not u["home_mapped"]:
                miss.append(u["home"])
            if not u["away_mapped"]:
                miss.append(u["away"])
            print(f"        {u['league']} {u['home']} vs {u['away']} — 缺映射：{'、'.join(miss)}")

    # 5. 保存基线 + 报告
    _save_baseline(metrics)
    report = {
        "date": day or datetime.now().strftime("%Y%m%d"),
        "generated_at": datetime.now().isoformat(),
        "archive": archive,
        "metrics": {k: v for k, v in metrics.items() if k != "calibration"},
        "calibration_directions": list(calib.keys()),
        "calibration_gain": calib_gain,
        "delta_vs_baseline": delta,
        "unmapped_teams": unmapped,
    }
    rp = data_dir("results") / "feedback_report.json"
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成 HTML 报告
    html_path = _render_feedback_html(report)
    report["html"] = str(html_path)

    print("[4/5] 回测对比报告已生成")
    print("[5/5] 完成")
    return report


def _nav_feedback() -> str:
    """渲染与主站一致的顶部菜单栏（含官方计算器外链）。"""
    items = [
        ("index.html", "首页", False),
        ("matches.html", "今日场次", False),
        ("scores.html", "比分推荐", False),
        ("live.html", "今日赛果", False),
        ("settled.html", "结算场次", False),
        ("accuracy.html", "累计命中率", False),
        ("feedback.html", "数据回馈", False),
        ("calculator.html", "官方计算器", False),
    ]
    links = []
    for href, label, external in items:
        if external:
            links.append(f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label} ↗</a>')
        else:
            cls = "active" if href == "feedback.html" else ""
            links.append(f'<a href="{href}" class="{cls}">{label}</a>')
    return f"""
<nav class="navbar">
  <div class="navbar-inner">
    <a href="index.html" class="brand"><span class="logo">⚽</span>竞彩概率分析</a>
    <div class="nav-links">{''.join(links)}</div>
  </div>
</nav>"""


def _render_feedback_html(report: dict):
    """生成每日回馈报告 HTML（命中率变化可视化）。"""
    from pathlib import Path

    m = report["metrics"]
    delta = report.get("delta_vs_baseline", {})

    def _delta_row(name: str, key: str, better_lower: bool) -> str:
        d = delta.get(key)
        if not d:
            return ""
        ch = d["change"]
        # 判断改善/恶化
        if better_lower:
            improved = ch < 0
        else:
            improved = ch > 0
        color = "#15803d" if improved else "#b91c1c"
        arrow = "↓" if improved else "↑"
        sign = "+" if ch >= 0 else ""
        return (
            f'<div class="prob"><div class="prob-label">{name}</div>'
            f'<div class="prob-val" style="color:{color}">{d["baseline"]:.4f} → {d["current"]:.4f} '
            f'({arrow}{sign}{ch:.4f})</div></div>'
        )

    rows = ""
    rows += _delta_row("Log Loss（越低越好）", "log_loss", True)
    rows += _delta_row("RPS（越低越好）", "rps", True)
    rows += _delta_row("Brier（越低越好）", "brier_score", True)
    rows += _delta_row("Top-1 命中率（越高越好）", "top1_accuracy", False)

    if not rows:
        rows = '<div class="prob" style="color:#64748b">本次为初始基线，暂无历史对比。</div>'

    # 校准效果区块
    cg = report.get("calibration_gain")
    if cg:
        d = cg["delta"] * 100
        color = "#15803d" if d > 0 else ("#b91c1c" if d < 0 else "#64748b")
        arrow = "↑" if d > 0 else ("↓" if d < 0 else "→")
        calib_block = (
            f'<div class="stat-grid">'
            f'<div class="stat"><div class="num">{cg["before"]*100:.2f}%</div><div class="lbl">校准前 Top-1</div></div>'
            f'<div class="stat"><div class="num" style="color:{color}">{cg["after"]*100:.2f}%</div><div class="lbl">校准后 Top-1</div></div>'
            f'</div>'
            f'<div class="prob"><div class="prob-label">命中率变化</div>'
            f'<div class="prob-val" style="color:{color}">{arrow}{d:+.2f}%</div></div>'
            f'<div class="sub" style="font-size:12px;color:#64748b;margin-top:8px">'
            f'基于 {cg["n_test"]} 场 out-of-sample 样本验证</div>'
        )
    else:
        calib_block = '<div class="prob" style="color:#64748b">样本不足，暂未计算校准增益。</div>'

    # 队名映射告警区块
    unmapped = report.get("unmapped_teams", [])
    if unmapped:
        items = "".join(
            f'<div class="prob"><div class="prob-label">'
            f'{u["league"]} · {u["home"]} vs {u["away"]}</div>'
            f'<div class="prob-val" style="color:#b91c1c">缺映射</div></div>'
            for u in unmapped
        )
        unmapped_block = (
            f'<div class="card"><h2>⚠️ 队名映射告警（{len(unmapped)} 场）</h2>'
            f'{items}'
            f'<div class="sub" style="font-size:12px;color:#64748b;margin-top:8px">'
            f'这些场次 DC 模型会静默失效、退化为市场去水概率（无比分分布）。'
            f'建议补充 config/team_name_map.json。</div></div>'
        )
    else:
        unmapped_block = ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日数据回馈报告 - 竞彩概率分析</title>
<style>
body {{ font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; background:radial-gradient(1200px 600px at 20% -10%,rgba(99,102,241,.18),transparent 60%),radial-gradient(1000px 500px at 100% 0%,rgba(168,85,247,.14),transparent 55%),#0a0e1a; background-attachment:fixed; color:#e8ecf6; line-height:1.6; margin:0; }}
.navbar {{ position:sticky; top:0; z-index:100; background:rgba(13,17,30,.82); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); border-bottom:1px solid rgba(99,102,241,.25); box-shadow:0 2px 20px rgba(0,0,0,.4); }}
.navbar-inner {{ max-width:1080px; margin:0 auto; padding:0 16px; display:flex; align-items:center; justify-content:space-between; height:60px; }}
.brand {{ color:#fff; font-size:17px; font-weight:800; letter-spacing:.5px; text-decoration:none; display:flex; align-items:center; gap:9px; }}
.brand .logo {{ width:30px; height:30px; background:linear-gradient(135deg,#6366f1,#a855f7); border-radius:9px; display:flex; align-items:center; justify-content:center; font-size:16px; box-shadow:0 0 16px rgba(124,58,237,.5); }}
.nav-links {{ display:flex; gap:2px; }}
.nav-links a {{ color:#a7b0c8; text-decoration:none; font-size:13px; padding:8px 12px; border-radius:9px; transition:all .2s; white-space:nowrap; }}
.nav-links a:hover {{ background:rgba(99,102,241,.16); color:#fff; }}
.nav-links a.active {{ background:linear-gradient(135deg,rgba(99,102,241,.9),rgba(168,85,247,.85)); color:#fff; font-weight:600; box-shadow:0 2px 12px rgba(99,102,241,.4); }}
.container {{ max-width:720px; margin:0 auto; padding:32px 16px 48px; }}
h1 {{ font-size:24px; font-weight:800; background:linear-gradient(135deg,#a5b4fc,#c4b5fd,#22d3ee); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }}
.sub {{ color:#8b94ad; font-size:13px; margin-bottom:24px; }}
.card {{ background:linear-gradient(180deg,#232c46,#1b2236); border-radius:14px; padding:20px; margin-bottom:16px; box-shadow:0 4px 16px rgba(0,0,0,.35); border:1px solid #2a334d; }}
.card h2 {{ font-size:16px; margin:0 0 14px; color:#e8ecf6; }}
.stat-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }}
.stat {{ background:#121828; border-radius:10px; padding:14px; text-align:center; border:1px solid #2a334d; }}
.stat .num {{ font-size:22px; font-weight:800; color:#a5b4fc; }}
.stat .lbl {{ font-size:11px; color:#8b94ad; margin-top:4px; }}
.prob {{ display:flex; align-items:center; justify-content:space-between; margin:8px 0; font-size:14px; }}
.prob-label {{ color:#cbd5e1; }}
.prob-val {{ font-weight:600; color:#e8ecf6; }}
.banner {{ background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.25); border-radius:10px; padding:12px 16px; font-size:12px; color:#fcd34d; }}
.footer {{ text-align:center; color:#6b748c; font-size:12px; margin-top:24px; }}
@media (max-width:640px) {{ .nav-links a {{ font-size:12px; padding:7px 9px; }} .navbar-inner {{ height:56px; }} }}
</style>
</head>
<body>
{_nav_feedback()}
<div class="container">
  <h1>每日数据回馈报告</h1>
  <div class="sub">日期 {report['date']} · 生成于 {report['generated_at']}</div>

  <div class="card">
    <h2>赛果归档</h2>
    <div class="stat-grid">
      <div class="stat"><div class="num">{report['archive'].get('n_archived', 0)}</div><div class="lbl">本日归档场次</div></div>
      <div class="stat"><div class="num">{report['archive'].get('n_hit', 0)}</div><div class="lbl">归档中命中</div></div>
    </div>
  </div>

  <div class="card">
    <h2>回测指标（重拟合后）</h2>
    <div class="stat-grid">
      <div class="stat"><div class="num">{m['top1_accuracy']*100:.1f}%</div><div class="lbl">Top-1 命中率</div></div>
      <div class="stat"><div class="num">{m['log_loss']:.4f}</div><div class="lbl">Log Loss</div></div>
      <div class="stat"><div class="num">{m['rps']:.4f}</div><div class="lbl">RPS</div></div>
      <div class="stat"><div class="num">{m['n_matches']}</div><div class="lbl">回测场次</div></div>
    </div>
  </div>

  <div class="card">
    <h2>与基线对比</h2>
    {rows}
  </div>

  <div class="card">
    <h2>概率校准</h2>
    <div class="prob"><div class="prob-label">已校准方向</div><div class="prob-val">{'、'.join(report.get('calibration_directions', [])) or '样本不足，暂未校准'}</div></div>
  </div>

  <div class="card">
    <h2>概率校准效果（out-of-sample）</h2>
    {calib_block}
  </div>

  {unmapped_block}

  <div class="banner">⚠️ 本报告为模型统计评估，命中率变化不构成任何投注建议，不代表未来结果。</div>
  <div class="footer">竞彩概率分析工具 · 数据回馈闭环</div>
</div>
</body>
</html>"""
    out = Path("docs") / "feedback.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    day = sys.argv[1] if len(sys.argv) > 1 else None
    r = run_daily_feedback(day)
    print(json.dumps(r, ensure_ascii=False, indent=2))
