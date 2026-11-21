
"""FastAPI app (Team A). Thin wrapper: all logic lives in session.py.

Run: python -m backend.main
Then open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import ROOT, ConfigError, load_config
from .session import Connection, build_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_app(cfg=None, state=None):
    try:
        cfg = cfg or load_config()
    except ConfigError as e:
        print(f"\n{e}\n", file=sys.stderr)
        raise SystemExit(1)

    state = state or build_state(cfg)
    app = FastAPI(title="Voice Field Register")
    app.state.vfr = state

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "mode": cfg.provider,
            "stt": state.stt.name,
            "tts": state.tts.name,
            "llm": state.llm.name,
        }

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()

        async def send(message: dict):
            await websocket.send_json(message)

        conn = Connection(state, send)
        tasks: set[asyncio.Task] = set()

        try:
            while True:
                raw = await websocket.receive_text()

                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError("Message must be a JSON object")
                except ValueError:
                    await conn.error(
                        "bad_json",
                        "Message must be a JSON object.",
                        retryable=False,
                    )
                    continue

                if msg.get("type") == "end_utterance":
                    task = asyncio.create_task(conn.handle(msg))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                else:
                    await conn.handle(msg)

        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app.mount(
        "/",
        StaticFiles(directory=str(ROOT / "frontend"), html=True),
        name="frontend",
    )

    return app


if __name__ == "__main__":
    import uvicorn

    try:
        config = load_config()
        application = create_app(config)
        uvicorn.run(
            application,
            host=config.host,
            port=config.port,
        )
    except ConfigError as e:
        print(f"\n{e}\n", file=sys.stderr)
        raise SystemExit(1)
