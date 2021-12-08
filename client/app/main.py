import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Union
import os

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, create_model
from pymemcache.client.base import Client
from starlette.responses import RedirectResponse

description = """
Ephemeral TODO List, save your todo task as long as the server lives.

## List

You will be able to:

* **Create list:** Define a list with a new unique name and an optional description. An ID will be given to the list where list_name's blank spaces are replaced with '_'. If the list_name has no spaces then the ID and the name are the same.
* **Consult list's info and tasks:** Get your list data and assigned tasks. You can choose between getting a list of task names or a list with each task's data.
* **Delete a list:** Delete an existing list and every assigned task to that list.

## Task

You will be able to:

* **Create task in an existing list:** Add a name, a description, a status and a due date.
* **Get task data**.
* **Edit task data:** Edit an existing task's description, status or due date.
* **Delete a task**.

## Backup

Save all existing data in files. Due to memcached's nature there may be some lost items...
"""

# ---------------------------------------------------------
# Memcached
# ---------------------------------------------------------
MEMCACHED_IP = (os.environ['MEMCACHED_IP'], 11211)

memcached_db = Client(MEMCACHED_IP)

task_list_id_set = set()


def delete_tasks(list_id: str, tasks: List[str]):
    for task_id in tasks:
        memcached_db.delete(f'task-key_{list_id}_{task_id}')


# ---------------------------------------------------------
# API Aplication
# ---------------------------------------------------------

app = FastAPI(
    title="Ephemeral TODO List",
    description=description,
    version="2.3.0",
    contact={
        "name": "Iker de la Iglesia Martínez",
        "email": "idelaiglesia004@ikasle.ehu.eus"
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)


@app.on_event("startup")
def startup():
    global memcached_db
    memcached_db = Client(MEMCACHED_IP)


@app.on_event("shutdown")
def shutdown():
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


class UpdatedTaskData(BaseModel):
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    due_date: Optional[str] = None


class Task(BaseModel):
    name: str
    description: str
    status: TaskStatus = TaskStatus.assigned
    due_date: str = None


class TaskInDB(Task):
    task_id: str
    assigned_list: str
    creation_date: str = Field(default_factory=lambda: datetime.now().strftime("%d-%b-%Y (%H:%M:%S)"))


class TaskList(BaseModel):
    name: str
    description: Optional[str] = None


class TaskListInDB(TaskList):
    list_id: str
    creation_date: str = Field(default_factory=lambda: datetime.now().strftime("%d-%b-%Y (%H:%M:%S)"))
    tasks: List[Union[str, TaskInDB]] = Field(default_factory=list)


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url='/docs')


# Lists
# ---------------------------------------------------------

@app.post("/todo_lists/", response_model=TaskListInDB, status_code=status.HTTP_201_CREATED, tags=["Lists"])
def create_list(data: TaskList):
    list_id = data.name.replace(' ', '_')
    if memcached_db.get(f'task-list-key_{list_id}'):
        raise HTTPException(status_code=409, detail=f"There's already a list with id {list_id}")
    data = TaskListInDB(list_id=list_id, **dict(data))
    memcached_db.set(f'task-list-key_{list_id}', data.json())
    task_list_id_set.add(list_id)
    return data


@app.get("/todo_lists/{list_id}", response_model=TaskListInDB, status_code=status.HTTP_202_ACCEPTED, tags=["Lists"])
def get_list(list_id: str, get_task_data: Optional[bool] = False):
    if not (list_data := memcached_db.get(f'task-list-key_{list_id}')):
        raise HTTPException(status_code=404, detail=f"List {list_id} not found")
    list_data = TaskListInDB(**json.loads(list_data))
    if get_task_data:
        for task_indx, task_id in enumerate(list_data.tasks):
            if task_data := memcached_db.get(f'task-key_{list_id}_{task_id}'):
                list_data.tasks[task_indx] = TaskInDB(**json.loads(task_data))
    return list_data


@app.delete("/todo_lists/{list_id}",
            response_model=create_model('DeleteListResponse', message=(str, ...), list_id=(str, ...), deleted_tasks=(List[str], ...)),
            status_code=status.HTTP_200_OK,
            tags=["Lists"])
def delete_list(list_id: str):
    # Check if list exists and get list data
    if not (list_data := memcached_db.get(f'task-list-key_{list_id}')):
        raise HTTPException(status_code=404, detail=f"List {list_id} not found")

    # Get related tasks and delete them
    related_tasks = TaskListInDB(**json.loads(list_data)).tasks
    delete_tasks(list_id, related_tasks)

    # Delete the list
    memcached_db.delete(f'task-list-key_{list_id}')
    task_list_id_set.remove(list_id)

    # Inform
    return {'message': f'{list_id} deleted successfully.',
            'list_id': list_id,
            'deleted_tasks': related_tasks}


# Tasks
# ---------------------------------------------------------

@app.post("/todo_lists/{list_id}", response_model=TaskInDB, status_code=status.HTTP_201_CREATED, tags=["Tasks"])
def add_task(list_id: str, task_data: Task):
    # Check if list exists
    if not (list_data := memcached_db.get(f'task-list-key_{list_id}')):
        raise HTTPException(status_code=404, detail=f"List {list_id} not found")

    # Check if task already exists
    task_id = task_data.name.replace(' ', '_')
    if memcached_db.get(f'task-key_{list_id}_{task_id}'):
        raise HTTPException(status_code=409,
                            detail=f"There's already a task with id {task_id} on list {list_id}.\n"
                                   f"Use PUT method instead to edit task data.")

    # Generate task
    task_data = TaskInDB(task_id=task_id, assigned_list=list_id, **dict(task_data))
    memcached_db.set(f'task-key_{list_id}_{task_id}', task_data.json())

    # Add task to list data and uodate
    list_data = TaskListInDB(**json.loads(list_data))
    list_data.tasks.append(task_id)
    memcached_db.set(f'task-list-key_{list_id}', list_data.json())

    return task_data


@app.put("/todo_lists/{list_id}/{task_id}", response_model=TaskInDB, status_code=status.HTTP_200_OK, tags=["Tasks"])
def edit_task(list_id: str, task_id: str, updated_task_data: UpdatedTaskData):
    # Check if task exists
    if not (task_data := memcached_db.get(f'task-key_{list_id}_{task_id}')):
        raise HTTPException(status_code=404, detail=f"Task {task_id} on list {list_id} not found.")

    # Get original data and updated values
    task_data = json.loads(task_data)
    updated_task_data = updated_task_data.dict(exclude_unset=True)

    # Update data
    task_data.update(updated_task_data)
    task_data = TaskInDB(**task_data)  # Convert to model
    memcached_db.set(f'task-key_{list_id}_{task_id}', task_data.json())
    return task_data


@app.get("/todo_lists/{list_id}/{task_id}", response_model=TaskInDB, status_code=status.HTTP_200_OK, tags=["Tasks"])
def get_task(list_id: str, task_id: str):
    if not (task_data := memcached_db.get(f'task-key_{list_id}_{task_id}')):
        raise HTTPException(status_code=404, detail=f"Task {task_id} on list {list_id} not found.")
    return TaskInDB(**json.loads(task_data))


@app.delete("/todo_lists/{list_id}/{task_id}",
            response_model=create_model('DeleteTaskResponse', message=(str, ...), task_id=(str, ...), list_id=(str, ...)),
            status_code=status.HTTP_200_OK,
            tags=["Tasks"])
def delete_task(list_id: str, task_id: str):
    # Check if list exists
    if not (list_data := memcached_db.get(f'task-list-key_{list_id}')):
        raise HTTPException(status_code=404, detail=f"List {list_id} not found")

    # Delete and check if task exists
    if not memcached_db.delete(f'task-key_{list_id}_{task_id}', noreply=False):
        raise HTTPException(status_code=404, detail=f"Task {task_id} on list {list_id} not found.")

    # Delete task to list data and uodate
    list_data = TaskListInDB(**json.loads(list_data))
    try:
        list_data.tasks.remove(task_id)
        memcached_db.set(f'task-list-key_{list_id}', list_data.json())
    except ValueError:
        pass

    return {'message': f'Task {task_id} on list {list_id} deleted successfully.',
            'task_id': task_id,
            'list_id': list_id}


@app.post("/backup", status_code=status.HTTP_201_CREATED, tags=["Backup"])
def make_backup():
    data = [get_list(task_list, get_task_data=True).dict() for task_list in task_list_id_set]
    Path('./backup').mkdir(exist_ok=True)
    with open(f'./backup/backup_data_{datetime.now().strftime("%d-%m-%Y_%H%M%S")}.json', 'w', encoding='utf8') as f:
        json.dump(data, f, indent=2)
