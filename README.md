# 竞彩足球胜平负概率分析工具

> ⚠️ **合规声明**：本工具是**体育数据分析工具**，不是投注建议或中奖预测工具。所有输出（胜/平/负概率、预期比分分布、模型参考值）仅用于**统计分析**。不保证任何结果，不展示"胜率"，不接入任何投注、支付、资金通道。请理性看待数据，遵守中国法律法规，远离非法博彩。

## 项目定位

每天自动获取当日竞彩足球场次与官方赔率，结合球队历史数据，用 Dixon-Coles 泊松模型输出胜/平/负概率，赛后自动结算并记录模型表现，形成可追溯的回测闭环。

**核心价值**：一个框架正确、可追溯、可扩展的统计模型，而非"稳赢"的预测工具。

## 目录结构

```
.
├── .github/workflows/daily.yml   # 定时采集+预测+部署+结算
├── src/
│   ├── collect/                  # 数据采集层
│   │   ├── sporttery.py          # 体彩官方 API（移动端UA）
│   │   └── history.py            # football-data.co.uk 历史数据
│   ├── features/                 # 特征层
│   │   └── build_features.py
│   ├── engine/                   # 预测引擎层
│   │   └── dixon_coles.py        # Dixon-Coles 泊松模型
│   ├── strategy/                 # 策略/融合层
│   │   └── ensemble.py
│   ├── output/                   # 输出层
│   │   └── render.py             # JSON + HTML
│   └── backtest/                 # 回测（防泄露）
│       └── walk_forward.py
├── data/
│   ├── raw/                      # 原始赔率、赛果
│   ├── features/                 # 特征中间产物
│   └── results/                  # 预测+结算结果
├── config/params.yaml            # 模型超参
└── docs/                         # GitHub Pages 输出目录
```

## 每日流水线

```
00:00  GitHub Actions cron 触发
  ├─ 采集：抓当日竞彩场次 + had/hhad 赔率
  ├─ 历史：更新球队历史比分
  ├─ 特征：构建主客队近期状态
  ├─ 预测：Dixon-Coles → 胜/平/负概率 + 比分分布
  ├─ 输出：JSON + 静态 HTML（含合规声明）
  ├─ 部署：push gh-pages → GitHub Pages 发布
  └─ 结算（次日）：读赛果 → 对账 → 追加回测记录
```

## 快速开始

```bash
pip install -r requirements.txt
# 采集当日数据
python -m src.collect.sporttery
# 训练模型并预测
python -m src.engine.dixon_coles
# 生成展示页
python -m src.output.render
```

## 回测与验收指标

- **严格 walk-forward**：每周重拟合，训练集只用 `< 预测日` 的比赛，杜绝未来数据泄露。
- 指标：Log Loss、RPS（Ranked Probability Score）、Brier Score、校准度曲线。
- 基线：与去水后市场隐含概率对比。

## 诚实声明

纯 Dixon-Coles 模型在高效市场上通常**无法稳定跑赢市场**——这是学界公认结论。本项目价值在于框架正确、过程可追溯、可在此基础上扩展 Elo/XGBoost/LightGBM 集成。

## 数据源

- 中国体彩官网 API：`webapi.sporttery.cn`
- 历史数据：football-data.co.uk（免费 CSV）

---

**免责声明**：历史回测表现不代表未来结果。本工具不构成任何形式的投注建议。
