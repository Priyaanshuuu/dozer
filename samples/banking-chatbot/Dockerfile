FROM python:3.11.13-slim AS api
WORKDIR /sample
COPY . .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "profile_api.py"]

FROM api AS chatbot
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "chat.py", "--customer", "1"]
