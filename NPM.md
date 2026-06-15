# NPM 发布命令

```bash
cd mind_npm
```

## 配置 Token

```bash
npm login
npm config set //registry.npmjs.org/:_authToken "npm_xxx_your_token"
```

## 验证登录和版本

```bash
npm whoami
npm config get registry
npm view @proxymind/mind version
npm view @proxymind/mind-win32 version
npm view @proxymind/mind-darwin version
npm pkg get version --workspaces
```

## 升级版本

需要同步的文件：

```text
packages/mind/package.json
packages/mind-win32/package.json
packages/mind-darwin/package.json
packages/mind/package.json optionalDependencies
```

## 同步产物

```bash
npm run sync:applications
```

## Dry Run

```bash
npm publish -w @proxymind/mind-win32 --dry-run --access public
npm publish -w @proxymind/mind-darwin --dry-run --access public
npm publish -w @proxymind/mind --dry-run --access public
```

## 发布

```bash
npm publish -w @proxymind/mind-win32 --access public
npm publish -w @proxymind/mind-darwin --access public
npm publish -w @proxymind/mind --access public
```
