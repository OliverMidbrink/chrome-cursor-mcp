import asyncio
import json
import datetime
from typing import Dict, Optional

import websockets


def debug_log(*args):
    pass  # Disabled


class BridgeServer:
    def __init__(self) -> None:
        self.extension_ws: Optional[websockets.WebSocketServerProtocol] = None
        self.controller_clients: set[websockets.WebSocketServerProtocol] = set()
        self.pending_by_id: Dict[str, websockets.WebSocketServerProtocol] = {}

    async def handle_client(self, ws: websockets.WebSocketServerProtocol) -> None:
        client_id = f"{ws.remote_address[0]}:{ws.remote_address[1]}"
        debug_log(f"🔌 NEW CLIENT CONNECTED", {"client_id": client_id, "remote_address": ws.remote_address})
        
        is_extension = False
        self.controller_clients.add(ws)
        debug_log(f"📝 Added to controller_clients", {"client_id": client_id, "total_controller_clients": len(self.controller_clients)})
        
        try:
            debug_log(f"👂 Starting message loop for client", {"client_id": client_id})
            async for raw in ws:
                debug_log(f"📨 RAW MESSAGE RECEIVED", {
                    "client_id": client_id, 
                    "raw_length": len(raw) if raw else 0,
                    "raw_preview": raw[:100] if raw else "empty"
                })
                
                try:
                    msg = json.loads(raw)
                    debug_log(f"📋 MESSAGE PARSED", {"client_id": client_id, "msg": msg})
                except Exception as parse_error:
                    debug_log(f"❌ JSON PARSE FAILED", {"client_id": client_id, "error": str(parse_error), "raw": raw})
                    continue

                # Detect extension hello/event stream
                if isinstance(msg, dict) and msg.get("event") == "hello":
                    debug_log(f"👋 EXTENSION HELLO DETECTED", {"client_id": client_id, "msg": msg})
                    # Mark this connection as the extension bridge
                    is_extension = True
                    self.controller_clients.discard(ws)
                    self.extension_ws = ws
                    debug_log(f"✅ EXTENSION REGISTERED", {
                        "client_id": client_id, 
                        "controller_clients_count": len(self.controller_clients),
                        "extension_connected": self.extension_ws is not None
                    })
                    continue

                # Extension streaming logs or other events; ignore
                if isinstance(msg, dict) and msg.get("event"):
                    debug_log(f"📡 Extension event ignored", {"client_id": client_id, "event": msg.get("event")})
                    continue

                # If message has an id and tool, it's a controller request → forward to extension
                if isinstance(msg, dict) and "tool" in msg and "id" in msg:
                    debug_log(f"🛠️ CONTROLLER REQUEST", {"client_id": client_id, "tool": msg.get("tool"), "id": msg.get("id")})
                    
                    if not self.extension_ws:
                        error_response = {"id": msg.get("id"), "ok": False, "error": "extension not connected"}
                        debug_log(f"❌ NO EXTENSION - sending error", {"client_id": client_id, "response": error_response})
                        await self.safe_send(ws, json.dumps(error_response))
                        continue
                    
                    # Record pending mapping for routing the reply
                    req_id = str(msg.get("id"))
                    self.pending_by_id[req_id] = ws
                    debug_log(f"📤 FORWARDING TO EXTENSION", {
                        "req_id": req_id, 
                        "tool": msg.get("tool"),
                        "pending_requests": len(self.pending_by_id)
                    })
                    await self.safe_send(self.extension_ws, json.dumps(msg))
                    continue

                # Otherwise, if message has an id and ok, it's a reply from the extension → route back
                if isinstance(msg, dict) and "id" in msg and ("ok" in msg or "error" in msg):
                    req_id = str(msg.get("id"))
                    target = self.pending_by_id.pop(req_id, None)
                    debug_log(f"📬 EXTENSION RESPONSE", {
                        "req_id": req_id,
                        "has_target": target is not None,
                        "ok": msg.get("ok"),
                        "error": msg.get("error"),
                        "pending_requests_remaining": len(self.pending_by_id)
                    })
                    
                    if target:
                        debug_log(f"📤 ROUTING RESPONSE BACK", {"req_id": req_id})
                        await self.safe_send(target, json.dumps(msg))
                    else:
                        debug_log(f"⚠️ TARGET CLIENT GONE", {"req_id": req_id})
                    continue

                # Fallback: ignore unknown shapes
                debug_log(f"❓ UNKNOWN MESSAGE SHAPE", {"client_id": client_id, "msg": msg})
                
        except Exception as loop_error:
            debug_log(f"💥 MESSAGE LOOP ERROR", {"client_id": client_id, "error": str(loop_error)})
        finally:
            debug_log(f"🔌 CLIENT DISCONNECTING", {"client_id": client_id, "was_extension": is_extension})
            if is_extension and self.extension_ws is ws:
                self.extension_ws = None
                debug_log(f"📵 EXTENSION DISCONNECTED")
            self.controller_clients.discard(ws)
            # Drop any pending requests for this ws
            to_drop = [rid for rid, client in self.pending_by_id.items() if client is ws]
            for rid in to_drop:
                self.pending_by_id.pop(rid, None)
            debug_log(f"🧹 CLEANUP COMPLETE", {
                "client_id": client_id, 
                "dropped_pending_requests": len(to_drop),
                "remaining_controller_clients": len(self.controller_clients)
            })

    async def safe_send(self, ws: websockets.WebSocketServerProtocol, data: str) -> None:
        try:
            debug_log(f"📤 SENDING DATA", {"data_length": len(data), "data_preview": data[:100]})
            await ws.send(data)
            debug_log(f"✅ DATA SENT SUCCESSFULLY")
        except Exception as send_error:
            debug_log(f"❌ SEND FAILED", {"error": str(send_error)})


async def main(host: str = "127.0.0.1", port: int = 6385) -> None:
    debug_log(f"🚀 STARTING BRIDGE SERVER", {"host": host, "port": port})
    server = BridgeServer()
    
    try:
        async with websockets.serve(server.handle_client, host, port):
            debug_log(f"✅ BRIDGE SERVER LISTENING", {"host": host, "port": port})
            # Keep running forever
            await asyncio.Future()
    except Exception as server_error:
        debug_log(f"💥 BRIDGE SERVER ERROR", {"error": str(server_error)})


if __name__ == "__main__":
    asyncio.run(main())


