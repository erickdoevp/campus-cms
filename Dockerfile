FROM python:3.12-slim

# mysqlclient compiles against the MySQL client headers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
# The Django admin's CSS and JS, served by WhiteNoise.
RUN python manage.py collectstatic --noinput
EXPOSE 8000
# Production (tutor local). `tutor dev` runs runserver instead, with the repo mounted.
CMD ["gunicorn", "campus_cms.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "150", "--access-logfile", "-"]
