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

* **Create list:** Define a list with a new unique name and an optional description..
* **Consult list's info and tasks:** Get your list data and assigned tasks. You can choose between getting a list of task names or a list with each task's data.
* **Delete a list:** Delete an existing list and every assigned task to that list.

## Task

You will be able to:

* **Create task in an existing list:** (_not implemented_).
* **Get task data:** (_not implemented_).
* **Edit task data:** (_not implemented_).
* **Delete a task:** (_not implemented_).
"""

# ---------------------------------------------------------
# Memcached
# ---------------------------------------------------------
MEMCACHED_IP = ('memcached', 11211)

memcached_db = Client(MEMCACHED_IP)


def delete_tasks(list_id: str, tasks: List[str]):
    for task in tasks:
        memcached_db.delete(f'task-key_{list_id}_{task}')


# ---------------------------------------------------------
# API Aplication
# ---------------------------------------------------------

app = FastAPI(
    title="Ephemeral TODO List",
    description=description,
    version="2.0.1",
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
        raise HTTPException(status_code=409, detail=f"There's already a list with name {data.name}")
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
            response_model=create_model('DeleteListResponse', message=(str, ...), list_name=(str, ...), deleted_tasks=(List[str], ...)),
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
    # Check if list exists
    if not (list_data := memcached_db.get(f'task-list-key_{list_name}')):
        raise HTTPException(status_code=404, detail=f"List {list_name} not found")

    # Check if task already exists
    if memcached_db.get(f'task-key_{list_name}_{task_data.name}'):
        raise HTTPException(status_code=409,
                            detail=f"There's already a task with name {task_data.name} on list {list_name}.\n"
                                   f"Use PUT method instead to edit task data.")

    # Generate task
    task_data = TaskInDB(assigned_list=list_name, **dict(task_data))
    memcached_db.set(f'task-key_{list_name}_{task_data.name}', task_data.json())

    # Add task to list data and uodate
    list_data = TaskListInDB(**json.loads(list_data))
    list_data.tasks.append(task_data.name)
    memcached_db.set(f'task-list-key_{list_name}', list_data.json())

    return task_data


@app.put("/todo_lists/{list_name}/{task_name}", response_model=TaskInDB, status_code=status.HTTP_200_OK, tags=["Tasks"])
async def edit_task(list_name: str, task_name: str, updated_task_data: UpdatedTask):
    # Check if task exists
    if not (task_data := memcached_db.get(f'task-key_{list_name}_{task_name}')):
        raise HTTPException(status_code=404, detail=f"Task {task_name} on list {list_name} not found.")

    # Get original data and updated values
    task_data = json.loads(task_data)
    updated_task_data = updated_task_data.dict(exclude_unset=True)

    # Update data
    task_data.update(updated_task_data)
    task_data = TaskInDB(**task_data)  # Convert to model
    memcached_db.set(f'task-key_{list_name}_{task_name}', task_data.json())
    return task_data


@app.get("/todo_lists/{list_name}/{task_name}", response_model=TaskInDB, status_code=status.HTTP_200_OK, tags=["Tasks"])
async def get_task(list_name: str, task_name: str):
    if not (task_data := memcached_db.get(f'task-key_{list_name}_{task_name}')):
        raise HTTPException(status_code=404, detail=f"Task {task_name} on list {list_name} not found.")
    return TaskInDB(**json.loads(task_data))


@app.delete("/todo_lists/{list_name}/{task_name}",
            response_model=create_model('DeleteTaskResponse', message=(str, ...), task_name=(str, ...), list_name=(str, ...)),
            status_code=status.HTTP_200_OK,
            tags=["Tasks"])
async def delete_task(list_name: str, task_name: str):
    # Check if list exists
    if not (list_data := memcached_db.get(f'task-list-key_{list_name}')):
        raise HTTPException(status_code=404, detail=f"List {list_name} not found")

    # Delete and check if task exists
    if not memcached_db.delete(f'task-key_{list_name}_{task_name}', noreply=False):
        raise HTTPException(status_code=404, detail=f"Task {task_name} on list {list_name} not found.")

    # Delete task to list data and uodate
    list_data = TaskListInDB(**json.loads(list_data))
    try:
        list_data.tasks.remove(task_name)
        memcached_db.set(f'task-list-key_{list_name}', list_data.json())
    except ValueError:
        pass

    return {'message': f'Task {task_name} on list {list_name} deleted successfully.',
            'task_name': task_name,
            'list_name': list_name}
