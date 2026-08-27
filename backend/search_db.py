import pyodbc
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

DB_SERVER = os.getenv('DB_SERVER')
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_DRIVER = os.getenv('DB_DRIVER', "ODBC Driver 17 for SQL Server")

def search_columns(database):
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
        
        # Search for columns that might indicate throughput/production
        query = """
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME LIKE '%Statistic%'
               OR TABLE_NAME LIKE '%Tally%'
               OR TABLE_NAME LIKE '%CargoDirect%'
               OR TABLE_NAME LIKE '%JobMethod%'
               OR TABLE_NAME = 'Job'
            ORDER BY TABLE_NAME
        """
        cursor.execute(query)
        results = cursor.fetchall()
        
        # Group by table
        table_cols = {}
        for row in results:
            t_name = row[0]
            c_name = row[1]
            if t_name not in table_cols:
                table_cols[t_name] = []
            table_cols[t_name].append(c_name)
            
        conn.close()
        return table_cols
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    res = search_columns("SmartTOS")
    with open('output_tables.txt', 'w', encoding='utf-8') as f:
        f.write("--- Searching in SmartTOS ---\n")
        if isinstance(res, dict):
            for t, cols in res.items():
                f.write(f"{t}: {', '.join(cols)}\n")
        else:
            f.write(str(res) + "\n")
