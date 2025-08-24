#!/usr/bin/env python3

"""
MCP Server for Chrome automation
Provides open_tab command via WebSocket connection to Chrome extension
"""

import asyncio
import json
import sys
import logging
import websockets
from typing import Any, Dict, Optional
from mcp.server import NotificationOptions, Server
from mcp.types import InitializeResult, Tool, Implementation
import mcp.types as types
import mcp.server.stdio

# Set up logging to stderr so it doesn't interfere with MCP protocol
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

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
        
    async def start_websocket_server(self):
        """Start WebSocket server for Chrome extension connection (with retry)."""
        async def handle_client(websocket, path):
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
        )
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
                    InitializeResult(
                        protocolVersion="2024-11-05",
                        capabilities=server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                        serverInfo=Implementation(
                            name="cursor-chrome-mcp",
                            version="1.0.0"
                        ),
                    ),
                )
        except Exception as e:
            log_throttled("main-loop", "error", f"MCP server error: {e}; restarting in 0.5s")
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
