# app/services/nlp_service.py
import httpx
import json
import datetime
from typing import Optional, Dict, Any, List
from app.core.config import settings 

def get_task_parsing_prompt(natural_language_query: str, current_time_str: str, requesting_user_id: Optional[str]) -> List[Dict[str, str]]:
    """
    构建用于请求AI模型解析自然语言任务的提示信息。
    natural_language_query: 完整的用户原始输入，可能包含如 '[群ID:xxx,好友昵称:yyy]' 的前缀。
    current_time_str: 当前服务器本地时间字符串。
    requesting_user_id: 发起此NLP请求的用户ID，可作为默认的 triggering_user_id。
    """
    # 确保DEFAULT_WEBHOOK_HEADERS和DEFAULT_WEBHOOK_BODY_TEMPLATE是JSON字符串或None
    # config.py 中已经将它们解析为Python dict，这里需要转回JSON字符串给AI看
    default_headers_json_str_for_prompt = json.dumps(settings.DEFAULT_WEBHOOK_HEADERS) if settings.DEFAULT_WEBHOOK_HEADERS is not None else "{}"
    default_body_template_json_str_for_prompt = json.dumps(settings.DEFAULT_WEBHOOK_BODY_TEMPLATE) if settings.DEFAULT_WEBHOOK_BODY_TEMPLATE is not None else "{}"

    json_schema_description = """
    {{
      "operation": "string (必须. 用户的主要意图。有效值: 'CREATE_TASK', 'QUERY_TASKS', 'UPDATE_TASK', 'DELETE_TASK')",
      
      "task_name": "string (用于 'CREATE_TASK': 任务的简洁名称。用于 'QUERY_TASKS': 可选，提取的任务名关键词。用于 'UPDATE_TASK'/'DELETE_TASK': 可能作为 target_task_identifier 的一部分)",
      "description": "string (可选。用于 'CREATE_TASK': 任务的详细描述。用于 'QUERY_TASKS': 用户的原始查询或AI总结。用于 'UPDATE_TASK'/'DELETE_TASK': 可能包含上下文)",
      
      "triggering_user_id": "string (可选但重要。解析用户输入前缀（如 '[好友ID:xxx]'）得到的用户ID，或使用请求参数中提供的用户ID '{requesting_user_id}' 作为默认值。这是任务的原始触发者)，特殊情况：如果用户要求在群里提醒所有人或@所有人，则triggering_user_id值为`notify@all`",
      "target_chat_id": "string (可选但重要。解析用户输入前缀（如 '[群ID:yyy]'）得到的群ID。如果不是群聊或是私聊给机器人，此字段通常与 triggering_user_id 相同，或者使用 triggering_user_id)，如果要求在群里提醒所有人或@所有人，则值为群ID",
      "mention_user_nickname": "string (可选。解析用户输入前缀（如 '[好友昵称:zzz]'）得到的昵称。主要用于群聊中@原始触发者)，如果要求在群里提醒所有人或@所有人，则值为 `所有人`",

      "query_filters": {{ "(仅当 operation 为 'QUERY_TASKS' 时使用)"
        "status": "string (可选, 任务状态，请返回英文大写标准值: 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'PENDING_CALCULATION'. 如果用户说'进行中'，考虑'PENDING'或'RUNNING'. 如果不明确，可省略此字段或使用 'PENDING')",
        "keywords": "string (可选, 搜索任务名或描述的关键词)"
      }},

      "target_task_identifier": {{ "(当 operation 为 'UPDATE_TASK' 或 'DELETE_TASK' 时必须尝试填充)"
        "task_id": "string (可选, 如果用户直接提供了任务ID)",
        "task_name_keyword": "string (可选, 用户提及的用于识别目标任务的任务名称或关键词。例如，“修改打卡提醒”，这里可以是“打卡提醒”)"
      }},

      "update_fields": {{ "(仅当 operation 为 'UPDATE_TASK' 时使用, 包含用户想要修改的字段及其新值。字段名应严格匹配TaskInfoBase或其内嵌模型的字段名，包括 triggering_user_id, target_chat_id, webhook_channel等如果被要求更新)"
        // 示例: "task_name": "新的任务名称", "reminder_content": "新的纯净提醒内容", 
        // "webhook_channel": {{ "url": "new_url", ... }},
        // "one_time_specific_config": {{ "trigger_at": "YYYY-MM-DD HH:MM:SS" }}
      }},

      "webhook_channel": {{ "(用于 'CREATE_TASK'。如果用户未指定通知方式但需要通知，你应该优先考虑并填充此字段，可参考下面提供的系统默认Webhook配置。如果用户明确要求邮件，则填充 email_channel)"
        "url": "string (Webhook URL)",
        "method": "string (例如 'POST', 默认为 'POST')",
        "headers": {{ "additionalProp1": "string" }}, "(例如：{default_headers_json_str_for_prompt})"
        "payload_template": {{ "additionalProp1": "string", "content": "{{{{content}}}}", "ToUserName": "{{{{target_chat_id}}}}", "AtWxIDList": ["{{{{triggering_user_id}}}}"] }} "(这是一个示例结构，实际内容应从系统默认配置参考或用户指定。注意JSON中双大括号的转义)" 
      }},
      "email_channel": {{ "(用于 'CREATE_TASK'。仅当用户明确要求使用邮件通知时填充此字段)"
        "subject": "string (邮件主题)",
        "recipient_email": "string (收件人邮箱地址)"
      }},
      "reminder_content": "string (用于 'CREATE_TASK'。提醒的核心、纯净文本内容。不应包含用户ID、群ID、@某人等元数据或指令性前缀。例如，如果用户说 '[群ID:group123] 下午六点半提醒我： 下班了记得打卡', 此字段应为 '下班了记得打卡'。对于 'UPDATE_TASK'，此字段应在 update_fields 对象中提供，如果被更新)",
      "is_dify_generated": "boolean (用于 'CREATE_TASK'或'UPDATE_TASK'。仅当用户要求使用dify生成或AI生成内容时设为true, 默认为false)",
      "is_recurring": "boolean (用于 'CREATE_TASK'或'UPDATE_TASK'。一次性任务时设为false, 默认为false)",
      
      "cron_config": {{ "(用于 'CREATE_TASK'或'UPDATE_TASK'。当 is_recurring=true 时通常需要)"
        "cron_expression": "string (标准的5或6字段CRON表达式。若 is_lunar=true 或 limit_days 有值，则cron表达式通常只需 时、分 部分, 例如：每天中午十二点执行一次：'0 0 12 * * ?'; 每天14点到14：59分，每1分钟执行一次：'0 * 14 * * ?'; 每天14点到14：05分，每1分钟执行一次：'0 0-5 14 * * ?'; 每天14点到14：55分，每5分钟执行一次：'0 0/5 14 * * ?')",
        "start_time": "string (可选, YYYY-MM-DD HH:MM:SS，限制任务执行开始时间)",
        "end_time": "string (可选, YYYY-MM-DD HH:MM:SS，限制任务执行结束时间)",
        "limit_days": ["string"] (可选, 限制执行的日期类型数组。允许的值: "WORKDAY", "HOLIDAY", "WEEKEND", "WEEKDAY_ONLY". 如果用户说“工作日”，使用 ["WORKDAY"])",
        "is_lunar": "boolean (默认为false，仅当需要计算农历提醒任务时为true)",
        "lunar_month": "integer (可选, is_lunar=true时必需, 1-12)",
        "lunar_day": "integer (可选, is_lunar=true时必需, 1-30)"
      }},
      "countdown_config": {{ "(用于 'CREATE_TASK'或'UPDATE_TASK'。当 is_recurring=false 且非特定时间时使用)"
        "countdown_duration": "string (例如 '30m', '1d2h')"
      }},
      "one_time_specific_config": {{ "(用于 'CREATE_TASK'或'UPDATE_TASK'。当 is_recurring=false 且为特定时间点时使用)"
        "trigger_at": "string (YYYY-MM-DD HH:MM:SS)"
      }}
    }} 
    """ + f"""
    规则:
    1.  严格按照上述JSON结构和字段名输出。必须包含 "operation" 字段。
    2.  用户原始输入可能包含一个方括号括起来的前缀，格式如 `[群ID：<id>,好友ID：<id>,好友昵称：<昵称>]` (所有部分都是可选的)。你需要解析这个前缀：
        *   `群ID` 应映射到 `target_chat_id`。
        *   `好友ID` (如果存在于前缀中) 应优先映射到 `triggering_user_id`。如果前缀中无 `好友ID`，则使用请求参数中提供的 `requesting_user_id` ('{requesting_user_id}') 作为 `triggering_user_id`。
        *   `好友昵称` 应映射到 `mention_user_nickname`。
        *   如果解析出 `群ID`，但无 `好友ID`，则 `target_chat_id` 为群ID，`triggering_user_id` 仍按上述逻辑处理 (可能来自请求参数)。
        *   如果既无 `群ID` 也无 `好友ID`，则 `target_chat_id` 和 `triggering_user_id` 都应考虑使用请求参数中的 `requesting_user_id` ('{requesting_user_id}')。
        *   解析完前缀后，剩余部分是主要的指令内容。
        *   特殊情况：如果要求在群里提醒或@所有人，则，`target_chat_id` 为群ID，`mention_user_nickname` 为 "所有人"，`triggering_user_id` 值为 "notify@all"。
    3.  所有时间都应基于当前本地时间 ({current_time_str}) 解析和表达 (YYYY-MM-DD HH:MM:SS)。
    4.  对于 'CREATE_TASK': 
        *   `task_name` 和 `reminder_content` 必填。`reminder_content` 应为纯粹的提醒事项本身。
        *   必须提供 `webhook_channel` 或 `email_channel` 之一。如果用户未指定通知方式，优先生成 `webhook_channel`。
        *   你可以参考以下系统默认Webhook配置来构建 `webhook_channel` (如果适用且用户未指定其他方式):
            *   URL: `{settings.DEFAULT_WEBHOOK_URL if settings.DEFAULT_WEBHOOK_ENABLED else "N/A (默认Webhook未启用或未配置URL)"}`
            *   Method: `{settings.DEFAULT_WEBHOOK_METHOD if settings.DEFAULT_WEBHOOK_ENABLED else "N/A"}`
            *   Headers (JSON string for AI to parse into object if used): `{default_headers_json_str_for_prompt if settings.DEFAULT_WEBHOOK_ENABLED else "{{}}"}`
            *   Payload Template (JSON string for AI to parse into object if used): `{default_body_template_json_str_for_prompt if settings.DEFAULT_WEBHOOK_ENABLED else "{{}}"}`
            *   如果使用上述默认信息构造 `webhook_channel`，请确保其 `headers` 和 `payload_template` 字段在你的JSON输出中是有效的JSON对象，而不是字符串。
        *   根据用户输入判断 `is_recurring` 并提供相应的 `cron_config` 或 (`countdown_config`/`one_time_specific_config`)。
    5.  对于 'QUERY_TASKS': 尝试填充 `query_filters`。
    6.  对于 'UPDATE_TASK': 必须尝试填充 `target_task_identifier` 和 `update_fields`。`update_fields` 中只包含用户明确要修改的字段。
    7.  对于 'DELETE_TASK': 必须尝试填充 `target_task_identifier`。
    8.  `limit_days` 在 `cron_config` 中必须是一个字符串数组，例如 `["WORKDAY"]`。
    9.  输出必须是单一有效的JSON对象。不要包含任何解释性文本或```json markdown标记。
    10. 模型思考模式：关闭 /no_think
    """
    system_prompt_content = f"""
    你是一个多功能任务管理助手。你的任务是将用户的自然语言输入（可能包含特定格式的前缀）解析为一个结构化的JSON对象，用于执行任务的创建、查询、修改或删除操作。
    当前的服务器本地时间是: {current_time_str}。请根据这个时间来理解相对时间表述（如“明天”、“下周一”等）。
    请求的原始用户ID是: {requesting_user_id if requesting_user_id else "未提供"}。
    请严格按照以下JSON格式和规则输出。

    输出的JSON结构定义如下:
    {json_schema_description}
    """
    # natural_language_query 现在是完整的用户输入，包含潜在的前缀
    user_prompt_content = f"请将以下用户完整请求解析为JSON格式的指令对象：\n\n用户请求: \"{natural_language_query}\""

    return [
        {"role": "system", "content": system_prompt_content},
        {"role": "user", "content": user_prompt_content}
    ]

async def parse_natural_language_to_task_info(query: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not settings.AI_API_URL or not settings.AI_API_KEY or not settings.AI_MODEL_NAME:
        print("NLP服务错误: AI模型配置不完整。")
        return None

    current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 将原始查询和请求用户ID传递给prompt构造函数
    messages = get_task_parsing_prompt(query, current_time_str, user_id) 

    headers = {
        "Authorization": f"Bearer {settings.AI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": settings.AI_MODEL_NAME,
        "messages": messages,
        "response_format": {"type": "json_object"} 
    }
    
    print(f"NLP服务: 向 {settings.AI_API_URL} 发送请求 (模型: {settings.AI_MODEL_NAME}) "
          f"原始查询传递给AI: '{query[:100]}...'")
    # print(f"NLP服务DEBUG: 构造的发送给AI的messages: {json.dumps(messages, ensure_ascii=False, indent=2)}")


    try:
        async with httpx.AsyncClient(timeout=180.0) as client: 
            response = await client.post(settings.AI_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            response_data = response.json()
            
            if response_data.get("choices") and len(response_data["choices"]) > 0:
                message = response_data["choices"][0].get("message")
                if message and message.get("content"):
                    content_str = message["content"]
                    print(f"NLP服务: AI原始响应内容 (前500字符): {content_str[:500]}...")
                    try:
                        # 移除可能的Markdown代码块标记
                        if content_str.strip().startswith("```json"):
                            content_str = content_str.split("```json", 1)[1].rsplit("```", 1)[0].strip()
                        elif content_str.strip().startswith("```"): 
                            content_str = content_str.split("```", 1)[1].rsplit("```", 1)[0].strip()

                        parsed_json = json.loads(content_str)

                        # AI有时可能将limit_days返回为字符串而不是列表，这里尝试修复
                        def fix_limit_days_in_cron_config(cron_config_dict, context_msg=""):
                            if cron_config_dict and "limit_days" in cron_config_dict and isinstance(cron_config_dict["limit_days"], str):
                                print(f"NLP_SERVICE_FIX {context_msg}: 将 cron_config.limit_days 从字符串 ('{cron_config_dict['limit_days']}') 转换为列表。")
                                cron_config_dict["limit_days"] = [day.strip() for day in cron_config_dict["limit_days"].split(',') if day.strip()]


                        if "cron_config" in parsed_json and isinstance(parsed_json.get("cron_config"), dict):
                            fix_limit_days_in_cron_config(parsed_json["cron_config"], "(CREATE/TOP_LEVEL)")
                        
                        if parsed_json.get("operation") == "UPDATE_TASK" and \
                           "update_fields" in parsed_json and \
                           isinstance(parsed_json["update_fields"], dict) and \
                           "cron_config" in parsed_json["update_fields"] and \
                           isinstance(parsed_json["update_fields"]["cron_config"], dict):
                            fix_limit_days_in_cron_config(parsed_json["update_fields"]["cron_config"], "(UPDATE_FIELDS)")
                            
                        print(f"NLP服务: 成功解析AI响应为JSON: {json.dumps(parsed_json, ensure_ascii=False, indent=2)}")
                        return parsed_json
                    except json.JSONDecodeError as e:
                        print(f"NLP服务错误: 无法将AI响应解码为JSON: {e}")
                        print(f"NLP服务错误: 接收到的非JSON内容: {content_str}")
                        return None
                else:
                    print(f"NLP服务错误: AI响应中缺少预期的 'message' 或 'content' 字段。响应: {response_data}")
                    return None
            else:
                print(f"NLP服务错误: AI响应中缺少 'choices'。响应: {response_data}")
                return None
    except httpx.HTTPStatusError as e:
        error_text = e.response.text
        print(f"NLP服务错误: AI API 请求失败 (HTTP {e.response.status_code}): {error_text[:500]}")
        return None
    except httpx.RequestError as e:
        print(f"NLP服务错误: AI API 请求时发生网络错误: {e}")
        return None
    except Exception as e:
        print(f"NLP服务错误:调用AI API或处理响应时发生未知错误: {e}")
        import traceback
        traceback.print_exc()
        return None
