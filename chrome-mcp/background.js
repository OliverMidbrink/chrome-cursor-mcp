// Chrome MCP background service worker  
// Commands supported: navigate, screenshot, console_logs_for_tab, enable_console_stream, evaluate_js, active_tab, get_all_open_tabs, navigate_tab, screenshot_tab, get_window_bounds, get_viewport, close_tab, close_tabs_by_url

let ws = null;
let wsUrl = "ws://127.0.0.1:6385";
let reconnectTimer = null;
const reconnectDelayMs = 500; // Fixed 500ms interval
let connecting = false;
let heartbeatTimer = null;
let lastActivityTs = Date.now();

// Throttled logging to avoid console spam when server is down
const logLast = new Map();
function logThrottled(key, ...args) {
  const now = Date.now();
  const last = logLast.get(key) || 0;
  if (now - last > 2000) {
    console.log(...args);
    logLast.set(key, now);
  }
}

// Keep a rolling buffer of console logs per tab
const tabLogs = new Map(); // tabId -> string[]
const attachedDebuggerTabs = new Set();

chrome.runtime.onInstalled.addListener(() => {
  console.log("Chrome MCP installed");
});

// Connect to WebSocket immediately on startup
connectWS();

// Also attempt a connection on browser startup
chrome.runtime.onStartup?.addListener(() => {
  connectWS();
});

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS();
  }, reconnectDelayMs);
}

function connectWS() {
  // Prevent any uncaught errors from bubbling up
  try {
    if (connecting || (ws && ws.readyState === WebSocket.OPEN)) return;
    
    // Clean up existing connection
    if (ws) {
      try { ws.close(); } catch (_) {}
      ws = null;
    }
    
    connecting = true;
    
    // Wrap WebSocket creation in try-catch to prevent errors
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      logThrottled("connect-error", "Failed to connect to MCP server - server not started?");
      connecting = false;
      scheduleReconnect();
      return;
    }
    
    ws.onopen = () => {
      console.log("Chrome MCP connected:", wsUrl);
      try {
        ws.send(JSON.stringify({ event: "hello", ua: navigator.userAgent }));
      } catch (e) {
        console.log("Failed to send hello message");
      }
      connecting = false;

      // Reset activity and start heartbeat keepalive
      lastActivityTs = Date.now();
      if (heartbeatTimer) { try { clearInterval(heartbeatTimer); } catch (_) {} heartbeatTimer = null; }
      heartbeatTimer = setInterval(() => {
        try {
          if (ws && ws.readyState === WebSocket.OPEN) {
            // Application-level heartbeat (server may ignore)
            ws.send(JSON.stringify({ event: "ping", ts: Date.now() }));
            // If no activity for a while, force reconnect
            if (Date.now() - lastActivityTs > 30000) {
              try { ws.close(); } catch (_) {}
            }
          } else {
            try { clearInterval(heartbeatTimer); } catch (_) {}
            heartbeatTimer = null;
          }
        } catch (_) {}
      }, 10000);
    };
    
    ws.onclose = (event) => {
      logThrottled("closed", "MCP server connection closed");
      ws = null;
      connecting = false;
      if (heartbeatTimer) { try { clearInterval(heartbeatTimer); } catch (_) {} heartbeatTimer = null; }
      scheduleReconnect();
    };
    
    ws.onerror = (error) => {
      logThrottled("connect-error", "Failed to connect to MCP server - server not started?");
      try { 
        if (ws) ws.close(); 
      } catch (_) {}
      ws = null;
      connecting = false;
      if (heartbeatTimer) { try { clearInterval(heartbeatTimer); } catch (_) {} heartbeatTimer = null; }
      scheduleReconnect();
    };
    
    ws.onmessage = async (ev) => {
      try {
        let msg;
        try { 
          msg = JSON.parse(ev.data); 
        } catch { 
          return; 
        }
        
        lastActivityTs = Date.now();
        const { id, tool, args } = msg || {};
        if (!tool || id == null) return;
        
        try {
          const res = await handleTool(tool, args || {});
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ id, ok: true, ...res }));
          }
        } catch (e) {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ id, ok: false, error: String(e) }));
          }
        }
      } catch (e) {
        console.log("Error handling WebSocket message:", e.message);
      }
    };
    
  } catch (e) {
    logThrottled("connect-error", "Failed to connect to MCP server - server not started?");
    connecting = false;
    ws = null;
    scheduleReconnect();
  }
}

// Listen to DevTools Protocol events to capture console/log entries
chrome.debugger.onEvent.addListener((source, method, params) => {
  try {
    const tabId = source.tabId;
    if (!tabId) return;
    const buf = tabLogs.get(tabId) || [];
    if (method === 'Runtime.consoleAPICalled') {
      const level = params.type || 'log';
      const args = (params.args || []).map(a => a.value != null ? String(a.value) : a.description || '').join(' ');
      buf.push(`[${level}] ${args}`);
    } else if (method === 'Log.entryAdded') {
      const e = params.entry || {};
      buf.push(`[${e.level || 'log'}] ${e.text || ''}`);
    } else if (method === 'Runtime.exceptionThrown') {
      const d = params.exceptionDetails || {};
      buf.push(`[error] exception ${d.text || ''}`);
    }
    if (buf.length > 2000) buf.shift();
    tabLogs.set(tabId, buf);
  } catch (_) {}
});

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
  if (tool === "navigate_tab") {
    const { tabId, url, active } = args;
    if (tabId == null || !url) throw new Error("navigate_tab requires tabId and url");
    const tab = await chrome.tabs.update(tabId, { url, active: active !== false });
    return { done: true, tabId: tab?.id, url: tab?.url };
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
  if (tool === "console_logs_for_tab") {
    const { tabId } = args || {};
    if (tabId == null) throw new Error("console_logs_for_tab requires tabId");
    const logs = tabLogs.get(tabId) || [];
    return { logs };
  }
  if (tool === "enable_console_stream") {
    const { tabId } = args || {};
    if (tabId == null) throw new Error("enable_console_stream requires tabId");
    const target = { tabId };
    if (!attachedDebuggerTabs.has(tabId)) {
      await new Promise((resolve, reject) => chrome.debugger.attach(target, "1.3", () => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()));
      attachedDebuggerTabs.add(tabId);
      try { await sendCommand(target, 'Runtime.enable', {}); } catch (_) {}
      try { await sendCommand(target, 'Log.enable', {}); } catch (_) {}
    }
    return { ok: true };
  }
  if (tool === "screenshot") {
    const dataUrl = await chrome.tabs.captureVisibleTab();
    return { dataUrl };
  }
  if (tool === "screenshot_tab") {
    const { tabId } = args;
    if (tabId == null) throw new Error("screenshot_tab requires tabId");
    const tab = await chrome.tabs.get(tabId);
    if (!tab) throw new Error("tab not found");
    try {
      // Use Chrome DevTools Protocol via chrome.debugger to capture without focus
      const target = { tabId };
      await new Promise((resolve, reject) => chrome.debugger.attach(target, "1.3", () => chrome.runtime.lastError ? reject(chrome.runtime.lastError) : resolve()));
      await sendCommand(target, "Page.enable", {});
      // Ensure frame is ready
      await delay(200);
      const screenshot = await sendCommand(target, "Page.captureScreenshot", { format: "jpeg", quality: 85, fromSurface: true });
      await new Promise((resolve) => chrome.debugger.detach(target, () => resolve()));
      const dataUrl = `data:image/jpeg;base64,${screenshot.data}`;
      return { dataUrl, tabId };
    } catch (e) {
      try { await new Promise((resolve) => chrome.debugger.detach({ tabId }, () => resolve())); } catch (_) {}
      return { ok: false, error: String(e && e.message || e) };
    }
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
  if (tool === "close_tab") {
    const { tabId } = args || {};
    if (tabId == null) throw new Error("close_tab requires tabId");
    try { await chrome.tabs.remove(tabId); } catch (e) { return { ok: false, error: String(e) }; }
    return { ok: true };
  }
  if (tool === "close_tabs_by_url") {
    const { includes } = args || {};
    if (!includes) throw new Error("close_tabs_by_url requires includes substring");
    const allTabs = await chrome.tabs.query({});
    const toClose = allTabs.filter(t => (t.url || '').includes(includes)).map(t => t.id).filter(Boolean);
    if (toClose.length) {
      try { await chrome.tabs.remove(toClose); } catch (_) {}
    }
    return { ok: true, closed: toClose };
  }
  if (tool === "get_window_bounds") {
    const { tabId } = args;
    const tab = tabId != null ? await chrome.tabs.get(tabId) : await getActiveTab();
    const win = await chrome.windows.get(tab.windowId);
    return { width: win.width, height: win.height, left: win.left, top: win.top, windowId: win.id };
  }
  if (tool === "get_viewport") {
    const { tabId } = args;
    const tab = tabId != null ? await chrome.tabs.get(tabId) : await getActiveTab();
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({ innerWidth: window.innerWidth, innerHeight: window.innerHeight, devicePixelRatio: window.devicePixelRatio }),
      args: [],
    });
    return { ...result };
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

// Cleanup tab log buffer when tabs are closed
chrome.tabs.onRemoved.addListener((tabId) => {
  try { tabLogs.delete(tabId); } catch (_) {}
});

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function openTab(url, active = true) {
  return chrome.tabs.create({ url, active });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sendCommand(target, method, params) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand(target, method, params, (result) => {
      const err = chrome.runtime.lastError;
      if (err) return reject(err);
      resolve(result);
    });
  });
}