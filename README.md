# 邮件发送助手插件 v2.0.0

基于 LLM Tool 的智能邮件发送插件，支持人设化交互和精致 HTML 邮件。

## 功能特性

- **LLM Tool 集成**：AI 自动判断是否需要发送邮件，无需手动命令
- **人设化交互**：所有交互语言基于 AstrBot 人格设定
- **自动识别收件人**：支持 QQ 号自动转换为 QQ 邮箱
- **精致邮件格式**：HTML 邮件模板，渐变色头部，圆角卡片设计
- **多格式支持**：支持纯文本和 HTML 格式邮件

## 使用方式

### 方式一：自然语言触发（推荐）

直接用自然语言和 AI 对话，AI 会自动判断是否需要发送邮件：
- "给 123456 发封邮件，主题是明天聚会取消"
- "帮我发邮件到 test@example.com"
- "给他发封邮件"

AI 会根据人设风格与你交互，确认收件人、主题和内容后自动发送。

### 方式二：命令触发

```
/mail_test recipient@example.com    # 发送测试邮件
/mail_config_check                  # 检查 SMTP 配置
```

## 对话流程示例

```
用户: 给他发封邮件
Bot: (根据人设回复) 好的，请告诉我收件人的 QQ 号或邮箱地址～

用户: 123456
Bot: (根据人设回复) 收件人是 123456@qq.com，请告诉我邮件主题～

用户: 明天聚会取消
Bot: (根据人设回复) 主题是「明天聚会取消」，请告诉我邮件内容要点～

用户: 因为天气原因取消
Bot: (根据人设回复) 邮件已成功发送到 123456@qq.com～
```

## 配置说明

### SMTP 配置（必填）

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `smtp.server` | SMTP 服务器地址 | `smtp.qq.com` |
| `smtp.port` | SMTP 端口 | `465` |
| `smtp.email` | 发件邮箱地址 | `your_email@qq.com` |
| `smtp.password` | SMTP 授权码 | `your_authorization_code` |
| `smtp.use_ssl` | 是否使用 SSL | `true` |

### LLM Tool 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable_llm_tool` | 是否启用 LLM Tool | `true` |

### QQ 邮箱 SMTP 配置步骤

1. 登录 QQ 邮箱
2. 进入「设置」→「账户」
3. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务」
4. 开启「POP3/SMTP 服务」或「IMAP/SMTP 服务」
5. 按照提示获取授权码
6. 将授权码填入 `smtp.password` 配置

## 安装方式

1. 将 `email_sender` 文件夹放入 AstrBot 的插件目录
2. 在 AstrBot 管理界面启用插件
3. 配置 SMTP 信息
4. 重启 AstrBot 或热加载插件

## 与 v1.0.0 的区别

v2.0.0 完全重写了架构：
- 使用 LLM Tool 替代手动意图解析
- 交互语言完全基于人设
- 代码更简洁，依赖更少
- 参考了 [astrbot_plugin_mailer](https://github.com/FFFold/astrbot_plugin_mailer) 的实现
