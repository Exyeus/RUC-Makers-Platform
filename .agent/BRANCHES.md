# 学校分支与现场隔离

学校仓库为 `Exyeus/RUC-Makers-Platform`，个人检出的 `origin` 应指向该仓库。`main` 是默认集成分支；日常 `feat/`、`fix/`、`docs/` 小分支通过 PR 合入 `origin/main`。集成分支的设置不表示部署、新环境或产品测试已经验证。

`upstream/dev` 仅用于只读对照上游；不向 upstream 推送，也不把它当学校 PR 目标。`rucmaker` 保留历史。服务器当前 `work/server-development` 含未发布工作，本轮文档交付不携带这些功能或工作区改动。

## 个人干净源树

复用已有个人源树；Windows / WSL 共用该源树，不为跨平台另建 checkout。下列命令仅用于自己的干净检出，**不能在当前服务器工作树执行**。先确认 `git status --short` 没有输出、`origin` 指向学校仓库；有未提交内容时先保留现场、确认归属，停止切换。

```bash
git status --short
git remote -v
git fetch origin
git switch -c feat/example origin/main
```

将 `feat/example` 换成本次任务的唯一分支名；修复和文档可用 `fix/`、`docs/`。完成定向自测与复看后，只推个人分支：

```bash
git push -u origin feat/example
```

PR 的 base repository 选 `Exyeus/RUC-Makers-Platform`，base branch 选 `main`。不直接推 `main`，不将整棵服务器工作树打包或提交。

## 上游仅用于读取

若个人检出已有 `upstream`，先核实 fetch URL；以下只配置本地 push URL，不改变 fetch URL：

```bash
git remote get-url upstream
git config --local --replace-all remote.upstream.pushurl disabled://upstream
git remote get-url --push --all upstream
git fetch upstream
```

预期 push URL 仅为 `disabled://upstream`，用于阻止误推；fetch 仍正常。没有 `upstream` 时无需为了日常任务新增它；需要对照时先核实正确上游地址。

## 服务器现场

当前服务器有其他人的未提交与未跟踪改动，禁止在该现场执行 checkout / switch、merge、rebase、reset、clean 或 pull。不要全树暂存、提交或让文档上传覆盖已有文件改动。

后续整理现有功能时，先确认归属和范围，再在个人干净源树中按小任务整理成 PR；逐项保留并核对原作者改动。`work/server-development` 的存在不表示这些改动已集成或发布。CI 暂不实施，GitHub Actions 当前已禁用；定向自测、一人复看和授权发布照常保留。
