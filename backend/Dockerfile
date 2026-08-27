FROM python:3.11-slim-bullseye

# Thiết lập biến môi trường để Python chạy mượt hơn trong Docker
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cài đặt các thư viện hệ thống và Microsoft ODBC Driver 17 for SQL Server
RUN apt-get update && apt-get install -y curl apt-transport-https gnupg2 unixodbc-dev build-essential \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc trong container
WORKDIR /app

# Copy file requirements và cài đặt thư viện Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ source code backend vào một thư mục con là /app/backend
COPY . ./backend

# Mở port 8000
EXPOSE 8000

# Lệnh khởi chạy server FastAPI dưới dạng package (backend.main:app)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
