# Framix 常用命令

## 命令规则

- 除 `framix --apply ...` 外，所有示例命令都加 `--debug`，确保执行时输出日志。
- 路径参数始终加引号。
- Windows PowerShell 中 JSON 字符串需要转义双引号。

## 视频分析

分析单个视频：

```bash
framix --debug --video "/path/to/video.mp4"
```

分析多个视频：

```bash
framix --debug --video "/path/to/a.mp4" --video "/path/to/b.mp4"
```

## JSON 任务输入

使用 `--frame` 传入单个 JSON 任务：

```bash
framix --debug --frame '{"label":"20250814000130","title":"Test","video":["/path/to/a.mp4"]}'
```

Windows PowerShell 示例：

```powershell
framix --debug --frame "{`"label`":`"20250814000130`",`"title`":`"Test`",`"video`":[`"C:/Users/Alice/a.mp4`"]}"
```

## 任务目录分析

```bash
framix --debug --stack "/path/to/task-folder"
```

## 快速拆帧

适用于不关心精确时间戳，只想快速生成帧级结果的场景。

```bash
framix --debug --video "/path/to/video.mp4" --speed
```

## 基础时间戳分析

适用于需要帧时间戳、耗时判断和时序分析的场景。

```bash
framix --debug --video "/path/to/video.mp4" --basic
```

## 模型辅助分析

适用于关键帧识别、阶段分类、视觉模型判断等场景。

```bash
framix --debug --video "/path/to/video.mp4" --keras
```

## 远程推理

适用于需要云端 GPU 或远程模型推理的场景。

```bash
framix --debug --video "/path/to/video.mp4" --infer
```

## 训练数据准备

```bash
framix --debug --train "/path/to/video.mp4"
```

## 构建模型

```bash
framix --debug --build "/path/to/Model_xxx"
```

模型目录要求：

```text
Model_xxx/
├── 0/
├── 1/
├── 2/
```

规则：

- 必须包含 `0/`
- 目录名必须是连续数字
- 标签范围最多 `0` 到 `5`
- 图片格式建议为 `.jpg` 或 `.png`

## 合并无时间戳报告

```bash
framix --debug --union "/path/to/report-folder"
```

## 合并有时间戳报告

```bash
framix --debug --merge "/path/to/report-folder"
```

## 同步资源

首次部署或资源缺失时使用：

```bash
framix --debug --syncs all
```

可选值：

```text
all
template
toolkit
model
```

## 授权激活

`--apply` 是例外，不加 `--debug`：

```bash
framix --apply ACT-XXXX-XXXX
```

## 路径规则

始终建议给路径加引号：

```bash
framix --debug --video "/Users/user/Videos/test video.mp4"
```

不要使用未加引号的路径：

```bash
framix --debug --video /Users/user/Videos/test video.mp4
```
