"""Offline notification contract and failure tests; never send to a real group."""

import contextlib
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
    def post(self, name='issues', action='opened', body='请 @alice review', **extra):
        item = {**ITEM, 'body': body}
        key = 'pull_request' if name == 'pull_request_target' else 'issue'
        event = {'action': action, key: item, **extra}
        return notify.build_post(name, event, REPO, SERVER, {'alice': 'ou_alice', 'bob': 'ou_bob'})

    def at_ids(self, post):
        return [node['user_id'] for row in post['zh_cn']['content'] for node in row if node['tag'] == 'at']

    def test_compact_template_omits_body_and_shortens_link(self):
        post = self.post(body='正文，没有 reviewer')['zh_cn']
        self.assertEqual(post['title'], ITEM['title'])
        self.assertEqual(len(post['content']), 1)
        self.assertNotIn('正文', json.dumps(post, ensure_ascii=False))
        self.assertEqual(post['content'][0][-1], {
            'tag': 'a', 'text': '#17', 'href': f'{SERVER}/{REPO}/issues/17',
        })
        self.assertIn('@owner', json.dumps(post))

    def test_opened_pr_maps_requested_reviewer_and_preserves_team_text(self):
        post = self.post('pull_request_target', body='测试正文')
        self.assertEqual(self.at_ids(post), ['ou_alice'])
        self.assertIn('@example/backend', json.dumps(post, ensure_ascii=False))
        self.assertEqual(post['zh_cn']['content'][-1][-1]['href'], f'{SERVER}/{REPO}/pull/17')

    def test_dependency_bot_events_are_silent(self):
        for login in ('dependabot[bot]', 'renovate[bot]', 'Dependabot[bot]'):
            for name, actions in (
                ('issues', ('opened', 'edited')),
                ('pull_request_target', ('opened', 'edited', 'review_requested')),
                ('issue_comment', ('created', 'edited')),
            ):
                for action in actions:
                    with self.subTest(login=login, name=name, action=action):
                        self.assertIsNone(
                            self.post(
                                name,
                                action,
                                sender={'login': login},
                                comment={'id': 123, 'body': '请 @alice review'},
                                changes={'body': {'from': ''}},
                                requested_reviewer={'login': 'alice'},
                            )
                        )

    def test_people_can_request_review_and_mention_on_bot_prs(self):
        item = {**ITEM, 'user': {'login': 'dependabot[bot]'}, 'body': '请 @alice review'}
        events = (
            (
                'pull_request_target',
                {'action': 'edited', 'pull_request': item, 'changes': {'body': {'from': ''}}},
                ['ou_alice'],
            ),
            (
                'pull_request_target',
                {'action': 'review_requested', 'pull_request': item, 'requested_reviewer': {'login': 'bob'}},
                ['ou_bob'],
            ),
            (
                'issue_comment',
                {'action': 'created', 'issue': {**item, 'pull_request': {}}, 'comment': {'id': 123, 'body': '@bob'}},
                ['ou_bob'],
            ),
            (
                'issue_comment',
                {
                    'action': 'edited',
                    'issue': {**item, 'pull_request': {}},
                    'comment': {'id': 123, 'body': '@bob', 'user': {'login': 'dependabot[bot]'}},
                    'changes': {'body': {'from': ''}},
                },
                ['ou_bob'],
            ),
        )
        for name, event, expected_ids in events:
            with self.subTest(name=name, action=event['action']):
                post = notify.build_post(
                    name, {**event, 'sender': {'login': 'alice'}}, REPO, SERVER, {'alice': 'ou_alice', 'bob': 'ou_bob'}
                )
                self.assertEqual(self.at_ids(post), expected_ids)

    def test_dependency_titles_labels_and_other_bots_are_not_filtered(self):
        for login in ('alice', 'dependabot-helper', 'github-actions[bot]'):
            with self.subTest(login=login):
                post = self.post(
                    'pull_request_target',
                    sender={'login': login},
                    pull_request={
                        **ITEM,
                        'title': 'chore(deps): bump a dependency',
                        'labels': [{'name': 'dependencies'}],
                    },
                )
                self.assertIsNotNone(post)
                self.assertEqual(self.at_ids(post), ['ou_alice'])

    def test_mentions_are_case_insensitive_and_repeated_user_pings_once(self):
        self.assertEqual(
            self.at_ids(self.post(body='请 @Alice 和 @alice review，@bob 也请看。')), ['ou_alice', 'ou_bob']
        )

    def test_unknown_user_is_plain_text(self):
        post = self.post(body='请 @unknown review')
        self.assertEqual(self.at_ids(post), [])
        self.assertIn('@unknown', json.dumps(post))

    def test_chinese_text_can_touch_the_mention(self):
        self.assertEqual(self.at_ids(self.post(body='请@alice帮忙review，谢谢')), ['ou_alice'])

    def test_comments_only_trigger_when_they_mention_someone(self):
        for is_pr in (False, True):
            with self.subTest(is_pr=is_pr):
                item = {**ITEM, **({'pull_request': {}} if is_pr else {})}
                event = {'action': 'created', 'issue': item, 'comment': {'id': 123, 'body': '请 @alice review'}}
                post = notify.build_post('issue_comment', event, REPO, SERVER, {'alice': 'ou_alice'})
                self.assertEqual(self.at_ids(post), ['ou_alice'])
                self.assertTrue(post['zh_cn']['content'][-1][-1]['href'].endswith('#issuecomment-123'))
                event['comment']['body'] = '普通讨论'
                self.assertIsNone(notify.build_post('issue_comment', event, REPO, SERVER))

    def test_edited_description_only_pings_new_mentions(self):
        for name in ('issues', 'pull_request_target'):
            with self.subTest(name=name):
                post = self.post(
                    name, 'edited', body='请 @alice 和 @bob review', changes={'body': {'from': '请 @alice review'}}
                )
                self.assertEqual(self.at_ids(post), ['ou_bob'])
                self.assertIsNone(
                    self.post(
                        name, 'edited', body='请 @ALICE review，改文字', changes={'body': {'from': '请 @alice review'}}
                    )
                )
                self.assertIsNone(self.post(name, 'edited'))

    def test_edited_comment_does_not_reping_existing_users(self):
        event = {
            'action': 'edited',
            'issue': ITEM,
            'comment': {'id': 2, 'body': '@alice @bob'},
            'changes': {'body': {'from': '@alice'}},
        }
        post = notify.build_post('issue_comment', event, REPO, SERVER, {'alice': 'ou_alice', 'bob': 'ou_bob'})
        self.assertEqual(self.at_ids(post), ['ou_bob'])

    def test_mention_added_to_empty_description_notifies_without_a_user_map(self):
        for name, item_key in (('issues', 'issue'), ('pull_request_target', 'pull_request')):
            for previous in ('', None):
                with self.subTest(name=name, previous=previous):
                    post = notify.build_post(
                        name,
                        {
                            'action': 'edited',
                            item_key: {**ITEM, 'body': '@Exyeus Check further'},
                            'changes': {'body': {'from': previous}},
                        },
                        REPO,
                        SERVER,
                    )
                    self.assertIn('@exyeus', json.dumps(post))
                    self.assertNotIn('Check further', json.dumps(post))

    def test_null_previous_comment_body_notifies_and_title_edits_stay_silent(self):
        event = {
            'action': 'edited',
            'issue': ITEM,
            'comment': {'id': 123, 'body': '@Exyeus Can you receive this message?'},
            'changes': {'body': {'from': None}},
        }
        post = notify.build_post('issue_comment', event, REPO, SERVER)
        self.assertTrue(post['zh_cn']['content'][-1][-1]['href'].endswith('#issuecomment-123'))
        self.assertIsNone(
            notify.build_post(
                'issues',
                {
                    'action': 'edited',
                    'issue': {**ITEM, 'body': '@Exyeus'},
                    'changes': {'title': {'from': 'Old title'}},
                },
                REPO,
                SERVER,
            )
        )

    def test_review_request_only_pings_the_new_reviewer(self):
        post = self.post(
            'pull_request_target', 'review_requested', body='原文 @alice', requested_reviewer={'login': 'bob'}
        )
        self.assertEqual(self.at_ids(post), ['ou_bob'])
        self.assertIn('review', json.dumps(post))
        team = self.post('pull_request_target', 'review_requested', requested_team={'slug': 'backend'})
        self.assertEqual(self.at_ids(team), [])

    def test_code_email_urls_and_team_mentions_do_not_ping(self):
        body = (
            '`@alice`\n```python\n@bob\n```\n<!-- @alice -->\n~~~\n@bob\n~~~\n'
            'mail@alice.com https://github.com/@bob @example/backend \\@alice'
        )
        self.assertEqual(notify.mentions(body), set())
        self.assertEqual(self.at_ids(self.post(body=body)), [])

    def test_code_delimiters_and_unclosed_fences_do_not_ping(self):
        for body in (
            '``@alice``',
            '`code with `` @alice`',
            '```python\n@alice\n````',
            '~~~python\n@alice\n~~~~',
            '```python\n@alice',
            '<!-- @alice',
        ):
            with self.subTest(body=body):
                self.assertEqual(notify.mentions(body), set())
        self.assertEqual(notify.mentions('``@alice`` 之后请 @bob review'), {'bob'})

    def test_untrusted_html_cannot_create_a_feishu_mention(self):
        text = '<at user_id="all">所有人</at> ${{ secrets.FEISHU_APP_SECRET }} $(touch /tmp/pwned)'
        post = self.post(body=text)
        self.assertEqual(self.at_ids(post), [])
        self.assertNotIn('user_id', json.dumps(post))
        self.assertNotIn('secrets', json.dumps(post))

    def test_long_body_still_mentions_mapped_user_after_excerpt(self):
        post = self.post(body='中' * 65000 + ' @alice')
        self.assertEqual(self.at_ids(post), ['ou_alice'])
        self.assertLess(len(json.dumps(post, ensure_ascii=False).encode()), 20000)
        self.assertNotIn('中', json.dumps(post, ensure_ascii=False))

    def test_empty_body_and_unsubscribed_events(self):
        self.assertIsNotNone(self.post(body=None))
        self.assertIsNone(notify.build_post('issues', {'action': 'closed'}, REPO, SERVER))
        self.assertIsNone(notify.build_post('pull_request_target', {'action': 'synchronize'}, REPO, SERVER))
        self.assertIsNone(
            notify.build_post('issues', {'action': 'opened', 'issue': {'pull_request': {}}}, REPO, SERVER)
        )

    def test_user_map_validation_rejects_invalid_data_and_all(self):
        self.assertEqual(notify.user_map('{"Alice": "ou_alice"}'), {'alice': 'ou_alice'})
        for raw in (
            'invalid',
            '[]',
            '{"alice":"all"}',
            '{"@alice":"ou_a"}',
            '{"alice":12}',
            '{"alice":"ou_a", "Alice":"ou_b"}',
            '{"alice":"ou_a", "alice":"ou_b"}',
        ):
            with self.subTest(raw=raw), self.assertRaises(notify.NotificationError):
                notify.user_map(raw)
        self.assertEqual(notify.user_map(''), {})

    def test_manual_mention_preview_uses_same_mapping(self):
        post = notify.build_post(
            'workflow_dispatch', {'inputs': {'mention_user': 'Alice'}}, REPO, SERVER, {'alice': 'ou_alice'}
        )
        self.assertEqual(self.at_ids(post), ['ou_alice'])

    def test_assignment_only_pings_new_assignee(self):
        post = self.post(action='assigned', assignee={'login': 'Bob'})
        self.assertEqual(self.at_ids(post), ['ou_bob'])
        self.assertNotIn('@owner', json.dumps(post))
        self.assertIn('指派', json.dumps(post, ensure_ascii=False))
        self.assertIsNone(self.post(action='assigned'))
        self.assertIsNone(self.post(action='unassigned', assignee={'login': 'bob'}))
        post = self.post(action='assigned', assignee={'login': 'unknown'})
        self.assertIn('@unknown', json.dumps(post))
        self.assertEqual(self.at_ids(post), [])

    def test_opened_issue_maps_all_assignees_and_deduplicates_mentions(self):
        post = self.post(issue={**ITEM, 'body': '@alice', 'assignees': [{'login': 'Alice'}, {'login': 'bob'}]})
        self.assertEqual(self.at_ids(post), ['ou_alice', 'ou_bob'])

    def test_same_feishu_identity_is_only_pinged_once(self):
        event = {'action': 'opened', 'issue': {**ITEM, 'body': '@alice @bob', 'assignees': []}}
        post = notify.build_post('issues', event, REPO, SERVER, {'alice': 'ou_same', 'bob': 'ou_same'})
        self.assertEqual(self.at_ids(post), ['ou_same'])

    def test_assignment_bot_filter_and_workflow_subscription(self):
        self.assertIsNone(self.post(action='assigned', assignee={'login': 'bob'}, sender={'login': 'dependabot[bot]'}))
        workflow = (Path(__file__).parents[1] / 'workflows/feishu-notify.yml').read_text()
        self.assertIn('issues:\n    types: [opened, edited, assigned]', workflow)

    def test_many_unknown_mentions_cannot_expand_the_compact_post(self):
        body = ' '.join(f'@user{number}' for number in range(4000)) + ' @alice'
        post = self.post(body=body)
        self.assertEqual(self.at_ids(post), ['ou_alice'])
        self.assertLess(len(json.dumps(post, ensure_ascii=False).encode()), 2000)
        self.assertIn('等4001人', json.dumps(post, ensure_ascii=False))

    def test_visible_template_and_payload_are_less_than_half_the_old_long_message(self):
        body = '详细描述和验证步骤' * 300 + ' @alice'
        item = {**ITEM, 'title': '长标题' * 100, 'body': body}
        post = notify.build_post('issues', {'action': 'opened', 'issue': item}, REPO, SERVER, {'alice': 'ou_alice'})
        self.assertLessEqual(len(post['zh_cn']['title']), 80)
        self.assertEqual(self.at_ids(post), ['ou_alice'])
        old = {'zh_cn': {'title': item['title'][:200], 'content': [
            [{'tag': 'text', 'text': body[:2000]}],
            [{'tag': 'a', 'text': f'{SERVER}/{REPO}/issues/17', 'href': f'{SERVER}/{REPO}/issues/17'}],
        ]}}
        self.assertLess(
            len(json.dumps(post, ensure_ascii=False).encode()), len(json.dumps(old, ensure_ascii=False).encode()) / 2
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

    @patch.object(notify, 'urlopen')
    def test_dependency_bot_event_skips_without_secrets_or_network(self, urlopen):
        Path(self.env['GITHUB_EVENT_PATH']).write_text(
            json.dumps({'action': 'opened', 'pull_request': ITEM, 'sender': {'login': 'dependabot[bot]'}}),
            encoding='utf-8',
        )
        env = {key: value for key, value in self.env.items() if not key.startswith('FEISHU_')}
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(notify.main(), 0)
        self.assertIn('skipped', stdout.getvalue())
        urlopen.assert_not_called()

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
    def test_configured_mapping_reaches_serialized_post(self, urlopen):
        with (
            patch.dict(os.environ, {**self.env, 'FEISHU_USER_MAP': '{"Alice":"ou_alice"}'}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            urlopen.side_effect = [response({'code': 0, 'tenant_access_token': 'fake-token'}), response({'code': 0})]
            self.assertEqual(notify.main(), 0)
        post = json.loads(json.loads(urlopen.call_args.args[0].data)['content'])
        self.assertIn({'tag': 'at', 'user_id': 'ou_alice'}, post['zh_cn']['content'][0])

    @patch.object(notify, 'urlopen')
    def test_invalid_mapping_fails_without_logging_its_value(self, urlopen):
        with (
            patch.dict(os.environ, {**self.env, 'FEISHU_USER_MAP': 'sensitive-invalid-value'}, clear=True),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(notify.main(), 1)
        self.assertIn('FEISHU_USER_MAP', stderr.getvalue())
        self.assertNotIn('sensitive-invalid-value', stderr.getvalue())
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
