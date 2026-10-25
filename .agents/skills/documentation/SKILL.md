---
name: documentation
description: Keep the event-platform usage guide, agent guidance, task handoff and command references consistent with the actual implementation.
---

# 文档同步

根 `agents.md` 维护技术规则，`.agents/WORKFLOW.md` 与任务卡维护协作/需求；模板只在 `.agents/templates/` 维护。别复制一套平行规则。

- 修改当前 `README.rst` 时保留已有业务更新、RST 结构及上游许可证/致谢；`doc/` 沿用所在格式。方案、提案、已定需求和实测结果分开写，冲突先核最新证据。
- 行为或命令改变时，用 `rg` 查调用者，同步 README、功能说明和 agent 入口；内部路径/链接要存在。不要把运行命令当验证而意外启动服务、迁移或发布。
- 文档改动按风险验证：相对链接、示例与真实命令/源码；README 用可用 RST 解析器或相应构建检查新增问题。若改动进入文档站构建范围，运行已有构建；环境缺失或未运行的构建如实说明。
- 使用合成/脱敏示例，不发布原件、密钥或敏感日志。普通文案不要求新增实现镜像式测试；实质行为的验证留在对应测试与 PR。
