"""Bounded ZIP directory previews; file contents are never decompressed."""

import zipfile

from django.core.exceptions import ValidationError

from eventyay.hackathon.forms import validate_zip_directory


def zip_preview(source):
    validate_zip_directory(source)
    roots = {}
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        for entry in entries:
            parts = entry.filename.split('/')
            if entry.filename.startswith(('/', '\\')) or '\\' in entry.filename or '..' in parts:
                raise ValidationError('压缩包包含不安全的路径，无法预览。')
            name = parts[0]
            if not name:
                continue
            row = roots.setdefault(name, {'name': name, 'directory': False, 'size': 0, 'files': 0})
            row['directory'] |= len(parts) > 1 or entry.is_dir()
            row['size'] += entry.file_size
            row['files'] += not entry.is_dir()
    ordered = sorted(roots.values(), key=lambda row: (not row['directory'], row['name'].casefold()))
    return {'entries': ordered[:200], 'total': len(ordered), 'truncated': len(ordered) > 200}
