---
name: webapp-testing
description: 用于本地 Web 应用 web app 的交互与测试 testing，优先使用当前会话中的 Playwright MCP 或 browser tools，支持验证前端功能、调试 UI 行为、截取 screenshot，以及查看 console log 和 network request。Mind 需要测试或排查本地网页应用时使用；仅在 MCP 不可用且明确需要原生回退时编写 Python Playwright 脚本。
---

# Web 应用测试

测试本地 Web 应用时，优先使用当前会话已经连接的 Playwright MCP 浏览器工具。MCP 可用时，直接调用工具完成页面导航、结构读取、交互、截图和日志检查，不导入 Python `playwright`，不生成原生 Playwright 脚本，也不在客户端安装浏览器依赖。

## 执行路径

先查看当前会话提供的工具，再选择路径。不要仅凭配置文件判断 MCP 可用；只有相关浏览器工具已经出现在当前工具列表中，才视为连接成功。工具名可能带有 `mcp__playwright__` 前缀，也可能使用其他服务命名，应以工具说明和参数结构为准。

```text
当前会话有 Playwright MCP 或 browser tools？
├── 有：直接使用 MCP 工具
│   ├── 页面已运行：进入“观察后操作”流程
│   └── 页面未运行：先按项目已有命令启动应用，再使用 MCP
└── 没有：是否明确需要原生 Python Playwright 回退？
    ├── 是：确认依赖已安装，再使用 examples/ 和 scripts/ 中的资源
    └── 否：提示连接 Playwright MCP，不盲目导包或安装依赖
```

## MCP 主流程

遵循“观察后操作”原则：

1. 打开目标 URL，并等待页面达到可交互状态。
2. 读取页面快照、可访问性树或 DOM 结构。
3. 从渲染后的页面识别按钮、链接、输入框和其他目标元素。
4. 使用 MCP 返回的元素引用或稳定语义定位执行点击、输入、选择、上传等动作。
5. 等待导航、请求、元素状态或页面内容稳定，不依赖固定时长猜测。
6. 再次读取页面结构，验证预期结果。
7. 按需截取 screenshot，读取 console log 或检查 network request，形成可核验结果。

优先使用角色、标签、可访问名称和 MCP 返回的元素引用。只有页面缺少稳定语义时才退回 CSS selector；不要根据未验证的页面结构盲目点击。

## 本地服务

Playwright MCP 负责浏览器交互，不负责自动启动待测 Web 应用。测试前先确认目标 URL 可以访问：

- 应用已运行时，直接使用 MCP 打开页面。
- 应用未运行时，优先使用项目已有的启动命令和文档。
- 不确定启动方式时，检查 `package.json`、项目说明或现有测试配置，不自行猜测命令。
- MCP 主路径不要用 `scripts/with_server.py` 包裹浏览器操作，因为 MCP 调用不是该脚本能够管理的子命令。

## 静态 HTML

测试静态 HTML 时，先直接读取文件以理解结构和定位元素。需要实际交互时：

- Playwright MCP 支持 `file://` 时，可以直接打开本地文件。
- MCP 不允许本地文件协议时，使用项目现有方式或轻量本地服务暴露该目录，再通过 HTTP 打开。
- 不要仅凭源文件断言动态行为；涉及 JavaScript 时仍需读取渲染后的页面状态。

## 常见测试任务

### 功能验证

- 执行关键用户流程，并在每个关键动作后验证页面状态。
- 表单提交后等待成功提示、URL 变化、请求完成或目标元素更新。
- 同时检查可见结果与必要的控制台、网络证据。

### UI 调试

- 操作前先读取页面结构，确认实际可交互元素。
- 出现异常时，依次检查页面状态、console log 和 network request。
- 需要视觉证据时截取完整页面或目标区域 screenshot。

### 响应式检查

- 使用 MCP 提供的 viewport 或窗口调整能力切换桌面和移动尺寸。
- 调整尺寸后重新读取结构并截图，不沿用旧页面引用。

## 原生 Python 回退

只有当前会话没有可用的 Playwright MCP，并且用户明确需要原生 Playwright 或环境已经具备相关依赖时，才使用 Python 回退。

回退时遵守以下规则：

1. 先确认 Python `playwright` 和浏览器二进制已经可用，不擅自安装依赖。
2. 需要同时管理应用服务器和自动化子命令时，先运行：

   ```bash
   python scripts/with_server.py --help
   ```

3. 把 `scripts/with_server.py` 当作黑盒使用。只有帮助信息无法满足需求且确实需要定制时才读取源码。
4. 仅在回退路径中参考 `examples/`：
   - `element_discovery.py`：发现页面中的按钮、链接和输入框
   - `static_html_automation.py`：通过 `file://` 操作本地 HTML
   - `console_logging.py`：在自动化过程中捕获控制台日志
5. 等待动态页面完成加载或目标状态出现，结束时关闭浏览器。

单服务回退示例：

```bash
python scripts/with_server.py --server "npm run dev" --port 5173 -- python your_automation.py
```

多服务回退示例：

```bash
python scripts/with_server.py \
  --server "cd backend && python server.py" --port 3000 \
  --server "cd frontend && npm run dev" --port 5173 \
  -- python your_automation.py
```

## 常见问题

- **MCP 已写入配置但工具不可见**：配置不代表当前会话已连接；先启动服务并重新建立外接 MCP 会话。
- **动态页面结构不完整**：等待页面稳定后重新获取快照，不要继续使用旧元素引用。
- **交互后没有变化**：检查动作是否命中正确元素，再查看控制台和网络请求。
- **原生回退导包失败**：不要在任务中临时安装；优先恢复 Playwright MCP，或明确向用户报告缺少依赖。
- **服务器无法访问**：先修复应用启动或端口问题，再排查浏览器自动化。

## 最佳实践

- MCP 可用时始终优先使用 MCP，不编写重复的浏览器驱动代码。
- 每次重要交互前后都读取页面状态，形成可验证闭环。
- 优先等待具体状态，避免用固定延时掩盖竞态问题。
- 截图用于视觉证据，页面结构用于精确判断；两者按需结合。
- 测试完成后关闭不再需要的页面或浏览器会话，避免污染后续任务。
