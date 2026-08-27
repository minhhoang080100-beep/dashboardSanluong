import pyodbc
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

DB_SERVER = os.getenv('DB_SERVER')
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_DRIVER = os.getenv('DB_DRIVER', "ODBC Driver 17 for SQL Server")

def get_tables(database):
    try:
        conn_str = (
            f"DRIVER={{{DB_DRIVER}}};"
            f"SERVER={DB_SERVER};"
            f"DATABASE={database};"
            f"UID={DB_USERNAME};"
            f"PWD={DB_PASSWORD};"
            "Encrypt=yes;"
            "TrustServerCertificate=yes;"
        )
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_TYPE = 'BASE TABLE'
              AND (
                TABLE_NAME LIKE '%Vessel%' OR 
                TABLE_NAME LIKE '%Voyage%' OR 
                TABLE_NAME LIKE '%Container%' OR 
                TABLE_NAME LIKE '%Cargo%' OR 
                TABLE_NAME LIKE '%Job%' OR 
                TABLE_NAME LIKE '%Gate%' OR 
                TABLE_NAME LIKE '%Yard%' OR 
                TABLE_NAME LIKE '%Throughput%' OR 
                TABLE_NAME LIKE '%Tonnage%' OR 
                TABLE_NAME LIKE '%Tau%' OR 
                TABLE_NAME LIKE '%HangHoa%' OR 
                TABLE_NAME LIKE '%SanLuong%' OR
                TABLE_NAME LIKE '%BL%' OR
                TABLE_NAME LIKE '%Manifest%' OR
                TABLE_NAME LIKE '%Ship%' OR
                TABLE_NAME LIKE '%TOS%'
              )
            ORDER BY TABLE_NAME
        """)
        
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return tables
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    print("--- Tables in SmartTOS ---")
    tables_cua_lo = get_tables("SmartTOS")
    if isinstance(tables_cua_lo, list):
        print(f"Found {len(tables_cua_lo)} tables.")
        print("Sample 20 tables:", tables_cua_lo[:20])
    else:
        print(tables_cua_lo)
        
    print("\n--- Tables in SmartTOS_BenThuy ---")
    tables_ben_thuy = get_tables("SmartTOS_BenThuy")
    if isinstance(tables_ben_thuy, list):
        print(f"Found {len(tables_ben_thuy)} tables.")
        print("Sample 20 tables:", tables_ben_thuy[:20])
    else:
        print(tables_ben_thuy)
