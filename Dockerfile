FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
ENV PATH="/srv/.venv/bin:$PATH" DB_PATH=/tmp/citecheck.db PORT=8000
EXPOSE 8000
# Render and Hugging Face pass the port in $PORT.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
