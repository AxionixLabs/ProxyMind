# npm 发布工作流

## 登录和配置 Token

```bash
npm login
npm config set //registry.npmjs.org/:_authToken "npm_xxx"
```

**Windows：**
```shell
npm whoami; npm config get registry
```

**macOS：**
```bash
npm whoami && npm config get registry
```

---

## 验证登录和版本

```bash
npm view @craftline/mind version
npm view @craftline/mind-win32 version
npm view @craftline/mind-darwin version
```

---

### 升级版本

需要同步的文件：

```text
packages/mind/package.json
packages/mind-win32/package.json
packages/mind-darwin/package.json
packages/mind/package.json optionalDependencies
```

---

## 🪟 Windows 发布

进入 npm 工作目录，后续 `npm pack`、本地 tarball 安装和发布命令都在该目录执行。

```shell
Set-Location .\npm; npm pkg get version --workspaces
```

### 🪟 同步产物

```shell
npm run sync:applications
```

```shell
$target = "packages\mind-win32\applications\MindEngine\schematic\supports\windows\helix.dist"; if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop }
```

```shell
$target = "packages\mind-win32\applications\MindEngine\schematic\supports\windows\helix.dist"; if (Test-Path -LiteralPath $target) { throw "$target still exists" }
```

### 🪟 Dry Run

```shell
npm publish -w @craftline/mind-win32 --dry-run --access public; if ($LASTEXITCODE -eq 0) { npm publish -w @craftline/mind --dry-run --access public }
```

### 🪟 发布前本地安装测试

```shell
npm pack -w @craftline/mind-win32; if ($LASTEXITCODE -eq 0) { npm pack -w @craftline/mind }
```

### 🪟 安装本地 tarball：

```shell
npm install -g craftline-mind-win32-1.0.0.tgz craftline-mind-1.0.0.tgz
```

### 🪟 验证命令入口：

```shell
mind
```

### 🪟 卸载测试包：

```shell
Get-NetTCPConnection -LocalPort 3333 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; npm uninstall -g @craftline/mind @craftline/mind-win32
```

### 🪟 发布
```shell
npm publish -w @craftline/mind-win32 --access public
```

```shell
npm publish -w @craftline/mind --access public
```

---

## 🍎 macOS 发布

进入 npm 工作目录，后续 `npm pack`、本地 tarball 安装和发布命令都在该目录执行。

```bash
cd npm && npm pkg get version --workspaces
```

### 🍎 同步产物

```bash
npm run sync:applications
```

```bash
target="packages/mind-darwin/applications/Mind.app/Contents/MacOS/schematic/supports/macos/helix.app"; rm -rf "$target"
```

```bash
target="packages/mind-darwin/applications/Mind.app/Contents/MacOS/schematic/supports/macos/helix.app"; test ! -e "$target" || { echo "$target still exists" >&2; exit 1; }
```

### 🍎 Dry Run

```bash
npm publish -w @craftline/mind-darwin --dry-run --access public && npm publish -w @craftline/mind --dry-run --access public
```

### 🍎 发布前本地安装测试

```bash
npm pack -w @craftline/mind-darwin && npm pack -w @craftline/mind
```

### 🍎 安装本地 tarball：

```bash
npm install -g craftline-mind-darwin-1.0.0.tgz craftline-mind-1.0.0.tgz
```

### 🍎 验证命令入口：

```bash
mind
```

### 🍎 卸载测试包：

```bash
lsof -ti tcp:3333 | while read -r pid; do kill -9 "$pid"; done; npm uninstall -g @craftline/mind @craftline/mind-darwin
```

### 🍎 发布

```bash
npm publish -w @craftline/mind-darwin --access public
```

```bash
npm publish -w @craftline/mind --access public
```
