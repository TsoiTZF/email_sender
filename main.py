"""
邮件发送助手插件
支持 LLM 意图判断、多轮对话确认收件人、根据人设生成邮件内容
"""

import asyncio
import json
import smtplib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain
from astrbot.api import logger


@dataclass
class EmailSession:
    """邮件发送会话状态"""
    target_qq: str = ""  # 目标 QQ 号
    target_email: str = ""  # 目标邮箱
    subject: str = ""  # 邮件主题
    content: str = ""  # 邮件内容
    step: str = "init"  # 会话步骤: init -> ask_email -> confirm -> done
    original_message: str = ""  # 原始用户消息


@register("email_sender", "势魏延", "智能邮件发送插件，支持 LLM 意图判断、多轮对话确认收件人、根据人设生成邮件内容", "v1.0.0", "https://github.com/势魏延/email_sender")
class EmailSenderPlugin(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.config = config
        self.sessions: Dict[str, EmailSession] = {}  # 用户会话存储

        # 默认意图判断提示词
        self.intent_prompt = config.get("llm.intent_prompt", "") or """你是一个邮件发送助手。请判断用户的消息是否包含发送邮件的意图。

用户消息: {message}

请以 JSON 格式回复，包含以下字段:
- "is_email_intent": boolean，是否包含发邮件意图
- "target_qq": string，目标 QQ 号（如果提到的话，否则为空字符串。如果消息中有被 @ 的用户，优先使用被 @ 的用户 QQ 号）
- "target_email": string，目标邮箱（如果明确提到的话，否则为空字符串）
- "subject": string，邮件主题（如果提到的话，否则为空字符串）
- "content_hint": string，邮件内容提示（如果提到的话，否则为空字符串）

注意：如果用户消息中 @ 了某人，且意图是给被 @ 的人发邮件，则 target_qq 应该是被 @ 的用户 QQ 号。

只回复 JSON，不要有其他文字。"""

        # 默认邮件内容生成提示词
        self.content_prompt = config.get("llm.content_prompt", "") or """你是一个邮件内容生成助手。请根据以下信息生成一封简洁、得体的邮件内容。

主题: {subject}
内容提示: {content_hint}
收件人: {recipient}

要求:
1. 内容简洁，不要太长（100-200字左右）
2. 不要使用 Markdown 格式
3. 直接输出邮件正文内容，不要有其他说明
4. 内容要有礼貌，结构清晰，适当分段"""

        # 人设化提示语生成模板
        self.persona_prompt_template = """你需要用指定的人设风格来回复用户。只回复一句话，不要有其他说明。

人设：{persona}
场景：{scene}
要求：{requirement}"""

    def _get_persona(self, event: AstrMessageEvent = None) -> str:
        """获取当前会话的人设"""
        try:
            # 从 provider_manager 获取人格列表
            personas = self.context.provider_manager.personas
            if not personas:
                return ""

            # 如果有 event，尝试获取当前会话的人格 ID
            if event:
                try:
                    umo = event.unified_msg_origin
                    cid = self.context.conversation_manager.get_curr_conversation_id(umo)
                    if cid:
                        conversation = self.context.conversation_manager.get_conversation(
                            unified_msg_origin=umo,
                            conversation_id=cid,
                            create_if_not_exists=True,
                        )
                        if conversation and conversation.persona_id and conversation.persona_id != "[%None]":
                            # 从人格列表中找到对应的人格
                            for persona in personas:
                                if persona.get("name") == conversation.persona_id:
                                    return persona.get("prompt", "")
                except Exception:
                    pass

            # 如果没有指定人格或获取失败，使用默认人格
            # 尝试获取默认人格
            try:
                if hasattr(self.context, 'persona_manager') and self.context.persona_manager.selected_default_persona_v3:
                    default_persona_name = self.context.persona_manager.selected_default_persona_v3.get("name")
                    if default_persona_name:
                        for persona in personas:
                            if persona.get("name") == default_persona_name:
                                return persona.get("prompt", "")
            except Exception:
                pass

            # 如果还是没有，使用第一个人格
            if personas:
                return personas[0].get("prompt", "")
        except Exception:
            pass
        return ""

    async def _generate_persona_reply(self, scene: str, requirement: str, event: AstrMessageEvent = None) -> str:
        """根据人设生成回复"""
        persona = self._get_persona(event)
        if not persona:
            return ""

        prompt = self.persona_prompt_template.format(
            persona=persona,
            scene=scene,
            requirement=requirement
        )
        reply = await self._call_llm(prompt)
        return reply.strip() if reply else ""

    def _get_session_key(self, event: AstrMessageEvent) -> str:
        """获取会话唯一标识"""
        return f"{event.get_sender_id()}:{event.get_group_id()}"

    def _get_session(self, event: AstrMessageEvent) -> EmailSession:
        """获取或创建会话"""
        key = self._get_session_key(event)
        if key not in self.sessions:
            self.sessions[key] = EmailSession()
        return self.sessions[key]

    def _clear_session(self, event: AstrMessageEvent):
        """清除会话"""
        key = self._get_session_key(event)
        if key in self.sessions:
            del self.sessions[key]

    def _extract_at_qq(self, event: AstrMessageEvent) -> str:
        """提取消息中 @ 的用户 QQ 号"""
        if not event.message_obj or not event.message_obj.message:
            return ""
        for comp in event.message_obj.message:
            if isinstance(comp, At):
                return str(comp.qq)
        return ""

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        try:
            provider = self.context.get_using_provider()
            if not provider:
                return ""
            result = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                image_urls=[]
            )
            return result.completion_text if result else ""
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return ""

    async def _parse_intent(self, message: str, at_qq: str = "") -> dict:
        """解析用户意图"""
        # 如果有 @ 用户，在提示词中提供
        at_info = f"\n被 @ 的用户 QQ 号: {at_qq}" if at_qq else ""
        prompt = self.intent_prompt.format(message=message) + at_info

        response = await self._call_llm(prompt)

        try:
            # 尝试提取 JSON
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

        return {"is_email_intent": False}

    async def _generate_email_content(self, subject: str, content_hint: str, recipient: str) -> str:
        """生成邮件内容"""
        # 获取人设
        persona = self._get_persona()

        # 构建提示词
        persona_info = f"\n你的人设是：{persona}" if persona else ""
        prompt = self.content_prompt.format(
            subject=subject,
            content_hint=content_hint or "请根据主题生成合适的内容",
            recipient=recipient
        ) + persona_info

        content = await self._call_llm(prompt)
        return content.strip() if content else ""

    def _generate_html_content(self, subject: str, content: str) -> str:
        """生成 HTML 格式邮件内容"""
        # 将换行符转换为 HTML 段落
        paragraphs = content.split('\n')
        html_paragraphs = []
        for p in paragraphs:
            p = p.strip()
            if p:
                html_paragraphs.append(f'<p style="margin: 0 0 15px 0; line-height: 1.6;">{p}</p>')
        body_html = '\n'.join(html_paragraphs)

        html_template = f"""<!DOCTYPE html>
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
                    <!-- 头部 -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">{subject}</h1>
                        </td>
                    </tr>
                    <!-- 内容 -->
                    <tr>
                        <td style="padding: 40px 30px;">
                            {body_html}
                        </td>
                    </tr>
                    <!-- 底部 -->
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
        return html_template

    async def _send_email(self, to_email: str, subject: str, content: str) -> bool:
        """发送邮件"""
        smtp_server = self.config.get("smtp.server", "smtp.qq.com")
        smtp_port = self.config.get("smtp.port", 465)
        smtp_email = self.config.get("smtp.email", "")
        smtp_password = self.config.get("smtp.password", "")
        use_ssl = self.config.get("smtp.use_ssl", True)

        if not smtp_email or not smtp_password:
            logger.error("SMTP 配置不完整，请检查 smtp.email 和 smtp.password")
            return False

        try:
            # 创建邮件
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_email
            msg['To'] = to_email
            msg['Subject'] = subject

            # 添加纯文本版本
            text_part = MIMEText(content, 'plain', 'utf-8')
            msg.attach(text_part)

            # 添加 HTML 版本
            html_content = self._generate_html_content(subject, content)
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
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    async def _get_reply(self, scene: str, requirement: str, fallback: str, event: AstrMessageEvent = None) -> str:
        """获取人设化回复，如果没有人设则返回默认回复"""
        reply = await self._generate_persona_reply(scene, requirement, event)
        return reply if reply else fallback

    @filter.command("发邮件")
    async def handle_email_command(self, event: AstrMessageEvent):
        """处理 /发邮件 命令"""
        session = self._get_session(event)
        message = event.message_str.strip()

        # 移除命令前缀
        message = re.sub(r'^/发邮件\s*', '', message).strip()

        if not message:
            session.step = "ask_target"
            reply = await self._get_reply(
                "用户触发了发邮件命令，但没有提供收件人",
                "询问用户收件人的 QQ 号或邮箱地址",
                "请告诉我收件人的 QQ 号或邮箱地址～",
                event
            )
            event.set_result(reply)
            return

        # 先用正则提取，不依赖 LLM
        # 检查是否是邮箱
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', message)
        if email_match:
            session.target_email = email_match.group()
            session.step = "ask_subject"
            reply = await self._get_reply(
                f"用户要发邮件到 {session.target_email}",
                "确认收件邮箱，并询问邮件主题",
                f"好的，收件邮箱是 {session.target_email}。请告诉我邮件主题～",
                event
            )
            event.set_result(reply)
            return

        # 检查是否是 QQ 号
        qq_match = re.search(r'\b\d{5,12}\b', message)
        if qq_match:
            session.target_qq = qq_match.group()
            session.step = "ask_email_type"
            reply = await self._get_reply(
                f"用户要给 QQ 号 {session.target_qq} 发邮件",
                "询问是否发送到 QQ 邮箱，或提供其他邮箱地址",
                f"好的，目标 QQ 号是 {session.target_qq}。请问发到 QQ 邮箱吗？（回复「是」或提供其他邮箱地址）",
                event
            )
            event.set_result(reply)
            return

        # 都没匹配到，调用 LLM 尝试解析
        intent = await self._parse_intent(message)

        if intent.get("target_email"):
            session.target_email = intent["target_email"]
            session.step = "ask_subject"
            reply = await self._get_reply(
                f"用户要发邮件到 {session.target_email}",
                "确认收件邮箱，并询问邮件主题",
                f"好的，收件邮箱是 {session.target_email}。请告诉我邮件主题～",
                event
            )
            event.set_result(reply)
        elif intent.get("target_qq"):
            session.target_qq = intent["target_qq"]
            session.step = "ask_email_type"
            reply = await self._get_reply(
                f"用户要给 QQ 号 {session.target_qq} 发邮件",
                "询问是否发送到 QQ 邮箱，或提供其他邮箱地址",
                f"好的，目标 QQ 号是 {session.target_qq}。请问发到 QQ 邮箱吗？（回复「是」或提供其他邮箱地址）",
                event
            )
            event.set_result(reply)
        else:
            session.step = "ask_target"
            reply = await self._get_reply(
                "用户触发了发邮件命令，但没有提供收件人",
                "询问用户收件人的 QQ 号或邮箱地址",
                "请告诉我收件人的 QQ 号或邮箱地址～",
                event
            )
            event.set_result(reply)

    @filter.event_message_type
    async def handle_message(self, event: AstrMessageEvent):
        """处理所有消息，支持多轮对话和意图判断"""
        session = self._get_session(event)
        message = event.message_str.strip()

        # 如果是命令，跳过（由 handle_email_command 处理）
        if message.startswith("/"):
            return

        # 处理会话流程
        if session.step == "ask_target":
            await self._handle_ask_target(event, session, message)
        elif session.step == "ask_email_type":
            await self._handle_ask_email_type(event, session, message)
        elif session.step == "ask_subject":
            await self._handle_ask_subject(event, session, message)
        elif session.step == "ask_content":
            await self._handle_ask_content(event, session, message)
        elif session.step == "confirm":
            await self._handle_confirm(event, session, message)
        else:
            # 尝试意图判断
            await self._handle_intent_detection(event, session, message)

    async def _handle_ask_target(self, event: AstrMessageEvent, session: EmailSession, message: str):
        """处理收件人输入"""
        # 检查是否是邮箱
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', message)
        if email_match:
            session.target_email = email_match.group()
            session.step = "ask_subject"
            reply = await self._get_reply(
                f"用户提供了收件邮箱 {session.target_email}",
                "确认收件邮箱，并询问邮件主题",
                f"好的，收件邮箱是 {session.target_email}。请告诉我邮件主题～",
                event
            )
            event.set_result(reply)
            return

        # 检查是否是 QQ 号
        qq_match = re.search(r'\b\d{5,12}\b', message)
        if qq_match:
            session.target_qq = qq_match.group()
            session.step = "ask_email_type"
            reply = await self._get_reply(
                f"用户提供了 QQ 号 {session.target_qq}",
                "询问是否发送到 QQ 邮箱，或提供其他邮箱地址",
                f"好的，目标 QQ 号是 {session.target_qq}。请问发到 QQ 邮箱吗？（回复「是」或提供其他邮箱地址）",
                event
            )
            event.set_result(reply)
            return

        reply = await self._get_reply(
            "用户提供的收件人信息无法识别",
            "提示用户输入有效的 QQ 号或邮箱地址",
            "没有识别到有效的 QQ 号或邮箱地址，请重新输入～",
            event
        )
        event.set_result(reply)

    async def _handle_ask_email_type(self, event: AstrMessageEvent, session: EmailSession, message: str):
        """处理邮箱类型确认"""
        if message in ["是", "对", "好", "嗯", "y", "yes", "Y", "Yes"]:
            session.target_email = f"{session.target_qq}@qq.com"
            session.step = "ask_subject"
            reply = await self._get_reply(
                f"用户确认发送到 QQ 邮箱 {session.target_email}",
                "确认收件邮箱，并询问邮件主题",
                f"好的，将发送到 {session.target_email}。请告诉我邮件主题～",
                event
            )
            event.set_result(reply)
        elif "@" in message:
            session.target_email = message
            session.step = "ask_subject"
            reply = await self._get_reply(
                f"用户提供了其他邮箱 {session.target_email}",
                "确认收件邮箱，并询问邮件主题",
                f"好的，收件邮箱是 {session.target_email}。请告诉我邮件主题～",
                event
            )
            event.set_result(reply)
        else:
            reply = await self._get_reply(
                "用户没有明确回复邮箱类型",
                "提示用户回复「是」发送到 QQ 邮箱，或提供其他邮箱地址",
                "请回复「是」发送到 QQ 邮箱，或提供其他邮箱地址～",
                event
            )
            event.set_result(reply)

    async def _handle_ask_subject(self, event: AstrMessageEvent, session: EmailSession, message: str):
        """处理主题输入"""
        session.subject = message
        session.step = "ask_content"
        reply = await self._get_reply(
            f"用户提供了邮件主题「{session.subject}」",
            "确认主题已记录，并询问邮件内容要点，或提示回复「自动生成」",
            "好的，主题已记录。请告诉我邮件内容要点（我会帮你润色），或回复「自动生成」让我根据主题生成～",
            event
        )
        event.set_result(reply)

    async def _handle_ask_content(self, event: AstrMessageEvent, session: EmailSession, message: str):
        """处理内容输入"""
        if message in ["自动生成", "自动生成", "生成", "auto"]:
            session.content = ""
        else:
            session.content = message

        # 生成邮件内容
        recipient = session.target_email or f"{session.target_qq}@qq.com"
        generated_content = await self._generate_email_content(
            session.subject,
            session.content,
            recipient
        )

        if not generated_content:
            reply = await self._get_reply(
                "邮件内容生成失败",
                "提示用户重试",
                "邮件内容生成失败，请重试～",
                event
            )
            event.set_result(reply)
            self._clear_session(event)
            return

        session.content = generated_content
        session.step = "confirm"

        # 显示预览
        preview = f"""═══════════════════
    邮件预览
═══════════════════

收件人: {session.target_email}
主题: {session.subject}

{session.content}

─────────────────
确认发送吗？（回复「是」发送，「否」取消）"""

        event.set_result(preview)

    async def _handle_confirm(self, event: AstrMessageEvent, session: EmailSession, message: str):
        """处理确认"""
        if message in ["是", "对", "好", "嗯", "y", "yes", "Y", "Yes"]:
            # 发送邮件
            success = await self._send_email(
                session.target_email,
                session.subject,
                session.content
            )

            if success:
                reply = await self._get_reply(
                    f"邮件已成功发送到 {session.target_email}",
                    "告知用户邮件发送成功，语气轻松愉快",
                    f"邮件已成功发送到 {session.target_email}～",
                    event
                )
                event.set_result(reply)
            else:
                reply = await self._get_reply(
                    "邮件发送失败",
                    "告知用户邮件发送失败，建议检查配置或稍后重试",
                    "邮件发送失败，请检查 SMTP 配置或稍后重试～",
                    event
                )
                event.set_result(reply)
        else:
            reply = await self._get_reply(
                "用户取消了邮件发送",
                "告知用户已取消发送",
                "已取消发送～",
                event
            )
            event.set_result(reply)

        self._clear_session(event)

    async def _handle_intent_detection(self, event: AstrMessageEvent, session: EmailSession, message: str):
        """处理意图检测"""
        # 提取 @ 的用户 QQ 号
        at_qq = self._extract_at_qq(event)

        # 检查是否有发邮件意图
        intent = await self._parse_intent(message, at_qq)

        if not intent.get("is_email_intent"):
            return  # 没有发邮件意图，不处理

        # 有发邮件意图，开始会话
        # 优先使用 @ 的用户 QQ 号
        target_qq = at_qq or intent.get("target_qq", "")
        target_email = intent.get("target_email", "")

        if target_email:
            session.target_email = target_email
            session.step = "ask_subject"
            reply = await self._get_reply(
                f"用户要发邮件到 {session.target_email}",
                "确认收件邮箱，并询问邮件主题",
                f"好的，收件邮箱是 {session.target_email}。请告诉我邮件主题～",
                event
            )
            event.set_result(reply)
        elif target_qq:
            session.target_qq = target_qq
            session.step = "ask_email_type"
            reply = await self._get_reply(
                f"用户要给 QQ 号 {session.target_qq} 发邮件",
                "询问是否发送到 QQ 邮箱，或提供其他邮箱地址",
                f"好的，目标 QQ 号是 {session.target_qq}。请问发到 QQ 邮箱吗？（回复「是」或提供其他邮箱地址）",
                event
            )
            event.set_result(reply)
        else:
            session.step = "ask_target"
            reply = await self._get_reply(
                "检测到用户想发邮件，但没有提供收件人",
                "询问收件人的 QQ 号或邮箱地址",
                "检测到你想发邮件，请告诉我收件人的 QQ 号或邮箱地址～",
                event
            )
            event.set_result(reply)

        if intent.get("subject"):
            session.subject = intent["subject"]
        if intent.get("content_hint"):
            session.content = intent["content_hint"]
