---
name: repo-navigation
description: Locate event-platform business code, templates, assets and tests in the current Eventyay checkout.
---

# 找到当前活动代码

先核实 Git 根目录、revision 和未提交改动，读 [业务地图](../../context/architecture.md)。

- 活动改动从 `app/eventyay/hackathon/` 及 `app/tests/hackathon/` 开始；页面用 Django 模板和 `app/eventyay/static/hackathon/` 的原生 JS/CSS。
- 使用 `rg` 跟踪 URL → view → form/service/model → template/static → tests；有同名功能时先核实活动 program 和入口权限。
- 上游共享用户、Event、存储等依赖沿引用追查；改公共模块时说明受影响入口。`webapp/` 的 Vue 与 Docker 部署不是活动页面改动的默认前置步骤。
- 文档更新 `README.rst` / `doc/`；协作流程与模板统一在 `.agents/`。不要为跨平台另建源码 checkout。
