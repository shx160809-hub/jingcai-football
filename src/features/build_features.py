"""特征层：构建主客队近期状态特征。

从历史比分数据中，为每支球队构建近期攻防特征（近N场进/失球均值等），
并处理数据稀疏情况（样本不足时标记，由引擎层回退到联赛均值先验）。
"""
from __future__ import annotations

import pandas as pd

from src.common import data_dir, load_config


def build_team_form(history: pd.DataFrame, window: int = 6) -> pd.DataFrame:
    """为每支球队构建近期状态特征。

    返回 DataFrame，每行一支球队，含：
      - avg_goals_for / avg_goals_against：近 window 场进/失球均值
      - sample_size：可用样本数
      - last_match_date：最近比赛日期
    """
    rows = []
    teams = pd.unique(history[["home", "away"]].values.ravel())

    for team in teams:
        # 该队作为主队或客队的所有比赛
        as_home = history[history["home"] == team][
            ["date", "home_goals", "away_goals"]
        ].rename(columns={"home_goals": "for_", "away_goals": "against"})
        as_away = history[history["away"] == team][
            ["date", "away_goals", "home_goals"]
        ].rename(columns={"away_goals": "for_", "home_goals": "against"})

        games = pd.concat([as_home, as_away]).sort_values("date").tail(window)

        rows.append(
            {
                "team": team,
                "avg_goals_for": games["for_"].mean(),
                "avg_goals_against": games["against"].mean(),
                "sample_size": len(games),
                "last_match_date": games["date"].max() if not games.empty else None,
            }
        )

    form = pd.DataFrame(rows)
    return form


def build_features(history: pd.DataFrame) -> pd.DataFrame:
    """特征层主入口：产出球队状态特征表并落盘。"""
    cfg = load_config()
    window = cfg.get("features", {}).get("form_window", 6)
    form = build_team_form(history, window=window)

    out = data_dir("features") / "team_form.csv"
    form.to_csv(out, index=False)
    return form


if __name__ == "__main__":
    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    f = build_features(hist)
    print(f"已构建 {len(f)} 支球队特征，落盘 data/features/team_form.csv")
