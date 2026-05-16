"""
邮件发送助手插件
基于 LLM Tool 实现，支持人设化交互和精致 HTML 邮件
"""

import asyncio
import smtplib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any

from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("email_sender", "势魏延", "智能邮件发送插件，基于 LLM Tool 实现，支持人设化交互", "v2.0.0", "https://github.com/TsoiTZF/email_sender")
class EmailSenderPlugin(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.context = context
        self.config = config

        # 注册 LLM Tool
        if config.get("enable_llm_tool", True):
            self.context.add_llm_tools(self._build_send_email_tool())

    def _build_send_email_tool(self) -> FunctionTool:
        """构建 send_email 工具"""
        return FunctionTool(
            name="send_email",
            description=(
                "在用户明确要求发送邮件时，通过 SMTP 发送邮件。"
                "支持纯文本和 HTML 格式。必须具备清晰的收件人、主题和正文。"
                "如果用户说「给他发邮件」但没指定邮箱，可以先询问。"
                "邮件内容应该根据人设风格来生成。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "收件人邮箱地址。如果是 QQ 号，自动转换为 QQ 邮箱格式。",
                    },
                    "subject": {
                        "type": "string",
                        "description": "邮件主题。",
                    },
                    "content": {
                        "type": "string",
                        "description": "邮件正文内容，纯文本格式。",
                    },
                    "is_html": {
                        "type": "boolean",
                        "description": "content 是否为 HTML 格式，默认 false。",
                        "default": False,
                    },
                },
                "required": ["to", "subject", "content"],
            },
        )

    def _resolve_email(self, to: str) -> str:
        """解析收件人邮箱，支持 QQ 号自动转换"""
        to = to.strip()
        # 如果是纯数字，当作 QQ 号处理
        if re.match(r'^\d{5,12}$', to):
            return f"{to}@qq.com"
        # 验证邮箱格式
        if re.match(r'^[\w.-]+@[\w.-]+\.\w+$', to):
            return to
        raise ValueError(f"无效的邮箱地址或 QQ 号: {to}")

    def _generate_html_content(self, subject: str, content: str) -> str:
        """生成 HTML 格式邮件"""
        paragraphs = content.split('\n')
        html_paragraphs = []
        for p in paragraphs:
            p = p.strip()
            if p:
                html_paragraphs.append(f'<p style="margin: 0 0 15px 0; line-height: 1.6;">{p}</p>')
        body_html = '\n'.join(html_paragraphs)

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f5f5f5; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color: #f5f5f5;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                    <tr>
                        <td style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">{subject}</h1>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 40px 30px;">
                            {body_html}
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 20px 30px; border-radius: 0 0 12px 12px; text-align: center;">
                            <p style="margin: 0; color: #6c757d; font-size: 12px;">
                                此邮件由 AstrBot 邮件助手自动发送
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    async def send_email_tool(self, event: AstrMessageEvent, **payload: Any) -> str:
        """LLM Tool 回调函数"""
        try:
            to = payload.get("to", "")
            subject = payload.get("subject", "")
            content = payload.get("content", "")
            is_html = payload.get("is_html", False)

            # 解析收件人
            to_email = self._resolve_email(to)

            # SMTP 配置
            smtp_server = self.config.get("smtp.server", "smtp.qq.com")
            smtp_port = self.config.get("smtp.port", 465)
            smtp_email = self.config.get("smtp.email", "")
            smtp_password = self.config.get("smtp.password", "")
            use_ssl = self.config.get("smtp.use_ssl", True)

            if not smtp_email or not smtp_password:
                return "邮件发送失败：SMTP 配置不完整，请在插件配置中填写 smtp.email 和 smtp.password"

            # 创建邮件
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_email
            msg['To'] = to_email
            msg['Subject'] = subject

            # 添加纯文本版本
            text_part = MIMEText(content, 'plain', 'utf-8')
            msg.attach(text_part)

            # 添加 HTML 版本
            if not is_html:
                html_content = self._generate_html_content(subject, content)
            else:
                html_content = content
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)

            # 发送邮件
            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()

            server.login(smtp_email, smtp_password)
            server.send_message(msg)
            server.quit()

            logger.info(f"邮件发送成功: {to_email}")
            return f"邮件发送成功！收件人: {to_email}，主题: {subject}"

        except ValueError as e:
            return f"参数错误：{e}"
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return f"邮件发送失败：{e}"

    @filter.command("mail_test")
    async def mail_test(self, event: AstrMessageEvent, recipient: str = ""):
        """测试邮件发送"""
        if not recipient.strip():
            yield event.plain_result("用法: /mail_test recipient@example.com")
            return

        result = await self.send_email_tool(
            event,
            to=recipient,
            subject="AstrBot 邮件插件测试",
            content="这是一封由 email_sender 插件发送的测试邮件。"
        )
        yield event.plain_result(result)

    @filter.command("mail_config_check")
    async def mail_config_check(self, event: AstrMessageEvent):
        """检查 SMTP 配置"""
        smtp_server = self.config.get("smtp.server", "smtp.qq.com")
        smtp_port = self.config.get("smtp.port", 465)
        smtp_email = self.config.get("smtp.email", "")
        has_password = bool(self.config.get("smtp.password", ""))

        yield event.plain_result(
            f"邮件插件配置：\n"
            f"SMTP 服务器: {smtp_server}:{smtp_port}\n"
            f"发件邮箱: {smtp_email or '未配置'}\n"
            f"授权码: {'已配置' if has_password else '未配置'}\n"
            f"LLM Tool: {'已启用' if self.config.get('enable_llm_tool', True) else '已禁用'}"
        )
