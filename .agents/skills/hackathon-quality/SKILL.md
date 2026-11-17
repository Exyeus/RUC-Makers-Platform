---
name: hackathon-quality
description: 在黑客松平台完成功能改动、准备 PR 或用户要求自动验收时，协调现有 CI、UI/UX 检查、浏览器证据和独立审计。微小变更按风险缩减。
---

# hackathon-quality

读取仓库根规范和 `.agents/quality/README.md`。先定义用户目标、改动范围、已定需求、源码身份与完成证据。检查前置环境和隔离数据，运行匹配范围的轻量检查、既有 Activity CI 和 Playwright。只按当前风险选择检查；专项技能由用户显式调用，不自动串联读取。实质改动继续使用既有 adversarial-audit，沿用其 ledger 与权限约束。常规改动 1 名独立 reviewer，多风险才 2–3 名。
按 `.agents/quality/REPORTING.md` 交付。关键流程没有可核实证据则记 blocked/not_run。将工具错误与业务失败分开，禁止静默跳过或修改标准来过测。测试或截图变化列入复看。仅在原任务授权范围内修复；审计不授予发布权。
