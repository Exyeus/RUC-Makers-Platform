"""Deployment-only Django settings for the Hong Kong website."""

from eventyay.config.settings import *

CSRF_TRUSTED_ORIGINS = ["https://hackathon.rucmakers.club"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
EMAIL_FILE_PATH = "/srv/ruc-makers/runtime/mail"

# Use the pinned native esbuild binary on this host; no runtime npm downloads.
COMPRESS_PRECOMPILERS = tuple(
    (
        kind,
        "esbuild {infile} --bundle --minify --platform=browser"
        if kind == "module"
        else compiler,
    )
    for kind, compiler in COMPRESS_PRECOMPILERS
)
