# GitHub Issue / PR 飞书群通知

[工作流](../.github/workflows/feishu-notify.yml) 调用 [Python 脚本](../.github/scripts/feishu_notify.py)，仅使用 Python 3.12 标准库，无需部署常驻服务、安装 SDK 或配置飞书事件回调。

本应用只做 GitHub → 飞书的单向消息转发和明确规则下的用户名映射，不调用 AI/大模型，不读取群内对话用于整理内容。lark-cli 仅作为本机配置、查询成员 ID 和排查权限的辅助工具；不参与 GitHub Actions 的运行，不需要安装 AI Agent 插件或启动消息监听服务。

## 通知规则与模板

默认过滤事件发送者为 `dependabot[bot]` 或 `renovate[bot]` 的自动通知，包括依赖更新 PR、自动编辑、评论和 reviewer 请求。按本次事件的 `sender.login` 精确匹配（不区分大小写），不根据标题、`dependencies` 标签或 PR 原作者屏蔽整个讨论。同学在机器人创建的 PR 中手动新增 @、发表评论 @ 人或请求 reviewer，仍按下表发送；其他机器人也继续按原规则处理。过滤在获取飞书 token、发送消息之前完成；Actions 显示成功跳过，不产生群消息。手动测试不受此过滤影响。

消息只展示三部分：

```text
PR/Issue 标题
正文
链接
```

标题使用 GitHub 原标题；正文使用描述或本次评论内容。通过 Reviewers 请求审阅时，正文前加“请 @用户名 review”；新建 PR 已有 reviewer 时，正文前加请求审阅名单。最后一行展示可点击的 GitHub URL。移除仓库、作者、操作者、状态等额外展示行。

| GitHub 事件 | 发送条件与正文 |
| --- | --- |
| `issues.opened` / `pull_request_target.opened` | 每次创建发送；正文为原描述，PR 附当前个人和团队 reviewer |
| `issues.edited` / `pull_request_target.edited` | 仅正文新增个人 @用户名时发送；支持原正文为空（空字符串或 null）后首次添加 @；仅提醒本次新增的用户名 |
| `issue_comment.created` / `issue_comment.edited` | Issue 或 PR 的 Conversation 评论中包含/新增个人 @用户名时发送；正文为评论，链接定位到该评论 |
| `pull_request_target.review_requested` | 发送审阅请求；只 @ 本次被请求的个人 reviewer，不重复提醒描述中其他人 |
| `workflow_dispatch` | 发送一条测试消息；可填 `mention_user` 测试指定 GitHub 用户的飞书 @ 映射 |

正文最多展示 2000 字符，标题最多展示 200 字符，超出内容通过链接查看。已映射用户的 @ 位于截断部分时，会补到正文末尾，保留提醒。Markdown 和 HTML 按普通文字显示；真正的 @ 由结构化 `at` 标签生成，不执行正文中的 HTML。

个人用户名不区分大小写，一条消息内同一个 GitHub 用户只转换一次。识别普通文字里的 `@alice`，跳过常见反引号代码片段、代码围栏、HTML 注释、邮箱、URL 路径、转义的 `\@alice`。这是轻量文本识别，不是 GitHub Markdown 的完整渲染器。团队 `@组织/团队` 只显示原文字，不自动展开群成员，也不会 @ 所有人。未配置映射的用户名仍以普通文字发送。

PR diff 行内评论和 review 评语暂不订阅；请在 PR 的 **Conversation** 标签页评论。标题中的 @ 不作为提醒目标。普通评论、仅改标题、删除 @、推送提交、关闭和重开不发送。重复编辑未新增用户名不发送；移除后再次加入同一用户名会再次发送。多个 reviewer 请求可能触发多条消息，不合并跨事件通知。

## 让 GitHub @ 同步为飞书真正的 @

需要配置身份映射，GitHub 用户名不会自动对应飞书账号。原有三个 secrets 保留，再在 **Settings → Secrets and variables → Actions → New repository secret** 新建 `FEISHU_USER_MAP`：

```json
{
  "alice": "ou_replaceWithAlicesRealOpenId",
  "bob": "ou_replaceWithBobsRealOpenId"
}
```

以上为合成示例，必须替换为真实 GitHub 用户名（不带 `@`）和真实 `open_id`。映射不写入代码、PR 或日志。未填写这个可选 secret 时，照常发送文字通知。JSON 无效、重复的大小写用户名或非 `ou_` ID 会使通知 job 明确失败，避免误用 `all` 触发全群提醒。

### 获取协作者的 open_id

1. 在**当前发送通知的同一个飞书应用**中，申请“查看群成员”（`im:chat.members:read`），发布并按组织要求完成审批。这是查询成员 ID 时需要的权限；发送消息本身仍使用 `im:message:send_as_bot`。
2. 打开飞书官方[获取群成员列表](https://open.feishu.cn/document/server-docs/group/chat-member/get?lang=zh-CN)的 API 调试台，选择该应用，使用它的 `tenant_access_token`。
3. 调用 `GET /open-apis/im/v1/chats/{chat_id}/members?member_id_type=open_id&page_size=100`，`chat_id` 使用已配置的目标群。机器人需已在群内；如返回 `has_more=true`，继续用 `page_token` 查询后续页。
4. 按返回成员姓名确认每位协作者，将对应 `member_id`（`ou_...`）填入映射。重名时先确认本人，不能只凭姓名猜测。确保被 @ 的人确实在目标群里。
5. `open_id` 只对该应用有效；不要复制其他机器人的 ID。若人员的 ID 已通过当前应用的可信渠道取得，可直接配置映射，无需为运行时额外查询通讯录。

也可用[官方 lark-cli](https://github.com/larksuite/cli)执行相同的群成员查询。本机安装并验证 CLI 后，配置时选择**已有应用**，使用发送 GitHub 通知的同一套 App ID / App Secret，品牌选 `feishu`；不要用 `--new` 创建另一个应用。App Secret 在自己的终端录入，不粘贴到聊天、命令参数或仓库文件中。

```bash
lark-cli config init --name eventyay-notify
lark-cli --profile eventyay-notify config default-as bot
lark-cli --profile eventyay-notify im chat.members get \
  --chat-id oc_替换为目标群ID \
  --member-id-type open_id --as bot --page-all
```

这个查询路径直接使用机器人应用身份，不需要 `auth login` 授权个人账号。需要发布 `im:chat.members:read` 权限，机器人已经在群里，并确认该应用与目标组织匹配。返回的成员 `member_id` 就是所选类型的 `open_id`；同名用户应先确认本人。安装 CLI 不会改变现有通知的转发行为。

### 在 GitHub 里使用

在 Issue/PR 描述，或其 Conversation 评论中输入 `@` 后选择协作者的 **GitHub 用户名**，例如：

```text
请 @alice review 这部分改动。
```

也可在 PR 右侧 **Reviewers** 里选择 alice。两者都会触发通知，区别是：评论里的 @ 只是提及，Reviewers 会创建 GitHub 的正式审阅请求；Issue 没有正式 Reviewers 功能。

完成映射后，脚本将 `@alice` 转为飞书富文本节点 `{"tag":"at","user_id":"ou_真实ID"}`，在群里显示为该成员的飞书名字。原理见[飞书富文本 at 标签文档](https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json?lang=zh-CN)。团队到个人的提醒规则尚未配置，团队请求仅显示团队文字。

## 需要配置的内容

在仓库 **Settings → Secrets and variables → Actions → Repository secrets** 配置：

| Secret | 值 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书企业自建应用的 App ID |
| `FEISHU_APP_SECRET` | 同一个应用的 App Secret |
| `FEISHU_CHAT_ID` | 目标群的 `chat_id`，通常以 `oc_` 开头；不是群名称、邀请链接或自定义机器人 webhook |

这三个 repository secrets 足够发送普通通知；真正 @ 用户时再加上述可选的 `FEISHU_USER_MAP`，不需要额外 GitHub PAT。若 secrets 放在 **Environment secrets** 下，本工作流没有绑定 environment，需改成 Repository secrets。组织级 secrets 则需授权本仓库访问。

飞书开放平台还需完成：

1. 为这个企业自建应用添加**机器人**能力。
2. 在权限管理中申请 **以应用的身份发消息**（`im:message:send_as_bot`）。发送到已知群无需申请读取全部群消息或通讯录的权限。
3. 创建并发布应用版本，让上述能力与权限生效；如组织要求，完成管理员审核，并确认应用可用范围。
4. 在目标群添加**这个应用的机器人**，确认群设置允许它发言，且 `FEISHU_CHAT_ID` 对应该群。若为外部群，还需按[飞书外部群机器人说明](https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN) 配置共享能力。
5. 确认 GitHub 仓库启用 Actions，并允许工作流中固定版本的 `actions/checkout` 和 `actions/setup-python`。

不需要在聊天中提供任何 secret 的实际值。

## 启用与验收

将相关文件合入仓库默认分支（本仓库为 `main`）后，进入 **Actions → Feishu notifications → Run workflow**，选择 `main` 运行。该操作会向配置的群发送一条测试消息。配置映射后可填 `mention_user`（如 `alice`，不含 `@`），测试真正 @ 到本人。然后创建 Issue/PR，在描述与 Conversation 评论中新增 @，并通过 Reviewers 请求个人审阅，核对群消息、链接和实际提醒。

Issue、评论事件依赖默认分支上存在新版工作流；PR 使用默认分支的可信代码。测试分支的改动合入前，自动事件仍使用旧版；可以手动 Run workflow 选择测试分支，预览其模板和 @ 测试。首次添加本工作流的 PR 不保证能触发自身通知，以合入后的新事件验收。不补发历史事件。脚本和工作流只在本地新增时，GitHub 还不会开始通知。

`pull_request_target` 用于支持外部 fork PR。检出版本固定为该运行的 `github.sha`（该事件下为默认分支提交），不检出 PR head、不运行 PR 代码、不安装项目依赖，GitHub token 仅有 `contents: read` 且不持久化。标题与正文通过 `GITHUB_EVENT_PATH` 作为数据读取，不插入 shell 命令。详情见 [GitHub 事件与安全说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target)。

## 排查与重试

- 在 Actions 对应运行中查看 `Send notification to Feishu`。失败会使通知 job 变红；日志只输出 HTTP 状态码、飞书业务错误码或缺失的配置名，不打印 token、secret、群 ID、正文及完整 API 响应。
- `Missing FEISHU_...`：检查 Actions repository secrets 的名称与作用范围。
- 有通知但没有真正 @：核对 `FEISHU_USER_MAP` 用户名拼写、ID 是否来自同一个应用、该人是否在群内，以及新代码是否已合入默认分支。
- HTTP 401/403 或飞书权限错误：检查 App ID / Secret 是否属于同一个有效应用、权限是否发布并获批。
- 群不存在或机器人不在群：检查 `chat_id` 与机器人所在群、外部群支持及群发言权限。
- HTTP 429、5xx、网络错误及飞书频控码 `99991400` 最多尝试 3 次，单次请求超时 20 秒，退避 1、2 秒；永久错误立即失败。`99991403` 表示月度 API 配额已用尽，不会自动重试，需要管理员处理配额，参见[飞书调用量说明](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/platform-notices)。更长时间频控需稍后重跑该 job。
- 每次发送使用由仓库名与 `GITHUB_RUN_ID` 派生的稳定 UUID，网络重试和同一运行的 re-run 复用 UUID。飞书仅在其去重有效窗口（文档为 1 小时）内保证相同 UUID 的请求最多发送一次；超时重跑、不同事件或新手动运行仍可能产生新消息。
- GitHub Actions 自身的 `GITHUB_TOKEN` 触发的事件通常不会递归启动其他工作流；若其他自动化通过该 token 创建 Issue / PR 或请求 reviewer，需在来源工作流中另行设计通知，参见 [GitHub 工作流触发限制](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow#triggering-a-workflow-from-a-workflow)。

## 本地验证与官方资料

离线测试不使用 secrets，也不发送真实消息；Activity CI 会运行这些测试与脚本 lint：

```bash
python3 -m unittest discover -s .github/scripts -p 'test_feishu_notify.py' -v
app/.venv/bin/ruff check --config app/pyproject.toml .github/scripts/feishu_notify.py .github/scripts/test_feishu_notify.py
```

实现依据：

- [自建应用获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)：通过 App ID / Secret 获取应用访问凭证。
- [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)：向 `receive_id_type=chat_id` 发送 `post` 消息；`content` 是 JSON 序列化后的字符串，`uuid` 用于请求去重。
- [发送消息内容结构](https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json?lang=zh-CN)：使用 `zh_cn` 富文本结构及 `text` / `a` / `at` 标签组织内容。

离线测试只能验证格式、触发数据处理及失败路径；真实租户权限、群成员关系和端到端送达以 Actions 与群内验收为准。
