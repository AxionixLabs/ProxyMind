## MCP Server macOS Pack
```
nuitka --macos-create-app-bundle --macos-app-name=Helix --macos-app-version=1.0.0 --macos-app-icon=schematic/resources/images/macos/helix_macos_icn.png --show-progress --output-dir=applications backend/helix.py
```

---

## MCP Server Windows Pack
```
nuitka --mode=standalone --product-name=Helix --product-version=1.0.0 --windows-icon-from-ico=schematic/resources/icons/helix_windows_icn.ico --show-progress --show-memory --assume-yes-for-downloads --output-dir=applications backend/helix.py
```

---

## macOS Kill 3333 Port
```
lsof -ti :3333 | xargs kill -9
```

---

## Windows Kill 3333 Port
```
Get-NetTCPConnection -LocalPort 3333 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---
