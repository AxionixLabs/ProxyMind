# Agents

本文件供自动化编码代理使用。开始修改前先阅读本文件，再阅读相关代码和测试。
仅记录全仓库长期有效的规则；特定领域的约束应放入对应目录的局部 `AGENTS.md`。

## 工作方式

- 先理解现有实现和调用链，再做小范围、可验证的修改。
- 不做无关重构，不保留无意义兼容层或只转发一次调用的 facade。
- 优先复用现有模块、契约和生命周期；只有边界或状态所有权明确时才拆模块。
- 避免含义不清的布尔值、`None` 和数字位置参数；优先使用关键字参数、枚举或具名类型。
- 不使用 `global`、`nonlocal`、`in locals()` 或 mixin。
- 路径、shell 和子进程改动默认兼容 Windows、Linux、macOS；平台专用行为必须显式隔离。

## 命名与文档

- `Mind`/`mind` 是既有品牌和包名，不要扩展为新的领域语义。
- 新增的类、函数、方法、属性和常量按实际职责命名，不包含 `Mind` 或 `mind`。
- 展示字符串中的应用名称引用 `mind_nova.const.APP_NAME` 或 `APP_DESC`，不要硬编码。
- 既有包路径、稳定入口和外部契约中的 `Mind`/`mind` 保持不变。
- 非测试函数的 docstring 使用中文中性描述；新增 `Protocol`、ABC 或跨层契约时，
  说明其职责、生命周期和实现方约束。

## 架构边界

```text
mind.py -> mind_app.cli -> mind_app.controller
                         -> mind_app.runtime / subscription / tui / output
mind_app -> mind_core -> mind_nova
```

- `mind_app` 是主程序控制和运行侧，可以依赖 `mind_core`、`mind_nova`。
- `mind_core` 持有配置、偏好和共享设计，可以依赖 `mind_nova`，不得导入 `mind_app`。
- `mind_nova` 只持有协议、传输、认证、事件和标识，不读取本地配置或 skills，
  不得导入 `mind_core` 或 `mind_app`。
- `backend` 只能依赖自身、标准库和第三方库；其他包不得导入 `backend`。
- `engine.ports` 负责本地端口探测和进程清理，不放入 `mind_nova`。
- CLI 是组合根；控制器、runtime 和 subscription 不判断具体输出前端。
- 保持公共 API 精简，不为测试扩大生产模块的公开接口；测试辅助函数放在测试代码中。

## 测试

- 使用 pytest 风格；异步测试使用 pytest 的异步标记，mock 可以使用 `unittest.mock`。
- 测试优先比较完整对象，不为静态定义值、已删除逻辑或低价值实现细节机械增加用例。
- 新增功能或复杂行为变化覆盖核心主流程和关键失败路径；小改动不机械新增测试。
- 测试不要直接修改进程环境；优先从上层传入环境派生值或依赖。
- 先运行受影响模块的定向测试；修改共享配置、协议、控制器或公共契约时再扩大范围。
- Windows 下使用仓库虚拟环境运行测试：`.\venv\Scripts\python.exe -m pytest`，不要直接调用系统 `pytest`。

## 验证

- 修改后运行与影响范围匹配的测试，并执行 `python -m py_compile` 或等价语法检查。
- 根目录提供 PyCharm 离线检查脚本，可在需要排查 IDE warning 时按需使用；
  该工具不属于每次修改的强制验证步骤：

```powershell
.\inspect-ide-warnings.ps1 mind_app/tui/core/runtime.py
```

- 参数可以是项目内文件或目录；默认使用 `.idea/inspectionProfiles/Project_Default.xml`。
- 报告写入 `.ide-inspection/reports/<模块名>`，原始输出写入
  `.ide-inspection/inspect.log`，索引和 IDE 日志位于 `.ide-inspection/system`。
- 需要查看完整启动输出时使用 `-ShowInspectorOutput`：

```powershell
.\inspect-ide-warnings.ps1 mind_app/tui/core/runtime.py -ShowInspectorOutput
```

- 脚本只清理当前报告的旧 XML，不修改 `venv` 或项目配置；检查失败时查看
  `.ide-inspection/inspect.log`。

## 编辑与安全

- 搜索优先使用 `rg`；结构化数据使用解析器或项目现有 API，不做脆弱的字符串拼接。
- 使用 `apply_patch` 编辑文件，不用 shell 重定向或临时脚本覆盖源码。
- 不使用 `# noinspection PyBroadException` 屏蔽检查；应收窄异常类型，或通过不吞异常的生命周期清理保证状态收敛。
- 保留用户已有改动，不回退无关文件；不要使用破坏性的 `git reset --hard` 或 `git checkout --`。
