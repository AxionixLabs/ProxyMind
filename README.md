# 📦 Mind Compile

---

## Mind Compile for Windows
```
nuitka --mode=standalone --product-name=Mind --product-version=1.0.0 --windows-icon-from-ico=schematic/resources/icons/mind_windows_icn.ico --show-progress --show-memory --assume-yes-for-downloads --output-dir=applications mind.py
```

---

## Mind Compile for macOS
```
nuitka --macos-create-app-bundle --macos-app-name=Mind --macos-app-version=1.0.0 --macos-app-icon=schematic/resources/images/macos/mind_macos_icn.png --show-progress --output-dir=applications mind.py
```

---
