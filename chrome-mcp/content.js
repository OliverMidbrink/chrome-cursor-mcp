// Inject a page-context script to hook window.console in the main world,
// then relay messages back to the extension via window.postMessage → content script → background.
(function () {
  try {
    const relayListener = (event) => {
      try {
        if (event.source !== window) return;
        const msg = event.data;
        if (!msg || msg.source !== "mcp-console") return;
        chrome.runtime.sendMessage({ type: "chrome-console-log", payload: `[${msg.level}] ${msg.text}` });
      } catch (_) {}
    };
    window.addEventListener("message", relayListener, false);

    const hookCode = `(() => {
      try {
        const safe = (v) => {
          try { return typeof v === 'string' ? v : JSON.stringify(v); } catch { return String(v); }
        };
        const join = (args) => Array.from(args).map(safe).join(' ');
        const log = window.console.log.bind(window.console);
        const warn = window.console.warn.bind(window.console);
        const error = window.console.error.bind(window.console);
        window.console.log = function() { try { window.postMessage({ source: 'mcp-console', level: 'log', text: join(arguments) }, '*'); } catch {} return log.apply(this, arguments); };
        window.console.warn = function() { try { window.postMessage({ source: 'mcp-console', level: 'warn', text: join(arguments) }, '*'); } catch {} return warn.apply(this, arguments); };
        window.console.error = function() { try { window.postMessage({ source: 'mcp-console', level: 'error', text: join(arguments) }, '*'); } catch {} return error.apply(this, arguments); };
      } catch {}
    })();`;

    const s = document.createElement('script');
    s.textContent = hookCode;
    (document.documentElement || document.head || document.body).appendChild(s);
    s.remove();
  } catch (_) {}
})();
