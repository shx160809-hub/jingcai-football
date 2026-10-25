"""增强回测：对比「基线 DC / DC+Elo 融合 / DC+Elo+XGBoost」三档的 walk-forward 表现。

严格 walk-forward：按联赛分组、逐周重拟合、只用历史数据。输出三档 Log Loss /
Brier / RPS / Top-1 对比，用于决定增强层是否值得上线。

用法：python -m src.backtest.enhanced_backtest
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.common import data_dir, load_config
from src.engine.dixon_coles import DixonColes
from src.engine.elo import compute_elo, elo_to_prob
from src.strategy.ensemble import blend_probabilities
from src.features.enhanced_features import _recent_form, _odds_movement_features, _load_xg_map


def _one_hot(hg, ag):
    if hg > ag:
        return np.array([1.0, 0.0, 0.0])
    if hg < ag:
        return np.array([0.0, 0.0, 1.0])
    return np.array([0.0, 1.0, 0.0])


def _log_loss(p, t):
    return float(-np.sum(t * np.log(np.clip(p, 1e-12, 1.0))))


def _brier(p, t):
    return float(np.mean((p - t) ** 2))


def _rps(p, t):
    return float(np.mean((np.cumsum(p) - np.cumsum(t)) ** 2))


def run_enhanced_backtest(
    history: pd.DataFrame,
    refit_freq_days: int = 7,
    min_train: int = 50,
    xgb_min_train: int = 300,
    w_dc: float = 0.85,
) -> dict:
    """返回三档指标对比 + 逐场记录。

    严格 walk-forward：DC/Elo 每 refit_freq_days 重拟合一次，且 XGBoost 层的 L1 概率
    特征也严格用「截至该时点」的模型生成（修复了此前用全量拟合导致的特征泄露）。
    """
    cfg = load_config()
    history = history.sort_values("date").reset_index(drop=True)

    # 三档累计指标
    acc = {
        "dc": {"log_loss": [], "brier": [], "rps": [], "top1": 0, "n": 0},
        "blend": {"log_loss": [], "brier": [], "rps": [], "top1": 0, "n": 0},
        "xgb": {"log_loss": [], "brier": [], "rps": [], "top1": 0, "n": 0},
    }
    records = []

    for league, lhist in history.groupby("league", observed=True):
        lhist = lhist.sort_values("date").reset_index(drop=True)
        dates = lhist["date"]
        n = len(lhist)
        # 首个可预测位置
        start = 0
        cum = np.arange(1, n + 1)
        while start < n and cum[start] < min_train:
            start += 1

        dc = None
        elo = None
        xgb = None
        last_refit = None
        last_xgb_refit = None
        # 状态/欧赔/xG 特征（严格用「该场之前」信息，无泄露），只算一次
        lfeat = _precompute_league_features(lhist, cfg, w_dc)
        # 为该联赛每条样本预留 L1 概率列，后续在 walk-forward 循环里用「当时模型」回填
        l1_cols = ["dc_p_home", "dc_p_draw", "dc_p_away", "elo_p_home", "elo_p_draw", "elo_p_away"]
        for c in l1_cols:
            lfeat[c] = np.nan

        # 预计算所有「重拟合点」索引：从 start 起，每跨越 refit_freq_days 天重拟合一次
        refit_points = [start]
        for i in range(start + 1, n):
            if (dates.iloc[i] - dates.iloc[refit_points[-1]]).days >= refit_freq_days:
                refit_points.append(i)

        for i in range(start, n):
            d = dates.iloc[i]
            # 重拟合 DC + Elo：i 恰为某个重拟合点时触发
            if i in refit_points:
                train = lhist.iloc[:i]
                dc = DixonColes(xi=cfg["dixon_coles"]["xi"], min_sample=cfg["dixon_coles"]["min_sample"])
                dc.fit(train, today=d)
                elo = compute_elo(train)
                # 用「截至 i 的模型」回填 [i, 下一个重拟合点) 区间内所有样本的 L1 概率，
                # 保证这些 L1 概率严格只用 i 之前的数据（无未来泄露），且无遗漏。
                next_point = refit_points[refit_points.index(i) + 1] if i != refit_points[-1] else n
                _backfill_l1_range(lfeat, lhist, i, next_point, dc, elo)

            row = lhist.iloc[i]
            p_dc = dc.predict(row["home"], row["away"])
            p_elo = elo_to_prob(elo.get(row["home"], 1500), elo.get(row["away"], 1500))
            blended = blend_probabilities(
                {"p_home": p_dc["p_home"], "p_draw": p_dc["p_draw"], "p_away": p_dc["p_away"]},
                p_elo, w_dc=w_dc,
            )

            truth = _one_hot(row["home_goals"], row["away_goals"])
            t_label = int(np.argmax(truth))

            pv_dc = np.array([p_dc["p_home"], p_dc["p_draw"], p_dc["p_away"]])
            pv_blend = np.array([blended["p_home"], blended["p_draw"], blended["p_away"]])

            # XGBoost 层（样本充足，30 天重训练一次；训练特征 L1 概率是严格 walk-forward 的）
            pv_xgb = None
            if n >= xgb_min_train and i >= xgb_min_train:
                if last_xgb_refit is None or (d - last_xgb_refit).days >= 30:
                    try:
                        # 只用 [0, i) 的样本训练，且这些样本的 L1 概率都已在之前窗口回填
                        train_feat = lfeat.iloc[:i].dropna(subset=["dc_p_home", "elo_p_home"])
                        xgb = _fit_xgb_from_precomputed(train_feat)
                        last_xgb_refit = d
                    except Exception:  # noqa: BLE001
                        xgb = None
                if xgb is not None:
                    feat = lfeat.iloc[i].to_dict()
                    pv_xgb = _xgb_predict(xgb, feat)

            # 累计指标
            for key, pv in [("dc", pv_dc), ("blend", pv_blend)]:
                acc[key]["log_loss"].append(_log_loss(pv, truth))
                acc[key]["brier"].append(_brier(pv, truth))
                acc[key]["rps"].append(_rps(pv, truth))
                acc[key]["top1"] += 1 if int(np.argmax(pv)) == t_label else 0
                acc[key]["n"] += 1

            if pv_xgb is not None:
                acc["xgb"]["log_loss"].append(_log_loss(pv_xgb, truth))
                acc["xgb"]["brier"].append(_brier(pv_xgb, truth))
                acc["xgb"]["rps"].append(_rps(pv_xgb, truth))
                acc["xgb"]["top1"] += 1 if int(np.argmax(pv_xgb)) == t_label else 0
                acc["xgb"]["n"] += 1

            records.append({
                "league": league, "date": d, "home": row["home"], "away": row["away"],
                "truth_label": t_label,
                "dc_p_home": p_dc["p_home"], "dc_p_draw": p_dc["p_draw"], "dc_p_away": p_dc["p_away"],
                "blend_p_home": blended["p_home"], "blend_p_draw": blended["p_draw"], "blend_p_away": blended["p_away"],
            })

    # 汇总
    out = {}
    for key, a in acc.items():
        if a["n"] == 0:
            continue
        out[key] = {
            "n": a["n"],
            "log_loss": round(float(np.mean(a["log_loss"])), 4),
            "brier": round(float(np.mean(a["brier"])), 4),
            "rps": round(float(np.mean(a["rps"])), 4),
            "top1_accuracy": round(float(a["top1"]) / a["n"], 4),
        }

    # 落盘逐场记录
    if records:
        pd.DataFrame(records).to_csv(data_dir("results") / "enhanced_backtest_records.csv", index=False)

    return out


def _backfill_l1_range(lfeat, lhist, start_idx, end_idx, dc, elo):
    """用「截至 start_idx 拟合」的 dc/elo 模型，为 [start_idx, end_idx) 区间内的样本
    回填 L1 概率。这些概率严格只用 start_idx 之前的数据，无未来泄露。"""
    for j in range(start_idx, end_idx):
        row = lhist.iloc[j]
        p_dc = dc.predict(row["home"], row["away"])
        p_elo = elo_to_prob(elo.get(row["home"], 1500), elo.get(row["away"], 1500))
        lfeat.loc[lfeat.index[j], "dc_p_home"] = p_dc["p_home"]
        lfeat.loc[lfeat.index[j], "dc_p_draw"] = p_dc["p_draw"]
        lfeat.loc[lfeat.index[j], "dc_p_away"] = p_dc["p_away"]
        lfeat.loc[lfeat.index[j], "elo_p_home"] = p_elo["p_home"]
        lfeat.loc[lfeat.index[j], "elo_p_draw"] = p_elo["p_draw"]
        lfeat.loc[lfeat.index[j], "elo_p_away"] = p_elo["p_away"]


def _precompute_league_features(lhist, cfg, w_dc):
    """为一个联赛预构建「状态 + 欧赔 + xG」特征（每场一行，含标签）。

    注意：这里**不再**计算 L1 概率（dc_p_*/elo_p_*），因为 L1 概率必须严格用
    「该场之前」的模型生成（walk-forward），由 _backfill_l1_range 在回测循环里回填。
    状态特征（近期状态/xG/欧赔）严格用「该场之前」的信息，无泄露。
    """
    from src.features.enhanced_features import build_match_features

    feat = build_match_features(lhist)
    feat = feat[feat["home"].notna()].reset_index(drop=True)
    return feat


def _fit_xgb_from_precomputed(feat_df):
    """从预构建特征 DataFrame 训练梯度提升树（scikit-learn HistGradientBoosting）。

    剔除全 NaN 的特征列（如某联赛无 xG 数据时 xG 列全空），否则 HistGB 分箱报错。
    """
    import warnings

    from sklearn.ensemble import HistGradientBoostingClassifier

    df = feat_df.copy()
    # 只保留有实际数据的特征列（排除全 NaN 列）
    usable_cols = [c for c in ALL_FEATS if c in df.columns and not df[c].isna().all()]
    X = df[usable_cols].astype(float)
    y = df["truth"].astype(int).to_numpy()
    # 若有效样本不足 3 类，或样本过少，则不训练
    if len(df) < 30 or len(np.unique(y)) < 3:
        raise ValueError("样本不足")
    model = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.05, max_iter=300,
        l2_regularization=1.0, min_samples_leaf=20,
        max_leaf_nodes=15, early_stopping=False, random_state=42,
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        model.fit(X, y)
    model._feature_cols = usable_cols
    return model


# 统一的特征列顺序（训练与预测必须一致）
CTX_FEATS = [
    "home_gf", "home_ga", "home_pts_pg",
    "away_gf", "away_ga", "away_pts_pg",
    "gf_diff", "pts_diff",
    "home_xgf", "home_xga", "away_xgf", "away_xga",
    "avg_mv_home", "avg_mv_draw", "avg_mv_away",
    "b365_mv_home", "b365_mv_draw", "b365_mv_away",
    "avg_p_home", "avg_p_draw", "avg_p_away",
]
L1_FEATS = ["dc_p_home", "dc_p_draw", "dc_p_away", "elo_p_home", "elo_p_draw", "elo_p_away"]
ALL_FEATS = L1_FEATS + CTX_FEATS

# xG 球队特征查找表（模块加载时一次性载入）
_xg_lookup = _load_xg_map()


def _xgb_predict(xgb, feat_row):
    # 用模型训练时实际使用的特征列（避免预测时引入训练时已剔除的全 NaN 列）
    cols = getattr(xgb, "_feature_cols", ALL_FEATS)
    X = pd.DataFrame([feat_row])[cols].astype(float)
    return xgb.predict_proba(X)[0]


if __name__ == "__main__":
    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    print(f"历史数据：{len(hist)} 场，{hist['league'].nunique()} 联赛")
    result = run_enhanced_backtest(hist)
    print("\n===== 三档对比 =====")
    labels = {"dc": "基线 DC", "blend": "DC+Elo 融合", "xgb": "DC+Elo+XGBoost"}
    for key in ["dc", "blend", "xgb"]:
        if key in result:
            m = result[key]
            print(f"\n{labels[key]}（n={m['n']}）:")
            print(f"  Log Loss: {m['log_loss']}  Brier: {m['brier']}  RPS: {m['rps']}  Top-1: {m['top1_accuracy']}")
