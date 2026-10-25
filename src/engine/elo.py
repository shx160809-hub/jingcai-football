"""预测引擎层：Elo 评分（可选扩展）。

提供球队 Elo 评分计算，作为 Dixon-Coles 的补充特征或融合输入。
后续可与 XGBoost/LightGBM 集成。
"""
from __future__ import annotations

import math

import pandas as pd


def expected_score(elo_a: float, elo_b: float) -> float:
    """Elo 期望胜率（A 视角）。"""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def compute_elo(
    matches: pd.DataFrame, k: float = 30.0, home_adv: float = 60.0
) -> dict[str, float]:
    """按时间顺序计算每队 Elo 评分。

    matches 需含 date, home, away, home_goals, away_goals（按时间升序）。
    """
    elo: dict[str, float] = {}

    for _, row in matches.sort_values("date").iterrows():
        h, a = row["home"], row["away"]
        elo_h = elo.get(h, 1500.0) + home_adv
        elo_a = elo.get(a, 1500.0)

        # 实际结果
        if row["home_goals"] > row["away_goals"]:
            score_h, score_a = 1.0, 0.0
        elif row["home_goals"] < row["away_goals"]:
            score_h, score_a = 0.0, 1.0
        else:
            score_h, score_a = 0.5, 0.5

        exp_h = expected_score(elo_h, elo_a)
        exp_a = 1.0 - exp_h

        # 更新（去掉 home_adv 后再存储）
        new_h = elo.get(h, 1500.0) + k * (score_h - exp_h)
        new_a = elo.get(a, 1500.0) + k * (score_a - exp_a)
        elo[h] = new_h
        elo[a] = new_a

    return elo


def elo_to_prob(elo_home: float, elo_away: float) -> dict[str, float]:
    """Elo 差分近似胜/平/负概率（经验映射，仅作参考）。"""
    diff = elo_home - elo_away
    p_home = expected_score(elo_home, elo_away)
    p_away = 1.0 - p_home
    # 平局概率随实力接近而升高（经验式）
    draw_factor = math.exp(-abs(diff) / 300.0) * 0.28
    p_home -= draw_factor * p_home
    p_away -= draw_factor * p_away
    p_draw = draw_factor * (p_home + p_away + draw_factor)
    total = p_home + p_away + p_draw
    return {
        "p_home": p_home / total,
        "p_draw": p_draw / total,
        "p_away": p_away / total,
    }
