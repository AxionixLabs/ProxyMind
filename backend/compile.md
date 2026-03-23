# 📦 Helix Compile

---

## Helix Compile for Windows
```
nuitka --mode=standalone --product-name=Helix --product-version=1.0.0 --windows-icon-from-ico=schematic/resources/icons/helix_windows_icn.ico --include-data-dir=backend/web=web --include-data-dir=backend/requires/windows=requires/windows --show-progress --show-memory --assume-yes-for-downloads --output-dir=schematic/supports/windows backend/helix.py
```

---

## Helix Compile for macOS
```
nuitka --macos-create-app-bundle --macos-app-name=Helix --macos-app-version=1.0.0 --macos-app-icon=schematic/resources/images/macos/helix_macos_icn.png --include-data-dir=backend/web=web --include-data-dir=backend/requires/macos=requires/macos --show-progress --output-dir=schematic/supports/macos backend/helix.py
```

---
