"""输出层：模型置信度评分与 Top-N 筛选。

置信度 = 概率分布的信息量（熵的补），即概率最极端（最不接近均匀分布）的场次。
这代表"模型最明确的观点"，而非"命中率"。措辞严格合规，不暗示中奖。
"""
from __future__ import annotations

import math


def confidence_score(p_home: float, p_draw: float, p_away: float) -> float:
    """基于信息熵的置信度评分（0~1）。

    概率越极端（越远离 1/3 均匀分布），评分越高。
    本质是"模型对这场结果的确定性"，与命中率无关。
    """
    probs = [p_home, p_draw, p_away]
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    max_entropy = math.log(3)  # 均匀分布时熵最大
    # 归一化到 0~1：熵越小（越确定）置信度越高
    return 1.0 - entropy / max_entropy


def annotate_confidence(results: list[dict]) -> list[dict]:
    """为每场预测添加置信度评分。"""
    for r in results:
        r["confidence_score"] = round(
            confidence_score(r["p_home"], r["p_draw"], r["p_away"]), 4
        )
    return results


def top_n_by_confidence(results: list[dict], n: int = 5) -> list[dict]:
    """返回模型置信度最高的 n 场（按置信度降序）。"""
    scored = sorted(results, key=lambda r: r.get("confidence_score", 0), reverse=True)
    return scored[:n]


# ============================================================
# 比分置信度
# ============================================================

def score_confidence(top_scores: list[dict]) -> float | None:
    """比分置信度 = 最可能比分的概率（0~1）。

    模型对「最可能比分」给出的概率越高，说明对该场具体比分的预测越确定。
    仅适用于有比分分布的场次（Dixon-Coles 覆盖）；无比分分布返回 None。
    """
    if not top_scores:
        return None
    return round(top_scores[0]["prob"], 4)


def annotate_score_confidence(results: list[dict]) -> list[dict]:
    """为每场预测添加比分置信度（score_confidence）与最可能比分。"""
    for r in results:
        top = r.get("top_scores") or []
        sc = score_confidence(top)
        r["score_confidence"] = sc
        if top:
            r["most_likely_score"] = top[0]["score"]
    return results


def top_n_by_score_confidence(results: list[dict], n: int = 3) -> list[dict]:
    """返回比分置信度最高的 n 场（仅统计有比分分布的场次）。"""
    scored = [
        r for r in results
        if r.get("score_confidence") is not None
    ]
    scored.sort(key=lambda r: r["score_confidence"], reverse=True)
    return scored[:n]
