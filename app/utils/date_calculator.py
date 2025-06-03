# app/utils/date_calculator.py
import datetime # 标准库
import re
from typing import Optional, List, Tuple, Callable, Dict
from croniter import croniter
from lunardate import LunarDate # 导入lunardate库

from app.schemas import CronConfig, CountdownConfig, OneTimeSpecificConfig, TaskInfoCreate
from app.models import HolidayDateDB, TaskStatusEnum

def parse_countdown_duration(duration_str: str) -> datetime.timedelta:
    """解析倒计时字符串 (如 '1d2h3m4s') 为 timedelta 对象。"""
    match = re.match(r'^((?P<days>\d+)d)?((?P<hours>\d+)h)?((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?$', duration_str.lower())
    if not match: raise ValueError(f"无效倒计时格式: {duration_str}")
    parts = match.groupdict()
    time_params = {name: int(param) for name, param in parts.items() if param}
    if not time_params: raise ValueError(f"倒计时时长不能为空: {duration_str}")
    return datetime.timedelta(**time_params)

def calculate_initial_trigger_time(
    task_info: TaskInfoCreate,
    holiday_dates_getter: Callable[[int], List[HolidayDateDB]]
) -> Tuple[Optional[datetime.datetime], TaskStatusEnum]:
    """
    计算任务的首次触发本地时间。
    """
    current_local_time = datetime.datetime.now() 

    if task_info.is_recurring: 
        if not task_info.cron_config: raise ValueError("定时任务缺少 cron_config")
        return get_next_cron_run_time( 
            task_info.cron_config,
            base_local_time=current_local_time, 
            holiday_dates_getter=holiday_dates_getter
        )
    else: 
        if task_info.one_time_specific_config: 
            trigger_at_local = task_info.one_time_specific_config.trigger_at 
            if trigger_at_local <= current_local_time:
                return current_local_time + datetime.timedelta(seconds=1), TaskStatusEnum.PENDING
            return trigger_at_local, TaskStatusEnum.PENDING 
        
        elif task_info.countdown_config: 
            try:
                delta = parse_countdown_duration(task_info.countdown_config.countdown_duration) 
                return current_local_time + delta, TaskStatusEnum.PENDING 
            except ValueError: 
                return None, TaskStatusEnum.FAILED 
        else: 
            raise ValueError("一次性任务缺少 one_time_specific_config 或 countdown_config 配置")

def get_next_cron_run_time(
    cron_config: CronConfig,
    base_local_time: datetime.datetime,
    holiday_dates_getter: Callable[[int], List[HolidayDateDB]],
    max_attempts: int = 366 * 2 # 大约两年，对于年度重复的农历任务应该足够
) -> Tuple[Optional[datetime.datetime], TaskStatusEnum]:
    """
    根据 Cron 配置计算下一次有效执行本地时间。
    支持公历和农历，以及基于 `limit_days` 的日期类型过滤。
    """
    start_dt_local = cron_config.start_time 
    if start_dt_local and base_local_time < start_dt_local:
        base_local_time = start_dt_local - datetime.timedelta(seconds=1)

    end_dt_local = cron_config.end_time 
    
    try:
        # 对于农历任务，cron_expression 的日月字段应为 '*'，时间字段正常
        # croniter 用于生成每日的候选时间点
        iter_cron = croniter(cron_config.cron_expression, base_local_time)
    except ValueError as e_croniter: 
        print(f"Cron表达式格式错误: {cron_config.cron_expression} - {e_croniter}")
        return None, TaskStatusEnum.FAILED

    for attempt in range(max_attempts): 
        try:
            next_run_local = iter_cron.get_next(datetime.datetime) 
        except Exception as e: 
            print(f"Croniter 在获取下一个时间点时出错: {e} (表达式: {cron_config.cron_expression}, 尝试次数: {attempt+1})")
            return None, TaskStatusEnum.FAILED

        if end_dt_local and next_run_local > end_dt_local:
            return None, TaskStatusEnum.COMPLETED 

        if start_dt_local and next_run_local < start_dt_local:
            continue 

        target_date_str_local = next_run_local.strftime("%Y-%m-%d")
        needs_calendar_data_for_limit_days = bool(cron_config.limit_days)
        current_day_holiday_info: Optional[HolidayDateDB] = None

        if needs_calendar_data_for_limit_days: 
            year_data_local = holiday_dates_getter(next_run_local.year) 
            if not year_data_local: 
                print(f"警告: 年份 {next_run_local.year} 日历数据缺失 (cron: {cron_config.cron_expression}, 检查日期: {target_date_str_local})。任务将进入待计算状态。")
                return next_run_local, TaskStatusEnum.PENDING_CALCULATION 

            for day_obj in year_data_local:
                if day_obj.date == target_date_str_local:
                    current_day_holiday_info = day_obj
                    break
            if not current_day_holiday_info: 
                print(f"警告: 日期 {target_date_str_local} 详细日历信息缺失。任务将进入待计算状态。")
                return next_run_local, TaskStatusEnum.PENDING_CALCULATION

        # --- 农历日期判断逻辑 (已修正) ---
        if cron_config.is_lunar:
            # 农历任务必须在 cron_config 中提供 lunar_month 和 lunar_day
            if cron_config.lunar_month is None or cron_config.lunar_day is None:
                print(f"错误: 农历任务 (is_lunar=true) 缺少 lunar_month 或 lunar_day 配置。Cron表达式: '{cron_config.cron_expression}'")
                return None, TaskStatusEnum.FAILED 
            try:
                target_lunar_month = int(cron_config.lunar_month)
                target_lunar_day = int(cron_config.lunar_day)
                
                # 将 croniter 生成的公历日期转换为农历日期
                lunar_date_of_next_run = LunarDate.fromSolarDate(next_run_local.year, 
                                                                 next_run_local.month, 
                                                                 next_run_local.day)
                
                # 检查转换后的农历月和日是否与配置中指定的农历月和日匹配
                if not (lunar_date_of_next_run.month == target_lunar_month and \
                        lunar_date_of_next_run.day == target_lunar_day):
                    continue # 如果不匹配，则继续查找下一个由croniter生成的公历日期
            except ValueError: 
                print(f"错误: 农历任务的 lunar_month ('{cron_config.lunar_month}') 或 lunar_day ('{cron_config.lunar_day}') 不是有效的整数。")
                return None, TaskStatusEnum.FAILED
            except Exception as e_lunar: 
                error_message_lower = str(e_lunar).lower()
                if "date" in error_message_lower and ("exist" in error_message_lower or "invalid" in error_message_lower or "range" in error_message_lower):
                    print(f"农历日期相关错误 (公历 {target_date_str_local}, cron='{cron_config.cron_expression}'): {e_lunar} (提示：可能日期不存在或无效)")
                else: 
                    print(f"处理农历日期时发生意外错误 (公历 {target_date_str_local}, cron='{cron_config.cron_expression}'): {e_lunar}")
                continue 
        
        if cron_config.limit_days: 
            if not current_day_holiday_info: 
                print(f"警告: 日期 {target_date_str_local} 详细日历信息缺失，无法应用 limit_days。任务将进入待计算状态。")
                return next_run_local, TaskStatusEnum.PENDING_CALCULATION 

            day_matched_limit = False 
            for limit_type_str in cron_config.limit_days:
                limit_type = limit_type_str.upper() 
                if limit_type == "WORKDAY" and current_day_holiday_info.is_workday(): day_matched_limit = True; break
                if limit_type == "HOLIDAY" and current_day_holiday_info.is_holiday(): day_matched_limit = True; break
                if limit_type == "WEEKEND" and current_day_holiday_info.is_weekend(): day_matched_limit = True; break
                if limit_type == "WEEKDAY_ONLY" and 1 <= next_run_local.isoweekday() <= 5: day_matched_limit = True; break 
            
            if not day_matched_limit: 
                continue 

        return next_run_local, TaskStatusEnum.PENDING

    print(f"在 {max_attempts} 次尝试后，未能为 cron '{cron_config.cron_expression}' (农历: {cron_config.is_lunar}, 月:{cron_config.lunar_month}, 日:{cron_config.lunar_day}) 找到有效的下次执行时间 (基准时间: {base_local_time.isoformat()})。")
    return None, TaskStatusEnum.FAILED