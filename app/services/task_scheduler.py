# app/services/task_scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import datetime # 标准库

from app.core.config import settings
from app import crud, models, schemas
from app.database import SessionLocal
from app.services import holiday_service, task_executor # 确保 task_executor 导入
from app.utils.date_calculator import get_next_cron_run_time # now_utc 等被移除

class TaskSchedulerService:
    _scheduler: AsyncIOScheduler = None

    def __init__(self):
        if TaskSchedulerService._scheduler is None:
            # 不指定 timezone，APScheduler 默认使用系统本地时区
            TaskSchedulerService._scheduler = AsyncIOScheduler()
            print(f"APScheduler 已使用系统本地时区初始化。")

    async def start(self):
        if not self._scheduler.running:
            db = SessionLocal()
            try:
                print("调度器启动：检查并更新日历数据...")
                await holiday_service.ensure_calendar_data_exists(db, force=False)
                
                print("调度器启动：加载数据库中的任务...")
                tasks_to_schedule = db.query(models.ReminderTaskDB).filter(
                    models.ReminderTaskDB.status == models.TaskStatusEnum.PENDING,
                    models.ReminderTaskDB.next_trigger_time.isnot(None) # type: ignore
                ).all()
                print(f"发现 {len(tasks_to_schedule)} 个待调度 (PENDING) 任务。")
                for task in tasks_to_schedule:
                    self.add_or_update_job_in_scheduler(task)
                
                pending_calc_tasks = db.query(models.ReminderTaskDB).filter(
                    models.ReminderTaskDB.status == models.TaskStatusEnum.PENDING_CALCULATION,
                ).count()
                if pending_calc_tasks > 0:
                    print(f"发现 {pending_calc_tasks} 个 PENDING_CALCULATION 任务，将由每日维护任务处理。")

                self._scheduler.add_job(
                    self.daily_maintenance_job,
                    trigger='cron', hour=1, minute=10, # 每日本地时间 1:10
                    id='daily_maintenance_job', replace_existing=True, misfire_grace_time=3600
                )
                print("每日维护任务已添加。")
                self._scheduler.start()
                print("任务调度器已启动。")
            finally:
                db.close()

    async def shutdown(self):
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            print("任务调度器已关闭。")

    def add_or_update_job_in_scheduler(self, task: models.ReminderTaskDB):
        if not task.next_trigger_time: # naive local time
            print(f"任务 {task.id} ({task.task_name}) 无下次执行时间，无法调度。")
            return
        
        trigger_local_time = task.next_trigger_time # 这是 naive local time
        
        if trigger_local_time <= datetime.datetime.now(): # 比较 naive local times
            print(f"任务 {task.id} 下次执行本地时间 {trigger_local_time.isoformat()} 已过 (当前 {datetime.datetime.now().isoformat()})。将尝试立即执行。")

        job_id = str(task.id)
        try:
            self._scheduler.add_job(
                func=task_executor.execute_task_by_id,
                trigger=DateTrigger(run_date=trigger_local_time), # APScheduler 将此 naive time 解释为本地时间
                args=[job_id, self], id=job_id, name=task.task_name,
                replace_existing=True,
                misfire_grace_time=task.is_recurring and 3600 or 600
            )
            print(f"任务 {job_id} ({task.task_name}) 已添加/更新到调度器，执行本地时间: {trigger_local_time.isoformat()}")
        except Exception as e:
            print(f"添加任务 {job_id} 到调度器失败: {e}")

    def remove_job_from_scheduler(self, task_id: str):
        job_id = str(task_id)
        try:
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)
                print(f"任务 {job_id} 已从调度器中移除。")
        except Exception as e:
            print(f"从调度器移除任务 {job_id} 失败: {e}")
    
    async def daily_maintenance_job(self):
        current_local_time_str = datetime.datetime.now().isoformat()
        print(f"[{current_local_time_str}] 开始执行每日维护任务...")
        db = SessionLocal()
        try:
            current_year = datetime.datetime.now().year
            await holiday_service.ensure_calendar_data_exists(db, current_year + 1, force=False)

            print("检查 PENDING_CALCULATION 状态的任务...")
            tasks_to_recalculate = crud.get_tasks_for_recalculation(db)
            print(f"发现 {len(tasks_to_recalculate)} 个待重新计算的任务。")
            
            for task_db in tasks_to_recalculate:
                print(f"重新计算任务 {task_db.id} ({task_db.task_name})...")
                try:
                    task_info_model = schemas.TaskInfo(**task_db.task_info)
                    if task_info_model.is_recurring and task_info_model.cron_config:
                        def get_holidays_for_year_local(year_val: int): return crud.get_holiday_dates_for_year(db, year_val)
                        
                        base_for_recalc_local = task_db.next_trigger_time or datetime.datetime.now()
                        
                        next_trigger_local, next_status = get_next_cron_run_time(
                            cron_config=task_info_model.cron_config,
                            base_local_time=base_for_recalc_local - datetime.timedelta(minutes=1),
                            holiday_dates_getter=get_holidays_for_year_local
                        )

                        if next_trigger_local and next_status == models.TaskStatusEnum.PENDING:
                            print(f"任务 {task_db.id} 重新计算成功，下次本地执行: {next_trigger_local.isoformat()}, 状态: PENDING")
                            task_db.next_trigger_time = next_trigger_local
                            task_db.status = next_status
                            db.commit()
                            self.add_or_update_job_in_scheduler(task_db)
                        elif next_trigger_local and next_status == models.TaskStatusEnum.PENDING_CALCULATION:
                            print(f"任务 {task_db.id} 仍为 PENDING_CALCULATION。下次尝试本地时间点: {next_trigger_local.isoformat()}")
                            task_db.next_trigger_time = next_trigger_local
                            db.commit()
                        else:
                             print(f"任务 {task_db.id} 重新计算后无下次执行或失败，状态: {next_status.value}")
                             task_db.status = next_status; task_db.next_trigger_time = None
                             db.commit()
                             self.remove_job_from_scheduler(task_db.id)
                    else:
                        print(f"任务 {task_db.id} 非周期性但为 PENDING_CALCULATION，标记为 FAILED。")
                        task_db.status = models.TaskStatusEnum.FAILED; task_db.next_trigger_time = None
                        db.commit()
                        self.remove_job_from_scheduler(task_db.id)
                except Exception as e_recalc:
                    print(f"重新计算任务 {task_db.id} 时发生错误: {e_recalc}")
                    task_db.status = models.TaskStatusEnum.FAILED; db.commit()
                    self.remove_job_from_scheduler(task_db.id)
            
            print(f"[{datetime.datetime.now().isoformat()}] 每日维护任务执行完毕。")
        except Exception as e_daily:
            print(f"每日维护任务执行过程中发生严重错误: {e_daily}")
        finally:
            db.close()

scheduler_service_instance = TaskSchedulerService()