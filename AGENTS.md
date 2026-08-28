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

## 当前架构边界

以下是 Agent Runtime 迁移完成前的当前生产依赖边界，不是目标目录声明。

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

### Agent Runtime 迁移规则

- `AGENT_RUNTIME_ARCHITECTURE.md` 只定义目标架构；阶段状态、准入条件和
  完成证据只以 `AGENT_RUNTIME_MIGRATION.md` 为准。
- 阶段 1 开始前，新生产代码继续遵守上述现有包边界；不得提前创建
  未接入主链路的空 `agent` 目录或一次性搬迁历史模块。
- 只有当迁移计划将相应阶段标为“进行中”时，才能新增该阶段所需的
  `agent` 模块；每次新增必须接入一个完整用例并具有删除旧路径的条件。
- `agent.protocol` 和 `agent.domain` 不得导入 `mind_app`、`mind_core`、
  `mind_nova`、`engine` 或具体网络客户端。`agent.runtime` 和
  `agent.application` 只依赖 `agent` 内部协议、领域与端口。
- 迁移期的 `agent.adapters`、`agent.capabilities` 或 `agent.stores` 如需复用
  旧实现，必须在迁移计划登记临时导入、删除条件和最晚阶段；
  旧包不得导入 `agent` 内部模块，只能调用明确的 application 公开入口。

## 测试

- 使用 pytest 风格；异步测试使用 pytest 的异步标记，mock 可以使用 `unittest.mock`。
- 测试优先比较完整对象，不为静态定义值、已删除逻辑或低价值实现细节机械增加用例。
- 新增功能或复杂行为变化覆盖核心主流程和关键失败路径；小改动不机械新增测试。
- 测试不要直接修改进程环境；优先从上层传入环境派生值或依赖。
- 先运行受影响模块的定向测试；修改共享配置、协议、控制器或公共契约时再扩大范围。
- Windows 下使用仓库虚拟环境运行测试：`.\venv\Scripts\python.exe -m pytest`，不要直接调用系统 `pytest`。

## 验证

- 修改后运行与影响范围匹配的测试，并执行 `python -m py_compile` 或等价语法检查。

## 编辑与安全

- 搜索优先使用 `rg`；结构化数据使用解析器或项目现有 API，不做脆弱的字符串拼接。
- 使用 `apply_patch` 编辑文件，不用 shell 重定向或临时脚本覆盖源码。
- 不使用 `# noinspection PyBroadException` 屏蔽检查；应收窄异常类型，或通过不吞异常的生命周期清理保证状态收敛。
- 保留用户已有改动，不回退无关文件；不要使用破坏性的 `git reset --hard` 或 `git checkout --`。
