import atexit
import os
import shutil
import tempfile

import pytest

# The tests create, drop and clear tables. They must never touch the application's own
# database (the local demo sentinel.db or a server database), so every test run gets a
# throwaway SQLite database. This runs before any app module is imported, so the app
# engine is created against it. SENTINEL_TEST_DATABASE_URL can point to another test database.
_test_db_dir = tempfile.mkdtemp(prefix="sentinel-tests-")
_test_db_path = os.path.join(_test_db_dir, "test.db").replace("\\", "/")
os.environ.pop("DATABASE_URL", None)
os.environ["SQLALCHEMY_DATABASE_URL"] = (
    os.environ.get("SENTINEL_TEST_DATABASE_URL") or f"sqlite:///{_test_db_path}"
)
atexit.register(shutil.rmtree, _test_db_dir, ignore_errors=True)

# Import the database module before any test module imports a model. Its startup then creates
# every table in the throwaway database; a test module that imports a model first would load the
# model half-way through and its table (e.g. connectors) would be missing.
import app.db.database  # noqa: E402,F401


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from app.api.v1.endpoints.auth import _ip_rate_limits
    _ip_rate_limits.clear()
    from app.services.governance_registry import initialize_registry_seeds
    initialize_registry_seeds()
