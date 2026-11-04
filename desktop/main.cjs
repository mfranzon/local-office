// Windows ARM64 desktop host. Native inference stays in the Python backend.
const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');

const root = path.resolve(__dirname, '..');
const data = path.join(root, 'data', 'snapdragon');
let backend;
let window;
let quitting = false;

app.setName('Local Office');
fs.mkdirSync(path.join(data, 'desktop'), { recursive: true });
app.setPath('userData', path.join(data, 'desktop'));
const primary = app.requestSingleInstanceLock();
if (!primary) app.quit();
app.on('second-instance', () => { if (window) { window.restore(); window.focus(); } });

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function start() {
  fs.mkdirSync(path.join(data, 'logs'), { recursive: true });
  const python = path.join(root, 'backend', '.venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(python)) throw new Error('Run scripts\\setup_snapdragon.ps1 first.');
  const port = await freePort();
  const log = fs.openSync(path.join(data, 'logs', 'app.log'), 'a');
  backend = spawn(python, ['-m', 'app.service', '--port', String(port)], {
    cwd: path.join(root, 'backend'), windowsHide: true, stdio: ['ignore', log, log],
    env: { ...process.env, LOCAL_OFFICE_BACKEND: 'snapdragon', DATA_DIR: data,
      MODELS_DIR: path.join(root, 'models'), GENIEX_LIB_PATH: path.join(root, 'build', 'geniex', 'lib'),
      GENIEX_PLUGIN_PATH: path.join(root, 'build', 'geniex', 'lib'), GGML_HEXAGON_DEVICES: 'HTP0', PYTHONUTF8: '1',
      LOCAL_OFFICE_PARENT_PID: String(process.pid) },
  });
  fs.closeSync(log);
  backend.on('error', error => {
    if (!quitting) dialog.showErrorBox('Local Office', error.message);
    app.quit();
  });
  backend.on('exit', code => {
    if (!quitting) {
      dialog.showErrorBox('Local Office', `The local service stopped (code ${code}). See data\\snapdragon\\logs\\app.log.`);
      app.quit();
    }
  });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const response = await fetch(`${base}/api/status`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) { ready = true; break; }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!ready) throw new Error('The local service did not start. See data\\snapdragon\\logs\\app.log.');
  window = new BrowserWindow({ title: 'Local Office', width: 1360, height: 880,
    minWidth: 1000, minHeight: 680, backgroundColor: '#f8fafc', autoHideMenuBar: true,
    show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
                      preload: path.join(__dirname, 'preload.cjs') } });
  ipcMain.handle('local-office:pick-folder', async event => {
    if (event.sender !== window.webContents || event.senderFrame.url !== `${base}/`) {
      throw new Error('Unknown folder-picker caller');
    }
    const result = await dialog.showOpenDialog(window, { properties: ['openDirectory'] });
    return result.canceled ? null : result.filePaths[0];
  });
  window.setMenu(null);
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    if (new URL(url).origin !== base) event.preventDefault();
  });
  window.webContents.session.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
  });
  await window.loadURL(base);
  // Chromium layout adjustments belong to this optional shell, not the Mac UI.
  await window.webContents.insertCSS('html, body { overflow: hidden; } textarea { min-width: 0; min-height: 40px; }');
  window.show();
  window.focus();
  window.on('closed', () => { window = null; app.quit(); });
}

app.on('before-quit', () => {
  quitting = true;
  if (backend && backend.exitCode === null) backend.kill();
});
app.on('window-all-closed', () => app.quit());
if (primary) app.whenReady().then(start).catch(error => {
  dialog.showErrorBox('Local Office', error.message);
  app.quit();
});
