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
    """Send a request to the bridge with timeout protection."""
    req_id = str(uuid.uuid4())
    print(f"🔧 [CONTROLLER] Starting request", {"tool": tool, "args": args, "req_id": req_id, "timeout": timeout})
    
    try:
        print(f"🔌 [CONTROLLER] Connecting to bridge at ws://{HOST}:{PORT}")
        ws = await asyncio.wait_for(
            websockets.connect(f"ws://{HOST}:{PORT}"), timeout=5
        )
        print(f"✅ [CONTROLLER] Connected successfully")
        
        try:
            request_msg = {"id": req_id, "tool": tool, "args": args}
            print(f"📤 [CONTROLLER] Sending request", {"message": request_msg})
            
            await asyncio.wait_for(
                ws.send(json.dumps(request_msg)),
                timeout=5
            )
            print(f"✅ [CONTROLLER] Request sent, waiting for response...")
            
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            print(f"📨 [CONTROLLER] Raw response received", {"length": len(raw), "raw": raw})
            
            response = json.loads(raw)
            print(f"📋 [CONTROLLER] Response parsed", {"response": response})
            return response
        finally:
            await ws.close()
            
    except asyncio.TimeoutError as e:
        error_msg = f"timeout after {timeout}s"
        print(f"⏰ [CONTROLLER] Timeout error", {"error": error_msg, "req_id": req_id})
        return {"ok": False, "error": error_msg}
    except Exception as e:
        error_msg = str(e)
        print(f"❌ [CONTROLLER] Request failed", {"error": error_msg, "req_id": req_id, "type": type(e).__name__})
        return {"ok": False, "error": error_msg}


async def wait_for_bridge_and_extension(bridge_server: BridgeServer, timeout_s: int = 30) -> bool:
    """Wait for both bridge to start accepting connections and extension to connect."""
    print(f"🕐 [CONTROLLER] Waiting up to {timeout_s}s for bridge + extension connection...")
    
    deadline = asyncio.get_event_loop().time() + timeout_s
    bridge_ready = False
    
    # First wait for bridge to accept connections
    print(f"🔍 [CONTROLLER] Testing bridge connectivity...")
    while asyncio.get_event_loop().time() < deadline and not bridge_ready:
        try:
            print(f"🔌 [CONTROLLER] Attempting bridge connection test...")
            ws = await asyncio.wait_for(
                websockets.connect(f"ws://{HOST}:{PORT}"), timeout=2
            )
            print(f"🧪 [CONTROLLER] Sending test message...")
            await ws.send("{}")  # Test connection
            await ws.close()
            bridge_ready = True
            print("✅ [CONTROLLER] Bridge accepting connections")
            break
        except Exception as e:
            print(f"❌ [CONTROLLER] Bridge connection test failed", {"error": str(e)})
            await asyncio.sleep(0.5)
    
    if not bridge_ready:
        print(f"💥 [CONTROLLER] Bridge not accepting connections after {timeout_s}s")
        return False
    
    # Then wait for extension to connect and send hello
    print(f"🕐 [CONTROLLER] Waiting for extension to connect and send hello...")
    extension_deadline = asyncio.get_event_loop().time() + (timeout_s - 5)  # Reserve some time
    check_count = 0
    while asyncio.get_event_loop().time() < extension_deadline:
        check_count += 1
        extension_connected = bridge_server.extension_ws is not None
        print(f"🔍 [CONTROLLER] Extension check #{check_count}", {
            "extension_connected": extension_connected,
            "controller_clients": len(bridge_server.controller_clients),
            "pending_requests": len(bridge_server.pending_by_id)
        })
        
        if extension_connected:
            print("✅ [CONTROLLER] Extension connected and ready")
            return True
        await asyncio.sleep(0.2)
    
    print(f"💥 [CONTROLLER] Extension did not connect within {timeout_s}s")
    print("⚠️  [CONTROLLER] Please ensure Chrome extension is loaded and refresh it")
    print(f"📊 [CONTROLLER] Final state: controller_clients={len(bridge_server.controller_clients)}, extension_ws={bridge_server.extension_ws is not None}")
    return False


async def test_bridge_and_extension() -> None:
    """Test bridge server with extension connection and tool execution."""
    print(f"🚀 [CONTROLLER] Initializing bridge server...")
    bridge_server = BridgeServer()
    
    print(f"🌐 [CONTROLLER] Starting bridge server on {HOST}:{PORT}...")
    
    try:
        async with websockets.serve(bridge_server.handle_client, HOST, PORT):
            print(f"✅ [CONTROLLER] Bridge server started successfully")
            
            # Wait for both bridge and extension to be ready
            if not await wait_for_bridge_and_extension(bridge_server, timeout_s=30):
                print("💥 [CONTROLLER] Failed to establish bridge + extension connection")
                return
            
            print("🎉 [CONTROLLER] Bridge and extension ready! Running tests...\n")
            
            # Test 1: Get all open tabs
            print("🧪 [CONTROLLER] TEST 1: get_all_open_tabs()")
            tabs_res = await request("get_all_open_tabs", {}, timeout=10)
            print(f"📊 [CONTROLLER] get_all_open_tabs result: {json.dumps(tabs_res, indent=2)}\n")
            
            # Test 2: Navigate to example.com
            print("🧪 [CONTROLLER] TEST 2: navigate('https://example.com')")
            nav_res = await request("navigate", {"url": "https://example.com"}, timeout=15)
            print(f"📊 [CONTROLLER] navigate result: {json.dumps(nav_res, indent=2)}\n")
            
            # Test 3: Get active tab after navigation
            print("🧪 [CONTROLLER] TEST 3: active_tab()")
            active_res = await request("active_tab", {}, timeout=10)
            print(f"📊 [CONTROLLER] active_tab result: {json.dumps(active_res, indent=2)}\n")
            
            # Test 4: Take a screenshot
            print("🧪 [CONTROLLER] TEST 4: screenshot()")
            screenshot_res = await request("screenshot", {}, timeout=10)
            if screenshot_res.get("ok"):
                print("📊 [CONTROLLER] screenshot result: ✓ Success (dataUrl received)")
            else:
                print(f"📊 [CONTROLLER] screenshot result: {json.dumps(screenshot_res, indent=2)}")
            
            print("\n🎉 [CONTROLLER] All tests completed!")
            
    except Exception as e:
        print(f"💥 [CONTROLLER] Bridge server error: {e}")


async def main() -> None:
    await test_bridge_and_extension()


if __name__ == "__main__":
    asyncio.run(main())


