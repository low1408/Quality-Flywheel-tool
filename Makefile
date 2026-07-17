SHELL := /bin/sh

.PHONY: help install-dev test test-agent-quality test-flywheel check-extension check clean

help:
	@echo "Quality Flywheel development targets"
	@echo "  install-dev        install both Python projects with development dependencies"
	@echo "  test               run both Python test suites"
	@echo "  check-extension    validate the VS Code extension"
	@echo "  check              run the complete local/CI verification suite"
	@echo "  clean              remove caches, egg metadata, and packaged VSIX files"

install-dev:
	@./scripts/install-dev.sh

test: test-agent-quality test-flywheel

test-agent-quality:
	@./scripts/check.sh agent-quality

test-flywheel:
	@./scripts/check.sh flywheel

check-extension:
	@./scripts/check.sh extension

check:
	@./scripts/check.sh all

clean:
	@./scripts/clean.sh
