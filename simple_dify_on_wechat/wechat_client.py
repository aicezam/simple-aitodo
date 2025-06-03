# wechat_client.py
import websocket # 使用 websocket-client 库
import threading
import time
import json
import logging
import requests # 用于 HTTP API 调用
import xml.etree.ElementTree as ET # 用于解析 XML 内容
import re # 用于正则表达式解析
from config import WECHAT_WS_URL, WECHAT_API_BASE_URL, WECHAT_TOKEN_KEY, WECHAT_BOT_WXID

logger = logging.getLogger(__name__)

class WeChatClient:
    def __init__(self, message_callback=None):
        self.ws_base_url = WECHAT_WS_URL
        self.api_base_url = WECHAT_API_BASE_URL
        self.token_key = WECHAT_TOKEN_KEY
        self.bot_wxid = WECHAT_BOT_WXID
        
        self.ws = None
        self.ws_thread = None
        self.is_ws_running = False
        self.message_callback = message_callback
        self.auto_reconnect_delay = 10 
        self.actual_ws_url = f"{self.ws_base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/GetSyncMsg?key={self.token_key}"

    def _on_message(self, ws, message):
        logger.debug(f"收到原始 WebSocket 消息: {message[:500]}...") 
        try:
            msg_data = json.loads(message)
            if self.message_callback:
                processed_msg = self._parse_wechat_message(msg_data)
                if processed_msg:
                    self.message_callback(processed_msg)
        except json.JSONDecodeError:
            logger.error(f"WebSocket 消息 JSON 解析失败: {message[:200]}")
        except Exception as e:
            logger.error(f"处理 WebSocket 消息时发生错误: {e}", exc_info=True)

    def _parse_wechat_message(self, raw_msg):
        unique_id_base = raw_msg.get("new_msg_id") 
        if not unique_id_base: 
            original_msg_id = raw_msg.get("msg_id", "")
            unique_id_base = f"{int(time.time()*1000)}_{original_msg_id}"
        else: 
            unique_id_base = str(unique_id_base)

        standard_msg = {
            "id": unique_id_base, 
            "type": None,
            "is_group": False,
            "sender_id": None, # 实际发送消息的个人wxid (群聊时也是个人)
            "sender_nickname": None, # 实际发送消息的个人昵称
            "room_id": None, # 如果是群聊，群的ID
            "content": None, # 消息的纯文本内容 (群聊时，不含 "wxid_xxx:\n" 前缀)
            "file_url": None, 
            "file_data_b64": None,
            "at_list": [],
            "raw": raw_msg, 
            "voice_format_code": None 
        }

        msg_type = raw_msg.get("msg_type")
        from_user_name_obj = raw_msg.get("from_user_name", {})
        from_user_name_str = from_user_name_obj.get("str", "") # 可能是群ID或个人ID
        
        content_obj = raw_msg.get("content", {})
        # content_str_from_obj: 原始消息内容，群聊时通常是 "wxid_个人:\n实际内容"
        content_str_from_obj = content_obj.get("str", "") 
        
        # push_content: 例如 "momo在群聊中@了你" 或 "好友昵称: 文本消息"
        push_content_str = raw_msg.get("push_content", "") 
        
        actual_content_for_type_parsing = content_str_from_obj 

        if from_user_name_str.endswith("@chatroom"):
            standard_msg["is_group"] = True
            standard_msg["room_id"] = from_user_name_str # 群ID
            
            # 从 content 中提取实际发送者的 wxid
            sender_id_match = re.match(r"^(wxid_[a-zA-Z0-9]+?):\n", content_str_from_obj) 
            if sender_id_match:
                standard_msg["sender_id"] = sender_id_match.group(1) # 个人 wxid
                # 更新用于类型解析的实际内容，去除 "wxid_xxx:\n" 前缀
                actual_content_for_type_parsing = content_str_from_obj[len(sender_id_match.group(0)):].strip()
            else:
                logger.debug(f"群消息 {standard_msg['id']} 未能从 content 字段按 'wxid_xxx:\\n' 模式提取个人发送者 wxid。原始 content: {content_str_from_obj[:100]}")
                # 如果无法从content中提取，可能需要其他方式或标记为未知

            # 从 push_content 中提取昵称 (改进逻辑)
            if push_content_str:
                # 尝试匹配 "昵称在群聊中..." 或 "昵称: ..."
                # 例如: "momo在群聊中@了你" -> 提取 "momo"
                # 例如: "张三: @机器人 你好" (如果push_content是这样的话) -> 提取 "张三"
                # 正则表达式尝试捕获消息内容前的昵称部分
                # (.*?) 匹配尽可能少的字符，直到遇到 "在群聊中" 或 ":" 或消息末尾
                # nickname_match = re.match(r"^(.*?)(?:\s*在群聊中|\s*:|$)", push_content_str)
                nickname_match = re.match(r"^(.+?)(?:\s*在群聊中|\s*:\s*@|\s*:\s*\S)", push_content_str)
                if nickname_match:
                    potential_nickname = nickname_match.group(1).strip()
                    if potential_nickname: 
                        standard_msg["sender_nickname"] = potential_nickname
                        logger.debug(f"从 push_content ('{push_content_str}') 中为群消息提取到昵称: '{potential_nickname}'")
                    else:
                        logger.debug(f"从 push_content ('{push_content_str}') 提取到的昵称为空。")
                else:
                    logger.debug(f"未能从 push_content ('{push_content_str}') 中按模式提取昵称。")
            
            msg_source_xml_str = raw_msg.get("msg_source", "")
            if msg_source_xml_str:
                try:
                    source_root = ET.fromstring(msg_source_xml_str)
                    atuserlist_element = source_root.find("atuserlist")
                    if atuserlist_element is not None and atuserlist_element.text:
                        at_users_str = atuserlist_element.text
                        standard_msg["at_list"] = [uid.strip() for uid in at_users_str.split(',') if uid.strip()]
                except ET.ParseError as e_xml_source:
                    logger.warning(f"解析群消息 {standard_msg['id']}的 msg_source XML 失败: {e_xml_source}. XML: {msg_source_xml_str[:200]}")
        else: 
            standard_msg["is_group"] = False
            standard_msg["sender_id"] = from_user_name_str # 私聊时, from_user_name_str 就是个人wxid
            actual_content_for_type_parsing = content_str_from_obj
            # 私聊时，尝试从 push_content 提取昵称 (例如 "好友昵称: 文本消息")
            if push_content_str:
                private_nick_match = re.match(r"^(.*?)\s*:", push_content_str)
                if private_nick_match:
                    potential_nickname = private_nick_match.group(1).strip()
                    if potential_nickname:
                        standard_msg["sender_nickname"] = potential_nickname
                        logger.debug(f"从 push_content ('{push_content_str}') 中为私聊消息提取到昵称: '{potential_nickname}'")


        if msg_type == 1: 
            standard_msg["type"] = "text"
            standard_msg["content"] = actual_content_for_type_parsing
        elif msg_type == 3: 
            standard_msg["type"] = "image"
            standard_msg["content"] = "[图片]" 
            if content_str_from_obj:
                 standard_msg["raw"]["wechat_xml_content"] = content_str_from_obj
            try:
                if content_str_from_obj:
                    img_root = ET.fromstring(content_str_from_obj)
                    img_element = img_root.find("img")
                    if img_element is not None:
                        hd_url = img_element.get("cdnhdurl")
                        mid_url = img_element.get("cdnmidimgurl")
                        thumb_url = img_element.get("cdnthumburl") 
                        if hd_url: standard_msg["file_url"] = hd_url
                        elif mid_url: standard_msg["file_url"] = mid_url
                        elif thumb_url: standard_msg["file_url"] = thumb_url
            except ET.ParseError:
                logger.warning(f"解析图片消息 {standard_msg['id']} 内容XML提取URL失败。XML: {content_str_from_obj[:200]}")
            
        elif msg_type == 34: 
            standard_msg["type"] = "voice"
            standard_msg["content"] = "[语音]"
            if content_str_from_obj:
                standard_msg["raw"]["wechat_xml_content"] = content_str_from_obj
                try:
                    voice_xml_root = ET.fromstring(content_str_from_obj)
                    voicemsg_node = voice_xml_root.find("voicemsg")
                    if voicemsg_node is not None:
                        standard_msg["voice_format_code"] = voicemsg_node.get("voiceformat")
                        standard_msg["raw"]["voicelength"] = voicemsg_node.get("voicelength")
                        cdn_url = voicemsg_node.get("cdnurl") # 或者 voiceurl
                        if not cdn_url: cdn_url = voicemsg_node.get("voiceurl")
                        if cdn_url : standard_msg["file_url"] = cdn_url
                        logger.info(f"语音消息 {standard_msg['id']} 检测到 voiceformat: {standard_msg['voice_format_code']}")
                    else:
                        logger.warning(f"语音消息 {standard_msg['id']} 的XML中未找到 'voicemsg' 节点。XML: {content_str_from_obj[:200]}")
                except ET.ParseError as e_xml_voice:
                    logger.warning(f"解析语音消息 {standard_msg['id']} 内容XML失败: {e_xml_voice}. XML: {content_str_from_obj[:200]}")

            img_buf_content = raw_msg.get("img_buf", {}) 
            if img_buf_content.get("buffer") and img_buf_content.get("len", 0) > 0:
                standard_msg["file_data_b64"] = img_buf_content["buffer"]
                logger.info(f"语音消息 {standard_msg['id']}：已从 img_buf 获取 base64 数据。")
            else: 
                logger.debug(f"语音消息 {standard_msg['id']}：img_buf 为空或无数据。")
        
        else:
            logger.debug(f"未处理的微信消息类型: {msg_type}。原始数据ID: {standard_msg['id']}")
            return None 

        if not standard_msg["sender_id"] : 
             logger.warning(f"解析消息后 sender_id 为空，忽略。消息ID: {standard_msg['id']}")
             return None
        
        # 确保 sender_nickname 如果是 None，则保持为 None，而不是 "N/A"
        log_sender_nickname = standard_msg['sender_nickname'] if standard_msg['sender_nickname'] else 'None'

        logger.info(f"成功解析微信消息: ID={standard_msg['id']}, 类型={standard_msg['type']}, "
                    f"发信人ID={standard_msg['sender_id']}, 发信人昵称='{log_sender_nickname}', "
                    f"群聊={standard_msg['is_group']}, 群ID={standard_msg['room_id']}, "
                    f"提及={standard_msg['at_list']}")
        return standard_msg

    def _on_error(self, ws, error):
        logger.error(f"WebSocket 错误: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info(f"WebSocket 连接已关闭。状态码: {close_status_code}, 原因: {close_msg}")
        self.is_ws_running = False
        if self.auto_reconnect_delay > 0: 
            logger.info(f"{self.auto_reconnect_delay} 秒后尝试 WebSocket 重连...")
            reconnect_thread = threading.Thread(target=self._do_ws_reconnect, daemon=True)
            reconnect_thread.start()

    def _do_ws_reconnect(self):
        time.sleep(self.auto_reconnect_delay)
        if not self.is_ws_running : 
            logger.info("正在尝试重新连接 WebSocket...")
            self.connect_websocket()
        else:
            logger.info("重连前检测到 WebSocket 已在运行，取消重连。")

    def _on_open(self, ws):
        logger.info(f"WebSocket 连接成功: {self.actual_ws_url}")
        self.is_ws_running = True

    def connect_websocket(self):
        if self.is_ws_running:
            logger.warning("WebSocket 客户端已在运行或正在连接中。")
            return
        logger.info(f"正在连接到 WebSocket: {self.actual_ws_url}")
        self.ws = websocket.WebSocketApp(self.actual_ws_url,
                                         on_open=self._on_open,
                                         on_message=self._on_message,
                                         on_error=self._on_error,
                                         on_close=self._on_close)
        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()

    def _send_http_request(self, method, endpoint_path, params=None, json_payload=None, **kwargs):
        url = f"{self.api_base_url}{endpoint_path}"
        request_params = {"key": self.token_key}
        if params:
            request_params.update(params)
        
        request_kwargs = {'params': request_params, 'json': json_payload, 'timeout': (10, 60)} 
        request_kwargs.update(kwargs)

        try:
            response = requests.request(method, url, **request_kwargs)
            response.raise_for_status() 
            res_json = response.json() 
            
            if res_json.get("Code") == 200 : 
                return res_json.get("Data", res_json) 
            else:
                logger.error(f"WeChat API 返回逻辑错误: Code={res_json.get('Code')}, Message={res_json.get('Message', res_json.get('Msg', str(res_json)))}")
                return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"WeChat API HTTP 错误: {e.response.status_code} - {e.response.text[:200]}")
        except requests.exceptions.RequestException as e: 
            logger.error(f"WeChat API 请求错误 ({method} {url}): {e}")
        except json.JSONDecodeError:
            logger.error(f"WeChat API 响应 JSON 解析失败: {response.text[:200] if 'response' in locals() else 'N/A'}")
        except Exception as e: 
            logger.error(f"发送 WeChat API HTTP 请求时发生未知错误: {e}", exc_info=True)
        return None

    def send_text_message(self, recipient_wxid, content, at_wxid_list=None):
        endpoint = "/message/SendTextMessage"
        msg_item = {
            "TextContent": content,
            "ToUserName": recipient_wxid,
            "MsgType": 1
        }
        if at_wxid_list and isinstance(at_wxid_list, list) and recipient_wxid.endswith("@chatroom"):
            msg_item["AtWxIDList"] = at_wxid_list
        
        payload = {"MsgItem": [msg_item]}
        logger.info(f"准备发送文本消息给 {recipient_wxid}: {content[:50].replace(chr(10), ' ')}...")
        return self._send_http_request("POST", endpoint, json_payload=payload)

    def send_image_message_base64(self, recipient_wxid, image_base64):
        endpoint = "/message/SendImageMessage"
        payload = {
            "MsgItem": [
                {
                    "ImageContent": image_base64, 
                    "ToUserName": recipient_wxid,
                    "MsgType": 3 
                }
            ]
        }
        logger.info(f"准备发送 Base64 图片消息给 {recipient_wxid}...")
        return self._send_http_request("POST", endpoint, json_payload=payload)

    def send_voice_message_base64(self, recipient_wxid, voice_base64, duration_seconds):
        endpoint = "/message/SendSilkVoiceMessage" 
        payload = {
            "MsgItem": [
                {
                    "VoiceData": voice_base64, 
                    "ToUserName": recipient_wxid,
                    "MsgType": 34, 
                    "VoiceFormat": 0, 
                    "VoiceSecond": duration_seconds 
                }
            ]
        }
        logger.info(f"准备发送 Base64 语音消息给 {recipient_wxid}, 时长: {duration_seconds}s...")
        return self._send_http_request("POST", endpoint, json_payload=payload)

    def close_websocket(self):
        self.auto_reconnect_delay = 0 
        if self.ws:
            logger.info("正在关闭 WebSocket 连接...")
            self.is_ws_running = False 
            try:
                self.ws.close()
            except Exception as e:
                logger.warning(f"关闭WebSocket时出错: {e}")

        if self.ws_thread and self.ws_thread.is_alive():
             try:
                 self.ws_thread.join(timeout=2.0) 
             except RuntimeError: 
                 pass
        logger.info("WebSocket 连接已尝试关闭。")