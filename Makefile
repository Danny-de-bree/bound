.PHONY: help sync lock lint mypy test cov check build clean tag release

help:
	@echo "make sync                  Install/sync dependencies"
	@echo "make lock                  Update uv.lock after dependency changes"
	@echo "make lint                  Run Ruff"
	@echo "make mypy                  Run mypy type checking"
	@echo "make test                  Run tests"
	@echo "make cov                   Run tests with coverage"
	@echo "make check                 Run lockfile check + lint + mypy + tests"
	@echo "make build                 Build wheel + sdist"
	@echo "make clean                 Remove build artifacts"
	@echo "make tag VERSION=0.4.0     Create annotated git tag"
	@echo "make release VERSION=0.4.0 Run checks, build and push release tag"

sync:
	uv sync

lock:
	uv lock

lint:
	uv run ruff check .

mypy:
	uv run mypy src/

test:
	uv run pytest -q

cov:
	uv run pytest -q --cov=src/bound --cov-report=term-missing

check: lock-check lint mypy test

lock-check:
	uv lock --check

build: check
	rm -rf dist/
	uv build

clean:
	rm -rf dist/ build/ .pytest_cache/ .ruff_cache/
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +

validate-version:
	@test -n "$(VERSION)" || \
		(echo "ERROR: VERSION is required. Example: make release VERSION=0.4.0" && exit 1)
	@grep -q 'version = "$(VERSION)"' pyproject.toml || \
		(echo "ERROR: pyproject.toml version does not match $(VERSION)" && exit 1)
	@if git rev-parse "v$(VERSION)" >/dev/null 2>&1; then \
		echo "ERROR: Tag v$(VERSION) already exists"; exit 1; \
	fi

tag: validate-version
	@test -z "$$(git status --porcelain)" || \
		(echo "ERROR: Working tree is not clean" && exit 1)
	git tag -a "v$(VERSION)" -m "BOUND v$(VERSION)"

release: validate-version check build
	@test -z "$$(git status --porcelain)" || \
		(echo "ERROR: Working tree is not clean" && exit 1)
	git push origin HEAD
	git tag -a "v$(VERSION)" -m "BOUND v$(VERSION)"
	git push origin "v$(VERSION)"