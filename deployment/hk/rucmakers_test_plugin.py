"""Isolate upstream cache callbacks and constrain transactional fixture cleanup."""

import pytest
from django.db.backends.postgresql.operations import DatabaseOperations


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    # Django is initialized by pytest-django before this hook.
    from eventyay.base.models.cache import VersionedModel

    VersionedModel._set_cache_version_sync = lambda self: None
    original = DatabaseOperations.sql_flush

    def flush_test_database(self, style, tables, **kwargs):
        if self.connection.settings_dict["NAME"] != "test_rucmakers_hk_20261113":
            raise RuntimeError(
                "Refusing to flush a database outside the deployment test sandbox."
            )
        kwargs["allow_cascade"] = True
        return original(self, style, tables, **kwargs)

    DatabaseOperations.sql_flush = flush_test_database
