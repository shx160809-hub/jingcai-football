"""策略/融合层：多模型概率融合。

当前：Dixon-Coles 为主模型，Elo 为辅助。融合用加权平均。
后续扩展 XGBoost/LightGBM 时在此层接入。
"""
from __future__ import annotations


def blend_probabilities(
    dc: dict[str, float],
    elo: dict[str, float] | None,
    w_dc: float = 0.85,
) -> dict[str, float]:
    """融合 Dixon-Coles 与 Elo 概率。

    w_dc 为 Dixon-Coles 权重，Elo 权重为 1-w_dc（默认 0.15）。
    融合后归一化。
    """
    if elo is None:
        return dc

    blended = {}
    for key in ("p_home", "p_draw", "p_away"):
        blended[key] = w_dc * dc[key] + (1 - w_dc) * elo[key]

    total = sum(blended.values())
    for key in blended:
        blended[key] = round(blended[key] / total, 4)
    return blended
