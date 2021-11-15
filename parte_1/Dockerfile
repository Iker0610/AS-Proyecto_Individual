# Start from a python image
FROM python:3.9-alpine

# Create a new directory to allocate our API
WORKDIR /my_api

# Copy requirements and install
COPY ./requirements.txt /my_api/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /my_api/requirements.txt

# Copy API project
COPY ./app /my_api/app

# Run uvicorn server with our API on port 80
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]