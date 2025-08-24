import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Optional, Dict

import websockets
from mcp.server.fastmcp import FastMCP
from openai import OpenAI
from pathlib import Path
import base64
import os
import threading
import uuid
import logging


app = FastMCP("chrome-mcp")


@dataclass
class BridgeConfig:
    ws_url: str


state: BridgeConfig = BridgeConfig(ws_url="ws://127.0.0.1:6385")





async def _send(tool: str, args: dict) -> dict:
    async with websockets.connect(state.ws_url) as conn:
        req_id = str(uuid.uuid4())
        req = {"id": req_id, "tool": tool, "args": args}
        await conn.send(json.dumps(req))
        msg = await conn.recv()
        try:
            data = json.loads(msg)
        except Exception as e:
            return {"ok": False, "error": f"invalid json: {e}"}
        return data


# Embedded lightweight WebSocket bridge so the Chrome extension can connect to this MCP process
class _BridgeServer:
    def __init__(self) -> None:
        self.extension_ws: Optional[websockets.WebSocketServerProtocol] = None
        self.controller_clients: set[websockets.WebSocketServerProtocol] = set()
        self.pending_by_id: Dict[str, websockets.WebSocketServerProtocol] = {}

    async def handle_client(self, ws: websockets.WebSocketServerProtocol) -> None:
        is_extension = False
        self.controller_clients.add(ws)
        logging.info("bridge: client connected; total_clients=%d", len(self.controller_clients))
        try:
            async for raw in ws:
                logging.debug("bridge: recv raw=%s", raw)
                try:
                    msg = json.loads(raw)
                except Exception:
                    logging.warning("bridge: non-json message ignored")
                    continue

                if isinstance(msg, dict) and msg.get("event") == "hello":
                    is_extension = True
                    self.controller_clients.discard(ws)
                    self.extension_ws = ws
                    logging.info("bridge: extension hello; extension connected=%s", bool(self.extension_ws))
                    continue

                if isinstance(msg, dict) and msg.get("event"):
                    # stream events (e.g., console logs) are ignored here
                    logging.debug("bridge: event from extension ignored: %s", msg.get("event"))
                    continue

                if isinstance(msg, dict) and "tool" in msg and "id" in msg:
                    logging.info("bridge: controller -> extension tool=%s id=%s", msg.get("tool"), msg.get("id"))
                    # websockets v12 ServerConnection does not have .closed attr; rely on reference presence
                    if not self.extension_ws:
                        await self._safe_send(ws, json.dumps({"id": msg.get("id"), "ok": False, "error": "extension not connected"}))
                        continue
                    req_id = str(msg.get("id"))
                    self.pending_by_id[req_id] = ws
                    await self._safe_send(self.extension_ws, json.dumps(msg))
                    continue

                if isinstance(msg, dict) and "id" in msg and ("ok" in msg or "error" in msg):
                    req_id = str(msg.get("id"))
                    target = self.pending_by_id.pop(req_id, None)
                    if target and not target.closed:
                        logging.info("bridge: extension -> controller reply id=%s ok=%s", req_id, msg.get("ok"))
                        await self._safe_send(target, json.dumps(msg))
                    continue
        finally:
            logging.info("bridge: client disconnected")
            if is_extension and self.extension_ws is ws:
                self.extension_ws = None
            self.controller_clients.discard(ws)
            to_drop = [rid for rid, client in self.pending_by_id.items() if client is ws]
            for rid in to_drop:
                self.pending_by_id.pop(rid, None)

    async def _safe_send(self, ws: websockets.WebSocketServerProtocol, data: str) -> None:
        try:
            await ws.send(data)
        except Exception:
            pass


def _start_bridge_in_background(host: str = "127.0.0.1", port: int = 6385) -> None:
    async def _run() -> None:
        server = _BridgeServer()
        logging.info("bridge: starting on %s:%d", host, port)
        async with websockets.serve(server.handle_client, host, port):
            await asyncio.Future()

    def _thread_target() -> None:
        try:
            asyncio.run(_run())
        except Exception:
            # Silently ignore to avoid crashing the MCP server if port is already in use
            pass

    t = threading.Thread(target=_thread_target, name="chrome-mcp-bridge", daemon=True)
    t.start()


@app.tool()
async def active_tab() -> dict:
    return await _send("active_tab", {})


@app.tool()
async def get_all_open_tabs() -> dict:
    """Get information about all open browser tabs including ID, URL, title, and status."""
    return await _send("get_all_open_tabs", {})


@app.tool()
async def navigate(url: str) -> dict:
    return await _send("navigate", {"url": url})


@app.tool()
async def screenshot() -> dict:
    return await _send("screenshot", {})


@app.tool()
async def console_logs() -> dict:
    return await _send("console_logs", {})


@app.tool()
async def evaluate_js(expression: str) -> dict:
    return await _send("evaluate_js", {"expression": expression})


@app.tool()
async def analyze_screenshot(prompt: str) -> dict:
    """Analyze the latest screenshot with OpenAI VLM. Requires OPENAI_API_KEY and a prior screenshot()."""
    # find last screenshot written by chrome (artifacts/screenshot.jpg)
    image_path = Path("/Users/olivermidbrink/jobb/övrigt/tzafon/Tzafon-WayPoint/artifacts/screenshot.jpg")
    if not image_path.exists():
        return {"ok": False, "error": "no screenshot found; call screenshot() first"}
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY is not set"}
    img_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    client = OpenAI(api_key=api_key)
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
    ]
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": content}],
            temperature=0.2,
        )
        text = resp.choices[0].message.content
        return {"ok": True, "analysis": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    # Start embedded WebSocket bridge for the Chrome extension
    _start_bridge_in_background()
    # Run MCP over stdio
    app.run()


if __name__ == "__main__":
    main()


