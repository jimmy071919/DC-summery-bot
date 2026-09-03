FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py README.md .env.example ./

ENV DATABASE_PATH=/app/data/summary.db
CMD ["uv", "run", "--no-sync", "main.py"]
