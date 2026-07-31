# trend-hunter Makefile
#
# Single source of truth for dev / ops commands. Every target is idempotent
# and safe to re-run. The free, local-only stack needs very little here.

PYTHON     ?= python3
VENV       ?= .venv
VENV_PY    := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip

.DEFAULT_GOAL := help

.PHONY: help init install run scan doctor aggregate backup dashboard test lint clean money money-cycle forecast arbitrage scaffold ads loop-report migrate calibrate calibrate-backfill alert docs

help: ## show this help
	@awk 'BEGIN {FS = ":.*?## "; printf "Usage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── one-time setup ──────────────────────────────────────────────────────────
init: ## create venv + install deps + scaffold data dirs
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip wheel
	$(PIP) install -e ".[dev]"
	@mkdir -p data/logs
	@touch data/.gitkeep
	@echo "  ✅ venv ready at $(VENV)"
	@echo "     activate with:  source $(VENV)/bin/activate"

install: init ## alias for init

# ── daily operational targets ──────────────────────────────────────────────
run: ## daily cycle: scan + classify + aggregate + doctor
	$(VENV_PY) -m trend_hunter.cli run

scan: ## ingest only
	$(VENV_PY) -m trend_hunter.cli scan

doctor: ## self-test (DB integrity, free disk, source freshness)
	$(VENV_PY) -m trend_hunter.cli doctor

aggregate: ## build dashboard roll-ups
	$(VENV_PY) -m trend_hunter.cli aggregate

forecast: ## smoke-test the BaselineForecaster
	$(VENV_PY) -m trend_hunter.cli forecast

arbitrage: ## match trending products ↔ supplier catalog
	$(VENV_PY) -m trend_hunter.cli arbitrage

ads: ## validate-ads command (list ads ≥ 30 days running)
	$(VENV_PY) -m trend_hunter.cli validate-ads

validate-ads: ads ## alias
	$(VENV_PY) -m trend_hunter.cli validate-ads

scaffold: ## scaffold top 3 profitable rows (dry-run by default)
	$(VENV_PY) -m trend_hunter.cli scaffold --top 3

money: ## full money cycle: arbitrage → scaffold top 3
	$(VENV_PY) -m trend_hunter.cli money-sweep --top 3

money-cycle: ## full daily money cycle: scan → aggregate → money
	$(VENV_PY) -m trend_hunter.cli run
	$(VENV_PY) -m trend_hunter.cli money-sweep --top 3

migrate: ## apply pending schema migrations (open writer briefly; safe to re-run)
	$(VENV_PY) -m trend_hunter.cli migrate

loop-report: migrate ## apply migrations then dump 7-day feedback summary from run_history
	$(VENV_PY) -m trend_hunter.cli loop-report --days 7

calibrate: migrate ## forecast back-testing MAPE summary
	$(VENV_PY) -m trend_hunter.cli calibrate summary --days 7

calibrate-list: migrate ## list pending calibrations awaiting actual prices
	$(VENV_PY) -m trend_hunter.cli calibrate list --days 30

calibrate-backfill: migrate ## resolve overdue calibrations one-shot (auto-resolve leg)
	$(VENV_PY) -m trend_hunter.cli calibrate backfill --days 30

alert: ## check success rates and push notifications if degraded
	$(VENV_PY) -m trend_hunter.cli alert

schedule: ## start auto-scheduler (foreground; systemd-friendly)
	$(VENV_PY) -m trend_hunter.flows.daily

install-service: scripts/install-service.sh ## install + enable + start the systemd auto-scheduler
	bash scripts/install-service.sh

uninstall-service: scripts/install-service.sh ## stop + disable + remove the systemd scheduler service
	bash scripts/install-service.sh --uninstall

service-status: ## check if the scheduler service is running
	systemctl --user --no-pager status buffy-scheduler 2>&1 || echo "(not installed — run 'make install-service')"

service-logs: ## tail the scheduler service logs
	journalctl --user -u buffy-scheduler -n 50 --no-pager

dashboard: ## start Streamlit on http://localhost:8501
	$(VENV_PY) -m streamlit run trend_hunter/ui/Home.py --server.port 8501 --server.headless true

backup: ## snapshot local DB; rotate out-of-tree (restic / external drive)
	cp data/trends.duckdb data/trends-$(shell date -u +%Y%m%d-%H%M%S).duckdb
	@echo "  ✅ snapshot created (manual rotation; configure restic for off-host archival)"

# ── dev targets ─────────────────────────────────────────────────────────────
test: ## pytest
	$(VENV_PY) -m pytest -q

lint: ## ruff + mypy (lenient — Phase-1 noise tolerated)
	$(VENV_PY) -m ruff check trend_hunter tests ; true
	$(VENV_PY) -m mypy trend_hunter ; true

docs: ## loop-engineering diagram from docs/LOOP_ENGINEERING.md
	@echo "Open docs/LOOP_ENGINEERING.md in your editor for the live writeup."

clean: ## remove build artifacts
	@rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "  ✅ cleaned"
