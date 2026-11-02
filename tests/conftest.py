"""pytest configuration."""
import pytest


def pytest_collection_modifyitems(config, items):
    """Mark async tests automatically."""
    for item in items:
        if item.get_closest_marker("asyncio") is None:
            if hasattr(item, "function") and hasattr(item.function, "__wrapped__"):
                item.add_marker(pytest.mark.asyncio)
