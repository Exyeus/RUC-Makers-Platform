# Idea Market 组队匹配

本页说明当前组队匹配的实现与操作边界。历史设计附件保留在服务器备份中，不随代码分发；需求变更以最新任务卡和验收例子为准。
复用 `hackathon` 的账户、活动作用域、Participant、Team、作品与审计日志。

## 开放与政策

主办方进入 `/hackathon/{slug}/manage/matching/`，批准并保存本轮政策：

- 是否开放 Idea Market，以及是否批准正式入队。默认均关闭。
- 正式录取容量默认 150，审核录取时在活动锁内检查剩余名额。
- 每队人数上限：新活动默认 3，已有活动的既存数值不自动改写。
- 原方案的 Idea 三要素字数要求：默认 0，表示未批准发布，不自行猜测原方案字数。
- 是否批准单人正式参赛。默认关闭；暂停匹配不影响继续浏览。

待录取人员可讨论和发送意向，只有 `admission_state=admitted` 的人员能进入正式名单。
主办方在工作台逐人审核，包括原有队伍的选手；撤销已成队者资格前需先按名单规则处理成员关系。
已有成员全部确认增员；队长不能替他人确认。队长退出前先在组队进度中交接；最后一名成员可退出并解散。
冻结时保存名单与 roster_version 快照；按数据库最后校验时刻判断截止。
积分 +5、冲正、换队奖励均没有自动启用。推荐分不参与报名资格或积分。
可见性、通知期限和保留期仍须活动组织方按通知约定，不能把技术默认值当作业务批准。

## 使用

选手入口 `/hackathon/{slug}/market/`，包括找项目、找队友和组队进度。
匹配资料分别记录独立完成、指导下完成、愿意学习、技能需求、兴趣、目标、时间和异步意愿。
时段按半小时保存为绝对 UTC 编号，输入须带时区；未知时间和明确无交集分别处理。
联系方式独立保存，只有双方同意交流且本人授权披露时才能交换，不复用报名联系方式。
仅推荐可见的资料不进入人员浏览列表；暂停、屏蔽、资格和满员状态在每次计算及正式入队前重新检查。

正式名单提案包括完整成员 ID、Idea、赛道、队名和合作约定；提案绑定资料、Idea、政策和已有名单版本。
任何一人拒绝则提案终止；资料或名单变化使提案失效。旧同意不能用于另一份名单。
组队型 Idea 保留作者；揭榜型允许多个队伍独立实现，作者不自动占用名额。
启用后，原邀请码、申请审批和新建队伍入口均不能直接增员，而是转入全员确认。

## 事务与数据库

写操作按活动行排他锁 → 成员按 ID 排序 → 队伍 → 提案串行化；活动排他锁比 Spec 建议的共享锁更保守。
150 人规模优先保证正确性，尚未声称单活动高并发性能。
最后一次同意、正式成员关系、名单版本、冲突意向撤销、outbox 和幂等回执在同一事务提交。
Participant.team 是兼容现有作品/评审代码的唯一当前名单指针；TeamMembership 保存加入/离开历史，条件唯一索引限制一个活跃成员关系。
PostgreSQL 触发器校验跨活动外键、人数上限，并自动同步旧入口与 SQL 写入的成员历史。
迁移回填已有名单，不替已有成员伪造同意记录或发积分。

幂等键作用域为活动、操作、用户；同键同正文重放回执，同键不同正文返回 409。
失败通过保存点回滚业务写入，仍可读取失败回执。响应丢失后可重试或查询回执。
通知使用持久站内状态；outbox 消费以 source/consumer 唯一记录去重，不把网络投递描述为恰好一次。

定期执行以下命令处理过期状态与站内回执消费（可由现有任务调度器调用）：

```bash
cd app
.venv/bin/python manage.py process_matching_outbox --event EVENT_SLUG
```

尚未接入短信、微信或邮件通知提供方。未读状态和回执仍可在站内查询。
删除个人匹配资料同时清除联系方式、反馈和活动推荐快照，撤销相关意向与待确认提案；正式名单与审计同意保留。

## 接口

以下路径相对于 `/hackathon/{slug}/market/`。均要求已登录及活动访问权限；普通写请求要求 CSRF 和 `Idempotency-Key` 请求头。
HTML 表单携带等价的 `idempotency_key`。回执仅本人可查；运营接口需主办方权限。

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `api/profile/` | GET / PATCH / POST | 本人资料与版本 |
| `api/people/`、`api/teams/`、`api/ideas/` | GET | 可见卡片 |
| `api/recommendations/?target=people\|teams` | GET | 当前硬过滤、版本、说明与缺项 |
| `api/ideas/` | POST / PATCH | 发布 / 编辑本人 Idea，含 version、三要素、标签与角色 |
| `api/intents/` | POST | recipient_id、message、可选 team_id |
| `api/intent-decision/` | POST | intent_id、version、decision=accept/decline/cancel |
| `api/proposals/` | POST | member_ids、idea_id、track_id、team_name、agreement、可选 target_team_id |
| `api/consents/` | POST | proposal_id、proposal_hash、decision=accept/decline |
| `api/progress/` | GET | 本人意向、授权联系方式、完整提案与决定 |
| `api/feedback/`、`api/block/` | POST | 收藏/暂不考虑、双向屏蔽过滤 |
| `api/transfer/` | POST | 队长将权限交接给当前成员（participant_id） |
| `api/delete-profile/`、`api/leave/` | POST | 删除匹配资料、冻结前退出 |
| `api/matching-runs/`、`api/freeze/` | POST | 主办方生成建议、冻结名单 |
| `api/unmatched/` | GET | 仅本人 opt-in 且请求协助的队列 |
| `operations/{receipt_uuid}/` | GET | 读取本人幂等回执 |

资料接口字段与 `matching_forms.ProfileForm` 一致，包括 `version`、角色列表、逗号分隔的 `interests`、带时区的多行 `slots`、可见性与各独立授权。
错误使用 403 权限、404 合并私密对象不可用、409 冲突、410 提案过期、422 输入或资料缺项、429 意向数量/冷却限制。

## 推荐与集中撮合

`match_core.py` 从规格包独立参考实现移入，保留合成测试。
人员采用技能供需方向性评分和调和平均；数值仅在内部保存，界面显示事实说明。
已有队伍与所有成员重评，按项目角色的最大能力覆盖计算新增成员边际贡献；同步时间检查全队交集。
推荐展示评分靠前候选，并保留低曝光和新资料的探索位置。
保存算法版本、政策、画像快照、特征缺项、输入版本和内部排序。

集中撮合只使用 7 天内明确确认且主动报名本轮的人，完整旧队伍作为不可拆块。
每个锚点召回 Top-8 与额外探索候选，枚举小组并重新验证资格、Idea、全队时间、容量与最低双向分数。
保存候选集合，用确定性贪心选择不争用人员的可行建议；状态为 `feasible`，不声称最优。
优先新增未组队者覆盖，其次质量。保留完整候选集可重放，穷举 oracle 只用于小规模验证。
目前未集成 CP-SAT、语义模型或行为学习；它们属于可替换的后续增强。
生成建议不创建正式队伍、同意或积分，需运营/选手通过同一提案流程继续。

## 验证

```bash
cd app
GITHUB_WORKFLOW=local-hackathon-validation .venv/bin/pytest tests/hackathon --reuse-db
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run hackathon
.venv/bin/ruff check eventyay/hackathon tests/hackathon
```

测试包括参考算法、双向联系隐私、具体名单签署、幂等重放、旧版本失效、补位全员确认、揭榜多实现、资格隔离、冻结、数据库跨活动和容量保护，以及真实 PostgreSQL 20 路抢最后一位的并发校验。
不以合成测试宣称真人匹配有效性或整站性能；300 人 / 100 Idea / 50 队伍场景仍需指定硬件并完成负载验收。
