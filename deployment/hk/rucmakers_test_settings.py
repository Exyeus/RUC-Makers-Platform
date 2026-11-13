"""The deployment regression suite is confined to this precreated test database."""

from copy import deepcopy

from tests.tickets.settings import *

DATABASES = deepcopy(DATABASES)
DATABASES["default"]["NAME"] = "test_rucmakers_hk_20261113"
DATABASES["default"]["TEST"] = {"NAME": "test_rucmakers_hk_20261113"}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
HACKATHON_STORAGE_BACKEND = "local"

# Exercise the real SQL constraints and migration state graph, just like production.
MIGRATION_MODULES = {}
