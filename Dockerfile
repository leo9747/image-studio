FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Containers are reached from outside, so bind to all interfaces here. Set
# STUDIO_PASSWORD when running this, or anyone who reaches the port can spend
# your API credits.
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
