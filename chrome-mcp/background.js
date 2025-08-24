// Chrome MCP background service worker  
// Commands supported: navigate, screenshot, console_logs, evaluate_js, active_tab, get_all_open_tabs

let ws = null;
let wsUrl = "ws://127.0.0.1:6385";
let reconnectTimer = null;
let reconnectDelayMs = 500;
const reconnectMaxMs = 15000;
let connecting = false;

// Keep a rolling buffer of console logs per tab
const tabLogs = new Map(); // tabId -> string[]

chrome.runtime.onInstalled.addListener(() => {
  console.log("Chrome MCP installed");
});

// Connect to WebSocket immediately on startup
connectWS();

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS();
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectMaxMs, Math.floor(reconnectDelayMs * 2));
}

function connectWS() {
  try {
    if (connecting || ws) return;
    connecting = true;
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {
      console.log("Chrome MCP connected:", wsUrl);
      ws.send(JSON.stringify({ event: "hello", ua: navigator.userAgent }));
      reconnectDelayMs = 500;
      connecting = false;
    };
    ws.onclose = () => {
      ws = null;
      connecting = false;
      scheduleReconnect();
    };
    ws.onerror = () => {
      try { ws && ws.close(); } catch (_) {}
      ws = null;
      connecting = false;
      scheduleReconnect();
    };
    ws.onmessage = async (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      const { id, tool, args } = msg || {};
      if (!tool || id == null) return;
      try {
        const res = await handleTool(tool, args || {});
        ws && ws.send(JSON.stringify({ id, ok: true, ...res }));
      } catch (e) {
        ws && ws.send(JSON.stringify({ id, ok: false, error: String(e) }));
      }
    };
  } catch (e) {
    scheduleReconnect();
  }
}

async function handleTool(tool, args) {

  if (tool === "active_tab") {
    const tab = await getActiveTab();
    return { tabId: tab?.id, url: tab?.url };
  }
  if (tool === "open_tab") {
    const { url, active } = args;
    const tab = await openTab(url, active);
    return { tabId: tab?.id, url: tab?.url };
  }
  if (tool === "navigate") {
    const { url } = args;
    const tab = await getActiveTab();
    await chrome.tabs.update(tab.id, { url });
    return { done: true };
  }
  if (tool === "evaluate_js") {
    const { expression } = args;
    const tab = await getActiveTab();
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (expr) => {
        try { /* eslint-disable no-eval */ const r = eval(expr); return { ok: true, value: String(r) }; }
        catch (e) { return { ok: false, error: String(e) }; }
      },
      args: [expression],
    });
    return { ok: result?.ok, value: result?.value, error: result?.error };
  }
  if (tool === "console_logs") {
    const tab = await getActiveTab();
    const logs = tabLogs.get(tab.id) || [];
    return { logs };
  }
  if (tool === "screenshot") {
    const dataUrl = await chrome.tabs.captureVisibleTab();
    return { dataUrl };
  }
  if (tool === "get_all_open_tabs") {
    try {
      const allTabs = await chrome.tabs.query({});
      const tabsInfo = allTabs.map(tab => ({
        id: tab.id,
        url: tab.url,
        title: tab.title,
        active: tab.active,
        windowId: tab.windowId,
        index: tab.index,
        pinned: tab.pinned,
        status: tab.status
      }));
      return { ok: true, tabs: tabsInfo, count: tabsInfo.length };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  throw new Error(`unknown tool ${tool}`);
}

// Messaging entrypoint from popup or content scripts, acting like an MCP tool router
chrome.runtime.onMessage.addListener(async (msg, sender, sendResponse) => {
  try {
    const { tool, args } = msg || {};
    if (!tool) return;
    const res = await handleTool(tool, args || {});
    sendResponse({ ok: true, ...res });
  } catch (e) {
    sendResponse({ ok: false, error: String(e) });
  }
  return true;
});

// Capture console via content script relay
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === "chrome-console-log" && sender.tab && sender.tab.id != null) {
    const arr = tabLogs.get(sender.tab.id) || [];
    arr.push(msg.payload);
    if (arr.length > 2000) arr.shift();
    tabLogs.set(sender.tab.id, arr);
    // stream to WS as events
    try { ws && ws.send(JSON.stringify({ event: "console_log", tabId: sender.tab.id, line: msg.payload })); } catch (_) {}
  }
});

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function openTab(url, active = true) {
  return chrome.tabs.create({ url, active });
}