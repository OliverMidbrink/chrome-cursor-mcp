import asyncio
import json
import sys
import uuid
from pathlib import Path

import websockets


ROOT = Path(__file__).parent
# Ensure we import BridgeServer from server/src
SERVER_SRC = ROOT / "server" / "src"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from chrome_mcp.bridge import BridgeServer  # noqa: E402


HOST = "127.0.0.1"
PORT = 6385


async def send_controller_request(tool: str, args: dict) -> dict:
    async with websockets.connect(f"ws://{HOST}:{PORT}") as ws:
        req_id = str(uuid.uuid4())
        await ws.send(json.dumps({"id": req_id, "tool": tool, "args": args}))
        raw = await ws.recv()
        try:
            return json.loads(raw)
        except Exception as e:  # pragma: no cover
            return {"ok": False, "error": f"invalid json: {e}", "raw": raw}


async def main() -> None:
    server = BridgeServer()
    print(f"[bridge_test] Starting bridge on {HOST}:{PORT} ...")
    async with websockets.serve(server.handle_client, HOST, PORT):
        # Wait for the extension to connect and send hello
        print("[bridge_test] Waiting for extension connection (reload the extension now)...")
        for i in range(600):  # ~60s
            if server.extension_ws is not None:
                break
            await asyncio.sleep(0.1)

        if server.extension_ws is None:
            print("[bridge_test] ERROR: Extension did not connect within 60s. Ensure it is loaded and points to ws://127.0.0.1:6385, then rerun.")
            return
        print("[bridge_test] Extension connected.")

        # 1) List all open tabs
        print("[bridge_test] → get_all_open_tabs()")
        tabs_res = await send_controller_request("get_all_open_tabs", {})
        print("[bridge_test] get_all_open_tabs result:", json.dumps(tabs_res, indent=2))

        # 2) Navigate to example.com in the active tab (or create one)
        print("[bridge_test] → navigate('https://example.com')")
        nav_res = await send_controller_request("navigate", {"url": "https://example.com"})
        print("[bridge_test] navigate result:", json.dumps(nav_res, indent=2))

        # 3) Show active tab after navigation
        print("[bridge_test] → active_tab()")
        active_res = await send_controller_request("active_tab", {})
        print("[bridge_test] active_tab result:", json.dumps(active_res, indent=2))

        print("[bridge_test] Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


