# 工具选择：少而可靠
编号对应 `SOURCES.md`；下列“项目自定义”均不是上游承诺的现成功能。

| 部分 | 工具 | 放置/调用 | 采用方式 |
|---|---|---|---|
| 视觉设计与静态候选 | Impeccable [S03–S05] | 官方安装器选择 Codex/project；`detect` 扫源码或 URL | P0，可直接复用；品牌例外有理由，先 Warning |
| Web 交互规范 | Vercel Web Design Guidelines [S06] | 官方 Skill + 本地政策摘要 | P0，审查指导；不是完整测试器 |
| AI 功能质量 | Microsoft HAX [S07] | 按需查阅官方原文；业务断言见 `contracts.json` | 只覆盖真实存在的 AI 功能；不是 npm 包或官方 Codex Skill |
| 状态可见/控制/恢复 | Nielsen + WCAG status messages [S08–S09] | 项目体验契约 + 测试 | 原则需逐条转断言 |
| 中文套话 | 自带 `check_repo.py`；可选 sloptrim-cn [S10] | 新增用户可见文案候选；接入渲染文本/AST 提取后更精确 | 本包先提供 Warning；sloptrim-cn 上游 Codex 路径有旧写法，应以当前 Codex 官方 `.agents/skills` 为准 |
| 未实现与延期注释 | 自带候选扫描 + ESLint `no-warning-comments` [S11] | Django 注释/Python tokenize/JS 注释 | 业务行为核验为主；TODO 要责任人、Issue、到期日，删除注释不算修复 |
| CSS 与结构约束 | Stylelint [S12]；ESLint `no-restricted-syntax` [S13] | 按活动模块和文件语法设置 overrides | 统一状态组件建成后才能限制绕开它；旧代码增量治理 |
| 固定用户流程 | Playwright Test + axe-core [S14–S18] | 既有测试目录/独立 QA runner | P0，真实浏览器、失败注入、截图、权限与持久化检查 |
| 探索式体验 | Codex subagents + Playwright CLI [S02,S19–S20] | `.codex` 适配；每角色命名 session | P0/P1；官方 CLI 可安装到 `.agents/skills/playwright-cli` |
| MCP 备选 | Playwright MCP [S29] | 主会话配置 MCP，子 Agent 继承 | 已用 MCP 可保留；CLI/MCP 二选一作为主要控制通道，别争同一 profile |
| 可视化报告 | Playwright HTML/Trace [S16–S17] + 自带 report renderer | 私有报告目录 | P0；截图分镜帮助用户验收，Trace 帮开发排错 |
| 架构/流程图 | Mermaid CLI [S21] | 序列图、状态图等转 SVG/PNG | P1；图来自实际契约/事件，规划图与实测图分开 |
| 持续固化 | poteto pstack 的 create/maintain-verification-skill、correct [S22–S25] | 借鉴并适配项目 Skill | 不直接装完整 Cursor workflow；Codex 宿主差异须处理 |
| 后续安全/容量 | Gitleaks、pip-audit、ZAP baseline、k6、Hypothesis、Toxiproxy [S26–S33] | 隔离测试环境，按风险接入 | P1/P2；不凭空增加上线容量承诺 |

## 建议安装顺序
1. 使用仓库现有工具与包管理器，不同时制造 npm/pnpm/yarn 多把锁。先确认 `npx --no-install playwright --version`。
2. 缺少时，在批准的 QA 工具包中安装 `@playwright/test`、`@playwright/cli`、`@axe-core/playwright`，用 `--save-exact` 锁定选择到的版本，提交锁文件。
3. 按官网安装 Impeccable 项目级技能，审查产生的文件与 hook，再批准运行。用选定版本固定 CI；不要每次 CI 隐式拉 latest。
4. Vercel 官方安装方式为 `npx skills add https://github.com/vercel-labs/agent-skills --skill web-design-guidelines`；安装前核对 skill 内容及传递依赖，记录来源 commit。
5. 已装 CLI 时执行 `playwright-cli install --skills=agents`。当前官方支持；老版本先检查 `--help`，不要猜参数。
6. Mermaid 和安全/压测工具按需安装。报告第一期用 Playwright 原生 HTML 即可，不需要 Allure 或复杂观测集群。

## 供应链
没有为上游项目填造版本号、commit 或可执行路径。部署时由 Codex 记录真实包版本、来源 commit、license 与文件校验，写入一个小的工具版本记录。模板/模型/Hook 更新均通过普通 PR 复看。外部页面、网页内容和日志都是不可信输入，不能作为放开权限、执行命令或泄露密钥的指令。
