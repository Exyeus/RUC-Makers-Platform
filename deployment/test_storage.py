"""Offline checks: PYTHONPATH=app python -m unittest discover -s deployment -p test_storage.py."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from django.conf import settings

if not settings.configured:
    settings.configure(DEFAULT_CHARSET='utf-8', DATA_DIR=tempfile.gettempdir(), HACKATHON_STORAGE_BACKEND='local')

from django.core.files.base import ContentFile
from django.test import RequestFactory, override_settings

from eventyay.hackathon.downloads import stream_file
from eventyay.hackathon.storage import PrivateStorage, RangeReader, temporary_root


class StorageTests(unittest.TestCase):
    def test_local_storage_and_private_url(self):
        with tempfile.TemporaryDirectory() as root, override_settings(DATA_DIR=root):
            storage = PrivateStorage()
            name = storage.save('1/team/document.pdf', ContentFile(b'private'))
            with storage.open(name, 'rb') as source:
                self.assertEqual(source.read(), b'private')
            self.assertEqual(storage.size(name), 7)
            with self.assertRaises(ValueError):
                storage.url(name)
            self.assertEqual(temporary_root(), Path(root) / 'hackathon-private/.uploads')
            storage.delete(name)
            self.assertFalse(storage.exists(name))

    def test_range_reader_reuses_stream_for_small_forward_seek(self):
        storage = Mock()
        storage.size.return_value = 10
        storage.key.return_value = 'prefix/name'
        storage.client.get_object.return_value = {'Body': io.BytesIO(b'0123456789')}
        source = RangeReader(storage, 'name')
        self.assertEqual(source.read(2), b'01')
        source.seek(2, 1)
        self.assertEqual(source.read(2), b'45')
        storage.client.get_object.assert_called_once()
        source.close()
        self.assertTrue(source.closed)

    def test_download_ranges_head_and_invalid_range(self):
        factory = RequestFactory()
        for method, headers, status, expected in (
            ('get', {'HTTP_RANGE': 'bytes=2-4'}, 206, b'234'),
            ('get', {'HTTP_RANGE': 'bytes=-2'}, 206, b'89'),
            ('get', {'HTTP_RANGE': 'bytes=99-'}, 416, b''),
            ('head', {}, 200, b''),
        ):
            source = io.BytesIO(b'0123456789')
            response = stream_file(getattr(factory, method)('/', **headers), source, 10, 'video.mp4')
            self.assertEqual(response.status_code, status)
            data = b''.join(response.streaming_content) if response.streaming else response.content
            self.assertEqual(data, expected)
            response.close()
            self.assertTrue(source.closed)
