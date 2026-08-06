.PHONY: setup unit integration lint typecheck fixture-smoke

setup:
	python -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"

unit:
	.venv/bin/python -m pytest tests/unit -q

integration:
	.venv/bin/python -m pytest -q -m integration

lint:
	.venv/bin/python -m ruff check .

typecheck:
	.venv/bin/python -m mypy src

# Scoped to tests/smoke on purpose: pytest exits 4 when that path is absent and 5 when
# nothing carries the marker, so this target cannot report success on an empty selection.
fixture-smoke:
	.venv/bin/python -m pytest tests/smoke -q -m fixture_smoke
