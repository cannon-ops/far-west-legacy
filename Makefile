.PHONY: test lint format run clean

# Run all tests
test:
	pytest tests/ -v

# Lint with ruff
lint:
	ruff check src/ tests/

# Format with black
format:
	black src/ tests/

# Run the Flask app (dev mode)
run:
	python src/app.py

# Clean up temp files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache htmlcov .coverage
