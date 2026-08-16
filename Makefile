.PHONY: setup check unit cli integration integration-db integration-qdrant lint typecheck fixture-smoke require-dsn require-qdrant require-browser-dsn require-compose-env frontend-test frontend-build compose-check package-check image-check image-cold-check image-verify packaged-demo-check browser full-service w5-check ingest-real

SPECPILOT_PYTHON ?= .venv/bin/python
SPECPILOT_W5_TIMEOUT_SECONDS ?= 1800
SPECPILOT_W5_RUN := perl -e 'alarm shift; exec @ARGV' $(SPECPILOT_W5_TIMEOUT_SECONDS)
SPECPILOT_W5_ENV := env PYTHONPATH="$(CURDIR):$(CURDIR)/src"

setup:
	python -m venv .venv
	$(SPECPILOT_PYTHON) -m pip install --upgrade pip
	$(SPECPILOT_PYTHON) -m pip install -e ".[dev]"

check: lint typecheck unit cli

frontend-test:
	npm --prefix web/trace test -- --run

frontend-build:
	npm --prefix web/trace run build

unit:
	$(SPECPILOT_PYTHON) -m pytest tests/unit -q

cli:
	$(SPECPILOT_PYTHON) -m pytest tests/cli -q

integration:
	$(SPECPILOT_PYTHON) -m pytest -q -m integration

# The ledger tests skip without a database, and a skipped run produces no
# concurrency or recovery evidence at all, so this target refuses to look green
# when SPECPILOT_TEST_DSN is unset.
integration-db: require-dsn
	$(SPECPILOT_PYTHON) -m pytest tests/integration -q

lint:
	$(SPECPILOT_PYTHON) -m ruff check .
	$(SPECPILOT_PYTHON) scripts/check_clause_prose.py

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

fixture-smoke: require-dsn require-qdrant
	$(SPECPILOT_PYTHON) -m pytest tests/smoke -q -m fixture_smoke

require-dsn:
	@test -n "$$SPECPILOT_TEST_DSN" || { \
		echo "set SPECPILOT_TEST_DSN to a throwaway PostgreSQL; see README"; \
		exit 1; }

require-browser-dsn:
	@test -n "$$SPECPILOT_BROWSER_DSN" || { \
		echo "set SPECPILOT_BROWSER_DSN to the fresh dedicated browser database"; \
		exit 1; }

require-compose-env:
	@test -n "$$SPECPILOT_COMPOSE_ENV_FILE" || { \
		echo "set SPECPILOT_COMPOSE_ENV_FILE to an explicit local fixture env file"; \
		exit 1; }
	@test -f "$$SPECPILOT_COMPOSE_ENV_FILE" || { \
		echo "SPECPILOT_COMPOSE_ENV_FILE does not name a file"; \
		exit 1; }

# Render every deployment shape. The base and real configurations must remain
# unpublished; their structural exposure assertions also live in the unit gate.
compose-check: require-compose-env
	docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" --profile demo config --quiet
	docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" -f compose.yaml -f compose.demo.yaml --profile demo config --quiet
	docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" -f compose.yaml -f compose.real.yaml --profile real config --quiet
	@if docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" --profile real config | grep -q "published:"; then \
		echo "base/real configuration publishes a host port"; exit 1; fi
	@if docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" -f compose.yaml -f compose.real.yaml --profile real config | grep -q "published:"; then \
		echo "real override publishes a host port"; exit 1; fi

package-check:
	@test ! -L tmp || { echo "refusing symlinked package staging parent"; exit 1; }
	rm -rf -- build tmp/w5-dist
	mkdir -p tmp/w5-dist
	$(SPECPILOT_PYTHON) -m build --wheel --outdir tmp/w5-dist
	$(SPECPILOT_PYTHON) scripts/w5_verify_wheel.py

image-check: require-compose-env
	COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" -f compose.yaml -f compose.real.yaml --profile demo --profile real --profile ingestion build api mcp fixture-init real-init ingestion
	$(MAKE) image-verify

image-cold-check: require-compose-env
	COMPOSE_PARALLEL_LIMIT=1 docker compose --env-file "$$SPECPILOT_COMPOSE_ENV_FILE" -f compose.yaml -f compose.real.yaml --profile demo --profile real --profile ingestion build --no-cache api mcp fixture-init real-init ingestion
	$(MAKE) image-verify

image-verify:
	@for image in specpilot-real-init specpilot-fixture-init; do \
		if docker history --no-trunc "$$image" | grep -Eq 'node:|npm |static/trace'; then \
			echo "$$image contains frontend build history"; exit 1; fi; \
		docker run --rm --entrypoint python "$$image" -m specpilot.cli --help >/dev/null; \
		docker run --rm --entrypoint python "$$image" -c 'from pathlib import Path; import specpilot; assert not (Path(specpilot.__file__).parent / "api/static/trace").exists()'; \
	done

packaged-demo-check:
	$(SPECPILOT_PYTHON) scripts/w5_packaged_gate.py

browser: require-browser-dsn
	PYTHONPATH="$(CURDIR):$(CURDIR)/src" \
		SPECPILOT_PYTHON="$(SPECPILOT_PYTHON)" \
		npm --prefix web/trace run test:browser

# One invocation over the complete test tree. Capturing output lets the target
# reject service skips explicitly instead of accepting pytest's exit status 0.
full-service: require-dsn require-qdrant
	@report=$$(mktemp -t specpilot-w5-pytest.XXXXXX); status=0; \
	$(SPECPILOT_W5_RUN) env PYTHONPATH="$(CURDIR):$(CURDIR)/src" \
		$(SPECPILOT_PYTHON) -m pytest --import-mode=importlib -q -rs \
		>"$$report" 2>&1 || status=$$?; \
	cat "$$report"; \
	if test "$$status" -ne 0; then rm -f "$$report"; exit "$$status"; fi; \
	if grep -Eq '[0-9]+ skipped|SKIPPED' "$$report"; then \
		echo "W5 refuses a full-service run with skipped tests"; \
		rm -f "$$report"; exit 1; \
	fi; \
	rm -f "$$report"

# This is intentionally a recipe rather than prerequisites: each phase gets a
# hard wall-clock bound and the ordered log shows which evidence surface failed.
# The complete tree includes the four registered fixture scenarios over SSE.
w5-check:
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) check
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) frontend-test
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) frontend-build
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) compose-check
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) package-check
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) full-service
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) browser
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) image-check
	$(SPECPILOT_W5_RUN) $(SPECPILOT_W5_ENV) $(MAKE) packaged-demo-check

ingest-real:
	@test -n "$(CORPUS_DIR)" || { echo "set CORPUS_DIR to an absolute path"; exit 1; }
	@test "$$(printf '%s' "$(CORPUS_DIR)" | cut -c1)" = / || { echo "CORPUS_DIR must be absolute"; exit 1; }
	$(SPECPILOT_PYTHON) -m specpilot.cli corpus init-real \
		--corpus-dir "$(CORPUS_DIR)" \
		--corpus-manifest-dir "$${SPECPILOT_MCP_CORPUS_MANIFEST_DIR_HOST:?set SPECPILOT_MCP_CORPUS_MANIFEST_DIR_HOST}" \
		--ready-dir "$${SPECPILOT_READY_DIR:?set SPECPILOT_READY_DIR}" \
		--qdrant-url "$${SPECPILOT_QDRANT_URL:?set SPECPILOT_QDRANT_URL}"
