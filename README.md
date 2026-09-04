# 红利 ETF RSI(6) 定投提醒

每天中国时间 14:00（周一至周五）由 GitHub Actions 自动检查 4 只红利 ETF 的 RSI(6)。非交易日会自动跳过；任意标的 RSI(6) < 20 时，同时通过飞书 Webhook 和 Server酱发送提醒。

## 监控标的

| 名称 | 代码 | 跟踪方向 |
| --- | --- | --- |
| 红利低波ETF华泰柏瑞 | 512890 | 中证红利低波动指数 |
| 中证红利ETF招商 | 515080 | 中证红利指数 |
| 红利低波50ETF南方 | 515450 | 标普中国A股大盘红利低波50指数 |
| 红利低波100ETF景顺 | 515100 | 中证红利低波动100指数 |

> RSI 使用 14:00 左右实时价与前复权历史日线计算，周期为 6。公式采用国内行情软件常见的 SMA 平滑方式。

## GitHub Secrets

打开仓库：`Settings` → `Secrets and variables` → `Actions` → `New repository secret`，添加：

- `FEISHU_WEBHOOK`：飞书群自定义机器人的完整 Webhook URL。
- `SERVERCHAN_SENDKEY`：Server酱 SendKey（通常以 `SCT` 开头；也兼容 `sctp`）。
- `FEISHU_SIGNING_SECRET`：可选。仅当飞书机器人启用了“签名校验”时添加。

密钥不要写进代码，也不要提交到 Git。

## 手动测试

进入 `Actions` → `红利 ETF RSI(6) 监控` → `Run workflow`。

如果勾选 `force_notify`，即使当前 RSI 没有低于 20，也会发送一条测试摘要，用于验证飞书和 Server酱通知配置。

## 定时规则

GitHub Actions 的 cron 使用 UTC，因此工作流配置为：

```text
0 6 * * 1-5
```

即北京时间/中国标准时间周一至周五 14:00。脚本会根据实时行情时间判断当天是否为交易日，节假日不会发送 RSI 提醒。

> GitHub Actions 的 schedule 可能存在几分钟延迟，因此实际执行时间可能略晚于 14:00。
