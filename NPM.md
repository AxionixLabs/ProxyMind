# NPM 发布命令

## 登录和配置 Token

```bash
npm login
npm config set //registry.npmjs.org/:_authToken "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"
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

进入 npm 工作目录，后续 `npm pack`、本地 tarball 安装和发布命令都在该目录执行。

**Windows：**
```shell
Set-Location .\mind_npm; npm pkg get version --workspaces
```

**macOS：**
```bash
cd mind_npm && npm pkg get version --workspaces
```

---

## 升级版本

需要同步的文件：

```text
packages/mind/package.json
packages/mind-win32/package.json
packages/mind-darwin/package.json
packages/mind/package.json optionalDependencies
```
---

## 同步产物

```bash
npm run sync:applications
```
---

## Dry Run

```bash
npm publish -w @craftline/mind-win32 --dry-run --access public
npm publish -w @craftline/mind-darwin --dry-run --access public
npm publish -w @craftline/mind --dry-run --access public
```
---

## 发布前本地安装测试

> 本地测试必须生成两个 tarball：当前平台运行时包和主包。

**Windows：**
```shell
npm pack -w @craftline/mind-win32; if ($LASTEXITCODE -eq 0) { npm pack -w @craftline/mind }
```

**macOS：**
```bash
npm pack -w @craftline/mind-darwin && npm pack -w @craftline/mind
```

### 安装本地 tarball：

**Windows：**
```shell
npm install -g ./craftline-mind-win32-1.0.0.tgz ./craftline-mind-1.0.0.tgz
```

**macOS：**
```bash
npm install -g ./craftline-mind-darwin-1.0.0.tgz ./craftline-mind-1.0.0.tgz
```

### 验证命令入口：

```bash
mind
```

### 卸载测试包：

**Windows：**
```shell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 3333 -State Listen).OwningProcess -Force
npm uninstall -g @craftline/mind @craftline/mind-win32
```

**macOS：**
```bash
lsof -ti tcp:3333 | xargs kill -9
npm uninstall -g @craftline/mind @craftline/mind-darwin
```

---

## 发布

```bash
npm publish -w @craftline/mind-win32 --access public
```

```bash
npm publish -w @craftline/mind-darwin --access public
```

```bash
npm publish -w @craftline/mind --access public
```
