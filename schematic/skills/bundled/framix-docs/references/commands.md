# Framix 常用命令

## 命令规则

- 除 `framix --apply ...` 外，所有示例命令都加 `--debug`，确保执行时输出日志。
- 路径参数始终加引号。
- Windows PowerShell 中 JSON 字符串需要转义双引号。

## 1. 视频分析

分析单个视频：

```bash
framix --debug --video "/path/to/video.mp4"
```

分析多个视频：

```bash
framix --debug --video "/path/to/a.mp4" --video "/path/to/b.mp4"
```

路径中有空格时必须加引号：

```bash
framix --debug --video "C:/Users/Alice/Videos/My Sample.mp4"
```

---

## 2. JSON 任务输入

使用 `--frame` 传入单个 JSON 任务：

```bash
framix --debug --frame '{"label":"20250814000130","title":"Test","video":["/path/to/a.mp4"]}'
```

Windows PowerShell 示例：

```powershell
framix --debug --frame "{`"label`":`"20250814000130`",`"title`":`"Test`",`"video`":[`"C:/Users/Alice/a.mp4`"]}"
```

---

## 3. 任务目录分析

分析结构化任务目录：

```bash
framix --debug --stack "/path/to/task-folder"
```

---

## 4. 快速拆帧

适用于不关心精确时间戳，只想快速生成帧级结果的场景。

```bash
framix --debug --video "/path/to/video.mp4" --speed
```

---

## 5. 基础时间戳分析

适用于需要帧时间戳、耗时判断和时序分析的场景。

```bash
framix --debug --video "/path/to/video.mp4" --basic
```

---

## 6. 模型辅助分析

适用于关键帧识别、阶段分类、视觉模型判断等场景。

```bash
framix --debug --video "/path/to/video.mp4" --keras
```

---

## 7. 远程推理

适用于需要云端 GPU 或远程模型推理的场景。

```bash
framix --debug --video "/path/to/video.mp4" --infer
```

也可以单独启用远程推理模式：

```bash
framix --debug --infer
```

---

## 8. 训练数据准备

```bash
framix --debug --train "/path/to/video.mp4"
```

---

## 9. 构建模型

```bash
framix --debug --build "/path/to/Model_xxx"
```

指定模型输入尺寸：

```bash
framix --debug --shape 200,200 --build "/path/to/Model_xxx"
```

模型目录要求：

```text
Model_xxx/
├── 0/
├── 1/
├── 2/
```

规则：

* 必须包含 `0/`
* 目录名必须是连续数字
* 标签范围最多 `0` 到 `5`
* 图片格式建议为 `.jpg` 或 `.png`

---

## 10. 循环录制分析

用于周期性录制和持续分析。

```bash
framix --debug --flick
```

---

## 11. 执行单个脚本

```bash
framix --debug --carry script.json;ID-X
```

---

## 12. 执行完整脚本集合

```bash
framix --debug --fully script.json
```

---

## 13. 绘制辅助线

用于在截图上绘制横向和竖向辅助线，便于区域分析。

```bash
framix --debug --paint
```

---

## 14. 合并无时间戳报告

适用于无严格时间戳对齐的报告合并。

```bash
framix --debug --union "/path/to/report-folder"
```

多个目录：

```bash
framix --debug --union "/path/to/a" --union "/path/to/b"
```

---

## 15. 合并有时间戳报告

适用于需要按时间戳对齐的报告合并。

```bash
framix --debug --merge "/path/to/report-folder"
```

多个目录：

```bash
framix --debug --merge "/path/to/a" --merge "/path/to/b"
```

---

## 16. 综合报告合并

使用分组模式合并报告：

```bash
framix --debug --group --merge "/path/to/a" --merge "/path/to/b"
```

---

## 17. 配置对话预览

用于打开并校对 Framix 配置。

```bash
framix --debug --talks
```

---

## 18. 注入全局运行配置

用于加载报告目录、模型目录和模型选择等全局配置。

```bash
framix --debug --rings
```

---

## 19. 授权激活

```bash
framix --apply ACT-XXXX-XXXX
```

---

## 20. 批量文本转语音

```bash
framix --debug --voice 你好 世界 AI科技
```

---

## 21. 同步资源

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

---

## 22. 独立录屏控制

适用于多任务并行录制时，独立控制指定设备录屏停止。

```bash
framix --debug --alone
```

---

## 23. 高级模型分析参数

### 开启增强分析

```bash
framix --debug --keras --boost --video "/path/to/video.mp4"
```

### 开启彩色模型分析

```bash
framix --debug --keras --boost --color --video "/path/to/video.mp4"
```

### 开启分组分析

```bash
framix --debug --keras --boost --color --group --video "/path/to/video.mp4"
```

### 缩放视频画面

```bash
framix --debug --keras --boost --color --group --scale 0.5 --video "/path/to/video.mp4"
```

### 设置开始时间和分析时长

```bash
framix --debug --keras --boost --color --group --start 0.5 --limit 2 --video "/path/to/video.mp4"
```

### 设置终止时间

```bash
framix --debug --keras --boost --color --group --close 3 --video "/path/to/video.mp4"
```

### 设置帧率和识别阈值

```bash
framix --debug --keras --boost --color --group --frate 60 --thres 0.97 --video "/path/to/video.mp4"
```

### 设置偏移和分块参数

```bash
framix --debug --keras --boost --color --group --shift 3 --block 6 --video "/path/to/video.mp4"
```

### 设置目标分析区域

```bash
framix --debug --keras --boost --color --group --crops 0,0.1,1,0.9 --video "/path/to/video.mp4"
```

### 设置忽略区域

```bash
framix --debug --keras --boost --color --group --omits 0,0,1,0.2 --omits 0,0.9,1,0.1 --video "/path/to/video.mp4"
```

---

## 24. 常用组合

### 快速看视频结果

```bash
framix --debug --video "/path/to/video.mp4" --speed
```

### 做精确耗时分析

```bash
framix --debug --video "/path/to/video.mp4" --basic
```

### 做模型辅助识别

```bash
framix --debug --video "/path/to/video.mp4" --keras
```

### 做增强视觉分析

```bash
framix --debug --keras --boost --color --group --video "/path/to/video.mp4"
```

### 做指定时间段分析

```bash
framix --debug --keras --boost --color --group --start 0.5 --limit 2 --video "/path/to/video.mp4"
```

### 做指定区域分析

```bash
framix --debug --keras --boost --color --group --crops 0,0.1,1,0.9 --video "/path/to/video.mp4"
```

### 首次同步资源

```bash
framix --debug --syncs all
```

### 已有结果生成合并报告

```bash
framix --debug --group --merge "/path/to/a" --merge "/path/to/b"
```

---

## 25. 参数速查

| 参数        | 作用               |
| --------- | ---------------- |
| `--frame` | 使用 JSON 载荷注入视频任务 |
| `--video` | 指定视频文件           |
| `--stack` | 指定任务目录           |
| `--train` | 准备训练样本           |
| `--build` | 构建模型             |
| `--flick` | 循环录制和分析          |
| `--carry` | 执行单个脚本任务         |
| `--fully` | 执行完整脚本集合         |
| `--paint` | 绘制截图辅助线          |
| `--union` | 合并无时间戳报告         |
| `--merge` | 合并有时间戳报告         |
| `--talks` | 配置对话预览           |
| `--rings` | 注入全局运行配置         |
| `--apply` | 授权激活             |
| `--voice` | 批量文本转语音          |
| `--syncs` | 同步模板、模型或工具资源     |
| `--speed` | 快速拆帧             |
| `--basic` | 时间戳分析            |
| `--keras` | 模型辅助分析           |
| `--infer` | 远程推理             |
| `--alone` | 独立录屏控制           |
| `--boost` | 启用增强分析           |
| `--color` | 启用彩色模型/彩色分析      |
| `--group` | 启用分组分析或分组报告      |
| `--scale` | 缩放画面             |
| `--start` | 指定开始时间           |
| `--limit` | 指定分析时长           |
| `--close` | 指定终止时间           |
| `--frate` | 指定帧率             |
| `--thres` | 指定识别阈值           |
| `--shift` | 指定偏移参数           |
| `--block` | 指定分块参数           |
| `--crops` | 指定目标分析区域         |
| `--omits` | 指定忽略区域           |
| `--shape` | 指定模型输入尺寸         |

---

## 26. 路径规则

始终建议给路径加引号：

```bash
framix --debug --video "/Users/user/Videos/test video.mp4"
```

不要使用未加引号的路径：

```bash
framix --debug --video /Users/user/Videos/test video.mp4
```
