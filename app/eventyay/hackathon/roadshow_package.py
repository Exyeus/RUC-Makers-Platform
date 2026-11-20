"""The roadshow ZIP: every finalist's frozen roadshow file, named in roadshow order.

File names are 路演序号_队伍ID_队长_项目名.原扩展名 with a three-digit sequence,
so sorting by name gives the queue. 路演材料清单.csv lists every file. The package
reads the files frozen at the deadline and verifies each checksum; a missing or
changed file stops the package instead of producing a partial one.
"""

import csv
import hashlib
import io
import re
import tempfile
import zipfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils import timezone

from eventyay.hackathon.storage import STORAGE_ERRORS, PrivateStorage


KIND_LABELS = {'video': '视频', 'slides': 'PDF'}


def safe_part(value, limit=40):
    """Keep Chinese, letters and digits; drop path separators and characters file systems reject."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '', str(value or '')).strip()
    text = re.sub(r'\s+', ' ', text)
    return text[:limit].strip(' .') or '未命名'


def package_name(order, now=None):
    now = timezone.localtime(now or timezone.now())
    return f'造物有心_路演材料_顺序v{order.version:03d}_{now:%Y%m%d_%H%M}.zip'


def plan_files(order):
    """The files to pack, or ValidationError listing every team that cannot be packed."""
    entries = {row['project_id']: row for row in order.snapshot.manifest['entries']}
    files, issues = [], []
    for row in order.entries:
        frozen = entries.get(row['project_id'])
        kind = (frozen or {}).get('final_material_kind') or row.get('final_material_kind')
        asset = next((item for item in (frozen or {}).get('assets', []) if item['kind'] == kind), None)
        label = f'{row["position"]:03d} {row["team"]}'
        if not frozen:
            issues.append(f'{label}：名单中缺少该作品')
            continue
        if kind not in KIND_LABELS or not asset:
            issues.append(f'{label}：没有路演材料')
            continue
        suffix = Path(asset['filename']).suffix.lower()
        if kind == 'slides' and suffix != '.pdf':
            issues.append(f'{label}：路演材料不是 PDF')
            continue
        name = '_'.join(
            [
                f'{row["position"]:03d}',
                safe_part(row.get('team_code'), 20),
                safe_part(row.get('captain'), 20),
                safe_part(row.get('title')),
            ]
        )
        files.append({'row': row, 'asset': asset, 'kind': kind, 'filename': name + suffix, 'frozen': frozen})
    if not order.entries:
        issues.append('路演顺序为空')
    if issues:
        raise ValidationError(issues)
    return files


def manifest_csv(files, tracks):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ['路演序号', '队伍ID', '队长', '项目', '赛道', '材料类型', '文件名', '正式提交凭证', 'SHA-256', '校验']
    )
    for item in files:
        row = item['row']
        writer.writerow(
            [
                f'{row["position"]:03d}',
                row.get('team_code', ''),
                row.get('captain', ''),
                row.get('title', ''),
                tracks.get(str(row.get('track_id')), ''),
                KIND_LABELS[item['kind']],
                item['filename'],
                item['frozen'].get('receipt', ''),
                item['asset'].get('sha256', ''),
                '通过',
            ]
        )
    return '﻿' + buffer.getvalue()


def build(order, tracks):
    """Write the ZIP to a temporary file. Returns (file, size). Raises ValidationError on any problem."""
    files = plan_files(order)
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    storage = PrivateStorage()
    issues = []
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for item in files:
                digest, count = hashlib.sha256(), 0
                try:
                    with (
                        storage.open(item['asset']['storage_name'], 'rb') as source,
                        archive.open(item['filename'], 'w', force_zip64=True) as destination,
                    ):
                        while block := source.read(1024 * 1024):
                            count += len(block)
                            digest.update(block)
                            destination.write(block)
                except STORAGE_ERRORS:
                    issues.append(f'{item["row"]["position"]:03d} {item["row"]["team"]}：文件无法读取')
                    continue
                expected = item['asset'].get('sha256') or item['asset'].get('checksum')
                if count != item['asset'].get('size') or (expected and digest.hexdigest() != expected):
                    issues.append(f'{item["row"]["position"]:03d} {item["row"]["team"]}：文件与提交时不一致')
            archive.writestr('路演材料清单.csv', manifest_csv(files, tracks))
    except Exception:
        output.close()
        raise
    if issues:
        output.close()
        raise ValidationError(issues)
    size = output.tell()
    output.seek(0)
    return output, size
