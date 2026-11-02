"""Forward GitHub event metadata to a Feishu application bot using only stdlib."""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_ROOT = 'https://open.feishu.cn/open-apis'
RETRYABLE_CODES = {99991400}  # Frequency limit; 99991403 is a monthly quota exhaustion.
MAX_ATTEMPTS = 3
DEPENDENCY_BOTS = frozenset({'dependabot[bot]', 'renovate[bot]'})
LOGIN = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?'
MENTION = re.compile(rf'(?<![A-Za-z0-9_./@\\])@({LOGIN})(?![A-Za-z0-9_/@-])')
INLINE_CODE = re.compile(r'(?<!`)(?P<ticks>`+)(?!`).*?(?<!`)(?P=ticks)(?!`)', re.DOTALL)
HTML_COMMENT = re.compile(r'<!--.*?(?:-->|$)', re.DOTALL)


class NotificationError(RuntimeError):
    """An actionable error whose message contains no credentials or response body."""


def text_node(text: str) -> dict:
    # GitHub HTML/Markdown is literal; actual mentions are created only from the mapping.
    return {'tag': 'text', 'text': text}


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + '…（已截断，请查看 GitHub 原文）'


def unique_user_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key.lower() in result:
            raise NotificationError('FEISHU_USER_MAP contains duplicate case-insensitive usernames.')
        result[key.lower()] = value
    return result


def user_map(raw: str) -> dict[str, str]:
    """Only explicitly configured users can become real Feishu mentions."""
    try:
        value = json.loads(raw or '{}', object_pairs_hook=unique_user_keys)
    except ValueError:
        raise NotificationError(
            'FEISHU_USER_MAP must be a JSON object of GitHub usernames to Feishu open_id.'
        ) from None
    if not isinstance(value, dict):
        raise NotificationError('FEISHU_USER_MAP must be a JSON object.')
    result = {}
    for login, open_id in value.items():
        if (
            not re.fullmatch(LOGIN, login)
            or not isinstance(open_id, str)
            or not re.fullmatch(r'ou_[A-Za-z0-9]+', open_id)
        ):
            raise NotificationError('FEISHU_USER_MAP contains an invalid GitHub username or Feishu open_id.')
        result[login.lower()] = open_id
    return result


def mention_matches(body: str) -> list[re.Match]:
    # Keep offsets while masking comments, fenced blocks and exact-length code spans.
    masked = HTML_COMMENT.sub(lambda match: ' ' * len(match.group()), body)
    lines, fence = [], ''
    for line in masked.splitlines(keepends=True):
        if fence:
            lines.append(' ' * len(line))
            if re.fullmatch(rf' {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*\r?\n?', line):
                fence = ''
        elif opening := re.match(r' {0,3}(`{3,}|~{3,})', line):
            fence = opening[1]
            lines.append(' ' * len(line))
        else:
            lines.append(line)
    masked = INLINE_CODE.sub(lambda match: ' ' * len(match.group()), ''.join(lines))
    return list(MENTION.finditer(masked))


def mentions(body: str) -> set[str]:
    return {match[1].lower() for match in mention_matches(body)}


def body_nodes(body: str, mapping: dict[str, str], targets: set[str]) -> list[dict]:
    nodes, seen, start = [], set(), 0
    for match in mention_matches(body):
        login = match[1].lower()
        if login not in targets or login not in mapping or login in seen:
            continue
        if match.start() > start:
            nodes.append(text_node(body[start : match.start()]))
        nodes.append({'tag': 'at', 'user_id': mapping[login]})
        start = match.end()
        seen.add(login)
    if start < len(body):
        nodes.append(text_node(body[start:]))
    return nodes or [text_node(body)]


def build_post(
    event_name: str, event: dict, repo: str, server: str, mapping: dict[str, str] | None = None
) -> dict | None:
    """Build one rich-text post per subscribed event, without running event content."""
    action = event.get('action')
    if event_name == 'workflow_dispatch':
        login = event.get('inputs', {}).get('mention_user', '').strip()
        if login and not re.fullmatch(LOGIN, login):
            raise NotificationError('mention_user must be a GitHub username without @.')
        body = f'请 @{login} review；这是一条手动 @ 测试消息。' if login else '通知通道已连通；这是一条手动测试消息。'
        return {
            'zh_cn': {
                'title': 'GitHub → 飞书通知测试',
                'content': [
                    body_nodes(body, mapping or {}, mentions(body)),
                    [{'tag': 'a', 'text': f'{server}/{repo}', 'href': f'{server}/{repo}'}],
                ],
            }
        }
    # Filter the event sender, so people can still request reviews on bot-created PRs.
    if event.get('sender', {}).get('login', '').lower() in DEPENDENCY_BOTS:
        return None
    if event_name == 'issues' and action in ('opened', 'edited'):
        item = event['issue']
        if 'pull_request' in item:
            return None
        kind = 'issues'
    elif event_name == 'pull_request_target' and action in ('opened', 'edited', 'review_requested'):
        item = event['pull_request']
        kind = 'pull'
    elif event_name == 'issue_comment' and action in ('created', 'edited'):
        item = event['issue']
        kind = 'pull' if 'pull_request' in item else 'issues'
    else:
        return None

    number = int(item['number'])
    source = event['comment'] if event_name == 'issue_comment' else item
    body = (source.get('body') or '').strip()
    targets = mentions(body)
    if action == 'edited':
        change = event.get('changes', {}).get('body')
        if not isinstance(change, dict) or 'from' not in change:
            return None
        previous = change['from']
        if previous is None:
            previous = ''
        if not isinstance(previous, str):
            return None
        targets -= mentions(previous)
        if not targets:
            return None
    elif event_name == 'issue_comment' and not targets:
        return None

    url = f'{server}/{repo}/{kind}/{number}'
    if event_name == 'issue_comment':
        url += f'#issuecomment-{int(source["id"])}'
    if action == 'review_requested':
        target = event.get('requested_reviewer')
        if target:
            body = f'请 @{target["login"]} review。\n\n{body}'
            targets = {target['login'].lower()}
        elif event.get('requested_team'):
            body = f'请 @{repo.split("/")[0]}/{event["requested_team"]["slug"]} 团队 review。\n\n{body}'
            targets = set()  # No implicit team-to-person expansion.
    elif action == 'opened' and kind == 'pull':
        requested = [user['login'] for user in item.get('requested_reviewers', [])]
        teams = [f'{repo.split("/")[0]}/{team["slug"]}' for team in item.get('requested_teams', [])]
        if requested or teams:
            body = '请求审阅：' + ' '.join('@' + name for name in requested + teams) + '\n\n' + body
            targets.update(name.lower() for name in requested)
    mapping = mapping or {}
    nodes = body_nodes(clip(body or '（无正文）', 2000), mapping, targets)
    rendered_ids = {node['user_id'] for node in nodes if node['tag'] == 'at'}
    # A mention near the end of a long description must still reach its recipient.
    for login in sorted(targets):
        if login in mapping and mapping[login] not in rendered_ids:
            nodes.extend([text_node('\n'), {'tag': 'at', 'user_id': mapping[login]}])
            rendered_ids.add(mapping[login])
    rows = [nodes, [{'tag': 'a', 'text': url, 'href': url}]]
    return {'zh_cn': {'title': clip(item['title'], 200), 'content': rows}}


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
            user_map(os.environ.get('FEISHU_USER_MAP', '')),
        )
        if post is None:
            print('Event skipped by notification rules.')
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
