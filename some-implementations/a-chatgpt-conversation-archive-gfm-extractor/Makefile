.DEFAULT_GOAL := help
PYTHON ?= mise exec -- uv run --locked --package a-chatgpt-conversation-archive-gfm-extractor python
.PHONY: help list extract test build
help:
	@echo 'list FILE=archive.zip; extract FILE=archive.zip CONVERSATION=id OUTPUT=external.md [TIP=node] [METADATA=fields.yml]; test; build'
list:
	@$(PYTHON) -m a_chatgpt_conversation_archive_gfm_extractor --list -- "$${FILE:?Set FILE}"
extract:
	@$(PYTHON) -m a_chatgpt_conversation_archive_gfm_extractor --conversation="$${CONVERSATION:?Set CONVERSATION}" --output="$${OUTPUT:?Set OUTPUT}" $${TIP:+"--tip=$$TIP"} $${METADATA:+"--metadata=$$METADATA"} -- "$${FILE:?Set FILE}"
test:
	@$(PYTHON) ../../tools/run_tests.py a-chatgpt-conversation-archive-gfm-extractor
build:
	mise exec -- uv build --package a-chatgpt-conversation-archive-gfm-extractor
