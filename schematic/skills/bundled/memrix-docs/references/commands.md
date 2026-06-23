# Memrix 常用命令

## 命令规则

- 除 `memrix --apply ...` 外，所有 Memrix 示例命令都加 `--debug`，确保执行时输出日志。
- Android 应用采集必须提供 `--focus <包名>`。
- 多设备场景必须提供 `--imply <设备序列号>`。
- `adb devices` 不是 Memrix 命令，不需要追加 `--debug`。

## 内存与 I/O 联合采样

用于采集目标应用的内存波动和 I/O 行为。

```bash
memrix --debug --storm --focus <com.example.application>
```

指定设备：

```bash
memrix --debug --storm --focus <com.example.application> --imply <device.serial>
```

指定输出目录：

```bash
memrix --debug --storm --focus <com.example.application> --scene <output_dir>
```

设置报告标题：

```bash
memrix --debug --storm --focus <com.example.application> --title <report_name>
```

开启详细调试日志：

```bash
memrix --debug --storm --focus <com.example.application> --watch
```

## 帧率与流畅度分析

用于分析 Perfetto trace 文件中的帧率、卡顿和交互流畅度。

```bash
memrix --debug --sleek --focus <trace_file.perfetto>
```

## 生成报告

用于根据已有测试输出目录生成结构化 HTML 报告。

```bash
memrix --debug --forge <output_dir>
```

生成报告并区分前后台阶段：

```bash
memrix --debug --forge <output_dir> --layer
```

## 采集后自动生成报告

用于采集完成后自动生成图表报告。

```bash
memrix --debug --storm --focus <com.example.application> --atlas
```

## 启用评分标准

用于按 YAML 配置对内存、流畅度、I/O 等指标进行多维度评分。

```bash
memrix --debug --storm --focus <com.example.application> --align
```

通常可以和自动报告一起使用：

```bash
memrix --debug --storm --focus <com.example.application> --align --atlas
```

## 指定设备

当连接多台设备时使用。

```bash
memrix --debug --storm --focus <com.example.application> --imply <device.serial>
```

设备序列号可通过以下命令查看：

```bash
adb devices
```

## 指定任务目录

用于指定输出目录或任务挂载目录。

```bash
memrix --debug --storm --focus <com.example.application> --scene <file.name>
```

## 设置报告标题

```bash
memrix --debug --storm --focus <com.example.application> --title <name>
```

## 授权激活

`--apply` 是例外，不加 `--debug`：

```bash
memrix --apply ACT-XXXX-XXXX
```

## 常用组合

### 持续监控应用内存

```bash
memrix --debug --storm --focus <com.example.application>
```

### 指定设备采集

```bash
memrix --debug --storm --focus <com.example.application> --imply <device.serial>
```

### 采集并自动生成报告

```bash
memrix --debug --storm --focus <com.example.application> --atlas
```

### 采集、评分并生成报告

```bash
memrix --debug --storm --focus <com.example.application> --align --atlas
```

### 生成已有目录报告

```bash
memrix --debug --forge <file.name>
```

## 参数说明

| 参数 | 作用 |
| --- | --- |
| `--debug` | 输出执行日志 |
| `--storm` | 内存波动和 I/O 行为联合采样 |
| `--sleek` | 帧率稳定性和交互流畅度分析 |
| `--forge` | 生成结构化报告 |
| `--align` | 启用多维度评分标准 |
| `--apply` | 授权激活 |
| `--focus` | 指定应用包名或 trace 文件 |
| `--imply` | 指定 ADB 设备序列号 |
| `--scene` | 指定输出目录或任务目录 |
| `--title` | 设置报告标题 |
| `--atlas` | 任务完成后自动生成报告 |
| `--layer` | 报告中区分前台和后台阶段 |
| `--watch` | 输出详细调试信息 |
