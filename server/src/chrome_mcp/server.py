import argparse
import json
from dataclasses import dataclass
from typing import Optional
import websockets
from mcp.server.fastmcp import FastMCP
from openai import OpenAI
from pathlib import Path
import base64
import os

app = FastMCP("chrome-mcp")

@dataclass
class BridgeConfig:
    ws_url: str

state: BridgeConfig = BridgeConfig(ws_url="ws://127.0.0.1:6385")

async def _send(tool: str, args: dict) -> dict:
    async with websockets.connect(state.ws_url) as conn:
        req = {"id": "1", "tool": tool, "args": args}
        await conn.send(json.dumps(req))
        msg = await conn.recv()
        try:
            data = json.loads(msg)
        except Exception as e:
            return {"ok": False, "error": f"invalid json: {e}"}
        return data

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
async def create_tab(url: str) -> dict:
    """Create a new tab with the specified URL"""
    return await _send("create_tab", {"url": url})

@app.tool()
async def screenshot() -> dict:
    """Capture a screenshot via the Chrome extension and persist it to disk for later analysis.

    Saves to path from env var CHROME_MCP_ARTIFACT (default: ~/jobb/övrigt/chrome-mcp/artifacts/screenshot.jpg).
    Returns both the saved file path and the original dataUrl when available.
    """
    res = await _send("screenshot", {})
    # Expecting { ok: true, dataUrl: "data:image/...;base64,XXXXX" }
    data_url = res.get("dataUrl") if isinstance(res, dict) else None
    if not data_url:
        return {"ok": False, "error": "no dataUrl from extension", **(res if isinstance(res, dict) else {})}

    # Determine output path
    default_path = Path.home() / "jobb/övrigt/chrome-mcp/artifacts/screenshot.jpg"
    out_path = Path(os.environ.get("CHROME_MCP_ARTIFACT", str(default_path)))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Decode base64 from data URL
    try:
        comma_index = data_url.find(",")
        b64 = data_url[comma_index + 1 :] if comma_index != -1 else data_url
        raw = base64.b64decode(b64)
        out_path.write_bytes(raw)
    except Exception as e:
        return {"ok": False, "error": f"failed to write screenshot: {e}"}

    return {"ok": True, "path": str(out_path), "dataUrl": data_url}

@app.tool()
async def console_logs() -> dict:
    return await _send("console_logs", {})

@app.tool()
async def evaluate_js(expression: str) -> dict:
    return await _send("evaluate_js", {"expression": expression})

@app.tool()
async def analyze_screenshot(prompt: str) -> dict:
    image_path = Path(os.environ.get("CHROME_MCP_ARTIFACT", str(Path.home() / "jobb/övrigt/chrome-mcp/artifacts/screenshot.jpg")))
    if not image_path.exists():
        return {"ok": False, "error": "no screenshot found; run screenshot() first"}
    api_key = None
    key_file = Path.home() / "jobb/övrigt/chrome-mcp/open-ai-key.txt"
    if key_file.exists():
        api_key = key_file.read_text().strip()
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"ok": False, "error": "missing OPENAI_API_KEY or open-ai-key.txt"}
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
    # Fixed WebSocket URL - no configuration needed
    app.run()

if __name__ == "__main__":
    main()
