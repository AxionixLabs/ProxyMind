# NPM 发布命令

## 配置 Token

```bash
npm login
```

```bash
npm config set //registry.npmjs.org/:_authToken "npm_xxx_your_token"
```

## 验证登录和版本

```bash
npm whoami
```

```bash
npm config get registry
```

```bash
npm view @proxymind/mind version
```

```bash
npm view @proxymind/mind-win32 version
```

```bash
npm view @proxymind/mind-darwin version
```

```bash
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

进入 npm 工作目录：

```bash
cd mind_npm
```

## 同步产物

```bash
npm run sync:applications
```

## Dry Run

```bash
npm publish -w @proxymind/mind-win32 --dry-run --access public
```

```bash
npm publish -w @proxymind/mind-darwin --dry-run --access public
```

```bash
npm publish -w @proxymind/mind --dry-run --access public
```

## 发布前本地安装测试

生成主包 tarball：

```bash
npm pack -w @proxymind/mind
```

创建临时测试目录：

```bash
rm -rf ./tmp-npm-test
```

```bash
mkdir -p ./tmp-npm-test
```

进入临时测试目录：

```bash
cd ./tmp-npm-test
```

初始化测试项目：

```bash
npm init -y
```

安装本地 tarball：

```bash
npm install ../proxymind-mind-1.0.0.tgz
```

验证命令入口：

```bash
npx mind --help
```

卸载测试包：

```bash
npm uninstall @proxymind/mind
```

回到发布目录：

```bash
cd ..
```

清理临时测试目录：

```bash
rm -rf ./tmp-npm-test
```

## 发布

```bash
npm publish -w @proxymind/mind-win32 --access public
```

```bash
npm publish -w @proxymind/mind-darwin --access public
```

```bash
npm publish -w @proxymind/mind --access public
```
