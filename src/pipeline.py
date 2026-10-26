"""主流水线：串联 采集 → 特征 → 预测 → 输出 → 部署。

用法：
  python -m src.pipeline [date]
  不带 date 默认当天。

各层职责清晰、可独立调用；本脚本只做编排。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import pandas as pd

from src.common import data_dir, load_config
from src.collect.history import update_history
from src.collect.sporttery import fetch_today
from src.engine.dixon_coles import train_and_predict
from src.output.render import render


def run(day: str | None = None) -> dict:
    cfg = load_config()
    day = day or datetime.now().strftime("%Y%m%d")

    print(f"===== 流水线启动，目标日期 {day} =====")

    # Step 1: 采集官方赔率
    print("[1/6] 采集当日竞彩场次与赔率...")
    try:
        odds = fetch_today(day)
        fixtures = odds["matches"]
        print(f"      获取 {len(fixtures)} 场")
    except Exception as e:  # noqa: BLE001
        print(f"      ⚠️ 赔率采集失败：{e}")
        fixtures = []

    # Step 2: 更新历史数据
    print("[2/6] 更新历史比分数据...")
    try:
        update_history()
        print("      历史数据已更新")
    except Exception as e:  # noqa: BLE001
        print(f"      ⚠️ 历史数据更新失败：{e}")

    # Step 3: 加载历史数据
    hist_path = data_dir("raw") / "history.csv"
    if not hist_path.exists():
        print("      ❌ 无历史数据，无法预测，流水线终止")
        return {"status": "no_history"}

    history = pd.read_csv(hist_path, parse_dates=["date"])

    # Step 4: 预测（若无可预测场次则跳过）
    if fixtures:
        print(f"[3/6] Dixon-Coles 预测 {len(fixtures)} 场...")
        results = train_and_predict(history, fixtures)
        # 附加置信度评分 + 比分置信度 + 模型-市场分歧
        from src.output.confidence import annotate_confidence, annotate_score_confidence
        from src.strategy.divergence import annotate_divergence

        results = annotate_confidence(results)
        results = annotate_score_confidence(results)
        results = annotate_divergence(results)
    else:
        print("[3/6] 无场次可预测，跳过")
        results = []

    # Step 5: 采集今日赛果看板（实时比分 + 完赛 + 未开球，独立于模型预测）
    print("[4/6] 采集今日赛果看板...")
    try:
        from src.collect.sporttery import fetch_live_board

        board = fetch_live_board(day)
        print(f"      今日赛果 {len(board['matches'])} 场")
    except Exception as e:  # noqa: BLE001
        print(f"      ⚠️ 今日赛果采集失败：{e}")

    # Step 6: 输出 JSON + HTML
    print("[5/6] 生成输出...")
    if results:
        # 生成每日推荐并落库（比分 4-5 场 + 胜平负 Top5）
        try:
            from src.strategy.recommend import generate_recommendations

            generate_recommendations(results, day)
        except Exception as e:  # noqa: BLE001
            print(f"      ⚠️ 推荐生成失败：{e}")
        outs = render(results, day)
        for o in outs:
            print(f"      {o}")
    else:
        # 无预测结果时，仍生成今日赛果页面（不依赖模型）
        from src.output.render import render_live

        out = render_live(day)
        print(f"      {out}")

    # 汇总
    summary = {
        "date": day,
        "status": "ok",
        "n_matches": len(fixtures),
        "n_predicted": len(results),
        "history_size": len(history),
        "disclaimer": cfg["output"]["disclaimer"],
    }
    print("[6/6] 完成")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    run(day)
