# Agent Harness 第一方导入图

> 由 `scripts/agent_runtime_import_graph.py` 根据当前源码生成，请勿手工编辑。

## 范围

- 包含生产与构建边界：`agent`、`applications`、`build`、`frontends`、`infrastructure`、`metadata`、`mind`、`mind_app`、`mind_core`、`mind_npm`、`observability`、`protocol`、`server`、`setup`。
- 仅记录第一方边界之间的绝对 Python 导入；包内相对导入不展开。
- `backend/` 按独立打包边界单独验收，不纳入本图。
- `tests/`、`website/`、`schematic/`、`codex-main/` 和 `venv/` 不是运行时边界，不纳入本图。
- 根目录 `server/` 是客户端内置的 `ConfigServiceRuntime`，只提供配置 UI/健康检查；它不是 `mind.chat` 线上服务端，也不拥有 Harness 状态。

## 边界图

```mermaid
flowchart LR
    agent --> metadata
    agent --> observability
    agent --> protocol
    build --> infrastructure
    build --> metadata
    frontends --> agent
    frontends --> infrastructure
    frontends --> metadata
    frontends --> mind_app
    frontends --> observability
    frontends --> protocol
    frontends --> server
    infrastructure --> agent
    infrastructure --> metadata
    infrastructure --> observability
    infrastructure --> protocol
    mind --> agent
    mind --> frontends
    mind --> infrastructure
    mind --> mind_app
    mind_app --> agent
    mind_app --> frontends
    mind_app --> infrastructure
    mind_app --> metadata
    mind_app --> observability
    mind_app --> protocol
    observability --> metadata
    protocol --> metadata
    protocol --> observability
    server --> infrastructure
    server --> metadata
    server --> observability
    setup --> metadata
```

## 直接跨边界依赖

| 源边界 | 目标边界 | 导入文件数 | 导入语句数 | 证据文件 |
| --- | --- | ---: | ---: | --- |
| `agent` | `metadata` | 4 | 4 | `agent/application/approvals/policy.py`<br>`agent/application/turns/exception_text.py`<br>`agent/domain/hooks.py`<br>`agent/stores/approvals/permissions.py` |
| `agent` | `observability` | 7 | 7 | `agent/adapters/agents/messages.py`<br>`agent/composition.py`<br>`agent/harness/agents/control.py`<br>`agent/harness/execution/subagent_runner.py`<br>`agent/harness/hooks/registry.py`<br>`agent/harness/hooks/runtime.py`<br>`agent/stores/agents/graph.py` |
| `agent` | `protocol` | 25 | 40 | `agent/adapters/agents/execution.py`<br>`agent/adapters/agents/messages.py`<br>`agent/adapters/protocol/client.py`<br>`agent/application/agents/thread.py`<br>`agent/application/approvals/models.py`<br>`agent/application/approvals/policy.py`<br>`agent/application/turns/context.py`<br>`agent/application/turns/execution.py`<br>`agent/application/turns/lifecycle.py`<br>`agent/application/turns/stream_boundaries.py`<br>`agent/application/views/approval.py`<br>`agent/application/views/builders/approval.py`<br>`agent/capabilities/environment.py`<br>`agent/domain/agents.py`<br>`agent/domain/policies.py`<br>`agent/harness/agents/delivery.py`<br>`agent/harness/execution/subagent_runner.py`<br>`agent/harness/execution/subagent_submission.py`<br>`agent/harness/sessions/conversation.py`<br>`agent/ports/agent_messages.py`<br>`agent/ports/subagents.py`<br>`agent/ports/turns.py`<br>`agent/stores/agents/graph.py`<br>`agent/stores/agents/mailbox.py`<br>`agent/stores/sessions/history.py` |
| `build` | `infrastructure` | 1 | 2 | `build.py` |
| `build` | `metadata` | 1 | 1 | `build.py` |
| `frontends` | `agent` | 80 | 182 | `frontends/cli/bootstrap.py`<br>`frontends/cli/commands.py`<br>`frontends/cli/dispatch.py`<br>`frontends/cli/doctor.py`<br>`frontends/cli/entry.py`<br>`frontends/cli/session_archive.py`<br>`frontends/interaction/contracts.py`<br>`frontends/interaction/noninteractive.py`<br>`frontends/mcp/server.py`<br>`frontends/output/application.py`<br>`frontends/output/jsonl.py`<br>`frontends/output/silent.py`<br>`frontends/output/terminal_content.py`<br>`frontends/output/text.py`<br>`frontends/runtime.py`<br>`frontends/subscription/forwarding.py`<br>`frontends/subscription/loop.py`<br>`frontends/subscription/runtime.py`<br>`frontends/subscription/ws.py`<br>`frontends/terminal/animation.py`<br>`frontends/terminal/highlighting.py`<br>`frontends/terminal/mcp_status.py`<br>`frontends/terminal/renderers/approval.py`<br>`frontends/terminal/renderers/batch.py`<br>`frontends/terminal/renderers/dispatch.py`<br>`frontends/terminal/renderers/download.py`<br>`frontends/terminal/renderers/failure.py`<br>`frontends/terminal/renderers/hook.py`<br>`frontends/terminal/renderers/lifecycle.py`<br>`frontends/terminal/renderers/lifecycle_parts.py`<br>`frontends/terminal/renderers/patch.py`<br>`frontends/terminal/renderers/plan.py`<br>`frontends/terminal/renderers/progress.py`<br>`frontends/terminal/renderers/tool.py`<br>`frontends/terminal/renderers/upload.py`<br>`frontends/terminal/styles.py`<br>`frontends/terminal/text.py`<br>`frontends/terminal/text_layout.py`<br>`frontends/terminal/traces/approval.py`<br>`frontends/terminal/traces/command_parts.py`<br>`frontends/terminal/traces/generic.py`<br>`frontends/terminal/traces/native.py`<br>`frontends/terminal/traces/render/preview_error.py`<br>`frontends/terminal/traces/render/title.py`<br>`frontends/terminal/traces/render/title_parts.py`<br>`frontends/terminal/turn_lifecycle.py`<br>`frontends/terminal/worked.py`<br>`frontends/tui/adapters/application.py`<br>`frontends/tui/adapters/content.py`<br>`frontends/tui/adapters/hooks.py`<br>`frontends/tui/adapters/markdown.py`<br>`frontends/tui/adapters/output.py`<br>`frontends/tui/adapters/presentation.py`<br>`frontends/tui/adapters/session.py`<br>`frontends/tui/adapters/status.py`<br>`frontends/tui/core/activity.py`<br>`frontends/tui/core/approval.py`<br>`frontends/tui/core/approval_render.py`<br>`frontends/tui/core/document.py`<br>`frontends/tui/core/runtime.py`<br>`frontends/tui/core/styles.py`<br>`frontends/tui/features/agents.py`<br>`frontends/tui/features/conversation.py`<br>`frontends/tui/features/helix.py`<br>`frontends/tui/features/history.py`<br>`frontends/tui/features/hooks.py`<br>`frontends/tui/features/listener.py`<br>`frontends/tui/features/mailbox.py`<br>`frontends/tui/features/mcp.py`<br>`frontends/tui/features/model.py`<br>`frontends/tui/features/permissions.py`<br>`frontends/tui/features/processes.py`<br>`frontends/tui/features/shell.py`<br>`frontends/tui/features/summary.py`<br>`frontends/tui/features/tools.py`<br>`frontends/tui/session/dispatch.py`<br>`frontends/tui/session/loop.py`<br>`frontends/tui/session/state.py`<br>`frontends/tui/session/turn.py`<br>`frontends/tui/session/turn_input.py` |
| `frontends` | `infrastructure` | 33 | 88 | `frontends/cli/bootstrap.py`<br>`frontends/cli/commands.py`<br>`frontends/cli/dispatch.py`<br>`frontends/cli/doctor.py`<br>`frontends/cli/entry.py`<br>`frontends/cli/frontend.py`<br>`frontends/cli/invocation.py`<br>`frontends/cli/mcp_registry.py`<br>`frontends/cli/session_archive.py`<br>`frontends/interaction/attachments.py`<br>`frontends/mcp/server.py`<br>`frontends/subscription/forwarding.py`<br>`frontends/subscription/runtime.py`<br>`frontends/terminal/download_renderer.py`<br>`frontends/tui/core/input.py`<br>`frontends/tui/core/viewport.py`<br>`frontends/tui/features/agents.py`<br>`frontends/tui/features/context.py`<br>`frontends/tui/features/diff.py`<br>`frontends/tui/features/helix.py`<br>`frontends/tui/features/hooks.py`<br>`frontends/tui/features/listener.py`<br>`frontends/tui/features/mailbox.py`<br>`frontends/tui/features/model.py`<br>`frontends/tui/features/skills.py`<br>`frontends/tui/features/transcript_export.py`<br>`frontends/tui/prompting/commands.py`<br>`frontends/tui/prompting/skills.py`<br>`frontends/tui/runtime/ports.py`<br>`frontends/tui/session/barriers.py`<br>`frontends/tui/session/dispatch.py`<br>`frontends/tui/session/loop.py`<br>`frontends/tui/session/state.py` |
| `frontends` | `metadata` | 27 | 27 | `frontends/cli/arguments.py`<br>`frontends/cli/bootstrap.py`<br>`frontends/cli/completion.py`<br>`frontends/cli/doctor.py`<br>`frontends/cli/frontend.py`<br>`frontends/cli/invocation.py`<br>`frontends/cli/parser.py`<br>`frontends/mcp/server.py`<br>`frontends/output/application.py`<br>`frontends/output/recording.py`<br>`frontends/output/text.py`<br>`frontends/subscription/client.py`<br>`frontends/subscription/opening.py`<br>`frontends/subscription/runtime.py`<br>`frontends/terminal/highlighting.py`<br>`frontends/terminal/progress.py`<br>`frontends/terminal/traces/approval.py`<br>`frontends/tui/adapters/application.py`<br>`frontends/tui/adapters/clipboard.py`<br>`frontends/tui/core/styles.py`<br>`frontends/tui/features/conversation.py`<br>`frontends/tui/features/helix.py`<br>`frontends/tui/features/permissions.py`<br>`frontends/tui/features/tools.py`<br>`frontends/tui/features/transcript_export.py`<br>`frontends/tui/rendering/screen/surfaces.py`<br>`frontends/tui/session/turn.py` |
| `frontends` | `mind_app` | 11 | 14 | `frontends/cli/bootstrap.py`<br>`frontends/cli/dispatch.py`<br>`frontends/cli/doctor.py`<br>`frontends/cli/mcp_registry.py`<br>`frontends/mcp/server.py`<br>`frontends/terminal/turn_lifecycle.py`<br>`frontends/tui/adapters/output.py`<br>`frontends/tui/features/conversation.py`<br>`frontends/tui/features/helix.py`<br>`frontends/tui/features/mcp.py`<br>`frontends/tui/session/turn.py` |
| `frontends` | `observability` | 17 | 18 | `frontends/cli/bootstrap.py`<br>`frontends/cli/dispatch.py`<br>`frontends/cli/entry.py`<br>`frontends/mcp/server.py`<br>`frontends/output/recording.py`<br>`frontends/subscription/external_access.py`<br>`frontends/subscription/forwarding.py`<br>`frontends/subscription/loop.py`<br>`frontends/subscription/opening.py`<br>`frontends/subscription/runtime.py`<br>`frontends/subscription/status.py`<br>`frontends/subscription/ws.py`<br>`frontends/tui/features/conversation.py`<br>`frontends/tui/prompting/files.py`<br>`frontends/tui/session/barriers.py`<br>`frontends/tui/session/turn.py`<br>`frontends/tui/session/turn_input.py` |
| `frontends` | `protocol` | 11 | 16 | `frontends/cli/bootstrap.py`<br>`frontends/mcp/server.py`<br>`frontends/subscription/client.py`<br>`frontends/subscription/runtime.py`<br>`frontends/subscription/ws.py`<br>`frontends/tui/core/queued.py`<br>`frontends/tui/features/conversation.py`<br>`frontends/tui/features/history.py`<br>`frontends/tui/session/loop.py`<br>`frontends/tui/session/turn.py`<br>`frontends/tui/session/turn_input.py` |
| `frontends` | `server` | 3 | 3 | `frontends/cli/bootstrap.py`<br>`frontends/subscription/external_access.py`<br>`frontends/tui/session/dispatch.py` |
| `infrastructure` | `agent` | 11 | 18 | `infrastructure/config/execution_policy.py`<br>`infrastructure/config/execution_policy_manager.py`<br>`infrastructure/config/layers.py`<br>`infrastructure/config/schema.py`<br>`infrastructure/hooks/discovery.py`<br>`infrastructure/persistence/transcripts.py`<br>`infrastructure/platform/hook_command.py`<br>`infrastructure/platform/process_sessions.py`<br>`infrastructure/services/helix_capability.py`<br>`infrastructure/services/runtime_owner.py`<br>`infrastructure/services/turn_environment.py` |
| `infrastructure` | `metadata` | 20 | 20 | `infrastructure/config/execution_policy.py`<br>`infrastructure/config/execution_policy_manager.py`<br>`infrastructure/config/layers.py`<br>`infrastructure/config/paths.py`<br>`infrastructure/config/preferences.py`<br>`infrastructure/config/runtime_paths.py`<br>`infrastructure/config/store.py`<br>`infrastructure/config/trust.py`<br>`infrastructure/hooks/discovery.py`<br>`infrastructure/persistence/transcripts.py`<br>`infrastructure/platform/encoding.py`<br>`infrastructure/platform/file_assist.py`<br>`infrastructure/platform/hook_command.py`<br>`infrastructure/platform/hook_output_spill.py`<br>`infrastructure/platform/javascript_repl.py`<br>`infrastructure/services/keepalive.py`<br>`infrastructure/services/licensing.py`<br>`infrastructure/services/remote_services.py`<br>`infrastructure/services/server_manager.py`<br>`infrastructure/services/service_config.py` |
| `infrastructure` | `observability` | 12 | 12 | `infrastructure/config/preferences.py`<br>`infrastructure/persistence/transcripts.py`<br>`infrastructure/platform/process_sessions.py`<br>`infrastructure/services/keepalive.py`<br>`infrastructure/services/licensing.py`<br>`infrastructure/services/remote_services.py`<br>`infrastructure/services/runtime_owner.py`<br>`infrastructure/services/runtime_setup.py`<br>`infrastructure/services/server_manager.py`<br>`infrastructure/services/service_config.py`<br>`infrastructure/services/turn_environment.py`<br>`infrastructure/update/runtime.py` |
| `infrastructure` | `protocol` | 5 | 6 | `infrastructure/persistence/transcripts.py`<br>`infrastructure/services/licensing.py`<br>`infrastructure/services/remote_services.py`<br>`infrastructure/services/server_manager.py`<br>`infrastructure/update/runtime.py` |
| `mind` | `agent` | 1 | 7 | `mind.py` |
| `mind` | `frontends` | 1 | 3 | `mind.py` |
| `mind` | `infrastructure` | 1 | 7 | `mind.py` |
| `mind` | `mind_app` | 1 | 4 | `mind.py` |
| `mind_app` | `agent` | 43 | 181 | `mind_app/builtin_tools/permissions.py`<br>`mind_app/builtin_tools/registry.py`<br>`mind_app/client_tools/coding/native.py`<br>`mind_app/client_tools/registry.py`<br>`mind_app/client_tools/subagents.py`<br>`mind_app/client_tools/types.py`<br>`mind_app/controller.py`<br>`mind_app/native_coding/exec/exec_command.py`<br>`mind_app/native_coding/exec/shell_exec.py`<br>`mind_app/runtime/compaction.py`<br>`mind_app/runtime/hooks/compact.py`<br>`mind_app/runtime/hooks/presentation.py`<br>`mind_app/runtime/hooks/session.py`<br>`mind_app/runtime/hooks/tool.py`<br>`mind_app/runtime/hooks/turn.py`<br>`mind_app/runtime/mcp/external.py`<br>`mind_app/runtime/mcp/service_runtime.py`<br>`mind_app/runtime/mcp/session_adapter.py`<br>`mind_app/runtime/mcp/tool_runtime.py`<br>`mind_app/runtime/mcp/tools.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/runtime/tools/display.py`<br>`mind_app/runtime/tools/enhance_reporter.py`<br>`mind_app/runtime/tools/plan_call.py`<br>`mind_app/runtime/tools/plan_steps.py`<br>`mind_app/runtime/tools/progress.py`<br>`mind_app/runtime/tools/router.py`<br>`mind_app/runtime/tools/run.py`<br>`mind_app/runtime/turns/execution_runtime.py`<br>`mind_app/runtime/turns/executor.py`<br>`mind_app/runtime/turns/root.py`<br>`mind_app/runtime/turns/root_session.py`<br>`mind_app/runtime/turns/session_context.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_approval.py`<br>`mind_app/runtime/turns/stream_effects.py`<br>`mind_app/runtime/turns/stream_finalize.py`<br>`mind_app/runtime/turns/stream_model.py`<br>`mind_app/runtime/turns/stream_policy.py`<br>`mind_app/runtime/turns/stream_presentation.py`<br>`mind_app/runtime/turns/stream_setup.py`<br>`mind_app/runtime/turns/stream_tools.py`<br>`mind_app/runtime/turns/subagent_adapter.py` |
| `mind_app` | `frontends` | 3 | 9 | `mind_app/controller.py`<br>`mind_app/runtime/mcp/service_runtime.py`<br>`mind_app/runtime/turns/root.py` |
| `mind_app` | `infrastructure` | 18 | 45 | `mind_app/client_tools/coding/native.py`<br>`mind_app/client_tools/registry.py`<br>`mind_app/controller.py`<br>`mind_app/native_coding/base.py`<br>`mind_app/native_coding/exec/exec_command.py`<br>`mind_app/native_coding/exec/shell_exec.py`<br>`mind_app/native_coding/exec/user_shell.py`<br>`mind_app/native_coding/native_coding.py`<br>`mind_app/runtime/mcp/external.py`<br>`mind_app/runtime/mcp/group.py`<br>`mind_app/runtime/mcp/registry.py`<br>`mind_app/runtime/mcp/service_runtime.py`<br>`mind_app/runtime/tools/enhancement/handlers.py`<br>`mind_app/runtime/turns/session_context.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_finalize.py`<br>`mind_app/runtime/turns/stream_policy.py`<br>`mind_app/runtime/turns/stream_tools.py` |
| `mind_app` | `metadata` | 10 | 10 | `mind_app/native_coding/base.py`<br>`mind_app/native_coding/edit/diagnostics.py`<br>`mind_app/native_coding/edit/diff_render.py`<br>`mind_app/native_coding/edit/operations.py`<br>`mind_app/native_coding/edit/planning.py`<br>`mind_app/runtime/hooks/tool.py`<br>`mind_app/runtime/mcp/config.py`<br>`mind_app/runtime/mcp/local.py`<br>`mind_app/runtime/mcp/service_exec_env.py`<br>`mind_app/runtime/tools/client_call.py` |
| `mind_app` | `observability` | 23 | 25 | `mind_app/controller.py`<br>`mind_app/native_coding/exec/shell_exec.py`<br>`mind_app/runtime/compaction.py`<br>`mind_app/runtime/hooks/session.py`<br>`mind_app/runtime/mcp/external.py`<br>`mind_app/runtime/mcp/group.py`<br>`mind_app/runtime/mcp/local.py`<br>`mind_app/runtime/mcp/service_exec_env.py`<br>`mind_app/runtime/mcp/service_runtime.py`<br>`mind_app/runtime/mcp/session_adapter.py`<br>`mind_app/runtime/mcp/tool_progress.py`<br>`mind_app/runtime/mcp/tool_runtime.py`<br>`mind_app/runtime/mcp/tools.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/runtime/tools/enhancement/handlers.py`<br>`mind_app/runtime/tools/plan_steps.py`<br>`mind_app/runtime/tools/run.py`<br>`mind_app/runtime/turns/executor.py`<br>`mind_app/runtime/turns/session_context.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_approval.py`<br>`mind_app/runtime/turns/stream_finalize.py`<br>`mind_app/runtime/turns/stream_tools.py` |
| `mind_app` | `protocol` | 18 | 35 | `mind_app/builtin_tools/permissions.py`<br>`mind_app/client_tools/coding/native.py`<br>`mind_app/controller.py`<br>`mind_app/runtime/compaction.py`<br>`mind_app/runtime/mcp/local.py`<br>`mind_app/runtime/tools/client_call.py`<br>`mind_app/runtime/tools/enhancement/handlers.py`<br>`mind_app/runtime/tools/run.py`<br>`mind_app/runtime/turns/executor.py`<br>`mind_app/runtime/turns/root.py`<br>`mind_app/runtime/turns/stream.py`<br>`mind_app/runtime/turns/stream_approval.py`<br>`mind_app/runtime/turns/stream_effects.py`<br>`mind_app/runtime/turns/stream_model.py`<br>`mind_app/runtime/turns/stream_presentation.py`<br>`mind_app/runtime/turns/stream_setup.py`<br>`mind_app/runtime/turns/stream_tools.py`<br>`mind_app/runtime/turns/subagent_adapter.py` |
| `observability` | `metadata` | 1 | 1 | `observability/reporting.py` |
| `protocol` | `metadata` | 1 | 1 | `protocol/transport/config.py` |
| `protocol` | `observability` | 3 | 3 | `protocol/client/manifest.py`<br>`protocol/transport/events.py`<br>`protocol/transport/streaming.py` |
| `server` | `infrastructure` | 5 | 10 | `server/app.py`<br>`server/lifecycle.py`<br>`server/routers/pref.py`<br>`server/routers/services.py`<br>`server/storage.py` |
| `server` | `metadata` | 2 | 2 | `server/page.py`<br>`server/routers/basic.py` |
| `server` | `observability` | 1 | 1 | `server/lifecycle.py` |
| `setup` | `metadata` | 1 | 1 | `setup.py` |

## 循环与反向依赖

### 跨边界循环

- `frontends -> mind_app -> frontends`

### `engine` 残留引用

- 未发现 `engine` 残留导入；旧源包已删除。

## 复核命令

```powershell
.\venv\Scripts\python.exe scripts\agent_runtime_import_graph.py --check
```
