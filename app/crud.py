# app/crud.py
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
import datetime # 标准库

from . import models, schemas

# --- ReminderTaskDB CRUD ---
def get_task(db: Session, task_id: str) -> Optional[models.ReminderTaskDB]:
    return db.query(models.ReminderTaskDB).filter(models.ReminderTaskDB.id == task_id).first()

def get_tasks(db: Session, skip: int = 0, limit: int = 100) -> List[models.ReminderTaskDB]:
    return db.query(models.ReminderTaskDB).order_by(models.ReminderTaskDB.created_at.desc()).offset(skip).limit(limit).all()

def get_pending_tasks_for_scheduler(db: Session) -> List[models.ReminderTaskDB]:
    return db.query(models.ReminderTaskDB).filter(
        models.ReminderTaskDB.status.in_([models.TaskStatusEnum.PENDING, models.TaskStatusEnum.PENDING_CALCULATION]),
        models.ReminderTaskDB.next_trigger_time.isnot(None) # type: ignore
    ).all()

def get_tasks_for_recalculation(db: Session) -> List[models.ReminderTaskDB]:
    return db.query(models.ReminderTaskDB).filter(models.ReminderTaskDB.status == models.TaskStatusEnum.PENDING_CALCULATION).all()

def create_task(db: Session, task_data: schemas.TaskInfoCreate, initial_next_trigger_time: Optional[datetime.datetime], initial_status: models.TaskStatusEnum) -> models.ReminderTaskDB:
    task_info_full_dict = schemas.TaskInfo(
        **task_data.model_dump(), 
        task_creation_time=datetime.datetime.now() # 本地时间
    ).model_dump(mode='json')

    db_task = models.ReminderTaskDB(
        task_name=task_data.task_name,
        task_info=task_info_full_dict,
        status=initial_status,
        next_trigger_time=initial_next_trigger_time, # 本地时间
        is_recurring=task_data.is_recurring,
        created_at=datetime.datetime.now() # 本地时间
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task

def update_task(db: Session, task_id: str, task_update_data: schemas.TaskUpdateRequest) -> Optional[models.ReminderTaskDB]:
    db_task = get_task(db, task_id)
    if not db_task:
        return None

    update_data_dict = task_update_data.model_dump(exclude_unset=True)

    if "task_info" in update_data_dict and update_data_dict["task_info"] is not None:
        current_task_info_dict = db_task.task_info
        updated_task_info_partial_dict = update_data_dict["task_info"]
        merged_task_info_data = {**current_task_info_dict, **updated_task_info_partial_dict}
        db_task.task_info = schemas.TaskInfo(**merged_task_info_data).model_dump(mode='json')
        
        if 'task_name' in updated_task_info_partial_dict:
            db_task.task_name = updated_task_info_partial_dict['task_name']
        if 'is_recurring' in updated_task_info_partial_dict:
             db_task.is_recurring = updated_task_info_partial_dict['is_recurring']
    
    if "status" in update_data_dict:
        db_task.status = update_data_dict["status"]
    
    if "next_trigger_time" in update_data_dict:
        db_task.next_trigger_time = update_data_dict["next_trigger_time"] # 本地时间

    db.commit()
    db.refresh(db_task)
    return db_task

def delete_task(db: Session, task_id: str) -> bool:
    db_task = get_task(db, task_id)
    if db_task:
        db.delete(db_task)
        db.commit()
        return True
    return False

# --- HolidayDateDB CRUD ---
def get_holiday_date(db: Session, date_str: str) -> Optional[models.HolidayDateDB]:
    return db.query(models.HolidayDateDB).filter(models.HolidayDateDB.date == date_str).first()

def get_holiday_dates_for_year(db: Session, year: int) -> List[models.HolidayDateDB]:
    return db.query(models.HolidayDateDB).filter(models.HolidayDateDB.year == year).order_by(models.HolidayDateDB.date).all()

def create_or_update_holiday_dates(db: Session, year: int, holiday_data_list: List[Dict[str, Any]]):
    print(f"正在为年份 {year} 创建或更新日历数据...")
    created_count = 0
    updated_count = 0
    all_api_dates_in_year = []

    for month_data in holiday_data_list:
        for day_data in month_data.get("days", []):
            all_api_dates_in_year.append(day_data["date"])
            existing_date_db = get_holiday_date(db, day_data["date"])
            day_type_from_api = day_data.get("type")
            type_des_from_api = day_data.get("typeDes")

            if existing_date_db:
                changed = False
                if existing_date_db.day_type != day_type_from_api: existing_date_db.day_type = day_type_from_api; changed = True
                if existing_date_db.type_des != type_des_from_api: existing_date_db.type_des = type_des_from_api; changed = True
                if existing_date_db.lunar_calendar != day_data.get("lunarCalendar"): existing_date_db.lunar_calendar = day_data.get("lunarCalendar"); changed = True
                existing_date_db.raw_data = day_data
                if changed: updated_count += 1
            else:
                db.add(models.HolidayDateDB(
                    date=day_data["date"], year=day_data.get("year", year),
                    month=day_data.get("month", int(day_data["date"][5:7])), day=int(day_data["date"][8:10]),
                    week_day=day_data.get("weekDay"), day_type=day_type_from_api,
                    type_des=type_des_from_api, lunar_calendar=day_data.get("lunarCalendar"),
                    raw_data=day_data
                ))
                created_count += 1
    if created_count > 0 or updated_count > 0: db.commit()
    print(f"年份 {year} 日历数据处理完毕: 新增 {created_count} 条, 更新 {updated_count} 条.")