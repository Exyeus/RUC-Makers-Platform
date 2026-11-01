"""Offline notification contract and failure tests; never send to a real group."""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import feishu_notify as notify


REPO = 'example/project'
SERVER = 'https://github.com'
ITEM = {
    'number': 17,
    'title': '修复上传',
    'body': '描述与验证步骤',
    'user': {'login': 'author'},
    'assignees': [{'login': 'owner'}],
    'requested_reviewers': [{'login': 'alice'}],
    'requested_teams': [{'slug': 'backend'}],
}


def response(data: object) -> io.BytesIO:
    return io.BytesIO(json.dumps(data).encode())


class MessageTests(unittest.TestCase):
    def test_issue_contains_body_author_assignee_and_link(self):
        post = notify.build_post('issues', {'action': 'opened', 'issue': ITEM}, REPO, SERVER)['zh_cn']
        serialized = json.dumps(post, ensure_ascii=False)
        for value in ('新建 Issue #17', ITEM['body'], '@author', '@owner', f'{SERVER}/{REPO}/issues/17'):
            self.assertIn(value, serialized)
        self.assertNotIn('请求审阅', serialized)

    def test_pr_open_includes_people_teams_and_draft(self):
        pr = {**ITEM, 'draft': True}
        post = notify.build_post('pull_request_target', {'action': 'opened', 'pull_request': pr}, REPO, SERVER)
        serialized = json.dumps(post, ensure_ascii=False)
        for value in ('@alice', 'example/backend', '状态：草稿', f'{SERVER}/{REPO}/pull/17'):
            self.assertIn(value, serialized)

    def test_new_review_target_is_preserved_even_if_snapshot_lags(self):
        for target in ({'requested_reviewer': {'login': 'bob'}}, {'requested_team': {'slug': 'frontend'}}):
            with self.subTest(target=target):
                post = notify.build_post(
                    'pull_request_target',
                    {
                        'action': 'review_requested',
                        'pull_request': ITEM,
                        'sender': {'login': 'maintainer'},
                        **target,
                    },
                    REPO,
                    SERVER,
                )
                content = json.dumps(post, ensure_ascii=False)
                self.assertIn('PR 请求审阅', content)
                self.assertIn('本次请求', content)
                self.assertIn(next(iter(target.values())).get('login', 'frontend'), content)
                self.assertIn('@maintainer', content)

    def test_no_reviewers_and_empty_body_are_explicit(self):
        pr = {**ITEM, 'body': None, 'requested_reviewers': [], 'requested_teams': []}
        post = notify.build_post('pull_request_target', {'action': 'opened', 'pull_request': pr}, REPO, SERVER)
        serialized = json.dumps(post, ensure_ascii=False)
        self.assertIn('暂未指定', serialized)
        self.assertIn('（无正文）', serialized)

    def test_untrusted_content_is_literal_and_large_body_is_bounded(self):
        malicious = '<at user_id="all">所有人</at> ${{ secrets.FEISHU_APP_SECRET }} $(touch /tmp/pwned)'
        pr = {**ITEM, 'title': malicious, 'body': malicious + '中' * 65000}
        original = copy.deepcopy(pr)
        post = notify.build_post('pull_request_target', {'action': 'opened', 'pull_request': pr}, REPO, SERVER)
        nodes = [node for row in post['zh_cn']['content'] for node in row]
        self.assertTrue(all(node['tag'] in ('text', 'a') for node in nodes))
        self.assertTrue(any(malicious in node.get('text', '') for node in nodes))
        self.assertLess(len(json.dumps(post, ensure_ascii=False).encode('utf-8')), 20000)
        self.assertEqual(pr, original)

    def test_unsubscribed_events_do_not_generate_messages(self):
        self.assertIsNone(notify.build_post('issues', {'action': 'closed'}, REPO, SERVER))
        self.assertIsNone(notify.build_post('pull_request_target', {'action': 'synchronize'}, REPO, SERVER))
        self.assertIsNone(
            notify.build_post('issues', {'action': 'opened', 'issue': {'pull_request': {}}}, REPO, SERVER)
        )


class ApiTests(unittest.TestCase):
    @patch.object(notify.time, 'sleep')
    @patch.object(notify, 'urlopen')
    def test_retry_keeps_identical_uuid_and_request_bytes(self, urlopen, sleep):
        urlopen.side_effect = [
            HTTPError('https://example.invalid', 429, 'limited', {}, None),
            response({'code': 99991400}),
            response({'code': 0}),
        ]
        notify.request_json('/im/v1/messages?receive_id_type=chat_id', {'uuid': 'stable'}, 'test-token')
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(len(requests), 3)
        self.assertEqual({request.data for request in requests}, {b'{"uuid": "stable"}'})
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer test-token')
        self.assertEqual(sleep.call_count, 2)

    @patch.object(notify.time, 'sleep')
    @patch.object(notify, 'urlopen')
    def test_network_failures_are_bounded(self, urlopen, sleep):
        urlopen.side_effect = URLError('sensitive detail')
        with self.assertRaisesRegex(notify.NotificationError, 'network'):
            notify.request_json('/test', {})
        self.assertEqual(urlopen.call_count, 3)

    @patch.object(notify.time, 'sleep')
    @patch.object(notify, 'urlopen')
    def test_permanent_api_error_does_not_retry_or_expose_response(self, urlopen, sleep):
        urlopen.return_value = response({'code': 230002, 'msg': 'secret-test-token'})
        with self.assertRaises(notify.NotificationError) as raised:
            notify.request_json('/test', {})
        self.assertIn('230002', str(raised.exception))
        self.assertNotIn('secret-test-token', str(raised.exception))
        sleep.assert_not_called()
        self.assertEqual(urlopen.call_count, 1)

    @patch.object(notify.time, 'sleep')
    @patch.object(notify, 'urlopen')
    def test_monthly_quota_is_permanent_for_http_and_business_errors(self, urlopen, sleep):
        body = {'code': 99991403, 'msg': 'sensitive detail'}
        for result in (response(body), HTTPError('https://example.invalid', 429, 'quota', {}, response(body))):
            with self.subTest(result=type(result).__name__):
                urlopen.reset_mock()
                urlopen.side_effect = [result]
                with self.assertRaisesRegex(notify.NotificationError, '99991403') as raised:
                    notify.request_json('/test', {})
                self.assertNotIn('sensitive detail', str(raised.exception))
                self.assertEqual(urlopen.call_count, 1)
                sleep.assert_not_called()

    @patch.object(notify, 'urlopen')
    def test_malformed_responses_fail_closed(self, urlopen):
        for raw in (b'not-json', b'[]', b'{}', b'{"code": "0"}', b'{"code": false}'):
            with self.subTest(raw=raw):
                urlopen.return_value = io.BytesIO(raw)
                with self.assertRaises(notify.NotificationError):
                    notify.request_json('/test', {})


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        event_path = Path(self.directory.name) / 'event.json'
        event_path.write_text(json.dumps({'action': 'opened', 'pull_request': ITEM}), encoding='utf-8')
        self.env = {
            'GITHUB_EVENT_PATH': str(event_path),
            'GITHUB_EVENT_NAME': 'pull_request_target',
            'GITHUB_REPOSITORY': REPO,
            'GITHUB_SERVER_URL': SERVER,
            'GITHUB_RUN_ID': '123',
            'FEISHU_APP_ID': 'fake-app',
            'FEISHU_APP_SECRET': 'fake-secret',
            'FEISHU_CHAT_ID': 'oc_fake',
        }

    def test_auth_then_one_post_and_rerun_has_same_uuid(self):
        payloads = []
        for attempt in ('1', '2'):
            with (
                patch.dict(os.environ, {**self.env, 'GITHUB_RUN_ATTEMPT': attempt}, clear=True),
                patch.object(notify, 'urlopen') as urlopen,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                urlopen.side_effect = [
                    response({'code': 0, 'tenant_access_token': 'fake-token'}),
                    response({'code': 0}),
                ]
                self.assertEqual(notify.main(), 0)
                self.assertEqual(urlopen.call_count, 2)
                auth, send = [call.args[0] for call in urlopen.call_args_list]
                self.assertEqual(json.loads(auth.data), {'app_id': 'fake-app', 'app_secret': 'fake-secret'})
                self.assertTrue(send.full_url.endswith('/im/v1/messages?receive_id_type=chat_id'))
                payloads.append(json.loads(send.data))
        self.assertEqual(payloads[0]['uuid'], payloads[1]['uuid'])
        self.assertEqual(payloads[0]['msg_type'], 'post')
        self.assertEqual(payloads[0]['receive_id'], 'oc_fake')
        self.assertIn('zh_cn', json.loads(payloads[0]['content']))

    @patch.object(notify, 'urlopen')
    def test_missing_secret_fails_before_network(self, urlopen):
        with (
            patch.dict(os.environ, {**self.env, 'FEISHU_APP_SECRET': ''}, clear=True),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(notify.main(), 1)
        self.assertIn('Missing FEISHU_APP_SECRET', stderr.getvalue())
        urlopen.assert_not_called()

    @patch.object(notify, 'urlopen')
    def test_manual_smoke_test_sends_one_message(self, urlopen):
        with (
            patch.dict(os.environ, {**self.env, 'GITHUB_EVENT_NAME': 'workflow_dispatch'}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            urlopen.side_effect = [response({'code': 0, 'tenant_access_token': 'fake-token'}), response({'code': 0})]
            self.assertEqual(notify.main(), 0)
        message = json.loads(json.loads(urlopen.call_args.args[0].data)['content'])
        self.assertEqual(message['zh_cn']['title'], 'GitHub → 飞书通知测试')


if __name__ == '__main__':
    unittest.main()
