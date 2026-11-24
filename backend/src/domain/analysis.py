from collections import defaultdict


COLUMNS = (
    ("kind", "层级"), ("nozzle", "灌装头"), ("can", "罐号"), ("count", "完成试罐"),
    ("net", "扣重净量（g）"), ("q1", "Q1（g）"), ("q2", "中位数（g）"), ("q3", "Q3（g）"),
    ("spread", "四分位离散系数（%）"), ("skew", "Bowley 偏度"), ("tail", "低尾线（g）"),
    ("short", "短装数"), ("decision", "处置"),
)


def validate(row):
    if row["gross"] <= row["tare"]:
        raise ValueError("灌后毛重必须大于对应的空罐重量")


def quartile(ordered, probability):
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def analyze(rows, parameters):
    target = parameters.get("target", 328)
    multiplier = parameters.get("fence", 1.5)
    if not rows:
        raise ValueError("请先登记试灌称重")
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        validate(row)
        if row["can"] in seen:
            raise ValueError(f"重复罐号 {row['can']}，请只保留一组配对称重")
        seen.add(row["can"])
        groups[row["nozzle"]].append((round(row["gross"] - row["tare"], 4), row["can"]))
    table, chart = [], []
    short_count = tail_count = 0
    for nozzle, cans in sorted(groups.items()):
        ordered = sorted(cans)
        values = [value for value, _ in ordered]
        q1, median, q3 = [quartile(values, p) for p in (0.25, 0.5, 0.75)]
        iqr = q3 - q1
        lower = q1 - multiplier * iqr if len(values) >= 4 else None
        short = sum(value < target for value in values)
        table.append(dict(kind="灌装头", nozzle=nozzle, count=len(values), q1=round(q1, 3),
                          q2=round(median, 3), q3=round(q3, 3),
                          spread=round(100 * iqr / (q1 + q3), 4),
                          skew=round((q3 + q1 - 2 * median) / iqr, 4) if iqr > 0 else None,
                          tail=round(lower, 3) if lower is not None else None, short=short,
                          decision="少于 4 罐，暂不判断低尾" if lower is None else "复核灌装头" if short else "当前达线"))
        chart.append((nozzle, round(100 * short / len(values), 2)))
        short_count += short
        for net, can in ordered:
            below = net < target
            unusual = lower is not None and net < lower
            if below or unusual:
                table.append(dict(kind="复称明细", nozzle=nozzle, can=can, net=net,
                                  decision="短装与低尾" if below and unusual else "短装" if below else "低尾异常"))
            tail_count += unusual
    return report(f"{short_count} 罐低于试制净量线",
                  f"以 {target:g} g 判定短装，以 Q1−{multiplier:g}×IQR 找同喷头低尾。复称时保留原罐号和空罐重量。",
                  [("试灌称重", len(rows), "罐"), ("短装", short_count, "罐"), ("低尾异常", tail_count, "罐")],
                  COLUMNS, table, "各灌装头短装占比", chart, "%")


def seed():
    rows = []
    for nozzle, center in (("喷头-1", 332), ("喷头-2", 329), ("喷头-3", 334)):
        for index in range(16):
            tare = 15.1 + (index * 7 % 4) / 10
            net = center + (index * 5 % 11 - 5) / 3 - (11 if index == 13 else 0)
            rows.append(dict(nozzle=nozzle, can=f"QY-2607-{nozzle[-1]}{index+1:02d}",
                             tare=round(tare, 1), gross=round(net + tare, 3)))
    return rows


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit=""):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
