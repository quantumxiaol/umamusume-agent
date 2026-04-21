import os

import uvicorn

from umamusume_agent.server.dialogue_server import app


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
