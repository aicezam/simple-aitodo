# app/models.py
import uuid
import datetime
import enum
from typing import Optional, Any, Dict

from sqlalchemy import String, DateTime, JSON, Boolean, Integer, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

# 假设您的 Base 是通过 declarative_base() 创建的，这与 Mapped 兼容
from app.database import Base

class TaskStatusEnum(str, enum.Enum):
    PENDING = "待执行"
    PENDING_CALCULATION = "待计算"
    RUNNING = "执行中"
    COMPLETED = "执行完成"
    FAILED = "失败"

class ReminderTaskDB(Base):
    __tablename__ = "reminder_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # 对于 JSON 列，可以更精确地指定类型，例如 Mapped[Dict[str, Any]] 或 Mapped[List[Any]]
    # 如果结构未知或可变，Mapped[Any] 或 Mapped[dict] 也可以
    task_info: Mapped[Dict[str, Any]] = mapped_column(JSON)
    task_name: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.now) # 本地时间
    status: Mapped[TaskStatusEnum] = mapped_column(SQLEnum(TaskStatusEnum), default=TaskStatusEnum.PENDING, index=True)
    next_trigger_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True, index=True) # 本地时间
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)

class HolidayDateDB(Base):
    __tablename__ = "holiday_dates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, unique=True, index=True) # "YYYY-MM-DD"
    year: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[int] = mapped_column(Integer)
    day: Mapped[int] = mapped_column(Integer)
    week_day: Mapped[int] = mapped_column(Integer) # API: 1-周一 ... 7-周日
    day_type: Mapped[Optional[int]] = mapped_column(Integer, nullable=True) # API: 0 工作日 1 假日 2 节假日
    type_des: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lunar_calendar: Mapped[Optional[str]] = mapped_column(String, nullable=True) # API返回的农历字符串
    raw_data: Mapped[Dict[str, Any]] = mapped_column(JSON) # 假设 raw_data 是一个 JSON 对象

    # 使用 Mapped 后，类型检查器能更好地理解实例属性的类型
    # 为确保 nullable=True 的 day_type 在比较前得到妥善处理，增加对 None 的检查

    def is_workday(self) -> bool:
        if self.day_type is None: # 如果 day_type 未设置 (为 None)
            return False # 或者根据业务逻辑抛出异常或返回特定值
        return self.day_type == 0

    def is_holiday(self) -> bool:
        if self.day_type is None:
            return False
        return self.day_type == 1 or self.day_type == 2

    def is_legal_holiday(self) -> bool:
        if self.day_type is None:
            return False
        return self.day_type == 2

    def is_weekend(self) -> bool:
        # week_day 不是 nullable 的，但 day_type 是
        if self.day_type is None:
            return False
        return self.week_day in (6, 7) and self.day_type == 1