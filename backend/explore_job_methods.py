"""
Script khảo sát CSDL SmartTOS v2: Fix encoding + bỏ filter rowDeleted
"""
import pyodbc
import sys
import io

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=Tosdb.nghetinhport.vn\\mssqlserver,37689;"
    "DATABASE=SmartTOS;"
    "UID=nghetinhport_readonly;"
    "PWD=ngHeeJTInH~Port37!99;"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)

def run_query(conn, title, query):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        print(f"  Columns: {columns}")
        print(f"  {'-'*76}")
        rows = cursor.fetchall()
        for row in rows:
            print(f"  {list(row)}")
        print(f"  Total: {len(rows)} rows")
    except Exception as e:
        print(f"  ERROR: {e}")

try:
    conn = pyodbc.connect(conn_str, timeout=10)
    print("Connected to SmartTOS successfully!")

    # 1. CargoDirect - không filter rowDeleted
    run_query(conn, "1. CARGO DIRECT (ALL)", """
        SELECT cargoDirectId, cargoDirectCode, cargoDirectShortCode, cargoDirectName, rowDeleted
        FROM SmartTOS.dbo.CargoDirect
        ORDER BY cargoDirectId
    """)

    # 2. JobMethodType
    run_query(conn, "2. JOB METHOD TYPE (ALL)", """
        SELECT jobMethodTypeId, jobMethodTypeCode, jobMethodTypeName
        FROM SmartTOS.dbo.JobMethodType
        ORDER BY jobMethodTypeId
    """)

    # 3. JobMethod - top 40
    run_query(conn, "3. JOB METHOD (Top 40 - CUA LO)", """
        SELECT TOP 40 jm.jobMethodId, jm.jobMethodCode, jm.jobMethodName,
               jmt.jobMethodTypeCode, jmt.jobMethodTypeName,
               jm.jobMethodTypeId
        FROM SmartTOS.dbo.JobMethod jm
        LEFT JOIN SmartTOS.dbo.JobMethodType jmt ON jm.jobMethodTypeId = jmt.jobMethodTypeId
        WHERE jm.rowInvisible = 0
        ORDER BY jm.jobMethodCode
    """)

    # 4. TallyShift - Phân bổ theo cargoDirectId (không join CargoDirect)
    run_query(conn, "4. TALLY SHIFT CUA LO - cargoDirectId distribution (Aug 2026)", """
        SELECT t.cargoDirectId,
               COUNT(*) as record_count,
               SUM(ISNULL(t.weightNetSum, 0)) as total_weight
        FROM SmartTOS.dbo.TallyShift t
        WHERE t.shiftDate >= '2026-08-01' AND t.shiftDate <= '2026-08-17'
              AND t.rowDeleted = 0
        GROUP BY t.cargoDirectId
        ORDER BY total_weight DESC
    """)

    # 5. TallyShift - Phân bổ theo jobMethodId
    run_query(conn, "5. TALLY SHIFT CUA LO - jobMethod distribution (Aug 2026)", """
        SELECT TOP 15 t.jobMethodId, jm.jobMethodCode, jm.jobMethodName,
               t.cargoDirectId,
               COUNT(*) as record_count,
               SUM(ISNULL(t.weightNetSum, 0)) as total_weight
        FROM SmartTOS.dbo.TallyShift t
        JOIN SmartTOS.dbo.JobMethod jm ON t.jobMethodId = jm.jobMethodId
        WHERE t.shiftDate >= '2026-08-01' AND t.shiftDate <= '2026-08-17'
              AND t.rowDeleted = 0
        GROUP BY t.jobMethodId, jm.jobMethodCode, jm.jobMethodName, t.cargoDirectId
        ORDER BY total_weight DESC
    """)

    # 6. Bến Thủy - cargoDirectId
    run_query(conn, "6. TALLY SHIFT BEN THUY - cargoDirectId distribution (Aug 2026)", """
        SELECT t.cargoDirectId,
               COUNT(*) as record_count,
               SUM(ISNULL(t.weightNetSum, 0)) as total_weight
        FROM SmartTOS_BenThuy.dbo.TallyShift t
        WHERE t.shiftDate >= '2026-08-01' AND t.shiftDate <= '2026-08-17'
              AND t.rowDeleted = 0
        GROUP BY t.cargoDirectId
        ORDER BY total_weight DESC
    """)

    # 7. Bến Thủy - jobMethod
    run_query(conn, "7. TALLY SHIFT BEN THUY - jobMethod distribution (Aug 2026)", """
        SELECT TOP 15 t.jobMethodId, jm.jobMethodCode, jm.jobMethodName,
               t.cargoDirectId,
               COUNT(*) as record_count,
               SUM(ISNULL(t.weightNetSum, 0)) as total_weight
        FROM SmartTOS_BenThuy.dbo.TallyShift t
        JOIN SmartTOS_BenThuy.dbo.JobMethod jm ON t.jobMethodId = jm.jobMethodId
        WHERE t.shiftDate >= '2026-08-01' AND t.shiftDate <= '2026-08-17'
              AND t.rowDeleted = 0
        GROUP BY t.jobMethodId, jm.jobMethodCode, jm.jobMethodName, t.cargoDirectId
        ORDER BY total_weight DESC
    """)

    # 8. So sánh: SUM toàn bộ vs SUM chỉ cargoDirectId cụ thể
    run_query(conn, "8. SO SANH: Tong SUM vs SUM theo tung cargoDirectId (CUA LO, Aug 2026)", """
        SELECT 'ALL' as filter_type, 
               SUM(ISNULL(weightNetSum, 0)) as total_weight,
               COUNT(*) as total_records
        FROM SmartTOS.dbo.TallyShift
        WHERE shiftDate >= '2026-08-01' AND shiftDate <= '2026-08-17' AND rowDeleted = 0
    """)

    conn.close()
    print("\n\nDone. Connection closed.")

except Exception as e:
    print(f"Connection failed: {e}")
