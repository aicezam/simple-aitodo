# app/services/dify_client.py
import httpx
from typing import Optional, Dict, Any, List
from app.core.config import settings
from app.schemas import DifyChatRequest, DifyChatResponse # 确保导入

async def generate_content_with_dify(prompt: str, conversation_id: Optional[str] = None, user_id: str = "default_user") -> Optional[str]:
    if not settings.DIFY_API_KEY or not settings.DIFY_BASE_URL:
        print("错误：Dify API密钥或基础URL未配置。")
        return "Dify配置错误"

    api_url = f"{settings.DIFY_BASE_URL.rstrip('/')}/chat-messages"
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = DifyChatRequest(
        query=prompt,
        user=user_id,
        conversation_id=conversation_id,
        response_mode="blocking"
    )

    try:
        async with httpx.AsyncClient(timeout=360.0) as client: # Dify可能较慢
            response = await client.post(api_url, headers=headers, json=payload.model_dump(exclude_none=True))
            response.raise_for_status()
            dify_response_data = response.json()
            
            parsed_response = DifyChatResponse(**dify_response_data)
            print(f"Dify API 响应成功: {parsed_response.answer[:50]}...")
            return parsed_response.answer

    except httpx.HTTPStatusError as e:
        error_text = e.response.text
        print(f"Dify API 请求失败 (HTTP {e.response.status_code}): {error_text}")
        return f"Dify API 错误: {e.response.status_code} - {error_text[:100]}"
    except httpx.RequestError as e:
        print(f"Dify API 请求时发生网络错误: {e}")
        return f"Dify API 网络错误: {str(e)}"
    except Exception as e:
        print(f"调用Dify API或处理响应时发生未知错误: {e}")
        return f"Dify 未知错误: {str(e)}"