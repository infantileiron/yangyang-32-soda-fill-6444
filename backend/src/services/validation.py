import math
from datetime import date, datetime, timedelta, timezone

from src.services.errors import BusinessError

BEIJING_TZ = timezone(timedelta(hours=8))


def validated(fields, supplied, *, defaults=False):
    extra = set(supplied) - {item['key'] for item in fields}
    if extra:
        raise BusinessError(f'无法识别字段 {", ".join(sorted(extra))}')
    clean = {}
    for field in fields:
        key, label, kind = (field[prop] for prop in ('key', 'label', 'type'))
        value = supplied.get(key, field.get('default') if defaults else None)
        if value is None or str(value).strip() == '':
            if field.get('required', True):
                raise BusinessError(f'请填写「{label}」')
            continue
        try:
            if kind in ('number', 'integer'):
                if isinstance(value, bool):
                    raise ValueError
                value = float(value)
                if not math.isfinite(value) or value < field.get('min', -1e12) or value > field.get('max', 1e12):
                    raise ValueError
                if kind == 'integer':
                    if not value.is_integer():
                        raise ValueError
                    value = int(value)
            elif kind == 'date':
                value = date.fromisoformat(str(value)).isoformat()
            elif kind == 'datetime':
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    timestamp = float(value)
                    if timestamp > 1e12:
                        timestamp /= 1000.0
                    parsed = datetime.fromtimestamp(timestamp, BEIJING_TZ).replace(tzinfo=None)
                else:
                    text = str(value).strip().replace('Z', '+00:00')
                    try:
                        parsed = datetime.fromisoformat(text)
                    except ValueError:
                        parsed = None
                        for pattern in ('%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S',
                                        '%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M',
                                        '%Y/%m/%d', '%Y-%m-%d',
                                        '%Y年%m月%d日 %H时%M分%S秒',
                                        '%Y年%m月%d日 %H时%M分',
                                        '%Y年%m月%d日 %H:%M:%S',
                                        '%Y年%m月%d日 %H:%M', '%Y年%m月%d日'):
                            try:
                                parsed = datetime.strptime(text, pattern)
                                break
                            except ValueError:
                                continue
                        if parsed is None:
                            raise
                value = parsed.isoformat()
            elif kind == 'choice':
                if value not in field['choices']:
                    raise ValueError
            elif not isinstance(value, str) or len(value.strip()) > 300:
                raise ValueError
            else:
                value = value.strip()
        except (TypeError, ValueError, OverflowError) as exc:
            raise BusinessError(f'「{label}」的格式或取值范围不正确') from exc
        clean[key] = value
    return clean
