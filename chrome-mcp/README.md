# chrome-mcp

Standalone MCP server to control a real Chrome via a companion Chrome extension.

## Components
- Chrome extension (MV3) in this folder (manifest/background/content/popup)
- MCP server (Python) that connects to the extension via a local WebSocket bridge

## Install extension
1) Create placeholder icons (if not present):
```bash
cd apps/chrome-mcp
printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/az94xkAAAAASUVORK5CYII=' | base64 --decode > icon128.png
sips -Z 32 icon128.png --out icon32.png >/dev/null
sips -Z 16 icon128.png --out icon16.png >/dev/null
```
2) Load unpacked in Chrome → Extensions → Developer mode → Load unpacked → select `apps/chrome-mcp`.

## Run MCP server
```bash
cd apps/chrome-mcp
python -m pip install -e .
python -m chrome_mcp.server
```

## Cursor config example
```json
{
  "mcpServers": {
    "chrome": {
      "command": "python",
      "args": ["-m", "chrome_mcp.server"]
    }
  }
}
```

## Tools
- navigate(url) - Navigate to a URL
- screenshot() → dataUrl - Capture screenshot and save to disk
- console_logs() - Get browser console logs
- evaluate_js(expression) - Execute JavaScript code
- active_tab() - Get active tab information
- get_all_open_tabs() - Get information about all open browser tabs
- analyze_screenshot(prompt) - AI analysis of screenshots (requires OpenAI API key)

**Note:** Chrome MCP now uses a fixed WebSocket port (localhost:6385) - no configuration needed!
