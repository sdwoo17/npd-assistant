FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NPD_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home npd && mkdir /data && chown npd:npd /data
COPY app ./app
COPY static ./static
COPY samples ./samples
COPY manage.py ./
USER npd
EXPOSE 8765
ENTRYPOINT ["python", "manage.py"]
CMD ["serve", "--host", "0.0.0.0", "--origin", "http://127.0.0.1:8765"]
