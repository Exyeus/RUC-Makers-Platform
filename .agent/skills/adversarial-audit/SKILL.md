---
name: adversarial-audit
description: >-
  Independently audit substantive event-platform changes before PR review or merge, and around
  permissions, deadlines, scoring, migrations, or production-configuration changes. Record evidenced
  bugs, weak tests, documentation drift, workload mismatch, and coupling in a persistent ledger.
  Tiny copy/style edits can use a quick human review. An audit alone does not authorize fixes.
---

# Adversarial Audit

用代码和证据质疑结论，报告能说明触发条件、失败机制与后果的问题。没有成立的 finding 是有效结果；不要为了凑数报告猜想或风格偏好。报告使用用户语言，保留字段名和状态 token。

## 何时用、审什么

- 用户点名审计时使用；项目内的实质改动准备 PR / 合并时也使用。权限、截止、评分、迁移、生产配置操作前审计，操作后核实结果并按需追加同一 ledger。
- 纯文案、注释、微小样式可快速人工复看；不为每次小改动启动多人阵容。审计不是 CI，也不要求先建 CI。
- 优先用明确范围，否则审当前会话修改的文件 / hunks。先查实际源码、Git 状态 / diff、基线与当前 revision；本地资料目录不一定是源码工作树。不要假定本地归档与远端检出一致，或为审计另建 checkout。
- 先读根 `agents.md`、匹配的文件规则和当前需求。区分历史笔记、提议和已验证结果；需求矛盾要报告，不自行编规则。可沿依赖追查，但仅将范围内引入或新暴露的缺陷记为 finding。
- 当前活动域在 `app/eventyay/hackathon/`，测试在 `app/tests/hackathon/`，页面使用 Django 模板与原生 JavaScript；以实际检出为准。审计不重复或覆盖根规范。
- 本机 Linux 依赖、脚本和测试经 WSL Ubuntu、显式工作目录运行；Windows 应用与 UI 在 Windows 核实，保持同一源树、分开平台产物。远端遵守其部署规范；审计不自动授权上传、生产写入或恢复。
- 对每个结论写清源码 revision / diff、相关输入、环境和证据。静态推理、本机测试、目标环境验证分开写；没运行的检查、拿不到的源码或日志明确列入 gaps，不把烟测说成完整验证。
- 只读审计不修改源码。若当前任务已明确授权某项修复，按该范围继续；审计本身不扩大修复、提交、合并或发布权限。聊天问题不自动变成 ledger 维护任务。

## 独立复看与五个维度

默认 `adaptive`：常规实质改动用 **1 名独立 reviewer**；存在多个关键风险时按需要用 **2–3 名**。记录模式、实际人数和理由，不要求全员到齐。

使用可用的独立只读 subagent / reviewer；不让实现者的自测充当独立复看。用户指定 `single`（单人）、`generic-N`（N 名通用 reviewer）或 `dimensions`（每维一名）时遵从并说明覆盖方式。按可用容量并行或排队；无法独立委派时如实标注“单人审计、无独立交叉复看”，不假装已完成多人审查。

所有模式都看五个维度；人数不等于维度数，重点随实际风险调整：

1. **Bugs / design**：报名、组队、上传、评分的正确性；跨活动 / 跨队权限，截止与时区，重复请求、事务和失败传播。
2. **Weak tests**：只复述实现的断言、缺少失败 / 边界场景、静默跳过、无法拦截回归的测试。先查源码，不为过测削弱、删除或禁用测试。
3. **Documentation drift**：任务例子、页面提示、接口、FAQ 或部署说明与当前需求 / 实现不一致。
4. **Workload mismatch**：真实团队数量、文件大小、并发提交、评委操作与现有资源下的行为；检查完整性、限制与失败恢复。按已确认要求判断，不编造吞吐、延迟或备份指标，平均值不掩盖单次失败。
5. **Duplication / coupling**：表单、视图、原生 JS 或公共模块重复规则与越界依赖；必须说明具体维护或正确性后果。

给每名 reviewer：源码位置、精确 scope / diff 与 revision、需求、分配风险、既有 findings、证据规则及下方格式。reviewer 独立只读，不改文件、不分配新 ID；返回证据、不确定性、clean areas 和 gaps。高影响但不确定的候选项可再让一名 reviewer 反驳。

协调者核实并与既有轮次去重：同一根因沿用 ID，新证据才 reopen；不成立的候选项不写成事实。轮次从 0 递增，新项按严重程度、影响与耦合排序；复开详情留在原 finding，并计入新一轮。覆盖不全必须影响 verdict。

## Ledger、证据和后续处理

每个改动使用一个持久 Markdown ledger。优先既有文件，其次用户指定位置，再次明确 PR 的 `PR#<number>.md`，否则 `audit-<branch-or-timestamp>.md`。

Git 工作树内优先使用既有忽略目录，并以 `git check-ignore` 确认完整路径；没有则放工作树外，不为报告改忽略规则。非 Git 的资料目录默认用 `adversarial-audits/`；不写进 bare 仓库、不覆盖已有结果。敏感日志、联系方式、选手原件和备份不得进入公开仓库，证据需脱敏。

写入前完整读既有 ledger。保留原文、历史与 Owner advice；全局稳定 ID 从 `#[0]` 递增，不回收，输入兼容旧 `#N`。每轮追加，后续回复留在原 finding 下；只有终态标题、open index 和锚点位置可随状态更新。

首次写 ledger 或处理 advice / fix / reopen / remote reconciliation 前，读 [references/ledger.md](references/ledger.md) 的格式与状态规则。Owner advice、Agent status、Agent evaluation 是独立信息，不代替彼此；空 advice 不触发重复工作，未被接受的本地修复仍在 open index。

已发布源码使用经核实、固定完整 commit SHA 且行号匹配的仓库 permalink。未提交 / 本地证据用可访问的绝对路径链接，另记基线、diff 与行号；远端独有路径注明远端。不要造 URL、混用 revision 或为了有链接而提交 / push。

仅在请求修复时修复授权范围，验证原失败；已有实施授权继续有效。若提交也已获授权，按仓库约定分开独立修复、仅暂存相关文件，在 commit body 引用 `#[N]`，记录实际 hash。审计结论不自动授权合并或发布。

## 每轮输出

严重级别：`CRITICAL`、`HIGH`、`MEDIUM`、`LOW`。使用实际本地日期时间，精确到秒，并写数字 UTC 偏移。

每轮必须包括：

- **Scope** 与 **Source scope revision**：文件 / hunks、完整 SHA；未提交时另记基线 SHA 和 diff 身份，远端 / 本地范围明确。
- **Mode** 与 **Reviewers**：实际模式、人数、独立性与 adaptive 理由。
- **Verdict**：对明确下一步的 `go` / `no-go`、按严重程度计数（含 reopen）、阻塞和未验证要求。`go` 只表达审计判断。
- 每个 finding：稳定 `#[N]`、severity、标题、Explanation、Category、Suggested fix（`fix-now` / `can-wait`）、Confidence、Where、How to confirm、Agent status、Owner advice。
- **Clean areas** 与 **Coverage gaps**：实际查过且有依据的部分、缺少的环境 / 数据 / 验证；不得将未运行检查写为通过。

格式与状态操作使用 ledger reference。聊天只返回可点击 ledger 路径、round / reconciliation 标识、finding 计数及 verdict；仅问问题时在聊天答复，除非要求记录，不修改 ledger。
