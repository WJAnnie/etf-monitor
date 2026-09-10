# 持仓建议生产链路

## 目标链路

~~~
行情采集（13:52 / 14:02）
        ↓
data/latest_market_summary.json
        ↓
14:00 数据质量门
        ├─ READY          → 允许转发
        ├─ HOLIDAY_SKIP   → 安静跳过
        └─ NOT_READY      → 阻止旧数据/残缺数据转发，并发送失败告警
        ↓
Issue #1 中的 portfolio-advice 评论
        ↓
portfolio-advice-relay.yml
        ↓
飞书 + Server酱（独立尝试）
        ↓
data/pipeline_status.json + workflow artifact
~~~

## 状态字段

每次运行都会记录：

- market_data: ready / not_ready / holiday_skip
- analysis: accepted / blocked / holiday_skip / manual_dry_run
- github_issue: 评论是否已收到
- feishu: success / failed
- serverchan: success / failed
- overall: success / degraded / failed / skipped

失败不会被一个笼统的 completed 标记覆盖。即使其中一个通知渠道失败，另一个渠道仍会继续尝试，最终状态会保存在 artifact 中。

## 重要边界

GitHub Actions 本身不会生成 ChatGPT 的持仓文字分析；它负责在带有 portfolio-advice 标记的正式评论进入 Issue #1 后，做行情新鲜度闸门、双通道投递和可观测性记录。报告生成仍由外部 ChatGPT/人工流程完成。

因此，“14:05 自动分析”在当前架构中的准确含义是：

1. 行情采集和质量检查自动完成；
2. 正式分析评论到达后，自动阻止过期行情被当成当天建议；
3. 双通知结果和失败原因可追踪。

如果未来要实现全自动文字分析，需要另行接入受控的模型调用服务与密钥，不能把 Issue 评论 relay 误称为自动生成分析。
