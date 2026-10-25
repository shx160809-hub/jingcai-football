"""策略层：模型概率 vs 市场隐含概率 分歧分析。

将 Dixon-Coles 模型概率与官方赔率去水后的市场隐含概率对比，
计算分歧度。分歧大 = 模型与市场观点不一致（值得关注的场次）。
这是"盘口解读"，非投注建议。
"""
from __future__ import annotations

from src.strategy.de_vig import de_vig_probabilities


def model_market_divergence(pred: dict) -> dict | None:
    """计算单场模型概率与市场隐含概率的分歧。

    pred 需含 p_home/p_draw/p_away 和 had_win/had_draw/had_lose。
    返回分歧度量或 None（无赔率时）。
    """
    market = de_vig_probabilities(
        pred.get("had_win"), pred.get("had_draw"), pred.get("had_lose")
    )
    if market is None:
        return None

    keys = ["p_home", "p_draw", "p_away"]
    # 平均绝对差（L1 距离的一半）
    divergence = sum(abs(pred[k] - market[k]) for k in keys) / 2.0

    # 找出模型与市场分歧最大的方向
    diffs = {k: pred[k] - market[k] for k in keys}
    max_key = max(diffs, key=lambda k: abs(diffs[k]))
    direction_map = {"p_home": "主胜", "p_draw": "平局", "p_away": "客胜"}

    return {
        "divergence": round(divergence, 4),
        "market_p_home": market["p_home"],
        "market_p_draw": market["p_draw"],
        "market_p_away": market["p_away"],
        "model_higher_on": direction_map[max_key],
        "model_market_gap": round(diffs[max_key], 4),
    }


def annotate_divergence(results: list[dict]) -> list[dict]:
    """为每场预测添加模型-市场分歧信息。"""
    for r in results:
        div = model_market_divergence(r)
        if div is not None:
            r.update(div)
    return results
