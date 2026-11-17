# 交给本地 Codex 的部署任务

请在 RUC-Makers-Platform 的当前真实工作树中，把本包接成一个小而可靠的开发验收闭环。完成目标是“改动后能运行、能留下证据、能拒绝虚假通过”，不是装尽可能多的 Skill。先做组队和可靠作品提交；登录与主办方接管纳入它们的前后置。

## 0. 先盘点，保留已有资产
读取根规范、`.agents/WORKFLOW.md`、既有 `adversarial-audit`、`doc/development-ci.md`、任务卡和 diff。核实活动模块实际采用的模板/JS/样式、已有上传状态、权限、E2E 和种子数据。沿用 `tools/check-activity.sh`，不再造一套 Activity CI，不覆盖既有 ledger。保留当前未提交改动。

核实 Codex/Node/Python/Playwright 的实际版本、Windows 版本、WSL 发行版、安装方式与 sandbox 权限。运行本包 doctor，再实际启动一个可关闭的浏览器验证。doctor 只是盘点，不等于浏览器可用。

截至来源核验日，Playwright 当前安装说明列 Windows 11+ / WSL，Node 22/24/26；不假定 Windows 10 原生可运行最新版本。若宿主不在支持范围，明确报告，用受支持 WSL 环境运行自动化，并保留原生 Windows/Edge 的人工或兼容版本实测方案。按官网和仓库锁版本协调，不盲升业务依赖。

## 1. 文件安装与发现
- 本包新增 `.agents/quality/` 和五个独立 `hackathon-*` Skill，若本地已有同名先比较再合并。
- 将 `AGENTS.append.md` 精简合入实际根指令，确认 Codex 真正读取了它。`.agents` 内普通 Markdown 不会自动变成后台执行器。
- Skill 放 `.agents/skills/<name>/SKILL.md`；Codex role/hook/config 按已安装版本放 `.codex`。模板可复制后复核，但不要杜撰不支持的字段。
- 第三方工具用仓库既有包管理器或明确隔离的 QA 工具包，锁版本、记录来源 commit/license。审查安装产生的脚本和 Hook；需要信任批准时告诉用户具体哪个定义需要批准。

## 2. Windows / WSL / 阿里云分工
- 按现有仓库规则：Django、数据库、Linux 脚本在 WSL Ubuntu，显式指定工作目录；Windows 浏览器用于原生 UI/字体/缩放核验。CI 可用 Linux 浏览器，单独维护基线。
- Windows 浏览器先访问 WSL localhost 验证连通性。NAT/mirrored/VPN/防火墙需要实际检测；出现连接问题先定位，不全局关防火墙。
- 同一源树可共享，Windows/WSL 不共享 node_modules、虚拟环境或平台构建产物。Codex 在两侧的 home/config/auth 可能不同，分别确认。
- QA 浏览器运行在本机/专用 runner，不默认挤占正式 ECS。阿里云只提供经批准的 staging 服务及独立 DB、OSS bucket/prefix、通知替身。
- 网站真实部署验证使用实际测试域名/HTTPS链路；SSH 隧道可验证服务但不能代替公网 DNS/TLS/跨域检查。不要把数据库、MCP、CDP 或报告服务器端口直接开放公网。

## 3. 最小工具部署
- Impeccable：项目级 Codex skill + 源码/URL scan；保留合理品牌例外，先以 Warning 引入。安装/Hook 参数以该版本 `--help` 和官网为准。
- Vercel web-design-guidelines：官方 Skill；HAX/Nielsen/WCAG作为本地 UX 规则依据，不冒充可安装执行器。
- 自带 check_repo：先全量建立非阻断基线，再 changed-line 扫描活动代码；重点候选需 reviewer 复核。js/html 词法边界有局限，稳定后用 ESLint/模板解析器提取精确字符串/注释。
- Playwright Test + CLI + axe-core：固定流程自动断言，CLI 会话提供探索。优先一个浏览器控制通道；已有 MCP 可复用。
- 报告先用 Playwright HTML/Trace；需要流程分镜时接自带 renderer；Mermaid用于状态/交互图。

## 4. 必须补齐的工程接线
本包不含已绑定当前网站的登录/上传选择器，也没有种子账号命令。请从真实代码读取并实现：
1. 隔离测试账号与活动/队伍/材料初始化；每 worker 独立账号和数据命名空间，合作场景显式同步。
2. 匹配当前业务状态的 `contracts.json`；把每个断言映射到真实 Playwright case，记录契约变更依据。
3. 至少跑通 TEAM-01/02 和 SUBMIT-01/02 的真实 UI；再补断网重试、截止、所有权及主办方查验。正式宣称发布就绪前覆盖全部 required。
4. 上传确认用真实服务端/存储状态核实；Mock 只能作为异常状态专项验证，不作为持久化通过的证据。
5. 统一生成包含状态、revision、步骤、断言、截图/Trace的报告；生成 trusted runner→manifest 适配器，避免人工自填 passed。
6. 让扫描、浏览器测试、证据门禁进入现有 CI 的适当阶段。任何执行失败保持非零；分别处理工具错误和发现缺陷。

## 5. 自动触发
保存后/小批修改只跑轻量检查，可选受信任 Hook；任务完成跑相关固定流程及独立审查；PR Ready 做风险匹配；发布前跑关键全链路。Hook 是便利层，CI 是共享执行入口。不要创建无限自我修复循环；最多两轮自动修复建议值，之后准确报告。

## 6. 验收交付
输出安装版本表、实际命令、账号/数据隔离方式、关键流程证据、完整/局部/blocked状态、源码 SHA+diff、工具不可用的原因和下一条可执行修复命令。至少用一个故意失败的合成样本证明每个新增门禁会拒绝坏结果。

权限、截止、迁移、云资源策略和生产操作沿用仓库授权。不要自动发布、导入真实选手材料、修改真实报名/提交记录、放宽云权限、发送真实通知或运行生产压测。完成本地/隔离环境接线后，以可审查 diff 和报告交付。
