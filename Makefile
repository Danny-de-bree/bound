.PHONY: help sync lint test check build clean tag release

VERSION ?= 0.3.0

help:
	@echo "make sync              Install/sync dependencies"
	@echo "make lint              Run Ruff"
	@echo "make test              Run tests"
	@echo "make check             Run lint + tests"
	@echo "make build             Build wheel + sdist"
	@echo "make clean             Remove build artifacts"
	@echo "make tag VERSION=0.3.0 Create annotated git tag"
	@echo "make release VERSION=0.3.0 Run checks, build and push release tag"

sync:
	uv sync

lint:
	uv run ruff check .

test:
	uv run pytest -q

check: lint test

build: check
	rm -rf dist/
	uv build

clean:
	rm -rf dist/ build/ .pytest_cache/ .ruff_cache/
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +

tag:
	@test -n "$(VERSION)" || (echo "VERSION is required" && exit 1)
	@grep -q 'version = "$(VERSION)"' pyproject.toml || \
		(echo "pyproject.toml version does not match $(VERSION)" && exit 1)
	@test -z "$$(git status --porcelain)" || \
		(echo "Working tree is not clean" && exit 1)
	git tag -a "v$(VERSION)" -m "BOUND v$(VERSION)"

release: check build
	@test -n "$(VERSION)" || (echo "VERSION is required" && exit 1)
	@grep -q 'version = "$(VERSION)"' pyproject.toml || \
		(echo "pyproject.toml version does not match $(VERSION)" && exit 1)
	@test -z "$$(git status --porcelain)" || \
		(echo "Working tree is not clean" && exit 1)
	git push origin HEAD
	git tag -a "v$(VERSION)" -m "BOUND v$(VERSION)"
	git push origin "v$(VERSION)"