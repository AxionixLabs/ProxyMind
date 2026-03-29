# Utilities

目录约定：

- `runtime/`
  - 运行时装配、生命周期、空闲与任务跟踪。
  - 例：`app_context.py`、`pipeline.py`
- `state/`
  - 进程内共享状态仓库与快照。
  - 例：`store.py`
- `process/`
  - 子进程、端口、系统调用相关工具。
  - 例：`flux.py`、`ports.py`
- `storage/`
  - 日志、偏好、输出目录等落盘相关工具。
  - 例：`logs.py`、`prefs.py`、`output.py`
- `validation/`
  - 输入/路径/格式校验。
  - 例：`marked.py`
- 根层保留少量跨域稳定模块：
  - `const.py`：常量
  - `broadcast.py`：多目标广播执行与结果打包

约束：

- 不再新增 `toolbox.py` 这类杂项容器。
- 新增工具前先判断是否属于现有子目录；只有跨域且长期稳定的模块才放根层。
- 广播执行逻辑和结果整形统一放 `broadcast.py`，不要再混写在工具或 hub 层。
