# sellput-checker

一个使用 **yfinance** 从 Yahoo Finance 拉取期权链数据，并对 **Sell Put** 合约做“合理性评估”的命令行工具。

> 说明：Yahoo Finance 并没有官方公开 API，本项目使用社区库 **`yfinance`** 抓取网页数据，适合学习和个人研究用途。

## 功能
- 列出股票的所有可用到期日
- 拉取指定到期日的 Put 期权链
- 基于 Black-Scholes 计算近似 Delta、到期价内概率（近似）
- 计算买卖价差、预估成交价（中间价）、现金担保卖出保证金
- 计算单次 & 年化收益率
- 根据你的“检查表”做自动筛选：
  - Delta 落在某区间（例如 0.25~0.35）
  - 年化收益 ≥ 某阈值（例如 15%）
  - 买卖差价 ≤ 某阈值（例如 $0.10）
  - 成交量 / 未平仓量下限等

## 安装
```bash
# 1) 建议使用虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) 安装依赖
pip install -r requirements.txt

# 3) 可本地开发运行（无需安装为包）
python -m sellput_checker.cli --help
```

## 使用示例

### 1) 查看可用到期日
```bash
python -m sellput_checker.cli expirations --ticker NVDA
```

### 2) 查看某个到期日的 Put 期权链（过滤价差/成交量）
```bash
python -m sellput_checker.cli chain --ticker NVDA --exp 2025-09-19 --min-volume 100 --max-spread 0.10
```

### 3) 自动筛选“合理的 Sell Put”候选（按年化回报排序）
```bash
python -m sellput_checker.cli scan --ticker NVDA --exp 2025-09-19   --delta-low 0.25 --delta-high 0.35 --min-annual 0.15 --max-spread 0.10 --min-volume 100
```

### 4) 评估某个具体合约（指定行权价）
```bash
python -m sellput_checker.cli evaluate --ticker NVDA --exp 2025-09-19 --strike 160
```

## 重要提示
- **Delta/概率**是根据 Black-Scholes（欧式）公式及 `impliedVolatility` 估算，真实市场更复杂；本工具仅作参考，不构成投资建议。
- 现金担保卖出（Cash-Secured Put）的保证金粗略估算为：`(Strike - Premium) * 100`。不同券商规则略有差异，请以券商实际为准。
- `yfinance` 抓取的数据可能会延迟或偶有字段缺失，代码已做了基础容错与兜底。

## 运行示意
- `chain`：打印 DataFrame 表格，包含 strike / bid / ask / mid / IV / delta / itm_prob / annualized 等字段。
- `scan`：在 `chain` 基础上，按你的筛选条件挑出候选并排序。

---

Made with ❤ for option sellers.
