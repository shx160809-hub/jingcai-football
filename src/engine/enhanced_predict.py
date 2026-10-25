"""增强预测引擎：Dixon-Coles + Elo 融合 + XGBoost 二层 Stacking。

三层结构：
  L1  Dixon-Coles 泊松（主模型，输出胜/平/负概率）
  L1' Elo 评分（辅助模型，输出胜/平/负概率）
  L2  XGBoost 分类器（输入 L1 概率 + 近期状态特征 + 欧赔盘口特征，输出最终概率）

L2 训练与预测均按联赛分组（不同联赛特征尺度不同），严格 walk-forward 防泄露。

合规：仅输出概率与比分分布，不构成投注建议。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.common import load_config
from src.engine.dixon_coles import DixonColes
from src.engine.elo import compute_elo, elo_to_prob
from src.features.enhanced_features import build_match_features
from src.strategy.ensemble import blend_probabilities

FEATURE_COLS = [
    # L1 概率
    "dc_p_home", "dc_p_draw", "dc_p_away",
    "elo_p_home", "elo_p_draw", "elo_p_away",
    # 近期状态
    "home_gf", "home_ga", "home_pts_pg",
    "away_gf", "away_ga", "away_pts_pg",
    "gf_diff", "pts_diff",
    # 欧赔盘口
    "avg_mv_home", "avg_mv_draw", "avg_mv_away",
    "b365_mv_home", "b365_mv_draw", "b365_mv_away",
    "avg_p_home", "avg_p_draw", "avg_p_away",
]


def _train_xgb(X: pd.DataFrame, y: np.ndarray, params: dict | None = None):
    """训练梯度提升多分类器（3 类：主胜/平/客胜），返回模型。

    用 scikit-learn 的 HistGradientBoostingClassifier（纯 Cython，无 OpenMP 依赖，
    避免 xgboost/lightgbm 在 macOS 上缺 libomp.dylib 的问题）。
    """
    import warnings

    from sklearn.ensemble import HistGradientBoostingClassifier

    if params is None:
        params = {}
    model = HistGradientBoostingClassifier(
        max_depth=params.get("max_depth", 4),
        learning_rate=params.get("learning_rate", 0.05),
        max_iter=params.get("n_estimators", 300),
        l2_regularization=params.get("l2_regularization", 1.0),
        min_samples_leaf=params.get("min_child_weight", 20),
        max_leaf_nodes=params.get("max_leaf_nodes", 15),
        early_stopping=False,
        random_state=42,
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        model.fit(X, y)
    return model


def _predict_xgb(model, X: pd.DataFrame) -> np.ndarray:
    """梯度提升模型输出三分类概率矩阵 (n, 3)。"""
    return model.predict_proba(X)


class EnhancedPredictor:
    """按联赛维护 DC + Elo + XGBoost 模型，支持增量重拟合。"""

    def __init__(self, xi: float = 0.0065, min_sample: int = 10, w_dc: float = 0.85):
        self.xi = xi
        self.min_sample = min_sample
        self.w_dc = w_dc
        self.dc_models: dict[str, DixonColes] = {}
        self.elo_models: dict[str, dict[str, float]] = {}
        self.xgb_models: dict[str, object] = {}
        self.use_xgb: dict[str, bool] = {}

    def fit(self, history: pd.DataFrame, today: pd.Timestamp | None = None) -> None:
        """在历史数据上按联赛拟合所有层。"""
        if today is None:
            today = history["date"].max()
        for code, lhist in history.groupby("league", observed=True):
            lhist = lhist.sort_values("date")
            if len(lhist) < 50:
                continue
            # L1: Dixon-Coles
            dc = DixonColes(xi=self.xi, min_sample=self.min_sample)
            dc.fit(lhist, today=today)
            self.dc_models[code] = dc
            # L1': Elo
            self.elo_models[code] = compute_elo(lhist)
            # L2: XGBoost（需要特征 + 标签）
            try:
                self._fit_xgb(lhist, code)
            except Exception:  # noqa: BLE001
                self.use_xgb[code] = False

    def _fit_xgb(self, lhist: pd.DataFrame, code: str) -> None:
        """为一个联赛拟合 XGBoost 二层模型。

        严格 walk-forward：每条训练样本的 L1 概率（dc_p_*/elo_p_*）必须用「该场之前」
        的数据拟合的 DC/Elo 模型生成，杜绝未来信息泄露（否则 Stacking 必然过拟合）。
        """
        if len(lhist) < 300:  # XGBoost 需要足够样本
            self.use_xgb[code] = False
            return
        lhist = lhist.sort_values("date").reset_index(drop=True)
        # 状态/欧赔特征（严格用「该场之前」信息）
        feat = build_match_features(lhist)
        feat = feat[feat["home"].notna()].reset_index(drop=True)

        refit_freq = 7  # 每 7 场重拟合一次 L1 模型（与回测一致）
        n = len(lhist)
        l1 = {}
        dc = None
        elo = None
        for i in range(n):
            if dc is None or (i - l1.get("_last_refit", -refit_freq)) >= refit_freq:
                train = lhist.iloc[:i]
                if len(train) < 50:
                    continue
                dc = DixonColes(xi=self.xi, min_sample=self.min_sample)
                dc.fit(train, today=lhist.iloc[i - 1]["date"])
                elo = compute_elo(train)
                l1["_last_refit"] = i
            if dc is None:
                continue
            r = lhist.iloc[i]
            p = dc.predict(r["home"], r["away"])
            ep = elo_to_prob(elo.get(r["home"], 1500), elo.get(r["away"], 1500))
            l1[i] = {
                "dc_p_home": p["p_home"], "dc_p_draw": p["p_draw"], "dc_p_away": p["p_away"],
                "elo_p_home": ep["p_home"], "elo_p_draw": ep["p_draw"], "elo_p_away": ep["p_away"],
            }

        # 只保留有 L1 概率的样本（i >= 首次可拟合位置）构建训练集
        rows = []
        for i, feat_row in feat.iterrows():
            if i not in l1:
                continue
            row = dict(l1[i])
            for c in FEATURE_COLS:
                if c not in row and c in feat_row.index:
                    row[c] = feat_row[c]
            row["truth"] = feat_row["truth"]
            rows.append(row)
        if len(rows) < 300:
            self.use_xgb[code] = False
            return
        df = pd.DataFrame(rows)
        X = df[FEATURE_COLS].astype(float)
        y = df["truth"].astype(int).to_numpy()
        self.xgb_models[code] = _train_xgb(X, y)
        self.use_xgb[code] = True

    def predict(self, home: str, away: str, code: str, features: dict | None = None) -> dict:
        """预测一场比赛，返回融合后的概率。features 为线上特征（可选）。"""
        dc = self.dc_models.get(code)
        if dc is None:
            # 无 DC 模型：回退到市场隐含概率或均匀
            return None

        p_dc = dc.predict(home, away)
        elo = self.elo_models.get(code, {})
        p_elo = elo_to_prob(elo.get(home, 1500), elo.get(away, 1500))

        # 融合 DC + Elo
        blended = blend_probabilities(
            {"p_home": p_dc["p_home"], "p_draw": p_dc["p_draw"], "p_away": p_dc["p_away"]},
            p_elo,
            w_dc=self.w_dc,
        )

        # 若有 XGBoost 且特征齐备，用 L2 最终输出
        if self.use_xgb.get(code) and features is not None:
            try:
                xgb = self.xgb_models[code]
                feat_row = {
                    "dc_p_home": p_dc["p_home"], "dc_p_draw": p_dc["p_draw"], "dc_p_away": p_dc["p_away"],
                    "elo_p_home": p_elo["p_home"], "elo_p_draw": p_elo["p_draw"], "elo_p_away": p_elo["p_away"],
                }
                for c in FEATURE_COLS:
                    if c not in feat_row:
                        feat_row[c] = features.get(c, np.nan)
                X = pd.DataFrame([feat_row])[FEATURE_COLS].astype(float)
                probs = _predict_xgb(xgb, X)[0]
                blended = {
                    "p_home": round(float(probs[0]), 4),
                    "p_draw": round(float(probs[1]), 4),
                    "p_away": round(float(probs[2]), 4),
                }
            except Exception:  # noqa: BLE001
                pass

        return {
            "home": home,
            "away": away,
            "p_home": blended["p_home"],
            "p_draw": blended["p_draw"],
            "p_away": blended["p_away"],
            "expected_home_goals": p_dc["expected_home_goals"],
            "expected_away_goals": p_dc["expected_away_goals"],
            "most_likely_score": p_dc["most_likely_score"],
            "top_scores": _top_scores(p_dc),
            "source": "enhanced",
        }


def _top_scores(pred: dict, k: int = 5) -> list[dict]:
    """从 DC 比分分布提取 Top-k 比分。"""
    matrix = pred.get("score_matrix")
    if matrix is None:
        return []
    flat = np.argsort(matrix.ravel())[::-1][:k]
    scores = []
    for idx in flat:
        i, j = np.unravel_index(idx, matrix.shape)
        scores.append({"score": f"{i}-{j}", "prob": round(float(matrix[i, j]), 4)})
    return scores


if __name__ == "__main__":
    from src.common import data_dir

    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    pred = EnhancedPredictor()
    pred.fit(hist)
    # 演示：取某联赛最后一场作为预测对象
    demo = hist.groupby("league").tail(1).iloc[0]
    code = demo["league"]
    r = pred.predict(demo["home"], demo["away"], code)
    print(f"{code} 演示预测 {demo['home']} vs {demo['away']}:")
    print(r)
