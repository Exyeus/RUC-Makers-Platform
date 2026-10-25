# 简易开发 CI

学校仓库只启用 `Activity CI`：面向 `main` 的 PR、push，以及 GitHub Actions 页的手动运行。使用 Ubuntu 24.04、Python 3.12、PostgreSQL 16 和 uv 0.12.21。仓库 token 仅需读取权限，测试使用临时服务的公开测试凭据，不使用部署密钥。

## 检查范围

唯一检查入口为仓库根目录的 `bash tools/check-activity.sh`：

- agent 技能结构、Bash 和活动 JavaScript 语法。
- 活动代码与测试的 Ruff 检查，以及 Node 内置 crypto 对上传 SHA-256 的交叉校验。
- Django 配置检查、`hackathon` 迁移一致性，以及完整 `tests/hackathon` 回归。

测试使用独立 PostgreSQL，强制真实迁移，Channels/缓存使用内存，文件与日志使用临时目录。不会启动活动平台、初始化或公开活动，也不运行 Docker/Vue 构建或部署。旧的上游工作流从活动目录移除，历史版本可在 Git 中查看。

## 定位失败

打开 PR 的 **Checks → Activity CI**，或仓库 **Actions → Activity CI**，先看失败步骤中的第一条实际错误：

- 安装失败：检查锁文件、Python 3.12、依赖源和网络；不要为了过 CI 删除锁定约束。
- 语法、Ruff 或结构失败：按日志文件路径修正对应内容。
- 迁移一致性失败：检查是否遗漏模型状态对应的迁移，按迁移规范复看；不要自动改写已共享的迁移。
- 测试失败：保留失败用例、输入和预期结果，在同一隔离配置下复现；不删除或弱化断言。

修复后 push 到同一 PR。新运行会取代该分支较旧的运行；记录最终运行链接及结果，需求例子与独立复看照常完成。

## 本地复用

本机 Linux 命令通过 WSL Ubuntu、显式工作目录执行；服务器上使用已有源树。先准备可丢弃的独立 PostgreSQL 实例、`eventyay_ci` 数据库及能创建专属 `test_eventyay_ci` 测试库和所需扩展的测试用户。该数据库只存合成测试数据，不得指向现有业务库。需要 Python 3.12、uv 0.12.21、Node、Git、Bash 和系统 gettext。

从仓库根目录安装锁定依赖：

```bash
uv sync --directory app --locked --no-default-groups --group test --group lint --no-install-project
```

明确设置 `CI_POSTGRES_HOST` 指向该独立实例后再执行检查，例如对专门新建的本地测试实例：

```bash
CI_POSTGRES_HOST=127.0.0.1 CI_POSTGRES_PORT=5432 \
CI_POSTGRES_USER=eventyay CI_POSTGRES_PASSWORD=eventyay \
bash tools/check-activity.sh
```

示例账号仅用于测试服务；按自己的独立实例修改。脚本要求显式 host，避免误用日常运行配置。检查会创建和迁移测试数据库，并生成临时文件；不要用 `start-local.sh` 代替检查入口。

CI 通过说明当前检查范围通过，不代表浏览器视觉、真实外部服务、生产容量或旧业务数据升级已经验证。共享库迁移和发布仍需单独确认目标与恢复安排。
