.PHONY: setup run test stop seed clean

# ----------------------------------------------------
# Artikate Studio Backend Assessment
# One-command automation for setup, run, and test
# ----------------------------------------------------

VENV = venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
CELERY = $(VENV)/bin/celery
MANAGE = $(PYTHON) manage.py

# --- Setup ---
setup: $(VENV)/bin/activate
	$(PIP) install -r requirements.txt
	$(MANAGE) migrate
	$(MANAGE) seed_data --orders 500
	@echo ""
	@echo "✅ Setup complete! Run 'make run' to start the server."

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

# --- Run ---
run:
	@echo "Starting Celery worker in background..."
	$(CELERY) -A config worker -l info --detach --pidfile=celery.pid --logfile=celery.log
	@echo "Starting Django dev server..."
	$(MANAGE) runserver

# --- Test ---
test:
	$(MANAGE) test orders notifications --verbosity=2

# --- Stop ---
stop:
	@if [ -f celery.pid ]; then \
		kill $$(cat celery.pid) 2>/dev/null && echo "Celery worker stopped." && rm -f celery.pid; \
	else \
		echo "No Celery worker running (no celery.pid found)."; \
	fi

# --- Seed Data ---
seed:
	$(MANAGE) seed_data --orders 500

# --- Clean ---
clean:
	rm -f db.sqlite3 celery.pid celery.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned."
