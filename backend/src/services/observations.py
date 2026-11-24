import csv
import hashlib
import io
import json
import re
from email.parser import BytesParser
from email.policy import default as email_default_policy

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.project import SPEC
from src.domain import analysis
from src.models.workflow import AuditEvent, Observation
from src.services.errors import BusinessError
from src.services.validation import validated


MAX_OBSERVATIONS = 5000

FIELD_ALIASES = {
    '灌装头': 'nozzle',
    '喷头': 'nozzle',
    '灌装头编号': 'nozzle',
    '喷头编号': 'nozzle',
    'filling_head': 'nozzle',
    'nozzle_id': 'nozzle',
    'nozzle_name': 'nozzle',
    'head': 'nozzle',
    'filler': 'nozzle',
    '罐号': 'can',
    '罐编号': 'can',
    'can_no': 'can',
    'can_number': 'can',
    'can_id': 'can',
    'can_code': 'can',
    'can_name': 'can',
    'tank_no': 'can',
    'tank_number': 'can',
    'tank_id': 'can',
    '空罐称重（g）': 'tare',
    '空罐称重(g)': 'tare',
    '空罐重量（g）': 'tare',
    '空罐重量(g)': 'tare',
    '空重': 'tare',
    '皮重': 'tare',
    '空罐重量': 'tare',
    'tare_weight': 'tare',
    'empty_weight': 'tare',
    'can_weight': 'tare',
    '灌后毛重（g）': 'gross',
    '灌后毛重(g)': 'gross',
    '毛重': 'gross',
    '总重': 'gross',
    'gross_weight': 'gross',
    'total_weight': 'gross',
    'filled_weight': 'gross',
    '实称时间': 'weighed_at',
    '称重时间': 'weighed_at',
    '称量时间': 'weighed_at',
    '过磅时间': 'weighed_at',
    '时间': 'weighed_at',
    'weighed_time': 'weighed_at',
    'weighing_at': 'weighed_at',
    'weighing_time': 'weighed_at',
    'weigh_time': 'weighed_at',
    'weight_at': 'weighed_at',
    'weight_time': 'weighed_at',
    'weighted_at': 'weighed_at',
    'weighted_time': 'weighed_at',
    'measured_at': 'weighed_at',
    'measured_time': 'weighed_at',
    'measure_at': 'weighed_at',
    'measure_time': 'weighed_at',
    'observed_at': 'weighed_at',
    'observation_time': 'weighed_at',
    'recorded_at': 'weighed_at',
    'record_time': 'weighed_at',
    'reading_at': 'weighed_at',
    'reading_time': 'weighed_at',
    'actual_at': 'weighed_at',
    'actual_time': 'weighed_at',
    'date': 'weighed_at',
    'timestamp': 'weighed_at',
    'time': 'weighed_at',
    '称重类型': 'weighing_type',
    '记录类型': 'weighing_type',
    '类型': 'weighing_type',
    '称次': 'weighing_type',
    'weighingtype': 'weighing_type',
    'weight_type': 'weighing_type',
    'measure_type': 'weighing_type',
    'measurement_type': 'weighing_type',
    'reading_type': 'weighing_type',
    'record_type': 'weighing_type',
    'entry_type': 'weighing_type',
    'stage': 'weighing_type',
    'phase': 'weighing_type',
    'type': 'weighing_type',
}


def canonical_field_name(name):
    raw = str(name).strip().lstrip("﻿")
    if raw in FIELD_ALIASES:
        return FIELD_ALIASES[raw]
    spaced = re.sub(r'(?<!^)(?=[A-Z])', '_', raw).lower()
    normalized = re.sub(r'[\s\-]+', '_', spaced)
    normalized = re.sub(r'_+', '_', normalized).strip('_')
    if normalized in FIELD_ALIASES:
        return FIELD_ALIASES[normalized]
    if ('时间' in raw or '时刻' in raw or '日期' in raw) and any(word in raw for word in ('称', '秤', '量', '测', '读')):
        return 'weighed_at'
    if any(word in normalized for word in ('weigh', 'weight', 'measure', 'read')) and any(
            suffix in normalized for suffix in ('_at', '_time', '_date', 'time', 'date')):
        return 'weighed_at'
    if any(word in normalized for word in ('weigh', 'weight', 'measure', 'read')) and any(
            suffix in normalized for suffix in ('type', 'kind', 'stage', 'phase', 'mode', 'category')):
        return 'weighing_type'
    return normalized


def normalize_field_aliases(raw):
    row = {}
    for key, value in raw.items():
        if key is None:
            target = None
        else:
            target = canonical_field_name(key)
        if target not in row or (row[target] in (None, '') and value not in (None, '')):
            row[target] = value
    return row


def clean_observation(raw):
    row = validated(SPEC['fields'], normalize_field_aliases(raw))
    try:
        if hasattr(analysis, 'validate'):
            analysis.validate(row)
    except ValueError as exc:
        raise BusinessError(str(exc)) from exc
    return row


def content_key(row):
    return 'data:' + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_csv(raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if len(raw) > 512 * 1024:
        raise BusinessError('CSV 文件上限 512 KB', 413)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        header = reader.fieldnames or []
        normalized_header = [canonical_field_name(item) for item in header]
        known = {item['key'] for item in SPEC['fields']}
        required = {item['key'] for item in SPEC['fields'] if item.get('required', True)}
        if (len(set(header)) != len(header) or not required.issubset(normalized_header)
                or not set(normalized_header).issubset(known)):
            raise BusinessError('CSV 表头与导入模板不匹配')
        rows = []
        for row in reader:
            try:
                if None in row or any(value is None for value in row.values()):
                    raise BusinessError('列数与表头不匹配')
                rows.append(clean_observation(row))
            except BusinessError as exc:
                raise BusinessError(f'CSV 第 {reader.line_num} 行，{exc}') from exc
            if len(rows) > 500:
                raise BusinessError('单次导入上限 500 条')
        if not rows:
            raise BusinessError('CSV 里没有观测记录')
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BusinessError('请上传 UTF-8 编码的有效 CSV') from exc


def extract_multipart_file(body, content_type):
    header = f'Content-Type: {content_type}\r\n\r\n'.encode()
    try:
        message = BytesParser(policy=email_default_policy).parsebytes(header + bytes(body))
    except Exception as exc:
        raise BusinessError('无法读取上传的 CSV 文件') from exc
    for part in message.walk():
        disposition = str(part.get('Content-Disposition') or '')
        filename = part.get_filename()
        if 'form-data' in disposition and filename:
            payload = part.get_payload(decode=True)
            if payload is not None:
                return payload
    raise BusinessError('请选择要导入的 CSV 文件', 422)


def add_observations(db, rows, actor, action):
    keyed = {content_key(row): row for row in rows}
    existing = set(db.scalars(select(Observation.source_key).where(Observation.source_key.in_(keyed))))
    fresh = {key: row for key, row in keyed.items() if key not in existing}
    if db.scalar(select(func.count(Observation.id))) + len(fresh) > MAX_OBSERVATIONS:
        raise BusinessError('当前项目最多保留 5000 条观测记录', 409)
    items = [Observation(source_key=key, payload=row) for key, row in fresh.items()]
    db.add_all(items)
    if items:
        db.add(AuditEvent(actor=actor, action=action, detail=f'新增 {len(items)} 条观测'))
    try:
        db.flush()
    except IntegrityError as exc:
        raise BusinessError('有相同记录刚刚写入，请刷新后重试', 409) from exc
    return items
