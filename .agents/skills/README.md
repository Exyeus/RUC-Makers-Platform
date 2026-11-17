# 项目技能入口

按任务选择对应技能，先读其 `SKILL.md`；技能不会代替业务规则和人工判断。质量工具、证据状态和上游版本见 [质量工具入口](../quality/README.md)。

## 开发与审计

- [repo-navigation](repo-navigation/SKILL.md)：定位业务代码与测试。
- [django-run-locally](django-run-locally/SKILL.md)：启动流程及副作用边界。
- [django-run-migrations](django-run-migrations/SKILL.md)：迁移及隔离验证。
- [documentation](documentation/SKILL.md)：同步文档、命令和路径。
- [adversarial-audit](adversarial-audit/SKILL.md)：独立审计与问题账本。
- [hackathon-quality](hackathon-quality/SKILL.md)：按风险协调检查、浏览器证据和审计。
- [hackathon-browser-audit](hackathon-browser-audit/SKILL.md)：隔离环境下按角色探索验收。
- [hackathon-regression](hackathon-regression/SKILL.md)：将已证实的重复问题固化为回归检查。
- [hackathon-review-report](hackathon-review-report/SKILL.md)：汇总真实证据及未测项。
- [playwright-cli](playwright-cli/SKILL.md)：浏览器操作和 Playwright 测试。

## 设计与体验

- [hackathon-ux](hackathon-ux/SKILL.md)：将指定页面的体验要求映射到业务契约。
- [ui-adapt](ui-adapt/SKILL.md)、[ui-audit](ui-audit/SKILL.md)、[ui-clarify](ui-clarify/SKILL.md)、[ui-critique](ui-critique/SKILL.md)、[ui-harden](ui-harden/SKILL.md)、[ui-polish](ui-polish/SKILL.md)：显式调用的适配、审查、文案、体验、边界及细节工作。
- [impeccable](impeccable/SKILL.md) 和 [web-design-guidelines](web-design-guidelines/SKILL.md)：上游设计工具与规范；保留原许可证，版本和校验见 [upstream-lock.json](../quality/upstream-lock.json)。

任务/PR 使用 [统一模板](../templates/)，协作步骤见 [WORKFLOW.md](../WORKFLOW.md)。`SKILL.md` 必需；其他资源按需添加。在仓库根目录运行 `./tools/validate_skill_layout.sh` 检查结构；新增或修改 skill 时另用可用的官方 quick_validate 校验 frontmatter。检查不授权生产写入或发布。
