(function () {
  const origLog = console.log;
  const origWarn = console.warn;
  const origError = console.error;
  const send = (level, args) => {
    try {
      const text = Array.from(args).map(String).join(" ");
      chrome.runtime.sendMessage({ type: "chrome-console-log", payload: `[${level}] ${text}` });
    } catch (_) {}
  };
  console.log = function () {
    send("log", arguments);
    return origLog.apply(console, arguments);
  };
  console.warn = function () {
    send("warn", arguments);
    return origWarn.apply(console, arguments);
  };
  console.error = function () {
    send("error", arguments);
    return origError.apply(console, arguments);
  };
})();
