# Memrix 典型流程

## 1. 测试前确认设备

适用于 Android 设备测试前确认连接状态。

```bash
adb devices
```

确认设备在线后，再执行 Memrix 采集命令。

如果只有一台设备，可以不传 `--imply`。

如果有多台设备，建议指定设备序列号：

```bash
memrix --debug --storm --focus <com.example.application> --imply <device.serial>
```

## 2. 采集应用内存和 I/O

适用于判断应用是否存在内存上涨、波动异常或 I/O 异常。

```bash
memrix --debug --storm --focus <com.example.application>
```

关注结果：

- 内存是否持续上涨
- 是否存在明显波动
- I/O 是否异常频繁
- 采样过程是否稳定
- 是否生成中间数据

## 3. 采集并自动生成报告

适用于一次性完成采集和报告输出。

```bash
memrix --debug --storm --focus <com.example.application> --atlas
```

关注结果：

- 是否生成 HTML 报告
- 报告中是否包含趋势图
- 是否包含异常提示
- 是否方便归档和共享

## 4. 启用评分机制

适用于需要准出判断、风险标注或统一测试标准的场景。

```bash
memrix --debug --storm --focus <com.example.application> --align --atlas
```

关注结果：

- 是否加载评分配置
- 是否输出评分结果
- 是否标注风险项
- 是否满足准出标准

## 5. 指定输出目录和报告标题

适用于批量测试或需要归档的场景。

```bash
memrix --debug --storm --focus <com.example.application> --scene <output_dir> --title <report_name> --atlas
```

说明：

- `--scene` 控制输出目录
- `--title` 控制报告标题
- `--atlas` 控制采集结束后自动生成报告

## 6. 多设备测试

适用于同时连接多台 Android 设备的场景。

```bash
memrix --debug --storm --focus <com.example.application> --imply <device.serial> --scene <output_dir>
```

注意：

- 多设备场景必须确认设备序列号
- 不要让工具自动猜测目标设备
- 输出目录建议按设备或任务命名

## 7. 分析帧率和流畅度

适用于已有 Perfetto trace 文件，或需要分析滑动、拖拽、掉帧等问题。

```bash
memrix --debug --sleek --focus <trace_file.perfetto>
```

关注结果：

- FPS 是否稳定
- 是否存在 Jank
- 是否存在明显掉帧段
- 是否能定位异常交互区间

## 8. 根据已有目录生成报告

适用于采集已经完成，只需要补充生成报告的场景。

```bash
memrix --debug --forge <output_dir>
```

如果需要区分前台和后台阶段：

```bash
memrix --debug --forge <output_dir> --layer
```

## 9. 调试异常采集

适用于采集失败、设备无响应、数据缺失或命令行为异常。

```bash
memrix --debug --storm --focus <com.example.application> --watch
```

关注内容：

- ADB 是否连接正常
- 目标包名是否正确
- 是否有权限或设备状态异常
- 采样过程是否中断

## 10. 授权激活

首次部署或授权过期时使用。

```bash
memrix --apply ACT-XXXX-XXXX
```

注意：

- 激活码通常为一次性授权码
- 授权会绑定设备指纹
- 激活成功后会在本地生成授权文件

## 11. 回答用户时的推荐格式

先给命令：

```bash
memrix --debug --storm --focus <com.example.application> --align --atlas
```

再简要说明：

```text
这个命令会对目标应用进行内存和 I/O 采样，并启用评分标准，任务结束后自动生成结构化报告。适合做性能准出、内存异常排查和测试结果归档。
```

## 12. 常见判断

用户说“看内存有没有涨”：

```bash
memrix --debug --storm --focus <com.example.application>
```

用户说“顺便出报告”：

```bash
memrix --debug --storm --focus <com.example.application> --atlas
```

用户说“要按标准判断是否通过”：

```bash
memrix --debug --storm --focus <com.example.application> --align --atlas
```

用户说“有多台设备”：

```bash
memrix --debug --storm --focus <com.example.application> --imply <device.serial>
```

用户说“已有结果，只要报告”：

```bash
memrix --debug --forge <output_dir>
```
