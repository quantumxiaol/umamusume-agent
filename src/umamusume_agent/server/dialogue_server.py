"""Stable Uvicorn/Hugging Face entry point; implementation lives in app.py."""
from .app import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=1111)
