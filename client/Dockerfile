# Start from a python image
FROM python:3.9-alpine

# Create a new directory to allocate our API
WORKDIR /client_api

# Copy requirements and install
COPY requirements.txt /client_api/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /client_api/requirements.txt

# Copy API project
COPY app /client_api/app

# Set default memcached database IP
ENV MEMCACHED_IP=localhost

# Run uvicorn server with our API on port 80
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]