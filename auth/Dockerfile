FROM python:3.12-alpine
WORKDIR /app
COPY app.py /app/app.py
# services.json is mounted at runtime so editing it needs no rebuild.
EXPOSE 8000
CMD ["python", "-u", "/app/app.py"]
