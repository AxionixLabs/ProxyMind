# Agent Harness 第一方导入图

> 由 `scripts/agent_runtime_import_graph.py` 根据当前源码生成，请勿手工编辑。

## 范围

- 包含生产与构建边界：`agent`、`applications`、`build`、`infrastructure`、`metadata`、`mind`、`mind_app`、`mind_core`、`mind_npm`、`observability`、`protocol`、`server`、`setup`。
- 仅记录第一方边界之间的绝对 Python 导入；包内相对导入不展开。
- `backend/` 按独立打包边界单独验收，不纳入本图。
- `tests/`、`website/`、`schematic/`、`codex-main/` 和 `venv/` 不是运行时边界，不纳入本图。
- 根目录 `server/` 是客户端内置的 `ConfigServiceRuntime`，只提供配置 UI/健康检查；它不是 `mind.chat` 线上服务端，也不拥有 Harness 状态。

## 边界图

```mermaid
flowchart LR
    agent --> metadata
    agent --> protocol
    build --> infrastructure
    build --> metadata
    infrastructure --> agent
    infrastructure --> metadata
    infrastructure --> observability
    infrastructure --> protocol
    mind --> agent
    mind --> mind_app
    mind_app --> agent
    mind_app --> infrastructure
    mind_app --> metadata
    mind_app --> mind_core
    mind_app --> observability
    mind_app --> protocol
    mind_app --> server
    mind_core --> agent
    mind_core --> infrastructure
    mind_core --> metadata
    mind_core --> observability
    protocol --> metadata
    protocol --> observability
    server --> infrastructure
    server --> metadata
    server --> mind_core
    server --> observability
    setup --> metadata
```

## 直接跨边界依赖

| 源边界 | 目标边界 | 导入文件数 | 导入语句数 | 证据文件 |
| --- | --- | ---: | ---: | --- |
| `agent` | `metadata` | 1 | 1 | `agent/domain/hooks.py` |
| `agent` | `protocol` | 3 | 9 | `agent/adapters/protocol_client.py`<br>`agent/capabilities/environment.py`<br>`agent/domain/policies.py` |
| `build` | `infrastructure` | 1 | 2 | `build.py` |
| `build` | `metadata` | 1 | 1 | `build.py` |
| `infrastructure` | `agent` | 1 | 1 | `infrastructure/hooks/discovery.py` |
| `infrastructure` | `metadata` | 8 | 8 | `infrastructure/config/paths.py`<br>`infrastructure/config/trust.py`<br>`infrastructure/hooks/discovery.py`<br>`infrastructure/platform/encoding.py`<br>`infrastructure/platform/file_assist.py`<br>`infrastructure/services/licensing.py`<br>`infrastructure/services/remote_services.py`<br>`infrastructure/services/server_manager.py` |
| `infrastructure` | `observability` | 4 | 4 | `infrastructure/services/licensing.py`<br>`infrastructure/services/remote_services.py`<br>`infrastructure/services/server_manager.py`<br>`infrastructure/update/runtime.py` |
| `infrastructure` | `protocol` | 4 | 5 | `infrastructure/services/licensing.py`<br>`infrastructure/services/remote_services.py`<br>`infrastructure/services/server_manager.py`<br>`infrastructure/update/runtime.py` |
| `mind` | `agent` | 1 | 1 | `mind.py` |
| `mind` | `mind_app` | 1 | 1 | `mind.py` |
| `mind_app` | `agent` | 44 | 56 | `mind_app/cli/bootstrap.py`<br>`mind_app/cli/dispatch.py`<br>`mind_app/cli/entry.py`<br>`mind_app/client_tools/registry.py`<br>`mind_app/controller.py`<br>`mind_app/mcp/server.py`<br>`mind_app/native_coding/exec/process_session.py`<br>`mind_app/native_coding/native_coding.py`<br>`mind_app/presentation/run_views.py`<br>`mind_app/runtime/conversation.py`<br>`mind_app/runtime/environment/coding_lifecycle.py`<br>`mind_app/runtime/environment/snapshot.py`<br>`mind_app/runtime/execution.py`<br>`mind_app/runtime/hooks/catalog.py`<br>`mind_app/runtime/hooks/command.py`<br>`mind_app/runtime/hooks/compact.py`<br>`mind_app/runtime/hooks/effects.py`<br>`mind_app/runtime/hooks/events.py`<br>`mind_app/runtime/hooks/matching.py`<br>`mind_app/runtime/hooks/models.py`<br>`mind_app/runtime/hooks/protocol.py`<br>`mind_app/runtime/hooks/registry.py`<br>`mind_app/runtime/hooks/runtime.py`<br>`mind_app/runtime/hooks/scope.py`<br>`mind_app/runtime/hooks/session.py`<br>`mind_app/runtime/mcp/service_lifecycle.py`<br>`mind_app/runtime/subagents/context.py`<br>`mind_app/runtime/subagents/graph.py`<br>`mind_app/runtime/subagents/runtime.py`<br>`mind_app/runtime/subagents/thread.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/runtime/turns/root.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_effects.py`<br>`mind_app/runtime/turns/stream_model.py`<br>`mind_app/runtime/turns/stream_presentation.py`<br>`mind_app/subscription/forwarding.py`<br>`mind_app/subscription/runtime.py`<br>`mind_app/tui/features/conversation.py`<br>`mind_app/tui/features/permissions.py`<br>`mind_app/tui/session/loop.py`<br>`mind_app/tui/session/state.py`<br>`mind_app/tui/session/turn.py`<br>`mind_app/tui/session/turn_input.py` |
| `mind_app` | `infrastructure` | 39 | 54 | `mind_app/assets.py`<br>`mind_app/attach.py`<br>`mind_app/cli/bootstrap.py`<br>`mind_app/cli/dispatch.py`<br>`mind_app/cli/doctor.py`<br>`mind_app/cli/entry.py`<br>`mind_app/cli/frontend.py`<br>`mind_app/cli/mcp_registry.py`<br>`mind_app/cli/session_archive.py`<br>`mind_app/controller.py`<br>`mind_app/mcp/group.py`<br>`mind_app/mcp/server.py`<br>`mind_app/native_coding/exec/exec_policy.py`<br>`mind_app/native_coding/exec/sandbox_client.py`<br>`mind_app/native_coding/js_repl/runtime.py`<br>`mind_app/native_coding/native_coding.py`<br>`mind_app/paths.py`<br>`mind_app/runtime/environment/coding_lifecycle.py`<br>`mind_app/runtime/mcp/external.py`<br>`mind_app/runtime/mcp/keepalive.py`<br>`mind_app/runtime/mcp/service_lifecycle.py`<br>`mind_app/runtime/mcp/service_runtime.py`<br>`mind_app/runtime/subagents/runtime.py`<br>`mind_app/runtime/tools/enhancement/handlers.py`<br>`mind_app/runtime/turns/stream_setup.py`<br>`mind_app/subscription/forwarding.py`<br>`mind_app/tui/core/input.py`<br>`mind_app/tui/features/context.py`<br>`mind_app/tui/features/helix.py`<br>`mind_app/tui/features/listener.py`<br>`mind_app/tui/features/mailbox.py`<br>`mind_app/tui/features/model.py`<br>`mind_app/tui/features/skills.py`<br>`mind_app/tui/prompting/commands.py`<br>`mind_app/tui/prompting/skills.py`<br>`mind_app/tui/runtime/ports.py`<br>`mind_app/tui/session/barriers.py`<br>`mind_app/tui/session/dispatch.py`<br>`mind_app/tui/session/state.py` |
| `mind_app` | `metadata` | 49 | 49 | `mind_app/approval/permission_grants.py`<br>`mind_app/approval/policy.py`<br>`mind_app/cli/arguments.py`<br>`mind_app/cli/bootstrap.py`<br>`mind_app/cli/completion.py`<br>`mind_app/cli/doctor.py`<br>`mind_app/cli/frontend.py`<br>`mind_app/cli/invocation.py`<br>`mind_app/cli/parser.py`<br>`mind_app/frontend/sinks.py`<br>`mind_app/history/transcript.py`<br>`mind_app/mcp/config.py`<br>`mind_app/mcp/server.py`<br>`mind_app/native_coding/base.py`<br>`mind_app/native_coding/edit/diagnostics.py`<br>`mind_app/native_coding/edit/diff_render.py`<br>`mind_app/native_coding/edit/operations.py`<br>`mind_app/native_coding/edit/planning.py`<br>`mind_app/native_coding/encoding.py`<br>`mind_app/native_coding/exec/exec_policy.py`<br>`mind_app/native_coding/exec/execpolicy/parser.py`<br>`mind_app/native_coding/js_repl/runtime.py`<br>`mind_app/output/text.py`<br>`mind_app/paths.py`<br>`mind_app/presentation/code_highlight.py`<br>`mind_app/reporting.py`<br>`mind_app/runtime/agent/client.py`<br>`mind_app/runtime/hooks/command.py`<br>`mind_app/runtime/hooks/output_spill.py`<br>`mind_app/runtime/hooks/tool.py`<br>`mind_app/runtime/mcp/keepalive.py`<br>`mind_app/runtime/mcp/local.py`<br>`mind_app/runtime/mcp/service_exec_env.py`<br>`mind_app/runtime/support/clipboard.py`<br>`mind_app/runtime/support/session_policy.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/stream_events/approval_trace.py`<br>`mind_app/stream_io/output_record.py`<br>`mind_app/subscription/opening.py`<br>`mind_app/subscription/runtime.py`<br>`mind_app/tui/adapters/application.py`<br>`mind_app/tui/core/styles.py`<br>`mind_app/tui/features/conversation.py`<br>`mind_app/tui/features/helix.py`<br>`mind_app/tui/features/permissions.py`<br>`mind_app/tui/features/tools.py`<br>`mind_app/tui/features/transcript_export.py`<br>`mind_app/tui/rendering/screen/surfaces.py`<br>`mind_app/tui/session/turn.py` |
| `mind_app` | `mind_core` | 32 | 55 | `mind_app/cli/bootstrap.py`<br>`mind_app/cli/commands.py`<br>`mind_app/cli/dispatch.py`<br>`mind_app/cli/doctor.py`<br>`mind_app/cli/entry.py`<br>`mind_app/cli/frontend.py`<br>`mind_app/cli/invocation.py`<br>`mind_app/cli/mcp_registry.py`<br>`mind_app/controller.py`<br>`mind_app/mcp/registry.py`<br>`mind_app/mcp/server.py`<br>`mind_app/paths.py`<br>`mind_app/presentation/renderers/dispatch.py`<br>`mind_app/presentation/renderers/patch.py`<br>`mind_app/tui/adapters/application.py`<br>`mind_app/tui/adapters/presentation.py`<br>`mind_app/tui/core/activity.py`<br>`mind_app/tui/core/approval.py`<br>`mind_app/tui/core/approval_render.py`<br>`mind_app/tui/core/runtime.py`<br>`mind_app/tui/core/screen.py`<br>`mind_app/tui/core/status_frames.py`<br>`mind_app/tui/core/styles.py`<br>`mind_app/tui/core/viewport.py`<br>`mind_app/tui/features/context.py`<br>`mind_app/tui/features/history.py`<br>`mind_app/tui/features/hooks.py`<br>`mind_app/tui/features/model.py`<br>`mind_app/tui/features/skills.py`<br>`mind_app/tui/rendering/screen/terminal.py`<br>`mind_app/tui/session/dispatch.py`<br>`mind_app/tui/session/state.py` |
| `mind_app` | `observability` | 53 | 54 | `mind_app/approval/coordinator.py`<br>`mind_app/cli/bootstrap.py`<br>`mind_app/cli/dispatch.py`<br>`mind_app/cli/entry.py`<br>`mind_app/controller.py`<br>`mind_app/history/transcript.py`<br>`mind_app/mcp/group.py`<br>`mind_app/mcp/session_adapter.py`<br>`mind_app/mcp/tools.py`<br>`mind_app/native_coding/exec/process_session.py`<br>`mind_app/native_coding/exec/shell_exec.py`<br>`mind_app/reporting.py`<br>`mind_app/runtime/conversation.py`<br>`mind_app/runtime/environment/snapshot.py`<br>`mind_app/runtime/hooks/registry.py`<br>`mind_app/runtime/hooks/runtime.py`<br>`mind_app/runtime/hooks/session.py`<br>`mind_app/runtime/mcp/external.py`<br>`mind_app/runtime/mcp/keepalive.py`<br>`mind_app/runtime/mcp/local.py`<br>`mind_app/runtime/mcp/service_exec_env.py`<br>`mind_app/runtime/mcp/service_lifecycle.py`<br>`mind_app/runtime/mcp/service_runtime.py`<br>`mind_app/runtime/mcp/tool_runtime.py`<br>`mind_app/runtime/subagents/control.py`<br>`mind_app/runtime/subagents/delivery.py`<br>`mind_app/runtime/subagents/graph.py`<br>`mind_app/runtime/subagents/runner.py`<br>`mind_app/runtime/subagents/runtime.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/runtime/tools/enhancement/handlers.py`<br>`mind_app/runtime/tools/notify.py`<br>`mind_app/runtime/tools/plan_steps.py`<br>`mind_app/runtime/tools/run.py`<br>`mind_app/runtime/turns/executor.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_approval.py`<br>`mind_app/runtime/turns/stream_finalize.py`<br>`mind_app/runtime/turns/stream_setup.py`<br>`mind_app/runtime/turns/stream_tools.py`<br>`mind_app/stream_io/output_record.py`<br>`mind_app/subscription/external_access.py`<br>`mind_app/subscription/forwarding.py`<br>`mind_app/subscription/loop.py`<br>`mind_app/subscription/opening.py`<br>`mind_app/subscription/runtime.py`<br>`mind_app/subscription/status.py`<br>`mind_app/subscription/ws.py`<br>`mind_app/tui/features/conversation.py`<br>`mind_app/tui/prompting/files.py`<br>`mind_app/tui/session/barriers.py`<br>`mind_app/tui/session/turn.py`<br>`mind_app/tui/session/turn_input.py` |
| `mind_app` | `protocol` | 44 | 72 | `mind_app/approval/models.py`<br>`mind_app/approval/policy.py`<br>`mind_app/builtin_tools/permissions.py`<br>`mind_app/cli/bootstrap.py`<br>`mind_app/client_tools/coding/native.py`<br>`mind_app/controller.py`<br>`mind_app/mcp/server.py`<br>`mind_app/presentation/approval_views.py`<br>`mind_app/runtime/agent/client.py`<br>`mind_app/runtime/conversation.py`<br>`mind_app/runtime/execution.py`<br>`mind_app/runtime/mcp/local.py`<br>`mind_app/runtime/subagents/control.py`<br>`mind_app/runtime/subagents/delivery.py`<br>`mind_app/runtime/subagents/executor.py`<br>`mind_app/runtime/subagents/graph.py`<br>`mind_app/runtime/subagents/mailbox.py`<br>`mind_app/runtime/subagents/runner.py`<br>`mind_app/runtime/subagents/runtime.py`<br>`mind_app/runtime/subagents/thread.py`<br>`mind_app/runtime/support/conversation.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/runtime/tools/enhancement/handlers.py`<br>`mind_app/runtime/tools/run.py`<br>`mind_app/runtime/turns/event_reporting.py`<br>`mind_app/runtime/turns/executor.py`<br>`mind_app/runtime/turns/root.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_approval.py`<br>`mind_app/runtime/turns/stream_effects.py`<br>`mind_app/runtime/turns/stream_model.py`<br>`mind_app/runtime/turns/stream_outcome.py`<br>`mind_app/runtime/turns/stream_presentation.py`<br>`mind_app/runtime/turns/stream_setup.py`<br>`mind_app/runtime/turns/stream_tools.py`<br>`mind_app/stream_events/assistant_boundary.py`<br>`mind_app/stream_events/lifecycle.py`<br>`mind_app/subscription/runtime.py`<br>`mind_app/subscription/ws.py`<br>`mind_app/tui/core/queued.py`<br>`mind_app/tui/features/conversation.py`<br>`mind_app/tui/session/loop.py`<br>`mind_app/tui/session/turn.py`<br>`mind_app/tui/session/turn_input.py` |
| `mind_app` | `server` | 3 | 3 | `mind_app/cli/bootstrap.py`<br>`mind_app/subscription/external_access.py`<br>`mind_app/tui/session/dispatch.py` |
| `mind_core` | `agent` | 2 | 4 | `mind_core/config.py`<br>`mind_core/config_layers.py` |
| `mind_core` | `infrastructure` | 5 | 8 | `mind_core/config.py`<br>`mind_core/config_layers.py`<br>`mind_core/config_session.py`<br>`mind_core/config_store.py`<br>`mind_core/preference.py` |
| `mind_core` | `metadata` | 5 | 5 | `mind_core/config_layers.py`<br>`mind_core/config_store.py`<br>`mind_core/design/terminal_progress.py`<br>`mind_core/preference.py`<br>`mind_core/service_config.py` |
| `mind_core` | `observability` | 2 | 2 | `mind_core/preference.py`<br>`mind_core/service_config.py` |
| `protocol` | `metadata` | 1 | 1 | `protocol/transport/config.py` |
| `protocol` | `observability` | 3 | 3 | `protocol/client/manifest.py`<br>`protocol/transport/events.py`<br>`protocol/transport/streaming.py` |
| `server` | `infrastructure` | 2 | 3 | `server/lifecycle.py`<br>`server/storage.py` |
| `server` | `metadata` | 2 | 2 | `server/page.py`<br>`server/routers/basic.py` |
| `server` | `mind_core` | 5 | 7 | `server/app.py`<br>`server/lifecycle.py`<br>`server/routers/pref.py`<br>`server/routers/services.py`<br>`server/storage.py` |
| `server` | `observability` | 1 | 1 | `server/lifecycle.py` |
| `setup` | `metadata` | 1 | 1 | `setup.py` |

## 循环与反向依赖

### 跨边界循环

- 未发现跨边界循环。

### `engine` 残留引用

- 未发现 `engine` 残留导入；旧源包已删除。

## 复核命令

```powershell
.\venv\Scripts\python.exe scripts\agent_runtime_import_graph.py --check
```
