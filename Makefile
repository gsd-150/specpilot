.PHONY: setup check unit integration integration-db integration-qdrant lint typecheck fixture-smoke require-dsn require-qdrant frontend-test frontend-build

SPECPILOT_PYTHON ?= .venv/bin/python

setup:
	python -m venv .venv
	$(SPECPILOT_PYTHON) -m pip install --upgrade pip
	$(SPECPILOT_PYTHON) -m pip install -e ".[dev]"

check: lint typecheck unit

frontend-test:
	npm --prefix web/trace test -- --run

frontend-build:
	npm --prefix web/trace run build

unit:
	$(SPECPILOT_PYTHON) -m pytest tests/unit -q

integration:
	$(SPECPILOT_PYTHON) -m pytest -q -m integration

# The ledger tests skip without a database, and a skipped run produces no
# concurrency or recovery evidence at all, so this target refuses to look green
# when SPECPILOT_TEST_DSN is unset.
integration-db: require-dsn
	$(SPECPILOT_PYTHON) -m pytest tests/integration -q

lint:
	$(SPECPILOT_PYTHON) -m ruff check .

typecheck:
	$(SPECPILOT_PYTHON) -m mypy src

# Scoped to tests/smoke on purpose: pytest exits 4 when that path is absent and 5
# when nothing carries the marker, so this target cannot report success on an
# empty selection. The DSN is required for the same reason as integration-db:
# the demo route exercises the real ledger, and a skip proves nothing.
# Same reasoning as integration-db: these are the only evidence that the
# collection schema, point count, and read-only-after-freeze posture hold
# against a real server, and a skipped run produces none of it.
integration-qdrant: require-qdrant
	$(SPECPILOT_PYTHON) -m pytest tests/integration/qdrant -q

require-qdrant:
	@test -n "$$SPECPILOT_TEST_QDRANT_URL" || { \
		echo "set SPECPILOT_TEST_QDRANT_URL; see compose.index.yaml"; \
		exit 1; }

fixture-smoke: require-dsn
	$(SPECPILOT_PYTHON) -m pytest tests/smoke -q -m fixture_smoke

require-dsn:
	@test -n "$$SPECPILOT_TEST_DSN" || { \
		echo "set SPECPILOT_TEST_DSN to a throwaway PostgreSQL; see README"; \
		exit 1; }
