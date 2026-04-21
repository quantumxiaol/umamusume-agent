---
title: Umamusume Agent
emoji: 🐎
colorFrom: pink
colorTo: blue
sdk: docker
app_port: 7860
short_description: FastAPI backend for Umamusume roleplay chat, deployed on Hugging Face Spaces.
---

# Umamusume Agent

This Hugging Face Space runs the FastAPI backend for the Umamusume roleplay chat app.

## Runtime

- Docker Space
- App port: `7860`
- Entry point: [app.py](/app.py)
- Startup script: [docker-entrypoint.sh](/docker-entrypoint.sh)

## Exposed API

After the Space starts successfully, it serves these HTTP endpoints:

- `GET /`
- `GET /characters`
- `POST /load_character`
- `POST /chat`
- `POST /chat_stream`
- `GET /audio`
- `HEAD /audio`

## Configuration

At startup, the container copies `.env.template` to `.env`, then replaces these placeholders using Space Secrets:

- `ROLEPLAY_LLM_MODEL_NAME`
- `ROLEPLAY_LLM_MODEL_BASE_URL`
- `ROLEPLAY_LLM_MODEL_API_KEY`

If any of them is missing, startup fails fast.

Optional protection settings:

- `API_ACCESS_KEY`
- `API_RATE_LIMIT_ENABLED`
- `API_RATE_LIMIT_WINDOW_SECONDS`
- `API_RATE_LIMIT_MAX_REQUESTS`
- `API_CHAT_RATE_LIMIT_MAX_REQUESTS`

If `API_ACCESS_KEY` is set, all endpoints except `/` and `/audio` require `X-API-Key`.

The backend also applies in-memory rate limits by client IP. Chat endpoints use a stricter bucket than general API calls.

## Frontend Integration

The static frontend can be deployed separately on GitHub Pages and call this Space over HTTPS.

Current frontend target:

- `https://quantumxiaol-umamusume-agent.hf.space`

Note: if you embed `VITE_API_ACCESS_KEY` into a GitHub Pages frontend, it is visible to users. This only works as a light abuse barrier, not a private secret.

More project notes remain in [readme.md](/readme.md).
