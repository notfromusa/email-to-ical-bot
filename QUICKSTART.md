# Quick Start with UV

## Setup with UV (Recommended)

```bash
# 1. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Linux/Mac
# .venv\Scripts\activate   # On Windows

# 3. Install the project
uv pip install -e .

# 4. Copy and configure environment variables
cp .env.example .env
nano .env  # Edit with your credentials

# 5. Make sure Ollama is running with a model
ollama pull llama3.2
ollama serve  # In a separate terminal

# 6. Run the bot
python main.py

# 7. (Optional) Use the Web UI with local auth
# Start the UI
python run.py
# Open http://127.0.0.1:5000
# Visit /setup from localhost to create your UI login
```

## Why UV?

- ⚡ **10-100x faster** than pip for installing packages
- 🎯 **Single tool** for venv, pip, and dependency management
- 📦 **Modern** uses pyproject.toml standard
- 🔒 **Better dependency resolution**
- 🚀 **Instant** virtual environment creation

## UV Commands Cheat Sheet

```bash
# Create venv
uv venv

# Install dependencies
uv pip install -e .

# Add a new dependency
uv pip install package-name
# Then add it to pyproject.toml dependencies list

# Sync dependencies (install from pyproject.toml)
uv pip sync

# Update all packages
uv pip install --upgrade -e .

# Run without activating venv
uv run python main.py
```

## Traditional Setup (Alternative)

If you prefer the traditional approach:

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```
