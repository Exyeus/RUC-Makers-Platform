---
name: django-run-locally
description: Prepare and start the event-platform preview using the existing checkout while checking database, static-file and service side effects.
---

# 启动与运行边界

先读 `README.rst`、`start-local.sh` 和当前运行配置；确认目标主机、数据库、端口和是否已有服务，不打印密钥。复用现有源树与平台专用依赖。

- 本机 Linux 命令在 WSL Ubuntu、显式工作目录执行；Windows UI 在 Windows。远端服务器运行记录实际主机与环境。
- `./start-local.sh --help` 可查看参数。启动会同步依赖、以 `migrate --check` 检查待应用迁移、收集静态文件并按需构建资源，然后监听（脚本默认 `0.0.0.0:8000`）。它不自动应用迁移或初始化/公开活动。确认目标后按 README 显式执行必要的 `migrate`、`setup_hackathon`；新工作区默认私有，筹备内容核实后再由主办方公开。仅本机预览应明确传 `127.0.0.1:8000`。
- `shutdown-local.sh` 会停止进程；审计、代码同步或普通测试不自动执行启动/停止脚本。
- 测试先读 `app/pyproject.toml` 和测试 settings，核实专属测试数据库、媒体目录、缓存及外部服务。用 `app/.venv` 的 Python/pytest 或当前平台已配置环境，记录命令和结果；不把现有业务库当测试库。
- 启动获准后，验证所选活动入口、登录角色、静态资源和控制台错误。未做浏览器或目标环境检查时明确列为未验证。
