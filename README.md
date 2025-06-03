
## 安装与启动

### 1. 先决条件

*   Python 3.8+
*   pip
*   一个关系型数据库 (默认为 SQLite，可配置为 PostgreSQL 等)
*   (可选) 对应外部服务的 API Key 和配置信息 (见下方 `.env` 配置)

### 2. 安装步骤

1.  **克隆代码库**:
    ```bash
    git clone <your-repository-url>
    cd <repository-name>
    ```

2.  **创建并激活虚拟环境** (推荐):
    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```

3.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **配置环境变量**:
    复制项目根目录下的 `.env.example` 或根据下面的说明创建一个 `.env` 文件，并填入必要的配置信息。

    ```
    # .env 文件示例内容

    # 数据库URL示例:
    DATABASE_URL="sqlite:///./reminders.db"
    # DATABASE_URL="postgresql://user:password@host:port/dbname"

    # Dify API 配置 (如果使用 Dify 生成内容)
    DIFY_API_KEY="app-xxxxxxxxxxxxxxxxxxxxxx"
    DIFY_BASE_URL="https://api.dify.ai/v1" # 或您的 Dify 实例 URL

    # 节假日API配置 (请自主从 www.mxnzp.com 申请)
    HOLIDAY_API_URL_TEMPLATE="https://www.mxnzp.com/api/holiday/list/year/{year}"
    HOLIDAY_APP_ID="your_holiday_app_id"
    HOLIDAY_APP_SECRET="your_holiday_app_secret"

    # 邮件配置 (如果使用邮件提醒)
    MAIL_SERVER="smtp.example.com"
    MAIL_PORT=465 # 或 587
    MAIL_USERNAME="your_email_username"
    MAIL_PASSWORD="your_email_password"
    MAIL_SENDER="sender_email@example.com"

    # AI Agent 配置 (用于自然语言处理)
    # --- DeepSeek 示例 ---
    AI_API_URL="https://api.deepseek.com/chat/completions"
    AI_API_KEY="sk-your_deepseek_api_key"
    AI_MODEL_NAME="deepseek-chat"

    # 默认Webhook 配置 (例如对接微信机器人，例如 [WeChatPadPro](https://github.com/luolin-ai/WeChatPadPro/))
    DEFAULT_WEBHOOK_ENABLED=true # 设置为 true 启用, false 禁用
    DEFAULT_WEBHOOK_URL="http://your_webhook_receiver_url/message/SendTextMessage?key=your_receiver_key" # 替换为您的实际URL
    DEFAULT_WEBHOOK_METHOD="POST" # Webhook 通常使用 POST
    DEFAULT_WEBHOOK_HEADERS_JSON='{"Content-Type": "application/json"}' # JSON 字符串格式的请求头
    # 下面是一个微信文本消息的 BODY 模板示例 (WeChatPadPro)。
    # 注意：模板中的 {{content}} (最终提醒文本)、{{user_id}} (目标聊天ID)、{{triggering_user_id}} (原始触发者ID) 会被替换。
    DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON='{
      "MsgItem": [
        {
          "AtWxIDList": ["{{triggering_user_id}}"],  # 示例：在群聊中@原始触发者
          "ImageContent": "",
          "MsgType": 0,
          "TextContent": "{{content}}",
          "ToUserName": "{{user_id}}"
        }
      ]
    }'
    # --- 钉钉机器人 Webhook 示例 (注释掉，如需使用请取消注释并配置,注意只能有一个webhook) ---
    # DEFAULT_WEBHOOK_ENABLED=true
    # DEFAULT_WEBHOOK_URL="https://oapi.dingtalk.com/robot/send?access_token=your_dingtalk_access_token"
    # DEFAULT_WEBHOOK_METHOD="POST"
    # DEFAULT_WEBHOOK_HEADERS_JSON='{"Content-Type": "application/json"}'
    # DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON='{"msgtype": "text", "text": {"content": "提醒: {{content}}\n触发者: {{triggering_user_id}}\n触发关键词"}}'


    # 服务端 API 密钥 (用于保护 API 接口)
    # 请替换为您自己的强API密钥，客户端请求时需在 X-API-Key 请求头中提供此密钥
    SERVER_API_KEY="your_strong_secret_api_key"
    ```
    **重要**:
    *   请务必将示例值替换为您自己的实际配置。
    *   `SERVER_API_KEY` 是用于保护 API 接口的密钥，客户端在调用受保护接口时需要在 `X-API-Key` 请求头中提供此值。如果留空，则 API 鉴权不启用。
    *   `AI_API_URL`, `AI_API_KEY`, `AI_MODEL_NAME` 需要根据您选择的 AI 服务商进行配置。`.env` 文件中提供了多个服务商的示例，请选择一个并取消注释，填入您的密钥。
    *   `DEFAULT_WEBHOOK_BODY_TEMPLATE_JSON` 中的占位符 `{{content}}` 会被替换为最终的提醒文本 (可能包含@信息)，`{{base_content}}` 为纯净提醒内容，`{{user_id}}` 或 `{{target_chat_id}}` 为通知的目标聊天ID，`{{triggering_user_id}}` 为原始触发任务的用户ID，`{{mention_nickname}}` 为群聊中要@的昵称, `{{task_name}}` 为任务名，`{{task_description}}` 为任务描述。请根据您的 Webhook 接收端调整模板。

### 3. 启动应用

*   **开发模式** (使用 Uvicorn, 支持热重载):
    ```bash
    uvicorn app.main:app --reload --host 0.0.0.0 --port 5004
    ```
    服务将运行在 `http://0.0.0.0:5004`。

*   **生产模式** (使用 Gunicorn):
    项目已包含 `gunicorn_conf.py` 配置文件。
    ```bash
    gunicorn -c gunicorn_conf.py app.main:app
    ```
    Gunicorn 会根据 `gunicorn_conf.py` 中的配置 (如 `bind = '0.0.0.0:5004'`) 启动服务。

应用启动时会自动初始化数据库 (如果表不存在) 并启动任务调度器。

## API 文档

API 接口统一前缀为 `/api/v1`。所有受保护的接口都需要在请求头中包含 `X-API-Key: your_server_api_key`。

### 1. 健康检查

*   **GET** `/health`
    *   描述: 检查服务是否正常运行。
    *   响应: `{"status": "ok", "serverTime": "YYYY-MM-DDTHH:MM:SS.ffffff"}`

### 2. 任务管理 (自然语言 API)

*   **POST** `/tasks/natural/`
    *   描述: 通过自然语言处理任务（创建、查询、修改、删除）。AI 模型会解析 `query` 字段，并尝试将其转换为内部的任务操作和参数。
    *   请求体: `schemas.NaturalLanguageTaskRequest`
        ```json
        {
          "query": "提醒我明天下午三点开会，通过微信发给我", // 完整的用户输入，包括潜在的上下文前缀
          "user_id": "wxid_user123" // 发起请求的用户ID, 会作为默认的triggering_user_id
        }
        ```
        **AI 提示工程说明**:
        系统会向配置的 AI 大模型发送一个精心构造的 Prompt，其中包含用户原始查询、当前时间、以及任务数据结构的详细描述 (基于 `app/schemas.py`)。AI 模型被要求严格按照预定义的 JSON 格式返回解析结果，包括操作类型 (`CREATE_TASK`, `QUERY_TASKS`, `UPDATE_TASK`, `DELETE_TASK`) 和相应的参数。
        AI 需要解析用户输入中可能存在的上下文前缀，例如：
        `[群ID：group123，好友ID：wxid_user456，好友昵称：张三] 明天提醒我打卡`
        *   `群ID` -> `target_chat_id`
        *   `好友ID` -> `triggering_user_id`
        *   `好友昵称` -> `mention_user_nickname`
        如果 AI 未能提供通知渠道信息，且系统配置了默认 Webhook，则会自动使用默认 Webhook。
    *   响应:
        *   创建成功: `201 Created`, `schemas.TaskResponse`
        *   查询成功: `200 OK`, `List[schemas.TaskResponse]`
        *   更新成功: `200 OK`, `schemas.TaskResponse`
        *   删除成功: `200 OK`, `{"message": "任务已成功删除。"}`
        *   解析或处理失败: `400 Bad Request` 或 `503 Service Unavailable` (如 AI 服务未配置)

### 3. 任务管理 (结构化 API)

*   **POST** `/tasks/`
    *   描述: 创建一个新的提醒任务。
    *   请求体: `schemas.TaskCreateRequest`
        ```json
        // 示例: 创建一个一次性倒计时提醒，通过默认 webhook 通知
        {
          "task_info": {
            "task_name": "喝水提醒",
            "description": "每天下午3点提醒我喝水",
            "triggering_user_id": "wxid_user123", // 触发任务的用户
            "target_chat_id": "wxid_user123",   // 通知目标 (私聊)
            // "mention_user_nickname": null, // 私聊不需要
            "webhook_channel": { // 如果不提供，且 DEFAULT_WEBHOOK_ENABLED=true，则使用默认配置
              "url": "YOUR_WEBHOOK_URL_IF_NOT_USING_DEFAULT",
              "method": "POST",
              "headers": {"Content-Type": "application/json"},
              "payload_template": {"text": "{{content}} to {{user_id}}"} // 自定义模板
            },
            // "email_channel": null, // 或者配置 email_channel
            "reminder_content": "记得喝水！",
            "is_dify_generated": false,
            "is_recurring": false,
            "countdown_config": {
              "countdown_duration": "5m" // 5分钟后提醒
            }
            // "one_time_specific_config": null // 与 countdown_config 二选一
          }
        }
        ```
        ```json
        // 示例: 创建一个工作日每天早上9点通过邮件提醒的周期性任务
        {
          "task_info": {
            "task_name": "每日站会提醒",
            "triggering_user_id": "user_email_id",
            "target_chat_id": "user_email_id", // 对于邮件，可以复用
            "email_channel": {
              "subject": "每日站会提醒",
              "recipient_email": "test@example.com"
            },
            "reminder_content": "准备参加每日站会。",
            "is_recurring": true,
            "cron_config": {
              "cron_expression": "0 9 * * *", // 每天9:00
              "limit_days": ["WORKDAY"] // 仅工作日
            }
          }
        }
        ```
    *   响应: `201 Created`, `schemas.TaskResponse`

*   **GET** `/tasks/`
    *   描述: 查询任务列表 (支持分页)。
    *   查询参数: `skip` (int, default 0), `limit` (int, default 100)。
    *   响应: `List[schemas.TaskResponse]`

*   **GET** `/tasks/{task_id}`
    *   描述: 查询指定 ID 的任务。
    *   响应: `schemas.TaskResponse` 或 `404 Not Found`

*   **PUT** `/tasks/{task_id}`
    *   描述: 编辑指定 ID 的任务。
    *   请求体: `schemas.TaskUpdateRequest` (可以只包含需要更新的字段)
        ```json
        // 示例: 更新任务状态和提醒内容
        {
          "task_info": {
            "task_name": "喝水提醒 (已更新)", // task_name 是 TaskInfoCreate 的必填字段，更新时如果 task_info 存在则需要提供
            "reminder_content": "该喝水了，朋友！",
            // 假设原任务是 webhook, 更新时也必须提供一个渠道
            "webhook_channel": { 
                "url": "...", "method": "POST", "payload_template": {"text": "{{content}}"}
            }
            // ... 其他 task_info 字段如果需要更新
          },
          "status": "待执行" // 使用 TaskStatusEnum 中的值
        }
        ```
    *   响应: `schemas.TaskResponse` 或 `404 Not Found`

*   **DELETE** `/tasks/{task_id}`
    *   描述: 删除指定 ID 的任务。
    *   响应: `204 No Content` 或 `404 Not Found`



### 4. 管理接口

*   **POST** `/admin/update-calendar/{year}`
    *   描述: 手动触发更新指定年份的日历数据。
    *   路径参数: `year` (int)
    *   查询参数: `force` (bool, default False) - 是否强制从 API 获取，即使本地已有数据。
    *   响应: `{"message": "年份 YYYY 日历数据更新请求已处理。"}` 或错误信息。

*   **POST** `/admin/trigger-daily-maintenance`
    *   描述: 手动触发执行每日维护任务 (检查日历、重新计算 `PENDING_CALCULATION` 状态的任务等)。
    *   响应: `{"message": "每日维护任务已异步触发。"}`

### 主要数据模型 (`app/schemas.py`)

*   `TaskInfoBase`: 任务核心信息的基础模型，包含任务名、描述、触发用户ID、目标聊天ID、提醒内容、通知渠道配置 (Webhook/Email)、周期性配置 (Cron) 或一次性配置 (倒计时/特定时间) 等。
*   `WebhookChannelConfig`: Webhook 通知渠道的详细配置 (URL, Method, Headers, Payload Template)。
*   `EmailChannelConfig`: 邮件通知渠道的详细配置 (主题, 收件人)。
*   `CronConfig`: Cron 任务的详细配置 (表达式, 起止时间, 限制日期类型, 农历月日)。
*   `TaskStatusEnum`: 任务状态枚举 (`待执行`, `待计算`, `执行中`, `执行完成`, `失败`)。

## 附属项目：微信 Dify 助手 (`simple_wechat_on_dify/`)

项目包含一个名为 `simple_wechat_on_dify` 的子目录，这是一个独立的、简单的 Python 应用，用于将微信个人号（通过 [WeChatPadPro](https://github.com/luolin-ai/WeChatPadPro/) 等HTTP API框架接入）与 Dify AI 应用连接起来。

### `simple_wechat_on_dify` 功能：

*   通过 WebSocket 接收微信消息。
*   对指定条件的消息 (如私聊消息、群聊中@机器人的消息) 进行处理。
*   支持文本、图片、语音（暂未实现）消息。
*   语音消息会先进行格式转换 (如 SILK/AMR 转 MP3)，然后尝试通过 Dify 的 Audio-to-Text (STT) 服务转为文字。
*   将处理后的文本和上传到 Dify 的文件（图片）一起作为查询发送给 Dify 应用。
*   接收 Dify 的回复，并将其发送回微信。
*   支持消息批处理：短时间内收到的来自同一用户的多条消息会合并后一次性发送给 Dify。
*   Dify 回复中的 Markdown 图片链接会被解析并作为图片发送到微信。

### `simple_wechat_on_dify` 安装与配置：

1.  进入子目录: `cd simple_wechat_on_dify`
2.  安装依赖: `pip install -r requirements.txt` (注意，此 `requirements.txt` 与主项目的不同)
3.  配置 `.env` 文件 (在 `simple_wechat_on_dify` 目录下创建):
    ```env
    # WeChatPadPro Configuration
    WECHAT_API_BASE_URL="http://your_wechatpadpro_host:port"
    WECHAT_WS_URL="ws://your_wechatpadpro_host:port"
    WECHAT_TOKEN_KEY="your_wechatpadpro_token"
    WECHAT_BOT_WXID="your_bot_wxid" # 机器人自身的微信ID

    # Dify Configuration
    DIFY_API_KEY="app-your_dify_app_api_key"
    DIFY_BASE_URL="https://api.dify.ai/v1" # 或您的 Dify 实例 URL

    # Application Configuration
    LOG_LEVEL="INFO" # 日志级别 (DEBUG, INFO, WARNING, ERROR)
    MAX_FILE_SIZE_MB="15" # Dify 文件上传大小限制 (MB)
    # DIFY_USER_ID_PREFIX="wechat_" # (可选) Dify 用户 ID 前缀
    MESSAGE_BATCH_DELAY_SECONDS="5" # 消息批处理延迟秒数
    ```
4.  **额外依赖**: `ffmpeg`
    语音消息处理依赖 `ffmpeg` 进行音频格式转换。请确保您的系统已安装 `ffmpeg` 并且其路径已添加到系统环境变量 `PATH` 中。

### `simple_wechat_on_dify` 运行：

```bash
python simple_wechat_on_dify/main.py
