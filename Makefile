.PHONY: help install install-dev ingest process process-full labels app test lint clean

REGION ?= sao_jose_sc

help:
	@echo "Available commands:"
	@echo "  make install        - install production dependencies"
	@echo "  make install-dev    - install production + dev dependencies"
	@echo "  make ingest         - download raw data for REGION"
	@echo "  make process        - run full pipeline for REGION"
	@echo "  make app            - launch the Streamlit app"
	@echo "  make test           - run tests"
	@echo "  make lint           - run ruff + black --check"
	@echo "  make clean          - remove interim/processed data (keeps raw)"
	@echo ""
	@echo "Usage: make process REGION=sao_jose_sc"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

ingest:
	python -m src.core.pipeline --region $(REGION) --stage ingest

process:
	python -m src.core.pipeline --region $(REGION) --stage all

process-full:
	python -m src.core.pipeline --region $(REGION) --stage all --with-labels

labels:
	python -m src.core.pipeline --region $(REGION) --stage labels

app:
	streamlit run src/app/streamlit_app.py

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/
	black --check src/ tests/

clean:
	rm -rf data/interim/* data/processed/*