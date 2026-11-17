#!/usr/bin/env python3
"""Hash actual tracked and untracked nonignored source, including dirty edits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

def identity(root):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    revision = git('rev-parse', 'HEAD').decode().strip()
    names = set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')) - {b''}
    digest = hashlib.sha256(revision.encode())
    for name in sorted(names):
        path = root / os.fsdecode(name)
        digest.update(len(name).to_bytes(8, 'big') + name)
        if path.is_symlink():
            content = b'link:' + os.fsencode(os.readlink(path))
        elif path.is_file():
            content = b'file:' + str(path.stat().st_mode & 0o777).encode() + b':' + path.read_bytes()
        elif not path.exists():
            content = b'deleted'
        else:
            raise ValueError(f'Unsupported source entry: {path}')
        digest.update(hashlib.sha256(content).digest())
    return {'revision': revision, 'source_fingerprint': digest.hexdigest()}

if __name__ == '__main__':
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel']).decode().strip())
    print(json.dumps(identity(root)))
