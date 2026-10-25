"""特征层：欧赔盘口变化分析。

从开盘价 vs 收盘价计算赔率移动，捕捉市场资金流向信号。
赔率下降 = 市场资金流入该结果（更多人看好）；赔率上升 = 资金流出。

注意：Pinnacle 自 2025-07 起 API 不稳，优先用 Bet365 + 市场均价。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def implied_prob(odds: float) -> float:
    """赔率 -> 隐含概率。"""
    if odds is None or np.isnan(odds) or odds <= 0:
        return np.nan
    return 1.0 / odds


def odds_movement(
    open_odds: pd.Series, close_odds: pd.Series
) -> pd.Series:
    """计算赔率移动（隐含概率变化，正=该结果概率上升/资金流入）。"""
    open_p = open_odds.apply(implied_prob)
    close_p = close_odds.apply(implied_prob)
    return close_p - open_p


def build_odds_features(history: pd.DataFrame) -> pd.DataFrame:
    """为每场比赛计算欧赔盘口变化特征。

    输出列（每场一行）：
      - mv_home / mv_draw / mv_away：市场均价隐含概率变化（主胜/平/客胜）
      - b365_mv_home / b365_mv_draw / b365_mv_away：Bet365 隐含概率变化
      - max_movement：三项中绝对移动最大者（资金流向强度）
    """
    out = history[["league", "date", "home", "away"]].copy()

    # 市场均价：开盘 -> 收盘
    for suffix, col_open, col_close in [
        ("home", "AvgH", "AvgCH"),
        ("draw", "AvgD", "AvgCD"),
        ("away", "AvgA", "AvgCA"),
    ]:
        out[f"mv_{suffix}"] = odds_movement(
            history[col_open], history[col_close]
        )

    # Bet365：开盘 -> 收盘
    for suffix, col_open, col_close in [
        ("home", "B365H", "B365CH"),
        ("draw", "B365D", "B365CD"),
        ("away", "B365A", "B365CA"),
    ]:
        out[f"b365_mv_{suffix}"] = odds_movement(
            history[col_open], history[col_close]
        )

    # 资金流向强度：三项中最大绝对移动
    mv_cols = ["mv_home", "mv_draw", "mv_away"]
    out["max_movement"] = out[mv_cols].abs().max(axis=1)

    return out


if __name__ == "__main__":
    from src.common import data_dir

    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    feat = build_odds_features(hist)
    print(f"欧赔特征构建完成，{len(feat)} 场")
    print(feat[["home", "away", "mv_home", "mv_draw", "mv_away", "max_movement"]].head())
