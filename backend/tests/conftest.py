import pytest


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from app.db.database import engine, Base
    import app.models.decision_context_models  # noqa
    import app.models.governance_storage  # noqa
    import app.models.evidence  # noqa
    Base.metadata.create_all(bind=engine)

    from app.api.v1.endpoints.auth import _ip_rate_limits
    _ip_rate_limits.clear()
    from app.services.governance_registry import initialize_registry_seeds
    initialize_registry_seeds()
