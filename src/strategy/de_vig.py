"""策略层：从官方赔率反推去水概率（降级方案）。

当某联赛无历史数据时，用官方赔率去水（去除返还率）得到隐含概率，
作为该场次胜/平/负的模型参考值。这本质是"市场共识概率"，非独立模型预测，
但比无数据的均值先验更有信息量。

去水方法（回测验证，8905 场）：
  - 比例去水（proportional）：p_i = (1/o_i) / Σ(1/o_j)，Log Loss 0.9749
  - 幂去水（power method）：求 k 使 Σ(1/o_i^k) = 1，p_i = 1/o_i^k，
    Log Loss 0.9742（优于比例法 0.0007，概率更校准）
  默认用幂去水；数值求解失败时回退到比例去水。
"""
from __future__ import annotations

from scipy.optimize import brentq


def de_vig_probabilities(
    had_win: float | None, had_draw: float | None, had_lose: float | None,
    method: str = "power",
) -> dict[str, float] | None:
    """从胜平负赔率去水，得到市场隐含概率。

    返还率 overround = 1/win + 1/draw + 1/lose。去水即归一化。

    方法：
      - "power"：幂去水（默认，回测 Log Loss 最优）
      - "proportional"：比例去水（传统方法）

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

    if method == "power":
        try:
            return _power_method(w, d, l)
        except Exception:  # noqa: BLE001
            pass
    # 回退：比例去水
    return _proportional(w, d, l)


def _proportional(w: float, d: float, l: float) -> dict[str, float]:
    """比例去水：p_i = (1/o_i) / Σ(1/o_j)。"""
    inv = 1 / w + 1 / d + 1 / l  # overround
    return {
        "p_home": round((1 / w) / inv, 4),
        "p_draw": round((1 / d) / inv, 4),
        "p_away": round((1 / l) / inv, 4),
    }


def _power_method(w: float, d: float, l: float) -> dict[str, float]:
    """幂去水：求 k 使 Σ(1/o_i^k) = 1，p_i = 1/o_i^k。

    幂法假设博彩公司在每个结果上施加等比例的利润边际（乘法模型），
    比比例法的加法模型更贴合真实定价，去水后的概率更校准。
    """
    odds = [w, d, l]

    def f(k: float) -> float:
        return sum(o ** (-k) for o in odds) - 1.0

    # k 通常落在 (0, 5] 区间；赔率越大（冷门）需要的 k 越小
    k = brentq(f, 1e-6, 10.0, xtol=1e-10)
    p = [o ** (-k) for o in odds]
    s = sum(p)
    return {
        "p_home": round(p[0] / s, 4),
        "p_draw": round(p[1] / s, 4),
        "p_away": round(p[2] / s, 4),
    }
