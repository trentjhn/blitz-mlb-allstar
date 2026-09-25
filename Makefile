PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
INSTALLED := $(VENV)/.installed

.PHONY: install lint test

install: $(INSTALLED)

$(PY):
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
		|| { echo "Python 3.11+ is required. Try: make install PYTHON=python3.11"; exit 1; }
	$(PYTHON) -m venv $(VENV)

# Re-runs pip whenever a requirements file changes, and again after a failed install.
$(INSTALLED): requirements.txt requirements-dev.txt | $(PY)
	$(PY) -m pip install --quiet -r requirements-dev.txt
	touch $@

lint: $(INSTALLED)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

test: $(INSTALLED)
	$(PY) -m pytest -q
