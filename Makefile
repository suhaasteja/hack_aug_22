VENV := .venv
PY := $(VENV)/bin/python

.PHONY: setup run test clean

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	@echo "setup complete"

run:
	$(PY) -m app.main

test:
	$(PY) -m pytest -q

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__
