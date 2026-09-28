"""Immutable, bounded export versions. HTTP reads never run SQL Server queries.

Publication is atomic per resource and company. Coverage is explicit: a period
that has not been extracted is not an empty result. Consumers pin snapshotId
after page one so a concurrent publication cannot duplicate or skip records.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from .registry import IDENTITY, MODELS, PRODUCTION
from .operation_contracts import OPERATION_MODELS, OPERATION_REFERENCES
from .errors import CorporateError
from .freshness import ensure_fresh, read_time, replace_window
from .metadata_identity import POLICY as METADATA_MERGE_POLICY, validate_variants, matching_pair

TZ = timezone(timedelta(hours=7))
MAX_ROWS = 250_000
ANY_VERSION = object()
CUSTOMER_DATE_BASIS = 'source_createdDate_v1'


def operation_dependencies(resource, rows):
    """Cache only actually used catalog resources, never source IDs or payloads."""
    references = OPERATION_REFERENCES.get(resource, {})
    if not references:
        return []
    dependencies = set()
    for row in rows:
        for field, target in references.items():
            value = row.get(field)
            if value is not None and (not isinstance(value, list) or value):
                dependencies.add(target)
    return sorted(dependencies)


def ensure_dependencies_fresh(db, dependencies, company_id, max_age_seconds, *, snapshot_published_at=None):
    """Check linked catalog age and retained-snapshot integrity in one DB view.

    A retained snapshot cannot rely on a catalog republished since that snapshot.
    This conservative invalidation also covers changed-then-reverted catalogs
    and applies independently of the optional freshness limit.
    """
    if not dependencies or (max_age_seconds in (None, 0) and snapshot_published_at is None):
        return
    placeholders = ','.join('?' for _ in dependencies)
    versions = {row['resource']: row for row in db.execute(f'''
        SELECT resource,read_at,published_at FROM export_versions
        WHERE current=1 AND company_id=? AND resource IN ({placeholders})''',
        [company_id, *dependencies])}
    if snapshot_published_at is not None:
        try:
            snapshot_time = datetime.fromisoformat(snapshot_published_at)
            changed = snapshot_time.tzinfo is None or bool(set(dependencies) - set(versions))
            changed = changed or any(datetime.fromisoformat(version['published_at']) > snapshot_time
                                     for version in versions.values())
        except (TypeError, ValueError):
            changed = True
        if changed:
            raise CorporateError(409, 'SNAPSHOT_REFERENCES_CHANGED',
                                 'Danh mục liên quan đã thay đổi. Hãy lấy lại trang 1.')
    if set(dependencies) - set(versions):
        raise CorporateError(503, 'REFERENCE_DATASET_NOT_READY',
                             'Danh mục liên quan chưa sẵn sàng. Vui lòng thử lại sau.', retry_after=300)
    for version in versions.values():
        try:
            ensure_fresh(version['read_at'], max_age_seconds)
        except CorporateError as exc:
            code = 'REFERENCE_DATASET_STALE' if exc.code == 'DATASET_STALE' else 'REFERENCE_SOURCE_TIME_INVALID'
            raise CorporateError(503, code,
                                 'Danh mục liên quan chưa được cập nhật hợp lệ. Vui lòng thử lại sau.',
                                 retry_after=300) from None
        except (TypeError, ValueError):
            raise CorporateError(503, 'REFERENCE_SOURCE_TIME_INVALID',
                                 'Danh mục liên quan chưa được cập nhật hợp lệ. Vui lòng thử lại sau.',
                                 retry_after=300) from None


def operation_day(value):
    """Index actual source timestamps in Vietnam time; never invent a change date."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TZ)
    return parsed.strftime('%Y%m%d')


def customer_created_day(row):
    """S customer query dates refer only to the original metadata.createdDate."""
    metadata = row.get('metadata') if isinstance(row, dict) else None
    if not isinstance(metadata, dict):
        raise ValueError('Customer creation-date metadata is unavailable.')
    return operation_day(metadata.get('createdDate'))


def state_path():
    control = Path(os.environ.get('DASHBOARD_STATE_PATH') or Path(__file__).parents[1] / '.data' / 'control.sqlite3')
    return Path(os.environ.get('CORPORATE_EXPORT_PATH') or control.parent / 'corporate-exports.sqlite3')


class ExportStore:
    def __init__(self, path=None):
        self._local = threading.local()
        self.path = Path(path or state_path()).resolve()
        forbidden = {Path(os.environ.get('DASHBOARD_STATE_PATH') or Path(__file__).parents[1] / '.data' / 'control.sqlite3').resolve(),
                     Path(os.environ.get('CORPORATE_STATE_PATH') or self.path.parent / 'corporate.sqlite3').resolve()}
        if self.path in forbidden:
            raise ValueError('Export and authentication stores must use separate files.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS export_versions (
                    snapshot_id TEXT PRIMARY KEY, resource TEXT NOT NULL, company_id TEXT NOT NULL,
                    read_at TEXT NOT NULL, published_at TEXT NOT NULL, coverage TEXT NOT NULL,
                    warnings TEXT NOT NULL, digest TEXT NOT NULL, rule_version TEXT NOT NULL,
                    current INTEGER NOT NULL CHECK(current IN (0,1))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_export
                    ON export_versions(resource,company_id) WHERE current=1;
                CREATE TABLE IF NOT EXISTS export_rows (
                    snapshot_id TEXT NOT NULL REFERENCES export_versions(snapshot_id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL, identity TEXT, finish_date TEXT, changed_date TEXT,
                    ship_id TEXT, method_id TEXT, cargo_type_id TEXT, size_id TEXT,
                    tax_code TEXT, customer_type TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id,seq)
                );
                CREATE INDEX IF NOT EXISTS export_period ON export_rows(snapshot_id,finish_date,seq);
                CREATE UNIQUE INDEX IF NOT EXISTS export_identity ON export_rows(snapshot_id,identity)
                    WHERE identity IS NOT NULL;
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(export_rows)')}
            for name in ('tax_code_known', 'customer_type_known'):
                if name not in columns:
                    db.execute(f'ALTER TABLE export_rows ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
            for name in ('created_date', 'modified_date'):
                if name not in columns:
                    db.execute(f'ALTER TABLE export_rows ADD COLUMN {name} TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS export_created ON export_rows(snapshot_id,created_date,seq)')
            db.execute('CREATE INDEX IF NOT EXISTS export_modified ON export_rows(snapshot_id,modified_date,seq)')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS export_row_date_variants (
                    snapshot_id TEXT NOT NULL, row_seq INTEGER NOT NULL, variant_seq INTEGER NOT NULL,
                    created_day TEXT, modified_day TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id,row_seq,variant_seq),
                    FOREIGN KEY(snapshot_id,row_seq) REFERENCES export_rows(snapshot_id,seq) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS export_variant_created
                    ON export_row_date_variants(snapshot_id,created_day,row_seq);
                CREATE INDEX IF NOT EXISTS export_variant_modified
                    ON export_row_date_variants(snapshot_id,modified_day,row_seq);
            ''')
            version_columns = {row['name'] for row in db.execute('PRAGMA table_info(export_versions)')}
            if 'source_windows' not in version_columns:
                db.execute('ALTER TABLE export_versions ADD COLUMN source_windows TEXT')
            if 'reference_catalogs' not in version_columns:
                db.execute('ALTER TABLE export_versions ADD COLUMN reference_catalogs TEXT')
            if 'customer_date_basis' not in version_columns:
                db.execute('ALTER TABLE export_versions ADD COLUMN customer_date_basis TEXT')
            # One-time migration of immutable old snapshots. GET must not parse
            # every row just to establish whether an optional FK is used.
            for version in db.execute('''SELECT snapshot_id,resource FROM export_versions
                    WHERE reference_catalogs IS NULL''').fetchall():
                references = OPERATION_REFERENCES.get(version['resource'])
                dependencies = operation_dependencies(version['resource'], (
                    json.loads(row['payload']) for row in db.execute(
                        'SELECT payload FROM export_rows WHERE snapshot_id=?', (version['snapshot_id'],))
                )) if references else []
                db.execute('UPDATE export_versions SET reference_catalogs=? WHERE snapshot_id=?',
                           (json.dumps(dependencies), version['snapshot_id']))
            # Legacy customer exports indexed the latest change date. Rebuild
            # only from original payload metadata, including retained snapshots.
            # Missing/invalid evidence stays NULL so filtered reads fail closed.
            for version in db.execute('''SELECT snapshot_id FROM export_versions WHERE resource='customers'
                    AND (customer_date_basis IS NULL OR customer_date_basis!=?)''',
                    (CUSTOMER_DATE_BASIS,)).fetchall():
                for row in db.execute('SELECT seq,payload FROM export_rows WHERE snapshot_id=?',
                                      (version['snapshot_id'],)):
                    try:
                        created = customer_created_day(json.loads(row['payload']))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        created = None
                    db.execute('UPDATE export_rows SET created_date=? WHERE snapshot_id=? AND seq=?',
                               (created, version['snapshot_id'], row['seq']))
                db.execute('UPDATE export_versions SET customer_date_basis=? WHERE snapshot_id=?',
                           (CUSTOMER_DATE_BASIS, version['snapshot_id']))
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        existing = getattr(self._local, 'connection', None)
        if existing is not None:
            yield existing
            return
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def atomic_publication(self):
        """All selected catalogs and production datasets publish or roll back together."""
        with self.db() as db:
            if db.in_transaction:
                raise ValueError('A publication is already running in this thread.')
            db.execute('BEGIN IMMEDIATE')
            self._local.connection = db
            # All resource versions in this atomic batch become visible at the
            # same time; FK publication order must not invalidate its snapshots.
            self._local.published_at = datetime.now(timezone.utc).isoformat()
            try:
                yield
            finally:
                del self._local.connection
                del self._local.published_at

    def publish(self, resource, company_id, rows, *, read_at, coverage=None,
                warnings=(), rule_version, retain=8, expected_current=ANY_VERSION, _source_windows=None,
                metadata_merge_policy=None):
        if resource not in MODELS or company_id != 'CNT':
            raise ValueError('Unknown resource or company.')
        if metadata_merge_policy is not None and (metadata_merge_policy != METADATA_MERGE_POLICY
                                                  or resource not in OPERATION_MODELS):
            raise ValueError('Invalid resource metadata merge policy.')
        if not 2 <= retain <= 32:
            raise ValueError('Retain between 2 and 32 export versions.')
        if len(rows) > MAX_ROWS:
            raise ValueError('Export exceeds the bounded row limit.')
        source_time = datetime.fromisoformat(read_at)
        if source_time.tzinfo is None:
            raise ValueError('Source read time must include a timezone.')
        intervals = coverage or []
        if resource in PRODUCTION and not intervals:
            raise ValueError('Production export must declare coverage, even when empty.')
        for start, end in intervals:
            datetime.strptime(start, '%Y%m%d')
            datetime.strptime(end, '%Y%m%d')
            if end < start:
                raise ValueError('Invalid coverage interval.')
        prepared, seen = [], set()
        for row in rows:
            variants = validate_variants(resource, row, metadata_merge_policy)
            public = {k: v for k, v in row.items() if not k.startswith('_')}
            validated = MODELS[resource].model_validate(public).model_dump(mode='json')
            if 'companyId' in validated and validated['companyId'] != company_id:
                raise ValueError('Cross-company row in export.')
            if resource in PRODUCTION:
                finish = validated['finishDate']
                datetime.strptime(finish, '%Y%m%d')
                if not any(a <= finish <= b for a, b in intervals):
                    raise ValueError('Production row outside declared coverage.')
                key = tuple((k, str(v)) for k, v in sorted(validated.items())
                            if k not in {'reportDate', 'containerWeight', 'containerTEU', 'bulkWeight'})
            else:
                key = validated[IDENTITY[resource]]
            if key in seen:
                raise ValueError('Duplicate export identity or aggregation dimensions.')
            seen.add(key)
            prepared.append((key, validated, row, variants))
        prepared.sort(key=lambda entry: (entry[1].get('finishDate', ''), str(entry[0])))
        dependencies = operation_dependencies(resource, (entry[1] for entry in prepared))
        digest = hashlib.sha256(json.dumps([
            {'payload': x[1], 'sourceDateVariants': x[3]} if x[3] else x[1] for x in prepared], ensure_ascii=False,
                                          sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        snapshot_id = uuid.uuid4().hex
        published = getattr(self._local, 'published_at', None) or datetime.now(timezone.utc).isoformat()
        with self.db() as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            if expected_current is not ANY_VERSION:
                current = db.execute('SELECT snapshot_id FROM export_versions WHERE resource=? AND company_id=? AND current=1',
                                     (resource, company_id)).fetchone()
                if (current['snapshot_id'] if current else None) != expected_current:
                    raise CorporateError(409, 'PUBLICATION_CONFLICT', 'Bộ dữ liệu đã thay đổi trong lúc công bố. Hãy chạy lại lệnh công bố.')
            db.execute('UPDATE export_versions SET current=0 WHERE resource=? AND company_id=?', (resource, company_id))
            db.execute('''INSERT INTO export_versions
                       (snapshot_id,resource,company_id,read_at,published_at,coverage,warnings,digest,rule_version,current,source_windows,reference_catalogs,customer_date_basis)
                       VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                       (snapshot_id, resource, company_id, read_at, published,
                        json.dumps(intervals), json.dumps(list(warnings), ensure_ascii=False), digest, rule_version,
                        json.dumps(_source_windows if _source_windows is not None
                                   else [[a, b, read_at] for a, b in intervals]), json.dumps(dependencies),
                        CUSTOMER_DATE_BASIS if resource == 'customers' else None))
            for seq, (_, data, internal, variants) in enumerate(prepared):
                payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                db.execute('''INSERT INTO export_rows(snapshot_id,seq,identity,finish_date,changed_date,ship_id,
                    method_id,cargo_type_id,size_id,tax_code,customer_type,payload,tax_code_known,customer_type_known,
                    created_date,modified_date)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (snapshot_id, seq, data.get(IDENTITY.get(resource, '')), data.get('finishDate'),
                            internal.get('_changedDate'), data.get('shipId'), data.get('handlingMethodId'),
                            data.get('cargoTypeId'), data.get('containerSizeId'), data.get('customerTaxCode'),
                            internal.get('_customerType'), payload,
                            int(internal.get('_taxCodeMapped', data.get('customerTaxCode') is not None)),
                            int(internal.get('_customerTypeMapped', internal.get('_customerType') is not None)),
                            (operation_day(data.get('createdDate')) if resource in OPERATION_MODELS
                             else customer_created_day(data) if resource == 'customers' else None),
                            operation_day(data.get('modifiedDate')) if resource in OPERATION_MODELS else None))
                for variant_seq, pair in enumerate(variants or []):
                    db.execute('''INSERT INTO export_row_date_variants
                        (snapshot_id,row_seq,variant_seq,created_day,modified_day,payload)
                        VALUES(?,?,?,?,?,?)''', (snapshot_id, seq, variant_seq,
                            operation_day(pair['createdDate']), operation_day(pair['modifiedDate']),
                            json.dumps(pair, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))
            db.execute('''DELETE FROM export_versions WHERE snapshot_id IN (
                SELECT snapshot_id FROM export_versions WHERE resource=? AND company_id=?
                ORDER BY current DESC,published_at DESC LIMIT -1 OFFSET ?)''', (resource, company_id, retain))
        return {'snapshotId': snapshot_id, 'rows': len(prepared), 'digest': digest}

    def publish_period(self, resource, company_id, rows, *, start, end, read_at, warnings=(), rule_version):
        """Replace a fully extracted period, including deletions, keep other days."""
        if resource not in PRODUCTION or end < start:
            raise ValueError('A production resource and valid replacement period are required.')
        previous = (datetime.strptime(start, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
        following = (datetime.strptime(end, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
        with self.db() as db:
            if not db.in_transaction:
                db.execute('BEGIN')
            version = db.execute('SELECT * FROM export_versions WHERE resource=? AND company_id=? AND current=1',
                                 (resource, company_id)).fetchone()
            if version and version['rule_version'] != rule_version:
                raise CorporateError(409, 'RULE_VERSION_CHANGED', 'Quy tắc ánh xạ đã thay đổi. Cần công bố lại toàn bộ kỳ với cùng quy tắc.')
            old_rows = [] if not version else [json.loads(row[0]) for row in db.execute('''SELECT payload FROM export_rows
                WHERE snapshot_id=? AND (finish_date<? OR finish_date>?) ORDER BY seq''', (version['snapshot_id'], start, end))]
        for row in rows:
            if not start <= row['finishDate'] <= end:
                raise ValueError('Replacement row outside the extracted period.')
        intervals = [[start, end]]
        if version:
            for a, b in json.loads(version['coverage']):
                if a < start:
                    intervals.append([a, min(b, previous)])
                if b > end:
                    intervals.append([max(a, following), b])
        merged = []
        for a, b in sorted(intervals):
            if merged and a <= (datetime.strptime(merged[-1][1], '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d'):
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        # Use the oldest extraction timestamp still represented by the data.
        source_windows = replace_window(version, start, end, read_at)
        if version and any(a < start or b > end for a, b in merged):
            read_at = min(read_at, version['read_at'], key=datetime.fromisoformat)
            warnings = list(dict.fromkeys([*json.loads(version['warnings']), *warnings]))
        return self.publish(resource, company_id, [*old_rows, *rows], read_at=read_at,
                            coverage=merged, warnings=warnings, rule_version=rule_version,
                            expected_current=version['snapshot_id'] if version else None,
                            _source_windows=source_windows)

    @staticmethod
    def _version(db, resource, company_id, snapshot_id):
        if snapshot_id:
            version = db.execute('SELECT * FROM export_versions WHERE snapshot_id=? AND resource=? AND company_id=?',
                                 (snapshot_id, resource, company_id)).fetchone()
            if version is None:
                raise CorporateError(410, 'SNAPSHOT_EXPIRED', 'Phiên dữ liệu đã hết hạn hoặc không thuộc phạm vi yêu cầu. Hãy lấy lại trang 1.')
        else:
            version = db.execute('SELECT * FROM export_versions WHERE resource=? AND company_id=? AND current=1',
                                 (resource, company_id)).fetchone()
            if version is None:
                raise CorporateError(503, 'DATASET_NOT_READY', 'Bộ dữ liệu chưa được đối chiếu và công bố cho API.', retry_after=300)
        return version

    def read(self, resource, query, *, identity=None, max_age_seconds=None):
        query.check_resource(resource)
        if query.page > 1 and not query.snapshotId:
            raise CorporateError(409, 'SNAPSHOT_REQUIRED', 'Truyền snapshotId của trang 1 để lấy các trang tiếp theo.')
        with self.db() as db:
            db.execute('BEGIN')  # Version selection/count/page share one SQLite read transaction.
            version = self._version(db, resource, query.companyId, query.snapshotId)
            if resource in PRODUCTION:
                covered = sorted(json.loads(version['coverage']))
                cursor = query.startDate
                for start, end in covered:
                    if start <= cursor <= end:
                        if end >= query.endDate:
                            break
                        cursor = (datetime.strptime(end, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
                else:
                    raise CorporateError(503, 'PERIOD_NOT_READY', 'Chưa công bố đủ dữ liệu cho toàn bộ kỳ yêu cầu.', retry_after=300)
            source_read_at = read_time(version, query.startDate if resource in PRODUCTION else None,
                                      query.endDate if resource in PRODUCTION else None)
            ensure_fresh(source_read_at, max_age_seconds)
            if resource in OPERATION_MODELS:
                try:
                    dependencies = json.loads(version['reference_catalogs'])
                    if (not isinstance(dependencies, list)
                            or any(target not in OPERATION_MODELS for target in dependencies)):
                        raise ValueError('Invalid catalog dependency metadata')
                except (TypeError, ValueError):
                    raise CorporateError(503, 'REFERENCE_METADATA_NOT_READY',
                                         'Chưa xác minh danh mục liên quan. Vui lòng thử lại sau.',
                                         retry_after=300) from None
                if version['current']:
                    ensure_dependencies_fresh(db, dependencies, query.companyId, max_age_seconds)
                else:
                    ensure_dependencies_fresh(db, dependencies, query.companyId, max_age_seconds,
                                              snapshot_published_at=version['published_at'])
            where, args = ['snapshot_id=?'], [version['snapshot_id']]
            for field, column in [('shipId', 'ship_id'), ('handlingMethodId', 'method_id'),
                                  ('cargoTypeId', 'cargo_type_id'), ('containerSizeId', 'size_id'),
                                  ('customerTaxCode', 'tax_code'), ('customerType', 'customer_type')]:
                value = getattr(query, field, None)
                if value is not None:
                    where.append(f'{column}=?')
                    args.append(value)
            if query.startDate and resource in OPERATION_MODELS:
                if db.execute('''SELECT 1 FROM export_rows WHERE snapshot_id=?
                        AND created_date IS NULL AND modified_date IS NULL LIMIT 1''',
                        (version['snapshot_id'],)).fetchone():
                    raise CorporateError(503, 'FILTER_NOT_READY', 'Danh mục còn thiếu ngày tạo/sửa để lọc kỳ đầy đủ.')
                where.append('''((created_date>=? AND created_date<=?) OR (modified_date>=? AND modified_date<=?)
                    OR EXISTS (SELECT 1 FROM export_row_date_variants d
                        WHERE d.snapshot_id=export_rows.snapshot_id AND d.row_seq=export_rows.seq
                        AND ((d.created_day>=? AND d.created_day<=?) OR (d.modified_day>=? AND d.modified_day<=?))))''')
                args.extend([query.startDate, query.endDate] * 4)
            elif query.startDate and resource == 'customers':
                if (version['customer_date_basis'] != CUSTOMER_DATE_BASIS
                        or db.execute('SELECT 1 FROM export_rows WHERE snapshot_id=? AND created_date IS NULL LIMIT 1',
                                      (version['snapshot_id'],)).fetchone()):
                    raise CorporateError(503, 'FILTER_NOT_READY',
                                         'Danh mục khách hàng thiếu ngày tạo nguồn hợp lệ để lọc kỳ đầy đủ.')
                where.extend(['created_date>=?', 'created_date<=?'])
                args.extend([query.startDate, query.endDate])
            elif query.startDate:
                date_column = 'finish_date' if resource in PRODUCTION else 'changed_date'
                if resource not in PRODUCTION and db.execute('SELECT 1 FROM export_rows WHERE snapshot_id=? AND changed_date IS NULL LIMIT 1',
                                                         (version['snapshot_id'],)).fetchone():
                    raise CorporateError(503, 'FILTER_NOT_READY', 'Danh mục còn thiếu ngày tạo/sửa để lọc kỳ đầy đủ.')
                where.extend([f'{date_column}>=?', f'{date_column}<=?'])
                args.extend([query.startDate, query.endDate])
            if resource == 'customers' and query.customerType and db.execute(
                    'SELECT 1 FROM export_rows WHERE snapshot_id=? AND customer_type_known=0 LIMIT 1',
                    (version['snapshot_id'],)).fetchone():
                raise CorporateError(503, 'FILTER_NOT_READY', 'Chưa có ánh xạ loại khách hàng đầy đủ để sử dụng customerType.')
            if resource == 'customers' and query.customerTaxCode and db.execute(
                    'SELECT 1 FROM export_rows WHERE snapshot_id=? AND tax_code_known=0 LIMIT 1',
                    (version['snapshot_id'],)).fetchone():
                raise CorporateError(503, 'FILTER_NOT_READY', 'Chưa có ánh xạ mã số thuế đầy đủ để sử dụng customerTaxCode.')
            if identity is not None:
                where.append('identity=?')
                args.append(identity)
            clause = ' AND '.join(where)
            total = db.execute(f'SELECT COUNT(*) FROM export_rows WHERE {clause}', args).fetchone()[0]
            if identity is not None and total == 0:
                raise CorporateError(404, 'NOT_FOUND', 'Không tìm thấy mã trong danh mục của công ty.')
            result = db.execute(f'SELECT seq,payload FROM export_rows WHERE {clause} ORDER BY seq LIMIT ? OFFSET ?',
                                [*args, query.limit, (query.page - 1) * query.limit]).fetchall()
            data = [json.loads(row['payload']) for row in result]
            if resource in OPERATION_MODELS and result:
                variants_by_seq = {}
                placeholders = ','.join('?' for _ in result)
                for item in db.execute(f'''SELECT row_seq,payload FROM export_row_date_variants
                        WHERE snapshot_id=? AND row_seq IN ({placeholders}) ORDER BY row_seq,variant_seq''',
                        [version['snapshot_id'], *(row['seq'] for row in result)]):
                    variants_by_seq.setdefault(item['row_seq'], []).append(json.loads(item['payload']))
                for item, row in zip(result, data):
                    if item['seq'] in variants_by_seq:
                        pair = matching_pair(variants_by_seq[item['seq']], query.startDate, query.endDate)
                        if pair is None:
                            raise CorporateError(503, 'FILTER_NOT_READY', 'Chưa xác minh đủ ngày nguồn để trả kết quả kỳ yêu cầu.')
                        row.update(pair)
        today = datetime.now(TZ).strftime('%Y-%m-%d' if resource in OPERATION_MODELS else '%Y%m%d')
        if 'reportDate' in MODELS[resource].model_fields:
            data = [{**row, 'reportDate': today} for row in data]
        return {'data': data, 'code': '1', 'message': 'Lấy dữ liệu thành công',
                'pagination': {'page': query.page, 'limit': query.limit, 'total': total,
                               'hasNext': query.page * query.limit < total,
                               'snapshotId': version['snapshot_id'], 'sourceReadAt': source_read_at,
                               'warnings': json.loads(version['warnings']), 'ruleVersion': version['rule_version']}}

    def describe(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('''SELECT v.resource,v.company_id,v.snapshot_id,
                v.read_at,v.coverage,v.warnings,v.rule_version,COUNT(r.seq) AS row_count
                FROM export_versions v LEFT JOIN export_rows r ON v.snapshot_id=r.snapshot_id
                WHERE v.current=1 GROUP BY v.snapshot_id ORDER BY v.resource''')]
