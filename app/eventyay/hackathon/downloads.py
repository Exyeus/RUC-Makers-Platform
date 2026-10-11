"""Bounded-memory WSGI/ASGI file responses, including single HTTP byte ranges."""

import asyncio
import mimetypes
import re

from django.core.handlers.asgi import ASGIRequest
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header


BLOCK_SIZE = 256 * 1024


class StreamedFileResponse(StreamingHttpResponse):
    def __init__(self, source, iterator, **kwargs):
        self.source = source
        super().__init__(iterator, **kwargs)

    def close(self):
        # A disconnected client may never start the generator (whose finally
        # block would therefore not run). Always release its file descriptor.
        try:
            self.source.close()
        finally:
            super().close()


def file_chunks(source, start, length):
    try:
        source.seek(start)
        while length:
            block = source.read(min(length, BLOCK_SIZE))
            if not block:
                break
            length -= len(block)
            yield block
    finally:
        source.close()


async def async_file_chunks(source, start, length):
    try:
        await asyncio.to_thread(source.seek, start)
        while length:
            block = await asyncio.to_thread(source.read, min(length, BLOCK_SIZE))
            if not block:
                break
            length -= len(block)
            yield block
    finally:
        source.close()


def stream_file(request, source, size, filename, *, attachment=True, checksum=''):
    etag = f'"{checksum}"' if checksum else ''
    start, end, status = 0, size - 1, 200
    value = request.headers.get('Range', '')
    if request.headers.get('If-Range') and request.headers['If-Range'] != etag:
        value = ''
    if value:
        match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', value)
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                end = min(end, int(match[2])) if match[2] else end
            else:
                start = max(0, size - int(match[2]))
            status = 206
        else:
            start = size
        if start > end or start >= size:
            source.close()
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
    length = end - start + 1
    if request.method == 'HEAD':
        source.close()
        response = HttpResponse(status=status)
    else:
        iterator = async_file_chunks if isinstance(request, ASGIRequest) else file_chunks
        response = StreamedFileResponse(source, iterator(source, start, length), status=status)
    response['Content-Length'] = length
    response['Content-Type'] = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    response['Content-Disposition'] = content_disposition_header(attachment, filename)
    response['Accept-Ranges'] = 'bytes'
    response['X-Content-Type-Options'] = 'nosniff'
    if etag:
        response['ETag'] = etag
    if status == 206:
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return response


def asset_response(request, asset, attachment=True):
    try:
        source = asset.file.open('rb')
    except FileNotFoundError as error:
        raise Http404('材料文件不可用，请联系主办方。') from error
    return stream_file(request, source, asset.size, asset.filename, attachment=attachment, checksum=asset.checksum)
