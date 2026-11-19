# Minimal production-ish container for iCal Bot + Flask UI
# Uses a non-root user and installs only runtime deps
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (add build-essential if native deps are introduced later)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies directly from pyproject constraints + production WSGI server
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "requests>=2.31.0" \
        "python-dotenv>=1.0.0" \
        "flask>=3.0.0" \
        "exchangelib>=5.0.0" \
        "pypdf>=4.2.0" \
        "defusedxml>=0.7.1" \
        "gunicorn>=21.2.0"

# Add a non-root user
RUN useradd -ms /bin/bash appuser

# Copy application code
COPY . /app
RUN mkdir -p /app/data && chown -R appuser:appuser /app && chmod 700 /app/data

USER appuser

EXPOSE 5000
ENV WEB_UI_HOST=0.0.0.0

# Single worker preserves in-memory mailbox credentials across threads
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:5000", "web_ui:app"]
