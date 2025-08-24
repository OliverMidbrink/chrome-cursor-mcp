#!/usr/bin/env python3

"""
MCP Server for Chrome automation
Provides open_tab command via WebSocket connection to Chrome extension
"""

import asyncio
import json
import sys
import logging
from pathlib import Path
import os
import uuid
import base64
import websockets
from typing import Any, Dict, Optional
from mcp.server import NotificationOptions, Server
from mcp.types import Tool
try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore
import mcp.types as types
import mcp.server.stdio

# Set up logging to stderr so it doesn't interfere with MCP protocol, and also mirror to a file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Mirror logs to a file in the project root
try:
    log_file_path = (Path(__file__).resolve().parent / "mcp_server_log.txt")
    file_handler = logging.FileHandler(str(log_file_path))
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(file_handler)
except Exception:
    # Never fail startup due to file logging issues
    pass

# Throttled logging helper to reduce spam
_last_log: dict[str, float] = {}
def log_throttled(key: str, level: str, message: str):
    import time
    now = time.time()
    last = _last_log.get(key, 0)
    if now - last >= 2.0:
        _last_log[key] = now
        if level == "info":
            logger.info(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.error(message)

class ChromeMCPServer:
    def __init__(self):
        self.websocket = None
        self.websocket_server = None
        self.message_id = 0
        self.pending_requests = {}
        self.connected = False
        self.sessions: dict[str, dict[str, Any]] = {}
        self.selected_session_id: Optional[str] = None
        
    async def start_websocket_server(self):
        """Start WebSocket server for Chrome extension connection (with retry)."""
        async def handle_client(websocket):
            logger.info("Chrome extension connected!")
            self.websocket = websocket
            self.connected = True
            
            try:
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        
                        # Handle hello message from extension
                        if data.get('event') == 'hello':
                            logger.info(f"Extension info: {data.get('ua', 'Unknown')}")
                            continue
                            
                        # Handle tool responses
                        if 'id' in data and data['id'] in self.pending_requests:
                            future = self.pending_requests.pop(data['id'])
                            if data.get('ok'):
                                future.set_result(data)
                            else:
                                future.set_exception(Exception(data.get('error', 'Unknown error')))
                                
                    except json.JSONDecodeError as e:
                        logger.error(f"Error parsing WebSocket message: {e}")
                        
            except websockets.exceptions.ConnectionClosed:
                logger.info("Chrome extension disconnected")
            finally:
                self.websocket = None
                self.connected = False
        
        # Start WebSocket server with retry until it binds
        while True:
            if self.websocket_server is not None:
                # Already started
                return
            try:
                self.websocket_server = await websockets.serve(
                    handle_client,
                    "127.0.0.1",
                    6385
                )
                logger.info("WebSocket server started on ws://127.0.0.1:6385")
                logger.info("Waiting for Chrome extension to connect...")
                return
            except OSError as e:
                # Address already in use or similar — retry shortly
                log_throttled("ws-bind", "warning", f"WebSocket bind failed ({e}); retrying in 0.5s")
                await asyncio.sleep(0.5)
            except Exception as e:
                log_throttled("ws-start", "error", f"Failed to start WebSocket server ({e}); retrying in 0.5s")
                await asyncio.sleep(0.5)
    
    async def send_tool_request(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Send a tool request to the Chrome extension"""
        if not self.websocket or not self.connected:
            raise Exception("Chrome extension not connected. Please make sure the Chrome extension is loaded and running.")
        
        self.message_id += 1
        message_id = self.message_id
        
        message = {
            "id": message_id,
            "tool": tool,
            "args": args
        }
        
        # Create future for response
        future = asyncio.Future()
        self.pending_requests[message_id] = future
        
        try:
            # Send message
            await self.websocket.send(json.dumps(message))
            
            # Wait for response with timeout
            response = await asyncio.wait_for(future, timeout=10.0)
            return response
            
        except asyncio.TimeoutError:
            self.pending_requests.pop(message_id, None)
            raise Exception("Request timeout - Chrome extension did not respond")
        except Exception as e:
            self.pending_requests.pop(message_id, None)
            raise Exception(f"Failed to send request: {e}")

    def _ensure_artifacts_dir(self) -> Path:
        artifacts_dir = Path(__file__).resolve().parent / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return artifacts_dir

    def _save_data_url(self, data_url: str) -> Path:
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            raise ValueError("Invalid data URL")
        header, b64 = data_url.split(",", 1)
        ext = "png"
        try:
            mime = header.split(";")[0].split(":")[1]
            if "/" in mime:
                ext = mime.split("/")[-1] or "png"
        except Exception:
            pass
        raw = base64.b64decode(b64)
        file_path = self._ensure_artifacts_dir() / f"{uuid.uuid4()}.{ext}"
        file_path.write_bytes(raw)
        return file_path

# Global instance
chrome_server = ChromeMCPServer()

# Create MCP server
server = Server("cursor-chrome-mcp")

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """List available tools"""
    return [
        Tool(
            name="open_tab",
            description="Open a new tab in Chrome",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to open"
                    },
                    "active": {
                        "type": "boolean",
                        "description": "Whether to make the tab active (default: true)",
                        "default": True
                    }
                },
                "required": ["url"]
            },
        ),
        Tool(
            name="list_tabs",
            description="List all open Chrome tabs",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="navigate_tab",
            description="Navigate a specific tab to a new URL",
            inputSchema={
                "type": "object",
                "properties": {
                    "tabId": {"type": "number"},
                    "url": {"type": "string"},
                    "active": {"type": "boolean", "default": True}
                },
                "required": ["tabId", "url"]
            },
        ),
        Tool(
            name="screenshot_tab",
            description="Capture a screenshot of a specific tab and save it to artifacts",
            inputSchema={
                "type": "object",
                "properties": {"tabId": {"type": "number"}},
                "required": ["tabId"]
            },
        ),
        Tool(
            name="create_session",
            description="Create a session from a URL or an existing tab ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "tabId": {"type": "number"},
                    "active": {"type": "boolean", "default": True}
                }
            },
        ),
        Tool(
            name="select_session",
            description="Select an existing session by ID",
            inputSchema={
                "type": "object",
                "properties": {"sessionId": {"type": "string"}},
                "required": ["sessionId"]
            },
        ),
        Tool(
            name="session_info",
            description="Get info about the currently selected session",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="session_navigate",
            description="Navigate the selected session's tab to a URL",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "active": {"type": "boolean", "default": True}
                },
                "required": ["url"]
            },
        ),
        Tool(
            name="session_screenshot",
            description="Screenshot the selected session's tab and save to artifacts",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="analyze_screenshot",
            description="Analyze an image artifact with OpenAI VLM (gpt-4o-mini)",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "artifactPath": {"type": "string"}
                },
                "required": ["prompt"]
            },
        ),
        Tool(
            name="screenshot_and_analyze",
            description="Capture a screenshot of a tab (by ID) and analyze it with OpenAI VLM",
            inputSchema={
                "type": "object",
                "properties": {
                    "tabId": {"type": "number"},
                    "prompt": {"type": "string"}
                },
                "required": ["tabId", "prompt"]
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Handle tool calls"""
    if name == "open_tab":
        url = arguments.get("url")
        active = arguments.get("active", True)
        
        if not url:
            raise ValueError("URL is required")
        
        try:
            # Send request to Chrome extension
            result = await chrome_server.send_tool_request("open_tab", {
                "url": url,
                "active": active
            })
            
            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully opened tab: {url}\nTab ID: {result.get('tabId')}\nActive: {active}"
                )
            ]
            
        except Exception as e:
            raise Exception(f"Failed to open tab: {str(e)}")
    elif name == "list_tabs":
        try:
            result = await chrome_server.send_tool_request("get_all_open_tabs", {})
            tabs = result.get("tabs", [])
            return [types.TextContent(type="text", text=json.dumps({"count": len(tabs), "tabs": tabs}, indent=2))]
        except Exception as e:
            raise Exception(f"Failed to list tabs: {str(e)}")
    elif name == "navigate_tab":
        tab_id = arguments.get("tabId")
        url = arguments.get("url")
        active = arguments.get("active", True)
        if tab_id is None or not url:
            raise ValueError("tabId and url are required")
        try:
            await chrome_server.send_tool_request("navigate_tab", {"tabId": tab_id, "url": url, "active": active})
            return [types.TextContent(type="text", text=f"Navigated tab {tab_id} to {url}")]
        except Exception as e:
            raise Exception(f"Failed to navigate tab: {str(e)}")
    elif name == "screenshot_tab":
        tab_id = arguments.get("tabId")
        if tab_id is None:
            raise ValueError("tabId is required")
        try:
            result = await chrome_server.send_tool_request("screenshot_tab", {"tabId": tab_id})
            data_url = result.get("dataUrl")
            if not data_url:
                raise Exception(result.get("error", "No dataUrl returned"))
            file_path = chrome_server._save_data_url(data_url)
            return [types.TextContent(type="text", text=f"Saved screenshot to {file_path}")]
        except Exception as e:
            raise Exception(f"Failed to screenshot tab: {str(e)}")
    elif name == "create_session":
        url = arguments.get("url")
        tab_id = arguments.get("tabId")
        active = arguments.get("active", True)
        if not url and tab_id is None:
            raise ValueError("Provide either url or tabId")
        try:
            actual_tab_id: Optional[int] = None
            if url:
                res = await chrome_server.send_tool_request("open_tab", {"url": url, "active": active})
                actual_tab_id = res.get("tabId")
            else:
                actual_tab_id = int(tab_id)
            if actual_tab_id is None:
                raise Exception("Could not resolve tab ID")
            session_id = str(uuid.uuid4())
            chrome_server.sessions[session_id] = {"tab_id": actual_tab_id, "last_artifact": None}
            chrome_server.selected_session_id = session_id
            return [types.TextContent(type="text", text=json.dumps({"sessionId": session_id, "tabId": actual_tab_id}, indent=2))]
        except Exception as e:
            raise Exception(f"Failed to create session: {str(e)}")
    elif name == "select_session":
        session_id = arguments.get("sessionId")
        if not session_id or session_id not in chrome_server.sessions:
            raise ValueError("Unknown sessionId")
        chrome_server.selected_session_id = session_id
        return [types.TextContent(type="text", text=f"Selected session {session_id}")]
    elif name == "session_info":
        sid = chrome_server.selected_session_id
        info = chrome_server.sessions.get(sid, None) if sid else None
        return [types.TextContent(type="text", text=json.dumps({"selectedSessionId": sid, "info": info}, indent=2))]
    elif name == "session_navigate":
        sid = chrome_server.selected_session_id
        if not sid:
            raise ValueError("No session selected")
        sess = chrome_server.sessions.get(sid)
        if not sess:
            raise ValueError("Selected session not found")
        url = arguments.get("url")
        active = arguments.get("active", True)
        if not url:
            raise ValueError("url is required")
        try:
            await chrome_server.send_tool_request("navigate_tab", {"tabId": sess["tab_id"], "url": url, "active": active})
            return [types.TextContent(type="text", text=f"Session {sid}: navigated tab {sess['tab_id']} to {url}")]
        except Exception as e:
            raise Exception(f"Failed to navigate session tab: {str(e)}")
    elif name == "session_screenshot":
        sid = chrome_server.selected_session_id
        if not sid:
            raise ValueError("No session selected")
        sess = chrome_server.sessions.get(sid)
        if not sess:
            raise ValueError("Selected session not found")
        try:
            result = await chrome_server.send_tool_request("screenshot_tab", {"tabId": sess["tab_id"]})
            data_url = result.get("dataUrl")
            if not data_url:
                raise Exception(result.get("error", "No dataUrl returned"))
            file_path = chrome_server._save_data_url(data_url)
            sess["last_artifact"] = str(file_path)
            return [types.TextContent(type="text", text=json.dumps({"artifactPath": str(file_path), "sessionId": sid}, indent=2))]
        except Exception as e:
            raise Exception(f"Failed to screenshot session tab: {str(e)}")
    elif name == "analyze_screenshot":
        prompt = arguments.get("prompt")
        artifact_path = arguments.get("artifactPath")
        if not prompt:
            raise ValueError("prompt is required")
        if not artifact_path:
            sid = chrome_server.selected_session_id
            if sid and sid in chrome_server.sessions:
                artifact_path = chrome_server.sessions[sid].get("last_artifact")
        if not artifact_path:
            raise ValueError("artifactPath not provided and no recent session screenshot available")
        if OpenAI is None:
            raise ValueError("openai package not installed")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        p = Path(str(artifact_path))
        if not p.exists():
            raise ValueError(f"artifact does not exist: {artifact_path}")
        img_b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
        # Use default env-based client
        client = OpenAI()
        image_suffix = p.suffix.lstrip('.') or 'png'
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{image_suffix};base64,{img_b64}"}},
        ]
        try:
            resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": content}], temperature=0.2)
            text = resp.choices[0].message.content
            return [types.TextContent(type="text", text=text)]
        except Exception as e:
            raise Exception(f"OpenAI analysis failed: {str(e)}")
    elif name == "screenshot_and_analyze":
        tab_id = arguments.get("tabId")
        prompt = arguments.get("prompt")
        if tab_id is None or not prompt:
            raise ValueError("tabId and prompt are required")
        try:
            shot = await chrome_server.send_tool_request("screenshot_tab", {"tabId": tab_id})
            data_url = shot.get("dataUrl")
            if not data_url:
                raise Exception(shot.get("error", "No dataUrl returned"))
            img_path = chrome_server._save_data_url(data_url)
            # Reuse analyze_screenshot flow
            return await handle_call_tool("analyze_screenshot", {"prompt": prompt, "artifactPath": str(img_path)})
        except Exception as e:
            raise Exception(f"Failed screenshot_and_analyze: {str(e)}")
    
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    """Main server function with self-healing loop"""
    while True:
        try:
            # Start WebSocket server (retries inside until bound)
            await chrome_server.start_websocket_server()

            # One-time startup smoke test: wait 2s for extension, then open example.com inactive
            try:
                await asyncio.sleep(2.0)
                if chrome_server.connected:
                    try:
                        resp = await chrome_server.send_tool_request("open_tab", {"url": "https://example.com", "active": False})
                        logger.info(f"Startup test: opened example.com (inactive). Response: {resp}")
                    except Exception as e:
                        logger.warning(f"Startup test failed: {e}")
                else:
                    logger.warning("Startup test skipped: Chrome extension not connected")
            except Exception as e:
                logger.warning(f"Startup test error: {e}")

            # Run MCP server over stdio
            async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                )
        except Exception as e:
            log_throttled("main-loop", "error", f"MCP server error: {e}; restarting in 0.5s")
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
