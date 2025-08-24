### Quick note
You only install the Chrome extension. Ask Cursor to configure the MCP backend for you (it can add the entry to `~/.cursor/mcp.json` using this repo’s absolute path).

### Super-minimal setup (inside Cursor)
Ask Cursor to add this MCP server to `~/.cursor/mcp.json` using your project’s absolute path. For example:

- “Add an MCP server named chrome that runs `/ABS/PATH/TO/REPO/.venv/bin/python /ABS/PATH/TO/REPO/mcp_server.py` with cwd `/ABS/PATH/TO/REPO`.”

This lets Cursor write the config for you without manual editing.

## cursor-chrome-mcp

A minimal Chrome + MCP bridge so Cursor can see and act on real web pages you build.
- For web dev: lets Cursor open/navigate tabs, scroll, screenshot, and visually analyze pages for more autonomous development.

### Install (quick)
1) Chrome extension
- Open `chrome://extensions` → enable Developer Mode → Load unpacked → select the `chrome-mcp` folder.

2) Python server (MCP)
- Create venv and install deps:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mcp websockets openai
```
- Configure Cursor to run the server (global `~/.cursor/mcp.json` or per-project `.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "chrome": {
      "command": "/ABS/PATH/TO/REPO/.venv/bin/python",
      "args": ["/ABS/PATH/TO/REPO/mcp_server.py"],
      "cwd": "/ABS/PATH/TO/REPO"
    }
  }
}
```
- Optional: set `OPENAI_API_KEY` for screenshot analysis.

### Use
- In Cursor MCP panel, connect to "chrome" and try tools:
  - `open_tab`, `list_tabs`, `navigate_tab`, `scroll_to` / `scroll_by` / `scroll_from_point`
  - `screenshot_tab`, `screenshot_and_analyze` (requires `OPENAI_API_KEY`)

Notes
- Screenshots use Chrome DevTools Protocol and do not steal window focus.
- Artifacts are saved under `artifacts/`.
