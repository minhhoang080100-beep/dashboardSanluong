import pyodbc
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

DB_SERVER = os.getenv('DB_SERVER')
DB_USERNAME = os.getenv('DB_USERNAME')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_DRIVER = os.getenv('DB_DRIVER', "ODBC Driver 17 for SQL Server")

def test_queries(db_name):
    print(f"\n--- Testing Queries on {db_name} ---")
    conn_str = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={db_name};"
        f"UID={DB_USERNAME};"
        f"PWD={DB_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    
    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # 1. Total Throughput
        q1 = """
        SELECT 
            SUM(ISNULL(weightNetSum, 0)) as total_tonnage,
            SUM(ISNULL(containerTeuSum, 0)) as total_teu
        FROM TallyShift
        """
        cursor.execute(q1)
        row1 = cursor.fetchone()
        print(f"Total Tonnage: {row1[0]}, Total TEU: {row1[1]}")
        
        # 2. Cargo Breakdown
        q2 = """
        SELECT TOP 5 
            c.cargoName, 
            SUM(ISNULL(t.weightNetSum, 0)) as tonnage
        FROM TallyShift t
        LEFT JOIN Cargo c ON t.cargoId = c.cargoId
        WHERE t.weightNetSum > 0
        GROUP BY c.cargoName
        ORDER BY tonnage DESC
        """
        cursor.execute(q2)
        
        # Test get_overview query
        query = """
            WITH Combined AS (
                SELECT 'Cửa Lò' as Terminal, t.weightNetSum, t.quantityTotalSum, t.vesselVoyageId, c.cargoName
                FROM SmartTOS.dbo.TallyShift t
                LEFT JOIN SmartTOS.dbo.Cargo c ON t.cargoId = c.cargoId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                UNION ALL
                SELECT 'Bến Thủy' as Terminal, t.weightNetSum, t.quantityTotalSum, t.vesselVoyageId, c.cargoName
                FROM SmartTOS_BenThuy.dbo.TallyShift t
                LEFT JOIN SmartTOS_BenThuy.dbo.Cargo c ON t.cargoId = c.cargoId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
            )
            SELECT 
                SUM(ISNULL(weightNetSum, 0)) as total_tonnage,
                SUM(
                    CASE 
                        WHEN cargoName IN ('20F', '20E', '20R') THEN ISNULL(quantityTotalSum, 0)
                        WHEN cargoName IN ('40F', '40E', '40R', '45F') THEN ISNULL(quantityTotalSum, 0) * 2
                        ELSE 0
                    END
                ) as total_teu,
                COUNT(DISTINCT vesselVoyageId) as vessel_calls
            FROM Combined
        """
        params = ('2026-08-01', '2026-08-14', '2026-08-01', '2026-08-14')
        cursor.execute(query, params)
        row = cursor.fetchone()
        with open('analyze_output.txt', 'a', encoding='utf-8') as f:
            f.write(f"\nTEST OVERVIEW: Tonnage: {row[0]}, TEU: {row[1]}, Calls: {row[2]}\n")
            
    except Exception as e:
                
        # Check TEUs in vwVesselVoyageAll
        q5 = """
        SELECT SUM(ISNULL(dischargeTEU,0) + ISNULL(loadingTEU,0)) as total_teu
        FROM vwVesselVoyageAll
        """
        cursor.execute(q5)
        teu_vessel = cursor.fetchone()[0]
        
        # Check TEUs in TallyShift (by Cargo name)
        q6 = """
        SELECT c.cargoName, SUM(t.quantityTotalSum) as quantity, SUM(t.weightNetSum) as weight
        FROM TallyShift t
        JOIN Cargo c ON t.cargoId = c.cargoId
        WHERE c.cargoName LIKE '%20%' OR c.cargoName LIKE '%40%' OR c.cargoName LIKE '%container%' OR c.cargoName LIKE '%cont%'
        GROUP BY c.cargoName
        """
        cursor.execute(q6)
        cargos = cursor.fetchall()
        
        with open('analyze_output.txt', 'a', encoding='utf-8') as f:
            f.write(f"\nTotal TEU in vwVesselVoyageAll: {teu_vessel}\n")
            f.write("Container Cargo in TallyShift:\n")
            for r in cargos:
                f.write(f"  {r[0]} | QTY: {r[1]} | WGT: {r[2]}\n")
            
    except Exception as e:
        with open('analyze_output.txt', 'a', encoding='utf-8') as f:
            f.write(f"Error: {e}\n")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    import os
    if os.path.exists('analyze_output.txt'):
        os.remove('analyze_output.txt')
    test_queries("SmartTOS")
    test_queries("SmartTOS_BenThuy")
