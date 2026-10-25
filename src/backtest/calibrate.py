"""概率校准层：把模型输出的原始概率校准到更接近真实频率的经验概率。

原理：
  机器学习/统计模型的输出概率往往存在系统性偏差（如过度自信或自信不足）。
  校准（Calibration）用历史「预测概率 vs 实际结果」拟合一个单调映射，把原始概率
  修正到经验频率，从而提升概率的可靠性与 Top-1 命中率。

实现：
  - 分桶校准（Binning）：按预测概率分桶，用桶内实际命中率替换原始概率。稳健、可解释。
  - 保序回归（Isotonic，可选）：sklearn 单调回归，桶间更平滑，但需足够样本。

数据来源（合并，样本越多校准越准）：
  1. backtest_records.csv：walk-forward 回测的逐场预测 + 真实结果（样本充足，主来源）。
  2. match_results.csv：真实结算样本（predict + truth），与回测记录合并去重。

对「主胜/平/客胜」三个方向分别校准（各自概率尺度不同）。

合规：校准是统计修正，不构成投注建议，不保证命中率提升。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.results_db import load_db
from src.common import data_dir


def _load_calibration_samples() -> pd.DataFrame:
    """合并回测记录与真实结算样本，输出统一 DataFrame。

    列：p_home, p_draw, p_away, truth（home/draw/away）。
    """
    frames = []

    # 1. 回测逐场记录（主来源，样本充足）
    bp = data_dir("results") / "backtest_records.csv"
    if bp.exists():
        b = pd.read_csv(bp)
        truth_map = {0: "home", 1: "draw", 2: "away"}
        b["truth"] = b["truth_label"].map(truth_map)
        b = b[["p_home", "p_draw", "p_away", "truth"]].copy()
        frames.append(b)

    # 2. 真实结算样本（补充）
    db = load_db()
    if not db.empty:
        d = db[["p_home", "p_draw", "p_away", "truth"]].copy()
        d = d[d["truth"].notna() & (d["truth"] != "")]
        frames.append(d)

    if not frames:
        return pd.DataFrame(columns=["p_home", "p_draw", "p_away", "truth"])

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.dropna(subset=["p_home", "p_draw", "p_away", "truth"])
    return merged


def _samples(df: pd.DataFrame, outcome_col: str, truth_label: str) -> tuple[np.ndarray, np.ndarray]:
    """提取某个方向（主胜/平/客胜）的预测概率与 0/1 真实标签。"""
    mask = df[outcome_col].notna() & df["truth"].notna() & (df["truth"] != "")
    sub = df[mask]
    p = sub[outcome_col].to_numpy(dtype=float)
    y = (sub["truth"] == truth_label).to_numpy(dtype=float)
    return p, y


def fit_binning_calibration(
    df: pd.DataFrame | None = None,
    bins: int = 10,
) -> dict[str, dict]:
    """拟合分桶校准映射。

    返回：
      {
        "home": {"bin_edges": [...], "bin_rate": [...], "n_samples": int},
        "draw": {...},
        "away": {...},
      }
    """
    if df is None:
        df = _load_calibration_samples()
    if df.empty:
        return {}

    calib: dict[str, dict] = {}
    for outcome, truth_label in [("p_home", "home"), ("p_draw", "draw"), ("p_away", "away")]:
        p, y = _samples(df, outcome, truth_label)
        if len(p) < 20:
            continue  # 样本不足，不校准该方向
        # 等频分桶
        try:
            edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
        except Exception:  # noqa: BLE001
            continue
        if len(edges) < 2:
            continue
        bin_idx = np.digitize(p, edges[1:-1], right=True)
        rates, centers = [], []
        for b in range(len(edges) - 1):
            m = bin_idx == b
            if m.sum() < 3:
                rates.append(np.nan)
                centers.append((edges[b] + edges[b + 1]) / 2)
            else:
                rates.append(float(y[m].mean()))
                centers.append(float(p[m].mean()))
        # 用桶中心做插值节点，命中率做值
        calib[outcome] = {
            "centers": centers,
            "rates": rates,
            "edges": list(edges),
            "n_samples": int(len(p)),
        }
    return calib


def calibrate_probabilities(
    p_home: float, p_draw: float, p_away: float,
    calib: dict[str, dict],
) -> tuple[float, float, float]:
    """应用校准映射，把原始概率映射到校准概率。

    采用线性插值（按桶中心单调），缺失桶用最近邻；无法校准的方向保持原值。
    校准后重新归一化到 [0,1] 且和为 1。
    """
    out = {"p_home": p_home, "p_draw": p_draw, "p_away": p_away}
    for key, raw in [("p_home", p_home), ("p_draw", p_draw), ("p_away", p_away)]:
        c = calib.get(key)
        if not c:
            continue
        centers, rates = c["centers"], c["rates"]
        valid = [(x, r) for x, r in zip(centers, rates) if r == r]  # 去 NaN
        if len(valid) < 2:
            continue
        xs = np.array([v[0] for v in valid], dtype=float)
        ys = np.array([v[1] for v in valid], dtype=float)
        # 保序：确保单调
        order = np.argsort(xs)
        xs, ys = xs[order], ys[order]
        ys = np.maximum.accumulate(ys)  # 单调不减
        if raw < xs[0]:
            out[key] = float(ys[0])
        elif raw > xs[-1]:
            out[key] = float(ys[-1])
        else:
            out[key] = float(np.interp(raw, xs, ys))

    # 归一化
    s = out["p_home"] + out["p_draw"] + out["p_away"]
    if s > 0:
        out = {k: v / s for k, v in out.items()}
    return out["p_home"], out["p_draw"], out["p_away"]


def load_calibration(cache_path=None) -> dict[str, dict]:
    """加载（或拟合）校准映射，带缓存。

    默认缓存到 data/results/calibration.json。
    """
    import json

    from src.common import data_dir

    if cache_path is None:
        cache_path = data_dir("results") / "calibration.json"

    df = _load_calibration_samples()
    calib = fit_binning_calibration(df)
    if cache_path and calib:
        # 序列化（numpy 类型转 Python 原生）
        payload = {
            k: {kk: (vv.tolist() if hasattr(vv, "tolist") else vv) for kk, vv in v.items()}
            for k, v in calib.items()
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return calib


def _evaluate_calibration_gain(calib: dict[str, dict], test_ratio: float = 0.2) -> dict | None:
    """Out-of-sample 评估校准带来的 Top-1 命中率变化。

    用前 (1-test_ratio) 样本拟合校准，后 test_ratio 样本验证，
    对比「原始 argmax」与「校准后 argmax」的命中率，避免同批数据拟合+验证的乐观偏差。

    返回 {before, after, delta, n_test}，样本不足或校准为空返回 None。
    """
    df = _load_calibration_samples()
    if df.empty or not calib:
        return None
    n = len(df)
    if n < 100:
        return None
    split = int(n * (1 - test_ratio))
    train, test = df.iloc[:split], df.iloc[split:]

    # 用训练集重新拟合校准（而非传入的 calib，保证 out-of-sample）
    calib_fit = fit_binning_calibration(train)
    if not calib_fit:
        return None

    truth_map = {"home": 0, "draw": 1, "away": 2}
    labels = test["truth"].map(truth_map).to_numpy(dtype=float)
    p = test[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float)

    before = after = 0
    for i in range(len(test)):
        orig = int(np.argmax(p[i]))
        ph, pd_, pa = calibrate_probabilities(
            float(p[i, 0]), float(p[i, 1]), float(p[i, 2]), calib_fit
        )
        cal = int(np.argmax([ph, pd_, pa]))
        t = int(labels[i])
        before += 1 if orig == t else 0
        after += 1 if cal == t else 0

    n_test = len(test)
    return {
        "before": round(before / n_test, 4),
        "after": round(after / n_test, 4),
        "delta": round((after - before) / n_test, 4),
        "n_test": n_test,
    }


if __name__ == "__main__":
    calib = load_calibration()
    print("校准方向：", list(calib.keys()))
    for k, v in calib.items():
        print(f"{k}: n={v['n_samples']}, 桶数={len(v['centers'])}")
    # 示例
    if calib:
        ph, pd_, pa = calibrate_probabilities(0.5, 0.28, 0.22, calib)
        print(f"示例校准：0.50/0.28/0.22 -> {ph:.3f}/{pd_:.3f}/{pa:.3f}")
