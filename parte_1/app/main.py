import json
from datetime import datetime
from enum import Enum
from typing import Optional, List, Union

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, create_model
from pymemcache.client.base import Client
from starlette.responses import RedirectResponse

description = """
Ephemeral TODO List, save your todo task as long as the server lives.

## List

You will be able to:

* **Create list** (_not implemented_).
* **Add tasks to existing list** (_not implemented_).
* **Consult list's tasks** (_not implemented_).

## Task

You will be able to:

* **Create task** (_not implemented_).
* **Read task** (_not implemented_).
"""

MEMCACHED_IP = '127.0.0.1'


# ---------------------------------------------------------
# Data Classes
# ---------------------------------------------------------

class TaskStatus(str, Enum):
    assigned = 'Assigned'
    in_process = 'In Process'
    pending = 'Pending'
    closed = 'Closed'
    canceled = 'Canceled'


class UpdatedTask(BaseModel):
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    due_date: Optional[str] = None


class Task(BaseModel):
    name: str
    description: str
    status: TaskStatus = TaskStatus.assigned
    due_date: str = None


class TaskInDB(Task):
    assigned_list: str
    creation_date: str = Field(default_factory=lambda: datetime.now().strftime("%d-%b-%Y (%H:%M:%S)"))


class TaskList(BaseModel):
    name: str
    description: Optional[str] = None


class TaskListInDB(TaskList):
    creation_date: str = Field(default_factory=lambda: datetime.now().strftime("%d-%b-%Y (%H:%M:%S)"))
    tasks: List[Union[str, TaskInDB]] = Field(default_factory=list)


# ---------------------------------------------------------
# Memcached
# ---------------------------------------------------------

memcached_db = Client(MEMCACHED_IP)


def delete_tasks(list_id: str, tasks: List[str]):
    for task in tasks:
        memcached_db.delete(f'task-key_{list_id}_{task}')


# ---------------------------------------------------------
# Server
# ---------------------------------------------------------

app = FastAPI(
    title="Ephemeral TODO List",
    description=description,
    version="0.0.1",
    contact={
        "name": "Iker de la Iglesia Martínez",
        "email": "idelaiglesia004@ikasle.ehu.eus",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)


@app.on_event("startup")
async def startup():
    global memcached_db
    memcached_db = Client(MEMCACHED_IP)


@app.on_event("shutdown")
async def shutdown():
    memcached_db.close()


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url='/docs')


# Lists
# ---------------------------------------------------------

@app.post("/todo_lists/", response_model=TaskListInDB, status_code=status.HTTP_201_CREATED, tags=["Lists"])
async def create_list(data: TaskList):
    if memcached_db.get(f'task-list-key_{data.name}'):
        raise HTTPException(status_code=403, detail=f"There's already a list with name {data.name}")
    data = TaskListInDB(**dict(data))
    memcached_db.set(f'task-list-key_{data.name}', data.json())
    return data


@app.get("/todo_lists/{list_name}", response_model=TaskListInDB, status_code=status.HTTP_202_ACCEPTED, tags=["Lists"])
async def get_list(list_name: str, get_task_data: Optional[bool] = False):
    if not (list_data := memcached_db.get(f'task-list-key_{list_name}')):
        raise HTTPException(status_code=404, detail=f"List {list_name} not found")
    list_data = TaskListInDB(**json.loads(list_data))
    if get_task_data:
        for task_indx, task_name in enumerate(list_data.tasks):
            if task_data := memcached_db.get(f'task-key_{list_name}_{task_name}'):
                list_data.tasks[task_indx] = TaskInDB(**json.loads(task_data))
    return list_data


@app.delete("/todo_lists/{list_name}",
            response_model=create_model('DeleteListResponse', message=(str, ...), list_name=(str, ...), deleted_tasks=(str, ...)),
            status_code=status.HTTP_200_OK,
            tags=["Lists"])
async def delete_list(list_name: str):
    # Check if list exists and get list data
    if not (list_data := memcached_db.get(f'task-list-key_{list_name}')):
        raise HTTPException(status_code=404, detail=f"List {list_name} not found")

    # Get related tasks and delete them
    related_tasks = TaskListInDB(**json.loads(list_data)).tasks
    delete_tasks(list_name, related_tasks)

    # Delete the list
    memcached_db.delete(f'task-list-key_{list_name}')

    # Inform
    return {'message': f'{list_name} deleted successfully.',
            'list_name': list_name,
            'deleted_tasks': related_tasks}


# Tasks
# ---------------------------------------------------------

@app.post("/todo_lists/{list_name}", response_model=TaskInDB, status_code=status.HTTP_201_CREATED, tags=["Tasks"])
async def add_task(list_name: str, task_data: Task):
    if memcached_db.get(f'task-key_{list_name}_{task_data.name}'):
        raise HTTPException(status_code=403,
                            detail=f"There's already a task with name {task_data.name} on list {list_name}.\n"
                                   f"Use PUT method instead to edit task data.")
    task_data = TaskInDB(assigned_list=list_name, **dict(task_data))
    memcached_db.set(f'task-key_{list_name}_{task_data.name}', task_data.json())
    return task_data


@app.put("/todo_lists/{list_name}/{task_id}", response_model=TaskInDB, status_code=status.HTTP_200_OK, tags=["Tasks"])
async def edit_task(list_name: str, task_id: str, updated_task_data: UpdatedTask):
    # Check if task exists
    if not (task_data := memcached_db.get(f'task-key_{list_name}_{task_id}')):
        raise HTTPException(status_code=404, detail=f"Task {task_id} on list {list_name} not found.")

    # Get original data and updated values
    task_data = json.loads(task_data)
    updated_task_data = updated_task_data.dict(exclude_unset=True)

    # Update data
    task_data.update(updated_task_data)
    task_data = TaskInDB(**task_data)  # Convert to model
    memcached_db.set(f'task-key_{list_name}_{task_id}', task_data.json())
    return task_data


@app.get("/todo_lists/{list_name}/{task_id}", response_model=TaskInDB, status_code=status.HTTP_200_OK, tags=["Tasks"])
async def get_task(list_name: str, task_id: str):
    if not (task_data := memcached_db.get(f'task-key_{list_name}_{task_id}')):
        raise HTTPException(status_code=404, detail=f"Task {task_id} on list {list_name} not found.")
    return TaskInDB(**task_data)


@app.delete("/todo_lists/{list_name}/{task_id}",
            response_model=create_model('DeleteTaskResponse', message=(str, ...), task_name=(str, ...), list_name=(str, ...)),
            status_code=status.HTTP_200_OK,
            tags=["Tasks"])
async def delete_task(list_name: str, task_id: str):
    # Delete and check if task exists
    if not memcached_db.delete(f'task-key_{list_name}_{task_id}', noreply=False):
        raise HTTPException(status_code=404, detail=f"Task {task_id} on list {list_name} not found.")

    # Inform
    return {'message': f'Task {task_id} on list {list_name} deleted successfully.',
            'task_name': task_id,
            'list_name': list_name}
