# Ledger 格式与状态

写入前完整读取既有 ledger，保留历史与 advice。先判断请求是聊天问答、审计新轮次、处理 advice、修复，还是明确请求的 remote reconciliation；不要把一种请求扩展成另一种授权。

## 稳定 ID 与导航

- 同一根因始终沿用 `#[N]`；从 `#[0]` 开始跨轮次递增，不回收。读入兼容 `#N`，新内容写 `#[N]`。
- round 0 前仅有一个 `## Open findings`，按 ID 递增列出纯编号链接；本地已修但尚未被接受的项仍保留。无开放项写 `_None._`。
- 每项只有一个 `<a id="finding-N"></a>`，随后空一行，初始放在 `Agent status` 前。追加后续回复时，把锚点移到第一条新 response / status 前；旧评估与 advice 留在下方。仅 Owner 终态决定不移动锚点。
- 后续反馈追加在原 finding 的平级 bullet 列表，不改旧报告；允许更新终态标题标记、open index 和锚点位置。只在获准维护 ledger 时补缺失导航。
- 新轮次只追加轮次 heading / body；reopen 的详情写回原 finding，新轮次引用该 ID 并计数。

## 新轮次格式

尖括号内容是填写说明，应替换为实际结果；不要复制成已经发生的测试或结论。

```markdown
# Adversarial audit ledger - <change>

Owner fills advice slots; agent appends evidence and responses. History is retained.

## Open findings

- [#\[0\]](#finding-0)

## Audit round 0 - <YYYY-MM-DD HH:MM:SS UTC+08:00>

**Scope:** <files/hunks; local or remote source>
**Source scope revision:** <full SHA; for uncommitted work also baseline SHA and diff identity>
**Mode:** <mode> | **Reviewers:** <actual count, independence and adaptive rationale>
**Verdict:** <go / no-go for stated next step; blockers and limits> - <counts by severity, including reopened>

### What was audited

<requirements, evidence identity, platforms, actual checks and verification limits>

### Findings

#### #[0] [HIGH] <descriptive title>

- **Explanation:** <trigger, mechanism, consequence; understandable without chat history>
- **Category:** <bug / weak-test / doc-drift / workload-mismatch / decoupling>
- **Suggested fix:** <correction> - <fix-now / can-wait>
- **Confidence:** <high / medium / low; uncertainty>
- **Where:** <verified pinned source links or explicit local/remote evidence, revision and lines>
- **How to confirm:** <reproduction/test/reasoning; platform, actual versus unrun checks>
<a id="finding-0"></a>

- **Agent status:** `unreviewed`
- **Owner advice:**

### Clean areas

<areas actually checked and supported by evidence>

### Coverage gaps

<missing checks/environment/data; effect on verdict, or an explicit statement of coverage limits>
```

没有成立的 findings 时，open index 写 `_None._`，Findings 写“无有证据支持的新问题”，仍写 coverage 与 verdict。

## Owner advice、实施和评估

逐条按时间读取，仅处理新增且尚未处理的 advice；保留其原文。空 advice 是 no-op。明确的聊天记录指令也可触发相应工作，但不制造 Owner advice 或 owner acceptance。

| 请求 / 事件 | 动作 |
| --- | --- |
| advice 全值为 `feature`、`delayed`、`out-of-scope` | 记录并尊重决定，不改源码。已接受行为只有出现新相反证据才能再次报告 |
| 调查、解释、证据或方案 | 完成所请求事项，追加 response 与 evaluation；方案不是修复 |
| `bug-to-fix` 或明确实施授权 | 仅修授权范围，验证原失败；当前实施任务的已有授权同样有效 |
| owner `closed` / `finished` | 原 heading 严重级别前仅加一个 `[Closed]` / `[Finished]`，移出 open index，不开新 advice 空槽 |
| owner `reopen` | 去掉终态标记、恢复 open index，追加空 advice 槽 |
| 新证据推翻既有结论 | 沿用 ID，追加 `Agent status: reopened`、round / revision、漏掉的失败与证据，去掉终态标记、恢复 index；等待新的修复授权 |

`closed` 表示结束该项复看，`finished` 表示接受完成。识别 prose 中明确肯定的 closed / finished（忽略大小写和标点），不把否定、引文、假设、问句当决定；最近的明确声明生效。其他 disposition 只按 advice 全值识别，普通 prose 不静默抹掉先前决定；reopen 覆盖旧终态。

每个非空后续任务追加 response / status，以及有证据的 `Agent evaluation`：`resolved`、`partially-resolved`、`still-present` 或 `not-verifiable`；仍开放则再加空 `Owner advice:`。`fixed` 只用于已验证的实施，部分或受阻工作如实写；本地 `fixed` / `resolved` 仍开放，等待接受。纯终态 / reopen 决定不需重复评估；详细 reopen block 自带评估。

```markdown
<a id="finding-0"></a>

- **Agent response:** <answer or correction; what actually changed or was checked>
- **Agent status:** <progress; actual checks and commit hash if any>
- **Agent evaluation:** <assessment with evidence against the original failure>
- **Owner advice:**
```

终态不追加新 advice 槽。新证据 reopen 时，先写 `Agent status: reopened`，再给 finding 完整字段、新证据（可引用旧细节）与 `Agent evaluation`，末尾留空 advice 槽。跟进完成后检查 heading、index、anchor 与 advice 一致。

## 明确请求的远端对账

只有用户明确要求“用远端改动对账 ledger”时执行；单独 fetch 不触发。它不是新审计轮次，也不授权修复。

1. fetch 所指定分支（或复用该请求中已成功的 fetch），确认完整 SHA，不扰动本地工作。fetch / revision 失败则不改 ledger，报告限制。
2. 在该 snapshot 按原失败条件重评范围内 findings。追加 `Agent response`，以 `remote-update` 开头并写 branch / SHA、证据与实际检查；再写 `Agent evaluation`，将锚点移到新 block 前。
3. 仅此流程中新追加的 `resolved` 可自动给原 heading 加 `[Finished]` 并移出 open index，不加 advice 槽；注明这是观测到远端问题已消失，不代表 owner 接受或 agent 实施了修复。其他开放结果留空 advice 槽。
4. 既有终态保持，除非有具体相反证据需要完整 reopen；单纯不确定不能 reopen。之后的 reopen 可覆盖本次自动 finish。检查 heading / index / advice 一致后返回 reconciliation 标识及结论。
