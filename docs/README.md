# 正文目录
按主题分组跳转正文；这里只做目录，不再承担导流说明。

## 按任务方向阅读
### 入门与入口
适合补齐交互入口、REPL 切换、订阅和输入约束。

- [交互模式](interactive-mode.md)  
  适合看 REPL 指令、状态切换和输入约束。
- [订阅模式](agent-mode.md)  
  适合看 --agent 的会话、长链路监听、任务下发和恢复链路。

### 编排与协议
适合处理 `--code`、协议校验、模板层和批量执行结构。

- [星图协议](cli-code.md)  
  适合看 --code 的字段、层级、前后置和规则结构。
- [星图深入说明](cli-code-advanced.md)  
  适合看覆盖优先级、执行顺序、批跑控制语义和常见误写。
- [星图样例](code-blueprints.md)  
  适合看跨域 --code 编排的高层自然语言样例，以及什么时候该写星图。
- [星图源抽象设计](cli-code-source-design.md)  
  适合在把 --code 从本机文件入口升级为 source 抽象时阅读。
- [接口实战](playbook.api.md)  
  适合看协议能力的字段边界、提取、断言和批量请求。
- [模板能力](playbook.template.md)  
  适合看模板 helper、签名前置材料和模板层边界。

### 执行与取证
适合处理设备动作、多媒体证据链和端侧执行收束。

- [设备与 UI 实战](playbook.device.md)  
  适合看设备能力分层、多设备广播模型和稳定执行建议。
- [Monkey 扰动](playbook.monkey.md)  
  适合看 device.monkey.injection 的全部参数、执行流程和返回结构。
- [多媒体链路](playbook.media.md)  
  适合看抽帧、裁剪、转码、拼接和音频处理。

### 性能与安全
适合处理性能回归、稳定性诊断、签名和加解密链路。

- [安全工具](playbook.security.md)  
  适合看 security_* 的摘要、JWT、RSA、AES 和安全层边界。
- [性能实战](playbook.performance.md)  
  适合看 Framix、Memrix、Monkey 和性能星图样例。

### 结构与维护
适合继续读系统骨架、站点同步链路和维护约定。

- [背景与架构](architecture.md)  
  适合看系统分层、主要能力、云端增强和推理集群。
- [维护者指南](maintainer-guide.md)  
  适合在维护文档、官网壳和同步链路时阅读。
