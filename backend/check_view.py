import pyodbc

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=Tosdb.nghetinhport.vn\\mssqlserver,37689;"
    "DATABASE=SmartTOS;"
    "UID=nghetinhport_readonly;"
    "PWD=ngHeeJTInH~Port37!99;"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)

try:
    conn = pyodbc.connect(conn_str, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID('SmartTOS.dbo.vwStatisticsWarehouseInventoryByDay'))")
    res = cursor.fetchone()
    if res and res[0]:
        print("VIEW DEF:")
        print(res[0])
    else:
        print("No view definition found. Missing privileges?")
    conn.close()
except Exception as e:
    print("Error:", e)
