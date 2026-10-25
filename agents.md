# 活动平台 Agent 规范

这是所有 coding agents 的项目规范主源。编辑前应用匹配的 `.github/instructions/*.md`；适配文件仅指向本页。协作入口为 [.agents/README.md](.agents/README.md)。

## 当前开发范围

- Python 3.12、Django 5.2+；PostgreSQL、Redis、Celery 为已有后端基础。
- 活动功能：`app/eventyay/hackathon/`；测试：`app/tests/hackathon/`。
- 页面：Django 模板和 `app/eventyay/static/hackathon/` 的原生 JavaScript/CSS。上游 `webapp/` 中的 Vue 构建规则只用于实际修改对应子应用时，不作为活动页面的默认选型。
- 文档：`doc/`、`README.rst`；上游公共模块只在确有依赖时修改，PR 说明受影响入口。
- [architecture.md](.agents/context/architecture.md) 提供业务模块和运行入口；先核实实际源码，不把历史设计或分支名当成已验证结果。

## 实现与安全

1. 事件数据查询沿用 `django_scopes.scope(event=event)`；活动子对象同时绑定当前 hackathon，服务端校验用户角色、队伍归属与对象所属活动，不能只靠隐藏按钮。
2. 用 Django forms/模型和服务层验证输入；表单校验错误保留可理解的反馈。模板不承载复杂业务规则。
3. 新代码使用 `eventyay.*` 命名空间，导入放文件顶部；仅为解决循环依赖使用局部导入。保留具体异常类型。
4. JavaScript 放外部 ES modules，不引入 jQuery 或内联脚本；保持键盘操作、移动端和错误状态可用。第三方 vendored 文件保留许可证。
5. 涉及权限、截止、评分、重试、事务和迁移时验证相关失败/边界场景。查询按需要用 `select_related` / `prefetch_related`。
6. 密钥、联系方式、数据库/备份和未获授权的选手原件不进公开仓库；示例与测试用合成或脱敏数据。作品展示与材料访问权限分开确认。

## 协作与验证

从 [WORKFLOW.md](.agents/WORKFLOW.md) 的主任务卡确定范围和正确例子；学校 PR 面向 `main`，上游只读，分支步骤见 [BRANCHES.md](.agents/BRANCHES.md)。先确认工作树状态和已有改动归属，按授权范围提交，保留他人改动。

实质活动功能改动用 [adversarial-audit](.agents/skills/adversarial-audit/SKILL.md) 独立复看，通常一名 reviewer；小文案/微小样式可人工快速复看。记录实际环境、命令、结果和未验证项。学校 [Activity CI](doc/development-ci.md) 在 main 的 PR、push 和手动运行中执行基础检查；合并前查看结果并处理失败。CI 不替代任务例子、自测和独立复看。

本机 Linux 开发/测试通过 WSL2 Ubuntu、显式工作目录运行；Windows 应用/UI 检查在 Windows。共享一棵源树，分开平台依赖与产物。远端检查标明服务器环境。

启动、数据库迁移、`setup_hackathon --publish` 和生产发布均有副作用；普通审计/代码同步不自动执行这些操作。隔离测试先核实测试数据库、媒体目录和外部服务，不连接真实业务数据。

## 按需技能

| 要做什么 | 入口 |
| --- | --- |
| 找业务代码和测试 | [.agents/skills/repo-navigation/SKILL.md](.agents/skills/repo-navigation/SKILL.md) |
| 本地启动及副作用边界 | [.agents/skills/django-run-locally/SKILL.md](.agents/skills/django-run-locally/SKILL.md) |
| 生成/验证数据库迁移 | [.agents/skills/django-run-migrations/SKILL.md](.agents/skills/django-run-migrations/SKILL.md) |
| 同步文档与命令 | [.agents/skills/documentation/SKILL.md](.agents/skills/documentation/SKILL.md) |
| 独立审计与 ledger | [.agents/skills/adversarial-audit/SKILL.md](.agents/skills/adversarial-audit/SKILL.md) |

只保留解决当前项目具体问题的技能；`SKILL.md` 必需，references/assets/scripts 按需添加，不为目录结构制造空模板。
