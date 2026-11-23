.DEFAULT_GOAL := help
PYTHON ?= mise exec -- python
.PHONY: help build test test-browser
help:
	@echo 'build [OUTPUT=extension.zip]; test; test-browser'
build:
	@$(PYTHON) tools/build.py $${OUTPUT:+"--output=$$OUTPUT"}
test:
	@mise exec -- node --test tests/test_chatgpt_capture.mjs
	@$(PYTHON) -m unittest discover -s tests -p 'test_*.py'
test-browser:
	@$(PYTHON) tools/run_browser.py
