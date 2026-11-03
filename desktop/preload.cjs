const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('localOffice', {
  pickFolder: () => ipcRenderer.invoke('local-office:pick-folder'),
});
