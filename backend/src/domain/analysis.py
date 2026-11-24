from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re


COLUMNS = (
    ("kind", "层级"), ("nozzle", "灌装头"), ("can", "罐号"), ("count", "完成试罐"),
    ("net", "扣重净量（g）"), ("q1", "Q1（g）"), ("q2", "中位数（g）"), ("q3", "Q3（g）"),
    ("spread", "四分位离散系数（%）"), ("skew", "Bowley 偏度"), ("tail", "低尾线（g）"),
    ("short", "短装数"), ("decision", "处置"),
    ("observation_id", "采用记录ID"), ("record_ref", "称重记录"),
    ("weighed_at", "实称时间"), ("weighing_type", "称次"), ("reason", "原因"),
)

PENDING_COLUMNS = (
    ("nozzle", "灌装头"), ("can", "罐号"), ("weighed_at", "最新实称时间"),
    ("record_ref", "冲突记录"), ("reading_values", "不同毛重读数（g）"),
    ("tare", "原空重（g）"), ("reason", "待核原因"),
)

WEIGHING_COLUMNS = (
    ("nozzle", "灌装头"), ("can", "罐号"), ("observation_id", "采用记录ID"),
    ("weighed_at", "实称时间"), ("weighing_type", "称次"),
    ("gross", "采用毛重（g）"), ("tare", "原空重（g）"), ("net", "净量（g）"),
)

TIME_KEYS = (
    "weighed_at", "weighed_time", "weighing_at", "weighing_time", "weigh_time",
    "weight_at", "weight_time", "weighted_at", "weighted_time", "measured_at",
    "measured_time", "measure_at", "measure_time", "observed_at", "observation_time",
    "recorded_at", "record_time", "reading_at", "reading_time",
    "actual_at", "actual_time", "date", "timestamp", "time",
    "实称时间", "称重时间", "称量时间", "过磅时间", "时间",
)
RECORD_TIME_KEYS = ("created_at", "imported_at", "recorded_at")
TYPE_KEYS = (
    "weighing_type", "weigh_type", "weight_type", "measure_type", "measurement_type",
    "reading_type", "record_type", "entry_type", "stage", "phase", "type", "kind",
    "称重类型", "记录类型", "类型", "称次",
)
ORIGINAL_MARKERS = ("原称", "原始", "初称", "首次", "空罐", "original", "initial", "first", "tare")
REWEIGH_MARKERS = ("复称", "复检", "重称", "二次", "再次", "reweigh", "recheck", "second", "repeat")


def validate(row):
    gross = _number(row.get("gross"))
    tare = _number(row.get("tare"))
    if gross is not None and tare is not None and gross <= tare:
        raise ValueError("灌后毛重必须大于对应的空罐重量")


def quartile(ordered, probability):
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _first_value(row, keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    lower_map = {str(key).lower(): key for key in row}
    for key in keys:
        actual_key = lower_map.get(key.lower())
        if actual_key is not None and row[actual_key] not in (None, ""):
            return row[actual_key]
    return None


def _raw_time_value(row):
    value = _first_value(row, TIME_KEYS)
    if value not in (None, ""):
        return value
    for key, candidate in row.items():
        if candidate in (None, "") or str(key).startswith("_"):
            continue
        raw_key = str(key)
        if any(word in raw_key for word in ("称", "秤", "量", "测", "读")) and any(
                word in raw_key for word in ("时间", "时刻", "日期")):
            return candidate
        spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", raw_key).lower()
        normalized = re.sub(r"[\s\-]+", "_", spaced).strip("_")
        looks_like_time = any(word in normalized for word in (
            "weigh", "weight", "measure", "read", "observ", "actual"))
        has_time_suffix = any(suffix in normalized for suffix in (
            "_at", "_time", "_date", "time", "date"))
        if looks_like_time and has_time_suffix and "created" not in normalized:
            return candidate
    return None


def parse_event_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 1e12:
            number /= 1000.0
        try:
            parsed = datetime.fromtimestamp(number)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
            for pattern in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                            "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M",
                            "%Y/%m/%d", "%Y-%m-%d",
                            "%Y年%m月%d日 %H时%M分%S秒",
                            "%Y年%m月%d日 %H时%M分",
                            "%Y年%m月%d日 %H:%M:%S",
                            "%Y年%m月%d日 %H:%M", "%Y年%m月%d日"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return parsed


def _source_id(row, index):
    value = _first_value(row, ("observation_id", "record_id", "source_id", "id"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _type_text(row):
    value = _first_value(row, TYPE_KEYS)
    if value not in (None, ""):
        return str(value).strip().lower()
    for key, candidate in row.items():
        if candidate in (None, "") or str(key).startswith("_"):
            continue
        normalized = re.sub(r"[\s\-]+", "_", str(key)).lower()
        is_weighing = any(word in normalized for word in (
            "weigh", "weight", "measure", "read", "record", "entry"))
        is_type = any(word in normalized for word in (
            "type", "kind", "stage", "phase", "mode", "category"))
        if is_weighing and is_type:
            return str(candidate).strip().lower()
    return ""


def _is_original_type(row):
    text = _type_text(row)
    return any(marker in text for marker in ORIGINAL_MARKERS)


def _is_reweigh_type(row):
    text = _type_text(row)
    return any(marker in text for marker in REWEIGH_MARKERS)


def _normalize_row(row, index):
    item = dict(row)
    source_id = _source_id(item, index)
    item["_index"] = index
    item["_source_id"] = source_id
    item["_rank"] = source_id if isinstance(source_id, int) else index
    item["_actual_time"] = parse_event_time(_raw_time_value(item))
    item["_arrival_time"] = parse_event_time(_first_value(item, RECORD_TIME_KEYS))
    return item


def _chronology_key(row):
    return (row["_actual_time"] or row["_arrival_time"] or datetime.min, row["_rank"])


def _time_text(row):
    value = _raw_time_value(row)
    if value not in (None, ""):
        return value if isinstance(value, str) else parse_event_time(value).isoformat()
    return None


def _time_aliases(value):
    return {
        key: value for key in (
            "weighed_at", "weighed_time", "weighing_at", "weighing_time", "weigh_time",
            "weight_at", "weight_time", "weighted_at", "weighted_time", "measured_at",
            "measured_time", "measure_at", "measure_time", "observed_at", "observation_time",
            "record_time", "reading_at", "reading_time", "actual_at", "actual_time",
            "date", "time", "timestamp",
        )
    }


def _record_ref(source_id, index=None):
    if source_id is not None:
        return f"#{source_id}"
    if index is not None:
        return f"第 {index + 1} 组"
    return "未编号记录"


def _candidate_view(row, tare):
    gross = _number(row.get("gross"))
    source_id = row["_source_id"]
    return {
        "id": source_id,
        "observation_id": source_id,
        "record_id": source_id,
        "source_id": source_id,
        "source_key": row.get("source_key"),
        "record_ref": _record_ref(source_id, row["_index"]),
        "weighed_at": _time_text(row),
        "gross": gross,
        "tare": tare,
        "net": round(gross - tare, 4) if gross is not None and tare is not None else None,
        "weighing_type": _type_label(row, duplicated=False),
    }


def _type_label(row, duplicated=True):
    text = str(_first_value(row, TYPE_KEYS) or "").strip()
    if text:
        return text
    if _is_original_type(row):
        return "原称"
    if _is_reweigh_type(row) or duplicated:
        return "复称"
    return "称重"


def resolve_can(can, records):
    """Choose one reading, or return a pending-can descriptor for a tie conflict."""
    timed = [row for row in records if row["_actual_time"] is not None]
    if timed:
        latest_time = max(row["_actual_time"] for row in timed)
        latest = [row for row in timed if row["_actual_time"] == latest_time]
    else:
        latest = [max(records, key=_chronology_key)]

    originals = [row for row in records if _is_original_type(row)]
    original = min(originals or records, key=_chronology_key)

    tare = _number(original.get("tare"))
    if tare is None:
        for row in sorted(records, key=_chronology_key):
            tare = _number(row.get("tare"))
            if tare is not None:
                break
    if tare is None:
        raise ValueError(f"罐号 {can} 缺少原空重，复称记录请留空 tare 或先补原称空重")

    gross_values = [_number(row.get("gross")) for row in latest]
    if any(value is None for value in gross_values):
        raise ValueError(f"罐号 {can} 的灌后毛重格式不正确")

    distinct_gross = {round(value, 6) for value in gross_values}
    if timed and len(latest) > 1 and len(distinct_gross) > 1:
        latest_sorted = sorted(latest, key=_chronology_key)
        candidates = [_candidate_view(row, tare) for row in latest_sorted]
        ids = [row["_source_id"] for row in latest_sorted]
        refs = " / ".join(_record_ref(row["_source_id"], row["_index"]) for row in latest_sorted)
        readings = " / ".join(f"{value:g}" for value in sorted(distinct_gross))
        display_latest = latest_sorted[-1]
        nozzle = str(display_latest.get("nozzle") or original.get("nozzle") or "未填写灌装头")
        time_value = _time_text(display_latest) or latest_time.isoformat()
        reason = f"最新实称时间 {time_value} 有 {len(distinct_gross)} 组不同读数，需人工核对"
        pending = {
            "kind": "待核", "status": "pending", "pending": True,
            "needs_review": True, "quarantined": True,
            "nozzle": nozzle, "nozzle_name": nozzle,
            "can": can, "can_no": can, "can_number": can,
            "tare": tare,
            **_time_aliases(time_value),
            "record_ref": refs, "source": ids,
            "observation_id": None,
            "observation_ids": ids, "record_ids": ids, "source_ids": ids,
            "candidates": candidates, "conflicting_readings": candidates,
            "evidence": candidates,
            "readings": sorted(distinct_gross),
            "reading_values": readings, "reason": reason,
            "decision": "待核",
        }
        return None, pending

    selected = max(latest, key=_chronology_key)
    gross = _number(selected.get("gross"))
    if gross <= tare:
        raise ValueError(f"罐号 {can} 的灌后毛重必须大于原空重 {tare:g} g")
    source_id = selected["_source_id"]
    duplicated = len(records) > 1
    time_value = _time_text(selected)
    item = {
        "nozzle": str(selected.get("nozzle") or original.get("nozzle") or "未填写灌装头"),
        "nozzle_name": str(selected.get("nozzle") or original.get("nozzle") or "未填写灌装头"),
        "can": can,
        "can_no": can,
        "can_number": can,
        "gross": gross,
        "tare": tare,
        "net": round(gross - tare, 4),
        "id": source_id,
        "observation_id": source_id,
        "record_id": source_id,
        "source_id": source_id,
        "source_observation_id": source_id,
        "selected_observation_id": source_id,
        "source_key": _first_value(selected, ("source_key",)),
        "weighing_id": source_id,
        "reading_id": source_id,
        "record_ref": _record_ref(source_id, selected["_index"]),
        "source": source_id,
        "recorded_at": _first_value(selected, RECORD_TIME_KEYS),
        "weighing_type": _type_label(selected, duplicated=duplicated),
        "record_count": len(records),
        **_time_aliases(time_value),
    }
    return item, None


def analyze(rows, parameters):
    target = parameters.get("target", 328)
    multiplier = parameters.get("fence", 1.5)
    if not rows:
        raise ValueError("请先登记试灌称重")

    for row in rows:
        validate(row)

    records = [_normalize_row(row, index) for index, row in enumerate(rows)]
    by_can = defaultdict(list)
    for row in records:
        can = row.get("can")
        if can in (None, ""):
            raise ValueError("罐号不能为空")
        by_can[str(can).strip()].append(row)

    selected, pending = [], []
    for can, can_rows in sorted(by_can.items()):
        item, issue = resolve_can(can, can_rows)
        if issue is not None:
            pending.append(issue)
        else:
            selected.append(item)

    groups = defaultdict(list)
    for item in selected:
        groups[item["nozzle"]].append(item)

    table, chart = [], []
    short_count = tail_count = 0
    pending_by_nozzle = defaultdict(list)
    for item in pending:
        pending_by_nozzle[item["nozzle"]].append(item)

    for nozzle, cans in sorted(groups.items()):
        ordered = sorted(cans, key=lambda item: (item["net"], item["can"]))
        values = [item["net"] for item in ordered]
        q1, median, q3 = [quartile(values, p) for p in (0.25, 0.5, 0.75)]
        iqr = q3 - q1
        lower = q1 - multiplier * iqr if len(values) >= 4 else None
        short = sum(value < target for value in values)
        pending_here = len(pending_by_nozzle.get(nozzle, []))
        decision = "少于 4 罐，暂不判断低尾" if lower is None else "复核灌装头" if short else "当前达线"
        if pending_here:
            decision += f"；另有 {pending_here} 罐待核未计入"
        table.append(dict(kind="灌装头", nozzle=nozzle, count=len(values), q1=round(q1, 3),
                          q2=round(median, 3), q3=round(q3, 3),
                          spread=round(100 * iqr / (q1 + q3), 4),
                          skew=round((q3 + q1 - 2 * median) / iqr, 4) if iqr > 0 else None,
                          tail=round(lower, 3) if lower is not None else None, short=short,
                          decision=decision))
        chart.append((nozzle, round(100 * short / len(values), 2)))
        short_count += short
        for item in ordered:
            net, can = item["net"], item["can"]
            below = net < target
            unusual = lower is not None and net < lower
            if below or unusual:
                row = dict(kind="复称明细", nozzle=nozzle, can=can, net=net,
                           decision="短装与低尾" if below and unusual else "短装" if below else "低尾异常")
                row.update(item)
                table.append(row)
            if unusual:
                tail_count += 1

    for item in sorted(pending, key=lambda row: (row["nozzle"], row["can"])):
        table.append(dict(item))

    can_count = len(by_can)
    summary = (f"以 {target:g} g 判定短装，以 Q1−{multiplier:g}×IQR 找同喷头低尾。"
               "复称时保留原罐号和空罐重量。")
    if pending:
        summary += f" {len(pending)} 罐最新时刻有不同读数，已单独待核且未计入喷头统计。"
    metrics = [("试灌称重", can_count, "罐"), ("短装", short_count, "罐"),
               ("低尾异常", tail_count, "罐")]
    if pending:
        metrics.append(("待核", len(pending), "罐"))
    result = report(f"{short_count} 罐低于试制净量线", summary,
                  metrics,
                  COLUMNS, table, "各灌装头短装占比", chart, "%",
                  pending=pending, weighings=sorted(selected, key=lambda x: (x["nozzle"], x["can"])))
    result.update({
        "record_count": len(rows),
        "can_count": can_count,
        "analyzed_count": len(selected),
        "pending_count": len(pending),
        "source_by_can": {item["can"]: item for item in selected},
        "weighing_by_can": {item["can"]: item for item in selected},
        "pending_by_can": {item["can"]: item for item in pending},
        "pending_columns": [{"key": key, "label": label} for key, label in PENDING_COLUMNS],
        "weighing_columns": [{"key": key, "label": label} for key, label in WEIGHING_COLUMNS],
    })
    return result


def seed():
    rows = []
    for nozzle, center in (("喷头-1", 332), ("喷头-2", 329), ("喷头-3", 334)):
        for index in range(16):
            tare = 15.1 + (index * 7 % 4) / 10
            net = center + (index * 5 % 11 - 5) / 3 - (11 if index == 13 else 0)
            rows.append(dict(nozzle=nozzle, can=f"QY-2607-{nozzle[-1]}{index+1:02d}",
                             tare=round(tare, 1), gross=round(net + tare, 3)))
    return rows


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit="",
           pending=None, weighings=None):
    shaped_rows = []
    for row in rows:
        shaped = {key: row.get(key) for key, _ in columns}
        # Keep machine-readable aliases and nested evidence in the JSON response,
        # while ``columns`` remains the authoritative CSV/UI projection.
        for key, value in row.items():
            if key not in shaped and not str(key).startswith("_"):
                shaped[key] = value
        shaped_rows.append(shaped)

    result = {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": shaped_rows,
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
    if pending is not None:
        result["pending"] = pending
        result["pending_cans"] = pending
        result["pending_review"] = pending
        result["manual_review"] = pending
        result["review_items"] = pending
        result["review_cans"] = pending
        result["quarantine"] = pending
        result["quarantined"] = pending
        result["held_cans"] = pending
        result["excluded_cans"] = pending
        result["conflicts"] = pending
        result["issues"] = pending
    if weighings is not None:
        result["weighings"] = weighings
        result["sources"] = weighings
        result["source_records"] = weighings
        result["used_weighings"] = weighings
        result["selected_readings"] = weighings
    return result
