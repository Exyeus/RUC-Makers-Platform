from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static


register = template.Library()


@lru_cache(maxsize=64)
def asset_digest(filename: str, modified_ns: int, size: int) -> str:
    return sha256(Path(filename).read_bytes()).hexdigest()[:16]


@register.simple_tag
def versioned_static(path: str) -> str:
    url = static(path)
    filename = finders.find(path)
    if not filename:
        return url
    info = Path(filename).stat()
    digest = asset_digest(filename, info.st_mtime_ns, info.st_size)
    separator = '&' if '?' in url else '?'
    return f'{url}{separator}v={digest}'
