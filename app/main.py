# app/main.py
import asyncio
from fastapi import FastAPI, Depends, HTTPException, status, Header # 导入 Header
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any, Union
from fastapi.responses import JSONResponse

from app import crud, models, schemas
from app.database import init_db, get_db
from app.core.config import settings
from app.services.task_scheduler import scheduler_service_instance
from app.services import holiday_service, nlp_service
from app.utils.date_calculator import calculate_initial_trigger_time
import datetime
import json

# --- API 密钥鉴权依赖 ---
API_KEY_NAME = "X-API-Key" # 标准请求头名称

async def get_api_key(api_key: str = Header(None, alias=API_KEY_NAME)):
    """
    校验 API 密钥。
    如果 settings.SERVER_API_KEY 未配置，则认为鉴权禁用，允许所有请求。
    如果已配置，则客户端必须提供匹配的密钥。
    """
    if not settings.SERVER_API_KEY:
        # 服务器未配置API密钥，鉴权功能关闭，允许请求通过
        # print("调试信息: 服务器未配置SERVER_API_KEY，请求被允许通过（鉴权关闭状态）。")
        return api_key

    # 服务器配置了API密钥，开始校验客户端提供的密钥
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, # 401表示未授权（缺少凭证）
            detail=f"请求头中缺少 API 密钥 '{API_KEY_NAME}'",
        )
    if api_key != settings.SERVER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, # 403表示禁止访问（凭证无效）
            detail="提供的 API 密钥无效",
        )
    return api_key
# --- 结束 API 密钥鉴权依赖 ---

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

@app.on_event("startup")
async def startup_event():
    print("应用启动中 (本地时间模式)...")
    init_db()
    print("正在启动任务调度器...")
    await scheduler_service_instance.start()
    print(f"应用启动完成。当前服务器本地时间: {datetime.datetime.now().isoformat()}")

@app.on_event("shutdown")
async def shutdown_event():
    print("应用关闭中...")
    await scheduler_service_instance.shutdown()
    print("应用已关闭.")

@app.get(f"{settings.API_V1_STR}/health", summary="健康检查", tags=["管理"])
async def health_check():
    return {"status": "ok", "serverTime": datetime.datetime.now().isoformat()}

@app.post(f"{settings.API_V1_STR}/tasks/", response_model=schemas.TaskResponse, status_code=status.HTTP_201_CREATED, summary="创建新提醒任务 (结构化)", tags=["任务管理"], dependencies=[Depends(get_api_key)])
async def create_new_task_structured(task_request: schemas.TaskCreateRequest, db: Session = Depends(get_db)):
    task_info_create = task_request.task_info
    def get_holidays_for_year_local(year: int): return crud.get_holiday_dates_for_year(db, year)
    try:
        initial_trigger_local_time, initial_status = calculate_initial_trigger_time(
            task_info_create, get_holidays_for_year_local
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"任务配置错误: {str(e)}")
    
    if initial_status == models.TaskStatusEnum.FAILED and not initial_trigger_local_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法计算任务触发时间，请检查配置。")

    db_task = crud.create_task(db, task_info_create, initial_trigger_local_time, initial_status)
    
    if db_task.status == models.TaskStatusEnum.PENDING and db_task.next_trigger_time:
        scheduler_service_instance.add_or_update_job_in_scheduler(db_task)
    elif db_task.status == models.TaskStatusEnum.PENDING_CALCULATION:
        print(f"任务 {db_task.id} 创建后状态为 PENDING_CALCULATION。")
    return db_task

# --- 自然语言处理主接口 (支持创建、查询、修改、删除) ---
@app.post(f"{settings.API_V1_STR}/tasks/natural/",
          summary="通过自然语言处理任务（创建、查询、修改、删除）",
          tags=["任务管理"],
          dependencies=[Depends(get_api_key)])
async def process_natural_language_request(
    request: schemas.NaturalLanguageTaskRequest,
    db: Session = Depends(get_db)
):
    if not settings.AI_API_URL or not settings.AI_API_KEY or not settings.AI_MODEL_NAME:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI模型服务未配置，无法通过自然语言处理任务。"
        )

    raw_query = request.query # NLP服务将处理完整的原始查询
    triggering_user_id_from_request = request.user_id # 传递给NLP服务作为参考

    print(f"接收到自然语言请求: '{raw_query}' from user_id: {triggering_user_id_from_request}. 将完整查询发送给NLP服务。")

    # NLP服务现在负责解析包括前缀在内的整个查询，并填充所有必要的字段
    nlp_result = await nlp_service.parse_natural_language_to_task_info(
        query=raw_query, # 发送原始查询
        user_id=triggering_user_id_from_request # NLP可以用此作为默认的triggering_user_id
    )

    if not nlp_result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="NLP服务未能解析请求。请检查输入或NLP服务日志。"
        )

    operation = nlp_result.get("operation")
    print(f"NLP解析操作: {operation}. NLP完整结果: {json.dumps(nlp_result, ensure_ascii=False)}")

    if operation == "CREATE_TASK":
        print(f"NLP解析意图为创建任务。")
        # 从NLP结果中移除不再需要的元数据字段，如果它们仍然被返回的话
        nlp_result.pop("operation", None)
        nlp_result.pop("query_filters", None)
        nlp_result.pop("target_task_identifier", None)
        nlp_result.pop("update_fields", None)
        
        # triggering_user_id, target_chat_id, mention_user_nickname 现在应该由NLP直接填充
        # 并且 webhook_channel 或 email_channel 也应由NLP直接填充

        print(f"NLP为CREATE_TASK返回的数据（准备Pydantic验证）: {json.dumps(nlp_result, ensure_ascii=False)}")

        try:
            task_info_create = schemas.TaskInfoCreate(**nlp_result)
        except Exception as e:
            error_detail = f"NLP服务为CREATE_TASK返回的结构无法通过任务模型验证: {e}. "
            print(f"Pydantic验证失败 (CREATE_TASK)，NLP原始输入数据给Pydantic: {nlp_result}")
            # 详细记录AI返回的与通知渠道相关的字段，帮助调试
            webhook_channel_from_ai = nlp_result.get('webhook_channel')
            email_channel_from_ai = nlp_result.get('email_channel')
            if webhook_channel_from_ai or email_channel_from_ai:
                error_detail += f"AI返回的通知渠道字段: webhook={json.dumps(webhook_channel_from_ai)}, email={json.dumps(email_channel_from_ai)}"
            else:
                error_detail += "AI未提供通知渠道 (webhook_channel 或 email_channel)，或提供的结构不符合schema。"
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)
        
        def get_holidays_local_create(year: int): return crud.get_holiday_dates_for_year(db, year)
        try:
            initial_trigger_local_time, initial_status = calculate_initial_trigger_time(
                task_info_create, get_holidays_local_create
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"CREATE_TASK: 配置在计算触发时间时出错: {str(e)}")
        if initial_status == models.TaskStatusEnum.FAILED and not initial_trigger_local_time:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CREATE_TASK: 无法计算触发时间。")
        
        db_task_created = crud.create_task(db, task_info_create, initial_trigger_local_time, initial_status)
        if db_task_created.status == models.TaskStatusEnum.PENDING and db_task_created.next_trigger_time:
            scheduler_service_instance.add_or_update_job_in_scheduler(db_task_created)
        elif db_task_created.status == models.TaskStatusEnum.PENDING_CALCULATION:
            print(f"任务 {db_task_created.id} (来自NLP) 创建后状态为 PENDING_CALCULATION。")

        # 确保响应模型使用从数据库对象中正确转换的 task_info
        # crud.create_task 内部已经将 task_info 存储为 TaskInfo 的 JSON dump
        # 所以从 db_task_created.task_info 解析回 TaskInfo schema 是合适的
        task_info_model_resp_created = schemas.TaskInfo(**db_task_created.task_info)
        created_task_response_schema = schemas.TaskResponse(
            id=db_task_created.id, task_name=db_task_created.task_name, task_info=task_info_model_resp_created,
            created_at=db_task_created.created_at, status=db_task_created.status,
            next_trigger_time=db_task_created.next_trigger_time, is_recurring=db_task_created.is_recurring
        )
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=created_task_response_schema.model_dump(mode='json'))

    elif operation == "QUERY_TASKS":
        print(f"NLP解析意图为查询任务。")
        query_filters_data = nlp_result.get("query_filters", {})
        status_keyword_from_ai = query_filters_data.get("status")
        keywords_from_ai = query_filters_data.get("keywords")
        
        db_tasks_all = crud.get_tasks(db, skip=0, limit=200) # 考虑分页或更智能的过滤
        filtered_db_tasks = []
        
        # NLP应该返回标准的英文状态值
        target_statuses_to_match_enum = []
        if status_keyword_from_ai:
            try:
                # 尝试直接转换为枚举，如果AI返回的是标准值
                target_statuses_to_match_enum = [models.TaskStatusEnum[status_keyword_from_ai.upper()]]
            except KeyError:
                # 如果AI返回的是中文或其他描述性词语，则使用之前的映射 (尽管理想情况下AI应返回标准值)
                status_mapping_fallback = {
                    "进行中": [models.TaskStatusEnum.PENDING, models.TaskStatusEnum.RUNNING],
                    "待执行": [models.TaskStatusEnum.PENDING],
                }
                if status_keyword_from_ai in status_mapping_fallback:
                     target_statuses_to_match_enum = status_mapping_fallback[status_keyword_from_ai]
                else:
                    print(f"QUERY_TASKS: NLP返回的status '{status_keyword_from_ai}' 无法直接映射到已知状态。")


        for task_db_item in db_tasks_all:
            matches_status = not target_statuses_to_match_enum or task_db_item.status in target_statuses_to_match_enum
            
            matches_keywords = True
            if keywords_from_ai and matches_status: # 仅当状态匹配或没有状态过滤时才检查关键词
                task_info_dict_q = task_db_item.task_info # task_info已经是dict
                description_q = task_info_dict_q.get("description", "").lower()
                reminder_content_q = task_info_dict_q.get("reminder_content", "").lower()
                # 检查任务名、描述、提醒内容是否包含关键词
                if not (keywords_from_ai.lower() in task_db_item.task_name.lower() or \
                        keywords_from_ai.lower() in description_q or \
                        keywords_from_ai.lower() in reminder_content_q):
                    matches_keywords = False
            
            if matches_status and matches_keywords:
                filtered_db_tasks.append(task_db_item)
        
        response_tasks_list_queried = []
        for db_task_item_filtered in filtered_db_tasks:
            # 从数据库的 task_info (已经是dict) 转换为 TaskInfo Pydantic模型
            task_info_model_item_filtered = schemas.TaskInfo(**db_task_item_filtered.task_info)
            response_tasks_list_queried.append(schemas.TaskResponse(
                id=db_task_item_filtered.id, task_name=db_task_item_filtered.task_name,
                task_info=task_info_model_item_filtered,
                created_at=db_task_item_filtered.created_at, status=db_task_item_filtered.status,
                next_trigger_time=db_task_item_filtered.next_trigger_time,
                is_recurring=db_task_item_filtered.is_recurring
            ))
        return JSONResponse(status_code=status.HTTP_200_OK, content=[task.model_dump(mode='json') for task in response_tasks_list_queried])


    elif operation == "UPDATE_TASK":
        print(f"NLP解析意图为更新任务。")
        target_identifier = nlp_result.get("target_task_identifier", {})
        update_fields_from_ai = nlp_result.get("update_fields", {})

        if not target_identifier:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UPDATE_TASK: 未能识别目标任务。")
        if not update_fields_from_ai:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UPDATE_TASK: 未提供要更新的字段信息。")

        task_to_update_db = None
        target_id_from_nlp = target_identifier.get("task_id")
        target_keyword_from_nlp = target_identifier.get("task_name_keyword")

        if target_id_from_nlp:
            task_to_update_db = crud.get_task(db, task_id=target_id_from_nlp)
            if not task_to_update_db:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UPDATE_TASK: 未找到ID为 {target_id_from_nlp} 的任务。")
        elif target_keyword_from_nlp:
            # 此处逻辑与之前类似，通过关键词查找唯一任务
            all_db_tasks_for_update_search = crud.get_tasks(db, limit=1000) # 限制搜索范围
            candidate_tasks_for_update = [
                t for t in all_db_tasks_for_update_search if target_keyword_from_nlp.lower() in t.task_name.lower()
            ]
            if len(candidate_tasks_for_update) == 1:
                task_to_update_db = candidate_tasks_for_update[0]
            elif len(candidate_tasks_for_update) > 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"UPDATE_TASK: 找到多个包含关键词 '{target_keyword_from_nlp}' 的任务，请提供更精确的标识。")
            else:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UPDATE_TASK: 未找到包含关键词 '{target_keyword_from_nlp}' 的任务。")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UPDATE_TASK: 需要提供任务ID或任务名称关键词以识别目标任务。")
        
        if not task_to_update_db: # Should be caught above, but as a final check
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UPDATE_TASK: 最终未能确定要更新的目标任务。")

        # 准备 TaskUpdateRequest 模型
        task_update_request_args = {}
        
        # 检查 update_fields_from_ai 是否包含 TaskInfoBase 的字段
        potential_task_info_updates_from_ai = {}
        for key, value in update_fields_from_ai.items():
            # 检查 key 是否是 TaskInfoBase 模型的一个字段
            if key in schemas.TaskInfoBase.model_fields:
                potential_task_info_updates_from_ai[key] = value
            # 检查 key 是否是 TaskUpdateRequest 允许的顶级字段 (status, next_trigger_time)
            elif key in schemas.TaskUpdateRequest.model_fields:
                 task_update_request_args[key] = value


        if potential_task_info_updates_from_ai:
            # AI提供了TaskInfo相关的更新，需要将其包装在TaskInfoCreate中
            # 注意：这里不能直接用 TaskInfoCreate(**potential_task_info_updates_from_ai)
            # 因为它可能只包含部分字段，而 TaskInfoCreate 可能有必填字段。
            # 我们需要合并现有task_info和AI提供的更新。
            
            current_task_info_dict_for_update = json.loads(json.dumps(task_to_update_db.task_info)) # 深拷贝
            
            # 自定义深层合并，确保嵌套字典如 cron_config 被正确更新而不是替换
            def deep_update_dict_for_main(target_dict, updates_dict):
                for key_ud, val_ud in updates_dict.items():
                    if isinstance(val_ud, dict) and key_ud in target_dict and isinstance(target_dict[key_ud], dict):
                        deep_update_dict_for_main(target_dict[key_ud], val_ud)
                    else:
                        target_dict[key_ud] = val_ud
                return target_dict
            
            merged_task_info_dict_for_schema = deep_update_dict_for_main(current_task_info_dict_for_update, potential_task_info_updates_from_ai)

            # Pydantic 在从字典创建模型时，如果datetime是字符串，它会尝试解析。
            # 确保 task_creation_time (如果存在于合并结果中) 是 datetime 对象或能被Pydantic解析的字符串
            if 'task_creation_time' in merged_task_info_dict_for_schema and \
               isinstance(merged_task_info_dict_for_schema['task_creation_time'], str):
                try:
                    merged_task_info_dict_for_schema['task_creation_time'] = datetime.datetime.fromisoformat(merged_task_info_dict_for_schema['task_creation_time'])
                except ValueError:
                    # 如果转换失败，Pydantic的TaskInfo验证稍后会捕获它
                    pass
            
            # 关键：如果NLP没有提供上下文信息（triggering_user_id等）的更新，
            # 我们需要从旧的task_info中保留它们，因为TaskInfoCreate可能需要它们。
            # NLP应该只在update_fields中包含用户明确想要更改的TaskInfo字段。
            # 如果NLP的update_fields中包含了triggering_user_id等，则使用NLP的。
            # 否则，从原任务中继承。
            for ctx_key in ['triggering_user_id', 'target_chat_id', 'mention_user_nickname']:
                if ctx_key not in merged_task_info_dict_for_schema and task_to_update_db.task_info.get(ctx_key) is not None:
                    merged_task_info_dict_for_schema[ctx_key] = task_to_update_db.task_info.get(ctx_key)
                # 如果 ctx_key 在 potential_task_info_updates_from_ai 中，它已经被 deep_update 合并了

            try:
                # 使用合并后的完整信息尝试创建 TaskInfoCreate 实例
                task_update_request_args['task_info'] = schemas.TaskInfoCreate(**merged_task_info_dict_for_schema)
            except Exception as p_val_error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"UPDATE_TASK: 更新的任务信息内容无效: {p_val_error}. " \
                           f"AI提供用于TaskInfo的更新: {potential_task_info_updates_from_ai}, " \
                           f"合并后尝试创建TaskInfoCreate的数据: {merged_task_info_dict_for_schema}"
                )
        
        # 处理顶层的 status 和 next_trigger_time (如果AI放在了 update_fields 的顶层)
        if "status" in update_fields_from_ai and "status" not in task_update_request_args: # 避免重复赋值
            try:
                # AI应该返回TaskStatusEnum的字符串值
                task_update_request_args['status'] = models.TaskStatusEnum[str(update_fields_from_ai["status"]).upper()]
            except KeyError: # 如果AI返回了无效的状态字符串
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"UPDATE_TASK: 无效的任务状态值 '{update_fields_from_ai['status']}'")

        if "next_trigger_time" in update_fields_from_ai and "next_trigger_time" not in task_update_request_args:
            try:
                # AI应该返回ISO格式的datetime字符串
                task_update_request_args['next_trigger_time'] = datetime.datetime.fromisoformat(str(update_fields_from_ai["next_trigger_time"]))
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"UPDATE_TASK: next_trigger_time格式无效 '{update_fields_from_ai['next_trigger_time']}'")

        if not task_update_request_args: # 如果task_update_request_args为空，说明AI没提供有效更新
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UPDATE_TASK: 未能从NLP结果中解析出有效的更新字段。")
            
        # 创建 TaskUpdateRequest 实例
        try:
            task_update_schema_for_crud = schemas.TaskUpdateRequest(**task_update_request_args)
        except Exception as e_update_req_schema:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"UPDATE_TASK: 构造更新请求时数据验证失败: {e_update_req_schema}. 提供给TaskUpdateRequest的数据: {task_update_request_args}")
        
        # 调用现有的更新函数
        updated_task_response_schema = await update_existing_task(
            task_id=task_to_update_db.id,
            task_update=task_update_schema_for_crud,
            db=db
        )
        # update_existing_task 返回的是DB模型，需要转换为响应模型
        # （或者修改 update_existing_task 返回 TaskResponse）
        # 假设 update_existing_task 返回的是 DB 模型:
        if not isinstance(updated_task_response_schema, schemas.TaskResponse):
             # 重新构造 TaskResponse
            updated_task_info_model = schemas.TaskInfo(**updated_task_response_schema.task_info)
            final_response_obj = schemas.TaskResponse(
                id=updated_task_response_schema.id,
                task_name=updated_task_response_schema.task_name,
                task_info=updated_task_info_model,
                created_at=updated_task_response_schema.created_at,
                status=updated_task_response_schema.status,
                next_trigger_time=updated_task_response_schema.next_trigger_time,
                is_recurring=updated_task_response_schema.is_recurring
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content=final_response_obj.model_dump(mode='json'))
        else: # 如果 update_existing_task 已经返回 TaskResponse
            return JSONResponse(status_code=status.HTTP_200_OK, content=updated_task_response_schema.model_dump(mode='json'))


    elif operation == "DELETE_TASK":
        print(f"NLP解析意图为删除任务。")
        target_identifier_del = nlp_result.get("target_task_identifier", {})
        if not target_identifier_del:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="DELETE_TASK: 未能识别目标任务。")

        task_to_delete_db = None
        target_id_for_delete = target_identifier_del.get("task_id")
        target_keyword_for_delete = target_identifier_del.get("task_name_keyword")

        if target_id_for_delete:
            task_to_delete_db = crud.get_task(db, task_id=target_id_for_delete)
            if not task_to_delete_db:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"DELETE_TASK: 未找到ID为 {target_id_for_delete} 的任务。")
        elif target_keyword_for_delete:
            # 此处逻辑与之前类似
            all_db_tasks_for_delete_search = crud.get_tasks(db, limit=1000)
            candidate_tasks_for_delete = [
                t for t in all_db_tasks_for_delete_search if target_keyword_for_delete.lower() in t.task_name.lower()
            ]
            if len(candidate_tasks_for_delete) == 1:
                task_to_delete_db = candidate_tasks_for_delete[0]
            elif len(candidate_tasks_for_delete) > 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"DELETE_TASK: 找到多个包含关键词 '{target_keyword_for_delete}' 的任务，请提供更精确的标识。")
            else:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"DELETE_TASK: 未找到包含关键词 '{target_keyword_for_delete}' 的任务。")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="DELETE_TASK: 需要提供任务ID或任务名称关键词以识别目标任务。")

        if not task_to_delete_db: # Should be caught
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DELETE_TASK: 最终未能确定要删除的目标任务。")

        await delete_existing_task(task_id=task_to_delete_db.id, db=db) # delete_existing_task 返回 204，所以这里直接返回消息
        return JSONResponse(status_code=status.HTTP_200_OK, content={"message": f"任务 '{task_to_delete_db.task_name}' (ID: {task_to_delete_db.id}) 已成功删除。"})

    else:
        error_msg = f"NLP未能识别明确的操作意图或返回了无法处理的操作: '{operation}'."
        if not operation:
            error_msg = "NLP响应中缺少必要的操作意图字段 ('operation')."
        print(f"{error_msg} 原始查询: '{raw_query}'. AI解析详情: {json.dumps(nlp_result, ensure_ascii=False)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg
        )

@app.get(f"{settings.API_V1_STR}/tasks/", response_model=List[schemas.TaskResponse], summary="查询任务列表 (结构化)", tags=["任务管理"], dependencies=[Depends(get_api_key)])
async def read_tasks(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    db_tasks = crud.get_tasks(db, skip=skip, limit=limit)
    # 将数据库模型列表转换为响应模型列表
    response_tasks = []
    for db_task in db_tasks:
        task_info_model = schemas.TaskInfo(**db_task.task_info) # task_info in db is dict
        response_tasks.append(schemas.TaskResponse(
            id=db_task.id,
            task_name=db_task.task_name,
            task_info=task_info_model,
            created_at=db_task.created_at,
            status=db_task.status,
            next_trigger_time=db_task.next_trigger_time,
            is_recurring=db_task.is_recurring
        ))
    return response_tasks


@app.get(f"{settings.API_V1_STR}/tasks/{{task_id}}", response_model=schemas.TaskResponse, summary="查询指定任务 (结构化)", tags=["任务管理"], dependencies=[Depends(get_api_key)])
async def read_task(task_id: str, db: Session = Depends(get_db)):
    db_task = crud.get_task(db, task_id=task_id)
    if db_task is None: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到")
    
    task_info_model = schemas.TaskInfo(**db_task.task_info)
    return schemas.TaskResponse(
        id=db_task.id,
        task_name=db_task.task_name,
        task_info=task_info_model,
        created_at=db_task.created_at,
        status=db_task.status,
        next_trigger_time=db_task.next_trigger_time,
        is_recurring=db_task.is_recurring
    )


@app.put(f"{settings.API_V1_STR}/tasks/{{task_id}}", response_model=schemas.TaskResponse, summary="编辑任务 (结构化)", tags=["任务管理"], dependencies=[Depends(get_api_key)])
async def update_existing_task(task_id: str, task_update: schemas.TaskUpdateRequest, db: Session = Depends(get_db)):
    db_task_before_update = crud.get_task(db, task_id)
    if not db_task_before_update: 
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到")

    # 准备传递给 crud.update_task 的数据
    update_data_for_crud_layer = task_update.model_dump(exclude_unset=True)
    
    recalculate_trigger = False
    new_next_trigger_local_time_val = db_task_before_update.next_trigger_time
    new_status_val = db_task_before_update.status

    if task_update.task_info is not None:
        recalculate_trigger = True # 如果task_info有任何变动，则需要重新计算时间
        # 合并 task_info: 将传入的 task_update.task_info (部分更新) 与现有的 task_info 合并
        current_task_info_dict = json.loads(json.dumps(db_task_before_update.task_info)) # 确保是可修改的字典
        
        # TaskInfoCreate.model_dump(exclude_unset=True) 确保只获取用户提供的字段
        provided_task_info_updates_dict = task_update.task_info.model_dump(exclude_unset=True)

        # 深层合并逻辑
        def deep_update_internal(target, updates):
            for key, value in updates.items():
                if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                    deep_update_internal(target[key], value)
                else:
                    target[key] = value
            return target

        merged_task_info_dict = deep_update_internal(current_task_info_dict, provided_task_info_updates_dict)
        
        # 如果 task_creation_time 是字符串，尝试转换为 datetime
        if 'task_creation_time' in merged_task_info_dict and isinstance(merged_task_info_dict['task_creation_time'], str):
            try:
                merged_task_info_dict['task_creation_time'] = datetime.datetime.fromisoformat(merged_task_info_dict['task_creation_time'])
            except ValueError:
                 pass # Pydantic 验证会处理

        # 确保上下文信息 (triggering_user_id等) 被保留或更新
        # 如果 TaskInfoCreate schema 中的这些字段是可选的，并且用户没有在 task_update.task_info 中提供它们，
        # 它们将从 merged_task_info_dict (即 current_task_info_dict 的更新版本) 中继承。
        # 如果 TaskInfoCreate 要求它们，则用户必须在 task_update.task_info 中提供。
        # schemas.TaskInfoBase 将这些定义为 Optional，所以这里应该没问题。
        
        try:
            # 使用合并后的数据验证并准备 TaskInfoCreate 对象，这将用于 calculate_initial_trigger_time
            temp_task_info_for_recalc = schemas.TaskInfoCreate(**merged_task_info_dict)
        except Exception as e_pydantic_merge:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"更新的任务信息内容在合并或验证时出错: {e_pydantic_merge}. 合并后数据: {merged_task_info_dict}")

        # 将合并后的 task_info (仍然是dict) 放入 crud 更新数据中
        update_data_for_crud_layer['task_info'] = merged_task_info_dict 

        # --- 重新计算触发时间和状态 ---
        def get_holidays_for_year_local_update(year: int): return crud.get_holiday_dates_for_year(db, year)
        try:
            trigger_time_after_update, status_after_update = calculate_initial_trigger_time(
                temp_task_info_for_recalc, get_holidays_for_year_local_update
            )
            new_next_trigger_local_time_val = trigger_time_after_update
            new_status_val = status_after_update
        except ValueError as e_calc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"更新后的任务配置导致计算触发时间错误: {str(e_calc)}")

    # 如果顶层 status 或 next_trigger_time 被直接更新 (且 task_info 未更新或未导致重新计算)
    if task_update.status is not None and not recalculate_trigger:
        new_status_val = task_update.status
        recalculate_trigger = True # 标记需要更新调度器
    
    if task_update.next_trigger_time is not None and not task_update.task_info : # 仅当task_info未提供时，才单独考虑顶层next_trigger_time
        new_next_trigger_local_time_val = task_update.next_trigger_time
        recalculate_trigger = True # 标记需要更新调度器
        # 如果next_trigger_time被直接设置，通常也意味着状态应为PENDING，除非另有指定
        if task_update.status is None and new_status_val != models.TaskStatusEnum.PENDING_CALCULATION:
             new_status_val = models.TaskStatusEnum.PENDING

    # 将计算或直接设置的 next_trigger_time 和 status 更新到 crud 数据中
    update_data_for_crud_layer['next_trigger_time'] = new_next_trigger_local_time_val
    update_data_for_crud_layer['status'] = new_status_val
    
    # 使用最终构造的 update_data_for_crud_layer (已经是字典) 来创建 TaskUpdateRequest
    # 这是因为 crud.update_task 期望的是 TaskUpdateRequest schema 对象
    final_task_update_schema = schemas.TaskUpdateRequest(**update_data_for_crud_layer)

    updated_db_task = crud.update_task(db, task_id, final_task_update_schema)
    if not updated_db_task:
        # 这理论上不应发生，因为前面已经检查过任务是否存在
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务在更新过程中未找到或失败。")

    # --- 更新调度器 ---
    if recalculate_trigger or task_update.status is not None or (task_update.next_trigger_time is not None and not task_update.task_info):
        if updated_db_task.status == models.TaskStatusEnum.PENDING and updated_db_task.next_trigger_time:
            scheduler_service_instance.add_or_update_job_in_scheduler(updated_db_task)
        elif updated_db_task.status == models.TaskStatusEnum.PENDING_CALCULATION:
            # 对于 PENDING_CALCULATION，我们通常会移除现有作业，让每日维护任务来处理
            scheduler_service_instance.remove_job_from_scheduler(updated_db_task.id)
            print(f"任务 {updated_db_task.id} 更新后状态为 PENDING_CALCULATION，已从调度器移除，等待每日维护。")
        else: # COMPLETED, FAILED, etc.
            scheduler_service_instance.remove_job_from_scheduler(updated_db_task.id)
    
    # 构造响应
    updated_task_info_model = schemas.TaskInfo(**updated_db_task.task_info)
    return schemas.TaskResponse(
        id=updated_db_task.id,
        task_name=updated_db_task.task_name,
        task_info=updated_task_info_model,
        created_at=updated_db_task.created_at,
        status=updated_db_task.status,
        next_trigger_time=updated_db_task.next_trigger_time,
        is_recurring=updated_db_task.is_recurring
    )


@app.delete(f"{settings.API_V1_STR}/tasks/{{task_id}}", status_code=status.HTTP_204_NO_CONTENT, summary="删除任务 (结构化)", tags=["任务管理"], dependencies=[Depends(get_api_key)])
async def delete_existing_task(task_id: str, db: Session = Depends(get_db)):
    task = crud.get_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务未找到，无法删除")

    # 从数据库删除
    delete_success = crud.delete_task(db, task_id=task_id) # crud.delete_task 返回 bool
    if not delete_success:
        # 理论上，如果上面get_task成功，这里不应失败，除非并发删除
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="从数据库删除任务失败。")

    # 从调度器移除
    scheduler_service_instance.remove_job_from_scheduler(task_id)
    return None # FastAPI 会自动处理 204 响应体

@app.post(f"{settings.API_V1_STR}/admin/update-calendar/{{year}}", summary="手动更新指定年份日历数据", tags=["管理"], dependencies=[Depends(get_api_key)])
async def trigger_calendar_update(year: int, force: bool = False, db: Session = Depends(get_db)):
    if not (2000 <= year <= datetime.datetime.now().year + 5): # 调整年份范围检查
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="年份无效，应在2000到当前年份+5之间")
    success = await holiday_service.update_calendar_data_for_year(db, year, force_update=force)
    if success: return {"message": f"年份 {year} 日历数据更新请求已处理。强制更新: {force}"}
    # 如果 update_calendar_data_for_year 返回 False，可能是API调用失败或数据库存储失败
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"更新年份 {year} 日历数据失败。请检查服务日志。")

@app.post(f"{settings.API_V1_STR}/admin/trigger-daily-maintenance", summary="手动触发每日维护任务", tags=["管理"], dependencies=[Depends(get_api_key)])
async def manual_trigger_daily_maintenance():
    print("手动触发每日维护任务...")
    # 确保异步执行，不阻塞当前请求
    asyncio.create_task(scheduler_service_instance.daily_maintenance_job())
    return {"message": "每日维护任务已异步触发。请查看服务日志了解执行情况。"}