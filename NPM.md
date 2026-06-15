# NPM 分平台发布

采用 npm workspace 分三包发布：

```text
@proxymind/mind         主包，只包含 Node launcher
@proxymind/mind-win32   Windows 运行时
@proxymind/mind-darwin  macOS 运行时
```

用户只安装：

```bash
npm install -g @proxymind/mind
```

## 目录结构

```text
mind_npm/
  package.json                  # workspace root，不发布
  scripts/
    sync-applications.js
    require-runtime.js
  packages/
    mind/                       # @proxymind/mind
      bin/mind.js
      package.json
    mind-win32/                 # @proxymind/mind-win32
      applications/
        MindEngine/mind.exe
        mind.bat
        Structure/
      package.json
    mind-darwin/                # @proxymind/mind-darwin
      applications/
        Mind.app/Contents/MacOS/mind
      package.json
```

## 产物同步

先确保根目录已有构建产物：

```text
applications/
  MindEngine/
    mind.exe
  mind.bat
  Structure/
  Mind.app/
```

同步到平台包：

```bash
cd mind_npm
npm run sync:applications
```

同步关系：

```text
applications/MindEngine -> packages/mind-win32/applications/MindEngine
applications/mind.bat   -> packages/mind-win32/applications/mind.bat
applications/Structure  -> packages/mind-win32/applications/Structure
applications/Mind.app   -> packages/mind-darwin/applications/Mind.app
```

## 版本同步

三个包版本必须一致：

```text
packages/mind/package.json
packages/mind-win32/package.json
packages/mind-darwin/package.json
```

主包的 `optionalDependencies` 也要同步到同一版本：

```json
{
  "optionalDependencies": {
    "@proxymind/mind-win32": "1.0.1",
    "@proxymind/mind-darwin": "1.0.1"
  }
}
```

如需同步应用显示版本，修改：

```text
mind_nova/const.py
```

## 发布前检查

```bash
npm whoami
npm config get registry
npm ping
```

确认：

```text
账号：acekeppel
registry：https://registry.npmjs.org/
组织：acekeppel 对 proxymind 有发布权限
```

## Dry Run

```bash
cd mind_npm
npm run sync:applications
npm publish -w @proxymind/mind-win32 --dry-run --access public
npm publish -w @proxymind/mind-darwin --dry-run --access public
npm publish -w @proxymind/mind --dry-run --access public
```

平台包带 `prepublishOnly` 校验：

```text
Windows 必须存在：
  applications/MindEngine/mind.exe
  applications/mind.bat
  applications/Structure

macOS 必须存在：
  applications/Mind.app/Contents/MacOS/mind
```

缺文件时 dry-run/publish 失败是正确行为。

## 正式发布

必须先发布平台包，再发布主包：

```bash
cd mind_npm
npm publish -w @proxymind/mind-win32 --access public
npm publish -w @proxymind/mind-darwin --access public
npm publish -w @proxymind/mind --access public
```

如果 npm 要求 OTP：

```bash
npm publish -w @proxymind/mind --access public --otp 123456
```

把 `123456` 替换为当前 6 位验证码。

## 安装验证

```bash
npm install -g @proxymind/mind
mind --upgrade
mind
```

`--upgrade` 跳过 npm 应用更新检测，继续走内置后端升级流程。

## 自动更新

launcher 检查主包版本：

```bash
npm view @proxymind/mind version
```

用户选择更新时执行：

```bash
npm install -g @proxymind/mind
```

跳过条件：

```text
MIND_NO_UPDATE_CHECK=1
stdin/stdout 不是 TTY
命令包含 --upgrade
距离上次检查不足 24 小时
已选择 Skip until next version
```

