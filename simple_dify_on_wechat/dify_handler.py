# dify_handler.py
import requests
import json
import logging
import os
from urllib.parse import urlparse
import mimetypes

from config import DIFY_API_KEY, DIFY_BASE_URL, MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

class DifyHandler:
    def __init__(self):
        self.api_key = DIFY_API_KEY
        self.base_url = DIFY_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.upload_auth_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        try:
            self.max_file_size_bytes = int(MAX_FILE_SIZE_MB) * 1024 * 1024
        except ValueError:
            logger.error(f"Invalid MAX_FILE_SIZE_MB value in config: {MAX_FILE_SIZE_MB}. Defaulting to 15MB.")
            self.max_file_size_bytes = 15 * 1024 * 1024

    def _make_request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}{endpoint}"
        timeout = kwargs.pop('timeout', (10, 180))
        
        current_headers = self.headers.copy() # Start with a copy
        if 'files' in kwargs:
            # For multipart/form-data, requests sets Content-Type, so remove it from our default headers
            # Only keep Authorization from upload_auth_headers if it's different or specific
            current_headers = self.upload_auth_headers.copy() 
            if "Content-Type" in current_headers: # Should not be there for upload_auth_headers
                del current_headers["Content-Type"]
        
        if 'headers' in kwargs: # Allow overriding headers
            final_headers = {**current_headers, **kwargs.pop('headers')}
        else:
            final_headers = current_headers

        try:
            # logger.debug(f"Dify Request: {method} {url} Headers: {final_headers} Payload: {str(kwargs.get('json', kwargs.get('data')))[:200]}")
            response = requests.request(method, url, headers=final_headers, timeout=timeout, **kwargs)
            response.raise_for_status()
            
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return response.json()
            elif "text/event-stream" in content_type:
                return response
            elif "audio/" in content_type:
                return response.content, content_type
            else:
                if response.status_code == 201 and method.upper() == "POST" and endpoint == "/files/upload":
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        logger.warning(f"Dify file upload returned 201 but response was not JSON: {response.text[:200]}")
                        return {"id": None, "name": None, "warning": "Response not JSON but status 201"}
                logger.warning(f"Dify API response with unhandled Content-Type '{content_type}': {response.text[:200]}")
                return response
        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            logger.error(f"Dify API HTTP 错误: {e.response.status_code} - {error_text[:500]}")
            error_details = {"error": str(e), "status_code": e.response.status_code, "details_text": error_text}
            try:
                error_details["details_json"] = e.response.json()
            except json.JSONDecodeError:
                logger.debug(f"Dify API HTTP 错误响应不是有效的JSON: {error_text[:200]}")
            return error_details
        except requests.exceptions.Timeout:
            logger.error(f"Dify API 请求超时 ({method} {url})")
            return {"error": "API请求超时", "status_code": 408}
        except requests.exceptions.RequestException as e:
            logger.error(f"Dify API 请求错误: {e}")
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"处理 Dify API 响应时发生未知错误: {e}", exc_info=True)
            return {"error": f"未知错误: {str(e)}"}

    def send_chat_message(self, user_id, query, conversation_id=None, stream=False, files=None):
        # ... (此方法保持不变) ...
        endpoint = "/chat-messages"
        payload = {
            "inputs": {}, 
            "query": query,
            "user": user_id,
            "response_mode": "streaming" if stream else "blocking"
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        
        if files: 
            payload["files"] = files 
            logger.debug(f"Dify 聊天消息将包含文件引用: {files}")

        return self._make_request("POST", endpoint, json=payload, stream=stream)

    def upload_file_to_dify(self, user_id, file_bytes, file_name_hint):
        # ... (此方法保持不变) ...
        endpoint = "/files/upload"
        if not file_bytes:
            logger.error("上传到Dify的文件字节流为空。")
            return {"error": "文件内容为空", "status_code": 400} 
        
        if len(file_bytes) > self.max_file_size_bytes:
            logger.error(f"待上传Dify文件大小 ({len(file_bytes)} bytes) 超出限制 ({self.max_file_size_bytes} bytes): {file_name_hint}")
            return {"error": f"文件大小超出应用配置限制 ({MAX_FILE_SIZE_MB}MB)", "status_code": 413}

        mime_type, _ = mimetypes.guess_type(file_name_hint)
        if not mime_type: 
            name_lower = file_name_hint.lower()
            if name_lower.endswith(('.png')): mime_type = 'image/png'
            elif name_lower.endswith(('.jpg', '.jpeg')): mime_type = 'image/jpeg'
            elif name_lower.endswith(('.gif')): mime_type = 'image/gif'
            elif name_lower.endswith(('.webp')): mime_type = 'image/webp'
            elif name_lower.endswith(('.txt', '.text')): mime_type = 'text/plain'
            elif name_lower.endswith(('.pdf')): mime_type = 'application/pdf'
            elif name_lower.endswith(('.csv')): mime_type = 'text/csv'
            else: mime_type = 'application/octet-stream'
            logger.info(f"无法从文件名 '{file_name_hint}' 准确猜测MIME类型，已设置为 '{mime_type}'")
        
        files_payload = {'file': (file_name_hint, file_bytes, mime_type)}
        data_payload = {'user': user_id} 
        
        logger.info(f"准备上传文件到 Dify 内部存储: 文件名提示='{file_name_hint}', User='{user_id}', 大小={len(file_bytes)} bytes, ContentType='{mime_type}'")
        
        response_data = self._make_request("POST", endpoint, data=data_payload, files=files_payload) 
        
        if isinstance(response_data, dict) and "id" in response_data: 
            logger.info(f"文件成功上传到Dify: ID='{response_data.get('id')}', Name='{response_data.get('name')}'")
            return response_data 
        else: 
            logger.error(f"上传文件到Dify失败或响应格式不正确: {str(response_data)[:300]}")
            if isinstance(response_data, dict) and "error" in response_data:
                return response_data 
            return {"error": "上传文件到Dify失败或响应格式不正确", "details": str(response_data)[:300]}

    def audio_to_text(self, user_id: str, audio_file_path: str): # <--- 修改点：参数变为 audio_file_path
        """
        将本地音频文件通过Dify API转换为文本。
        """
        endpoint = "/audio-to-text"
        if not os.path.exists(audio_file_path):
            logger.error(f"Dify Audio-to-Text: 音频文件路径不存在 '{audio_file_path}'。")
            return {"error": "音频文件路径不存在", "status_code": 400}
        
        file_size = os.path.getsize(audio_file_path)
        audio_file_name_hint = os.path.basename(audio_file_path) # 从路径获取文件名提示

        dify_stt_specific_limit = 15 * 1024 * 1024  # Dify STT通常的限制，具体请查阅Dify文档
        effective_limit = min(self.max_file_size_bytes, dify_stt_specific_limit)

        if file_size == 0:
            logger.error(f"Dify Audio-to-Text: 音频文件为空 '{audio_file_path}'。")
            return {"error": "音频文件内容为空", "status_code": 400}

        if file_size > effective_limit:
            logger.error(f"STT 音频文件大小 ({file_size} bytes) 超出限制 ({effective_limit // (1024*1024)}MB): {audio_file_name_hint}")
            return {"error": f"STT 音频文件大小超出限制 ({effective_limit // (1024*1024)}MB)", "status_code": 413}

        mime_type, _ = mimetypes.guess_type(audio_file_name_hint)
        if not mime_type:
            name_lower = audio_file_name_hint.lower()
            if name_lower.endswith('.wav'): mime_type = 'audio/wav'
            elif name_lower.endswith('.mp3'): mime_type = 'audio/mpeg'
            elif name_lower.endswith('.m4a'): mime_type = 'audio/m4a'
            elif name_lower.endswith('.webm'): mime_type = 'audio/webm'
            elif name_lower.endswith('.mp4'): mime_type = 'audio/mp4'
            elif name_lower.endswith('.mpeg'): mime_type = 'audio/mpeg'
            elif name_lower.endswith('.mpga'): mime_type = 'audio/mpeg'
            # 补充 silk 和 amr 作为常见微信语音格式的MIME猜测
            elif name_lower.endswith('.silk'): mime_type = 'audio/silk' # 非标准，但常见
            elif name_lower.endswith('.amr'): mime_type = 'audio/amr'   # 标准
            else: mime_type = 'application/octet-stream'
            logger.info(f"无法从音频文件名 '{audio_file_name_hint}' 准确猜测MIME类型，已设置为 '{mime_type}'")
        
        # 构造 files 和 data payload
        # files={'file': (文件名, 文件对象, 内容类型)}
        # data={'user': 用户ID}
        try:
            with open(audio_file_path, 'rb') as audio_file:
                files_payload = {'file': (audio_file_name_hint, audio_file, mime_type)}
                data_payload = {'user': user_id}
            
                logger.info(f"发送本地音频文件到 Dify Audio-to-Text API: user_id='{user_id}', 文件路径='{audio_file_path}', 文件名提示='{audio_file_name_hint}', 大小={file_size} bytes, ContentType='{mime_type}'")
                
                # 使用 _make_request 发送，它会处理headers
                stt_response = self._make_request("POST", endpoint, data=data_payload, files=files_payload)
        except IOError as e:
            logger.error(f"读取本地音频文件失败 '{audio_file_path}': {e}")
            return {"error": f"读取本地音频文件失败: {e}", "status_code": 500}
        except Exception as e_req: # 其他请求构建或发送阶段的错误
            logger.error(f"发送音频到Dify STT时出错: {e_req}", exc_info=True)
            return {"error": f"发送音频到Dify STT时出错: {e_req}", "status_code": 500}


        if isinstance(stt_response, dict) and "text" in stt_response:
            logger.info(f"Dify Audio-to-Text 成功: '{stt_response['text'][:50]}...'")
            return stt_response
        else:
            logger.error(f"Dify Audio-to-Text 失败或响应格式不正确: {str(stt_response)[:300]}")
            if isinstance(stt_response, dict) and "error" in stt_response:
                return stt_response # 返回 _make_request 构造的错误字典
            return {"error": "Dify Audio-to-Text失败或响应格式不正确", "details": str(stt_response)[:300]}