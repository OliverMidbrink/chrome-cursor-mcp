#!/usr/bin/env python3

"""
Simple MCP Server for Chrome automation
"""

import asyncio
import json
import sys
import logging
import websockets
from typing import Any, Dict

# Set up logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

class SimpleMCPServer:
    def __init__(self):
        self.websocket = None
        self.websocket_server = None
        self.message_id = 0
        self.pending_requests = {}
        self.connected = False
        
    async def start_websocket_server(self):
        """Start WebSocket server for Chrome extension connection"""
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
        
        # Start WebSocket server
        try:
            self.websocket_server = await websockets.serve(
                handle_client, 
                "127.0.0.1", 
                6385
            )
            logger.info("WebSocket server started on ws://127.0.0.1:6385")
            logger.info("Waiting for Chrome extension to connect...")
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}")
            raise
    
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

    def handle_mcp_request(self, request):
        """Handle MCP JSON-RPC requests"""
        method = request.get('method')
        params = request.get('params', {})
        
        if method == 'initialize':
            return {
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
            return {
                "tools": [
                    {
                        "name": "open_tab",
                        "description": "Open a new tab in Chrome",
                        "inputSchema": {
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
                        }
                    }
                ]
            }
        
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments', {})
            
            if name == 'open_tab':
                if not self.connected:
                    raise Exception("Chrome extension not connected. Please make sure the Chrome extension is loaded and running.")
                
                # This will be handled by the async part
                return {"async_call": True, "tool": "open_tab", "args": arguments}
            else:
                raise Exception(f"Unknown tool: {name}")
        
        else:
            raise Exception(f"Unknown method: {method}")

# Global instance
chrome_server = SimpleMCPServer()

async def handle_async_call(tool, args):
    """Handle async tool calls"""
    try:
        result = await chrome_server.send_tool_request(tool, args)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully opened tab: {args.get('url')}\nTab ID: {result.get('tabId')}\nActive: {args.get('active', True)}"
                }
            ]
        }
    except Exception as e:
        raise Exception(f"Failed to open tab: {str(e)}")

async def main():
    """Main server function"""
    try:
        # Start WebSocket server
        await chrome_server.start_websocket_server()
        
        # Handle MCP protocol over stdio
        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    request = json.loads(line)
                    request_id = request.get('id')
                    
                    try:
                        result = chrome_server.handle_mcp_request(request)
                        
                        # Handle async calls
                        if isinstance(result, dict) and result.get('async_call'):
                            result = await handle_async_call(result['tool'], result['args'])
                        
                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": result
                        }
                        
                    except Exception as e:
                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -1,
                                "message": str(e)
                            }
                        }
                    
                    print(json.dumps(response))
                    sys.stdout.flush()
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Error parsing JSON: {e}")
                    
            except Exception as e:
                logger.error(f"Error handling request: {e}")
                break
                
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
