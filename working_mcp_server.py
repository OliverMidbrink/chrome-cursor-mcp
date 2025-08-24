#!/usr/bin/env python3

import asyncio
import json
import sys
import websockets
import logging
from typing import Dict, Any

# Redirect all logging to stderr to avoid interfering with stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

class ChromeExtensionMCP:
    def __init__(self):
        self.ws_server = None
        self.chrome_ws = None
        self.message_counter = 0
        self.pending_requests = {}
        
    async def start_websocket_server(self):
        """Start WebSocket server to communicate with Chrome extension"""
        async def handle_chrome_connection(websocket, path):
            logger.info("Chrome extension connected")
            self.chrome_ws = websocket
            try:
                async for message in websocket:
                    data = json.loads(message)
                    
                    # Handle hello from extension
                    if data.get('event') == 'hello':
                        logger.info(f"Extension hello: {data.get('ua', 'unknown')}")
                        continue
                    
                    # Handle responses to our requests
                    msg_id = data.get('id')
                    if msg_id and msg_id in self.pending_requests:
                        future = self.pending_requests.pop(msg_id)
                        future.set_result(data)
                        
            except websockets.exceptions.ConnectionClosed:
                logger.info("Chrome extension disconnected")
                self.chrome_ws = None
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.chrome_ws = None
        
        self.ws_server = await websockets.serve(handle_chrome_connection, "127.0.0.1", 6385)
        logger.info("WebSocket server started on ws://127.0.0.1:6385")
    
    async def send_to_chrome(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to Chrome extension and wait for response"""
        if not self.chrome_ws:
            raise Exception("Chrome extension not connected")
        
        self.message_counter += 1
        msg_id = self.message_counter
        
        message = {
            "id": msg_id,
            "tool": tool,
            "args": args
        }
        
        # Create future for response
        future = asyncio.Future()
        self.pending_requests[msg_id] = future
        
        try:
            await self.chrome_ws.send(json.dumps(message))
            # Wait for response with timeout
            response = await asyncio.wait_for(future, timeout=10.0)
            return response
        except asyncio.TimeoutError:
            self.pending_requests.pop(msg_id, None)
            raise Exception("Timeout waiting for Chrome extension response")
        except Exception as e:
            self.pending_requests.pop(msg_id, None)
            raise Exception(f"Error communicating with Chrome: {e}")

# Global instance
chrome_mcp = ChromeExtensionMCP()

def handle_mcp_message(message: str) -> str:
    """Handle incoming MCP JSON-RPC message and return response"""
    try:
        request = json.loads(message)
        method = request.get('method')
        params = request.get('params', {})
        req_id = request.get('id')
        
        if method == 'initialize':
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "cursor-chrome-mcp",
                    "version": "1.0.0"
                }
            }
            
        elif method == 'tools/list':
            result = {
                "tools": [
                    {
                        "name": "open_tab",
                        "description": "Open a new tab in Chrome browser",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string", 
                                    "description": "URL to open"
                                },
                                "active": {
                                    "type": "boolean",
                                    "description": "Make tab active (default: true)"
                                }
                            },
                            "required": ["url"]
                        }
                    }
                ]
            }
            
        elif method == 'tools/call':
            # This needs to be handled async, so we'll mark it
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"async_pending": True}
            })
            
        else:
            raise Exception(f"Unknown method: {method}")
        
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }
        
        return json.dumps(response)
        
    except Exception as e:
        error_response = {
            "jsonrpc": "2.0",
            "id": request.get('id') if 'request' in locals() else None,
            "error": {
                "code": -1,
                "message": str(e)
            }
        }
        return json.dumps(error_response)

async def handle_tool_call(params: Dict[str, Any], req_id: Any) -> str:
    """Handle tool calls asynchronously"""
    try:
        name = params.get('name')
        arguments = params.get('arguments', {})
        
        if name == 'open_tab':
            url = arguments.get('url')
            active = arguments.get('active', True)
            
            if not url:
                raise Exception("URL is required")
            
            # Send to Chrome extension
            response = await chrome_mcp.send_to_chrome('open_tab', {
                'url': url,
                'active': active
            })
            
            if response.get('ok'):
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": f"✅ Opened tab: {url}\nTab ID: {response.get('tabId')}\nActive: {active}"
                        }
                    ]
                }
            else:
                raise Exception(response.get('error', 'Unknown error'))
                
        else:
            raise Exception(f"Unknown tool: {name}")
        
        return json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        })
        
    except Exception as e:
        return json.dumps({
            "jsonrpc": "2.0", 
            "id": req_id,
            "error": {
                "code": -1,
                "message": str(e)
            }
        })

async def main():
    """Main server loop"""
    # Start WebSocket server
    await chrome_mcp.start_websocket_server()
    
    # Process stdin
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            # Read line from stdin
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
                
            line = line.strip()
            if not line:
                continue
            
            # Check if this is a tool call
            try:
                request = json.loads(line)
                if request.get('method') == 'tools/call':
                    # Handle async
                    response = await handle_tool_call(request.get('params', {}), request.get('id'))
                    print(response)
                    sys.stdout.flush()
                    continue
            except:
                pass
            
            # Handle other messages synchronously
            response = handle_mcp_message(line)
            print(response)
            sys.stdout.flush()
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            break

if __name__ == "__main__":
    asyncio.run(main())
