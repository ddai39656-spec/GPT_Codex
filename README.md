# Edge Terminal V2

一个面向 Solana、Base、Ethereum 与美股的证据优先交易决策终端。

## 核心原则

- 严格 AND：任何关键条件失败或未知，都不会产生可执行加密币信号。
- 未知即否决：缺少卖出模拟、LP 锁定、真实美元净买或 2× 空间证据时保持静默。
- 新闻不是信号：事件只作为催化剂，必须由价格、成交和安全证据确认。
- 风险先于收益：没有有效止损的计划，仓位自动为 0。
- 可追溯：页面显示数据源、更新时间、通过项、失败项和未知项。

## 数据管线

- `scripts/update_market.py`：48 只高流动性美股、市场环境、相对强度、风险计划、新闻与 SEC 事件。
- `scripts/update_crypto.py`：DEX 多入口发现、真实主池选择、安全字段、连续快照、严格硬条件。
- `scripts/validate_data.py`：阻止任何关键条件未通过却被标记为可执行的数据进入部署。
- `.github/workflows/update-market.yml`：每 15 分钟生成、验证并直接部署，避免机器人数据提交无法触发 Pages 的问题。

## 数据能力边界

DEX Screener 的聚合买卖笔数和成交量不是逐笔美元净买，因此只用于观察，不能通过 5m/15m 真实净买硬条件。GoPlus 单一安全源也不能替代独立卖出模拟。未配置专业链上数据源前，系统可能长期保持 0 个可执行币，这是安全设计而不是故障。

GitHub Pages 只承载公开前端和生成后的 JSON。任何未来 API 密钥必须保存在受保护的后台或 GitHub Secrets 中，禁止写入网页代码。

## 本地验证

```text
python scripts/update_market.py
python scripts/update_crypto.py
python scripts/validate_data.py
python -m http.server 8765
```

访问 `http://127.0.0.1:8765/`。

## 风险声明

本项目是研究与决策辅助工具，不保证盈利，不构成投资建议。微市值加密资产可能因合约、流动性、操纵、交易拥堵和数据延迟发生全部损失。

