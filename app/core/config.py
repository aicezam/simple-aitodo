# app/core/config.py
import os
import json
from dotenv import load_dotenv
from pathlib import Path
from typing import Optional, Dict, Any

env_path = Path(__file__).resolve().parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# API_KEY_NAME 变量将在 app/main.py 中定义, 但我们可以在这里预先声明它以便在日志消息中使用
LOGGING_API_KEY_NAME = "X-API-Key" # 仅用于下面的日志消息

class Settings:
    PROJECT_NAME: str = "提醒任务服务 (本地时间版)"
    PROJECT_VERSION: str = "1.1.0"

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./reminders.db")

    DIFY_API_KEY: Optional[str] = os.getenv("DIFY_API_KEY")
    DIFY_BASE_URL: Optional[str] = os.getenv("DIFY_BASE_URL")

    HOLIDAY_API_URL_TEMPLATE: str = os.getenv("HOLIDAY_API_URL_TEMPLATE", "https://www.mxnzp.com/api/holiday/list/year/{year}")
    HOLIDAY_APP_ID: Optional[str] = os.getenv("HOLIDAY_APP_ID")
    HOLIDAY_APP_SECRET: Optional[str] = os.getenv("HOLIDAY_APP_SECRET")

    MAIL_SERVER: Optional[str] = os.getenv("MAIL_SERVER")
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", 587))
    MAIL_USERNAME: Optional[str] = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD: Optional[str] = os.getenv("MAIL_PASSWORD")
    MAIL_SENDER: Optional[str] = os.getenv("MAIL_SENDER")

    API_V1_STR: str = "/api/v1"

    # --- AI 模型设置 ---
    AI_API_URL: Optional[str] = os.getenv("AI_API_URL")
    AI_API_KEY: Optional[str] = os.getenv("AI_API_KEY")
    AI_MODEL_NAME: Optional[str] = os.getenv("AI_MODEL_NAME", "deepseek-ai/DeepSeek-chat")

    # --- 默认 Webhook 设置 ---
    DEFAULT_WEBHOOK_ENABLED_STR: Optional[str] = os.getenv("DEFAULT_WEBHOOK_ENABLED", "false")
    DEFAULT_WEBHOOK_ENABLED: bool = DEFAULT_WEBHOOK_ENABLED_STR.lower() == 'true' if DEFAULT_WEBHOOK_ENABLED_STR else False
    
    DEFAULT_WEBHOOK_URL: Optional[str] = os.getenv("DEFAULT_WEBHOOK_URL")
    DEFAULT_WEBHOOK_METHOD: str = os.getenv("DEFAULT_WEBHOOK_METHOD", "POST")
    
    DEFAULT_WEBHOOK_HEADERS_JSON_STR: Optional[str] = os.getenv("DEFAULT_WEBHOOK_HEADERS_JSON")
    DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON_STR: Optional[str] = os.getenv("DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON")

    DEFAULT_WEBHOOK_HEADERS: Optional[Dict[str, str]] = None
    DEFAULT_WEBHOOK_BODY_TEMPLATE: Optional[Dict[str, Any]] = None

    if DEFAULT_WEBHOOK_ENABLED and DEFAULT_WEBHOOK_URL:
        if DEFAULT_WEBHOOK_HEADERS_JSON_STR:
            try:
                DEFAULT_WEBHOOK_HEADERS = json.loads(DEFAULT_WEBHOOK_HEADERS_JSON_STR)
            except json.JSONDecodeError as e:
                print(f"警告: DEFAULT_WEBHOOK_HEADERS_JSON 格式无效，将使用空头部: {e}")
                DEFAULT_WEBHOOK_HEADERS = {}
        else:
            DEFAULT_WEBHOOK_HEADERS = {}

        if DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON_STR:
            try:
                DEFAULT_WEBHOOK_BODY_TEMPLATE = json.loads(DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON_STR)
            except json.JSONDecodeError as e:
                print(f"警告: DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON 格式无效，将无法使用默认模板: {e}")

    # --- 服务端 API 密钥 ---
    SERVER_API_KEY: Optional[str] = os.getenv("SERVER_API_KEY")

settings = Settings()

# 验证关键配置
if not settings.DIFY_API_KEY:
    print("警告: DIFY_API_KEY 未配置。")
if not settings.HOLIDAY_APP_ID or not settings.HOLIDAY_APP_SECRET:
    print("警告: HOLIDAY_APP_ID 或 HOLIDAY_APP_SECRET 未配置。")
if not all([settings.MAIL_SERVER, settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_SENDER]):
    print("警告: 邮件服务配置不完整。")

if settings.DEFAULT_WEBHOOK_ENABLED and not settings.DEFAULT_WEBHOOK_URL:
    print("警告: 默认Webhook已启用 (DEFAULT_WEBHOOK_ENABLED=true) 但 DEFAULT_WEBHOOK_URL 未配置。默认Webhook将不会生效。")

if not settings.AI_API_URL or not settings.AI_API_KEY or not settings.AI_MODEL_NAME:
    print("警告: AI 模型配置 (AI_API_URL, AI_API_KEY, AI_MODEL_NAME) 不完整。自然语言创建任务功能将不可用。")

# --- 服务端 API 密钥验证 ---
if not settings.SERVER_API_KEY:
    print("警告: SERVER_API_KEY 未在 .env 文件中配置。API 接口鉴权将不会启用。如果需要接口保护，请配置此密钥。")
else:
    print(f"提示: SERVER_API_KEY 已配置。所有受保护的API接口将需要有效的 '{LOGGING_API_KEY_NAME}' 请求头。")
