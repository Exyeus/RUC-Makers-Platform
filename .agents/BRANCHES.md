# 学校分支与上游隔离

学校仓库为 `Exyeus/RUC-Makers-Platform`，`origin` 指向该仓库。`main` 是默认集成分支，`feat/`、`fix/`、`docs/` 小分支通过 PR 合入它。合入代码不等于已部署或已完成环境验证。

`upstream/dev` 仅用于读取和对照，不是学校 PR 目标。`rucmaker`、`work/server-development` 是历史开发分支；整理其内容时，先核对与 main 的差异和工作区改动，保留备份，再按本次授权选择有意义的内容。不能把整个现场或历史附件直接打包发布。

## 日常任务

先确认自己的工作树干净、origin 正确；有未提交内容时先确认归属并保留，再决定分支操作。Windows / WSL 复用同一源树。

```bash
git status --short
git remote -v
git fetch origin
git switch -c feat/example origin/main
```

将示例换成任务的唯一分支名。完成定向自测和复看后，`git push -u origin feat/example`，PR base 选 `Exyeus/RUC-Makers-Platform:main`。共享服务器上先确认有无运行服务和他人改动；未经当前任务授权不切换、重置或清理现场。

## 上游只读

已有 upstream 时先核实 fetch URL；以下只禁用本地 push URL，保留 fetch：

```bash
git remote get-url upstream
git config --local --replace-all remote.upstream.pushurl disabled://upstream
git remote get-url --push --all upstream
```

预期 push URL 仅为 `disabled://upstream`。日常推送面向学校 origin；不要向 upstream 推送，不 force push 共享历史。没有 upstream 无需为普通任务新增。

CI 暂不实施，GitHub Actions 当前禁用。main 仍需定向自测、一人复看；发布、迁移、备份恢复单独核实影响与授权。
