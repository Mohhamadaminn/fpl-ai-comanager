FROM python:3.12-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements/ requirements/

RUN pip install --no-cache-dir -r requirements/dev.txt

COPY . .

ENV HTTP_PROXY=
ENV HTTPS_PROXY=

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]