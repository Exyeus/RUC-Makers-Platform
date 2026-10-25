# 当前项目技能

只保留当前活动平台需要的五个入口：

- [repo-navigation](repo-navigation/SKILL.md)：业务代码与测试位置。
- [django-run-locally](django-run-locally/SKILL.md)：启动流程及副作用边界。
- [django-run-migrations](django-run-migrations/SKILL.md)：迁移生成、隔离验证和共享数据库安全。
- [documentation](documentation/SKILL.md)：文档、命令和路径同步。
- [adversarial-audit](adversarial-audit/SKILL.md)：独立复看与持久记录。

任务/PR 使用 [统一模板](../templates/)；协作步骤见 [WORKFLOW.md](../WORKFLOW.md)，不再维护一套平行 PR 技能。通用 Django/REST/Vue/Docker 操作按实际代码和官方文档查询，不强加当前任务用不到的流程。

`SKILL.md` 必需；其他资源目录按需添加。仓库根运行 `./tools/validate_skill_layout.sh` 检查结构；新增/修改 skill 另用可用的官方 quick_validate 校验 frontmatter。
