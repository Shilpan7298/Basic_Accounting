# Urjapod Order & Invoicing System
VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: help setup seed api web dev test migrate migration templates fixtures up down clean

help:
	@echo "setup      install backend + frontend dependencies"
	@echo "seed       create the schema and seed masters (add DEMO=1 for a worked example)"
	@echo "api        run the API on :8000"
	@echo "web        run the front end on :5173"
	@echo "test       run the backend test suite"
	@echo "migrate    apply Alembic migrations"
	@echo "migration  create a new migration (M=\"message\")"
	@echo "templates  rebuild the starter .docx templates"
	@echo "fixtures   rebuild the anonymised sample PDFs"
	@echo "up/down    docker compose"

setup:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e "backend[dev]"
	cd frontend && npm install

seed:
	$(PY) backend/scripts/seed.py $(if $(DEMO),--demo,)

api:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000

web:
	cd frontend && npm run dev

test:
	$(PY) -m pytest backend/tests -q

migrate:
	cd backend && ../$(PY) -m alembic upgrade head

migration:
	@test -n "$(M)" || (echo 'usage: make migration M="what changed"'; exit 1)
	cd backend && ../$(PY) -m alembic revision --autogenerate -m "$(M)"

templates:
	$(PY) backend/scripts/build_docx_templates.py

fixtures:
	$(PY) backend/scripts/build_fixtures.py

up:
	docker compose up --build

down:
	docker compose down

clean:
	rm -rf storage/*.db storage/generated/* frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
