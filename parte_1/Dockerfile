FROM python:3.9-alpine

ADD cliente.py .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install pymemcache

CMD [ "python", "./cliente.py" ]