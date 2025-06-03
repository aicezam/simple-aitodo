# message_processor.py
import logging
import re
import requests
import os
import mimetypes
import base64
from urllib.parse import urlparse, unquote
import xml.etree.ElementTree as ET
import json
import time
import subprocess # 用于调用 ffmpeg

from config import WECHAT_BOT_WXID, DIFY_USER_ID_PREFIX, WECHAT_API_BASE_URL, WECHAT_TOKEN_KEY, MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)
conversation_store = {}

TEMP_AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_audio")
if not os.path.exists(TEMP_AUDIO_DIR):
    try:
        os.makedirs(TEMP_AUDIO_DIR)
        logger.info(f"已创建临时音频目录: {TEMP_AUDIO_DIR}")
    except OSError as e:
        logger.error(f"创建临时音频目录失败 {TEMP_AUDIO_DIR}: {e}")
        TEMP_AUDIO_DIR = None


class MessageProcessor:
    def __init__(self, dify_handler):
        self.dify_handler = dify_handler
        self.bot_wxid = WECHAT_BOT_WXID
        self.dify_user_id_prefix = DIFY_USER_ID_PREFIX
        try:
            self.max_upload_size_bytes = int(MAX_FILE_SIZE_MB) * 1024 * 1024
        except ValueError:
            logger.error(f"Invalid MAX_FILE_SIZE_MB value: {MAX_FILE_SIZE_MB}. Defaulting to 15MB.")
            self.max_upload_size_bytes = 15 * 1024 * 1024
        self.markdown_image_regex = r'!\[(.*?)\]\((.*?)\)'

    def get_dify_user_id(self, wechat_sender_id):
        if not wechat_sender_id:
            logger.warning("微信 sender_id 为空，无法生成 Dify 用户 ID。")
            return None
        return f"{self.dify_user_id_prefix}{wechat_sender_id}"

    def get_dify_conversation_id(self, wechat_contact_key):
        return conversation_store.get(wechat_contact_key)

    def set_dify_conversation_id(self, wechat_contact_key, dify_conversation_id):
        if wechat_contact_key and dify_conversation_id:
            conversation_store[wechat_contact_key] = dify_conversation_id
            logger.debug(f"已更新会话 ID: {wechat_contact_key} -> {dify_conversation_id}")
        else:
            logger.warning(f"设置会话 ID 失败，contact_key 或 dify_conversation_id 为空。")

    def should_process_wechat_message(self, wechat_msg):
        if not wechat_msg or not isinstance(wechat_msg, dict):
            logger.warning("无效的微信消息数据。")
            return False
        msg_type = wechat_msg.get("type")
        is_group = wechat_msg.get("is_group", False)
        sender_id = wechat_msg.get("sender_id")

        if not sender_id:
            logger.debug(f"消息缺少有效的个人 sender_id，忽略。")
            return False

        if sender_id == self.bot_wxid:
            logger.debug(f"消息来自机器人自身 ({sender_id})，忽略。")
            return False

        if msg_type not in ["text", "image", "voice"]:
            logger.debug(f"忽略非文本/图片/语音类型的消息: {msg_type}")
            return False

        if not is_group:
            logger.info(f"接收到好友 {sender_id} 的 {msg_type} 消息，符合处理条件。")
            return True
        else:
            at_list = wechat_msg.get("at_list", [])
            is_at_me = False
            if self.bot_wxid and at_list and self.bot_wxid in at_list:
                is_at_me = True

            if is_at_me:
                logger.info(f"接收到群聊 {wechat_msg.get('room_id')} 中来自 {sender_id} (昵称: {wechat_msg.get('sender_nickname', 'None')}) 的 @机器人 的 {msg_type} 消息，符合处理条件。")
                return True
            else:
                logger.debug(f"群聊消息未 @机器人，忽略。群ID: {wechat_msg.get('room_id')}, 发送者: {sender_id}")
                return False
        return False

    def _convert_audio_to_mp3(self, input_path, output_mp3_path, input_format_hint=None):
        if not os.path.exists(input_path):
            logger.error(f"输入音频文件不存在: {input_path}")
            return False
        try:
            command = ["ffmpeg", "-y"]
            if input_format_hint:
                command.extend(["-f", input_format_hint])
            command.extend(["-i", input_path, output_mp3_path])
            
            logger.info(f"执行转换命令: {' '.join(command)}")
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                if os.path.exists(output_mp3_path) and os.path.getsize(output_mp3_path) > 0:
                    logger.info(f"成功将 {input_path} (格式: {input_format_hint or '自动检测'}) 转换为 {output_mp3_path}")
                    return True
                else:
                    logger.error(f"ffmpeg报告成功但输出MP3文件无效或为空: {output_mp3_path}")
                    logger.error(f"ffmpeg stdout: {result.stdout.strip() if result.stdout else '无输出'}")
                    logger.error(f"ffmpeg stderr: {result.stderr.strip() if result.stderr else '无输出'}")
                    return False
            else:
                logger.error(f"ffmpeg转换失败 (返回码: {result.returncode}): {input_path} -> {output_mp3_path}")
                logger.error(f"ffmpeg stdout: {result.stdout.strip() if result.stdout else '无输出'}")
                logger.error(f"ffmpeg stderr: {result.stderr.strip() if result.stderr else '无输出'}")
                return False
        except FileNotFoundError:
            logger.error("ffmpeg 命令未找到。请确保ffmpeg已安装并在系统PATH中。")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"ffmpeg转换超时: {input_path}")
            return False
        except Exception as e:
            logger.error(f"ffmpeg转换过程中发生未知错误: {e}", exc_info=True)
            return False

    def _get_wechat_file_data(self, wechat_msg_obj):
        msg_id_original_str = str(wechat_msg_obj.get("raw", {}).get("msg_id", 0))
        try:
            msg_id_original = int(msg_id_original_str)
        except ValueError:
            logger.error(f"无法将消息ID '{msg_id_original_str}' 转换为整数。")
            msg_id_original = 0

        msg_new_id = wechat_msg_obj.get("id")
        msg_type = wechat_msg_obj.get("type")

        if msg_type == "voice":
            b64_data = wechat_msg_obj.get("file_data_b64")
            if b64_data:
                if not TEMP_AUDIO_DIR:
                    logger.error("临时音频目录不可用，无法处理语音文件。")
                    return None, "临时目录错误"
                try:
                    cleaned_b64_data = "".join(b64_data.split())
                    missing_padding = len(cleaned_b64_data) % 4
                    if missing_padding:
                        cleaned_b64_data += '=' * (4 - missing_padding)
                    voice_bytes = base64.b64decode(cleaned_b64_data)

                    voice_format_code = wechat_msg_obj.get("voice_format_code")
                    input_extension = ".silk" 
                    input_format_hint_for_ffmpeg = "silk" 

                    if voice_format_code == "4": 
                        input_extension = ".amr"
                        input_format_hint_for_ffmpeg = "amr"
                        logger.info(f"语音格式代码为 {voice_format_code}，将尝试作为 AMR 处理。")
                    elif voice_format_code == "1": 
                        logger.info(f"语音格式代码为 {voice_format_code}，将尝试作为 SILK 处理。")
                    elif voice_format_code:
                        logger.info(f"未知语音格式代码 {voice_format_code}，默认尝试作为 SILK 处理。")
                    else:
                        logger.info(f"未提供语音格式代码，默认尝试作为 SILK 处理。")

                    temp_input_filename = f"wechat_voice_in_{msg_new_id}{input_extension}"
                    temp_input_path = os.path.join(TEMP_AUDIO_DIR, temp_input_filename)
                    
                    temp_mp3_filename = f"wechat_voice_out_{msg_new_id}.mp3"
                    temp_mp3_path = os.path.join(TEMP_AUDIO_DIR, temp_mp3_filename)

                    with open(temp_input_path, 'wb') as f_input:
                        f_input.write(voice_bytes)
                    logger.info(f"原始语音数据 (消息ID: {msg_new_id}) 已保存到: {temp_input_path}")

                    conversion_successful = self._convert_audio_to_mp3(temp_input_path, temp_mp3_path, input_format_hint_for_ffmpeg)

                    if os.path.exists(temp_input_path):
                        try:
                            os.remove(temp_input_path)
                            logger.debug(f"已删除临时输入音频文件: {temp_input_path}")
                        except OSError as e_del_input:
                            logger.warning(f"删除临时输入音频文件失败 '{temp_input_path}': {e_del_input}")
                    
                    if conversion_successful and os.path.exists(temp_mp3_path):
                        logger.info(f"MP3文件已生成: {temp_mp3_path}, 大小: {os.path.getsize(temp_mp3_path)}B")
                        return temp_mp3_path, temp_mp3_filename 
                    else:
                        if not os.path.exists(temp_mp3_path) and conversion_successful:
                            logger.error(f"ffmpeg报告转换成功，但MP3文件未找到: {temp_mp3_path}")
                        return None, f"音频转MP3失败(格式:{input_format_hint_for_ffmpeg})"

                except Exception as e:
                    logger.error(f"保存或解码/转换语音数据失败 (消息ID: {msg_new_id}): {e}", exc_info=True)
                    if 'temp_input_path' in locals() and os.path.exists(temp_input_path):
                        try: os.remove(temp_input_path)
                        except: pass
                    return None, "语音处理通用失败"
            else:
                logger.warning(f"语音消息 {msg_new_id} 无内置b64数据。")
                return None, "无内置语音数据"

        elif msg_type == "image":
            if msg_id_original == 0:
                return None, "无效原始MsgID"
            raw_msg_data = wechat_msg_obj.get("raw", {})
            from_user_name = raw_msg_data.get("from_user_name", {}).get("str")
            to_user_name = raw_msg_data.get("to_user_name", {}).get("str")
            xml_content_str = raw_msg_data.get("wechat_xml_content")
            if not xml_content_str:
                xml_content_str = raw_msg_data.get("content", {}).get("str", "")
                if not xml_content_str: return None, "缺少XML内容"
                raw_msg_data["wechat_xml_content"] = xml_content_str
            estimated_total_len_from_xml = 0
            try:
                img_xml_root = ET.fromstring(xml_content_str)
                img_node = img_xml_root.find("img")
                if img_node is not None:
                    length_str = img_node.get("length")
                    hd_length_str = img_node.get("hdlength")
                    if hd_length_str and int(hd_length_str) > 0: estimated_total_len_from_xml = int(hd_length_str)
                    elif length_str and int(length_str) > 0: estimated_total_len_from_xml = int(length_str)
                if estimated_total_len_from_xml == 0 : logger.warning(f"XML中图片长度解析为0或未找到。MsgID: {msg_id_original}")
            except: logger.warning(f"解析图片XML长度失败。MsgID: {msg_id_original}")
            if not from_user_name or not to_user_name: return None, "API参数不完整"
            if estimated_total_len_from_xml > self.max_upload_size_bytes:
                error_msg = f"图片过大({estimated_total_len_from_xml // (1024*1024)}MB)"
                logger.error(f"图片消息预估过大 {error_msg}")
                return None, error_msg
            download_api_endpoint = "/message/GetMsgBigImg"
            full_download_url = f"{WECHAT_API_BASE_URL.rstrip('/')}{download_api_endpoint}"
            suggested_filename = f"wechat_image_{msg_new_id}.jpg"
            cdn_url_identifier = wechat_msg_obj.get("file_url")
            if cdn_url_identifier:
                try:
                    parsed_cdn_url = urlparse(cdn_url_identifier)
                    path_basename = os.path.basename(parsed_cdn_url.path)
                    if path_basename and '.' in path_basename:
                        temp_fn = unquote(path_basename)
                        name_part, ext_part = os.path.splitext(temp_fn)
                        if ext_part.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                            suggested_filename = f"wechat_image_{msg_new_id}{ext_part.lower()}"
                except Exception as e_parse_url: logger.warning(f"解析CDN URL '{cdn_url_identifier}'提取文件名时出错: {e_parse_url}")
            all_image_bytes_chunks = []
            current_pos = 0
            authoritative_total_len = 0 
            first_request_done = False
            PREFERRED_CHUNK_REQ_LEN = 65536 
            MAX_DOWNLOAD_ATTEMPTS = 20
            attempts = 0
            logger.info(f"准备通过 WeChatPadPro API '{download_api_endpoint}' 分块下载图片 (原始MsgID: {msg_id_original}), XML预估大小: {estimated_total_len_from_xml if estimated_total_len_from_xml > 0 else '未知'}")
            while attempts < MAX_DOWNLOAD_ATTEMPTS:
                attempts += 1
                request_total_len_in_payload = authoritative_total_len if first_request_done and authoritative_total_len > 0 else estimated_total_len_from_xml
                if first_request_done and authoritative_total_len > 0 and current_pos >= authoritative_total_len: break 
                if first_request_done and authoritative_total_len == 0:
                    if all_image_bytes_chunks: break
                    else: return None, "API未返回文件大小"
                if authoritative_total_len > 0 : request_data_len = min(PREFERRED_CHUNK_REQ_LEN, authoritative_total_len - current_pos)
                elif estimated_total_len_from_xml > 0: request_data_len = min(PREFERRED_CHUNK_REQ_LEN, estimated_total_len_from_xml - current_pos)
                else: request_data_len = PREFERRED_CHUNK_REQ_LEN
                if request_data_len <= 0: 
                    if current_pos < (authoritative_total_len or estimated_total_len_from_xml or float('inf')): 
                        request_data_len = PREFERRED_CHUNK_REQ_LEN
                    else: break 
                payload = {"CompressType": 0,"FromUserName": from_user_name,"MsgId": msg_id_original,"Section": {"DataLen": request_data_len, "StartPos": current_pos},"ToUserName": to_user_name,"TotalLen": request_total_len_in_payload }
                try:
                    response = requests.post(full_download_url, params={"key": WECHAT_TOKEN_KEY}, json=payload, timeout=60)
                    response.raise_for_status()
                    res_json = response.json() 
                    api_code = res_json.get("Code")
                    api_data_field_outer = res_json.get("Data", {}) 
                    api_base_response = api_data_field_outer.get("BaseResponse", {})
                    api_base_ret = api_base_response.get("ret")
                    api_err_msg_str = api_base_response.get("errMsg", {}).get("str", "N/A")
                    if api_code != 200 or api_base_ret != 0: return None, f"API错误({api_base_ret})" 
                    if not first_request_done:
                        new_authoritative_total_len = api_data_field_outer.get("TotalLen", 0)
                        if new_authoritative_total_len == 0: 
                            if estimated_total_len_from_xml > 0: authoritative_total_len = estimated_total_len_from_xml
                            else: 
                                current_chunk_len = api_data_field_outer.get("Data", {}).get("iLen", 0)
                                if current_chunk_len > 0: authoritative_total_len = current_chunk_len
                                else: return None, "无法确定文件大小"
                        else: authoritative_total_len = new_authoritative_total_len
                        logger.info(f"API首次响应 TotalLen: {authoritative_total_len} (XML预估: {estimated_total_len_from_xml})")
                        first_request_done = True
                        if authoritative_total_len > self.max_upload_size_bytes: return None, f"图片过大({authoritative_total_len // (1024*1024)}MB)"
                    image_chunk_data_field_inner = api_data_field_outer.get("Data", {}) 
                    b64_chunk_raw = image_chunk_data_field_inner.get("Buffer")
                    returned_data_len = image_chunk_data_field_inner.get("iLen", 0)
                    if b64_chunk_raw and returned_data_len > 0:
                        decoded_chunk_bytes = base64.b64decode("".join(b64_chunk_raw.split()))
                        all_image_bytes_chunks.append(decoded_chunk_bytes)
                        current_pos += returned_data_len
                        logger.info(f"块: {returned_data_len}B, 总计: {current_pos}/{authoritative_total_len if authoritative_total_len > 0 else '未知'}")
                    elif returned_data_len == 0: break 
                    else: break
                except Exception as e:
                    logger.error(f"图片下载块失败 (尝试 {attempts}): {e}", exc_info=True)
                    if attempts >= MAX_DOWNLOAD_ATTEMPTS: return None, "下载失败"
                    time.sleep(min(attempts, 5)); continue
            if not all_image_bytes_chunks: return None, "未获取数据"
            image_bytes = b"".join(all_image_bytes_chunks)
            if not image_bytes: return None, "数据合并为空"
            logger.info(f"图片下载成功 (原始MsgID: {msg_id_original}), 大小: {len(image_bytes)}B")
            return image_bytes, suggested_filename 
        else:
            logger.warning(f"不支持的文件类型 '{msg_type}' (消息ID: {msg_new_id}) 进行数据获取。")
            return None, "不支持类型"

    def prepare_batched_query_for_dify(self, wechat_msgs_list, dify_user_id):
        all_content_parts = [] 
        all_dify_files_payload = []
        encountered_errors = []

        if not wechat_msgs_list:
            logger.warning("prepare_batched_query_for_dify 被调用但消息列表为空。")
            return "", [], ["没有消息可处理"]

        first_msg = wechat_msgs_list[0]
        is_group = first_msg.get("is_group", False)
        room_id_for_prefix = first_msg.get("room_id") # 群ID
        # sender_id_from_first_msg 是实际发送消息的个人wxid
        sender_id_from_first_msg = first_msg.get("sender_id") 
        # sender_nickname_from_first_msg 是实际发送消息的个人昵称
        sender_nickname_from_first_msg = first_msg.get("sender_nickname") 

        for i, wechat_msg in enumerate(wechat_msgs_list):
            msg_type = wechat_msg.get("type")
            # original_content 对于群消息是 "wxid_xxx:\n" 之后的内容，对于私聊是完整内容
            original_content = wechat_msg.get("content") 
            current_sender_id_for_log = wechat_msg.get("sender_id", "未知用户")
            current_sender_nickname_for_log = wechat_msg.get("sender_nickname") or current_sender_id_for_log

            if msg_type == "text":
                if original_content is None:
                    err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 条文本消息内容为空。"
                    logger.warning(err_msg)
                    encountered_errors.append(err_msg)
                    all_content_parts.append(f"[{err_msg}]")
                    continue
                all_content_parts.append(original_content)

            elif msg_type == "image":
                logger.info(f"批处理：开始处理来自 {current_sender_nickname_for_log} 的图片消息 {wechat_msg.get('id')}...")
                file_data_result = self._get_wechat_file_data(wechat_msg)
                image_bytes, filename_hint_or_error = None, None
                if isinstance(file_data_result, tuple) and len(file_data_result) == 2: 
                    image_bytes, filename_hint_or_error = file_data_result
                else: 
                    filename_hint_or_error = "内部返回值错误(图片)"; image_bytes = None

                if image_bytes and isinstance(filename_hint_or_error, str): 
                    filename_hint = filename_hint_or_error
                    if len(image_bytes) > self.max_upload_size_bytes: 
                        err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 张图片 '{filename_hint}' (大小: {len(image_bytes)} bytes) 超出Dify上传限制。"
                        logger.error(err_msg); encountered_errors.append(err_msg); all_content_parts.append(f"[{err_msg}]")
                    else:
                        logger.info(f"获取到的 image_bytes 长度: {len(image_bytes)}，准备上传 (文件名提示: {filename_hint})...")
                        dify_upload_response = self.dify_handler.upload_file_to_dify(dify_user_id, image_bytes, filename_hint)
                        if dify_upload_response and dify_upload_response.get("id"):
                            dify_file_id = dify_upload_response.get("id"); dify_file_name = dify_upload_response.get("name", filename_hint)
                            all_dify_files_payload.append({"type": "image", "transfer_method": "local_file", "upload_file_id": dify_file_id})
                            all_content_parts.append(f"[图片: {dify_file_name}]"); logger.info(f"批处理：图片已上传至Dify ID: {dify_file_id}")
                        else:
                            error_detail = dify_upload_response.get('error', '上传响应无效') if isinstance(dify_upload_response, dict) else str(dify_upload_response)
                            err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 张图片 '{filename_hint}' 上传Dify失败 ({error_detail})。"
                            logger.error(err_msg + f" Dify响应: {dify_upload_response}"); encountered_errors.append(err_msg); all_content_parts.append(f"[{err_msg}]")
                else: 
                    error_reason = filename_hint_or_error if isinstance(filename_hint_or_error, str) else "未知图片获取错误"
                    err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 张图片数据获取失败 ({error_reason})。"
                    logger.error(err_msg); encountered_errors.append(err_msg); all_content_parts.append(f"[{err_msg}]")
            
            elif msg_type == "voice":
                logger.info(f"批处理：开始处理来自 {current_sender_nickname_for_log} 的语音消息 {wechat_msg.get('id')}...")
                file_data_result = self._get_wechat_file_data(wechat_msg)
                local_mp3_path, mp3_filename_hint_or_error = None, None
                
                if isinstance(file_data_result, tuple) and len(file_data_result) == 2:
                    local_mp3_path, mp3_filename_hint_or_error = file_data_result
                else:
                    mp3_filename_hint_or_error = "内部返回值错误(语音)"
                    local_mp3_path = None

                if local_mp3_path and isinstance(mp3_filename_hint_or_error, str) and os.path.exists(local_mp3_path):
                    mp3_filename_hint = mp3_filename_hint_or_error
                    logger.info(f"本地MP3文件已准备好: '{local_mp3_path}' (原始提示名: {mp3_filename_hint})，准备发送给Dify STT...")
                    
                    stt_response = None
                    try:
                        mp3_file_size = os.path.getsize(local_mp3_path)
                        dify_stt_max_size = 15 * 1024 * 1024 
                        effective_stt_limit = min(dify_stt_max_size, self.max_upload_size_bytes)
                        if mp3_file_size > effective_stt_limit:
                            err_msg = f"系统消息：转换后的MP3文件 '{mp3_filename_hint}' (大小: {mp3_file_size} bytes) 超出STT处理限制 ({effective_stt_limit // (1024*1024)}MB)。"
                            logger.error(err_msg)
                            encountered_errors.append(err_msg)
                            all_content_parts.append(f"[{err_msg}]")
                        else:
                            stt_response = self.dify_handler.audio_to_text(dify_user_id, local_mp3_path)
                            if stt_response and not stt_response.get("error") and "text" in stt_response:
                                recognized_text = stt_response["text"]
                                logger.info(f"批处理：Dify Audio-to-Text成功 (来自 {current_sender_nickname_for_log}): {recognized_text[:50]}...")
                                if not recognized_text.strip(): recognized_text = "[空语音或无法识别]"
                                all_content_parts.append(f"[语音转文字: {recognized_text}]")
                            else:
                                error_detail = stt_response.get('error', '未知STT错误') if isinstance(stt_response, dict) else str(stt_response)
                                err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 条语音 '{mp3_filename_hint}' Dify STT失败 ({error_detail})。"
                                logger.error(err_msg + f" Dify STT响应: {stt_response}")
                                encountered_errors.append(err_msg)
                                all_content_parts.append(f"[{err_msg}]")
                    except Exception as e_stt: 
                        logger.error(f"调用Dify STT时发生意外错误 for '{local_mp3_path}': {e_stt}", exc_info=True)
                        err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 条语音 '{mp3_filename_hint}' Dify STT调用时出错。"
                        encountered_errors.append(err_msg)
                        all_content_parts.append(f"[{err_msg}]")
                    finally: 
                        if local_mp3_path and os.path.exists(local_mp3_path): 
                            try:
                                os.remove(local_mp3_path)
                                logger.debug(f"已删除临时MP3文件: {local_mp3_path}")
                            except OSError as e_del:
                                logger.warning(f"删除临时MP3文件失败 '{local_mp3_path}': {e_del}")
                else: 
                    error_reason = mp3_filename_hint_or_error if isinstance(mp3_filename_hint_or_error, str) else "未知语音获取/转换错误"
                    err_msg = f"系统消息：来自 {current_sender_nickname_for_log} 的第 {i+1} 条语音数据处理失败 ({error_reason})。"
                    if local_mp3_path and not os.path.exists(local_mp3_path): 
                        err_msg += " (转换后文件未找到)"
                    logger.error(err_msg)
                    encountered_errors.append(err_msg)
                    all_content_parts.append(f"[{err_msg}]")
            else:
                logger.warning(f"批处理：跳过来自 {current_sender_nickname_for_log} 的未知或不支持类型的消息: {msg_type}")

        # --- 构造前缀 ---
        prefix_elements = []
        if is_group: # 群聊
            if room_id_for_prefix:
                prefix_elements.append(f"群ID：{room_id_for_prefix}")
            if sender_id_from_first_msg: # 群聊中的个人发送者ID
                prefix_elements.append(f"好友ID：{sender_id_from_first_msg}")
            if sender_nickname_from_first_msg and sender_nickname_from_first_msg.strip():
                prefix_elements.append(f"好友昵称：{sender_nickname_from_first_msg}")
        else: # 私聊
            if sender_id_from_first_msg: # 私聊的发送者ID
                prefix_elements.append(f"好友ID：{sender_id_from_first_msg}")
            if sender_nickname_from_first_msg and sender_nickname_from_first_msg.strip():
                prefix_elements.append(f"好友昵称：{sender_nickname_from_first_msg}")
        
        nlp_prefix_str = ""
        if prefix_elements:
            nlp_prefix_str = f"[{'，'.join(prefix_elements)}] "
        
        # --- 合并内容和前缀 ---
        actual_message_content = "\n".join(filter(None, all_content_parts)).strip()
        
        if not actual_message_content: 
            if all_dify_files_payload:
                actual_message_content = "附件已上传，请处理。" 
            elif encountered_errors:
                actual_message_content = "处理消息时发生错误，详情见错误列表。" 
            else:
                actual_message_content = "收到空消息或所有消息均未能处理。"

        final_query_text = f"{nlp_prefix_str}{actual_message_content}"
        
        logger.info(f"构造的Dify查询 (前缀部分: '{nlp_prefix_str}', 内容部分: '{actual_message_content[:100].replace(chr(10), ' ')}...'), 完整查询 (前200字符): {final_query_text[:200].replace(chr(10), ' ')}")
        if len(final_query_text) > 200:
             logger.debug(f"完整的Dify查询: {final_query_text}")
        logger.debug(f"Dify 文件载荷: {all_dify_files_payload}")
        if encountered_errors:
             logger.warning(f"Dify查询准备过程中遇到的错误: {encountered_errors}")

        return final_query_text, all_dify_files_payload, encountered_errors

    def prepare_wechat_response(self, dify_chat_response, original_wechat_msg_or_first_in_batch):
        actions_to_send = [] 
        default_error_text = "抱歉，AI服务暂时无法响应，请稍后再试。"

        if not dify_chat_response or isinstance(dify_chat_response, str):
            logger.error(f"Dify 响应无效或为错误字符串: {dify_chat_response}")
            actions_to_send.append({"type": "text", "content": dify_chat_response if isinstance(dify_chat_response, str) else default_error_text})
            return actions_to_send

        if "error" in dify_chat_response:
            error_detail = "未知错误"
            if isinstance(dify_chat_response.get('details_json'), dict) and 'message' in dify_chat_response['details_json']:
                error_detail = dify_chat_response['details_json']['message']
            elif isinstance(dify_chat_response.get('details'), str) and dify_chat_response['details']:
                title_match = re.search(r"<title>(.*?)</title>", dify_chat_response['details'], re.IGNORECASE | re.DOTALL)
                body_text_match = re.search(r"<body.*?>(.*?)</body>", dify_chat_response['details'], re.IGNORECASE | re.DOTALL)
                if title_match: error_detail = title_match.group(1).strip()
                elif body_text_match:
                    extracted_text = re.sub('<[^<]+?>', '', body_text_match.group(1)).strip()
                    error_detail = extracted_text[:200] + "..." if len(extracted_text) > 200 else extracted_text
                else: error_detail = dify_chat_response['error'] 
            else: error_detail = dify_chat_response.get('error', '错误详情未提供')
            text_to_send = f"AI 服务暂时遇到问题：{error_detail}"
            logger.error(f"Dify API 返回错误: {text_to_send}. 原始Dify响应: {dify_chat_response}")
            actions_to_send.append({"type": "text", "content": text_to_send})
            return actions_to_send

        answer_text = dify_chat_response.get("answer")
        
        if answer_text:
            parts = re.split(f'({self.markdown_image_regex})', answer_text)
            
            for part in parts:
                if not part: 
                    continue
                
                match = re.fullmatch(self.markdown_image_regex, part)
                if match:
                    alt_text = match.group(1) 
                    image_url = match.group(2) 
                    logger.info(f"从Dify回复中解析到Markdown图片: URL='{image_url}', Alt='{alt_text}'")
                    if image_url:
                        actions_to_send.append({"type": "image", "url": image_url, "alt_text": alt_text})
                else:
                    text_content = part.strip()
                    if text_content:
                        actions_to_send.append({"type": "text", "content": text_content})
        else:
            logger.warning(f"Dify 响应中未找到 'answer' 字段或为空: {dify_chat_response}")

        dify_generated_files = dify_chat_response.get("message_files", []) 
        if dify_generated_files:
            for file_info in dify_generated_files:
                if file_info.get("type") == "image" and file_info.get("url"):
                    already_added = False
                    for action in actions_to_send:
                        if action["type"] == "image" and action["url"] == file_info["url"]:
                            already_added = True
                            break
                    if not already_added:
                        logger.info(f"Dify 通过 message_files 返回了一张生成的图片: {file_info['url']}")
                        actions_to_send.append({
                            "type": "image", 
                            "url": file_info["url"],
                            "alt_text": file_info.get("name", "Dify生成的图片") 
                        })
                        if not answer_text: 
                            if not any(action["type"] == "text" for action in actions_to_send):
                                actions_to_send.insert(0, {"type": "text", "content": "[图片]"})
        
        if not actions_to_send: 
            if not answer_text and not dify_generated_files:
                 actions_to_send.append({"type": "text", "content": default_error_text})
                 logger.warning(f"Dify 响应既无有效文本也无生成的媒体文件。原始Dify响应: {dify_chat_response}")
            elif answer_text and not any(action.get("content", "").strip() or action.get("url", "").strip() for action in actions_to_send):
                actions_to_send.append({"type": "text", "content": "[AI回复了空内容或无效格式]"})
        return actions_to_send