# main.py
import time
import logging
import signal
import threading
import base64 
import requests 
from collections import defaultdict 

from config import LOG_LEVEL, WECHAT_BOT_WXID, MESSAGE_BATCH_DELAY_SECONDS 
from utils.converters import url_to_base64 # 确保此工具函数可用

from wechat_client import WeChatClient
from dify_handler import DifyHandler
from message_processor import MessageProcessor

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

shutdown_event = threading.Event()

class Application:
    def __init__(self):
        logger.info("正在初始化应用程序...")
        self.dify_handler = DifyHandler()
        self.message_processor = MessageProcessor(self.dify_handler)
        self.wechat_client = WeChatClient(message_callback=self.on_wechat_message_received_sync)
        
        self.message_buffers = defaultdict(list)
        self.user_timers = {} 
        self.buffer_locks = defaultdict(threading.Lock) 

        logger.info(f"应用程序初始化完成。消息批处理延迟: {MESSAGE_BATCH_DELAY_SECONDS}秒。")

    def on_wechat_message_received_sync(self, wechat_msg):
        logger.debug(f"原始微信消息进入批处理逻辑: ID={wechat_msg.get('id')}, 类型={wechat_msg.get('type')}")

        if not self.message_processor.should_process_wechat_message(wechat_msg):
            return

        wechat_contact_key = wechat_msg.get("room_id") if wechat_msg.get("is_group") else wechat_msg.get("sender_id")
        if not wechat_contact_key:
            logger.error("无法确定消息的 contact_key (room_id/sender_id)，无法进行批处理。")
            return

        with self.buffer_locks[wechat_contact_key]: 
            self.message_buffers[wechat_contact_key].append(wechat_msg)
            logger.info(f"消息 {wechat_msg.get('id')} 已添加到 {wechat_contact_key} 的缓冲区。"
                        f"当前数量: {len(self.message_buffers[wechat_contact_key])}")

            if wechat_contact_key in self.user_timers:
                timer = self.user_timers.pop(wechat_contact_key, None) 
                if timer:
                    timer.cancel()
                    logger.debug(f"已取消 {wechat_contact_key} 的现有计时器。")
            
            new_timer = threading.Timer(
                MESSAGE_BATCH_DELAY_SECONDS,
                self._trigger_process_batched_messages, 
                args=[wechat_contact_key]
            )
            self.user_timers[wechat_contact_key] = new_timer
            new_timer.daemon = True 
            new_timer.start()
            logger.debug(f"已为 {wechat_contact_key} 启动新的 {MESSAGE_BATCH_DELAY_SECONDS} 秒计时器。")

    def _trigger_process_batched_messages(self, wechat_contact_key):
        logger.info(f"计时器触发，为 {wechat_contact_key} 创建批处理任务线程。")
        thread = threading.Thread(target=self._process_batched_messages_thread_target, args=(wechat_contact_key,), daemon=True)
        thread.start()

    def _process_batched_messages_thread_target(self, wechat_contact_key):
        logger.info(f"线程开始处理 {wechat_contact_key} 的批处理消息...")
        messages_to_process = []
        with self.buffer_locks[wechat_contact_key]: 
            if wechat_contact_key in self.message_buffers: 
                messages_to_process = self.message_buffers.pop(wechat_contact_key, []) 
            
            self.user_timers.pop(wechat_contact_key, None)


        if not messages_to_process:
            logger.info(f"没有为 {wechat_contact_key} 找到待处理的批处理消息（可能已被处理或清空）。")
            return

        logger.info(f"处理来自 {wechat_contact_key} 的 {len(messages_to_process)} 条消息。")

        first_msg = messages_to_process[0]
        actual_sender_id = first_msg.get("sender_id") 
        
        if not actual_sender_id:
            logger.error(f"批处理消息 (来自 {wechat_contact_key}) 中首条消息缺少 sender_id，无法生成 Dify 用户 ID。")
            return

        dify_user_id = self.message_processor.get_dify_user_id(actual_sender_id)
        if not dify_user_id:
            logger.error(f"无法为微信用户 {actual_sender_id} (来自 {wechat_contact_key}) 生成 Dify 用户 ID，处理中止。")
            return
            
        dify_conversation_id = self.message_processor.get_dify_conversation_id(wechat_contact_key)

        logger.info(f"为批处理消息准备 Dify 查询 (Dify用户: {dify_user_id}, 微信联系人: {wechat_contact_key}, Dify会话ID: {dify_conversation_id})")
        
        query_text, dify_files_payload, prep_errors = self.message_processor.prepare_batched_query_for_dify(
            messages_to_process, dify_user_id
        )

        if prep_errors:
            logger.warning(f"为 {wechat_contact_key} 准备Dify查询时发生错误: {prep_errors}")
            # 发送错误给用户 (可选)
            # concatenated_errors = "准备您的消息时发生以下问题：\n" + "\n".join(prep_errors)
            # self.wechat_client.send_text_message(wechat_contact_key, concatenated_errors)
            # return # 决定是否中止，或者即使有准备错误也继续发送query_text

        if not query_text and not dify_files_payload: 
            logger.warning(f"未能为 {wechat_contact_key} 生成有效的 Dify 查询文本或文件，处理中止。")
            if not prep_errors: # 如果没有准备错误，但内容仍为空，发送一个通用消息
                 self.wechat_client.send_text_message(wechat_contact_key, "抱歉，您的消息未能被正确处理。")
            elif prep_errors: # 如果有准备错误，并且最终没有内容，把错误发回去
                error_summary = "抱歉，处理您的消息时遇到了一些问题：\n" + "\n".join(prep_errors)
                self.wechat_client.send_text_message(wechat_contact_key, error_summary)
            return

        logger.info(f"向 Dify 发送批处理查询 (Dify用户: {dify_user_id}, Dify会话ID: {dify_conversation_id}): "
                    f"Query='{query_text[:100].replace(chr(10), ' ')}...', " 
                    f"文件数量={len(dify_files_payload) if dify_files_payload else 0}")
        
        dify_response = self.dify_handler.send_chat_message(
            dify_user_id,
            query_text,
            conversation_id=dify_conversation_id,
            files=dify_files_payload, 
            stream=False 
        )
        logger.debug(f"Dify 原始响应 (批处理 for {wechat_contact_key}): {str(dify_response)[:200]}...")

        if dify_response:
            reply_to_id = wechat_contact_key 
            at_list_for_reply = None 
            # if first_msg.get("is_group"):
            # at_list_for_reply = [actual_sender_id] 
            
            if isinstance(dify_response, dict) and not dify_response.get("error"): 
                new_conversation_id = dify_response.get("conversation_id")
                if new_conversation_id: 
                    self.message_processor.set_dify_conversation_id(wechat_contact_key, new_conversation_id)
                
                # --- 新的响应处理逻辑 ---
                actions_to_send_to_wechat = self.message_processor.prepare_wechat_response(dify_response, first_msg)
                
                if not actions_to_send_to_wechat:
                    logger.warning(f"prepare_wechat_response 为 {wechat_contact_key} 返回空操作列表。Dify响应: {dify_response}")
                    # 可以选择发送一个通用错误消息
                    # self.wechat_client.send_text_message(reply_to_id, "抱歉，AI服务返回的内容无法解析。", at_wxid_list=at_list_for_reply)

                for action in actions_to_send_to_wechat:
                    action_type = action.get("type")
                    if action_type == "text":
                        text_content = action.get("content")
                        if text_content:
                            logger.info(f"准备将 Dify 文本回复发送到微信 {reply_to_id} (批处理): {text_content[:100].replace(chr(10), ' ')}...")
                            self.wechat_client.send_text_message(reply_to_id, text_content, at_wxid_list=at_list_for_reply)
                        else:
                            logger.warning(f"收到的文本操作内容为空 (批处理 for {wechat_contact_key})。")
                    
                    elif action_type == "image":
                        image_url = action.get("url")
                        alt_text = action.get("alt_text", "图片") # 获取alt文本，用于日志
                        if image_url:
                            logger.info(f"Dify 返回图片 URL (批处理 for {wechat_contact_key})，描述: '{alt_text}', URL: {image_url}。尝试下载并转 Base64...")
                            base64_content = url_to_base64(image_url) # utils.converters中的函数
                            if base64_content:
                                self.wechat_client.send_image_message_base64(reply_to_id, base64_content)
                                logger.info(f"成功发送 Base64 图片到 {reply_to_id} (来自URL: {image_url})。")
                            else:
                                logger.error(f"图片 URL {image_url} 处理失败 (批处理 for {wechat_contact_key})。将发送错误文本。")
                                self.wechat_client.send_text_message(reply_to_id, f"抱歉，我试图发送的图片（{alt_text}）处理失败了。", at_wxid_list=at_list_for_reply)
                        else:
                            logger.warning(f"收到的图片操作URL为空 (批处理 for {wechat_contact_key})。")
                    else:
                        logger.warning(f"未知的响应操作类型 '{action_type}' (批处理 for {wechat_contact_key})。")

            else: # Dify 响应中包含错误
                error_message = "AI 服务返回异常，请稍后再试。"
                if isinstance(dify_response, dict) and "error" in dify_response:
                    status_code = dify_response.get('status_code', 'N/A')
                    details = "未知错误详情"
                    if isinstance(dify_response.get('details_json'), dict): 
                        details = dify_response['details_json'].get('message', dify_response.get('error', details))
                    elif isinstance(dify_response.get('details'), str): 
                        details = dify_response.get('error', details) 
                        details = details[:250] 
                    else: 
                        details = dify_response.get('error', details)
                    
                    error_message = f"AI 服务错误 (状态码: {status_code}): {details}"
                logger.error(f"Dify API 错误详情 (批处理 for {wechat_contact_key}): {dify_response if isinstance(dify_response, dict) else str(dify_response)}")
                self.wechat_client.send_text_message(reply_to_id, error_message, at_wxid_list=at_list_for_reply)
        else: # Dify API 未返回任何响应
            logger.error(f"Dify API 未返回任何响应或响应为空 (批处理 for {wechat_contact_key})。")
            error_reply = "AI 服务未响应，请稍后重试。"
            self.wechat_client.send_text_message(wechat_contact_key, error_reply)


    def run(self):
        logger.info("正在启动 WeChat WebSocket 客户端...")
        self.wechat_client.connect_websocket()
        
        try:
            while not shutdown_event.is_set():
                time.sleep(1) 
        except KeyboardInterrupt:
            logger.info("接收到 Ctrl+C 信号，准备关闭...")
        finally:
            self.stop()

    def stop(self):
        logger.info("正在停止应用程序...")
        shutdown_event.set()

        logger.info("正在取消所有待处理的消息计时器...")
        for key in list(self.user_timers.keys()): 
            timer = self.user_timers.pop(key, None) 
            if timer:
                timer.cancel() 
        logger.info("所有计时器已尝试取消。")

        if self.wechat_client:
            self.wechat_client.close_websocket()
        logger.info("应用程序已停止。")

def signal_handler(sig, frame):
    logger.info(f"接收到信号 {sig}, 正在关闭...")
    shutdown_event.set()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    app = Application()
    app.run()