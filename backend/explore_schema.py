import pyodbc
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

DB_SERVER = os.getenv('DB_SERVER')
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_DRIVER = os.getenv('DB_DRIVER', "ODBC Driver 17 for SQL Server")

def get_table_schema(database, table_name):
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
        
        cursor.execute(f"""
            SELECT COLUMN_NAME, DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = '{table_name}'
        """)
        
        columns = [f"{row[0]} ({row[1]})" for row in cursor.fetchall()]
        conn.close()
        return columns
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    tables_to_inspect = ['Voyage', 'Vessel', 'Cargo', 'Container']
    for table in tables_to_inspect:
        print(f"\n--- Table {table} Schema (SmartTOS) ---")
        cols = get_table_schema("SmartTOS", table)
        if isinstance(cols, list):
            print(", ".join(cols[:15]) + ("..." if len(cols) > 15 else ""))
        else:
            print(cols)
