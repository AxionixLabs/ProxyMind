# Platform SDK 升级计划

状态：规划中

本计划只描述平台级 SDK 的阶段目标、出口和验收证据。系统职责、依赖方向、状态所有权和
生命周期以 `ARCHITECTURE.md` 为准；线上字段和事件以服务端正式契约及 `protocol/` 为准。
本文件不复制上述文档，也不记录逐次迁移过程。

## 目标

将当前面向单一客户端进程的 Agent Harness 演进为可供 TUI、桌面端、Web、MCP 和第三方
宿主复用的平台 SDK，同时保持以下约束：

- 消费者只依赖稳定的 `sdk/` 公开 API，不导入 `agent`、`infrastructure`、`frontends` 或具体第三方 SDK；
- SDK 同时支持远程协议连接和进程内 Harness，二者共享命令、事件、身份和结果模型；
- Session、Turn、Run、Tool、Approval、Effect 和游标的所有权仍由各自运行时持有，SDK 只提供访问与协调契约；
- 前端只负责交互和渲染，不复制状态机、协议 reducer、重试或效果对账；
- 公共 API 可独立版本化、测试、打包和替换实现，不绑定 CLI/TUI 生命周期。

## 目标边界

```text
frontend / host
        -> sdk.api + sdk.client
              -> sdk.adapters.remote  -> protocol
              -> sdk.adapters.local   -> agent.application / agent.ports
                                          -> infrastructure
```

`sdk/` 是唯一面向平台消费者的公开入口。`protocol/` 继续负责 wire schema、传输和服务端
映射；`agent/` 继续负责本地 Harness；`composition.py` 只负责选择并组装具体实现。上述内部
包不得反向导入 `sdk/`，避免形成循环依赖或隐藏兼容层。

## 阶段

### S0 契约冻结

明确 SDK 的支持语言、同步/异步模型、版本策略、错误分类、关闭语义和兼容承诺；整理当前
`protocol` 与 `agent` 中可复用的稳定值，标出内部实现和禁止外泄的字段。

出口：公开对象清单、依赖图、版本规则和不兼容变更策略经架构评审；没有未归属的 facade 或
重复状态模型。

### S1 公共 API

建立非 UI 的 `sdk/` 公共包，提供最小的不可变模型和生命周期契约：Client、Session、Turn、
Event、Tool、Approval、Effect、错误和结果。公开 API 使用具名类型和显式关闭，不导出内部
Store、Actor、Adapter 或第三方对象。

出口：SDK 可独立导入；公开导出经过快照测试；无 `agent`、`infrastructure`、`frontends`
反向依赖；最小示例只使用 SDK 类型完成一次会话观察。

### S2 远程适配

将 `protocol/client`、attach/replay、事件游标、重试、审批快照和工具结果收束到 SDK 的
远程适配器。传输细节留在 adapter，SDK 只交付统一事件和稳定错误。

出口：断线恢复、重复事件、序号缺口、`turn.logical_settled`、审批恢复和确定性错误均有
契约测试；远程客户端不暴露原始 HTTP、SSE 或第三方载荷。

### S3 本地适配

为进程内 Harness 提供 SDK 本地适配器，将 `agent.application` 用例和 `agent.ports` 能力
映射到相同的 Client/Session/Turn 契约。组合根注入实现，SDK 不读取配置文件、不创建 UI、
不自行发现本地服务。

出口：同一 SDK 用例可以切换远程和本地实现；Session 单写者、取消、关闭、恢复和终态行为
保持一致；本地实现没有重复状态机或临时代理层。

### S4 能力扩展

统一 SDK 的 Tool、Approval、Effect、Attachment、exec_env、Hook 和 Sidecar 扩展点。能力
以注册表或具名 Provider 注入，结果在边界归一化，未知效果进入既有 reconciliation 路径。

出口：客户端工具、托管工具、审批和 Sidecar 均能通过 typed contract 使用；权限、效果账本、
内部日志和供应商对象不进入 SDK 公共模型或线上 payload。

### S5 前端迁移

按 TUI、CLI、Subscription、stdio MCP、桌面/Web 适配器的顺序迁移消费者，使前端只依赖
SDK 公共入口和展示投影。删除前端对内部 `agent.ports`、`protocol` transport、组合对象
和具体 infrastructure 的直接依赖。

出口：至少两种不同前端使用同一 SDK 事件和命令模型；同一 Turn 的去重、retry/presentation
替代、审批快照和结算展示结果一致；旧导入路径和同义 facade 删除。

### S6 发布与兼容

定义 SDK 的包元数据、版本兼容矩阵、变更日志和发布产物。Python wheel、源码分发和需要的
平台包只包含声明的 SDK 公共模块；npm、桌面和 Web 客户端通过各自 adapter 消费，不复制
Python 内部实现。

出口：干净环境可安装并导入 SDK；版本升级、旧客户端错误、缺少可选能力和关闭失败均有
明确结果；发布清单、散列和依赖方向检查通过。

## 阶段准入

- 当前阶段的公开契约、责任人和删除条件已写入 `ARCHITECTURE.md`；
- 上一阶段的核心成功路径和关键失败路径均有测试证据；
- 新增公开对象具有稳定 identity、可空性、错误和关闭语义；
- 旧路径只在仍有外部契约时保留，并记录明确删除阶段；否则同次变更删除；
- SDK、Harness、wire protocol、基础设施和前端之间没有反向依赖。

## 最终验收

平台 SDK 完成必须同时满足：

- TUI、桌面/Web 和至少一个远程或本地宿主可以复用同一 SDK 契约；
- 命令、事件、游标、工具结果、审批和效果对账在不同实现间语义一致；
- 前端不直接导入内部 Harness、Transport、Store、第三方 SDK 或配置路径；
- 公共 API、版本策略、错误分类和关闭顺序有契约测试；
- 架构边界审计、受影响行为测试、语法检查和发布产物验证均通过。
