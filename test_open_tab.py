#!/usr/bin/env python3

import asyncio
import json
import sys
import logging
import websockets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ChromeMCPTestServer:
    def __init__(self):
        self.server = None
        self.client_ws = None
        self.message_id = 0
        self.pending = {}

    async def start(self):
        async def handler(websocket):
            logger.info("✅ Chrome extension connected!")
            self.client_ws = websocket
            try:
                async for raw in websocket:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("event") == "hello":
                        logger.info("📋 Extension UA: %s", msg.get("ua"))
                        # run tests shortly after connecting
                        asyncio.create_task(self.run_tests())
                        continue

                    mid = msg.get("id")
                    fut = self.pending.pop(mid, None)
                    if fut is not None:
                        fut.set_result(msg)
            finally:
                self.client_ws = None

        # websockets.serve supports either handler(websocket) or handler(websocket, path) depending on version
        # Use a lambda to normalize to the single-arg handler we defined
        self.server = await websockets.serve(lambda ws, _path=None: handler(ws), "127.0.0.1", 6385)
        logger.info("🚀 WebSocket server started on ws://127.0.0.1:6385")
        logger.info("⏳ Waiting for Chrome extension to connect...")

    async def send_tool(self, tool, args):
        if not self.client_ws:
            raise RuntimeError("Chrome extension not connected")
        self.message_id += 1
        mid = self.message_id
        payload = {"id": mid, "tool": tool, "args": args}
        fut = asyncio.get_event_loop().create_future()
        self.pending[mid] = fut
        await self.client_ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self.pending.pop(mid, None)
            raise

    async def run_tests(self):
        try:
            logger.info("\n🧪 Starting open_tab tests...")

            logger.info("Test 1: Google active tab")
            r1 = await self.send_tool("open_tab", {"url": "https://www.google.com", "active": True})
            logger.info("Test 1 result: %s", r1)
            await asyncio.sleep(1)

            logger.info("Test 2: GitHub background tab")
            r2 = await self.send_tool("open_tab", {"url": "https://www.github.com", "active": False})
            logger.info("Test 2 result: %s", r2)
            await asyncio.sleep(1)

            logger.info("Test 3: StackOverflow default active")
            r3 = await self.send_tool("open_tab", {"url": "https://stackoverflow.com"})
            logger.info("Test 3 result: %s", r3)

            logger.info("\n✅ All tests completed successfully!")
        except Exception as e:
            logger.error("❌ Test failed: %s", e)
        finally:
            await asyncio.sleep(2)
            logger.info("\n🔚 Closing test server...")
            await self.stop()
            # Exit process
            asyncio.get_event_loop().call_soon(asyncio.get_event_loop().stop)

    async def stop(self):
        try:
            if self.client_ws:
                await self.client_ws.close()
        finally:
            if self.server:
                self.server.close()
                await self.server.wait_closed()

async def main():
    srv = ChromeMCPTestServer()
    try:
        await srv.start()
        # keep running until run_tests stops the loop
        await asyncio.get_event_loop().create_future()
    except KeyboardInterrupt:
        await srv.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        # Event loop already stopped by run_tests
        pass
