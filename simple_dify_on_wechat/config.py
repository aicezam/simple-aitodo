# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# WeChatPadPro Configuration
WECHAT_API_BASE_URL = os.getenv("WECHAT_API_BASE_URL")
WECHAT_WS_URL = os.getenv("WECHAT_WS_URL") 
WECHAT_TOKEN_KEY = os.getenv("WECHAT_TOKEN_KEY") 
WECHAT_ADMIN_KEY = os.getenv("WECHAT_ADMIN_KEY") 
WECHAT_BOT_WXID = os.getenv("WECHAT_BOT_WXID")

# Dify Configuration
DIFY_API_KEY = os.getenv("DIFY_API_KEY")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "https://api.dify.ai/v1")
# DIFY_APP_ID is not used in the current code, can be removed if not planned for future use

# Application Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_FILE_SIZE_MB = os.getenv("MAX_FILE_SIZE_MB", "15") # Kept for DifyHandler and MessageProcessor initialization

DIFY_USER_ID_PREFIX = os.getenv("DIFY_USER_ID_PREFIX", "wechat_") # Retained from original

# MESSAGE_BATCH_DELAY_SECONDS: Read and validate
message_batch_delay_seconds_str = os.getenv("MESSAGE_BATCH_DELAY_SECONDS", "5")
try:
    MESSAGE_BATCH_DELAY_SECONDS = int(message_batch_delay_seconds_str)
    if MESSAGE_BATCH_DELAY_SECONDS < 0:
        raise ValueError("MESSAGE_BATCH_DELAY_SECONDS 不能为负数。")
except ValueError as e:
    raise ValueError(f"错误: MESSAGE_BATCH_DELAY_SECONDS ('{message_batch_delay_seconds_str}') 不是一个有效的非负整数。请检查 .env 文件。Details: {e}")


required_configs = {
    "WECHAT_API_BASE_URL": WECHAT_API_BASE_URL,
    "WECHAT_WS_URL": WECHAT_WS_URL,
    "WECHAT_TOKEN_KEY": WECHAT_TOKEN_KEY,
    "WECHAT_BOT_WXID": WECHAT_BOT_WXID,
    "DIFY_API_KEY": DIFY_API_KEY,
    "DIFY_BASE_URL": DIFY_BASE_URL,
}

missing_configs = [key for key, value in required_configs.items() if value is None]

if missing_configs:
    raise ValueError(f"错误：以下必要的配置项缺失，请检查.env 文件: {', '.join(missing_configs)}")

# Validate MAX_FILE_SIZE_MB format early
try:
    # Ensure it's an int, it will be used as int(MAX_FILE_SIZE_MB) later
    _ = int(MAX_FILE_SIZE_MB) 
    if int(MAX_FILE_SIZE_MB) <=0:
        raise ValueError("MAX_FILE_SIZE_MB 必须是正整数。")
except ValueError as e:
    raise ValueError(f"错误: MAX_FILE_SIZE_MB ('{MAX_FILE_SIZE_MB}') 不是一个有效的正整数。请检查 .env 文件。Details: {e}")