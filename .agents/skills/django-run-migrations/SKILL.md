---
name: django-run-migrations
description: Generate and review hackathon Django migrations, validate them in an isolated database, and plan authorized shared-database application.
---

# Django 迁移

先读根规范、目标模型与既有迁移依赖。当前活动 app label 为 `hackathon`，命令从 `app/`、当前平台虚拟环境执行，并核实实际 settings/数据库。

1. schema/模型状态变更按需生成 `python manage.py makemigrations hackathon`，逐项检查 operation、默认值、数据回填及现有记录兼容性。`choices` 等元数据也可能进入迁移状态，以实际检测结果为准。
2. 用 `python manage.py makemigrations hackathon --check --dry-run` 检查漏迁移；这不等于已在数据库应用成功。
3. 在隔离测试数据库验证迁移和受影响查询；数据迁移覆盖旧记录、活动/队伍隔离及可重复执行条件。记录升级、失败恢复和未验证项。
4. 不改写已用于共享/生产库的迁移。后续修正新增迁移；仅在确认从未共享应用且当前任务授权时整理未发布迁移。

`migrate`、`RunPython` 与回滚均可能修改/删除数据。应用到共享或生产库前确认授权、目标库、备份/恢复办法及依赖，不因改了 models.py 就自动执行。迁移/关键配置按 [audit](../adversarial-audit/SKILL.md) 复看。

`0012_isolate_existing_hackathons` 会把迁移执行时已有的全部工作区标记为 `program=hackathon`；创客营路由只接受 `maker_camp`，旧入口会返回 404。该数据迁移的 reverse 为 noop，回滚迁移不会恢复原 program。共享库升级前备份并核对现存工作区、访问入口及后续创客营初始化安排，不能无提示应用。
