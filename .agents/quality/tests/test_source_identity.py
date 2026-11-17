import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('source_identity', Path(__file__).parents[1] / 'scripts/source_identity.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class IdentityTests(unittest.TestCase):
    def test_dirty_untracked_deleted_and_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(['git', '-C', directory, *args], check=True, capture_output=True)
            git('init')
            git('config', 'user.email', 'synthetic@example.invalid')
            git('config', 'user.name', 'Synthetic')
            (root / 'file').write_text('original')
            (root / '.gitignore').write_text('ignored\n')
            git('add', '.')
            git('commit', '-m', 'synthetic fixture')
            original = module.identity(root)
            (root / 'ignored').write_text('runtime')
            self.assertEqual(original, module.identity(root))
            (root / 'file').write_text('changed')
            dirty = module.identity(root)
            self.assertNotEqual(original, dirty)
            (root / 'new').write_text('untracked')
            untracked = module.identity(root)
            self.assertNotEqual(dirty, untracked)
            (root / 'file').unlink()
            self.assertNotEqual(untracked, module.identity(root))
