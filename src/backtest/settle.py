"""结算层：赛后自动对账，记录模型表现。

读取前一日预测结果与真实赛果，计算模型是否命中最高概率选项，
追加到结算记录，形成可追溯的闭环。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.common import data_dir, load_config


def load_results(day: str) -> list[dict]:
    """读取真实赛果。优先级：本地 CSV > 体彩官方赛果接口 > football-data 兜底。

    期望赛果 CSV：data/raw/results_{day}.csv，列 match_num, home_goals, away_goals。
    官方接口：data/raw/results_{day}.json（由 fetch_results 落盘），按 match_num 精确匹配。
    兜底：从 history.csv 中按日期+球队名匹配真实比分。
    """
    # 1. 本地 CSV（手动填充优先）
    path = data_dir("raw") / f"results_{day}.csv"
    if path.exists():
        df = pd.read_csv(path)
        return df.to_dict("records")

    # 2. 体彩官方赛果（优先，覆盖全部竞彩场次）
    official = _results_from_official(day)
    if official:
        return official

    # 3. 兜底：从历史数据匹配（仅覆盖欧洲联赛，需球队名映射）
    return _results_from_history(day)


def _results_from_official(day: str) -> list[dict]:
    """从体彩官方赛果接口（或已落盘的 JSON）获取赛果，按 match_num 匹配。

    优先读已落盘的 data/raw/results_{day}.json，不存在则实时调用官方接口。
    返回格式统一为含 match_num / home_goals / away_goals 的记录。
    """
    # 先读已落盘的 JSON
    jp = data_dir("raw") / f"results_{day}.json"
    payload = None
    if jp.exists():
        try:
            payload = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = None

    # 落盘 JSON 为空或不存在，实时抓取
    if not payload or not payload.get("results"):
        try:
            from src.collect.sporttery import fetch_results

            payload = fetch_results(day)
        except Exception as e:  # noqa: BLE001
            print(f"[结算] 官方赛果接口不可用: {e}")
            return []

    results = payload.get("results", [])
    out = []
    for r in results:
        if r.get("home_goals") is None or r.get("away_goals") is None:
            continue
        out.append(
            {
                "match_num": r.get("match_num", ""),
                "home_goals": int(r["home_goals"]),
                "away_goals": int(r["away_goals"]),
            }
        )
    return out


def _results_from_history(day: str) -> list[dict]:
    """从历史数据中按日期匹配赛果（欧洲联赛兜底）。"""
    hist_path = data_dir("raw") / "history.csv"
    if not hist_path.exists():
        return []

    # 转换日期格式 YYYYMMDD -> 日期
    try:
        target = pd.to_datetime(day, format="%Y%m%d")
    except Exception:  # noqa: BLE001
        return []

    hist = pd.read_csv(hist_path, parse_dates=["date"])
    matched = hist[hist["date"] == target]

    # 加载队名映射（英文 -> 中文），用于与预测的 match_num 无关的兜底
    # 这里只返回英文队名的比分，settle 里按 home/away 中文名匹配不到时跳过
    results = []
    for _, row in matched.iterrows():
        results.append(
            {
                "home_en": row["home"],
                "away_en": row["away"],
                "home_goals": int(row["home_goals"]),
                "away_goals": int(row["away_goals"]),
            }
        )
    return results


def settle(day: str) -> list[dict]:
    """对账前一日预测。day 为预测日（预测的场次日期）。"""
    pred_path = data_dir("results") / f"predict_{day}.json"
    if not pred_path.exists():
        print(f"无预测记录 {pred_path}")
        return []

    predictions = json.loads(pred_path.read_text(encoding="utf-8"))["matches"]
    results = load_results(day)

    # 两种赛果格式：按 match_num，或按球队名（home_en/away_en）
    by_num = {str(r["match_num"]): r for r in results if "match_num" in r}
    by_team = {}
    for r in results:
        if "home_en" in r:
            by_team[(r["home_en"], r["away_en"])] = r

    settled = []
    for p in predictions:
        r = by_num.get(str(p.get("match_num")))
        if r is None:
            # 按球队名兜底（需映射英文名）
            home_en = _team_en(p.get("league"), p["home"])
            away_en = _team_en(p.get("league"), p["away"])
            r = by_team.get((home_en, away_en))
        if r is None:
            continue
        hg, ag = r["home_goals"], r["away_goals"]
        if hg > ag:
            truth = "home"
            hit = p["p_home"] >= p["p_draw"] and p["p_home"] >= p["p_away"]
        elif hg < ag:
            truth = "away"
            hit = p["p_away"] >= p["p_home"] and p["p_away"] >= p["p_draw"]
        else:
            truth = "draw"
            hit = p["p_draw"] >= p["p_home"] and p["p_draw"] >= p["p_away"]

        settled.append(
            {
                "date": day,
                "match_num": p.get("match_num"),
                "league": p.get("league", ""),
                "home": p["home"],
                "away": p["away"],
                "home_goals": int(hg),
                "away_goals": int(ag),
                "truth": truth,
                "pred_hit": hit,
                "p_home": p["p_home"],
                "p_draw": p["p_draw"],
                "p_away": p["p_away"],
                "confidence_score": p.get("confidence_score"),
                "source": p.get("source", ""),
            }
        )

    # 追加到结算记录
    out = data_dir("results") / "settlement.csv"
    new_df = pd.DataFrame(settled)
    if out.exists():
        old_df = pd.read_csv(out)
        combined = pd.concat([old_df, new_df]).drop_duplicates(
            subset=["date", "match_num"], keep="last"
        )
    else:
        combined = new_df
    combined.to_csv(out, index=False)

    # 打印命中率
    if settled:
        hit_rate = sum(s["pred_hit"] for s in settled) / len(settled)
        print(f"结算 {len(settled)} 场，最高概率命中率：{hit_rate:.1%}")
    return settled


def load_settlement_summary() -> dict:
    """读取历史结算记录，计算累计与近期命中率。

    返回：
      - total_matches / total_hits / overall_hit_rate
      - recent_hit_rate（最近 7 天）
      - daily：按日期的命中率列表
    """
    out = data_dir("results") / "settlement.csv"
    if not out.exists():
        return {
            "total_matches": 0,
            "total_hits": 0,
            "overall_hit_rate": None,
            "recent_hit_rate": None,
            "daily": [],
        }

    df = pd.read_csv(out)
    if df.empty:
        return {
            "total_matches": 0,
            "total_hits": 0,
            "overall_hit_rate": None,
            "recent_hit_rate": None,
            "daily": [],
        }

    total = len(df)
    hits = int(df["pred_hit"].sum())

    # 最近 7 天
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.Timedelta(days=7)
    recent = df[df["date"] >= cutoff]
    recent_hit_rate = float(recent["pred_hit"].mean()) if len(recent) else None

    # 按日汇总
    daily = (
        df.groupby(df["date"].dt.strftime("%Y-%m-%d"))
        .agg(n=("pred_hit", "size"), hit_rate=("pred_hit", "mean"))
        .reset_index()
        .rename(columns={"n": "n_matches"})
        .sort_values("date")
        .to_dict("records")
    )
    for d in daily:
        d["hit_rate"] = round(float(d["hit_rate"]), 4)

    return {
        "total_matches": total,
        "total_hits": hits,
        "overall_hit_rate": round(hits / total, 4),
        "recent_hit_rate": round(recent_hit_rate, 4) if recent_hit_rate is not None else None,
        "daily": daily,
    }


def _team_en(league: str | None, team_cn: str) -> str:
    """中文队名 -> 英文队名（用于结算时与历史数据匹配）。"""
    try:
        import json

        from src.common import ROOT

        team_map = json.loads(
            (ROOT / "config" / "team_name_map.json").read_text(encoding="utf-8")
        )
        return team_map.get(league or "", {}).get(team_cn, team_cn)
    except Exception:  # noqa: BLE001
        return team_cn


def settle_yesterday() -> list[dict]:
    """结算昨天的场次。"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    return settle(yesterday)


def settle_recent() -> list[dict]:
    """结算「昨日 + 今日」的场次（今日已完赛的场次也一并结算）。

    先抓取官方赛果落盘，再对昨日和今日分别结算。
    返回合并后的结算记录列表。
    """
    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    # 抓取官方赛果（昨日 + 今日），供结算使用
    from src.collect.sporttery import fetch_results

    for d in (yesterday, today):
        try:
            fetch_results(d)
        except Exception as e:  # noqa: BLE001
            print(f"[结算] 抓取 {d} 赛果失败: {e}")

    merged: list[dict] = []
    for d in (yesterday, today):
        try:
            merged.extend(settle(d))
        except Exception as e:  # noqa: BLE001
            print(f"[结算] 结算 {d} 失败: {e}")
    return merged


if __name__ == "__main__":
    import sys

    day = sys.argv[1] if len(sys.argv) > 1 else None
    if day:
        settle(day)
    else:
        settle_recent()
