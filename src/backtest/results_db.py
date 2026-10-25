"""赛果数据库层：把每日官方赛果归档为统一的、可累积的赛果数据库。

职责：
  1. 从体彩官方赛果接口（或已落盘 results_{day}.json）抓取指定比赛日的全部赛果。
  2. 与当日预测（predict_{day}.json）按 match_num 精确对账，合并出「预测 + 实际赛果」完整记录。
  3. 归档到统一赛果数据库 data/results/match_results.csv（按比赛日累积，去重幂等）。
  4. 可选：把中文队名的赛果回写进历史库 history.csv（用于扩展亚洲联赛训练数据）。

统一赛果数据库字段（match_results.csv）：
  date, match_num, league, home, away,
  home_goals, away_goals, truth, win_flag,
  p_home, p_draw, p_away, pred_hit, confidence_score, source,
  had_win, had_draw, had_lose

合规：本模块仅归档统计用赛果数据，不构成任何投注建议。
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from src.common import data_dir, load_config

DB_PATH = data_dir("results") / "match_results.csv"

DB_COLUMNS = [
    "date", "match_num", "league", "home", "away",
    "home_goals", "away_goals", "truth", "win_flag",
    "p_home", "p_draw", "p_away", "pred_hit", "confidence_score", "source",
    "had_win", "had_draw", "had_lose",
]


def _load_official_results(day: str) -> list[dict]:
    """加载指定比赛日的官方赛果（优先落盘 JSON，否则实时抓取）。"""
    jp = data_dir("raw") / f"results_{day}.json"
    payload = None
    if jp.exists():
        try:
            payload = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = None

    if not payload or not payload.get("results"):
        try:
            from src.collect.sporttery import fetch_results

            payload = fetch_results(day)
        except Exception as e:  # noqa: BLE001
            print(f"[赛果库] 官方赛果抓取失败 {day}: {e}")
            return []

    return payload.get("results", []) if payload else []


def _load_predictions(day: str) -> list[dict]:
    """加载指定比赛日的预测结果（合并 predict 文件 + settlement 快照）。

    predict_{day}.json 是「当前快照」，会被后续采集覆盖（只保留未开球场次），
    导致已完赛场次的预测记录丢失。settlement.csv 存的是开赛前的预测快照，
    因此优先用 settlement.csv 补全已完赛场次的预测。
    """
    preds: list[dict] = []
    jp = data_dir("results") / f"predict_{day}.json"
    if jp.exists():
        preds += json.loads(jp.read_text(encoding="utf-8")).get("matches", [])

    # 用 settlement.csv 补全（开赛前快照，字段更可靠）
    sp = data_dir("results") / "settlement.csv"
    if sp.exists():
        sdf = pd.read_csv(sp)
        sdf = sdf[sdf["date"].astype(str) == day]
        for r in sdf.to_dict("records"):
            preds.append({
                "match_num": r.get("match_num"),
                "league": r.get("league", ""),
                "home": r.get("home", ""),
                "away": r.get("away", ""),
                "p_home": r.get("p_home"),
                "p_draw": r.get("p_draw"),
                "p_away": r.get("p_away"),
                "confidence_score": r.get("confidence_score"),
                "source": r.get("source", ""),
            })

    # 按 match_num 去重，settlement 快照优先
    by_num: dict[str, dict] = {}
    for p in preds:
        num = str(p.get("match_num", ""))
        if num:
            by_num[num] = p
    return list(by_num.values())


def _truth_from_score(hg, ag) -> str:
    """比分 -> 胜平负方向。"""
    if hg is None or ag is None:
        return ""
    if hg > ag:
        return "home"
    if hg < ag:
        return "away"
    return "draw"


def _pred_hit(truth: str, p_home, p_draw, p_away) -> bool:
    """判断模型最高概率选项是否与实际结果一致。

    注意：不能用 `p_draw or -1` 这种短路写法——当概率为 0 时（合法值），
    `0 or -1` 会错误地变成 -1。改用显式的 None 检查。
    """
    if truth == "home":
        return (
            p_home is not None
            and p_home >= (p_draw if p_draw is not None else -1)
            and p_home >= (p_away if p_away is not None else -1)
        )
    if truth == "away":
        return (
            p_away is not None
            and p_away >= (p_home if p_home is not None else -1)
            and p_away >= (p_draw if p_draw is not None else -1)
        )
    if truth == "draw":
        return (
            p_draw is not None
            and p_draw >= (p_home if p_home is not None else -1)
            and p_draw >= (p_away if p_away is not None else -1)
        )
    return False


def archive_day(day: str, sync_history: bool = False) -> dict:
    """归档指定比赛日的赛果到统一数据库。

    sync_history 默认 False：暂不回写中文赛果到 history.csv。
    原因：中文赛果的联赛名（全称）与队名（中文）与历史库英文体系不匹配，
    回写会污染训练集或无法被模型训练识别。待建立完整中文队名映射后再启用。

    返回 {date, n_archived, n_hit, n_miss, n_no_pred} 摘要。
    """
    cfg = load_config()
    results = _load_official_results(day)
    if not results:
        print(f"[赛果库] {day} 无官方赛果，跳过归档")
        return {"date": day, "n_archived": 0}

    preds = _load_predictions(day)
    pred_by_num = {str(p.get("match_num", "")): p for p in preds}

    # 已归档的 (date, match_num) 集合，用于幂等去重
    existing = set()
    if DB_PATH.exists():
        old = pd.read_csv(DB_PATH)
        if "date" in old.columns and "match_num" in old.columns:
            existing = {
                (str(r.date), str(r.match_num))
                for r in old.itertuples(index=False)
            }

    rows: list[dict] = []
    n_hit = n_miss = n_no_pred = 0
    for r in results:
        hg = r.get("home_goals")
        ag = r.get("away_goals")
        if hg is None or ag is None:
            continue  # 未开赛/无比分，不归档
        num = str(r.get("match_num", ""))
        if (day, num) in existing:
            continue

        truth = _truth_from_score(hg, ag)
        p = pred_by_num.get(num, {})
        p_home, p_draw, p_away = p.get("p_home"), p.get("p_draw"), p.get("p_away")
        source = p.get("source", "")

        # P0 修复：预测文件丢失/对账失败时，用赛果库里的官方赔率去水反推概率回填，
        # 保证 pred_hit 可计算，命中率闭环不因「预测快照缺失」而断裂。
        # 主力预测本就是市场赔率去水概率，此回填与主力口径一致。
        if p_home is None or p_draw is None or p_away is None:
            from src.strategy.de_vig import de_vig_probabilities

            dv = de_vig_probabilities(
                r.get("had_win"), r.get("had_draw"), r.get("had_lose")
            )
            if dv is not None:
                p_home, p_draw, p_away = dv["p_home"], dv["p_draw"], dv["p_away"]
                source = "market_implied_fallback"

        hit = _pred_hit(truth, p_home, p_draw, p_away) if p_home is not None else None

        if p_home is not None:
            if hit:
                n_hit += 1
            else:
                n_miss += 1
        else:
            n_no_pred += 1

        rows.append({
            "date": day,
            "match_num": num,
            "league": r.get("league", "") or p.get("league", ""),
            "home": r.get("home", "") or p.get("home", ""),
            "away": r.get("away", "") or p.get("away", ""),
            "home_goals": int(hg),
            "away_goals": int(ag),
            "truth": truth,
            "win_flag": r.get("win_flag", ""),
            "p_home": p_home,
            "p_draw": p_draw,
            "p_away": p_away,
            "pred_hit": hit,
            "confidence_score": p.get("confidence_score"),
            "source": source,
            "had_win": r.get("had_win"),
            "had_draw": r.get("had_draw"),
            "had_lose": r.get("had_lose"),
        })

    if not rows:
        print(f"[赛果库] {day} 无新赛果可归档（可能已归档或无比分）")
        if sync_history:
            _sync_to_history(day)
        return {"date": day, "n_archived": 0, "n_hit": n_hit, "n_miss": n_miss, "n_no_pred": n_no_pred}

    new_df = pd.DataFrame(rows, columns=DB_COLUMNS)
    if DB_PATH.exists():
        old = pd.read_csv(DB_PATH)
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=["date", "match_num"], keep="last")
    combined = combined.sort_values(["date", "match_num"])
    combined.to_csv(DB_PATH, index=False)

    print(f"[赛果库] {day} 归档 {len(rows)} 场（命中 {n_hit} / 未命中 {n_miss} / 无预测 {n_no_pred}）")

    # 回写历史库（扩展训练数据）
    if sync_history:
        _sync_to_history(day)

    return {"date": day, "n_archived": len(rows), "n_hit": n_hit, "n_miss": n_miss, "n_no_pred": n_no_pred}


def _sync_to_history(day: str) -> None:
    """把中文队名的赛果（尤其是无历史数据覆盖的亚洲联赛）回写进 history.csv。

    历史库当前为英文队名 + 欧洲联赛，官方赛果为中文队名，二者队名体系不同。
    这里只把「中文队名」的场次以独立记录追加（league 用中文名），供后续按联赛分组
    训练时使用；不会污染英文联赛的训练集（league 名不同，groupby 天然隔离）。

    覆盖判断：config 里 leagues（全称->代码）与 league_name_map（简称->代码）的
    值共同构成「已有英文历史数据覆盖」的联赛，这些联赛跳过（避免中英文混用）。
    """
    if not DB_PATH.exists():
        return
    cfg = load_config()
    # 已有英文历史数据覆盖的联赛中文名集合（全称 + 简称）
    covered_names = set(cfg["collect"].get("leagues", {}).values())
    covered_names |= set(cfg["collect"].get("league_name_map", {}).keys())

    db = pd.read_csv(DB_PATH)
    day_rows = db[db["date"].astype(str) == day]
    if day_rows.empty:
        return

    hist_path = data_dir("raw") / "history.csv"
    hist = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame()

    new_frames = []
    for _, row in day_rows.iterrows():
        league = str(row.get("league", ""))
        if league in covered_names:
            continue  # 已有英文历史数据覆盖，跳过（避免中英文混用）
        if pd.isna(row.get("home_goals")) or pd.isna(row.get("away_goals")):
            continue
        if not str(row.get("home", "")).strip() or not str(row.get("away", "")).strip():
            continue
        try:
            d = datetime.strptime(str(row["date"]), "%Y%m%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        new_frames.append({
            "home": row.get("home", ""),
            "away": row.get("away", ""),
            "home_goals": int(row["home_goals"]),
            "away_goals": int(row["away_goals"]),
            "league": league,  # 中文联赛名，与英文代码隔离
            "date": d,
        })

    if not new_frames:
        return

    ndf = pd.DataFrame(new_frames)
    if not hist.empty:
        hist = pd.concat([hist, ndf], ignore_index=True)
    else:
        hist = ndf
    hist = hist.drop_duplicates(subset=["date", "home", "away"], keep="last")
    # 用 stable 排序，保持同日期已有行的原始顺序不变，
    # 只把新增的中文赛果按日期插入正确位置，避免全文件行序重排产生巨大 diff。
    hist = hist.sort_values("date", kind="stable")
    hist.to_csv(hist_path, index=False)
    print(f"[赛果库] 回写 {len(new_frames)} 场中文赛果到历史库（扩展训练数据）")


def load_db() -> pd.DataFrame:
    """读取统一赛果数据库（无则返回空 DataFrame）。"""
    if not DB_PATH.exists():
        return pd.DataFrame(columns=DB_COLUMNS)
    return pd.read_csv(DB_PATH)


if __name__ == "__main__":
    import sys

    day = sys.argv[1] if len(sys.argv) > 1 else (datetime.now().strftime("%Y%m%d"))
    r = archive_day(day)
    print(r)
