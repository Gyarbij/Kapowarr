FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NAME=Kapowarr

WORKDIR /app

COPY requirements.txt /app/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 5656

STOPSIGNAL SIGTERM

CMD ["python", "Kapowarr.py"]
