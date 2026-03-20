#!/bin/bash

# Quick setup script for iCal Bot

echo "🤖 iCal Bot Setup Script"
echo "========================"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi
echo "✓ Python 3 found"

# Check if Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "⚠️  Ollama not found. Installing..."
    curl -fsSL https://ollama.ai/install.sh | sh
else
    echo "✓ Ollama found"
fi

# Check if Ollama is running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  Ollama is not running. Starting Ollama in background..."
    ollama serve > /dev/null 2>&1 &
    sleep 3
fi
echo "✓ Ollama is running"

# Pull LLM model if needed
echo "Checking for llama3.2 model..."
if ! ollama list | grep -q "llama3.2"; then
    echo "📥 Pulling llama3.2 model (this may take a few minutes)..."
    ollama pull llama3.2
else
    echo "✓ llama3.2 model found"
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip install -q -e .
echo "✓ Dependencies installed"

# Setup .env file
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo ""
    echo "⚠️  IMPORTANT: Edit the .env file with your email credentials!"
    echo "   nano .env  (or use your preferred editor)"
    echo ""
else
    echo "✓ .env file already exists"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your email credentials"
echo "2. For Gmail: Create an App Password at https://myaccount.google.com/apppasswords"
echo "3. Run the bot: python main.py"
echo ""
echo "For more details, see README.md"
