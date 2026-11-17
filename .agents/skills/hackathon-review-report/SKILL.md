---
name: hackathon-review-report
description: 用户需要快速验收改动、查看截图流程、状态图和真实测试覆盖时，汇总已产生的证据，不虚构截图、测试或百分比。
---

# hackathon-review-report

读取 `.agents/quality/REPORTING.md`。从真实 runner 结果和已核验 ledger 生成首屏结论、场景覆盖、截图分镜和剩余决策。Playwright HTML/Trace保留为开发排错主源；可调用 scripts/render_report.py。
用 Mermaid 表达设计状态机或执行流程，分别标注设计、现状、已实测；图需注明来源 revision。所有证据用报告目录内相对路径，确认存在。展示 failed/blocked/not_run/skipped/flaky，缺测不算通过。发布前关键契约用 gate_evidence.py 检查。
采集和输出均脱敏；不上传真实材料、登录态、签名链接或未审查日志。报告生成成功不表示网站通过。
