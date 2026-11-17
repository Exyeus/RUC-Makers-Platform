import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_upstream.py"


class UpstreamIntegrityTests(unittest.TestCase):
    def test_original_passes_but_tampered_and_missing_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.md"
            source.write_bytes(b"official original\n")
            lock = root / "lock.json"
            lock.write_text(json.dumps({"schema_version": 1, "files": [{
                "path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}))
            def run():
                return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--lock", str(lock)], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            source.write_bytes(b"rewritten guidance\n")
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum mismatch", result.stderr)
            source.unlink()
            self.assertNotEqual(run().returncode, 0)

    def test_empty_and_traversal_manifest_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "lock.json"
            for records in ([], [{"path": "../outside.md", "sha256": "0" * 64}]):
                lock.write_text(json.dumps({"schema_version": 1, "files": records}))
                result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--lock", str(lock)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
