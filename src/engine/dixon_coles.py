"""预测引擎层：Dixon-Coles 泊松模型。

模型：
  log(λ_home) = α_home + β_away + γ          （主队期望进球）
  log(μ_away) = α_away + β_home              （客队期望进球）
  P(x,y) = τ(x,y; λ,μ,ρ) · Poisson(x;λ) · Poisson(y;μ)

  τ 为低比分依赖修正（0-0, 1-0, 0-1, 1-1），ρ 通常为小负数。
  时间衰减权重 exp(-ξ·days)，近期比赛影响更大。

估计：scipy L-BFGS-B 联合最大化（攻击/防守/主场优势/ρ）。
预测：对任意对阵，闭式计算胜/平/负概率 + 比分分布。

合规：本模块只输出概率与比分分布，不提供任何投注建议。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

from src.common import load_config


class DixonColes:
    def __init__(self, xi: float = 0.0065, min_sample: int = 10):
        self.xi = xi
        self.min_sample = min_sample
        self.attack: dict[str, float] = {}
        self.defence: dict[str, float] = {}
        self.home_adv: float = 0.0
        self.rho: float = 0.0
        self.team_mean_attack = 0.0
        self.team_mean_defence = 0.0

    # ---------- 拟合 ----------
    def fit(self, matches: pd.DataFrame, today: pd.Timestamp | None = None) -> None:
        """在历史比赛上拟合参数。

        matches 需含列：home, away, home_goals, away_goals, date。
        today 用于时间衰减基准（默认取数据最大日期）。
        """
        if today is None:
            today = matches["date"].max()

        teams = sorted(pd.unique(matches[["home", "away"]].values.ravel()))
        self._teams = teams
        n = len(teams)

        # 编码：attack 索引 0..n-1, defence 索引 n..2n-1
        team_idx = {t: i for i, t in enumerate(teams)}
        h_idx = matches["home"].map(team_idx).to_numpy()
        a_idx = matches["away"].map(team_idx).to_numpy()
        hg = matches["home_goals"].to_numpy(dtype=float)
        ag = matches["away_goals"].to_numpy(dtype=float)

        days_ago = (today - matches["date"]).dt.days.to_numpy(dtype=float)
        days_ago = np.clip(days_ago, 0, None)
        weights = np.exp(-self.xi * days_ago)

        def unpack(params):
            attack = params[:n]
            defence = params[n : 2 * n]
            home_adv = params[2 * n]
            rho = params[2 * n + 1]
            return attack, defence, home_adv, rho

        def neg_log_like(params):
            attack, defence, home_adv, rho = unpack(params)
            lam = np.exp(attack[h_idx] + defence[a_idx] + home_adv)
            mu = np.exp(attack[a_idx] + defence[h_idx])

            # 泊松 logpmf（向量化）
            log_p = (
                hg * np.log(lam) - lam - gammaln(hg + 1)
                + ag * np.log(mu) - mu - gammaln(ag + 1)
            )

            # Dixon-Coles τ 修正
            tau = np.ones_like(lam)
            # 0-0
            m00 = (hg == 0) & (ag == 0)
            tau[m00] = 1 - lam[m00] * mu[m00] * rho
            # 1-0
            m10 = (hg == 1) & (ag == 0)
            tau[m10] = 1 + mu[m10] * rho
            # 0-1
            m01 = (hg == 0) & (ag == 1)
            tau[m01] = 1 + lam[m01] * rho
            # 1-1
            m11 = (hg == 1) & (ag == 1)
            tau[m11] = 1 - rho

            log_p += np.log(np.clip(tau, 1e-12, None))

            # 可识别性约束：攻击与防守参数各自和为 0（软惩罚）
            # 消除 attack/defence 之间的平移自由度，避免参数漂移
            penalty = 1e-4 * (attack.sum() ** 2) + 1e-4 * (defence.sum() ** 2)
            return -np.sum(weights * log_p) + penalty

        # 初值
        x0 = np.zeros(2 * n + 2)
        x0[2 * n] = 0.2  # home_adv
        x0[2 * n + 1] = -0.08  # rho

        result = minimize(
            neg_log_like,
            x0,
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-10},
        )

        attack, defence, home_adv, rho = unpack(result.x)
        self.attack = {t: attack[i] for i, t in enumerate(teams)}
        self.defence = {t: defence[i] for i, t in enumerate(teams)}
        self.home_adv = float(home_adv)
        self.rho = float(rho)
        self.team_mean_attack = float(attack.mean())
        self.team_mean_defence = float(defence.mean())
        self._sample_size = {t: 0 for t in teams}
        # 统计每队样本数，用于稀疏降级判断
        for t in teams:
            self._sample_size[t] = int(
                ((matches["home"] == t) | (matches["away"] == t)).sum()
            )

    # ---------- 预测 ----------
    def _expected_goals(self, home: str, away: str) -> tuple[float, float]:
        """计算主客队期望进球，稀疏队回退到联赛均值。"""
        a_h = self.attack.get(home, self.team_mean_attack)
        d_h = self.defence.get(home, self.team_mean_defence)
        a_a = self.attack.get(away, self.team_mean_attack)
        d_a = self.defence.get(away, self.team_mean_defence)

        # 样本不足的队回退到均值先验
        if self._sample_size.get(home, 0) < self.min_sample:
            a_h, d_h = self.team_mean_attack, self.team_mean_defence
        if self._sample_size.get(away, 0) < self.min_sample:
            a_a, d_a = self.team_mean_attack, self.team_mean_defence

        lam = float(np.exp(a_h + d_a + self.home_adv))
        mu = float(np.exp(a_a + d_h))
        return lam, mu

    def predict(self, home: str, away: str, max_goals: int = 8) -> dict:
        """预测一场比赛的胜/平/负概率与比分分布。"""
        lam, mu = self._expected_goals(home, away)

        # 比分分布矩阵
        goals = np.arange(max_goals + 1)
        ph = np.exp(goals * np.log(lam) - lam - gammaln(goals + 1))
        pa = np.exp(goals * np.log(mu) - mu - gammaln(goals + 1))
        matrix = np.outer(ph, pa)

        # τ 修正
        rho = self.rho
        matrix[0, 0] *= 1 - lam * mu * rho
        matrix[1, 0] *= 1 + mu * rho
        matrix[0, 1] *= 1 + lam * rho
        matrix[1, 1] *= 1 - rho
        matrix = np.clip(matrix, 0, None)
        matrix /= matrix.sum()

        # 胜/平/负：matrix[主队进球, 客队进球]
        # 主胜 = 主队进球 > 客队进球 = 下三角（不含对角线）
        p_home = float(matrix[np.tril_indices(max_goals + 1, -1)].sum())
        p_away = float(matrix[np.triu_indices(max_goals + 1, 1)].sum())
        p_draw = float(np.trace(matrix))

        # 期望进球与最可能比分
        exp_home = lam
        exp_away = mu
        most_likely = np.unravel_index(np.argmax(matrix), matrix.shape)

        return {
            "home": home,
            "away": away,
            "p_home": round(p_home, 4),
            "p_draw": round(p_draw, 4),
            "p_away": round(p_away, 4),
            "expected_home_goals": round(exp_home, 3),
            "expected_away_goals": round(exp_away, 3),
            "most_likely_score": f"{most_likely[0]}-{most_likely[1]}",
            "score_matrix": matrix,
        }


def train_and_predict(
    history: pd.DataFrame, fixtures: list[dict], max_goals: int = 8
) -> list[dict]:
    """按联赛分组训练模型，对 fixtures 逐场预测。

    fixtures 每项需含 home, away, league（竞彩中文联赛名）。
    有历史数据的联赛用 Dixon-Coles；无覆盖的用官方赔率反推（降级）。
    """
    cfg = load_config()
    league_map = cfg["collect"].get("league_name_map", {})
    fallback = set(cfg["collect"].get("fallback_leagues", []))

    # 加载中英文队名映射
    import json

    from src.common import ROOT

    team_map_path = ROOT / "config" / "team_name_map.json"
    team_map = json.loads(team_map_path.read_text(encoding="utf-8"))

    # 为每个有历史数据的联赛分别训练模型
    models: dict[str, DixonColes] = {}
    for code, lhist in history.groupby("league", observed=True):
        if len(lhist) < cfg["backtest"]["min_matches_to_train"]:
            continue
        dc = DixonColes(
            xi=cfg["dixon_coles"]["xi"],
            min_sample=cfg["dixon_coles"]["min_sample"],
        )
        dc.fit(lhist)
        models[code] = dc

    from src.strategy.de_vig import de_vig_probabilities

    results = []
    for fx in fixtures:
        league_cn = fx.get("league", "")
        code = league_map.get(league_cn)
        model = models.get(code) if code else None

        # 球队名映射（中文 -> 英文）
        league_team_map = team_map.get(league_cn, {})
        home_en = league_team_map.get(fx["home"], fx["home"])
        away_en = league_team_map.get(fx["away"], fx["away"])

        if model is not None:
            pred = model.predict(home_en, away_en, max_goals=max_goals)
            matrix = pred.pop("score_matrix")
            pred["top_scores"] = _top_scores(matrix)
            pred["source"] = "dixon_coles"
            pred["confidence"] = "high"
            # 模型内部用英文队名查参数，但对外输出的队名回填为竞彩中文名，
            # 避免首页/今日场次/比分等页面显示英文队名。
            pred["home"] = fx["home"]
            pred["away"] = fx["away"]
        else:
            # 降级：官方赔率反推
            dv = de_vig_probabilities(
                fx.get("had_win"), fx.get("had_draw"), fx.get("had_lose")
            )
            if dv is None:
                # 连赔率都没有，均匀先验
                pred = {
                    "home": fx["home"],
                    "away": fx["away"],
                    "p_home": 1 / 3,
                    "p_draw": 1 / 3,
                    "p_away": 1 / 3,
                }
            else:
                pred = {
                    "home": fx["home"],
                    "away": fx["away"],
                    **dv,
                }
            pred["expected_home_goals"] = None
            pred["expected_away_goals"] = None
            pred["most_likely_score"] = None
            pred["top_scores"] = []
            pred["source"] = "odds_implied" if dv else "uniform_prior"
            pred["confidence"] = "low"

        pred.update(
            {
                "match_id": fx.get("match_id"),
                "match_num": fx.get("match_num"),
                "league": league_cn,
                "match_date": fx.get("match_date"),
                "kickoff": fx.get("kickoff"),
                "match_time": fx.get("match_time"),
                "had_win": fx.get("had_win"),
                "had_draw": fx.get("had_draw"),
                "had_lose": fx.get("had_lose"),
            }
        )
        results.append(pred)

    # 可选：应用概率校准（提升概率可靠性与 Top-1 命中率）
    results = _apply_calibration(results)
    return results


def _apply_calibration(results: list[dict]) -> list[dict]:
    """对预测结果应用概率校准（若已拟合且启用）。"""
    try:
        cfg = load_config()
        if not cfg.get("calibration", {}).get("enabled", False):
            return results
        from src.backtest.calibrate import load_calibration, calibrate_probabilities

        calib = load_calibration()
        if not calib:
            return results
        for m in results:
            ph, pd_, pa = calibrate_probabilities(
                m["p_home"], m["p_draw"], m["p_away"], calib
            )
            m["p_home"] = round(ph, 4)
            m["p_draw"] = round(pd_, 4)
            m["p_away"] = round(pa, 4)
            m["calibrated"] = True
        return results
    except Exception:  # noqa: BLE001
        # 校准失败不影响主流程
        return results


def _top_scores(matrix: np.ndarray, k: int = 5) -> list[dict]:
    """返回概率最高的 k 个比分。"""
    flat = np.argsort(matrix.ravel())[::-1][:k]
    scores = []
    for idx in flat:
        i, j = np.unravel_index(idx, matrix.shape)
        scores.append({"score": f"{i}-{j}", "prob": round(float(matrix[i, j]), 4)})
    return scores


if __name__ == "__main__":
    import json
    from datetime import datetime

    from src.common import data_dir

    hist = pd.read_csv(data_dir("raw") / "history.csv", parse_dates=["date"])
    # 简化演示：用历史数据最后一场作为"待预测"，仅验证引擎可跑
    demo = hist.tail(1).iloc[0]
    fixtures = [
        {
            "home": demo["home"],
            "away": demo["away"],
            "league": demo["league"],
            "match_id": "demo",
        }
    ]
    res = train_and_predict(hist, fixtures)
    print(json.dumps(res, ensure_ascii=False, indent=2))
