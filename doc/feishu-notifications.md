# GitHub Issue / PR 飞书群通知

[工作流](../.github/workflows/feishu-notify.yml) 调用 [Python 脚本](../.github/scripts/feishu_notify.py)，仅使用 Python 3.12 标准库，无需部署常驻服务、安装 SDK 或配置飞书事件回调。

## 通知规则

| GitHub 事件 | 群内消息 |
| --- | --- |
| `issues.opened` | 标题、作者、操作者、负责人、正文摘要和 Issue 链接 |
| `pull_request_target.opened` | 标题、作者、操作者、草稿状态、正文摘要、PR 链接、当前请求的个人和团队 reviewer |
| `pull_request_target.review_requested` | 同上，并标明本次新增的审阅请求对象 |
| `workflow_dispatch` | 一条标明为测试的连通性消息，附仓库链接 |

每个事件发送一条富文本消息。PR 创建时若尚未指定 reviewer，会显示“暂未指定”；随后添加 reviewer 会产生新的审阅请求通知。一次请求多个 reviewer 时 GitHub 可能触发多个事件，因此可能收到多条通知。消息使用事件发生时的快照，不实时查询 GitHub；不合并不同事件，也不追踪后续撤销请求。Issue 没有 reviewer，展示负责人。编辑、评论、推送提交、关闭和重开不触发通知。

正文超过 2000 字符、标题超过 200 字符会截断，原文保留在 GitHub。正文中的 Markdown 和 HTML 按普通文字显示；reviewer 展示 GitHub 用户/团队链接。这里的 `@GitHub用户名` **不会触发飞书的真正 @ 提醒**。若需要真正 @，需另行提供 GitHub 用户名到本应用下飞书 `open_id` 的映射，团队也需确定成员映射与提醒规则；不要把 App Secret 当作映射数据。

示例（合成数据）：

```text
PR 请求审阅 #17：修复上传
仓库：example/project
作者：@author | 操作者：@maintainer
本次请求：alice
请求审阅：@alice example/backend
状态：待审阅
正文摘要：描述与验证步骤
查看 #17（可点击链接）
```

## 需要配置的内容

在仓库 **Settings → Secrets and variables → Actions → Repository secrets** 配置：

| Secret | 值 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书企业自建应用的 App ID |
| `FEISHU_APP_SECRET` | 同一个应用的 App Secret |
| `FEISHU_CHAT_ID` | 目标群的 `chat_id`，通常以 `oc_` 开头；不是群名称、邀请链接或自定义机器人 webhook |

这三个 repository secrets 已足够；不需要额外 GitHub PAT。若 secrets 放在 **Environment secrets** 下，本工作流没有绑定 environment，需改成 Repository secrets。组织级 secrets 则需授权本仓库访问。

飞书开放平台还需完成：

1. 为这个企业自建应用添加**机器人**能力。
2. 在权限管理中申请 **以应用的身份发消息**（`im:message:send_as_bot`）。发送到已知群无需申请读取全部群消息或通讯录的权限。
3. 创建并发布应用版本，让上述能力与权限生效；如组织要求，完成管理员审核，并确认应用可用范围。
4. 在目标群添加**这个应用的机器人**，确认群设置允许它发言，且 `FEISHU_CHAT_ID` 对应该群。若为外部群，还需按[飞书外部群机器人说明](https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN) 配置共享能力。
5. 确认 GitHub 仓库启用 Actions，并允许工作流中固定版本的 `actions/checkout` 和 `actions/setup-python`。

不需要在聊天中提供任何 secret 的实际值。

## 启用与验收

将相关文件合入仓库默认分支（本仓库为 `main`）后，进入 **Actions → Feishu notifications → Run workflow**，选择 `main` 运行。该操作会向配置的群发送一条测试消息。然后创建一个 Issue、创建一个 PR，并在 PR 上请求个人或团队 reviewer，分别核对群消息和链接。

Issue 事件依赖默认分支上存在工作流；PR 使用默认分支的可信代码。首次添加本工作流的 PR 不保证能触发自身通知，以合入后的新事件验收。不补发历史事件。脚本和工作流只在本地新增时，GitHub 还不会开始通知。

`pull_request_target` 用于支持外部 fork PR。检出版本固定为该运行的 `github.sha`（该事件下为默认分支提交），不检出 PR head、不运行 PR 代码、不安装项目依赖，GitHub token 仅有 `contents: read` 且不持久化。标题与正文通过 `GITHUB_EVENT_PATH` 作为数据读取，不插入 shell 命令。详情见 [GitHub 事件与安全说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target)。

## 排查与重试

- 在 Actions 对应运行中查看 `Send notification to Feishu`。失败会使通知 job 变红；日志只输出 HTTP 状态码、飞书业务错误码或缺失的配置名，不打印 token、secret、群 ID、正文及完整 API 响应。
- `Missing FEISHU_...`：检查 Actions repository secrets 的名称与作用范围。
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
- [发送消息内容结构](https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json?lang=zh-CN)：使用 `zh_cn` 富文本结构及 `text` / `a` 标签组织内容。

离线测试只能验证格式、触发数据处理及失败路径；真实租户权限、群成员关系和端到端送达以 Actions 与群内验收为准。
