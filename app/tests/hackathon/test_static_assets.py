import os
from hashlib import sha256
from unittest.mock import patch

from django.template import Context, Template

from eventyay.hackathon.templatetags.hackathon_static import versioned_static


def test_stylesheet_url_changes_with_content_without_a_worker_restart(tmp_path):
    asset = tmp_path / 'pages.css'
    asset.write_text('body { color: white; }')
    with patch('eventyay.hackathon.templatetags.hackathon_static.finders.find', return_value=str(asset)):
        first = versioned_static('hackathon/workbench-pages.css')
        assert first.endswith('?v=' + sha256(asset.read_bytes()).hexdigest()[:16])
        assert versioned_static('hackathon/workbench-pages.css') == first
        original = asset.stat()
        asset.write_text('body { color: black; }')
        assert asset.stat().st_size == original.st_size
        os.utime(asset, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000))
        second = versioned_static('hackathon/workbench-pages.css')
        assert second != first
        assert second.endswith('?v=' + sha256(asset.read_bytes()).hexdigest()[:16])


def test_versioned_static_keeps_storage_url_and_escapes_html(tmp_path):
    asset = tmp_path / 'pages.css'
    asset.write_text('body {}')
    with (
        patch('eventyay.hackathon.templatetags.hackathon_static.finders.find', return_value=str(asset)),
        patch(
            'eventyay.hackathon.templatetags.hackathon_static.static',
            return_value='https://cdn.example.org/pages.css?a=1',
        ),
    ):
        rendered = Template('{% load hackathon_static %}<link href="{% versioned_static "pages.css" %}">').render(
            Context()
        )
        assert 'https://cdn.example.org/pages.css?a=1&amp;v=' in rendered


def test_missing_asset_keeps_normal_static_url():
    with patch('eventyay.hackathon.templatetags.hackathon_static.finders.find', return_value=None):
        assert versioned_static('hackathon/missing.css').endswith('/hackathon/missing.css')
