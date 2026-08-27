import pyodbc
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

DB_SERVER = os.getenv('DB_SERVER')
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_DRIVER = os.getenv('DB_DRIVER', "ODBC Driver 17 for SQL Server")

def query_data(query):
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
    queries = {
        "TallyShift": "SELECT TOP 5 shiftDate, weightNetSum, containerTeuSum, cargoWeightNetSum FROM TallyShift WHERE weightNetSum > 0 OR containerTeuSum > 0 ORDER BY shiftDate DESC"
    }
    
    with open('output.txt', 'w', encoding='utf-8') as f:
        for name, q in queries.items():
            f.write(f"--- {name} ---\n")
            cols, rows = query_data(q)
            if rows:
                f.write("Columns: " + str(cols) + "\n")
                for row in rows:
                    f.write(str(row) + "\n")
            else:
                f.write(str(cols) + "\n")
            f.write("\n")
