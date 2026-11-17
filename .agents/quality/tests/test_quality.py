import datetime as dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from check_repo import scan_text
from gate_evidence import evaluate, evidence_path
from render_report import render


class LintTests(unittest.TestCase):
    def test_contrast_is_warning(self):
        found = scan_text('app/page.html', '<p>这不是一个列表，而是创意的宇宙。</p>')
        self.assertEqual(found[0]['rule'], 'copy/negated-contrast')
        self.assertEqual(found[0]['severity'], 'warning')

    def test_necessary_negation_is_kept(self):
        self.assertEqual(scan_text('app/page.html', '<p>无法连接服务器，请重试。截止后不能修改作品。</p>'), [])

    def test_brand_gradient_is_not_banned(self):
        self.assertEqual(scan_text('app/theme.css', '.hero { background: linear-gradient(purple, red); }'), [])

    def test_untracked_deferral(self):
        found = scan_text('app/submit.py', '# 先在此占位，后续实现\npass\n')
        self.assertTrue(any(f['rule'] == 'debt/untracked-deferral' for f in found))

    def test_tracked_debt_allowed_until_expiry(self):
        text = '# TODO(owner=@alice, issue=#12, expires=2026-11-20): improve parser\n'
        self.assertEqual(scan_text('a.py', text, dt.date(2026, 11, 16)), [])

    def test_expired_debt(self):
        text = '# TODO(owner=@alice, issue=#12, expires=2026-11-10): improve parser\n'
        self.assertEqual(scan_text('a.py', text, dt.date(2026, 11, 16))[0]['rule'], 'debt/expired-deferral')

    def test_python_string_not_comment(self):
        self.assertEqual(scan_text('a.py', 'x = "# TODO example"\n'), [])

    def test_copy_comment_excluded(self):
        found = scan_text('a.html', '<!-- 这不是一句提示，而是注释。 -->')
        self.assertFalse(any(f['rule'].startswith('copy/') for f in found))

    def test_tiny_text_threshold(self):
        self.assertTrue(scan_text('x.css', 'p { font-size: 10px; }'))
        self.assertFalse(scan_text('x.css', 'p { font-size: 16px; }'))

    def test_focused_test_is_blocking_candidate(self):
        found = scan_text('tests/a.spec.ts', "test.only('flow', async () => {});")
        self.assertEqual(found[0]['severity'], 'error')

    def test_test_fixture_copy_excluded(self):
        self.assertEqual(scan_text('tests/a.test.js', 'const example = "不是列表，而是空间";'), [])

    def test_django_comment_debt(self):
        found = scan_text('a.html', '{# 后续实现真实提交 #}')
        self.assertTrue(any(f['rule'] == 'debt/untracked-deferral' for f in found))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Synthetic unit-test files, never presented as website evidence.
        (self.root/'synthetic.txt').write_text('unit test only')
        self.contracts = {'contracts': [{'id':'S1','scope':'submission','required':True,'assertions':['persisted'],'required_evidence':['execution-log']}]}
        self.report = {'run': {'revision':'abc','source_fingerprint':'abc:diff','kind':'website-acceptance','environment':'local-isolated'},
                       'checks':[{'id':'S1','kind':'browser','exit_code':0,'status':'passed','assertions':{'persisted':True},'evidence':[{'kind':'execution-log','path':'synthetic.txt'}]}]}

    def tearDown(self): self.temp.cleanup()

    def check(self): return evaluate(self.contracts, self.report, self.root, 'abc', 'all', 'abc:diff')

    def test_valid_evidence_structure(self): self.assertEqual(self.check(), [])

    def test_wrong_source_fingerprint_rejected(self):
        self.report['run']['source_fingerprint'] = 'abc:other-diff'
        self.assertTrue(self.check())

    def test_invalid_top_level_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(self.contracts, [], self.root, 'abc', 'all', 'abc:diff')

    def test_zero_required_scope_rejected(self):
        self.assertTrue(evaluate(self.contracts, self.report, self.root, 'abc', 'auth', 'abc:diff'))

    def test_example_report_cannot_pass(self):
        self.report['run']['kind'] = 'example-not-executed'
        self.assertTrue(self.check())

    def test_missing_case_rejected(self):
        self.report['checks'] = []
        self.assertTrue(any('not_run' in x for x in self.check()))

    def test_skip_flaky_blocked_not_passed(self):
        for status in ['skipped','flaky','blocked','not_run','failed']:
            with self.subTest(status=status):
                self.report['checks'][0]['status'] = status
                self.assertTrue(self.check())

    def test_wrong_revision_rejected(self):
        self.report['run']['revision'] = 'other'
        self.assertTrue(self.check())

    def test_missing_assertion_rejected(self):
        self.report['checks'][0]['assertions'] = {}
        self.assertTrue(self.check())

    def test_missing_file_rejected(self):
        self.report['checks'][0]['evidence'][0]['path'] = 'missing.txt'
        self.assertTrue(self.check())

    def test_unsafe_path_rejected(self):
        for path in ['../secret.txt','/tmp/secret','https://example.com/file','C:\\secret']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                evidence_path(self.root, path)

    def test_duplicate_id_rejected(self):
        self.report['checks'].append(self.report['checks'][0].copy())
        self.assertTrue(self.check())

    def test_report_escapes_text(self):
        self.report['checks'][0]['name'] = '<script>alert(1)</script>'
        text = render(self.report, self.root/'run.json', self.root/'index.html')
        self.assertNotIn('<script>alert(1)</script>', text)
        self.assertIn('&lt;script&gt;', text)


class GitDiffTests(unittest.TestCase):
    def test_changed_lines_and_untracked(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def git(*args):
                subprocess.run(['git','-C',name,*args],check=True,capture_output=True)
            git('init'); git('config','user.email','unit@example.invalid'); git('config','user.name','Unit')
            (root/'src').mkdir()
            (root/'src/a.html').write_text('<p>不是列表，而是空间。</p>\n<p>原文</p>\n',encoding='utf-8')
            git('add','.'); git('commit','-m','fixture')
            (root/'src/a.html').write_text('<p>不是列表，而是空间。</p>\n<p>不是页面，而是宇宙。</p>\n',encoding='utf-8')
            (root/'src/b.py').write_text('# TODO later\n',encoding='utf-8')
            config=root/'rules.json'; config.write_text(json.dumps({'include':['src/**']}))
            out=root/'result.json'
            p=subprocess.run([sys.executable,str(SCRIPTS/'check_repo.py'),'--root',name,'--config',str(config),'--diff-base','HEAD','--out',str(out)],capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr.decode())
            findings=json.loads(out.read_text())['findings']
            self.assertEqual([(f['path'],f['line']) for f in findings],[('src/a.html',2),('src/b.py',1)])

    def test_real_browser_layout_focused_case_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'tools/quality-browser/journeys.spec.cjs'
            source.parent.mkdir(parents=True)
            source.write_text("test.only('must not pass', () => {});\n")
            p = subprocess.run([sys.executable, str(SCRIPTS/'check_repo.py'), '--root', name], capture_output=True)
            self.assertEqual(p.returncode, 1, p.stderr.decode())
            self.assertIn('test/focused-test', p.stdout.decode())

    def test_invalid_base_is_not_clean(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); config=root/'rules.json'
            config.write_text(json.dumps({'include':['src/**']}))
            p=subprocess.run([sys.executable,str(SCRIPTS/'check_repo.py'),'--root',name,'--config',str(config),'--diff-base','missing'],capture_output=True)
            self.assertEqual(p.returncode,2)

if __name__ == '__main__': unittest.main()
