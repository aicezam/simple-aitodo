# app/services/holiday_service.py
import httpx
import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.config import settings
from app import crud, models # crud 用于存储, models 用于类型提示

async def fetch_holiday_data_from_api(year: int) -> Optional[List[Dict[str, Any]]]:
    if not settings.HOLIDAY_APP_ID or not settings.HOLIDAY_APP_SECRET:
        print("错误：节假日API的 app_id 或 app_secret 未配置。")
        return None

    url = settings.HOLIDAY_API_URL_TEMPLATE.format(year=year)
    params = {
        "app_id": settings.HOLIDAY_APP_ID,
        "app_secret": settings.HOLIDAY_APP_SECRET,
        "ignoreHoliday": "false"
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 1 and "data" in data:
                print(f"成功从API获取年份 {year} 的日历数据。")
                return data["data"]
            else:
                print(f"API获取年份 {year} 日历数据失败: {data.get('msg')}")
                return None
    except httpx.HTTPStatusError as e:
        print(f"请求节假日API ({year}) 时发生HTTP错误: {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as e:
        print(f"请求节假日API ({year}) 时发生错误: {e}")
        return None
    except Exception as e:
        print(f"处理节假日API ({year}) 响应时发生未知错误: {e}")
        return None

async def update_calendar_data_for_year(db: Session, year: int, force_update: bool = False) -> bool:
    print(f"尝试更新年份 {year} 的日历数据...")
    
    if not force_update:
        # 简单检查数据库中是否已有该年份的大量数据 (例如 > 300条)
        # 避免不必要的API调用
        existing_data_count = db.query(models.HolidayDateDB).filter(models.HolidayDateDB.year == year).count()
        if existing_data_count > 300: # 假设一年至少有300多天数据才算完整
             print(f"年份 {year} 的日历数据已在数据库中（{existing_data_count}条），跳过API获取。如需强制更新请使用 force_update=True。")
             return True

    api_data = await fetch_holiday_data_from_api(year)
    if api_data:
        try:
            # crud.create_holiday_dates 改名为 crud.create_or_update_holiday_dates
            crud.create_or_update_holiday_dates(db, year, api_data)
            print(f"年份 {year} 的日历数据已成功同步到数据库。")
            return True
        except Exception as e:
            print(f"存储年份 {year} 日历数据到数据库时出错: {e}")
            # 在这里可以考虑回滚 db.rollback()，但 crud 层通常处理 commit
            return False
    return False

async def ensure_calendar_data_exists(db: Session, target_year: Optional[int] = None, force: bool = False):
    current_year = datetime.datetime.now().year
    years_to_check = []

    if target_year:
        years_to_check.append(target_year)
    else: # 默认检查当年和明年
        years_to_check = [current_year, current_year + 1]

    for year in years_to_check:
        # 如果不是强制更新，先检查数据是否存在且大致完整
        # update_calendar_data_for_year 内部已有检查，除非 force=True
        await update_calendar_data_for_year(db, year, force_update=force)