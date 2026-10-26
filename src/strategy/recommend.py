"""推荐层：每日生成两类推荐并落库，形成可回溯的推荐数据库。

两类推荐：
  1. 比分推荐：按比分置信度（最可能比分概率）降序取前 4-5 场，每场给 2 个比分结果。
  2. 胜平负 Top5：按模型置信度（概率分布确定性）降序取前 5 场，给预测方向。

推荐落库到 data/results/recommendations.csv（追加式，按 (date, match_num, rec_type)
去重幂等）。赛果对账不在这里存，而是渲染时关联 match_results.csv 拿到实际比分与
命中标记，保证赛果只有一份事实来源。

合规：本模块只输出概率与比分分布，不构成任何投注建议。
"""
from __future__ import annotations

import pandas as pd

from src.common import data_dir

REC_PATH = data_dir("results") / "recommendations.csv"

REC_COLUMNS = [
    "date", "match_num", "home", "away", "league", "rec_type",
    "pred_home", "pred_draw", "pred_away",
    "score_1", "score_1_prob", "score_2", "score_2_prob",
    "confidence_score", "score_confidence",
]


def _max_key(d: dict) -> str:
    """返回概率 dict 中最高概率的键（home/draw/away）。"""
    return max(d, key=d.get)


def _clean(v):
    """把 NaN/None 统一为 None，避免写 CSV 时出现 nan 字符串。"""
    try:
        if v is None:
            return None
        if pd.isna(v):
            return None
        return v
    except (TypeError, ValueError):
        return v


def generate_recommendations(
    results: list[dict], day: str, n_score: int = 5, n_top: int = 5
) -> list[dict]:
    """从预测结果生成两类推荐并追加落库。

    n_score: 比分推荐场数（默认 5，实际取"有比分分布"的场次中比分置信度最高的 n 场）。
    n_top:   胜平负 Top-N（默认 5）。

    返回本次生成的推荐记录列表。
    """
    rows: list[dict] = []

    # ---- 比分推荐：有 ≥2 个比分分布 + 未开球优先，按比分置信度降序 ----
    # 规则：当日超过 n_score 场取 n_score 场，不足取全部（min(n_score, 有分布场次数)）
    scored = [r for r in results if len(r.get("top_scores") or []) >= 2]
    scored.sort(key=lambda r: r.get("score_confidence") or 0, reverse=True)
    for r in scored[:n_score]:
        top = r.get("top_scores") or []
        s1 = top[0] if len(top) > 0 else None
        s2 = top[1] if len(top) > 1 else None
        rows.append({
            "date": day,
            "match_num": _clean(r.get("match_num")),
            "home": r.get("home", ""),
            "away": r.get("away", ""),
            "league": r.get("league", ""),
            "rec_type": "score",
            "pred_home": _clean(r.get("p_home")),
            "pred_draw": _clean(r.get("p_draw")),
            "pred_away": _clean(r.get("p_away")),
            "score_1": s1["score"] if s1 else None,
            "score_1_prob": _clean(s1["prob"]) if s1 else None,
            "score_2": s2["score"] if s2 else None,
            "score_2_prob": _clean(s2["prob"]) if s2 else None,
            "confidence_score": _clean(r.get("confidence_score")),
            "score_confidence": _clean(r.get("score_confidence")),
        })

    # ---- 胜平负 Top5：按置信度降序 ----
    top5 = sorted(
        results, key=lambda r: r.get("confidence_score") or 0, reverse=True
    )[:n_top]
    for r in top5:
        rows.append({
            "date": day,
            "match_num": _clean(r.get("match_num")),
            "home": r.get("home", ""),
            "away": r.get("away", ""),
            "league": r.get("league", ""),
            "rec_type": "top5",
            "pred_home": _clean(r.get("p_home")),
            "pred_draw": _clean(r.get("p_draw")),
            "pred_away": _clean(r.get("p_away")),
            "score_1": None,
            "score_1_prob": None,
            "score_2": None,
            "score_2_prob": None,
            "confidence_score": _clean(r.get("confidence_score")),
            "score_confidence": _clean(r.get("score_confidence")),
        })

    if not rows:
        return []

    new_df = pd.DataFrame(rows, columns=REC_COLUMNS)

    # 追加落库 + 幂等去重（同一天同一场同一类型只保留最新）
    if REC_PATH.exists():
        old = pd.read_csv(REC_PATH)
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(
        subset=["date", "match_num", "rec_type"], keep="last"
    )
    combined = combined.sort_values(["date", "match_num"], kind="stable")
    combined.to_csv(REC_PATH, index=False)

    print(
        f"[推荐] {day} 生成推荐 {len(rows)} 条"
        f"（比分 {sum(1 for r in rows if r['rec_type']=='score')} / "
        f"Top5 {sum(1 for r in rows if r['rec_type']=='top5')}）"
    )
    return rows


def load_recommendations() -> pd.DataFrame:
    """读取推荐数据库（无则返回空 DataFrame）。"""
    if not REC_PATH.exists():
        return pd.DataFrame(columns=REC_COLUMNS)
    return pd.read_csv(REC_PATH)


def load_recent(day: str, days: int = 7) -> list[dict]:
    """读取近 N 日推荐记录，并关联赛果（实际比分 + 命中标记）。

    返回含 home_goals/away_goals/pred_hit 的 records（无赛果则为 None）。
    """
    recs = load_recommendations()
    if recs.empty:
        return []

    # 近 N 日过滤
    recs = recs.copy()
    recs["date"] = recs["date"].astype(str)
    if day:
        try:
            from datetime import datetime, timedelta

            anchor = datetime.strptime(day, "%Y%m%d")
            cutoff = (anchor - timedelta(days=days - 1)).strftime("%Y%m%d")
            recs = recs[recs["date"] >= cutoff]
        except ValueError:
            pass

    # 关联赛果
    db_path = data_dir("results") / "match_results.csv"
    db = pd.read_csv(db_path) if db_path.exists() else pd.DataFrame()
    if not db.empty and "date" in db.columns:
        db = db.copy()
        db["date"] = db["date"].astype(str)
        recs = recs.merge(
            db[["date", "match_num", "home_goals", "away_goals", "pred_hit"]],
            on=["date", "match_num"],
            how="left",
        )

    records = recs.sort_values(["date", "match_num"], ascending=False).to_dict("records")
    # 把 NaN 转 None（pandas 会把缺失的赛果列填成 NaN）
    for r in records:
        for col in ("home_goals", "away_goals", "pred_hit", "score_1_prob",
                    "score_2_prob", "confidence_score", "score_confidence",
                    "pred_home", "pred_draw", "pred_away"):
            if col in r:
                try:
                    if pd.isna(r[col]):
                        r[col] = None
                except (TypeError, ValueError):
                    pass
    return records


if __name__ == "__main__":
    import json
    import sys
    from datetime import datetime

    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    jp = data_dir("results") / f"predict_{day}.json"
    if not jp.exists():
        print(f"未找到预测结果 {jp}，请先运行预测")
        sys.exit(1)
    results = json.loads(jp.read_text(encoding="utf-8"))["matches"]
    rows = generate_recommendations(results, day)
    print(f"生成 {len(rows)} 条推荐")
