import pyodbc
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

DB_SERVER = os.getenv('DB_SERVER')
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_DRIVER = os.getenv('DB_DRIVER', "ODBC Driver 17 for SQL Server")

def query_schema(query):
    try:
        conn_str = (
            f"DRIVER={{{DB_DRIVER}}};"
            f"SERVER={DB_SERVER};"
            f"DATABASE=SmartTOS;"
            f"UID={DB_USERNAME};"
            f"PWD={DB_PASSWORD};"
            "Encrypt=yes;"
            "TrustServerCertificate=yes;"
        )
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        cursor.execute(query)
        results = cursor.fetchall()
        
        columns = [column[0] for column in cursor.description]
        conn.close()
        return columns, results
    except Exception as e:
        return f"Error: {e}", None

if __name__ == "__main__":
    q = """
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND (
            TABLE_NAME LIKE '%Container%' OR 
            TABLE_NAME LIKE '%Job%' OR 
            TABLE_NAME LIKE '%Bill%' OR 
            TABLE_NAME LIKE '%Order%' OR 
            TABLE_NAME LIKE '%Ticket%' OR
            TABLE_NAME LIKE '%Gate%'
          )
        ORDER BY TABLE_NAME
    """
    cols, rows = query_schema(q)
    if rows:
        tables = [row[0] for row in rows]
        print(f"Found {len(tables)} tables.")
        print(tables)
    else:
        print(cols)
