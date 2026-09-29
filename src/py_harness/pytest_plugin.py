import pytest

INTEGRATION = "integration"
# Either set to False ends an xfail without proving its test still fails.
UNPROVEN_XFAIL = ("strict", "run")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{INTEGRATION}: needs more than its own unit; runs in test-integration, not in test",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    unproven = [
        f"{item.nodeid} ({key}=False)"
        for item in items
        for marker in item.iter_markers("xfail")
        for key in UNPROVEN_XFAIL
        if marker.kwargs.get(key) is False
    ]
    if unproven:
        listed = ", ".join(unproven)
        raise pytest.UsageError(f"an xfail must prove its test still fails; set neither: {listed}")
