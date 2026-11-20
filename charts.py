# -*- coding: utf-8 -*-

import math
import html as _html


def _esc(s):
    return _html.escape(str(s))


def donut(segments, size=220, hole=0.6, title=""):
    """环形图。segments=[(label, value, color), ...]"""
    total = sum(v for _, v, _ in segments) or 1
    cx = cy = size / 2
    r = size / 2 - 10
    inner = r * hole
    start = -90.0  # 从顶部开始
    paths = []
    for label, value, color in segments:
        if value <= 0:
            continue
        frac = value / total
        end = start + frac * 360
        large = 1 if frac > 0.5 else 0
        x1 = cx + r * math.cos(math.radians(start))
        y1 = cy + r * math.sin(math.radians(start))
        x2 = cx + r * math.cos(math.radians(end))
        y2 = cy + r * math.sin(math.radians(end))
        xi2 = cx + inner * math.cos(math.radians(end))
        yi2 = cy + inner * math.sin(math.radians(end))
        xi1 = cx + inner * math.cos(math.radians(start))
        yi1 = cy + inner * math.sin(math.radians(start))
        d = (f"M{x1:.2f},{y1:.2f} A{r:.2f},{r:.2f} 0 {large} 1 {x2:.2f},{y2:.2f} "
             f"L{xi2:.2f},{yi2:.2f} A{inner:.2f},{inner:.2f} 0 {large} 0 {xi1:.2f},{yi1:.2f} Z")
        paths.append(f'<path d="{d}" fill="{color}"/>')
        start = end
    center_txt = (f'<text x="{cx}" y="{cy-4}" text-anchor="middle" '
                  f'font-size="26" font-weight="700" fill="#1e293b">{total}</text>'
                  f'<text x="{cx}" y="{cy+18}" text-anchor="middle" '
                  f'font-size="12" fill="#64748b">{_esc(title)}</text>')
    return (f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            + "".join(paths) + center_txt + "</svg>")


def bar(items, width=520, height=220, color="#3b82f6", onclick=None):
    """纵向柱状图。items=[(label, value), ...]
    onclick:JS 函数名(可选)。给出后每根柱+整列透明热区可点击,调用 onclick('标签'),
    用于「产品型号分布」点柱下钻该型号工单。"""
    if not items:
        return ""
    pad_l, pad_b, pad_t = 36, 28, 12
    maxv = max(v for _, v in items) or 1
    n = len(items)
    plot_w = width - pad_l - 12
    plot_h = height - pad_b - pad_t
    bw = plot_w / n * 0.55
    gap = plot_w / n
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    # y 轴刻度线
    for i in range(5):
        y = pad_t + plot_h * i / 4
        val = maxv * (1 - i / 4)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-12}" y2="{y:.1f}" stroke="#eef2f7"/>')
        parts.append(f'<text x="{pad_l-6}" y="{y+4:.1f}" text-anchor="end" font-size="10" fill="#94a3b8">{val:.0f}</text>')
    for i, (label, v) in enumerate(items):
        x = pad_l + gap * i + (gap - bw) / 2
        bh = plot_h * (v / maxv)
        y = pad_t + plot_h - bh
        rect = f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{color}"/>'
        txt = f'<text x="{x+bw/2:.1f}" y="{height-10}" text-anchor="middle" font-size="11" fill="#475569">{_esc(label)}</text>'
        if onclick:
            lab_js = str(label).replace("\\", "\\\\").replace("'", "\\'")
            hot = f'<rect x="{pad_l+gap*i:.1f}" y="{pad_t}" width="{gap:.1f}" height="{plot_h:.1f}" fill="transparent"/>'
            ttl = f'<title>{_esc(label)} · 点击查看工单</title>'
            parts.append(f'<g class="barclick" style="cursor:pointer;" onclick="{onclick}(\'{lab_js}\')">{ttl}{hot}{rect}{txt}</g>')
        else:
            parts.append(rect)
            parts.append(txt)
    parts.append("</svg>")
    return "".join(parts)


def hbar(series, labels, colors, width=520, height=240):
    """横向堆叠柱状图(负责人工作量)。
    series = {"待开始":[..], "进行中":[..], "已完成":[..]} 每个 list 与 labels 等长
    colors = {"待开始":"#94a3b8", ...}
    """
    if not labels:
        return ""
    pad_l, pad_r, pad_t, pad_b = 56, 12, 12, 24
    totals = [sum(series[k][i] for k in series) for i in range(len(labels))]
    maxv = max(totals) or 1
    n = len(labels)
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    rh = plot_h / n * 0.55
    gap = plot_h / n
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    for i, lab in enumerate(labels):
        y = pad_t + gap * i + (gap - rh) / 2
        x = pad_l
        parts.append(f'<text x="{pad_l-8}" y="{y+rh/2+4:.1f}" text-anchor="end" font-size="11" fill="#475569">{_esc(lab)}</text>')
        for k in series:
            v = series[k][i]
            if v <= 0:
                continue
            w = plot_w * (v / maxv)
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{rh:.1f}" fill="{colors[k]}"/>')
            x += w
    parts.append("</svg>")
    return "".join(parts)


def line(series, x_labels, width=900, height=220):
    """折线图。series=[(name, [values], color), ...]"""
    if not x_labels:
        return ""
    pad_l, pad_r, pad_t, pad_b = 36, 12, 12, 28
    allv = [v for _, vals, _ in series for v in vals] or [0]
    maxv = max(allv) or 1
    n = len(x_labels)
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    def px(i): return pad_l + (plot_w * i / max(n - 1, 1))
    def py(v): return pad_t + plot_h * (1 - v / maxv)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    for i in range(5):
        y = pad_t + plot_h * i / 4
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" stroke="#eef2f7"/>')
        parts.append(f'<text x="{pad_l-6}" y="{y+4:.1f}" text-anchor="end" font-size="10" fill="#94a3b8">{maxv*(1-i/4):.0f}</text>')
    for name, vals, color in series:
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/>')
        for i, v in enumerate(vals):
            parts.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="2.5" fill="{color}"/>')
    # x 轴标签(每隔几个显示一次,避免拥挤)
    step = max(1, n // 12)
    for i in range(0, n, step):
        parts.append(f'<text x="{px(i):.1f}" y="{height-8}" text-anchor="middle" font-size="9" fill="#94a3b8">{_esc(x_labels[i])}</text>')
    parts.append("</svg>")
    return "".join(parts)
