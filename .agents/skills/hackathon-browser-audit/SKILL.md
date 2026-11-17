---
name: hackathon-browser-audit
description: 完成功能后模拟陌生选手、队长、主办方或评委真实操作，进行隔离浏览器的探索验收；不用于生产破坏性测试。
---

# hackathon-browser-audit

读取 `.agents/quality/BROWSER-AUDIT.md`。协调者分配唯一 run/session/account/data namespace、白名单 origin、角色目标和预算。首轮黑盒审查只给用户目标与入口，不向探索者泄露源码/选择器/具体路径。不同角色独立会话；双人合作场景明确同步。
实际操作并保存步骤、可见反馈、截图、Trace与断言；出现失败按普通用户可发现方式尝试恢复。禁止改应用代码、测试契约或截图基线。环境/工具不可用时记 blocked，不能捏造独立子 Agent。发现按现有 adversarial-audit ledger 格式交协调者复现、去重。只在隔离环境内写测试数据，结束后按 run_id 清理。
职责只读不等于 OS 权限隔离；核实宿主能力并披露保障边界。
