"""Forward GitHub event metadata to a Feishu application bot using only stdlib."""

import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_ROOT = 'https://open.feishu.cn/open-apis'
RETRYABLE_CODES = {99991400}  # Frequency limit; 99991403 is a monthly quota exhaustion.
MAX_ATTEMPTS = 3


class NotificationError(RuntimeError):
    """An actionable error whose message contains no credentials or response body."""


def text_node(text: str) -> dict:
    # Keep untrusted GitHub content as literal text, never Markdown or mention tags.
    return {'tag': 'text', 'text': text}


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + '…（已截断，请查看 GitHub 原文）'


def reviewers(pr: dict, event: dict, repo: str, server: str) -> list[dict]:
    users = {user['login'] for user in pr.get('requested_reviewers', [])}
    teams = {team['slug'] for team in pr.get('requested_teams', [])}
    # The event target identifies the new request even if the list snapshot lags.
    if event.get('requested_reviewer'):
        users.add(event['requested_reviewer']['login'])
    if event.get('requested_team'):
        teams.add(event['requested_team']['slug'])
    nodes = []
    for login in sorted(users):
        nodes.extend([{'tag': 'a', 'text': '@' + login, 'href': f'{server}/{login}'}, text_node(' ')])
    owner = repo.split('/')[0]
    for slug in sorted(teams):
        nodes.extend(
            [
                {'tag': 'a', 'text': f'{owner}/{slug}', 'href': f'{server}/orgs/{owner}/teams/{slug}'},
                text_node(' '),
            ]
        )
    return nodes or [text_node('暂未指定')]


def build_post(event_name: str, event: dict, repo: str, server: str) -> dict | None:
    """Build one rich-text post per subscribed event, without running event content."""
    action = event.get('action')
    if event_name == 'workflow_dispatch':
        return {
            'zh_cn': {
                'title': 'GitHub → 飞书通知测试',
                'content': [
                    [text_node(f'仓库：{repo}')],
                    [text_node('通知通道已连通；这是一条手动测试消息。')],
                    [{'tag': 'a', 'text': '打开仓库', 'href': f'{server}/{repo}'}],
                ],
            }
        }
    if event_name == 'issues' and action == 'opened':
        item = event['issue']
        if 'pull_request' in item:
            return None
        label, kind = '新建 Issue', 'issues'
    elif event_name == 'pull_request_target' and action in ('opened', 'review_requested'):
        item = event['pull_request']
        label = '新建 PR' if action == 'opened' else 'PR 请求审阅'
        kind = 'pull'
    else:
        return None

    number = int(item['number'])
    author = item['user']['login']
    actor = event.get('sender', {}).get('login', author)
    rows = [
        [text_node(f'仓库：{repo}')],
        [text_node(f'作者：@{author}  |  操作者：@{actor}')],
    ]
    if kind == 'pull':
        target = event.get('requested_reviewer') or event.get('requested_team')
        if action == 'review_requested' and target:
            rows.append([text_node('本次请求：' + (target.get('login') or f'团队 {target["slug"]}'))])
        rows.extend(
            [
                [text_node('请求审阅：'), *reviewers(item, event, repo, server)],
                [text_node('状态：草稿' if item.get('draft') else '状态：待审阅')],
            ]
        )
    else:
        assignees = ', '.join('@' + user['login'] for user in item.get('assignees', []))
        rows.append([text_node('负责人：' + (assignees or '暂未指派'))])
    rows.extend(
        [
            [text_node('正文摘要：\n' + clip((item.get('body') or '').strip() or '（无正文）', 2000))],
            [{'tag': 'a', 'text': f'查看 #{number}', 'href': f'{server}/{repo}/{kind}/{number}'}],
        ]
    )
    return {'zh_cn': {'title': f'{label} #{number}：{clip(item["title"], 200)}', 'content': rows}}


def request_json(path: str, payload: dict, token: str = '') -> dict:
    """Retry transient failures; callers keep the message UUID stable across retries."""
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(API_ROOT + path, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=headers)
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
        except HTTPError as exc:
            status = exc.code
            try:
                error_body = json.load(exc)
            except (ValueError, UnicodeError):
                error_body = {}
            finally:
                exc.close()
            code = error_body.get('code') if isinstance(error_body, dict) else None
            retry = (status == 429 or 500 <= status < 600) and code != 99991403
            detail = f', API code {code}' if type(code) is int else ''
            failure = f'Feishu HTTP {status}{detail}; check the Feishu API error documentation.'
        except (URLError, TimeoutError, ConnectionError):
            retry = True
            failure = 'Feishu network request failed or timed out.'
        except (ValueError, UnicodeError):
            raise NotificationError('Feishu returned invalid JSON.') from None
        else:
            if not isinstance(result, dict) or type(result.get('code')) is not int:
                raise NotificationError('Feishu response is missing an integer code.')
            code = result['code']
            if code == 0:
                return result
            retry = code in RETRYABLE_CODES
            # Do not log server-supplied text: it may contain credentials or user content.
            failure = f'Feishu API code {code}; consult the API error documentation.'
        if not retry or attempt == MAX_ATTEMPTS - 1:
            raise NotificationError(failure)
        time.sleep(2**attempt)
    raise AssertionError('unreachable')


def required_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise NotificationError(f'Missing {name}; configure the repository Actions secret or workflow environment.')
    return value


def main() -> int:
    try:
        event = json.loads(Path(required_env('GITHUB_EVENT_PATH')).read_text(encoding='utf-8'))
        post = build_post(
            required_env('GITHUB_EVENT_NAME'),
            event,
            required_env('GITHUB_REPOSITORY'),
            required_env('GITHUB_SERVER_URL').rstrip('/'),
        )
        if post is None:
            print('Event is outside the notification subscriptions; skipped.')
            return 0
        app_id = required_env('FEISHU_APP_ID')
        app_secret = required_env('FEISHU_APP_SECRET')
        chat_id = required_env('FEISHU_CHAT_ID')
        token_result = request_json(
            '/auth/v3/tenant_access_token/internal',
            {
                'app_id': app_id,
                'app_secret': app_secret,
            },
        )
        token = token_result.get('tenant_access_token')
        if not isinstance(token, str) or not token:
            raise NotificationError('Feishu authentication response is missing tenant_access_token.')
        # GITHUB_RUN_ID survives reruns; Feishu deduplicates this UUID within its time window.
        message_uuid = str(
            uuid.uuid5(uuid.NAMESPACE_URL, required_env('GITHUB_REPOSITORY') + ':' + required_env('GITHUB_RUN_ID'))
        )
        request_json(
            '/im/v1/messages?receive_id_type=chat_id',
            {
                'receive_id': chat_id,
                'msg_type': 'post',
                'content': json.dumps(post, ensure_ascii=False),
                'uuid': message_uuid,
            },
            token,
        )
        print('Feishu notification sent successfully.')
        return 0
    except NotificationError as exc:
        print(f'Notification failed: {exc}', file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print('Notification failed: unreadable event file or unexpected event payload.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
