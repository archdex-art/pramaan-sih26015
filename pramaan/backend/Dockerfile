FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libpq-dev gcc curl \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml ./
RUN uv sync --no-install-project

COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
