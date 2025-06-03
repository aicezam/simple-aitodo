# app/services/task_executor.py
from sqlalchemy.orm import Session
import datetime 

from app import models, schemas, crud
from app.core.config import settings 
from app.services import dify_client, notification_service 
from app.utils.date_calculator import get_next_cron_run_time
from app.database import SessionLocal

async def execute_task_by_id(task_id: str, scheduler_instance: 'app.services.task_scheduler.TaskSchedulerService'):
    """根据任务ID执行任务（发送通知，并为周期性任务重新调度）。"""
    db: Session = SessionLocal() 
    task: Optional[models.ReminderTaskDB] = None
    try:
        task = crud.get_task(db, task_id) 
        if not task:
            print(f"任务执行失败：未找到任务ID {task_id}")
            scheduler_instance.remove_job_from_scheduler(task_id) 
            return

        current_local_time_iso = datetime.datetime.now().isoformat()
        print(f"[{current_local_time_iso}] 开始执行任务: {task.task_name} (ID: {task_id}, Status: {task.status.value})")
        
        if task.status not in [models.TaskStatusEnum.PENDING, models.TaskStatusEnum.RUNNING]:
            print(f"任务 {task_id} 状态为 {task.status.value}，非可执行状态，跳过。")
            if not task.next_trigger_time: 
                scheduler_instance.remove_job_from_scheduler(task_id)
            return

        task.status = models.TaskStatusEnum.RUNNING
        db.commit()
        
        task_info_model = schemas.TaskInfo(**task.task_info) 
        base_reminder_content = "" # 这是不包含@信息的纯粹提醒内容

        if task_info_model.is_dify_generated:
            print(f"任务 {task_id} 通过 Dify 生成内容。提示: {task_info_model.reminder_content[:30]}...")
            if settings.DIFY_API_KEY and settings.DIFY_BASE_URL: 
                dify_user_id_for_generation = task_info_model.triggering_user_id or f"task_executor_{task_id}"
                generated_content = await dify_client.generate_content_with_dify(
                    prompt=task_info_model.reminder_content, 
                    user_id=dify_user_id_for_generation 
                )
                if generated_content and not generated_content.startswith("Dify"): 
                    base_reminder_content = generated_content
                else: 
                    base_reminder_content = f"Dify内容生成失败({generated_content[:50]}...). 原始提示: {task_info_model.reminder_content}"
            else: 
                base_reminder_content = f"Dify未配置。原始提示: {task_info_model.reminder_content}"
        else: 
            base_reminder_content = task_info_model.reminder_content

        notification_sent_successfully = False
        
        # 从 task_info_model 中获取上下文信息用于通知
        task_triggering_user_id = task_info_model.triggering_user_id
        task_target_chat_id = task_info_model.target_chat_id
        task_mention_nickname = task_info_model.mention_user_nickname
        
        # 确定最终的接收者ID (ToUserName)
        # 如果 target_chat_id (群ID或个人ID) 存在，则用它；否则，回退到 triggering_user_id (兼容旧数据或私聊)
        final_recipient_id = task_target_chat_id if task_target_chat_id else task_triggering_user_id
        
        if not final_recipient_id:
            print(f"错误: 任务 {task.id} ({task.task_name}) 无法确定通知的最终接收者ID。")
            task.status = models.TaskStatusEnum.FAILED
            db.commit()
            if not task.is_recurring: scheduler_instance.remove_job_from_scheduler(task_id)
            return

        # 判断是否为群聊场景，并确定是否需要@以及@谁
        is_group_message_context = False
        at_user_id_for_payload: Optional[str] = None # 用于 AtWxIDList
        
        if task_target_chat_id and "@chatroom" in task_target_chat_id: # 简单判断是否为群聊ID
            is_group_message_context = True
            at_user_id_for_payload = task_triggering_user_id # 在群里@发起任务的人

        if task_info_model.webhook_channel:
            notification_sent_successfully = await notification_service.send_webhook_notification(
                config=task_info_model.webhook_channel, 
                base_content=base_reminder_content, # 传递纯净的提醒内容
                final_recipient_id=final_recipient_id, # 明确传递最终接收者
                triggering_user_id=task_triggering_user_id, # 原始触发用户ID
                mention_nickname_if_group=task_mention_nickname if is_group_message_context else None, # 如果是群聊且有昵称则传递
                at_target_user_id_if_group=at_user_id_for_payload if is_group_message_context else None, # 如果是群聊则传递被@者ID
                task_name=task_info_model.task_name,   
                task_description=task_info_model.description 
            )
        elif task_info_model.email_channel:
            if all([settings.MAIL_SERVER, settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_SENDER]):
                # 邮件通知通常直接发送给配置中的 recipient_email，但 base_reminder_content 可能也需要上下文
                # 如果邮件模板也需要 user_id, task_name 等，需类似地传递
                email_content_to_send = base_reminder_content # 简单示例
                if is_group_message_context and task_mention_nickname: # 邮件内容中也可以加入昵称信息
                    email_content_to_send = f"(来自群聊 {task_target_chat_id} 中 {task_mention_nickname} 的提醒)\n{base_reminder_content}"
                elif is_group_message_context and task_triggering_user_id:
                    email_content_to_send = f"(来自群聊 {task_target_chat_id} 中用户 {task_triggering_user_id} 的提醒)\n{base_reminder_content}"

                notification_sent_successfully = await notification_service.send_email_notification(
                    config=task_info_model.email_channel, 
                    content=email_content_to_send
                )
            else:
                print(f"任务 {task_id} 的邮件渠道配置不完整，无法发送。")
        else: 
            print(f"任务 {task_id} 未配置有效的通知渠道。")
            task.status = models.TaskStatusEnum.FAILED 
            db.commit()
            if not task.is_recurring:
                scheduler_instance.remove_job_from_scheduler(task_id)
            return 

        task.status = models.TaskStatusEnum.COMPLETED if notification_sent_successfully else models.TaskStatusEnum.FAILED
        db.commit()

        if task.is_recurring and task_info_model.cron_config:
            base_for_next_calc_local = datetime.datetime.now() 
            def get_holidays_for_year_local_exec(year_val: int): return crud.get_holiday_dates_for_year(db, year_val)

            next_trigger_local, next_status = get_next_cron_run_time(
                cron_config=task_info_model.cron_config,
                base_local_time=base_for_next_calc_local, 
                holiday_dates_getter=get_holidays_for_year_local_exec
            )
            task.next_trigger_time = next_trigger_local 
            task.status = next_status 
            db.commit()
            db.refresh(task) 

            if task.status == models.TaskStatusEnum.PENDING and task.next_trigger_time:
                scheduler_instance.add_or_update_job_in_scheduler(task)
                print(f"任务 {task_id} 已重新调度，下次本地执行: {task.next_trigger_time.isoformat() if task.next_trigger_time else 'N/A'}")
            elif task.status == models.TaskStatusEnum.PENDING_CALCULATION and task.next_trigger_time:
                scheduler_instance.remove_job_from_scheduler(task.id)
                print(f"任务 {task_id} 下次执行时间待精确计算 (当前估算本地时间: {task.next_trigger_time.isoformat() if task.next_trigger_time else 'N/A'})。将由每日维护任务处理。")
            else: 
                scheduler_instance.remove_job_from_scheduler(task_id)
                print(f"任务 {task_id} 无更多执行计划或计算失败，已从调度器移除。最终状态: {task.status.value}")
        else: 
            task.next_trigger_time = None 
            db.commit()
            scheduler_instance.remove_job_from_scheduler(task_id)
            print(f"一次性任务 {task_id} 执行完毕，已从调度器移除。状态: {task.status.value}")
        
        print(f"[{datetime.datetime.now().isoformat()}] 任务 {task_id} 处理完毕。")

    except Exception as e: 
        print(f"执行任务 {task_id} 时发生严重错误: {e}")
        import traceback; traceback.print_exc()
        if task: 
            task.status = models.TaskStatusEnum.FAILED
            db.commit()
        scheduler_instance.remove_job_from_scheduler(task_id) 
    finally:
        db.close()