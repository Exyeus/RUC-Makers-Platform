# 活动平台代码地图

当前活动域位于 `app/eventyay/hackathon/`，复用 Eventyay 的 Event、用户与权限基础。创客营/黑客松入口是否开放由实际 `program`、配置和权限决定，不能从模块名推断。

| 范围 | 位置 |
| --- | --- |
| 活动、报名、队伍、材料、评分数据 | `models.py`、`forms.py`、`services.py`、`views.py` |
| 当前活动/角色入口 | `permissions.py` 的 `event_view`；进入 Event scope、时区并解析 participant/staff |
| 创意组队 | `match_core.py`、`matching.py`、`matching_forms.py`、`matching_views.py` |
| 社区与活动时段 | `community.py`、`community_views.py`、`calendar.py`、`widgets.py` |
| 页面 | `templates/hackathon/`；模板继承和路由以当前源码为准 |
| 页面资源 | `app/eventyay/static/hackathon/`；原生 JavaScript/CSS，第三方资源在 `static/vendored/` |
| 迁移/管理命令 | `migrations/`、`management/commands/` |
| 测试 | `app/tests/hackathon/`；测试设置由 `app/pyproject.toml` 路由至 `tests.tickets.settings` |
| 使用说明与方案 | `doc/hackathon-workspace.md`、`doc/idea-market-matching.md`；历史 spec 需与新需求核对 |

Event-owned 模型仍需 `django_scopes.scope(event=event)`。进入活动 scope 后，子对象查询仍核实所属 hackathon、队伍和角色。上传使用私有存储与授权下载，不通过公开静态目录直接访问。

服务配置入口为 `app/eventyay/config/settings.py` 和当前环境设置；不打印 `.env`、本地 TOML 或连接凭据。数据库、上传文件、`.local-services` 与平台依赖属于运行现场，不是提交内容。

仓库 `start-local.sh` 会同步依赖、检查待应用迁移、收集/按需构建资源后监听；它不自动应用迁移或初始化/公开活动。`migrate`、`setup_hackathon` 按 README 显式执行。`shutdown-local.sh` 会停止服务。审计与测试先选隔离配置，不把启动脚本当作只读健康检查。
