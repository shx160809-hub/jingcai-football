"""数据采集层：中国体彩官网 API 抓取。

关键点：webapi.sporttery.cn 需使用移动端 User-Agent，否则被腾讯 EdgeOne 拦截（567）。
随机从真实移动设备 UA 池中选取，降低被识别为爬虫的风险。
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

import requests

from src.common import data_dir, load_config

# 移动端 UA 池（iOS + Android）
MOBILE_UAS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Redmi Note 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Mobile Safari/537.36",
]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://www.sporttery.cn",
    "Referer": "https://www.sporttery.cn/",
    "Connection": "keep-alive",
}


def _fetch(cfg: dict) -> dict:
    """调用体彩官方接口，返回 JSON。"""
    url = cfg["collect"]["sporttery_url"]
    params = {
        "channel": cfg["collect"]["channel"],
        "poolCode": cfg["collect"]["pool_codes"],
    }
    last_err = None
    for attempt in range(cfg["collect"]["retry"]):
        headers = dict(HEADERS)
        headers["User-Agent"] = random.choice(MOBILE_UAS)
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=cfg["collect"]["timeout"],
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"体彩 API 抓取失败: {last_err}")


def _build_kickoff(match_date: str | None, match_time: str | None) -> str:
    """拼接开球时间为 'YYYY-MM-DD HH:MM:SS'，缺失时回退到日期。

    体彩 API 中 matchDate 为 'YYYY-MM-DD'，matchTime 为 'HH:MM:SS'。
    """
    date = (match_date or "").strip()
    time_part = (match_time or "").strip()
    if not date:
        return time_part or ""
    if time_part:
        return f"{date} {time_part}"
    return date


def filter_by_kickoff_window(
    matches: list[dict],
    anchor: datetime,
    cutoff_hour: int = 11,
) -> list[dict]:
    """按开球时间窗口过滤：仅保留 [anchor 当天 cutoff_hour 点, 次日 cutoff_hour 点) 的场次。

    这是竞彩「开赛日」的划分惯例：每天以北京时间 cutoff_hour 点（默认 11:00）为界，
    从当天 11:00 到次日 11:00 的场次归为同一「比赛日」，避免跨夜欧洲联赛算错日期。

    - anchor: 锚定日期（通常是「今天」的日期部分）
    - cutoff_hour: 切分小时（默认 11，即每天上午 11:00）

    无 kickoff 信息的场次保留（无法判断，不丢弃）。
    """
    if not matches:
        return matches

    from datetime import timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=tz)

    window_start = anchor.replace(hour=cutoff_hour, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=1)

    kept: list[dict] = []
    for m in matches:
        kickoff = m.get("kickoff") or m.get("match_time") or ""
        ts = _parse_kickoff_ts(kickoff)
        if ts is None:
            # 无法解析开球时间：保留，避免误删
            kept.append(m)
            continue
        if window_start <= ts < window_end:
            kept.append(m)
    return kept


def _parse_kickoff_ts(kickoff: str):
    """解析 'YYYY-MM-DD HH:MM:SS' 或 'YYYY-MM-DD' 为 aware datetime，失败返回 None。"""
    if not kickoff:
        return None
    s = str(kickoff).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            # 假定为北京时间（Asia/Shanghai，无夏令时）
            from zoneinfo import ZoneInfo

            return dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except ValueError:
            continue
    return None


def _parse_matches(payload: dict) -> list[dict]:
    """从官方响应中解析比赛列表与胜平负/让球赔率。"""
    matches = []
    value = payload.get("value", {})
    match_list = value.get("matchInfoList") or value.get("matchList") or []
    for m in match_list:
        # 兼容不同字段命名
        sub = m.get("subMatchList") or [m]
        for sm in sub:
            had = sm.get("had") or {}
            hhad = sm.get("hhad") or {}
            # 精确开球时间 = matchDate + matchTime（matchTime 为 HH:MM:SS）
            kickoff = _build_kickoff(sm.get("matchDate"), sm.get("matchTime"))
            matches.append({
                "match_id": sm.get("matchId") or sm.get("matchNumStr", ""),
                "match_num": sm.get("matchNumStr", ""),
                "league": sm.get("leagueName") or sm.get("leagueAbbName", ""),
                "match_date": sm.get("matchDate") or "",
                "kickoff": kickoff,          # 精确开球时间 'YYYY-MM-DD HH:MM:SS'
                "match_time": kickoff or sm.get("matchDate") or sm.get("matchTime", ""),
                "home": sm.get("homeTeamAbbName", "") or sm.get("homeTeam", ""),
                "away": sm.get("awayTeamAbbName", "") or sm.get("awayTeam", ""),
                # 胜平负赔率
                "had_win": had.get("h"),
                "had_draw": had.get("d"),
                "had_lose": had.get("a"),
                # 让球胜平负
                "hhad_goal_line": hhad.get("goalLine"),
                "hhad_win": hhad.get("h"),
                "hhad_draw": hhad.get("d"),
                "hhad_lose": hhad.get("a"),
            })
    return matches


def fetch_today(day: str | None = None) -> dict:
    """抓取当日竞彩场次，返回结构化数据并落盘。

    仅保留「当日北京时间 cutoff_hour 点（默认 11:00）到次日 11:00」开球的场次。
    """
    cfg = load_config()
    day = day or datetime.now().strftime("%Y%m%d")
    cutoff_hour = int(cfg["collect"].get("day_cutoff_hour", 11))

    payload = _fetch(cfg)
    matches = _parse_matches(payload)

    # 按开球时间窗口过滤（当日 11:00 ~ 次日 11:00）
    anchor = datetime.strptime(day, "%Y%m%d")
    kept = filter_by_kickoff_window(matches, anchor, cutoff_hour=cutoff_hour)

    result = {
        "date": day,
        "fetched_at": datetime.now().isoformat(),
        "cutoff_hour": cutoff_hour,
        "disclaimer": cfg["output"]["disclaimer"],
        "matches": kept,
    }

    out = data_dir("raw") / f"odds_{day}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 归档体彩官方赔率到历史 CSV（供将来回测验证「体彩赔率去水」的真实质量）
    _archive_odds(kept, day)

    return result


def _archive_odds(matches: list[dict], day: str) -> None:
    """把当日采集的体彩赔率追加归档到 data/raw/market_odds_history.csv。

    长期积累体彩官方赔率历史，将来可与官方赛果对账，回测验证
    「体彩赔率去水概率」的质量（区别于 football-data 的市场收盘赔率）。
    幂等：同一 match_num + day 不重复写入。
    """
    import pandas as pd

    path = data_dir("raw") / "market_odds_history.csv"
    rows = []
    for m in matches:
        rows.append({
            "day": day,
            "match_num": m.get("match_num", ""),
            "match_id": m.get("match_id", ""),
            "league": m.get("league", ""),
            "home": m.get("home", ""),
            "away": m.get("away", ""),
            "kickoff": m.get("kickoff", ""),
            "had_win": m.get("had_win"),
            "had_draw": m.get("had_draw"),
            "had_lose": m.get("had_lose"),
            "hhad_goal_line": m.get("hhad_goal_line"),
            "hhad_win": m.get("hhad_win"),
            "hhad_draw": m.get("hhad_draw"),
            "hhad_lose": m.get("hhad_lose"),
        })
    if not rows:
        return
    new_df = pd.DataFrame(rows)
    try:
        if path.exists():
            old_df = pd.read_csv(path, dtype=str)
            # 幂等：按 (day, match_num) 去重
            old_key = set(zip(old_df["day"], old_df["match_num"]))
            new_df = new_df[~new_df.apply(lambda r: (r["day"], r["match_num"]) in old_key, axis=1)]
            if new_df.empty:
                return
            combined = pd.concat([old_df, new_df], ignore_index=True)
        else:
            combined = new_df
        combined.to_csv(path, index=False, encoding="utf-8")
    except Exception:  # noqa: BLE001
        # 归档失败不影响主流程
        pass


def _fetch_results(day: str, page_no: int = 1, page_size: int = 100) -> dict:
    """调用体彩官方赛果接口（getUniformMatchResultV1），返回 JSON。

    关键参数（缺一不可，否则报 P0001 参数不合法）：
      channel=c&matchPage=1&pcOrWap=0&leagueId=&matchBeginDate=YYYY-MM-DD&matchEndDate=YYYY-MM-DD
    """
    cfg = load_config()
    url = cfg["collect"].get("result_url")
    if not url:
        raise RuntimeError("config 缺少 collect.result_url")

    date_str = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    params = {
        "channel": cfg["collect"].get("channel", "c"),
        "matchPage": "1",
        "pcOrWap": "0",
        "leagueId": "",
        "matchBeginDate": date_str,
        "matchEndDate": date_str,
        "pageSize": str(page_size),
        "pageNo": str(page_no),
        "isFix": "0",
    }
    last_err = None
    for attempt in range(cfg["collect"].get("retry", 3)):
        headers = dict(HEADERS)
        headers["User-Agent"] = random.choice(MOBILE_UAS)
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=cfg["collect"]["timeout"]
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errorCode") not in ("0", 0):
                raise RuntimeError(f"赛果接口返回错误: {data.get('errorMessage')}")
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"体彩赛果抓取失败: {last_err}")


def _parse_results(payload: dict) -> list[dict]:
    """解析赛果接口响应，输出标准化的赛果记录列表。"""
    value = payload.get("value") or {}
    match_result = value.get("matchResult") or []
    results = []
    for m in match_result:
        full = m.get("sectionsNo999", "") or ""   # 全场比分 "1:2"
        half = m.get("sectionsNo1", "") or ""     # 半场比分 "0:1"
        home_goals = away_goals = None
        if full and ":" in full:
            try:
                home_goals, away_goals = (int(x) for x in full.split(":"))
            except (ValueError, TypeError):
                home_goals = away_goals = None
        results.append({
            "match_num": m.get("matchNumStr", ""),      # 如 "周日019"
            "match_id": m.get("matchId"),
            "match_date": m.get("matchDate", ""),
            "league": m.get("leagueName") or m.get("leagueNameAbbr", ""),
            "home": m.get("allHomeTeam") or m.get("homeTeam", ""),
            "away": m.get("allAwayTeam") or m.get("awayTeam", ""),
            "home_goals": home_goals,
            "away_goals": away_goals,
            "half_score": half,
            "win_flag": m.get("winFlag", ""),           # H=主胜 A=客胜 D=平
            "goal_line": m.get("goalLine", ""),
            "had_win": m.get("h"),
            "had_draw": m.get("d"),
            "had_lose": m.get("a"),
            "status": m.get("matchResultStatus", ""),
        })
    return results


def fetch_results(day: str | None = None) -> dict:
    """抓取指定比赛日的官方赛果，返回结构化数据并落盘。

    day 为 YYYYMMDD 比赛日（即按 11:00 切分的开赛日）。
    分页拉取全部场次，写入 data/raw/results_{day}.json。
    """
    day = day or datetime.now().strftime("%Y%m%d")
    cfg = load_config()
    page_size = 100
    all_results: list[dict] = []
    page_no = 1
    while True:
        payload = _fetch_results(day, page_no=page_no, page_size=page_size)
        batch = _parse_results(payload)
        all_results.extend(batch)
        value = payload.get("value") or {}
        pages = int(value.get("pages") or 0)
        if pages <= 1 or page_no >= pages:
            break
        page_no += 1

    result = {
        "date": day,
        "fetched_at": datetime.now().isoformat(),
        "disclaimer": cfg["output"]["disclaimer"],
        "results": all_results,
    }
    out = data_dir("raw") / f"results_{day}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _weekday_cn(dt: datetime) -> str:
    """返回 datetime 对应的中文星期（周一~周日）。"""
    return "周" + "一二三四五六日"[dt.weekday()]


def _current_matchday_week(cutoff_hour: int = 11) -> tuple[str, str]:
    """返回「当前比赛日」的星期前缀与锚定日期（YYYYMMDD）。

    比赛日按北京时间 cutoff_hour 点切分（默认 11:00）：
      - 当前小时 < cutoff_hour：仍处于上一个比赛日窗口（编号为「昨天」的星期）
      - 当前小时 >= cutoff_hour：已进入今日比赛日窗口（编号为「今天」的星期）

    返回 (星期前缀, 锚定日期 YYYYMMDD)。例如周日 22:00 -> ("周日", 当天)；
    周一 08:00 -> ("周日", 前一天)。
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.hour < cutoff_hour:
        anchor = now - timedelta(days=1)
    else:
        anchor = now
    return _weekday_cn(anchor), anchor.strftime("%Y%m%d")


def fetch_live_board(day: str | None = None) -> dict:
    """抓取「今日赛果」实时看板：仅覆盖「当前比赛日」的竞彩赛事。

    数据来源合并两路（体彩官方，权威且实时）：
      1. 赛果接口（getUniformMatchResultV1）：已开球场次，含实时/完赛比分、状态、胜负方向
      2. 在售接口（getMatchCalculatorV1）：尚未开球的场次

    两路按 match_num 互补（已开球只出现在赛果接口、未开球只出现在在售接口）。

    「当前比赛日」界定：按竞彩编号的星期前缀（如「周日001」）与当前比赛日星期对齐。
    比赛日按北京时间 11:00 切分（day_cutoff_hour）：11:00 前编号为「昨天」星期，
    11:00 后编号为「今天」星期——做到「每天 11:00 自动切换到当日比赛」。

    每条记录字段：
      match_num / league / home / away / kickoff / match_date
      home_goals / away_goals / half_score   （进行中/完赛才有）
      status（1=进行中/未开球, 2=已完赛）
      state（not_started / live / finished）
      win_flag（H/A/D，仅完赛）

    落盘 data/raw/live_board_{day}.json。
    """
    day = day or datetime.now().strftime("%Y%m%d")
    cfg = load_config()
    cutoff_hour = int(cfg["collect"].get("day_cutoff_hour", 11))

    # 当前比赛日的星期前缀（用于过滤赛果接口里混入的上一比赛日场次）
    today_week, _anchor_day = _current_matchday_week(cutoff_hour=cutoff_hour)

    # 1. 赛果接口：已开球（进行中 + 已完赛）
    live: list[dict] = []
    try:
        result_payload = fetch_results(day)
        for r in result_payload.get("results", []):
            num = r.get("match_num", "")
            # 仅保留「当前比赛日」的场次（按编号星期前缀过滤）
            if num and not num.startswith(today_week):
                continue
            hg = r.get("home_goals")
            ag = r.get("away_goals")
            has_score = hg is not None and ag is not None
            st = str(r.get("status", ""))
            if st == "2":
                state = "finished"
            elif has_score:
                state = "live"  # 已开球且有比分，进行中
            else:
                state = "not_started"  # 已开球但比分未更新（刚开场）
            live.append({
                "match_num": num,
                "match_id": r.get("match_id"),
                "league": r.get("league", ""),
                "home": r.get("home", ""),
                "away": r.get("away", ""),
                "match_date": r.get("match_date", ""),
                "kickoff": r.get("match_date", ""),
                "home_goals": hg,
                "away_goals": ag,
                "half_score": r.get("half_score", ""),
                "status": st,
                "state": state,
                "win_flag": r.get("win_flag", ""),
                "goal_line": r.get("goal_line", ""),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[今日赛果] 赛果接口抓取失败: {e}")

    # 2. 在售接口：尚未开球的场次（补全赛果接口没有的 match_num）
    covered = {m["match_num"] for m in live if m.get("match_num")}
    try:
        odds_payload = fetch_today(day)
        for m in odds_payload.get("matches", []):
            num = m.get("match_num", "")
            if num and num in covered:
                continue
            # 同样按星期前缀过滤（在售接口理论上已被 11:00 窗口过滤，双保险）
            if num and not num.startswith(today_week):
                continue
            live.append({
                "match_num": num,
                "match_id": m.get("match_id"),
                "league": m.get("league", ""),
                "home": m.get("home", ""),
                "away": m.get("away", ""),
                "match_date": m.get("match_date", ""),
                "kickoff": m.get("kickoff", "") or m.get("match_time", ""),
                "home_goals": None,
                "away_goals": None,
                "half_score": "",
                "status": "",
                "state": "not_started",
                "win_flag": "",
                "goal_line": m.get("hhad_goal_line", ""),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[今日赛果] 在售接口抓取失败: {e}")

    # 排序：进行中/已完赛在前，未开球按开球时间在后
    def _key(m: dict) -> tuple:
        order = {"live": 0, "finished": 1, "not_started": 2}
        return (order.get(m.get("state", "not_started"), 2), str(m.get("kickoff", "")))

    live.sort(key=_key)

    result = {
        "date": day,
        "fetched_at": datetime.now().isoformat(),
        "disclaimer": cfg["output"]["disclaimer"],
        "matches": live,
    }
    out = data_dir("raw") / f"live_board_{day}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    r = fetch_today()
    n = len(r["matches"])
    print(f"抓取到 {n} 场比赛（当日 {r['cutoff_hour']}:00 ~ 次日 {r['cutoff_hour']}:00 开球），已写入 data/raw/odds_{r['date']}.json")
