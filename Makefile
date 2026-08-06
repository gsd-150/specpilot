.PHONY: setup unit integration integration-db lint typecheck fixture-smoke

setup:
	python -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"

unit:
	.venv/bin/python -m pytest tests/unit -q

integration:
	.venv/bin/python -m pytest -q -m integration

# The ledger tests skip without a database, and a skipped run produces no
# concurrency or recovery evidence at all, so this target refuses to look green
# when SPECPILOT_TEST_DSN is unset.
integration-db:
	@test -n "$$SPECPILOT_TEST_DSN" || { echo "integration-db: set SPECPILOT_TEST_DSN to a throwaway PostgreSQL"; exit 1; }
	.venv/bin/python -m pytest tests/integration/egress -q

lint:
	.venv/bin/python -m ruff check .

typecheck:
	.venv/bin/python -m mypy src

# Scoped to tests/smoke on purpose: pytest exits 4 when that path is absent and 5 when
# nothing carries the marker, so this target cannot report success on an empty selection.
fixture-smoke:
	.venv/bin/python -m pytest tests/smoke -q -m fixture_smoke
