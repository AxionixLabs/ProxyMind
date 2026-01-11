## MCP Server macOS Pack
```
nuitka --macos-create-app-bundle --macos-app-name=Helix --macos-app-version=1.0.0 --macos-app-icon=schematic/resources/icons/mcp_proxy_mind_icn.ico --show-progress --output-dir=applications mcp_app/mcp_server.py
```

---

## MCP Server Windows Pack
```
nuitka --mode=standalone --product-name=Helix --product-version=1.0.0 --windows-icon-from-ico=schematic/resources/icons/mcp_proxy_mind_icn.ico --show-progress --show-memory --assume-yes-for-downloads --output-dir=applications mcp_app/mcp_server.py
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
