# 项目质量工具入口

本目录把“规范、执行、证据、复发预防”连接起来。现有 `adversarial-audit` 继续拥有代码审计、ledger、权限边界与最终结论；本目录补充 UI/UX、浏览器实测及报告，不创建竞争的缺陷账本。

## 文件索引
- `CODEX-DEPLOY.md`：部署委派与验收定义。
- `TOOLCHAIN.md`：已核验上游工具及适配取舍。
- `AGENTS.append.md`：合并进根规则的短入口，勿机械覆盖。
- `standards/README.md`：按需读取已锁定的原版 Vercel / Impeccable 文件。
- `BROWSER-AUDIT.md`：黑盒角色审查及故障注入。
- `contracts.json`：关键用户流程与机器可读断言名称；需先核对业务约定。
- `REPORTING.md`：报告结构和证据要求。
- `RECURRENCE.md`：错误如何变成结构约束。
- `scripts/`：标准库工具；`tests/`：工具自身测试。
- `templates/`：需要按仓库调整的配置、TypeScript helper 和 Mermaid 源。
- `NEXT-AUTOMATIONS.md`：依据项目风险整理的后续自动化候选。
- `SOURCES.md`：原始出处。原版技能与资料的 commit/license/hash 见 `upstream-lock.json`。

## 分层运行
| 时机 | 执行 | 验收证据 |
|---|---|---|
| 一小批 UI 文件修改后 | changed-line copy/debt/style lint；有条件时 Impeccable 定向 scan | 文件、行号、原因、修改建议 |
| 一个可体验功能完成 | 原 Activity CI 的相关检查 + 浏览器固定流程 + 1 名独立体验审查 | 真截图、trace、断言结果 |
| PR Ready、多个风险交叉 | 2–3 名独立角色，按风险拆分；相关失败路径 | 同一 revision、去重后的 ledger finding |
| 发布前 | 两条核心旅程 + 登录 + 主办方接管 + 目标域名/上传/恢复验证 | 关键契约齐全，未验证项透明 |
| 同类缺陷再次出现 | 复现→结构改进/类型/lint/测试→证明旧例能触发 | 回归证据、规则执行入口 |

固定场景用 Playwright Test，并行 AI 用于探索与语义判断；不把每次保存都变成多人浏览器审计。首次接入以可复用最小闭环为准，不重建现有上传、权限、CI 体系。

## 门禁与诚实状态
- `passed`：实际跑过且满足全部定义断言；`failed`：实际不满足；`blocked`：环境等阻碍；`not_run`：未运行；`skipped`：主动跳过；`flaky`：重试才通过。
- 关键流程的后五类均不等价于通过；普通 Warning 经过复核才决定阻断。
- 真环境验证和 Mock 验证分别列出；截图证明呈现，服务端证据证明持久化，两者不能互代。
- 业务规则、测试契约、黄金截图或忽略列表发生变化时显式列入 diff，由现有 reviewer 复看。开发者不能为过测静默放宽标准。
