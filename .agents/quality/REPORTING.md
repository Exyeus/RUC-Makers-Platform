# 报告与可视化

## 首屏只回答四个问题
改了什么；真实通过了哪些用户场景；哪些问题/缺口需要你决定；证据在哪里。

## 三种呈现
1. Playwright HTML + Trace：调试主源，保留实际步骤、断言、截图、网络及控制台关联。
2. 流程分镜 HTML：一项用户动作一组截图与说明，按角色组织；自带 `render_report.py` 可生成无外部 JS/CDN 的本地页面。
3. Mermaid：状态图表示允许状态/转换，序列图说明 Browser→Django→OSS→DB，流程图说明验收触发。记录来源 revision，分别标注“设计要求”“代码现状”“实际执行”；不要把规划边画成验证通过。

Mermaid CLI 输出 SVG/PNG 适合报告。行为图必须用实际步骤事件或已审定契约生成；本包只提供标注清楚的架构/状态图提案。第一期无需自建全站爬虫、完整 UML 平台或报告数据库。

## 机器可读格式
参考 `examples/not-run-report.json`。`run.kind` 真实网站验收为 `website-acceptance`，示例为 `example-not-executed`。run 记录 revision、source_fingerprint（commit 与工作区 diff 标识）、environment、生成时间。每个 check 含 stable ID、status、kind、exit_code、assertions、steps、evidence。

每个证据为 `{ "kind": "screenshot", "path": "screenshots/step-01.png", "caption": "点击提交后的服务端确认" }`，只允许报告目录内的相对路径。其他种类为 execution-log、server-verification、trace。门禁检查文件存在及必需断言，不鉴定截图真实性；报告应由实际 runner 生成并由受信任 CI 执行，而非让实现 Agent 自填全绿表。

```bash
python .agents/quality/scripts/render_report.py --input /private/run/report.json --out /private/run/index.html
python .agents/quality/scripts/gate_evidence.py --report /private/run/report.json --revision ACTUAL_COMMIT --source-fingerprint TRUSTED_SOURCE_ID --scope submission
```
`gate_evidence.py` 返回 0 只说明指定契约的证据结构通过，不授权发布。缺少完整覆盖或关键用例为 skipped/blocked/flaky/not_run 都返回 1。无效输入为 2。

## 图表与覆盖
优先展示“角色 × 场景 × 故障条件”的通过矩阵和明确计数，不生成貌似精确的 97 分 UX 总分。只对已定义测试集合计算比例；未测、阻塞、跳过分别列明。性能数据报告样本数、分位数和测试负载，不把 3 个 Agent 的体验测试当容量证明。

## 数据安全
默认本地私有报告。Trace 和截图可能含敏感数据，报告渲染器只做 HTML 转义/安全路径检查，**不自动脱敏**。采集时用合成账号和文件，输出前做密钥与个人信息检查；未经审查不上传 GitHub Pages、公开 Issue 或外部截图服务。报告保留期按活动需要设置，不能无限堆积。

运行器应独立计算本次源码标识（commit 加工作区改动及未跟踪源码的内容指纹），并传给 `--source-fingerprint`。不要从待验收 JSON 反抄这个值；门禁会拒绝源码指纹不匹配的旧报告。它仍是结构校验，不是对恶意伪造报告的密码学证明。
