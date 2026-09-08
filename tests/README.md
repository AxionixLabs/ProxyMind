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

- `scenarios/turns.py`：Turn 场景、事实记录和稳定不变量；
- `fakes/mind_chat.py`：可控协议故障与请求记录；
- `pty/`：跨平台 PTY、终端输入和渲染验收设施；
- `fixtures/`：无行为的正式协议、配置和展示输入。

公共 helper 只在至少三个测试模块共享稳定概念，或重复已经造成契约不一致时提取。fake 只实现
它声明的端口，不能复制生产状态机来计算期望结果。禁止新增 `support/`、`utils/`、`common/`
等通用收纳模块；仓库路径通过根 `conftest.py` 的 pytest fixture 显式注入。

## 结构守护

`test_package_architecture.py` 持续审计以下测试架构边界：

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
python -m pytest tests/test_package_architecture.py -q
```

真实 PTY 或 ConPTY 使用 `-m pty_acceptance`，故障注入使用 `-m runtime_fault`，逻辑帧使用
`-m runtime_frame`，固定 seed 的状态长序列使用 `-m runtime_stateful`。平台测试必须在对应平台
执行，缺少平台不能视为该门禁通过。

## 新增测试检查

- 主要断言是否属于唯一责任所有者和同一稳定不变量；
- 是否覆盖核心成功路径和关键失败路径，而不是只验证 mock 调用；
- 外部字典是否先经过正式边界校验，未知字段是否明确失败；
- clock、transport、路径和进程能力是否从边界注入；
- task、socket、进程、PTY、sidecar 和临时资源是否由创建方完整关闭；
- 失败信息是否包含必要的身份、序号、阶段、seed 和观察事实；
- 是否可以通过稳定的最小 pytest 命令独立运行。
