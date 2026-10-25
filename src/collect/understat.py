"""数据采集：understat xG 数据源（五大联赛）。

understat 提供逐场 xG（期望进球）数据，是比实际进球更稳健的「攻击力/防守力」代理。
接口：GET https://understat.com/getLeagueData/{league}/{season}
  - league 用带空格的代码：EPL / "La liga" / Bundesliga / "Serie A" / "Ligue 1"
  - 必须带 Referer 头，否则 404
  - 返回 { teams: {id: {title, history:[{date,xG,xGA,scored,missed,...}] } }, dates: {...} }

本模块抓取并落盘为统一格式，再聚合出每队的 xG 攻击/防守代理，供增强特征使用。

合规：xG 仅为统计特征，不构成投注建议。
"""
from __future__ import annotations

import json
import time

import pandas as pd

from src.common import data_dir

# understat 联赛代码（带空格） -> football-data 代码
LEAGUE_MAP = {
    "EPL": "E0",
    "La liga": "SP1",
    "Bundesliga": "D1",
    "Serie A": "I1",
    "Ligue 1": "F1",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://understat.com/league/EPL/2024",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_league_xg(league: str, season: str, session=None) -> pd.DataFrame:
    """抓取一个联赛一个赛季的逐场 xG，返回扁平 DataFrame（每场一行）。"""
    import requests

    url = f"https://understat.com/getLeagueData/{league}/{season}"
    r = (session or requests).get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    rows = []
    teams = data.get("teams", {})
    for team_id, team in teams.items():
        title = team.get("title", team_id)
        for h in team.get("history", []):
            # h 含 h_a(a/h), xG, xGA, scored, missed, date, result
            rows.append({
                "league": LEAGUE_MAP.get(league, league),
                "season": season,
                "team": title,
                "h_a": h.get("h_a", ""),
                "xG": h.get("xG"),
                "xGA": h.get("xGA"),
                "goals_for": h.get("scored"),
                "goals_against": h.get("missed"),
                "date": h.get("date", "")[:10],
                "result": h.get("result", ""),
            })
    return pd.DataFrame(rows)


def update_xg_data(seasons: list[str] | None = None, leagues: list[str] | None = None) -> str:
    """抓取并落盘五大联赛 xG 数据到 data/raw/xg_matches.csv。"""
    import requests

    if seasons is None:
        seasons = ["2024", "2025"]
    if leagues is None:
        leagues = list(LEAGUE_MAP.keys())

    frames = []
    with requests.Session() as s:
        for league in leagues:
            for season in seasons:
                try:
                    df = fetch_league_xg(league, season, session=s)
                    if not df.empty:
                        frames.append(df)
                        print(f"[xG] {league} {season}: {len(df)} 条记录")
                    time.sleep(0.6)
                except Exception as e:  # noqa: BLE001
                    print(f"[xG] {league} {season} 失败: {e}")
    if not frames:
        return ""
    all_df = pd.concat(frames, ignore_index=True)
    # 去重（同一场从主客两队各出现一次，保留即可，聚合时会合并）
    out = data_dir("raw") / "xg_matches.csv"
    all_df.to_csv(out, index=False)
    return str(out)


def build_xg_team_features(xg_df: pd.DataFrame) -> pd.DataFrame:
    """把逐场 xG 聚合为每队 xG 攻击/防守代理。

    返回每队一行：team, league, xgf(场均xG), xga(场均xGA), n(样本数)。
    """
    if xg_df.empty:
        return pd.DataFrame()
    agg = xg_df.groupby(["league", "team"]).agg(
        xgf=("xG", "mean"), xga=("xGA", "mean"), n=("xG", "size")
    ).reset_index()
    return agg


def load_xg_features() -> pd.DataFrame:
    """加载已落盘的 xG 数据并聚合为球队特征（无则返回空）。"""
    path = data_dir("raw") / "xg_matches.csv"
    if not path.exists():
        return pd.DataFrame()
    return build_xg_team_features(pd.read_csv(path))


if __name__ == "__main__":
    import sys

    seasons = sys.argv[1:] if len(sys.argv) > 1 else ["2024", "2025"]
    path = update_xg_data(seasons)
    if path:
        df = pd.read_csv(path)
        print(f"\n完成：{path}，共 {len(df)} 条记录，覆盖 {df['league'].nunique()} 联赛")
        feat = build_xg_team_features(df)
        print(f"聚合出 {len(feat)} 支球队的 xG 特征")
    else:
        print("未抓取到任何 xG 数据")
