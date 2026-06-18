PYTHON := .venv/bin/python3

.PHONY: start roi server

start:
	$(PYTHON) main.py

roi:
	$(PYTHON) roi_setup.py --source 2

server:
	$(PYTHON) server.py --config config.yaml
