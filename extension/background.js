// Chrome MCP background service worker  
// Commands supported: navigate, screenshot, console_logs, evaluate_js, active_tab, get_all_open_tabs

let ws = null;
let wsUrl = "ws://127.0.0.1:6385";
let reconnectTimer = null;
let reconnectDelayMs = 500; // exponential backoff start
const reconnectMaxMs = 15000;
let connecting = false;

function nowTs() {
  try { return new Date().toISOString(); } catch (_) { return String(Date.now()); }
}
function log(...args) {
  try { 
    console.log("[ChromeMCP]", nowTs(), ...args);
    // Also try to store in chrome.storage for debugging if console gets lost
    try {
      chrome.storage.local.get(['debug_logs'], (result) => {
        const logs = result.debug_logs || [];
        logs.push(`[${nowTs()}] ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')}`);
        if (logs.length > 100) logs.shift(); // Keep last 100 logs
        chrome.storage.local.set({debug_logs: logs});
      });
    } catch (_) {}
  } catch (_) {}
}

// Keep a rolling buffer of console logs per tab
const tabLogs = new Map(); // tabId -> string[]

chrome.runtime.onInstalled.addListener(() => {
  log("onInstalled: service worker installed");
});

chrome.runtime.onStartup.addListener(() => {
  log("onStartup: service worker starting up");
});

// Delay initial connection to allow Chrome to fully initialize
setTimeout(() => {
  log("Delayed startup: initializing WebSocket connection");
  connectWS();
}, 1000); // 1 second delay to allow Chrome extension system to fully load

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS();
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectMaxMs, Math.floor(reconnectDelayMs * 2));
  log("scheduleReconnect", { reconnectDelayMs });
}

function connectWS() {
  try {
    if (connecting || ws) {
      log("🔄 connectWS: SKIPPED - already connecting or connected", { 
        connecting, 
        hasWs: !!ws, 
        wsReadyState: ws ? ws.readyState : 'no-ws',
        wsUrl 
      });
      return;
    }
    log("🚀 connectWS: STARTING connection attempt", { wsUrl, reconnectDelayMs, attempt: Date.now() });
    connecting = true;
    
    try {
      ws = new WebSocket(wsUrl);
      log("📡 WebSocket object created", { readyState: ws.readyState, url: ws.url });
    } catch (wsError) {
      log("❌ WebSocket constructor failed", { error: String(wsError) });
      throw wsError;
    }
    
    ws.onopen = (event) => {
      log("✅ ws.onopen: CONNECTION ESTABLISHED", { 
        url: wsUrl, 
        readyState: ws && ws.readyState,
        timestamp: Date.now(),
        event: event ? 'has-event' : 'no-event'
      });
      
      const helloMsg = { event: "hello", ua: navigator.userAgent, timestamp: Date.now() };
      log("📤 Sending HELLO message", helloMsg);
      
      try {
        ws.send(JSON.stringify(helloMsg));
        log("✅ HELLO message sent successfully");
      } catch (sendError) {
        log("❌ Failed to send HELLO message", { error: String(sendError) });
      }
      
      reconnectDelayMs = 500; // reset backoff on success
      connecting = false;
      log("🎉 Extension ready for commands");
    };
    
    ws.onclose = (event) => {
      log("🔌 ws.onclose: CONNECTION CLOSED", { 
        code: event ? event.code : 'no-code',
        reason: event ? event.reason : 'no-reason',
        readyState: ws && ws.readyState,
        wasClean: event ? event.wasClean : 'unknown',
        timestamp: Date.now()
      });
      ws = null;
      connecting = false;
      scheduleReconnect();
    };
    
    ws.onerror = (event) => {
      log("💥 ws.onerror: CONNECTION ERROR", { 
        readyState: ws && ws.readyState,
        event: event ? 'has-error-event' : 'no-error-event',
        timestamp: Date.now(),
        note: "will close and retry"
      });
      try { ws && ws.close(); } catch (_) {}
      ws = null;
      connecting = false;
      scheduleReconnect();
    };
    
    ws.onmessage = async (ev) => {
      try {
        log("📨 ws.onmessage: RECEIVED MESSAGE", { 
          dataLength: (ev && ev.data && ev.data.length) || 0,
          timestamp: Date.now(),
          rawData: ev && ev.data ? ev.data.substring(0, 200) + (ev.data.length > 200 ? '...' : '') : 'no-data'
        });
        
        let msg;
        try { 
          msg = JSON.parse(ev.data); 
          log("📋 Message parsed successfully", { msgKeys: Object.keys(msg || {}), msg });
        } catch (parseError) { 
          log("❌ Failed to parse message JSON", { error: String(parseError), rawData: ev.data });
          return; 
        }
        
        const { id, tool, args } = msg || {};
        log("🔍 Message inspection", { hasId: id != null, tool, hasArgs: !!args, id });
        
        if (!tool || id == null) {
          log("⚠️ Ignoring message - missing tool or id", { tool, id });
          return;
        }
        
        try {
          log("🛠️ handleTool: STARTING", { tool, args, id, timestamp: Date.now() });
          const res = await handleTool(tool, args || {});
          log("✅ handleTool: SUCCESS", { tool, id, resultKeys: Object.keys(res || {}) });
          
          const response = { id, ok: true, ...res };
          log("📤 Sending SUCCESS response", { response });
          
          try {
            ws && ws.send(JSON.stringify(response));
            log("✅ SUCCESS response sent");
          } catch (sendError) {
            log("❌ Failed to send success response", { error: String(sendError), id, tool });
          }
          
        } catch (e) {
          log("❌ handleTool: ERROR", { tool, id, error: String(e), stack: e.stack });
          
          const errorResponse = { id, ok: false, error: String(e) };
          log("📤 Sending ERROR response", { errorResponse });
          
          try {
            ws && ws.send(JSON.stringify(errorResponse));
            log("✅ ERROR response sent");
          } catch (sendError) {
            log("❌ Failed to send error response", { error: String(sendError), id, tool });
          }
        }
      } catch (criticalError) {
        log("💥 CRITICAL ERROR in onmessage handler", { error: String(criticalError), stack: criticalError.stack });
        // Try to reconnect on critical errors
        try { ws && ws.close(); } catch (_) {}
        ws = null;
        connecting = false;
        scheduleReconnect();
      }
    };
    
  } catch (e) {
    log("💥 connectWS: EXCEPTION during setup", { error: String(e), stack: e.stack });
    // If constructor throws synchronously, clear flags so retries aren't blocked
    try { ws && ws.close(); } catch (_) {}
    ws = null;
    connecting = false;
    scheduleReconnect();
  }
}

// Minimal modular functions (5 lines each)
async function getAllTabs() {
  const tabs = await chrome.tabs.query({});
  log("📋 getAllTabs", { count: tabs.length, tabs: tabs.map(t => ({ id: t.id, url: t.url, title: t.title })) });
  return tabs.map(t => ({ id: t.id, url: t.url, title: t.title, active: t.active, windowId: t.windowId }));
}

async function createNewTab(url) {
  log("🆕 createNewTab START", { url });
  const tab = await chrome.tabs.create({ url, active: true });
  log("🆕 Tab created", { tabId: tab.id, url: tab.url, status: tab.status });
  
  await chrome.windows.update(tab.windowId, { focused: true });
  log("🆕 Window focused", { windowId: tab.windowId });
  
  await chrome.tabs.update(tab.id, { active: true });
  log("🆕 Tab activated", { tabId: tab.id });
  
  // Wait a moment and check tab status
  await new Promise(resolve => setTimeout(resolve, 1000));
  const updatedTab = await chrome.tabs.get(tab.id);
  log("🆕 Tab after 1s", { tabId: updatedTab.id, url: updatedTab.url, status: updatedTab.status });
  
  return { tabId: tab.id, url: tab.url };
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] ? { tabId: tabs[0].id, url: tabs[0].url } : null;
}

async function navigateTab(tabId, url) {
  log("🧭 navigateTab START", { tabId, url });
  
  // Check tab exists first
  try {
    const beforeTab = await chrome.tabs.get(tabId);
    log("🧭 Tab before navigation", { tabId, currentUrl: beforeTab.url, status: beforeTab.status });
  } catch (e) {
    log("❌ Tab not found", { tabId, error: String(e) });
    throw new Error(`Tab ${tabId} not found`);
  }
  
  await chrome.tabs.update(tabId, { url });
  log("🧭 Navigation command sent", { tabId, url });
  
  // Wait and check result
  await new Promise(resolve => setTimeout(resolve, 1000));
  const afterTab = await chrome.tabs.get(tabId);
  log("🧭 Tab after navigation", { tabId, newUrl: afterTab.url, status: afterTab.status });
  
  return { tabId, url };
}

async function getTabInfo(tabId) {
  const tab = await chrome.tabs.get(tabId);
  return { id: tab.id, url: tab.url, title: tab.title, status: tab.status };
}

async function captureScreenshot() {
  const dataUrl = await chrome.tabs.captureVisibleTab();
  return { dataUrl };
}

async function executeJS(tabId, expression) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId }, func: (expr) => eval(expr), args: [expression]
  });
  return { value: String(result.result) };
}

// Tool handler - atomic operations
async function handleTool(tool, args) {
  log("🔧 handleTool", { tool, args });
  
  if (tool === "get_all_open_tabs") return { tabs: await getAllTabs() };
  if (tool === "create_tab") return await createNewTab(args.url);
  if (tool === "active_tab") return await getActiveTab();
  if (tool === "navigate") return await navigateTab(args.tabId, args.url);
  if (tool === "get_tab_info") return await getTabInfo(args.tabId);
  if (tool === "screenshot") return await captureScreenshot();
  if (tool === "evaluate_js") return await executeJS(args.tabId, args.expression);
  if (tool === "console_logs") return { logs: tabLogs.get(args.tabId) || [] };
  
  throw new Error(`Unknown tool: ${tool}`);
}

// Messaging entrypoint from popup or content scripts, acting like an MCP tool router
chrome.runtime.onMessage.addListener(async (msg, sender, sendResponse) => {
  try {
    const { tool, args } = msg || {};
    if (!tool) return;
    const res = await handleTool(tool, args || {});
    sendResponse({ ok: true, ...res });
  } catch (e) {
    log("❌ Runtime message handler error", { error: String(e), msg, sender });
    sendResponse({ ok: false, error: String(e) });
  }
  return true;
});

// Capture console via content script relay
chrome.runtime.onMessage.addListener((msg, sender) => {
  try {
    if (msg && msg.type === "chrome-console-log" && sender.tab && sender.tab.id != null) {
      const arr = tabLogs.get(sender.tab.id) || [];
      arr.push(msg.payload);
      if (arr.length > 2000) arr.shift();
      tabLogs.set(sender.tab.id, arr);
      // stream to WS as events
      try { 
        ws && ws.send(JSON.stringify({ event: "console_log", tabId: sender.tab.id, line: msg.payload })); 
      } catch (wsError) {
        log("❌ Failed to send console log to WS", { error: String(wsError) });
      }
    }
  } catch (e) {
    log("❌ Console log handler error", { error: String(e), msg, sender });
  }
});




