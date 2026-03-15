# syntax=docker/dockerfile:1.3
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.6.4 /uv /uvx /bin/

# Install system dependencies
RUN apt-get update && apt-get install -y \
    htop \
    vim \
    curl \
    tar \
    python3-dev \
    build-essential \
    gcc \
    cmake \
    netcat-openbsd \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install tctl (Temporal CLI)
RUN curl -L https://github.com/temporalio/tctl/releases/download/v1.18.1/tctl_1.18.1_linux_arm64.tar.gz -o /tmp/tctl.tar.gz && \
    tar -xzf /tmp/tctl.tar.gz -C /usr/local/bin && \
    chmod +x /usr/local/bin/tctl && \
    rm /tmp/tctl.tar.gz

RUN pip install --upgrade pip setuptools wheel

ENV UV_HTTP_TIMEOUT=1000

# Copy pyproject.toml and README.md to install dependencies
COPY pyproject.toml /app/live-shop/pyproject.toml
COPY README.md /app/live-shop/README.md

WORKDIR /app/live-shop

# Copy the project code
COPY project /app/live-shop/project
COPY db /app/live-shop/db
COPY stream /app/live-shop/stream

# Install all packages in one command so pip resolves compatible versions together.
# google-adk pins its own google-genai version — splitting installs causes downgrade conflicts.
# Note: agentex-sdk (not agentex) — provides agentex.lib.*
RUN pip install --no-cache-dir \
    agentex-sdk \
    temporalio \
    "google-adk>=1.0.0" \
    "google-genai>=1.0.0" \
    google-cloud-firestore \
    google-auth \
    python-dotenv \
    termcolor \
    websockets \
    httpx \
    uvicorn

# Verify critical imports at build time
RUN python -c "from google.adk import Agent, Runner; print('google.adk OK')"
RUN python -c "from agentex.lib.utils.logging import make_logger; print('agentex.lib OK')"

WORKDIR /app/live-shop

ENV PYTHONPATH=/app

# Set agent environment variables
ENV AGENT_NAME=live-shop-agent

# Run the ACP server using uvicorn
CMD ["uvicorn", "project.acp:acp", "--host", "0.0.0.0", "--port", "8000"]

# When we deploy the worker, we will replace the CMD with the following
# CMD ["python", "-m", "project.run_worker"]