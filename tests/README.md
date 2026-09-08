# ProxyMind 测试

测试集使用 pytest 作为执行内核，目录按 `ARCHITECTURE.md` 中的稳定责任所有者组织，marker
只表达跨目录的风险门禁或环境要求。本文是测试归属、测试设施和执行方式的唯一长期说明。

## 归属规则

1. 单一生产责任的测试镜像到对应顶层包和最近的稳定职责目录。
2. 同一顶层包内的跨层用例归到拥有该生命周期的 application 或 harness。
3. 必须组合两个以上顶层所有者才能表达主要事实的测试归入 `integration/`。
4. 根组合和资源 owner 归入 `composition/`，构建与发布布局归入 `distribution/`。
5. 人工连接真实服务、设备或账号的脚本归入 `manual/`，且文件名不以 `test_` 开头。
6. `test_package_architecture.py` 是稳定的根级架构审计入口，不移动、不删除。
7. 客户端测试不直接验证独立 `backend/` 服务包，但保留客户端禁止依赖 backend 的边界审计。

目录不用于表达 unit、integration、slow 或平台属性。新增 marker 前必须先有实际的测试选择命令
或 CI 消费者。

责任目录迁移已经完成。`tests/` 根目录只保留全树配置、本文和稳定架构审计入口；测试分别归入
`agent/`、`protocol/`、`frontends/`、`infrastructure/`、`observability/`、`distribution/`、
`composition/` 与 `integration/`。测试设施归入 `scenarios/`、`fakes/`、`pty/`、`fixtures/`，
人工联调入口归入 `manual/`。不得为了清空目录把跨层测试随意塞入单一生产责任目录。

| 目录 | 主要责任 |
|------|----------|
| `agent/` | domain、application、harness、stores、capabilities 与 adapters |
| `protocol/` | schema、transport 与独立 client 契约 |
| `frontends/` | CLI、MCP、subscription、terminal、TUI 及输出边界 |
| `infrastructure/` | config、hooks、MCP、persistence、platform、services、sidecars、skills 与 update |
| `observability/` | 运行报告、结构化观察与记录边界 |
| `distribution/` | build、npm 与发布布局 |
| `composition/` | 根组合入口和进程资源所有权 |
| `integration/` | 必须组合两个以上顶层所有者才能成立的客户端用例 |
| `architecture/` | 从根稳定入口拆出的专题源码与依赖审计 |

## 测试设施

- `scenarios/turns.py`：Turn 场景、确定性组合选择和 Runtime 稳定不变量；
- `scenarios/frames.py`：逻辑 frame 事实与跨帧原子性断言；
- `fakes/mind_chat.py`：不可变故障计划、正式命令端口和请求/事件事实记录；
- `pty/`：跨平台 PTY、终端输入和渲染验收设施；
- `fixtures/`：无行为的正式协议、配置和展示输入；
- `frontends/tui/rendering/frame_scenarios.py`：TUI frame 输入构造、同步等待与可见事实读取；
- `architecture/source_inventory.py`：架构审计专用的源码清单和进程内 AST 缓存。

公共 helper 只在至少三个测试模块共享稳定概念，或重复已经造成契约不一致时提取。fake 只实现
它声明的端口，不能复制生产状态机来计算期望结果。禁止新增 `support/`、`utils/`、`common/`
等通用收纳模块；仓库路径通过根 `conftest.py` 的 pytest fixture 显式注入。

Turn 场景使用调用方提供的固定 seed，并在失败信息中输出 seed 与完整 trace。Runtime trace 包含
Turn/epoch、事件序号、cursor、输入 owner、lease、终态和执行门状态；Fake Server trace 包含命令
请求身份、Turn、事件序号、输入 owner 和工具结果状态。Fake Server 不创建后台 task、socket 或
进程；PTY 资源由 context manager 关闭。场景与 Fake 使用 event 或零延迟让步同步，真实 PTY、
sidecar 和终端时序可以保留带明确截止时间的轮询。

## 结构守护

`test_package_architecture.py` 保留全仓入口与基础依赖方向，`architecture/` 承载 Agent、基础设施、
前端和组合根的专题审计。两者共享同一源码清单与 AST 缓存，并共同审计以下测试架构边界：

- `tests/` 根目录只允许 `conftest.py`、`README.md` 和稳定架构审计入口；
- 不允许新增 `support/`、`utils/`、`common/` 等通用测试包；
- 客户端测试不得直接导入独立 `backend` 服务实现；
- 可迁移测试不得通过 `Path(__file__).parents[...]` 推导仓库根。

这些规则属于发布收口边界。普通业务迭代只运行受影响目录；目录、依赖方向或发布门禁变化时才
运行完整架构审计。

## 运行方式

先激活仓库虚拟环境，再运行最小受影响集合：

```shell
python -m pytest <targets> -q
python -m pytest tests/agent -q
python -m pytest tests/protocol -q
python -m pytest tests/infrastructure -q
python -m pytest tests/distribution -q
python -m pytest tests/frontends -m "not pty_acceptance" -q
python -m pytest tests/integration -m "not pty_acceptance" -q
python -m pytest --collect-only -q
python -m pytest -m runtime_p0 -q
python -m pytest tests/test_package_architecture.py tests/architecture -q
```

真实 PTY 或 ConPTY 使用 `-m pty_acceptance`，故障注入使用 `-m runtime_fault`，逻辑帧使用
`-m runtime_frame`，固定 seed 的状态长序列使用 `-m runtime_stateful`。平台测试必须在对应平台
执行，缺少平台不能视为该门禁通过。

## CI 门禁

`.github/workflows/tests.yml` 使用 Python 3.11，并把责任目录与执行属性保持正交：

| job | 触发 | 选择 | 职责 |
|-----|------|------|------|
| `fast` | PR、main | `-m "not pty_acceptance"` | 快速全量合并门禁 |
| `runtime-p0` | PR、main | `-m runtime_p0` | Runtime 核心风险独立门禁 |
| `platform` | 全部 | 三平台的 `infrastructure/platform` 与 `pty_acceptance` | 平台 adapter 和真实终端 |
| `full-regression` | 夜间、手动、版本标签 | 完整测试树 | 完整回归、最慢 50 项和 JUnit |
| `release-gate` | 版本标签 | 架构审计、compileall、差异检查 | 汇总完整回归与三平台结果后收口 |

每个 pytest job 都上传 JUnit；PR 报告保留 14 天，完整回归保留 30 天。`runtime_p0` 与快速全量
有意重叠，因为前者是可单独要求的风险门禁。源码仓不包含 macOS sandbox 可执行产物；对应的
sidecar 集成测试只在外部产物流水线提供 `MIND_SANDBOX_SERVER` 时成立，不计入源码 CI 的 macOS
通过结论。当前不启用 pytest-xdist：进程环境、固定端口和跨进程资产尚未完成并行隔离证明。

## 新增测试检查

- 主要断言是否属于唯一责任所有者和同一稳定不变量；
- 是否覆盖核心成功路径和关键失败路径，而不是只验证 mock 调用；
- 外部字典是否先经过正式边界校验，未知字段是否明确失败；
- clock、transport、路径和进程能力是否从边界注入；
- task、socket、进程、PTY、sidecar 和临时资源是否由创建方完整关闭；
- 失败信息是否包含必要的身份、序号、阶段、seed 和观察事实；
- 是否可以通过稳定的最小 pytest 命令独立运行。
- 超过约 1500 行时是否仍只覆盖一个状态机或不变量矩阵，并在模块顶部说明保持整体的原因。
