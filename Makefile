.PHONY: setup check unit integration integration-db integration-qdrant lint typecheck fixture-smoke require-dsn require-qdrant

setup:
	python -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"

check: lint typecheck unit

unit:
	.venv/bin/python -m pytest tests/unit -q

integration:
	.venv/bin/python -m pytest -q -m integration

# The ledger tests skip without a database, and a skipped run produces no
# concurrency or recovery evidence at all, so this target refuses to look green
# when SPECPILOT_TEST_DSN is unset.
integration-db: require-dsn
	.venv/bin/python -m pytest tests/integration -q

lint:
	.venv/bin/python -m ruff check .

typecheck:
	.venv/bin/python -m mypy src

# Scoped to tests/smoke on purpose: pytest exits 4 when that path is absent and 5
# when nothing carries the marker, so this target cannot report success on an
# empty selection. The DSN is required for the same reason as integration-db:
# the demo route exercises the real ledger, and a skip proves nothing.
# Same reasoning as integration-db: these are the only evidence that the
# collection schema, point count, and read-only-after-freeze posture hold
# against a real server, and a skipped run produces none of it.
integration-qdrant: require-qdrant
	.venv/bin/python -m pytest tests/integration/qdrant -q

require-qdrant:
	@test -n "$$SPECPILOT_TEST_QDRANT_URL" || { \
		echo "set SPECPILOT_TEST_QDRANT_URL; see compose.index.yaml"; \
		exit 1; }

fixture-smoke: require-dsn
	.venv/bin/python -m pytest tests/smoke -q -m fixture_smoke

require-dsn:
	@test -n "$$SPECPILOT_TEST_DSN" || { \
		echo "set SPECPILOT_TEST_DSN to a throwaway PostgreSQL; see README"; \
		exit 1; }
