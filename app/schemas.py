# app/schemas.py
from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator, ConfigDict
from typing import Optional, List, Dict, Any
import datetime
import re
from app.models import TaskStatusEnum

# Webhook通知渠道配置模型
class WebhookChannelConfig(BaseModel):
    url: str = Field(..., description="Webhook URL")
    method: str = Field("POST", description="HTTP方法, 例如 POST, GET")
    headers: Optional[Dict[str, str]] = Field(None, description="请求头")
    # payload_template 定义了基础结构，占位符如 {{content}}, {{triggering_user_id}}, {{task_name}} 等会被动态替换
    payload_template: Optional[Dict[str, Any]] = Field(None, description="请求体模板结构。实际发送时占位符会被替换。")

# 邮件通知渠道配置模型
class EmailChannelConfig(BaseModel):
    subject: str = Field(..., description="邮件主题")
    recipient_email: EmailStr = Field(..., description="收件人邮箱地址")

# Cron定时配置模型
class CronConfig(BaseModel):
    cron_expression: str = Field(..., description="标准的Cron表达式")
    start_time: Optional[datetime.datetime] = Field(None, description="任务执行的开始本地时间 (格式: YYYY-MM-DD HH:MM:SS)")
    end_time: Optional[datetime.datetime] = Field(None, description="任务执行的结束本地时间 (格式: YYYY-MM-DD HH:MM:SS)")
    limit_days: Optional[List[str]] = Field(None, description="限制任务执行的日期类型列表, 例如: [\"WORKDAY\"]. 有效值: WORKDAY, HOLIDAY, WEEKEND, WEEKDAY_ONLY")
    is_lunar: bool = Field(False, description="指示是否按农历日期匹配。如果为true，则必须提供lunar_month和lunar_day。")
    lunar_month: Optional[int] = Field(None, ge=1, le=12, description="农历月份 (1-12)，仅当 is_lunar=true 时有效且必需。")
    lunar_day: Optional[int] = Field(None, ge=1, le=30, description="农历日期 (1-30)，仅当 is_lunar=true 时有效且必需。")

    @model_validator(mode='after')
    def check_lunar_fields_consistency(self) -> 'CronConfig':
        if self.is_lunar: 
            if self.lunar_month is None or self.lunar_day is None: 
                raise ValueError("对于农历任务 (is_lunar=true)，必须同时提供 lunar_month 和 lunar_day。")
        elif not self.is_lunar and (self.lunar_month is not None or self.lunar_day is not None): 
            raise ValueError("lunar_month 和 lunar_day 字段仅在 is_lunar=true 时有效。")
        return self

    @field_validator('limit_days')
    @classmethod
    def check_limit_days(cls, v: Optional[List[str]]):
        if v is None: return v 
        allowed_limit_days = {"WORKDAY", "HOLIDAY", "WEEKEND", "WEEKDAY_ONLY"} 
        for item in v: 
            if item.upper() not in allowed_limit_days: 
                raise ValueError(f"limit_days 包含无效值: '{item}'. 允许的值为: {allowed_limit_days}")
        return [item.upper() for item in v] 

# 倒计时配置模型
class CountdownConfig(BaseModel):
    countdown_duration: str = Field(..., description="倒计时时长 (例如: '1d2h3m4s', '30m', '1h')")

    @field_validator('countdown_duration')
    @classmethod
    def check_countdown_duration_format(cls, v: str):
        if not re.match(r'^((?P<days>\d+)d)?((?P<hours>\d+)h)?((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?$', v.lower()) or not v:
            raise ValueError("倒计时格式无效或为空。有效格式如 '1d', '2h30m', '10s'。")
        return v

# 一次性特定时间配置模型
class OneTimeSpecificConfig(BaseModel):
    trigger_at: datetime.datetime = Field(..., description="任务的具体触发本地时间 (格式: YYYY-MM-DD HH:MM:SS)")

# 任务核心信息基础模型
class TaskInfoBase(BaseModel):
    task_name: str = Field(..., min_length=1, description="任务名称，不能为空")
    description: Optional[str] = Field(None, description="任务的详细描述")
    
    # --- 新增和调整的上下文相关字段 ---
    triggering_user_id: Optional[str] = Field(None, description="触发此任务的原始用户ID (例如微信wxid)")
    target_chat_id: Optional[str] = Field(None, description="通知实际发送的目标聊天ID (可以是个人wxid或群聊chatroom_id)")
    mention_user_nickname: Optional[str] = Field(None, description="在群聊中需要@的用户的昵称 (如果获取到)")
    # --- 结束 新增和调整的上下文相关字段 ---

    webhook_channel: Optional[WebhookChannelConfig] = Field(None, description="Webhook通知渠道配置")
    email_channel: Optional[EmailChannelConfig] = Field(None, description="邮件通知渠道配置")
    reminder_content: str = Field(..., min_length=1, description="提醒内容核心文本 (不包含@人等修饰)，不能为空")
    is_dify_generated: bool = Field(False, description="提醒内容是否由Dify服务生成")
    is_recurring: bool = Field(False, description="是否为周期性重复任务")
    cron_config: Optional[CronConfig] = Field(None, description="Cron定时配置，当is_recurring=true时必需")
    countdown_config: Optional[CountdownConfig] = Field(None, description="倒计时配置，当is_recurring=false时，与one_time_specific_config二选一")
    one_time_specific_config: Optional[OneTimeSpecificConfig] = Field(None, description="一次性特定时间配置，当is_recurring=false时，与countdown_config二选一")

    # 原有的 user_id 字段已被 triggering_user_id 和 target_chat_id 替代和细化，
    # 因此 TaskInfoBase 中不再需要名为 user_id 的顶层字段。
    # 如果之前的AI提示或代码逻辑中有生成 "user_id" 字段到 task_info 中，需要调整为使用新的字段。
    # 我们在 main.py 中注入这些新字段。

    @model_validator(mode='after')
    def check_notification_channel_config(self) -> 'TaskInfoBase':
        if bool(self.webhook_channel) + bool(self.email_channel) != 1:
            raise ValueError("必须且只能配置一个提醒渠道 (webhook_channel 或 email_channel)。")
        return self

    @model_validator(mode='after')
    def check_task_type_and_schedule_config(self) -> 'TaskInfoBase':
        if self.is_recurring: 
            if not self.cron_config: 
                raise ValueError("周期性任务 (is_recurring=true) 必须提供 cron_config。")
            if self.countdown_config or self.one_time_specific_config: 
                raise ValueError("周期性任务 (is_recurring=true) 不能同时配置 countdown_config 或 one_time_specific_config。")
        else: 
            if self.cron_config: 
                raise ValueError("一次性任务 (is_recurring=false) 不能配置 cron_config。")
            if bool(self.countdown_config) + bool(self.one_time_specific_config) != 1:
                raise ValueError("一次性任务 (is_recurring=false) 必须且只能配置 countdown_config 或 one_time_specific_config 中的一个。")
        return self

# 用于创建任务的Pydantic模型
class TaskInfoCreate(TaskInfoBase):
    pass 

# 包含任务创建时间等完整信息的Pydantic模型
class TaskInfo(TaskInfoBase):
    task_creation_time: datetime.datetime = Field(description="任务创建的本地时间")

# 创建任务的API请求体模型
class TaskCreateRequest(BaseModel):
    task_info: TaskInfoCreate 

# 更新任务的API请求体模型
class TaskUpdateRequest(BaseModel):
    task_info: Optional[TaskInfoCreate] = Field(None, description="可选，要更新的任务核心信息。")
    status: Optional[TaskStatusEnum] = Field(None, description="可选，要更新的任务状态。")
    next_trigger_time: Optional[datetime.datetime] = Field(None, description="可选，要更新的下次触发本地时间。")

# API响应中表示任务信息的模型
class TaskResponse(BaseModel):
    id: str 
    task_name: str 
    task_info: TaskInfo 
    created_at: datetime.datetime 
    status: TaskStatusEnum 
    next_trigger_time: Optional[datetime.datetime] = Field(None, description="任务下次预计执行的本地时间")
    is_recurring: bool 

    model_config = ConfigDict(from_attributes=True)

# 自然语言处理任务的API请求体模型
class NaturalLanguageTaskRequest(BaseModel):
    query: str = Field(..., description="用户的自然语言输入")
    user_id: Optional[str] = Field(None, description="发起请求的用户的ID (例如微信wxid)") # 明确此user_id为发起人

# Dify聊天请求模型
class DifyChatRequest(BaseModel):
    query: str
    inputs: Optional[Dict[str, Any]] = {}
    response_mode: str = "blocking"
    conversation_id: Optional[str] = None
    user: str = "reminder_service_user" 
    files: Optional[List[Dict[str, Any]]] = None 
    auto_generate_name: bool = True

# Dify聊天响应模型
class DifyChatResponse(BaseModel):
    event: str
    task_id: str 
    id: str 
    message_id: Optional[str] = None 
    conversation_id: str
    mode: str
    answer: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: int