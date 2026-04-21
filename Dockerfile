FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY characters ./characters
COPY resources ./resources
COPY umamusume_characters.json ./
COPY .env.template ./
COPY app.py ./
COPY docker-entrypoint.sh ./

RUN uv sync --frozen --no-dev
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 7860

CMD ["./docker-entrypoint.sh"]
