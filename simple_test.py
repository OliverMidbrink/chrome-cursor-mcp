import asyncio
import json
import sys
import uuid
from pathlib import Path
import websockets

# Ensure we import BridgeServer from server/src
ROOT = Path(__file__).parent
SERVER_SRC = ROOT / "server" / "src"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from chrome_mcp.bridge import BridgeServer

HOST = "127.0.0.1"
PORT = 6385

async def request(tool: str, args: dict, timeout: int = 10) -> dict:
    req_id = str(uuid.uuid4())
    ws = await asyncio.wait_for(websockets.connect(f"ws://{HOST}:{PORT}"), timeout=5)
    try:
        request_msg = {"id": req_id, "tool": tool, "args": args}
        await asyncio.wait_for(ws.send(json.dumps(request_msg)), timeout=5)
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        response = json.loads(raw)
        return response
    finally:
        await ws.close()

async def show_tabs():
    tabs_res = await request("get_all_open_tabs", {}, timeout=10)
    if tabs_res.get("tabs"):
        urls = [t['url'] for t in tabs_res["tabs"]]
        for url in urls:
            print(url)
        return len(urls)
    else:
        print("Failed to get tabs")
        return 0

async def main():
    bridge_server = BridgeServer()
    
    async with websockets.serve(bridge_server.handle_client, HOST, PORT):
        await asyncio.sleep(2)
        
        print("=== BEFORE ===")
        before_count = await show_tabs()
        
        print("\n=== CREATING TAB ===")
        create_res = await request("create_tab", {"url": "https://example.com"}, timeout=15)
        
        print("\n=== AFTER ===")
        after_count = await show_tabs()
        
        print(f"\nCOUNT: {before_count} → {after_count}")

if __name__ == "__main__":
    asyncio.run(main())
