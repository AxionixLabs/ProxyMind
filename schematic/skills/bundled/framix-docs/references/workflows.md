# Framix 典型流程

## 1. 首次环境准备

适用于新机器、新环境或资源缺失场景。

```bash
framix --debug --syncs all
```

目的：

- 同步模板
- 同步模型
- 同步运行所需工具资源
- 确保本地环境可执行

## 2. 快速分析一个视频

适用于快速预览视频帧变化，不要求精确耗时。

```bash
framix --debug --video "/path/to/video.mp4" --speed
```

输出关注：

- 是否成功拆帧
- 是否生成报告
- 是否存在明显异常帧

## 3. 分析 APP 操作耗时

适用于启动耗时、页面切换耗时、功能执行耗时分析。

```bash
framix --debug --video "/path/to/video.mp4" --basic
```

输出关注：

- 起始帧
- 结束帧
- 时间戳
- 耗时区间
- 是否超出预期

## 4. 使用模型辅助识别关键帧

适用于需要自动识别关键阶段、关键动作或视觉变化的场景。

```bash
framix --debug --video "/path/to/video.mp4" --keras
```

输出关注：

- 模型识别结果
- 关键帧区间
- 分类置信度
- 报告是否完整

## 5. 使用远程推理

适用于本地算力不足或需要云端模型的场景。

```bash
framix --debug --video "/path/to/video.mp4" --infer
```

注意：

- 远程服务不可用时可能降级为本地推理
- 需要关注网络、接口和返回结果

## 6. 批量任务输入

适用于脚本化、CI 或批量视频任务。

```bash
framix --debug --frame '{"label":"20250814000130","title":"Test","video":["/path/to/a.mp4","/path/to/b.mp4"]}'
```

要求：

- JSON 根节点必须是对象
- `label` 必须是任务标识
- `title` 必须是任务标题
- `video` 必须是视频路径数组

## 7. 训练样本准备

```bash
framix --debug --train "/path/to/video.mp4"
```

之后需要整理模型目录：

```text
Model_xxx/
├── 0/
├── 1/
├── 2/
```

## 8. 构建模型

```bash
framix --debug --build "/path/to/Model_xxx"
```

注意：

- 目录必须从 `0/` 开始
- 编号必须连续
- 最多支持 6 类，即 `0` 到 `5`

## 9. 报告合并

无严格时间戳时：

```bash
framix --debug --union "/path/to/report-folder"
```

需要按时间戳对齐时：

```bash
framix --debug --merge "/path/to/report-folder"
```

## 10. 回答用户时的推荐格式

优先给命令：

```bash
framix --debug --video "/path/to/video.mp4" --basic
```

然后简要说明：

```text
这个命令会对视频进行带时间戳的帧级分析，适合判断启动、页面切换或功能执行耗时。执行后重点查看生成的报告和关键帧区间。
```
