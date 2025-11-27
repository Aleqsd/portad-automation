.PHONY: help venv install run clean snapshots cron-install cron-remove update pull

PYTHON ?= python3
PIP ?= pip
VENV_DIR ?= .venv

help:
	@echo "Targets:"
	@echo "  make venv         - create virtualenv in $(VENV_DIR)"
	@echo "  make install      - install deps into venv (requests, bs4, lxml)"
	@echo "  make run          - run fetch_portad_dashboard.py with venv"
	@echo "  make update       - pull latest git changes and re-install deps"
	@echo "  make pull         - git pull (no install)"
	@echo "  make clean        - remove venv"
	@echo "  make snapshots    - list snapshot files"
	@echo "  make cron-install - add hourly cron (uses repo path & .env)"
	@echo "  make cron-oneline - print the cron one-liner (copy/paste)"
	@echo "  make cron-remove  - remove the cron entry"

venv:
	$(PYTHON) -m venv $(VENV_DIR)

install: venv
	$(VENV_DIR)/bin/$(PIP) install --upgrade pip
	$(VENV_DIR)/bin/$(PIP) install -r requirements.txt

run:
	$(VENV_DIR)/bin/python fetch_portad_dashboard.py

update: pull install

pull:
	git pull --ff-only

clean:
	rm -rf $(VENV_DIR)

snapshots:
	ls -1 snapshots 2>/dev/null || true

# Cron setup: runs hourly at minute 5
CRON_LINE=5 * * * * cd $(CURDIR) && /usr/bin/env bash -lc 'set -a; . .env; set +a; . $(VENV_DIR)/bin/activate; $(VENV_DIR)/bin/python fetch_portad_dashboard.py >> cron.log 2>&1'
CRON_ONE_SHOT=( crontab -l 2>/dev/null | grep -v "fetch_portad_dashboard.py"; echo "$(CRON_LINE)" ) | crontab -

cron-install:
	( crontab -l 2>/dev/null | grep -v "fetch_portad_dashboard.py"; echo "$(CRON_LINE)" ) | crontab -

cron-oneline:
	@echo "$(CRON_ONE_SHOT)"

cron-remove:
	( crontab -l 2>/dev/null | grep -v "fetch_portad_dashboard.py" ) | crontab -
