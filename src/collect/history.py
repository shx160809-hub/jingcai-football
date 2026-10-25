"""数据采集层：历史比分数据（football-data.co.uk 免费 CSV）。

覆盖欧洲主流联赛历史比分，用于 Dixon-Coles 模型训练与回测。
数据字段：Div, Date, HomeTeam, AwayTeam, FTHG(主队进球), FTAG(客队进球), 及各类赔率。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from src.common import data_dir, load_config

BASE_URL = "https://www.football-data.co.uk/mmz4281"


def download_league_history(league_code: str, season: str) -> pd.DataFrame | None:
    """下载某联赛某赛季的 CSV（赛季格式如 2425）。

    除比分外，同时保留欧赔字段（Bet365/市场均价 开盘+收盘），用于盘口变化分析。
    """
    url = f"{BASE_URL}/{season}/{league_code}.csv"
    try:
        df = pd.read_csv(url, encoding="latin-1")
    except Exception:  # noqa: BLE001
        return None
    if "Date" not in df.columns:
        return None

    # 基础比分字段
    base = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    # 欧赔字段（开盘 + 收盘，Bet365 + 市场均价，Pinnacle 因 API 不稳降为次要）
    odds_cols = [
        "B365H", "B365D", "B365A",          # Bet365 开盘
        "B365CH", "B365CD", "B365CA",        # Bet365 收盘
        "AvgH", "AvgD", "AvgA",              # 市场均价开盘
        "AvgCH", "AvgCD", "AvgCA",           # 市场均价收盘
        "PSH", "PSD", "PSA",                 # Pinnacle 开盘（次要）
        "PSCH", "PSCD", "PSCA",              # Pinnacle 收盘（次要）
    ]
    keep = [c for c in base + odds_cols if c in df.columns]
    df = df[keep].dropna(subset=["FTHG", "FTAG"])
    df = df.rename(
        columns={
            "FTHG": "home_goals",
            "FTAG": "away_goals",
            "HomeTeam": "home",
            "AwayTeam": "away",
        }
    )
    df["league"] = league_code
    df["date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["date"]).drop(columns=["Date"])
    return df


def update_history() -> Path:
    """更新本地历史比分 CSV（合并多联赛多赛季）。"""
    cfg = load_config()
    league_codes = list(cfg["collect"]["leagues"].keys())

    # 赛季范围：最近两个赛季 + 当前赛季（用当前年份推断）
    now = datetime.now()
    seasons = [f"{now.year - 2 % 100:02d}{now.year - 1 % 100:02d}"]
    # 更稳妥：直接生成近3个赛季标识
    seasons = []
    for offset in range(2, -1, -1):
        y1 = (now.year - offset) % 100
        y2 = (now.year - offset + 1) % 100
        seasons.append(f"{y1:02d}{y2:02d}")

    frames = []
    for code in league_codes:
        for season in seasons:
            df = download_league_history(code, season)
            if df is not None and not df.empty:
                frames.append(df)

    if not frames:
        raise RuntimeError("未能下载任何历史数据，请检查网络或联赛代码")

    hist = pd.concat(frames, ignore_index=True)
    hist = hist.sort_values("date").drop_duplicates(
        subset=["date", "home", "away"], keep="last"
    )

    out = data_dir("raw") / "history.csv"
    hist.to_csv(out, index=False)
    return out


if __name__ == "__main__":
    p = update_history()
    print(f"历史数据已更新：{p}，共 {len(pd.read_csv(p))} 场")
