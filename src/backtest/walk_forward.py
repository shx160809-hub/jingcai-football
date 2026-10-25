"""回测层：严格 walk-forward 回测（防数据泄露）。

核心原则：
  1. 按时间顺序滚动，每周（refit_freq_days）重拟合模型。
  2. 训练集只用 date < 预测日期 的比赛，绝不包含未来赛果。
  3. 禁止随机 K-Fold 划分（足球是时序数据，会泄露）。

指标：
  - Log Loss（对数损失，越低越好）
  - Brier Score（均方误差）
  - RPS（Ranked Probability Score，尊重胜>平>负有序性）
  - 校准度（Reliability）：预测 p 的场次实际命中率是否接近 p
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.common import data_dir, load_config
from src.engine.dixon_coles import DixonColes


def _one_hot_outcome(hg: int, ag: int) -> np.ndarray:
    """真实结果 one-hot：[主胜, 平, 客胜]。"""
    if hg > ag:
        return np.array([1.0, 0.0, 0.0])
    if hg < ag:
        return np.array([0.0, 0.0, 1.0])
    return np.array([0.0, 1.0, 0.0])


def _rps(pred: np.ndarray, truth: np.ndarray) -> float:
    """Ranked Probability Score（越小越好）。"""
    pred_cum = np.cumsum(pred)
    truth_cum = np.cumsum(truth)
    return float(np.mean((pred_cum - truth_cum) ** 2))


def _log_loss(pred: np.ndarray, truth: np.ndarray) -> float:
    p = np.clip(pred, 1e-12, 1.0)
    return float(-np.sum(truth * np.log(p)))


def _brier(pred: np.ndarray, truth: np.ndarray) -> float:
    return float(np.mean((pred - truth) ** 2))


def walk_forward_backtest(
    history: pd.DataFrame,
    refit_freq_days: int = 7,
    min_train: int = 50,
    verbose: bool = True,
) -> dict:
    """执行 walk-forward 回测，返回指标与逐场预测记录。

    history 需按 date 升序。每场比赛的预测都用"该场之前"的数据拟合的模型。
    """
    history = history.sort_values("date").reset_index(drop=True)
    dates = sorted(history["date"].unique())
    if len(dates) < 2:
        raise ValueError("历史数据不足，无法回测")

    # 按联赛分组回测（不同联赛攻防尺度不同，必须分开训练）
    records = []
    for league, lhist in history.groupby("league", observed=True):
        lhist = lhist.sort_values("date").reset_index(drop=True)
        records += _walk_forward_one_league(
            lhist, league, refit_freq_days, min_train, verbose
        )

    if not records:
        raise ValueError("回测未产生任何有效预测，请检查训练集规模")

    rec = pd.DataFrame(records)

    # 汇总指标
    metrics = {
        "n_matches": int(len(rec)),
        "log_loss": float(rec["log_loss"].mean()),
        "brier_score": float(rec["brier"].mean()),
        "rps": float(rec["rps"].mean()),
        "top1_accuracy": float((rec["argmax_pred"] == rec["truth_label"]).mean()),
        "calibration": _calibration(rec),
    }

    # 落盘逐场记录
    out = data_dir("results") / "backtest_records.csv"
    rec.to_csv(out, index=False)

    return metrics


def _walk_forward_one_league(
    lhist: pd.DataFrame,
    league: str,
    refit_freq_days: int,
    min_train: int,
    verbose: bool,
) -> list[dict]:
    """对单个联赛做 walk-forward 回测（按周批量重拟合）。"""
    cfg = load_config()
    records = []
    # 按周切分预测窗口
    lhist = lhist.sort_values("date").reset_index(drop=True)
    dates = lhist["date"]

    # 首个可预测日期：累计训练集达到 min_train 之后
    cum = np.arange(1, len(lhist) + 1)
    start = 0
    while start < len(lhist) and cum[start] < min_train:
        start += 1

    model = None
    last_refit = None

    for i in range(start, len(lhist)):
        d = dates.iloc[i]
        # 到达重拟合周期，或首次
        if last_refit is None or (d - last_refit).days >= refit_freq_days:
            train = lhist.iloc[:i]  # 严格只用该场之前的比赛
            model = DixonColes(xi=cfg["dixon_coles"]["xi"], min_sample=cfg["dixon_coles"]["min_sample"])
            model.fit(train, today=d)
            last_refit = d

        row = lhist.iloc[i]
        pred = model.predict(row["home"], row["away"])
        truth = _one_hot_outcome(row["home_goals"], row["away_goals"])
        pvec = np.array([pred["p_home"], pred["p_draw"], pred["p_away"]])

        records.append(
            {
                "league": league,
                "date": d,
                "home": row["home"],
                "away": row["away"],
                "home_goals": row["home_goals"],
                "away_goals": row["away_goals"],
                "p_home": pred["p_home"],
                "p_draw": pred["p_draw"],
                "p_away": pred["p_away"],
                "log_loss": _log_loss(pvec, truth),
                "brier": _brier(pvec, truth),
                "rps": _rps(pvec, truth),
                "argmax_pred": int(np.argmax(pvec)),
                "truth_label": int(np.argmax(truth)),
            }
        )

    if verbose and records:
        print(f"  [回测] {league}：{len(records)} 场")
    return records


def _calibration(rec: pd.DataFrame, bins: int = 5) -> list[dict]:
    """校准度：按预测概率分桶，对比实际命中率。"""
    result = []
    edges = np.linspace(0, 1, bins + 1)
    for label, col in [(0, "p_home"), (1, "p_draw"), (2, "p_away")]:
        for i in range(bins):
            lo, hi = edges[i], edges[i + 1]
            mask = (rec[col] >= lo) & (rec[col] < hi)
            sub = rec[mask]
            if len(sub) < 10:
                continue
            actual = (sub["truth_label"] == label).mean()
            predicted = sub[col].mean()
            result.append(
                {
                    "label": ["主胜", "平局", "客胜"][label],
                    "bin": f"{lo:.1f}-{hi:.1f}",
                    "n": int(len(sub)),
                    "predicted": round(float(predicted), 3),
                    "actual": round(float(actual), 3),
                }
            )
    return result


if __name__ == "__main__":
    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    m = walk_forward_backtest(hist)
    print("\n===== 回测指标 =====")
    for k, v in m.items():
        if k != "calibration":
            print(f"{k}: {v}")
    print("\n===== 校准度 =====")
    for c in m["calibration"]:
        print(c)
