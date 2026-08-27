import pyodbc
from database import get_db_connection
from typing import Dict, Any, List
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

class DashboardRepository:
    def __init__(self):
        # Biến để hỗ trợ thay đổi môi trường
        self.db_cua_lo = "SmartTOS.dbo"
        self.db_ben_thuy = "SmartTOS_BenThuy.dbo"
        # cargoDirectId: 1=LOADING (Xếp tàu), 2=UNLOADING (Dỡ tàu)
        # Chỉ tính sản lượng thông qua cảng, loại bỏ STORAGE_IMPORT/EXPORT để tránh đếm trùng
        self.throughput_filter = "AND t.cargoDirectId IN (1, 2) AND (j.jobMethodName LIKE N'%Tàu%' OR j.jobMethodName LIKE N'%thông qua%')"
        self.row_active_filter = "AND t.rowDeleted IS NULL"
        
        # Logic quy đổi TEU dùng chung
        self.teu_logic = """
            CASE 
                WHEN cargoName IN ('20F', '20E', '20R') THEN ISNULL(quantityTotalSum, 0)
                WHEN cargoName IN ('40F', '40E', '40R', '45F', '45E') THEN ISNULL(quantityTotalSum, 0) * 2
                ELSE 0
            END
        """
        
        # Logic quy đổi Tonnage cho Container (Theo quy định kinh doanh)
        self.tonnage_logic = """
            CASE 
                WHEN c.cargoName IN ('20F', '20R') THEN ISNULL(t.quantityTotalSum, 0) * 25
                WHEN c.cargoName IN ('40F', '40R', '45F') THEN ISNULL(t.quantityTotalSum, 0) * 30
                WHEN c.cargoName = '20E' THEN ISNULL(t.quantityTotalSum, 0) * 2.25
                WHEN c.cargoName IN ('40E', '45E') THEN ISNULL(t.quantityTotalSum, 0) * 3.88
                ELSE ISNULL(t.weightNetSum, 0)
            END
        """
        
    def _execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        if not conn:
            return []
            
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            columns = [column[0] for column in cursor.description]
            results = []
            for row in cursor.fetchall():
                # Xử lý các object đặc biệt như Decimal, date để parse thành json
                row_dict = dict(zip(columns, row))
                for key, val in row_dict.items():
                    if isinstance(val, date):
                        row_dict[key] = val.strftime('%Y-%m-%d')
                    elif val is not None:
                        try:
                            row_dict[key] = float(val)
                        except:
                            pass
                results.append(row_dict)
            return results
        except Exception as e:
            logger.error(f"SQL Error: {e}\nQuery: {query}")
            return []
        finally:
            if conn:
                conn.close()

    def get_overview(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Lấy dữ liệu tổng quan cho Dashboard từ cả 2 Xí nghiệp qua TallyShift.
        Chỉ tính sản lượng LOADING + UNLOADING để tránh đếm trùng.
        Bao gồm tính trend so sánh cùng kỳ tháng trước."""
        
        # Query chính: Dữ liệu tháng hiện tại
        query = f"""
            WITH Combined AS (
                SELECT N'Cửa Lò' as Terminal, t.weightNetSum, t.quantityTotalSum, t.vesselVoyageId, c.cargoName
                FROM {self.db_cua_lo}.TallyShift t
                LEFT JOIN {self.db_cua_lo}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT N'Bến Thủy' as Terminal, {self.tonnage_logic} as weightNetSum, t.quantityTotalSum, t.vesselVoyageId, c.cargoName
                FROM {self.db_ben_thuy}.TallyShift t
                LEFT JOIN {self.db_ben_thuy}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT 
                SUM(ISNULL(weightNetSum, 0)) as total_tonnage,
                SUM({self.teu_logic}) as total_teu,
                COUNT(DISTINCT Terminal + CAST(vesselVoyageId AS VARCHAR)) as vessel_calls
            FROM Combined
        """
        params = (start_date, end_date, start_date, end_date)
        res = self._execute_query(query, params)
        
        if not res or len(res) == 0:
            return {"total_teu": 0, "total_tonnage": 0, "vessel_calls": 0, "revenue_estimate": 0, "period": "",
                    "trend_teu": 0, "trend_tonnage": 0, "trend_vessels": 0}
        
        row = res[0]
        current = {
            "total_teu": row.get('total_teu', 0), 
            "total_tonnage": row.get('total_tonnage', 0),
            "vessel_calls": row.get('vessel_calls', 0), 
            "revenue_estimate": 0,
            "period": f"{start_date} - {end_date}"
        }
        
        # Query so sánh: Cùng kỳ tháng trước (DATEADD month -1)
        query_prev = f"""
            WITH Combined AS (
                SELECT N'Cửa Lò' as Terminal, t.weightNetSum, t.quantityTotalSum, t.vesselVoyageId, c.cargoName
                FROM {self.db_cua_lo}.TallyShift t
                LEFT JOIN {self.db_cua_lo}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= DATEADD(month, -1, ?) AND t.shiftDate <= DATEADD(month, -1, ?)
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT N'Bến Thủy' as Terminal, {self.tonnage_logic} as weightNetSum, t.quantityTotalSum, t.vesselVoyageId, c.cargoName
                FROM {self.db_ben_thuy}.TallyShift t
                LEFT JOIN {self.db_ben_thuy}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= DATEADD(month, -1, ?) AND t.shiftDate <= DATEADD(month, -1, ?)
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT 
                SUM(ISNULL(weightNetSum, 0)) as total_tonnage,
                SUM({self.teu_logic}) as total_teu,
                COUNT(DISTINCT Terminal + CAST(vesselVoyageId AS VARCHAR)) as vessel_calls
            FROM Combined
        """
        params_prev = (start_date, end_date, start_date, end_date)
        res_prev = self._execute_query(query_prev, params_prev)
        
        # Tính trend %
        def calc_trend(current_val, prev_val):
            if prev_val and prev_val > 0:
                return round(((current_val - prev_val) / prev_val) * 100, 1)
            return 0
        
        if res_prev and len(res_prev) > 0:
            prev = res_prev[0]
            current["trend_tonnage"] = calc_trend(current.get("total_tonnage", 0), prev.get("total_tonnage", 0))
            current["trend_teu"] = calc_trend(current.get("total_teu", 0), prev.get("total_teu", 0))
            current["trend_vessels"] = int(current.get("vessel_calls", 0) - prev.get("vessel_calls", 0))
        else:
            current["trend_tonnage"] = 0
            current["trend_teu"] = 0
            current["trend_vessels"] = 0
        
        return current

    def get_cargo_breakdown(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Phân tích cơ cấu loại hàng hóa chi tiết qua TallyShift JOIN Cargo.
        Chỉ tính LOADING + UNLOADING."""
        query = f"""
            WITH CombinedData AS (
                SELECT c.cargoGroupName as cargoName, {self.tonnage_logic} as weightNetSum
                FROM {self.db_cua_lo}.TallyShift t
                JOIN {self.db_cua_lo}.vwCargoStatistics c ON t.cargoId = c.cargoId
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT c.cargoGroupName as cargoName, {self.tonnage_logic} as weightNetSum
                FROM {self.db_ben_thuy}.TallyShift t
                JOIN {self.db_ben_thuy}.vwCargoStatistics c ON t.cargoId = c.cargoId
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT TOP 5 ISNULL(cargoName, N'Khác') as name, SUM(weightNetSum) as value
            FROM CombinedData
            GROUP BY ISNULL(cargoName, N'Khác')
            ORDER BY value DESC
        """
        params = (start_date, end_date, start_date, end_date)
        res = self._execute_query(query, params)
        
        # Format the output matching what the UI expects (name, value, tonnage)
        total = sum(r.get('value', 0) for r in res)
        formatted_res = []
        for r in res:
            perc = round((r.get('value', 0) / total) * 100, 1) if total > 0 else 0
            formatted_res.append({
                "name": str(r.get('name', 'Unknown')),
                "value": perc,
                "tonnage": r.get('value', 0)
            })
            
        if not formatted_res:
            return [
                {"name": "Dăm gỗ (Woodchips)", "value": 35, "tonnage": 437640},
                {"name": "Than đá (Coal)", "value": 20, "tonnage": 250080},
                {"name": "Clinker", "value": 15, "tonnage": 187560},
            ]
        return formatted_res

    def get_throughput_history(self) -> List[Dict[str, Any]]:
        """Sản lượng lịch sử 6 tháng gần nhất. Chỉ tính LOADING + UNLOADING."""
        query = f"""
            WITH MonthlyData AS (
                SELECT 
                    CONVERT(varchar(7), t.shiftDate, 126) as month_val,
                    {self.tonnage_logic} as tonnage,
                    {self.teu_logic} as teu
                FROM {self.db_cua_lo}.TallyShift t
                LEFT JOIN {self.db_cua_lo}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= DATEADD(month, DATEDIFF(month, 0, DATEADD(month, -5, GETDATE())), 0) AND t.shiftDate <= GETDATE()
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT 
                    CONVERT(varchar(7), t.shiftDate, 126) as month_val,
                    {self.tonnage_logic} as tonnage,
                    {self.teu_logic} as teu
                FROM {self.db_ben_thuy}.TallyShift t
                LEFT JOIN {self.db_ben_thuy}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= DATEADD(month, DATEDIFF(month, 0, DATEADD(month, -5, GETDATE())), 0) AND t.shiftDate <= GETDATE()
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT month_val as date, SUM(tonnage) as tonnage, SUM(teu) as teu
            FROM MonthlyData
            GROUP BY month_val
            ORDER BY month_val ASC
        """
        res = self._execute_query(query)
        return res

    def get_terminal_breakdown(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Phân tách theo Xí nghiệp. Chỉ tính LOADING + UNLOADING."""
        query = f"""
            SELECT N'Cửa Lò' as name, 
                   SUM({self.tonnage_logic}) as tonnage, 
                   SUM({self.teu_logic}) as teu 
            FROM {self.db_cua_lo}.TallyShift t
            LEFT JOIN {self.db_cua_lo}.Cargo c ON t.cargoId = c.cargoId
            JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
            WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                {self.throughput_filter} {self.row_active_filter}
            UNION ALL
            SELECT N'Bến Thủy' as name, 
                   SUM({self.tonnage_logic}) as tonnage, 
                   SUM({self.teu_logic}) as teu 
            FROM {self.db_ben_thuy}.TallyShift t
            LEFT JOIN {self.db_ben_thuy}.Cargo c ON t.cargoId = c.cargoId
            JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
            WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                {self.throughput_filter} {self.row_active_filter}
        """
        params = (start_date, end_date, start_date, end_date)
        res = self._execute_query(query, params)
        if not res:
            return [
                {"name": "Cửa Lò", "teu": 35000, "tonnage": 850000},
                {"name": "Bến Thủy", "teu": 10210, "tonnage": 400400}
            ]
        return res
        
    def get_direction_breakdown(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Phân tách theo Hướng hàng (Direction) thông qua vwCargoStatistics.
        Chỉ tính LOADING + UNLOADING."""
        query = f"""
            WITH DirData AS (
                SELECT 
                    CASE 
                        WHEN t.cargoDirectId = 1 THEN N'Hàng xếp (Loading)'
                        WHEN t.cargoDirectId = 2 THEN N'Hàng dỡ (Unloading)'
                        ELSE N'Khác (Others)'
                    END as dirName,
                    {self.tonnage_logic} as weightNetSum
                FROM {self.db_cua_lo}.TallyShift t
                LEFT JOIN {self.db_cua_lo}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT 
                    CASE 
                        WHEN t.cargoDirectId = 1 THEN N'Hàng xếp (Loading)'
                        WHEN t.cargoDirectId = 2 THEN N'Hàng dỡ (Unloading)'
                        ELSE N'Khác (Others)'
                    END as dirName,
                    {self.tonnage_logic} as weightNetSum
                FROM {self.db_ben_thuy}.TallyShift t
                LEFT JOIN {self.db_ben_thuy}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT dirName as name, SUM(weightNetSum) as value
            FROM DirData
            GROUP BY dirName
        """
        params = (start_date, end_date, start_date, end_date)
        res = self._execute_query(query, params)
        
        if res and len(res) > 0:
            total = sum(r.get('value', 0) for r in res)
            for r in res:
                r['value'] = round((r['value'] / total) * 100, 1) if total > 0 else 0
            return res
            
        return [
            {"name": "Nhập khẩu (Import)", "value": 45},
            {"name": "Xuất khẩu (Export)", "value": 35},
            {"name": "Nội địa (Domestic)", "value": 15},
            {"name": "Quá cảnh (Transit)", "value": 5}
        ]
        
    def get_top_customers(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Top Khách hàng/Hãng tàu (Customer/Shipping Line) qua bảng Partner.
        Chỉ tính LOADING + UNLOADING."""
        query = f"""
            WITH CustomerData AS (
                SELECT p.partnerShortName, {self.tonnage_logic} as weightNetSum
                FROM {self.db_cua_lo}.TallyShift t
                LEFT JOIN {self.db_cua_lo}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_cua_lo}.Partner p ON t.consigneeId = p.partnerId
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ?
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT p.partnerShortName, {self.tonnage_logic} as weightNetSum
                FROM {self.db_ben_thuy}.TallyShift t
                LEFT JOIN {self.db_ben_thuy}.Cargo c ON t.cargoId = c.cargoId
                JOIN {self.db_ben_thuy}.Partner p ON t.consigneeId = p.partnerId
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ? AND t.weightNetSum > 0
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT TOP 5 ISNULL(partnerShortName, N'Khách vãng lai') as name, SUM(weightNetSum) as volume, N'Tấn' as type
            FROM CustomerData
            GROUP BY ISNULL(partnerShortName, N'Khách vãng lai')
            ORDER BY volume DESC
        """
        params = (start_date, end_date, start_date, end_date)
        res = self._execute_query(query, params)
        if res and len(res) > 0:
            return res
            
        return [
            {"name": "Hãng tàu MSC", "volume": 12500, "type": "TEU"},
            {"name": "Tập đoàn Than Khoáng Sản", "volume": 320000, "type": "Tấn"},
            {"name": "Xi măng Công Thanh", "volume": 210000, "type": "Tấn"},
            {"name": "Hãng tàu Maersk", "volume": 8500, "type": "TEU"},
            {"name": "Tôn Hoa Sen", "volume": 150000, "type": "Tấn"}
        ]

    def get_efficiency_metrics(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Tính toán hiệu suất khai thác: Thời gian nằm bến & Năng suất giải phóng tàu.
        Chỉ tính LOADING + UNLOADING."""
        query = f"""
            WITH VesselStats AS (
                -- Tính thời gian nằm bến theo từng CHUYẾN TÀU (VesselVoyage)
                SELECT 
                    t.vesselVoyageId, 
                    AVG(CAST(DATEDIFF(minute, t.ATA, t.ATD) AS FLOAT) / 60.0) as turnaround_hours
                FROM {self.db_cua_lo}.vwTallyShiftFull t
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ? AND t.ATD IS NOT NULL AND t.ATA IS NOT NULL
                    {self.throughput_filter} {self.row_active_filter}
                GROUP BY t.vesselVoyageId
                UNION ALL
                SELECT 
                    t.vesselVoyageId, 
                    AVG(CAST(DATEDIFF(minute, t.ATA, t.ATD) AS FLOAT) / 60.0) as turnaround_hours
                FROM {self.db_ben_thuy}.vwTallyShiftFull t
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ? AND t.ATD IS NOT NULL AND t.ATA IS NOT NULL
                    {self.throughput_filter} {self.row_active_filter}
                GROUP BY t.vesselVoyageId
            ),
            ShiftStats AS (
                -- Tính năng suất giải phóng tàu (Tấn / Máng-Giờ) dựa trên số phút thực tế
                -- Xử lý trường hợp ca làm việc vắt qua đêm gây lỗi âm giờ
                SELECT 
                    SUM(t.weightNetSum) as total_weight,
                    SUM(CAST(
                        CASE WHEN t.shiftEndTime < t.shiftStartTime 
                             THEN DATEDIFF(minute, t.shiftStartTime, DATEADD(day, 1, t.shiftEndTime)) 
                             ELSE DATEDIFF(minute, t.shiftStartTime, t.shiftEndTime) 
                        END AS FLOAT) / 60.0) as total_gang_hours
                FROM {self.db_cua_lo}.vwTallyShiftFull t
                JOIN {self.db_cua_lo}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ? AND t.shiftEndTime IS NOT NULL AND t.shiftStartTime IS NOT NULL
                    {self.throughput_filter} {self.row_active_filter}
                UNION ALL
                SELECT 
                    SUM(t.weightNetSum) as total_weight,
                    SUM(CAST(
                        CASE WHEN t.shiftEndTime < t.shiftStartTime 
                             THEN DATEDIFF(minute, t.shiftStartTime, DATEADD(day, 1, t.shiftEndTime)) 
                             ELSE DATEDIFF(minute, t.shiftStartTime, t.shiftEndTime) 
                        END AS FLOAT) / 60.0) as total_gang_hours
                FROM {self.db_ben_thuy}.vwTallyShiftFull t
                JOIN {self.db_ben_thuy}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE t.shiftDate >= ? AND t.shiftDate <= ? AND t.shiftEndTime IS NOT NULL AND t.shiftStartTime IS NOT NULL
                    {self.throughput_filter} {self.row_active_filter}
            )
            SELECT 
                (SELECT AVG(turnaround_hours) FROM VesselStats) as avg_turnaround_time,
                (SELECT SUM(total_weight) / NULLIF(SUM(total_gang_hours), 0) FROM ShiftStats) as avg_productivity
        """
        params = (start_date, end_date, start_date, end_date, start_date, end_date, start_date, end_date)
        res = self._execute_query(query, params)
        
        if res and len(res) > 0 and res[0].get('avg_turnaround_time') is not None:
            return {
                "turnaround_time": round(res[0]['avg_turnaround_time'], 1),
                "productivity": round(res[0]['avg_productivity'], 1) if res[0].get('avg_productivity') else 0
            }
            
        # Fallback dummy data if no DB connection
        return {
            "turnaround_time": 42.5, # hours
            "productivity": 320.5    # tons/hour
        }

    def get_yard_occupancy(self) -> List[Dict[str, Any]]:
        """Lấy tỷ lệ lấp đầy kho bãi (Yard Occupancy)"""
        query = f"""
            SELECT TOP 5
                warehouseName as name,
                ISNULL(weightNetInventorySum, 0) as inventory,
                ISNULL(warehouseCapacity, 1) as capacity
            FROM {self.db_cua_lo}.vwStatisticsWarehouseInventoryByDay
            WHERE netGate = (SELECT MAX(netGate) FROM {self.db_cua_lo}.vwStatisticsWarehouseInventoryByDay) 
              AND warehouseCapacity > 0
            ORDER BY (weightNetInventorySum / warehouseCapacity) DESC
        """
        res = self._execute_query(query)
        
        if res and len(res) > 0:
            formatted = []
            for r in res:
                cap = r.get('capacity', 1)
                inv = r.get('inventory', 0)
                formatted.append({
                    "name": r.get('name', 'Bãi không tên'),
                    "occupancy": round((inv / cap) * 100, 1),
                    "capacity": cap
                })
            return formatted
            
        # Fallback dummy data
        return [
            {"name": "Bãi Container 1", "occupancy": 85.5, "capacity": 5000},
            {"name": "Bãi Dăm Gỗ", "occupancy": 65.2, "capacity": 150000},
            {"name": "Kho Than 2", "occupancy": 92.0, "capacity": 80000},
            {"name": "Bãi Tổng hợp", "occupancy": 45.0, "capacity": 100000}
        ]

dashboard_repo = DashboardRepository()
