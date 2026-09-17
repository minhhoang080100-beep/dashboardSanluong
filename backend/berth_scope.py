"""Whole-voyage production attribution from the first actual berth.

The grouped query is deliberately independent of reporting dates. A later berth
move cannot reassign the earlier or later operations of the same voyage.
"""

PRODUCTION_SCOPES = {
    "nghe_tinh": "Cảng Nghệ Tĩnh",
    "vietsun": "Cầu 5",
    "unclassified": "Chưa xác định cầu",
}
BERTH_RULE_VERSION = "initial-berth-v1"
_SCHEMAS = {"cua_lo": "SmartTOS.dbo", "ben_thuy": "SmartTOS_BenThuy.dbo"}


def validate_production_scope(value):
    if not isinstance(value, str) or value not in PRODUCTION_SCOPES:
        raise ValueError("Phạm vi sản lượng không hợp lệ.")
    return value


def initial_berth_query(schema: str, terminal: str, *, selection="all") -> str:
    """Return at most one assignment per voyage; only allowlisted identifiers.

    ATB is the actual berthing time; ATA is the fallback when ATB is absent.
    An actual arrival with no usable time, or distinct berths tied for the first
    time, is ambiguous. Arrival flags alone do not order moves. A planned row
    with no actual timestamp/arrival confirmation is not an actual berth.
    """
    if not isinstance(terminal, str) or _SCHEMAS.get(terminal) != schema:
        raise ValueError("Nguồn xí nghiệp không hợp lệ.")
    if not isinstance(selection, str) or selection not in {"all", "voyage"}:
        raise ValueError("Phạm vi tra cứu cầu không hợp lệ.")
    # Restrict WHICH voyage needs attribution, never WHEN its first berth was.
    selected_voyage = "AND history.vesselVoyageId = ?" if selection == "voyage" else ""
    assigned = """first_event.missing_time = 0
        AND first_event.invalid_first = 0
        AND first_event.first_berth_min = first_event.first_berth_max"""
    return f"""SELECT chosen.vesselVoyageId,
        chosen.initial_berth_id, chosen.initial_berth_code, chosen.initial_berth_at,
        chosen.berth_assignment_status,
        CASE WHEN chosen.berth_assignment_status <> 'assigned' THEN 'unclassified'
             WHEN '{terminal}' = 'cua_lo' AND chosen.initial_berth_id = 13 THEN 'vietsun'
             ELSE 'nghe_tinh' END AS production_scope
    FROM (
        SELECT first_event.vesselVoyageId,
            CASE WHEN {assigned} THEN first_event.first_berth_min END AS initial_berth_id,
            CASE WHEN {assigned} THEN first_event.first_code END AS initial_berth_code,
            CASE WHEN {assigned} THEN first_event.first_at END AS initial_berth_at,
            CASE WHEN {assigned} THEN 'assigned' ELSE 'ambiguous' END AS berth_assignment_status
        FROM (
            SELECT ranked.vesselVoyageId, MIN(ranked.first_at) AS first_at,
                MAX(CASE WHEN ranked.event_at IS NULL THEN 1 ELSE 0 END) AS missing_time,
                MIN(CASE WHEN ranked.event_at = ranked.first_at THEN ranked.berthId END) AS first_berth_min,
                MAX(CASE WHEN ranked.event_at = ranked.first_at THEN ranked.berthId END) AS first_berth_max,
                MIN(CASE WHEN ranked.event_at = ranked.first_at THEN ranked.berthCode END) AS first_code,
                MAX(CASE WHEN ranked.event_at = ranked.first_at
                    AND (ranked.berthId IS NULL OR ranked.berthId <= 0
                         OR NULLIF(LTRIM(RTRIM(ranked.berthCode)), '') IS NULL)
                    THEN 1 ELSE 0 END) AS invalid_first
            FROM (
                SELECT events.*, MIN(events.event_at) OVER (PARTITION BY events.vesselVoyageId) AS first_at
                FROM (
                    SELECT history.vesselVoyageId, history.berthId, berth.berthCode,
                        CASE WHEN history.ATB >= '1900-01-01T00:00:00' THEN history.ATB
                             WHEN history.ATA >= '1900-01-01T00:00:00' THEN history.ATA END AS event_at
                    FROM {schema}.DoBerth history
                    LEFT JOIN {schema}.Berth berth ON berth.berthId = history.berthId
                    WHERE COALESCE(history.rowDeleted, 0) = 0
                      {selected_voyage}
                      AND (history.isArrival = 1 OR history.ATB >= '1900-01-01T00:00:00'
                           OR history.ATA >= '1900-01-01T00:00:00')
                ) events
            ) ranked
            GROUP BY ranked.vesselVoyageId
        ) first_event
    ) chosen"""
