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


async def test_full_functionality() -> None:
    """Test bridge server with comprehensive tab management."""
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
            
            # Test 1: Check all tabs
            print("🧪 [CONTROLLER] TEST 1: get_all_open_tabs()")
            tabs_res = await request("get_all_open_tabs", {}, timeout=10)
            if tabs_res.get("tabs"):
                active_tabs = [t for t in tabs_res["tabs"] if t.get("active")]
                print(f"📊 [ACTIVE TABS] {len(active_tabs)} active: {[{'id': t['id'], 'url': t['url']} for t in active_tabs]}")
                print(f"📊 [TOTAL] {len(tabs_res['tabs'])} tabs total\n")
            else:
                print("❌ Failed to get tabs\n")
            
            # Test 2: Create new tab with example.com (first time) - ATOMIC
            first_tab_id = None
            try:
                print("🧪 [CONTROLLER] TEST 2: create_tab('https://example.com') - first time")
                tab_res1 = await request("create_tab", {"url": "https://example.com"}, timeout=15)
                first_tab_id = tab_res1.get("tabId")
                print(f"📊 [CONTROLLER] create_tab result 1: {json.dumps(tab_res1, indent=2)}")
                
                # VERIFY: Check actual tabs after creation
                verify_res = await request("get_all_open_tabs", {}, timeout=10)
                if verify_res.get("tabs"):
                    active_tabs = [t for t in verify_res["tabs"] if t.get("active")]
                    print(f"📊 [ACTIVE TABS] {len(active_tabs)} active: {[{'id': t['id'], 'url': t['url']} for t in active_tabs]}")
                    print(f"📊 [TOTAL] {len(verify_res['tabs'])} tabs total\n")
                else:
                    print("❌ [VERIFY] Could not get tabs after creation 1\n")
            except Exception as e:
                print(f"❌ [CONTROLLER] TEST 2 FAILED: {e}\n")
            
            # Test 3: Create new tab with example.com (second time) - ATOMIC
            second_tab_id = None
            try:
                print("🧪 [CONTROLLER] TEST 3: create_tab('https://example.com') - second time")
                tab_res2 = await request("create_tab", {"url": "https://example.com"}, timeout=15)
                second_tab_id = tab_res2.get("tabId")
                print(f"📊 [CONTROLLER] create_tab result 2: {json.dumps(tab_res2, indent=2)}")
                
                # VERIFY: Check actual tabs after creation
                verify_res = await request("get_all_open_tabs", {}, timeout=10)
                if verify_res.get("tabs"):
                    print(f"🔍 [VERIFY] Actual tabs after creation 2: {[{'id': t['id'], 'url': t['url']} for t in verify_res['tabs']]}\n")
                else:
                    print("❌ [VERIFY] Could not get tabs after creation 2\n")
            except Exception as e:
                print(f"❌ [CONTROLLER] TEST 3 FAILED: {e}\n")
            
            # Test 4: Get info for latest example.com tab (use second_tab_id) - ATOMIC
            try:
                if second_tab_id:
                    print(f"🧪 [CONTROLLER] TEST 4: get_tab_info(tabId={second_tab_id})")
                    tab_info_res = await request("get_tab_info", {"tabId": second_tab_id}, timeout=10)
                    print(f"📊 [CONTROLLER] tab_info result: {json.dumps(tab_info_res, indent=2)}\n")
                else:
                    print("⚠️ [CONTROLLER] TEST 4 SKIPPED: No second tab ID available\n")
            except Exception as e:
                print(f"❌ [CONTROLLER] TEST 4 FAILED: {e}\n")
            
            # Test 5: Navigate specific tab to google.com (use second_tab_id) - ATOMIC
            try:
                if second_tab_id:
                    print(f"🧪 [CONTROLLER] TEST 5: navigate(tabId={second_tab_id}, url='https://google.com')")
                    nav_google_res = await request("navigate", {"tabId": second_tab_id, "url": "https://google.com"}, timeout=15)
                    print(f"📊 [CONTROLLER] navigate to google result: {json.dumps(nav_google_res, indent=2)}")
                    
                    # VERIFY: Check actual tabs after navigation
                    verify_res = await request("get_all_open_tabs", {}, timeout=10)
                    if verify_res.get("tabs"):
                        print(f"🔍 [VERIFY] Actual tabs after navigation: {[{'id': t['id'], 'url': t['url']} for t in verify_res['tabs']]}\n")
                    else:
                        print("❌ [VERIFY] Could not get tabs after navigation\n")
                else:
                    print("⚠️ [CONTROLLER] TEST 5 SKIPPED: No second tab ID available\n")
            except Exception as e:
                print(f"❌ [CONTROLLER] TEST 5 FAILED: {e}\n")
            
            # Test 6: Final tab check - VERIFY TABS ACTUALLY EXIST
            print("🧪 [CONTROLLER] TEST 6: get_all_open_tabs() - final state")
            final_tabs_res = await request("get_all_open_tabs", {}, timeout=10)
            print(f"📊 [CONTROLLER] final tabs result: {json.dumps(final_tabs_res, indent=2)}\n")
            
            # VERIFICATION: Count example.com and google.com tabs
            if final_tabs_res.get("ok") and "tabs" in final_tabs_res:
                tabs = final_tabs_res["tabs"]
                example_tabs = [t for t in tabs if "example.com" in t.get("url", "")]
                google_tabs = [t for t in tabs if "google.com" in t.get("url", "")]
                
                print(f"🔍 [VERIFICATION] Found {len(example_tabs)} example.com tabs")
                print(f"🔍 [VERIFICATION] Found {len(google_tabs)} google.com tabs")
                
                if len(example_tabs) >= 1 and len(google_tabs) >= 1:
                    print("✅ [VERIFICATION] SUCCESS: Both example.com and google.com tabs exist!")
                else:
                    print("❌ [VERIFICATION] FAILED: Expected tabs not found in browser!")
                    print(f"📋 [VERIFICATION] All tab URLs: {[t.get('url') for t in tabs]}")
            else:
                print("❌ [VERIFICATION] FAILED: Could not get tabs list!")
            
            print("\n🎉 [CONTROLLER] All tests completed!")
            
    except Exception as e:
        print(f"💥 [CONTROLLER] Bridge server error: {e}")


async def main() -> None:
    await test_full_functionality()


if __name__ == "__main__":
    asyncio.run(main())
