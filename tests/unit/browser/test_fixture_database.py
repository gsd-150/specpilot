from __future__ import annotations

import pytest

from tests.browser.fixture_app import _require_browser_dsn


def test_w5_browser_scratch_database_is_exactly_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = "postgresql://specpilot@127.0.0.1:55435/specpilot_w5_task9_browser_scratch"
    monkeypatch.setenv("SPECPILOT_BROWSER_DSN", dsn)

    assert _require_browser_dsn() == dsn


@pytest.mark.parametrize(
    "database",
    [
        "specpilot_w5_task9_browser_scratch_extra",
        "prefix_specpilot_w5_task9_browser_scratch",
    ],
)
def test_w5_browser_scratch_allowlist_rejects_near_matches(
    monkeypatch: pytest.MonkeyPatch,
    database: str,
) -> None:
    monkeypatch.setenv(
        "SPECPILOT_BROWSER_DSN",
        f"postgresql://specpilot@127.0.0.1:55435/{database}",
    )

    with pytest.raises(RuntimeError, match="dedicated local database"):
        _require_browser_dsn()
