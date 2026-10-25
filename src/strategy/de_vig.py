"""策略层：从官方赔率反推去水概率（降级方案）。

当某联赛无历史数据时，用官方赔率去水（去除返还率）得到隐含概率，
作为该场次胜/平/负的模型参考值。这本质是"市场共识概率"，非独立模型预测，
但比无数据的均值先验更有信息量。
"""
from __future__ import annotations


def de_vig_probabilities(
    had_win: float | None, had_draw: float | None, had_lose: float | None
) -> dict[str, float] | None:
    """从胜平负赔率去水，得到市场隐含概率。

    返还率 overround = 1/win + 1/draw + 1/lose，去水即各项 / overround。
    返回归一化到 1 的概率。赔率为字符串或数字均可。
    """
    if had_win is None or had_draw is None or had_lose is None:
        return None
    try:
        w = float(had_win)
        d = float(had_draw)
        l = float(had_lose)
    except (TypeError, ValueError):
        return None
    if w <= 0 or d <= 0 or l <= 0:
        return None

    inv = 1 / w + 1 / d + 1 / l  # overround
    p_home = (1 / w) / inv
    p_draw = (1 / d) / inv
    p_away = (1 / l) / inv
    return {
        "p_home": round(p_home, 4),
        "p_draw": round(p_draw, 4),
        "p_away": round(p_away, 4),
    }
