# Quick Start with UV

## Setup with UV (Recommended)

```bash
# 1. Install uv (see https://docs.astral.sh/uv/getting-started/installation/ for package-manager/verified install)

# 2. Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Linux/Mac
# .venv\Scripts\activate   # On Windows

# 3. Sync environment dependencies (managed with uv)
uv sync

# 4. Copy and configure environment variables (owner-only permissions)
cp .env.example .env
chmod 600 .env
nano .env  # Edit with your credentials

# 5. Make sure Ollama is running with a model (/api/chat default)
ollama pull llama3.2
ollama serve  # In a separate terminal

# 6. Run the bot + Waitress Web UI together
uv run run.py
# Open http://127.0.0.1:5000
# Visit /setup from localhost to create your UI login (minimum 12-character password)

# 7. (Alternative) Run headless backend only
uv run main.py
```

## Why UV?

- ⚡ **10-100x faster** than pip for installing packages
- 🎯 **Single tool** for venv, pip, and dependency management
- 📦 **Modern** uses `pyproject.toml` and `uv.lock`
- 🔒 **Better dependency resolution**
- 🚀 **Instant** virtual environment creation

## UV Commands Cheat Sheet

```bash
# Sync dependencies from pyproject.toml / uv.lock
uv sync

# Add a dependency
uv add package-name

# Run backend + Waitress UI without activating venv
uv run run.py

# Run test and static analysis suite
uv run pytest -q
uv run ruff check src tests *.py
uv run mypy src/ical_bot
node --test tests/js/test_frontend_modules.mjs
```

## Traditional Setup (Alternative)

If you prefer the traditional approach:

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```
