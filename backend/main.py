from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import get_db_connection
from .repository import dashboard_repo
from typing import List, Dict, Any
from datetime import datetime, timedelta

app = FastAPI(
    title="Cảng Nghệ Tĩnh - Dashboard API",
    description="API cho hệ thống Dashboard sản lượng kết nối vào SmartTOS và SmartTOS_BenThuy",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Welcome to Cảng Nghệ Tĩnh Dashboard API"}

@app.get("/api/health")
def health_check():
    conn = get_db_connection()
    if conn:
        conn.close()
        return {"status": "ok", "database": "connected"}
    else:
        return {"status": "error", "database": "disconnected"}

@app.get("/api/overview")
def get_overview():
    """Lấy dữ liệu tổng quan thông qua Repository"""
    # Lấy dữ liệu của tháng hiện tại
    now = datetime.now()
    start_date = now.replace(day=1).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    
    return dashboard_repo.get_overview(start_date, end_date)

@app.get("/api/cargo-breakdown")
def get_cargo_breakdown():
    """Lấy cơ cấu loại hàng hóa thông qua Repository"""
    now = datetime.now()
    start_date = now.replace(day=1).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    
    return dashboard_repo.get_cargo_breakdown(start_date, end_date)

@app.get("/api/throughput-history")
def get_throughput_history():
    """Lấy lịch sử sản lượng thông qua Repository"""
    return dashboard_repo.get_throughput_history()

@app.get("/api/terminal-breakdown")
def get_terminal_breakdown():
    """Lấy dữ liệu phân tách theo xí nghiệp"""
    now = datetime.now()
    start_date = now.replace(day=1).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    return dashboard_repo.get_terminal_breakdown(start_date, end_date)

@app.get("/api/direction-breakdown")
def get_direction_breakdown():
    """Lấy dữ liệu phân tách theo hướng hàng"""
    now = datetime.now()
    start_date = now.replace(day=1).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    return dashboard_repo.get_direction_breakdown(start_date, end_date)

@app.get("/api/top-customers")
def get_top_customers():
    """Lấy dữ liệu top khách hàng/hãng tàu"""
    now = datetime.now()
    start_date = now.replace(day=1).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    return dashboard_repo.get_top_customers(start_date, end_date)

@app.get("/api/efficiency")
def get_efficiency():
    """Lấy chỉ số hiệu suất khai thác"""
    now = datetime.now()
    start_date = now.replace(day=1).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    return dashboard_repo.get_efficiency_metrics(start_date, end_date)

@app.get("/api/yard-occupancy")
def get_yard_occupancy():
    """Lấy dữ liệu lưu bãi"""
    return dashboard_repo.get_yard_occupancy()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=True)
