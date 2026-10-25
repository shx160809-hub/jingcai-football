"""增强特征层：合并近期状态 + 欧赔盘口变化，为 XGBoost 二层学习提供特征矩阵。

在已有 build_features.py（近期状态）与 odds_features.py（欧赔移动）基础上，
产出一个「按场次对齐」的特征矩阵，供第二层模型（XGBoost Stacking）使用。

关键：所有特征必须只用「该场之前」的信息计算，避免时序泄露。
本模块提供两套接口：
  1. build_match_features(history, as_of_date) —— 给定截止日期，逐场回溯构建特征
  2. build_live_features(history, fixtures, team_form, elo) —— 线上预测时构建当天特征
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _recent_form(history: pd.DataFrame, team: str, as_of: pd.Timestamp, window: int = 6) -> dict:
    """球队截至 as_of 的近 window 场攻防统计（严格只用 as_of 之前的比赛）。"""
    past = history[history["date"] < as_of]
    as_home = past[past["home"] == team][["date", "home_goals", "away_goals"]].rename(
        columns={"home_goals": "for_", "away_goals": "against"}
    )
    as_away = past[past["away"] == team][["date", "away_goals", "home_goals"]].rename(
        columns={"away_goals": "for_", "home_goals": "against"}
    )
    games = pd.concat([as_home, as_away]).sort_values("date").tail(window)
    if games.empty:
        return {
            "gf": np.nan, "ga": np.nan, "pts": np.nan, "n": 0,
            "form_pts_pg": np.nan,
        }
    pts = 0.0
    for _, g in games.iterrows():
        if g["for_"] > g["against"]:
            pts += 3
        elif g["for_"] == g["against"]:
            pts += 1
    return {
        "gf": float(games["for_"].mean()),
        "ga": float(games["against"].mean()),
        "pts": pts,
        "n": int(len(games)),
        "form_pts_pg": pts / len(games),
    }


def _odds_movement_features(row: pd.Series) -> dict:
    """单场欧赔开盘→收盘隐含概率变化（资金流向代理）。"""
    def imp(o):
        try:
            o = float(o)
        except (TypeError, ValueError):
            return np.nan
        return 1.0 / o if o > 0 else np.nan

    out = {}
    # 市场均价 + Bet365，三类方向开盘/收盘
    for src, oh, od, oa, ch, cd, ca in [
        ("avg", "AvgH", "AvgD", "AvgA", "AvgCH", "AvgCD", "AvgCA"),
        ("b365", "B365H", "B365D", "B365A", "B365CH", "B365CD", "B365CA"),
    ]:
        if oh not in row.index:
            continue
        ph_o, pd_o, pa_o = imp(row[oh]), imp(row[od]), imp(row[oa])
        ph_c, pd_c, pa_c = imp(row[ch]), imp(row[cd]), imp(row[ca])
        out[f"{src}_mv_home"] = (ph_c - ph_o) if not np.isnan(ph_c) and not np.isnan(ph_o) else np.nan
        out[f"{src}_mv_draw"] = (pd_c - pd_o) if not np.isnan(pd_c) and not np.isnan(pd_o) else np.nan
        out[f"{src}_mv_away"] = (pa_c - pa_o) if not np.isnan(pa_c) and not np.isnan(pa_o) else np.nan
        # 去水隐含概率（收盘市场共识）
        if not np.isnan(ph_c) and not np.isnan(pd_c) and not np.isnan(pa_c):
            ovr = ph_c + pd_c + pa_c
            out[f"{src}_p_home"] = ph_c / ovr
            out[f"{src}_p_draw"] = pd_c / ovr
            out[f"{src}_p_away"] = pa_c / ovr
    return out


def _load_xg_map() -> dict[tuple[str, str], dict]:
    """加载 xG 球队特征，返回 {(league_code, team): {xgf, xga, n}}。

    队名需与历史库英文名对齐（understat 队名与 football-data 略有差异，
    此处做常见映射，未命中则跳过）。
    """
    from src.collect.understat import load_xg_features

    feat = load_xg_features()
    if feat.empty:
        return {}
    xg_map = {}
    for _, r in feat.iterrows():
        xg_map[(r["league"], r["team"])] = {"xgf": r["xgf"], "xga": r["xga"], "n": r["n"]}
    return xg_map


def build_match_features(history: pd.DataFrame, window: int = 6) -> pd.DataFrame:
    """为历史每场比赛构建对齐特征矩阵（含标签），供 XGBoost 训练/回测。

    返回列：
      - 基础：league, date, home, away, home_goals, away_goals, truth(0/1/2)
      - 近期状态：home_gf/home_ga/home_pts_pg, away_gf/away_ga/away_pts_pg, 差值
      - 欧赔移动：avg_mv_*, b365_mv_*, avg_p_*（去水隐含概率）
    """
    history = history.sort_values("date").reset_index(drop=True)
    rows = []
    xg_map = _load_xg_map()
    for i, row in history.iterrows():
        as_of = row["date"]
        home, away = row["home"], row["away"]
        hf = _recent_form(history, home, as_of, window)
        af = _recent_form(history, away, as_of, window)

        # xG 攻击/防守代理（若该联赛/球队有 xG 数据）
        league = row.get("league", "")
        xh = xg_map.get((league, home))
        xa = xg_map.get((league, away))
        feat = {
            "league": league,
            "date": as_of,
            "home": home,
            "away": away,
            "home_goals": row["home_goals"],
            "away_goals": row["away_goals"],
            "truth": 0 if row["home_goals"] > row["away_goals"] else (2 if row["home_goals"] < row["away_goals"] else 1),
            "home_gf": hf["gf"], "home_ga": hf["ga"], "home_pts_pg": hf["form_pts_pg"],
            "away_gf": af["gf"], "away_ga": af["ga"], "away_pts_pg": af["form_pts_pg"],
            "gf_diff": (hf["gf"] - af["gf"]) if not np.isnan(hf["gf"]) and not np.isnan(af["gf"]) else np.nan,
            "pts_diff": (hf["form_pts_pg"] - af["form_pts_pg"]) if not np.isnan(hf["form_pts_pg"]) and not np.isnan(af["form_pts_pg"]) else np.nan,
            # xG 特征（无数据为 NaN，模型会忽略）
            "home_xgf": xh["xgf"] if xh else np.nan,
            "home_xga": xh["xga"] if xh else np.nan,
            "away_xgf": xa["xgf"] if xa else np.nan,
            "away_xga": xa["xga"] if xa else np.nan,
        }
        feat.update(_odds_movement_features(row))
        rows.append(feat)

    return pd.DataFrame(rows)


def _team_form_asof(history: pd.DataFrame, as_of: pd.Timestamp, window: int = 6) -> dict[str, dict]:
    """截至 as_of 所有球队的近期状态表（供线上预测批量取用）。"""
    teams = pd.unique(history[history["date"] < as_of][["home", "away"]].values.ravel())
    return {t: _recent_form(history, t, as_of, window) for t in teams}


def build_live_features(
    history: pd.DataFrame,
    fixtures: list[dict],
    team_form: dict[str, dict],
    window: int = 6,
) -> list[dict]:
    """线上预测：为待预测场次构建特征（fixtures 每项含 home/away/league + 欧赔）。

    team_form 可传 build_team_form 的结果，但为对齐「只用历史」这里用 _team_form_asof。
    返回与 fixtures 对齐的特征 dict 列表。
    """
    as_of = pd.Timestamp.now()
    # 欧赔特征需要 had_* 字段（当前官方赔率即「收盘」近似，无历史移动）
    feats = []
    for fx in fixtures:
        home, away = fx["home"], fx["away"]
        hf = _recent_form(history, home, as_of, window)
        af = _recent_form(history, away, as_of, window)
        f = {
            "home_gf": hf["gf"], "home_ga": hf["ga"], "home_pts_pg": hf["form_pts_pg"],
            "away_gf": af["gf"], "away_ga": af["ga"], "away_pts_pg": af["form_pts_pg"],
            "gf_diff": (hf["gf"] - af["gf"]) if not np.isnan(hf["gf"]) and not np.isnan(af["gf"]) else np.nan,
            "pts_diff": (hf["form_pts_pg"] - af["form_pts_pg"]) if not np.isnan(hf["form_pts_pg"]) and not np.isnan(af["form_pts_pg"]) else np.nan,
        }
        # 官方赔率作为当前市场共识（去水隐含概率），无开盘→收盘移动时视为 0
        f["avg_p_home"] = np.nan
        f["avg_p_draw"] = np.nan
        f["avg_p_away"] = np.nan
        feats.append(f)
    return feats


if __name__ == "__main__":
    from src.common import data_dir

    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    feat = build_match_features(hist)
    print(f"特征矩阵：{feat.shape[0]} 场 × {feat.shape[1]} 列")
    print("特征列：", list(feat.columns))
    print("\n欧赔移动特征缺失率：")
    for c in ["avg_mv_home", "avg_p_home", "gf_diff", "pts_diff"]:
        if c in feat.columns:
            print(f"  {c}: {feat[c].isna().mean():.1%}")
