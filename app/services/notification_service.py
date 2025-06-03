# app/services/notification_service.py
import httpx
import smtplib 
from email.mime.text import MIMEText
from email.header import Header 
from typing import Dict, Any, Optional, List # 添加 List
import asyncio 
import json # 导入 json 用于处理可能的JSON字符串到列表的转换

from app.core.config import settings
from app.schemas import WebhookChannelConfig, EmailChannelConfig


def replace_placeholders_in_data(data_structure: Any, replacements: Dict[str, Optional[str]]) -> Any:
    """
    递归地替换数据结构 (字典, 列表, 字符串) 中的占位符。
    占位符格式: {{key}} 或 {key}
    如果 replacements 字典中某个键对应的值是 None，则占位符会被替换为空字符串。
    """
    if isinstance(data_structure, dict):
        return {key: replace_placeholders_in_data(value, replacements) for key, value in data_structure.items()}
    elif isinstance(data_structure, list):
        return [replace_placeholders_in_data(item, replacements) for item in data_structure]
    elif isinstance(data_structure, str):
        modified_string = data_structure
        for placeholder_key, replacement_value in replacements.items():
            value_to_replace_with = str(replacement_value) if replacement_value is not None else ""
            # 替换双大括号和单大括号的占位符
            modified_string = modified_string.replace(f"{{{{{placeholder_key}}}}}", value_to_replace_with)
            modified_string = modified_string.replace(f"{{{placeholder_key}}}", value_to_replace_with)
        return modified_string
    else:
        return data_structure

async def send_webhook_notification(
    config: WebhookChannelConfig, 
    base_content: str, # 纯净的提醒内容，不含@
    final_recipient_id: str, # 最终的接收者 (个人wxid或群聊id)
    triggering_user_id: Optional[str] = None, # 原始触发任务的用户ID
    mention_nickname_if_group: Optional[str] = None, # 如果是群聊，且有昵称，则用此昵称@
    at_target_user_id_if_group: Optional[str] = None, # 如果是群聊，需要@的用户的wxid
    task_name: Optional[str] = None, 
    task_description: Optional[str] = None
) -> bool:
    """发送Webhook通知，并根据上下文正确构造payload。"""
    
    payload_template_to_use = config.payload_template 
    
    # 如果没有模板，使用非常基础的payload（这种情况应该较少，因为默认webhook会提供模板）
    if not payload_template_to_use:
        print(f"警告: Webhook ({config.url}) 未配置有效的 payload_template，将使用简单的默认payload。")
        # 即使没有模板，也尝试根据上下文发送
        final_text_content = base_content
        at_list_for_payload = []
        if at_target_user_id_if_group: # 如果是群聊且需要@人
            mention_prefix = f"@{mention_nickname_if_group if mention_nickname_if_group else at_target_user_id_if_group} "
            final_text_content = f"{mention_prefix}{base_content}"
            at_list_for_payload = [at_target_user_id_if_group]
        
        simple_payload = {
            "MsgItem": [{
                "ToUserName": final_recipient_id,
                "TextContent": final_text_content,
                "MsgType": 0 # 文本消息
            }]
        }
        if at_list_for_payload: # 只有当需要@人时才加入AtWxIDList
             simple_payload["MsgItem"][0]["AtWxIDList"] = at_list_for_payload
        
        payload = simple_payload
        print(f"使用简单构造的payload: {str(payload)[:200]}")

    else: # 有payload模板，进行占位符替换和特定字段的调整
        # 1. 准备最终的提醒文本 (TextContent)
        final_text_content_for_template = base_content
        if at_target_user_id_if_group: # 如果是群聊且需要@人
            mention_prefix = f"@{mention_nickname_if_group if mention_nickname_if_group else at_target_user_id_if_group} "
            final_text_content_for_template = f"{mention_prefix}{base_content}"
        
        # 2. 定义通用的占位符替换表
        placeholder_replacements = {
            "content": final_text_content_for_template, # {{content}} 将被替换为可能带@的完整文本
            "base_content": base_content, # 提供一个不带@的原始内容占位符，如果模板需要
            "user_id": final_recipient_id, # {{user_id}} 在模板中通常指代 ToUserName
            "triggering_user_id": triggering_user_id or "",
            "target_chat_id": final_recipient_id, # 与user_id类似，但更明确是目标聊天
            "mention_nickname": mention_nickname_if_group or "",
            "task_name": task_name or "",
            "task_description": task_description or ""
        }
        
        # 3. 使用通用替换函数处理整个模板
        payload = replace_placeholders_in_data(json.loads(json.dumps(payload_template_to_use)), placeholder_replacements) # 深拷贝模板再替换
        print(f"Webhook payload 模板初步替换完成。关联user_id(触发者): {triggering_user_id}, 最终接收者: {final_recipient_id}")

        # 4. 特殊处理 AtWxIDList (根据您的默认模板结构)
        # 您的默认模板是 "AtWxIDList": ["string"]
        # 我们需要根据情况覆盖这个字段
        if isinstance(payload, dict) and "MsgItem" in payload and \
           isinstance(payload["MsgItem"], list) and len(payload["MsgItem"]) > 0 and \
           isinstance(payload["MsgItem"][0], dict):
            
            if at_target_user_id_if_group: # 如果是群聊且需要@
                payload["MsgItem"][0]["AtWxIDList"] = [at_target_user_id_if_group]
                print(f"为群聊提醒设置 AtWxIDList: {[at_target_user_id_if_group]}")
            elif "AtWxIDList" in payload["MsgItem"][0] and payload["MsgItem"][0]["AtWxIDList"] == ["string"]:
                # 如果不是群聊@场景，且模板是默认的["string"]，则清空或设为符合API的空值
                payload["MsgItem"][0]["AtWxIDList"] = [] # 或者根据您的微信API要求设为None或不传
                print(f"非群聊@场景，将默认的 AtWxIDList: [\"string\"] 清空。")
            # 如果模板中 AtWxIDList 本身就是通过占位符 {{at_list_json}} 等方式动态生成的，
            # 并且 placeholder_replacements 中已包含对应的值，则上面的通用替换可能已处理好。
            # 但鉴于您的模板是固定的 ["string"]，我们需要这种直接修改。

        # 5. 确保 ToUserName 被正确设置 (如果模板中的占位符不是 {{user_id}} 或 {{target_chat_id}})
        # 通用替换函数应已处理好模板中如 {{user_id}} 的情况，这里是双重保障或针对不同模板的适应。
        if isinstance(payload, dict) and "MsgItem" in payload and \
           isinstance(payload["MsgItem"], list) and len(payload["MsgItem"]) > 0 and \
           isinstance(payload["MsgItem"][0], dict):
            if payload["MsgItem"][0].get("ToUserName") != final_recipient_id:
                 print(f"修正 ToUserName 从 '{payload['MsgItem'][0].get('ToUserName')}' 为 '{final_recipient_id}'")
                 payload["MsgItem"][0]["ToUserName"] = final_recipient_id
    
    print(f"最终准备发送的Webhook payload (部分): {str(payload)[:300]}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            request_method = config.method.upper()
            if request_method == "POST":
                response = await client.post(config.url, json=payload, headers=config.headers)
            elif request_method == "GET": 
                params_for_get = payload if isinstance(payload, dict) else {"content": base_content} # 简化GET参数
                if triggering_user_id and "user_id" not in params_for_get : # 将触发者ID作为user_id参数给GET（如果模板未使用）
                    params_for_get["user_id"] = triggering_user_id
                response = await client.get(config.url, headers=config.headers, params=params_for_get)
            else:
                print(f"不支持的Webhook HTTP方法: {config.method}")
                return False
            
            response.raise_for_status()
            print(f"Webhook 通知已发送至 {config.url}, 方法: {request_method}, 状态: {response.status_code}, 实际接收者: {final_recipient_id}, 触发者: {triggering_user_id}")
            return True
    except httpx.HTTPStatusError as e:
        print(f"发送Webhook通知至 {config.url} 失败 (HTTP {e.response.status_code}): {e.response.text[:200]}")
        return False
    except httpx.RequestError as e:
        print(f"发送Webhook通知至 {config.url} 失败 (网络错误): {e}")
        return False
    except Exception as e:
        print(f"发送Webhook时发生未知错误: {e}")
        import traceback
        traceback.print_exc()
        return False

# --- 邮件发送逻辑 (与之前相同，保持不变) ---
def _send_email_sync(config: EmailChannelConfig, content: str) -> bool:
    # ... (代码同上次)
    if not all([settings.MAIL_SERVER, settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_SENDER]):
        print("错误：邮件服务器配置不完整。")
        return False
    msg = MIMEText(content, 'plain', 'utf-8')
    msg['Subject'] = Header(config.subject, 'utf-8').encode() 
    msg['From'] = settings.MAIL_SENDER
    msg['To'] = config.recipient_email
    try:
        server: Optional[smtplib.SMTP] = None
        if settings.MAIL_PORT == 587: 
            server = smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT, timeout=10)
            server.starttls()
        elif settings.MAIL_PORT == 465: 
            server = smtplib.SMTP_SSL(settings.MAIL_SERVER, settings.MAIL_PORT, timeout=10)
        else: 
            server = smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT, timeout=10)
        if server is None: 
            print(f"无法根据端口 {settings.MAIL_PORT} 初始化SMTP服务器。")
            return False
        server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
        server.sendmail(settings.MAIL_SENDER, [config.recipient_email], msg.as_string())
        server.quit()
        print(f"邮件通知已发送至 {config.recipient_email}，主题: {config.subject}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"邮件登录失败: {e}")
    except smtplib.SMTPException as e:
        print(f"发送邮件时发生SMTP错误: {e}")
    except Exception as e:
        print(f"发送邮件时发生未知错误: {e}")
    return False

async def send_email_notification(config: EmailChannelConfig, content: str) -> bool:
    loop = asyncio.get_event_loop()
    try:
        success = await loop.run_in_executor(None, _send_email_sync, config, content)
        return success
    except Exception as e:
        print(f"执行邮件发送的异步任务时出错: {e}")
        return False
