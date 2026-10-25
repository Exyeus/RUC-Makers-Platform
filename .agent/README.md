# 活动平台协作入口

先读仓库根目录的 [agents.md](../agents.md)，再读与修改文件匹配的 `.github/instructions/` 规则。它们是技术规范的唯一来源；本目录补充通州活动的需求传递、交接和复看，不覆盖原有规则或 `.agents/skills/`。

| 要做的事 | 从这里开始 |
| --- | --- |
| 接需求、分工、交接、准备演示 | [WORKFLOW.md](WORKFLOW.md) |
| 确认学校分支、隔离上游与服务器现场 | [BRANCHES.md](BRANCHES.md) |
| 开一张任务卡 | [templates/task.md](templates/task.md) |
| 提交一个可复看的 PR | [templates/pull-request.md](templates/pull-request.md) |
| 对实质改动或关键操作做独立审计 | [skills/adversarial-audit/SKILL.md](skills/adversarial-audit/SKILL.md) |
| 查看往届材料聚合统计与建议（本届容量尚未冻结） | [previous-submissions-summary.md](../doc/research/previous-submissions-summary.md) |

当前活动功能在 `app/eventyay/hackathon/`，对应测试在 `app/tests/hackathon/`；页面沿用 Django 模板和原生 JavaScript。编辑前以实际检出的代码和根规范核实路径、命令及分支。

任务卡和 PR 使用可公开的脱敏或合成示例。密钥、联系方式、未获授权的选手原件、完整数据库与备份不得提交公开仓库。
