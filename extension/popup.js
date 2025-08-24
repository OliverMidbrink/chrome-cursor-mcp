const out = document.getElementById('out');
function log(obj) { out.textContent = JSON.stringify(obj, null, 2); }

document.getElementById('nav').onclick = async () => {
  const url = document.getElementById('url').value;
  const res = await chrome.runtime.sendMessage({ tool: 'navigate', args: { url } });
  log(res);
};

document.getElementById('shot').onclick = async () => {
  const res = await chrome.runtime.sendMessage({ tool: 'screenshot' });
  if (res?.ok && res.dataUrl) {
    const img = new Image();
    img.src = res.dataUrl;
    out.innerHTML = '';
    out.appendChild(img);
  } else {
    log(res);
  }
};

document.getElementById('logs').onclick = async () => {
  const res = await chrome.runtime.sendMessage({ tool: 'console_logs' });
  log(res);
};

document.getElementById('eval').onclick = async () => {
  const expression = document.getElementById('expr').value;
  const res = await chrome.runtime.sendMessage({ tool: 'evaluate_js', args: { expression } });
  log(res);
};
