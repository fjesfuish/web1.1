# -*- coding: utf-8 -*-
"""MES 数据桥接:读取本地 MES数据库.csv,按工单聚合在制工序。

数据流:MES(ODBC) → xlsm 刷新 → MES数据库.csv → 本模块读取 → sqlite
本模块只负责"读 csv + 聚合",不直连数据库。

csv 字段(GBK 编码):
  MFGORDERNAME 工单号 | PRODUCTNAME 产品型号 | SPECNAME 当前工序
  NEXT_SPECNAME 下一工序 | SPECDESC 工序描述 | SPECSEQUENCE 工序顺序
  STATUS 状态 | PRIORITY 优先级 | OWNERNAME 负责人代号 | QTY 数量
  LOTID 批次 | RUNTIME/WAITTIME/HOLDTIME 时长

【优化】
1. 新增 scan_orders():一次扫描 CSV 即可为「一批工单」构建全部在制信息,
   替代原来「每个工单各扫一遍整表」的 O(N×行数) 同步,降到一遍。
2. 编码探测 _detect_open 与 list_products 结果按「文件 mtime」缓存,避免反复扫表。
3. 前缀匹配由「包含(in)」改为「以…开头(startswith)」,避免 MB 误中 AMB3 等。
4. 工单号匹配统一用 _tail_key(后11位口径),短号不再错配。
"""
import csv
import os
import sys
import threading
from collections import defaultdict

# CSV 单字段可能很长(超 Python 默认 128KB 限制会抛 _csv.Error 致整次同步失败),放大上限防护。
try:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
except (OverflowError, ValueError):
    csv.field_size_limit(2**31 - 1)

# 默认 csv 路径:可被环境变量 MES_CSV 覆盖;否则取公用数据目录 common/MES数据库.csv
# (塑封/灌封共用同一份去掉排除项的全量 CSV,各按白名单过滤)。
DEFAULT_CSV = os.environ.get(
    "MES_CSV",
    os.path.join(os.path.dirname(__file__), "common", "MES数据库.csv"))

# MES 状态 → 系统状态(中文)
STATUS_MAP = {
    "WaitingTrackIn":  "等待进站",
    "WaitingMoveOut":  "等待出站",
    "RUNNING":         "运行中",
    "HOLD":            "HOLD",
    "InWareHouse":     "已入库",
}

# ---- 缓存(按文件 mtime 失效),避免同一次同步内反复扫全表 ----
_enc_cache = {}        # path -> (mtime, enc)
_products_cache = {}   # path -> (mtime, [(PACKAGESIZE, count)...])



def base_model(product):
    """产品型号 → 基础封装型号:只去掉 '/' 后的变体后缀,保留空格。
    全项目统一口径(CT/统计/筛选/MES匹配一致)。例:
      'MB12 / 4in1' → 'MB12';'HD9 SIC' → 'HD9 SIC'(灌封碳化硅版,空格属型号一部分,不可截)。"""
    return (product or "").split("/")[0].strip()

def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


_rows_cache = {}   # path -> (mtime, [row_dict, ...]) 整表解析结果,按文件 mtime 缓存


def _load_rows(path=None):
    """整表解析 MES CSV 为行字典列表,按文件 mtime 缓存。
    同一 CSV 版本内,各查询(加急/异常/Lot 检索/工单匹配等)复用同一份解析结果,
    避免每次请求都重新解析约 5 万行(10MB+)。CSV 刷新(mtime 变化)后自动重载。
    返回的列表仅供只读遍历,调用方不得就地修改其中的行。"""
    path = path or DEFAULT_CSV
    mt = _mtime(path)
    cached = _rows_cache.get(path)
    if cached and cached[0] == mt:
        return cached[1]
    enc = _detect_open(path)
    try:
        with open(path, encoding=enc, errors="replace") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        # 文件缺失/锁定或 CSV 解析失败 → 降级为空(不崩溃)。空数据由调用方安全处理:
        # 同步侧不会因「整表读不到」而把在产单误判为销账完成(见 _sync_manual_orders_impl 的 mes_has_data 闸)。
        rows = []
    _rows_cache[path] = (mt, rows)
    return rows


def has_data(path=None):
    """MES CSV 是否成功加载到数据行(>0)。供同步侧判断「数据源是否可用」——
    返回 False(文件缺失/锁定/空文件/解析失败)时,不得据此把工单判为销账完成。"""
    return len(_load_rows(path)) > 0


def _mes_comment(row):
    """MES 操作备注:合并 TXNCOMMENTS(工序/事务备注)与 HOLDCOMMENT(HOLD 专属备注)。
    两者都有且不同 → 用「｜」连接;只有一个 → 取那个;都没有 → 空。
    旧版 CSV 无这两列时 row.get 返回 None,安全降级为空串。"""
    t = (row.get("TXNCOMMENTS") or "").strip()
    h = (row.get("HOLDCOMMENT") or "").strip()
    if t and h and t != h:
        return t + " ｜ " + h
    return t or h


def _status_time(row, raw_status):
    """按当前状态取对应的时长字段:
    RUNNING→RUNTIME, HOLD→HOLDTIME, 等待(进/出站)→WAITTIME。
    返回带标签的字符串,如 'Runtime: 0.31h'。
    """
    s = (raw_status or "").upper()
    if s == "RUNNING":
        col, label = "RUNTIME", "Runtime"
    elif s == "HOLD":
        col, label = "HOLDTIME", "Holdtime"
    elif s.startswith("WAITING"):
        col, label = "WAITTIME", "Waittime"
    else:
        col, label = "WAITTIME", "Waittime"
    v = (row.get(col) or "").strip()
    if not v:
        return ""
    try:
        return f"{label}: {float(v):.2f}h"
    except (ValueError, TypeError):
        return f"{label}: {v}"


def _detect_open(path):
    """检测 csv 编码(结果按 mtime 缓存)。先试 utf-8-sig,再 utf-8,最后 gbk。
    关键:gbk 几乎不会解码报错,但会把 UTF-8 的 BOM 和中文读成乱码,
    导致第一列列名带乱码前缀 → 按 PACKAGESIZE 取值全空。
    所以这里不只看是否报错,还校验第一行表头里能否找到 PACKAGESIZE。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到 MES csv: {path}")
    mt = _mtime(path)
    cached = _enc_cache.get(path)
    if cached and cached[0] == mt:
        return cached[1]
    candidates = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    enc = "utf-8-sig"  # 兜底
    found = False
    # 第一轮:要求表头里能读到关键列名(确保编码完全正确、BOM 已去除)
    for e in candidates:
        try:
            with open(path, encoding=e) as f:
                head = f.readline()
            if "PACKAGESIZE" in head or "MFGORDERNAME" in head:
                enc = e
                found = True
                break
        except (UnicodeDecodeError, LookupError):
            continue
    if not found:
        # 第二轮:退而求其次,只要能解码不报错
        for e in candidates:
            try:
                with open(path, encoding=e) as f:
                    f.readline()
                enc = e
                break
            except (UnicodeDecodeError, LookupError):
                continue
    _enc_cache[path] = (mt, enc)
    return enc


def _tail_key(s):
    """工单号匹配关键段:统一取后 11 位(不足 11 位则取全部)。"""
    s = (s or "").strip()
    return s[-11:] if len(s) >= 11 else s


def list_products(path=None):
    """返回 [(PACKAGESIZE, 批次数), ...] 按批次数降序(结果按 mtime 缓存)。"""
    path = path or DEFAULT_CSV
    mt = _mtime(path)
    cached = _products_cache.get(path)
    if cached and cached[0] == mt:
        return cached[1]
    counts = defaultdict(int)
    for row in _load_rows(path):
        p = (row.get("PACKAGESIZE") or "").strip()
        if p:
            counts[p] += 1
    result = sorted(counts.items(), key=lambda x: -x[1])
    _products_cache[path] = (mt, result)
    return result


def _match_mode(model, all_sizes):
    """决定某录入型号用哪种匹配。返回 (mode, matcher)。
    mode: 'exact' 或 'prefix'
    matcher: 接收 PACKAGESIZE(已 upper)返回 bool 的函数
    规则(从严到松):
      1. 若 MES 中存在 PACKAGESIZE 精确等于该型号 → 精确匹配
      2. 否则 → 前两字符「开头」匹配(startswith,避免 MB 误中 AMB3)
    """
    m = (model or "").strip().upper()
    if not m:
        return "none", (lambda s: False)
    # 录入型号可能带子项目后缀(如 "MB12 / 4IN1"、"MB12 1IN1"):先取基础封装型号
    # (/ 或空格前),避免退化成过宽的两字符前缀(如 "MB" 会误中 MB24/MB1/MB6…)。
    base = base_model(m)
    if base in all_sizes:
        return "exact", (lambda s: s == base)
    if m in all_sizes:
        return "exact", (lambda s: s == m)
    prefix = base[:2]
    return "prefix", (lambda s: s.startswith(prefix))


def aggregate_by_model(model, path=None):
    """按录入型号抓取并聚合工单(从严到松匹配 PACKAGESIZE)。
    返回工单 dict 列表;另在每条上标注 _match_mode 供展示。
    """
    path = path or DEFAULT_CSV
    enc = _detect_open(path)

    all_sizes_upper = set(s.upper() for s, _ in list_products(path))
    mode, matcher = _match_mode(model, all_sizes_upper)
    if mode == "none":
        return [], mode

    wo = defaultdict(lambda: defaultdict(lambda: {
        "seq": "", "spec": "", "desc": "", "status": "", "raw_status": "",
        "lots": 0, "qty": 0, "time": ""}))
    meta = {}
    _rows = _load_rows(path)
    if _rows:
        for row in _rows:
            psize = (row.get("PACKAGESIZE") or "").strip().upper()
            if not matcher(psize):
                continue
            o = (row.get("MFGORDERNAME") or "").strip()
            if not o:
                continue
            seq = (row.get("SPECSEQUENCE") or "").strip()
            spec = (row.get("SPECNAME") or "").strip()
            key = (seq, spec)
            cell = wo[o][key]
            cell["seq"] = seq
            cell["spec"] = spec
            cell["desc"] = (row.get("SPECDESC") or "").strip()
            raw_status = (row.get("STATUS") or "").strip()
            cell["raw_status"] = raw_status
            cell["status"] = STATUS_MAP.get(raw_status, raw_status)
            cell["time"] = _status_time(row, raw_status)
            cell["lots"] += 1
            try:
                cell["qty"] += int(float(row.get("QTY") or 0))
            except (ValueError, TypeError):
                pass
            meta[o] = {
                "priority":    (row.get("PRIORITY") or "").strip(),
                "owner":       (row.get("OWNERNAME") or "").strip(),
                "packagesize": (row.get("PACKAGESIZE") or "").strip(),
                "product":     (row.get("PRODUCTNAME") or "").strip(),
            }

    result = []
    for o, procs in wo.items():
        def seq_key(p):
            try:
                return int(p["seq"])
            except (ValueError, TypeError):
                return 9999
        plist = sorted(procs.values(), key=seq_key)
        total_lots = sum(p["lots"] for p in plist)
        total_qty = sum(p["qty"] for p in plist)
        cur = plist[0] if plist else None
        result.append({
            "wo_no": o,
            "product": meta[o]["packagesize"],
            "product_name": meta[o]["product"],
            "priority": meta[o]["priority"],
            "owner": meta[o]["owner"],
            "lot_count": total_lots,
            "qty": total_qty,
            "processes": plist,
            "cur_process": (f'{cur["spec"]}({cur["desc"]})' if cur and cur["desc"]
                            else (cur["spec"] if cur else "")),
            "status": cur["status"] if cur else "",
        })
    result.sort(key=lambda x: -x["lot_count"])
    return result, mode


# 兼容旧名(纯前缀)
def aggregate_by_prefix(model, path=None):
    res, _ = aggregate_by_model(model, path)
    return res


def aggregate_by_product(product, path=None):
    res, _ = aggregate_by_model(product, path)
    return res


def aggregate_many(models, path=None):
    """单次扫描 csv,为多个录入型号各自聚合工单(性能优化)。
    返回 {model: (orders, mode)}。
    每个工单只归属到"最匹配"的一个型号:优先精确匹配的型号,
    避免同一工单在多个前缀型号下重复。
    """
    path = path or DEFAULT_CSV
    enc = _detect_open(path)
    all_sizes_upper = set(s.upper() for s, _ in list_products(path))

    # 为每个型号确定匹配模式
    plans = {}
    for m in models:
        mode, matcher = _match_mode(m, all_sizes_upper)
        plans[m] = {"mode": mode, "matcher": matcher,
                    "wo": defaultdict(lambda: defaultdict(lambda: {
                        "seq": "", "spec": "", "desc": "", "status": "",
                        "lots": 0, "qty": 0})),
                    "meta": {}}

    # 精确型号集合(用于"已被精确匹配的封装不再被前缀型号抢走")
    exact_sizes = {(m or "").strip().upper() for m in models
                   if plans[m]["mode"] == "exact"}

    _rows = _load_rows(path)
    if _rows:
        for row in _rows:
            psize = (row.get("PACKAGESIZE") or "").strip().upper()
            if not psize:
                continue
            o = (row.get("MFGORDERNAME") or "").strip()
            if not o:
                continue
            seq = (row.get("SPECSEQUENCE") or "").strip()
            spec = (row.get("SPECNAME") or "").strip()
            desc = (row.get("SPECDESC") or "").strip()
            status = STATUS_MAP.get((row.get("STATUS") or "").strip(),
                                    (row.get("STATUS") or "").strip())
            try:
                qty = int(float(row.get("QTY") or 0))
            except (ValueError, TypeError):
                qty = 0
            meta = {
                "priority":    (row.get("PRIORITY") or "").strip(),
                "owner":       (row.get("OWNERNAME") or "").strip(),
                "packagesize": (row.get("PACKAGESIZE") or "").strip(),
                "product":     (row.get("PRODUCTNAME") or "").strip(),
            }
            # 该行归属哪些型号
            for m, plan in plans.items():
                if plan["mode"] == "none":
                    continue
                # 前缀型号:若该封装已被某个精确型号覆盖,则跳过(防重复)
                if plan["mode"] == "prefix" and psize in exact_sizes:
                    continue
                if plan["matcher"](psize):
                    cell = plan["wo"][o][(seq, spec)]
                    cell["seq"], cell["spec"], cell["desc"] = seq, spec, desc
                    cell["status"] = status
                    cell["lots"] += 1
                    cell["qty"] += qty
                    plan["meta"][o] = meta

    # 组装结果
    def build(plan):
        result = []
        for o, procs in plan["wo"].items():
            def seq_key(p):
                try:
                    return int(p["seq"])
                except (ValueError, TypeError):
                    return 9999
            plist = sorted(procs.values(), key=seq_key)
            cur = plist[0] if plist else None
            result.append({
                "wo_no": o,
                "product": plan["meta"][o]["packagesize"],
                "product_name": plan["meta"][o]["product"],
                "priority": plan["meta"][o]["priority"],
                "owner": plan["meta"][o]["owner"],
                "lot_count": sum(p["lots"] for p in plist),
                "qty": sum(p["qty"] for p in plist),
                "processes": plist,
                "cur_process": (f'{cur["spec"]}({cur["desc"]})' if cur and cur["desc"]
                                else (cur["spec"] if cur else "")),
                "status": cur["status"] if cur else "",
            })
        result.sort(key=lambda x: -x["lot_count"])
        return result

    return {m: (build(plan), plan["mode"]) for m, plan in plans.items()}


def normalize_wo(wo_no):
    """把录入工单号归一为用于匹配的 11 位关键段。
    * 是通配首位;真正匹配的是首位之后的部分。统一取最后 11 位。
    """
    s = (wo_no or "").strip().lstrip("*").strip()
    return _tail_key(s)


# ---------- 工单在制信息:逐行累积 + 组装(供单查 / 批量共用) ----------
def _new_cell():
    return {"seq": "", "spec": "", "desc": "", "status": "", "lots": 0, "qty": 0,
            "time": "", "lot_list": []}


def _new_variants():
    return defaultdict(lambda: defaultdict(_new_cell))


def _row_into(cells, meta, full, row):
    """把 CSV 一行并入某 full_wo 的工序明细 cells(defaultdict[(seq,spec)]→cell)。"""
    seq = (row.get("SPECSEQUENCE") or "").strip()
    spec = (row.get("SPECNAME") or "").strip()
    cell = cells[(seq, spec)]
    cell["seq"], cell["spec"] = seq, spec
    cell["desc"] = (row.get("SPECDESC") or "").strip()
    raw_status = (row.get("STATUS") or "").strip()
    lot_status = STATUS_MAP.get(raw_status, raw_status)
    cell["status"] = lot_status
    cell["time"] = _status_time(row, raw_status)
    cell["lots"] += 1

    def _num(col):
        v = (row.get(col) or "").strip()
        try:
            return f"{float(v):.2f}h"
        except (ValueError, TypeError):
            return v or "-"
    cell["lot_list"].append({
        "lot_id": (row.get("LOTID") or "").strip(),
        "status": lot_status,
        "qty": (row.get("QTY") or "").strip(),
        "eqpld": (row.get("EQPID") or "").strip(),   # 当前所在设备号(显示为 EQP ID)
        "run_time": _num("RUNTIME"),
        "wait_time": _num("WAITTIME"),
        "hold_time": _num("HOLDTIME"),
        # HOLD原因 + MES操作备注(WIPProfile 程序「备注」列的真实来源);旧版 CSV 无此列时降级空串
        "hold_reason": (row.get("HOLDREASONNAME") or "").strip(),
        "txn_comment": _mes_comment(row),
    })
    try:
        cell["qty"] += int(float(row.get("QTY") or 0))
    except (ValueError, TypeError):
        pass
    meta[full] = {
        "priority":    (row.get("PRIORITY") or "").strip(),
        "owner":       (row.get("OWNERNAME") or "").strip(),
        "packagesize": (row.get("PACKAGESIZE") or "").strip(),
        "product":     (row.get("PRODUCTNAME") or "").strip(),
    }


def _seq_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 9999


def _majority_status(plist):
    """按 Lot 数量(颗数 QTY)加权统计各状态,返回占比最多的状态。
    例:60颗RUNNING / 30颗WAITING / 10颗HOLD → '运行中'。
    无 qty 时退化为按 Lot 条数计。
    返回 (状态, 占比0-1, 各状态qty字典, 该状态颗数最多的工序spec)。"""
    agg = {}
    total = 0
    status_spec = {}
    for p in plist:
        spec = p.get("spec") or ""
        for lot in p.get("lot_list", []):
            st = lot.get("status") or ""
            try:
                q = int(float(lot.get("qty") or 0))
            except (ValueError, TypeError):
                q = 0
            if q <= 0:
                q = 1
            agg[st] = agg.get(st, 0) + q
            total += q
            ss = status_spec.setdefault(st, {})
            ss[spec] = ss.get(spec, 0) + q
    if not agg or total <= 0:
        return ("", 0.0, {}, "")
    best = max(agg.items(), key=lambda kv: kv[1])
    bstat = best[0]
    bspec = ""
    if status_spec.get(bstat):
        bspec = max(status_spec[bstat].items(), key=lambda kv: kv[1])[0]
    return (bstat, best[1] / total, agg, bspec)


def _build_info(key, variants, meta):
    """把 {full_wo: {(seq,spec):cell}} 组装成 lookup_order 风格的工单信息。"""
    var_list = []
    for full, procs in variants.items():
        plist = sorted(procs.values(), key=lambda p: _seq_int(p["seq"]))
        cur = plist[0] if plist else None
        mstat, mratio, magg, mspec = _majority_status(plist)
        var_list.append({
            "full_wo": full, "first": full[0] if full else "",
            "cur_seq": cur["seq"] if cur else "",
            "cur_spec": cur["spec"] if cur else "",
            "cur_desc": cur["desc"] if cur else "",
            "cur_status": cur["status"] if cur else "",
            "qty_status": mstat,
            "qty_status_ratio": round(mratio, 3),
            "qty_status_spec": mspec,
            "processes": plist,
            "lot_count": sum(p["lots"] for p in plist),
            "qty": sum(p["qty"] for p in plist),
            "priority": meta[full]["priority"],
            "owner": meta[full]["owner"],
            "product": meta[full]["packagesize"],
            "product_name": meta[full]["product"],
        })
    var_list.sort(key=lambda v: _seq_int(v["cur_seq"]))  # 最快分身在前
    top = var_list[0]
    all_procs = [p for v in var_list for p in v["processes"]]
    order_qty_status, order_qty_ratio, order_qty_agg, order_qty_spec = _majority_status(all_procs)
    return {
        "key": key, "variants": var_list,
        "cur_process": (f'{top["cur_spec"]}({top["cur_desc"]})'
                        if top["cur_desc"] else top["cur_spec"]),
        "cur_status": top["cur_status"],
        "qty_status": order_qty_status,
        "qty_status_ratio": round(order_qty_ratio, 3),
        "qty_status_agg": order_qty_agg,
        "qty_status_spec": order_qty_spec,
        "product": top["product"], "product_name": top["product_name"],
        "owner": top["owner"], "priority": top["priority"],
    }


def lookup_order(wo_no, path=None):
    """按录入工单号(通配首位)在 MES 查找所有分身及在制工序。
    返回 dict(见 _build_info),无匹配返回 None。
    显示用 cur_process 取「工序最靠前」的分身;variants 列出全部分身。
    """
    key = normalize_wo(wo_no)
    if not key:
        return None
    path = path or DEFAULT_CSV
    enc = _detect_open(path)
    variants = _new_variants()
    meta = {}
    for row in _load_rows(path):
        full = (row.get("MFGORDERNAME") or "").strip()
        if _tail_key(full) != key:
            continue
        _row_into(variants[full], meta, full, row)
    if not variants:
        return None
    return _build_info(key, variants, meta)


def scan_orders(wo_list, path=None):
    """【性能核心】一次扫描 CSV,为一批录入工单号各自构建在制信息。
    返回 {录入工单号: info},info 结构同 lookup_order(无匹配则为 None)。
    用它替代「对每个工单各调一次 lookup_order(各扫一遍全表)」的 O(N×行数) 做法。
    """
    path = path or DEFAULT_CSV
    keymap = {}   # tail_key -> 录入工单号
    for w in wo_list:
        k = normalize_wo(w)
        if k:
            keymap[k] = w
    if not keymap:
        return {}
    enc = _detect_open(path)
    buckets = {inp: {"variants": _new_variants(), "meta": {}}
               for inp in keymap.values()}
    for row in _load_rows(path):
        full = (row.get("MFGORDERNAME") or "").strip()
        inp = keymap.get(_tail_key(full))
        if inp is None:
            continue
        b = buckets[inp]
        _row_into(b["variants"][full], b["meta"], full, row)
    out = {}
    for inp in keymap.values():
        b = buckets[inp]
        out[inp] = _build_info(normalize_wo(inp), b["variants"], b["meta"]) \
            if b["variants"] else None
    return out


def lookup_all_lots(wo_keys, path=None):
    """一次性扫描 MES CSV(只读一遍),返回所有匹配给定工单号集合的 Lot 明细。
    wo_keys: 录入工单号列表(会各取规范化后的后 11 位匹配)。
    返回 [{wo_key, full_wo, product, spec, desc, seq, status(中文), lot_id, qty,
           run_time, wait_time, hold_time}]。"""
    key_map = {}
    for w in wo_keys:
        k = normalize_wo(w)
        if k:
            key_map[k] = w
    if not key_map:
        return []
    path = path or DEFAULT_CSV
    enc = _detect_open(path)

    def _num(row, col):
        v = (row.get(col) or "").strip()
        try:
            return f"{float(v):.2f}h"
        except (ValueError, TypeError):
            return v or "-"

    out = []
    _rows = _load_rows(path)
    if _rows:
        for row in _rows:
            full = (row.get("MFGORDERNAME") or "").strip()
            k = _tail_key(full)
            if k not in key_map:
                continue
            raw_status = (row.get("STATUS") or "").strip()
            try:
                q = int(float(row.get("QTY") or 0))
            except (ValueError, TypeError):
                q = 0
            out.append({
                "wo_key": key_map[k],
                "full_wo": full,
                "product": (row.get("PRODUCTNAME") or "").strip(),
                "spec": (row.get("SPECNAME") or "").strip(),
                "desc": (row.get("SPECDESC") or "").strip(),
                "seq": (row.get("SPECSEQUENCE") or "").strip(),
                "status": STATUS_MAP.get(raw_status, raw_status),
                "lot_id": (row.get("LOTID") or "").strip(),
                "qty": q,
                "eqpld": (row.get("EQPID") or "").strip(),   # 当前所在设备号(显示为 EQP ID)
                "run_time": _num(row, "RUNTIME"),
                "wait_time": _num(row, "WAITTIME"),
                "hold_time": _num(row, "HOLDTIME"),
                # HOLD原因(HOLDREASONNAME) + MES操作备注(TXNCOMMENTS,即 WIPProfile 程序
                # 「备注」列的真实来源);旧版 CSV 无此两列时 row.get 返回 None → 安全降级为空串。
                "hold_reason": (row.get("HOLDREASONNAME") or "").strip(),
                "txn_comment": _mes_comment(row),
            })
    return out


def product_specs(product, path=None):
    """某 PRODUCTNAME 在 MES 出现过的工序(去重),按 SPECSEQUENCE 排序。
    返回 [{spec, name, seq}]。用于加急 Lot 的目标工序候选与 CT 表匹配。"""
    product = (product or "").strip()
    if not product:
        return []
    path = path or DEFAULT_CSV
    enc = _detect_open(path)
    seen = {}
    _rows = _load_rows(path)
    if _rows:
        for row in _rows:
            if (row.get("PRODUCTNAME") or "").strip() != product:
                continue
            sp = (row.get("SPECNAME") or "").strip()
            if sp and sp not in seen:
                seen[sp] = {"spec": sp, "name": (row.get("SPECDESC") or "").strip(),
                            "seq": (row.get("SPECSEQUENCE") or "").strip()}
    def _k(d):
        try:
            return int(d["seq"])
        except (ValueError, TypeError):
            return 9999
    return sorted(seen.values(), key=_k)


def lookup_lots_by_ids(lot_ids, path=None):
    """按给定 LOTID 集合(精确匹配)一次扫描 MES,返回 {lot_id: {...当前行...}}。
    供「加急验证Lot」按用户填写的 Lot 号直接取当前工序/状态/时长。
    返回字段:spec(SPECNAME), desc(SPECDESC), seq, status(中文), qty, run_time, wait_time, hold_time。"""
    want = {str(x).strip() for x in (lot_ids or []) if str(x).strip()}
    if not want:
        return {}
    path = path or DEFAULT_CSV
    enc = _detect_open(path)

    def _num(row, col):
        v = (row.get(col) or "").strip()
        try:
            return f"{float(v):.2f}h"
        except (ValueError, TypeError):
            return v or "-"

    out = {}
    _rows = _load_rows(path)
    if _rows:
        for row in _rows:
            lot = (row.get("LOTID") or "").strip()
            if lot not in want:
                continue
            raw_status = (row.get("STATUS") or "").strip()
            try:
                q = int(float(row.get("QTY") or 0))
            except (ValueError, TypeError):
                q = 0
            out[lot] = {
                "lot_id": lot,
                "full_wo": (row.get("MFGORDERNAME") or "").strip(),
                "product": (row.get("PRODUCTNAME") or "").strip(),
                "spec": (row.get("SPECNAME") or "").strip(),
                "desc": (row.get("SPECDESC") or "").strip(),
                "seq": (row.get("SPECSEQUENCE") or "").strip(),
                "status": STATUS_MAP.get(raw_status, raw_status),
                "qty": q,
                "eqpld": (row.get("EQPID") or "").strip(),   # 当前所在设备号(EQP ID)
                "run_time": _num(row, "RUNTIME"),
                "wait_time": _num(row, "WAITTIME"),
                "hold_time": _num(row, "HOLDTIME"),
                "hold_reason": (row.get("HOLDREASONNAME") or "").strip(),
                "txn_comment": _mes_comment(row),
            }
    return out


def lookup_by_lot(lot_kw, path=None):
    """按 Lot ID(支持部分匹配)查找所属工单号,再返回这些工单的详情。
    返回 [{wo_key, full_wo, detail}, ...],detail 结构同 lookup_order。
    """
    lot_kw = (lot_kw or "").strip()
    if not lot_kw:
        return []
    path = path or DEFAULT_CSV
    enc = _detect_open(path)
    hit_orders = {}   # full_wo -> set(lot_id)
    _rows = _load_rows(path)
    if _rows:
        for row in _rows:
            lot = (row.get("LOTID") or "").strip()
            if lot_kw.lower() in lot.lower():
                full = (row.get("MFGORDERNAME") or "").strip()
                if full:
                    hit_orders.setdefault(full, set()).add(lot)
    if not hit_orders:
        return []
    # 一次扫描取出所有命中工单的详情(按后 11 位去重)
    keys = {}
    for full, lots in hit_orders.items():
        keys.setdefault(_tail_key(full), set()).update(lots)
    infos = scan_orders(list(keys.keys()), path)
    results = []
    for key, lots in keys.items():
        detail = infos.get(key)
        if detail:
            results.append({
                "wo_key": key,
                "matched_lots": sorted(lots),
                "detail": detail,
            })
    return results


def calc_progress(cur_seq, min_seq, max_seq):
    """进度百分比计算。【占位算法,实际进度由 db 层用 CT 表计算】
    当前: 当前工序号 / 该封装最大工序号。
    """
    try:
        cur = int(cur_seq)
        mx = int(max_seq)
        if mx <= 0:
            return 0
        return max(0, min(100, round(cur / mx * 100)))
    except (ValueError, TypeError):
        return 0


def spec_is_virtual(spec, desc):
    """虚拟占位 / 暂存站判定:这些站不计入在制进度,也不计异常(单一判定口径)。
    覆盖:描述含'虚拟站'(建批首站、测试后虚拟站、不良品虚拟站等)或'送报废仓'(待报废暂存),
    以及已知占位工序号 MB_AGITEM03 / 送报废仓工序 TOPURGELOCATION。
    与异常监控的忽略口径保持一致,避免「停在这些站的残留 Lot 拖住工单进度」。"""
    d = desc or ""
    s = spec or ""
    return ("虚拟站" in d) or ("送报废仓" in d) or (s == "MB_AGITEM03") or (s == "TOPURGELOCATION")


def spec_is_start_virtual(spec, desc):
    """开工前占位虚拟站(建批后首站):Lot 在此表示工单尚未真正开工,不能当作已完成。
    用于区分「测试后 / 不良品虚拟站(已近完成)」与「建批首站(还没开始)」。"""
    d = desc or ""
    return ("首站" in d) or ("建批" in d)


def _build_info_to_sync(info):
    """把 _build_info 的结果转换成 sync_lookup 返回结构(供单查/批量共用)。"""
    _spec_virtual = spec_is_virtual

    def is_virtual(v):
        """虚拟首站分身:代表工序为虚拟占位站。"""
        return _spec_virtual(v.get("cur_spec"), v.get("cur_desc"))

    real_variants = [v for v in info["variants"] if not is_virtual(v)]
    calc_variants = real_variants if real_variants else info["variants"]
    fastest = calc_variants[0]
    slowest = calc_variants[-1]
    # 工序级过滤虚拟站:只把「停在虚拟站」的 lot 排除(不显示),虚拟站之后的真实工序正常计入。
    # 跨所有分身按单个在制工序判定,避免「整条分身因代表工序是虚拟站而被整体丢弃,
    # 连它已走过虚拟站、停在后续真实工序的 lot 也一起不显示」。
    real_specs = [p["spec"] for v in info["variants"] for p in v.get("processes", [])
                  if not _spec_virtual(p.get("spec"), p.get("desc"))]
    if not real_specs:   # 全部停在虚拟站 → 退回非虚拟的代表工序(通常也为空,即不显示)
        real_specs = [v["cur_spec"] for v in calc_variants
                      if not _spec_virtual(v.get("cur_spec"), v.get("cur_desc"))]
    return {
        "matched": True,
        "cur_process": (f'{slowest["cur_spec"]}({slowest["cur_desc"]})'
                        if slowest["cur_desc"] else slowest["cur_spec"]),
        "cur_status": slowest["cur_status"],
        "qty_status": info.get("qty_status", "") or slowest["cur_status"],
        "qty_status_ratio": info.get("qty_status_ratio", 0),
        "qty_status_spec": info.get("qty_status_spec", ""),
        "progress": 0,        # 由 db 层用 CT 表算
        "product": slowest["product"],
        "owner": slowest["owner"],
        "priority": slowest["priority"],
        "variants": info["variants"],
        "real_specs": real_specs,
        "slowest_spec": slowest["cur_spec"],
        "slowest_seq": slowest["cur_seq"],
        "fastest_spec": fastest["cur_spec"],
        "fastest_seq": fastest["cur_seq"],
    }


def sync_lookup(wo_no, path=None, info=None):
    """给手动工单做数据支持:按工单号后11位查 MES,取最慢分身(seq最大)。
    info 可传入预取的 lookup_order 结果(批量同步时由 scan_orders 提供),
    省去对同一文件的重复整表扫描。
    返回 {cur_process, cur_status, progress, product, owner, priority,
          matched(bool), variants(全部分身)} ;无匹配 matched=False。
    """
    path = path or DEFAULT_CSV
    if info is None:
        info = lookup_order(wo_no, path)
    if not info:
        return {"matched": False, "cur_process": "", "cur_status": "无MES数据",
                "progress": 0, "variants": []}
    return _build_info_to_sync(info)


def model_exists(model, path=None):
    """该型号是否在 MES 的 PACKAGESIZE 中精确存在(决定走精确还是前缀)。"""
    m = (model or "").strip().upper()
    if not m:
        return False
    try:
        sizes = set(s.upper() for s, _ in list_products(path))
    except FileNotFoundError:
        return True  # csv 不在时不拦截
    return m in sizes


# ======================================================================
# WIP 超期分析(顶层跨系列,直接读全量 MES 快照)
# ----------------------------------------------------------------------
# 口径:CSV 一行 = 一个 LOT(当前工序);STARTDATE = 建单日;
# 超期 = 今天 − 建单日 > 阈值(自然日,严格大于);
# InWareHouse(已入库)不算在制,其余状态(HOLD 等)均算。
# ======================================================================

WIP_ACTIVE_STATUSES = ("WaitingTrackIn", "WaitingMoveOut", "RUNNING", "HOLD")

# 明细/导出列(展示顺序);series 存代码,由调用方转中文。
WIP_EXPORT_HEADERS = [
    "工单号", "LOT号", "顶层型号", "细分项", "产品代码", "建单日", "超期天数",
    "当前工序", "工序描述", "状态", "数量", "系列", "优先级", "MES Owner",
    "Assy PC Owner", "MPC Owner", "Inline/ENG Owner", "Owner 类型", "工艺流程",
]


def _parse_start_date(s):
    """解析 MES STARTDATE(如 2025/11/23 9:02:04)为 date;失败返回 None。
    兼容 / 与 - 分隔、缺秒、纯日期。"""
    import datetime as _dt
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = s.replace("-", "/").split(" ")[0]
        y, m, dd = d.split("/")
        return _dt.date(int(y), int(m), int(dd))
    except (ValueError, TypeError):
        return None


def _wip_qty(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _wip_bucket_ranges(threshold, edges):
    """把「相对阈值的档位边界」转成 [(lo, hi, label), ...](绝对天数,hi=None 表示无上限)。
    例:threshold=60, edges=[15,30,60,120] → 61-75 / 76-90 / 91-120 / >120。"""
    t = int(threshold)
    es = set()
    for e in (edges or []):
        try:
            e = int(e)
        except (ValueError, TypeError):
            continue
        if e > 0:
            es.add(e)
    es = sorted(es)
    out, prev = [], t
    for e in es:
        hi = t + e
        out.append((prev + 1, hi, f"{prev + 1}-{hi}天"))
        prev = hi
    out.append((prev + 1, None, f">{prev}天"))
    return out


_wip_active_cache = {}   # (绝对路径, mtime, 今天) -> (在制 LOT 元组列表, 全量型号计数, 坏日期数)


_wip_snapshot_locks = {}   # CSV 绝对路径 -> Lock(冷启动只允许一个线程扫描)
_wip_snapshot_locks_guard = threading.Lock()


def _wip_snapshot_lock(path_key):
    with _wip_snapshot_locks_guard:
        lock = _wip_snapshot_locks.get(path_key)
        if lock is None:
            lock = threading.Lock()
            _wip_snapshot_locks[path_key] = lock
        return lock


def _wip_active_snapshot(path, today):
    """把 CSV 归一化成「在制 LOT」紧凑元组,按 CSV mtime + 当天日期缓存。
    超期页每次请求都要扫 7 万行,预解析可省掉重复的字符串/日期处理。
    返回 (records, products_all, bad_date_count, csv_rows)。

    并发保护:同一 CSV 的首次构建只允许一个线程做,其他线程等它完成后
    直接复用缓存,避免 A/B/C 同时各扫一遍 7 万行把 CPU 打满。"""
    import os as _os
    try:
        mt = _os.path.getmtime(path)
    except OSError:
        return [], {}, 0, 0
    key = (_os.path.abspath(path), mt, today.isoformat())
    hit = _wip_active_cache.get(key)
    if hit is not None:
        return hit
    lock = _wip_snapshot_lock(key[0])
    with lock:
        hit = _wip_active_cache.get(key)
        if hit is not None:
            return hit
        try:
            rows_all = _load_rows(path)
        except OSError:
            rows_all = []
        active = []
        products = {}
        bad = 0
        for row in rows_all:
            pkg = (row.get("PACKAGESIZE") or "").strip()
            if pkg:
                products[pkg] = products.get(pkg, 0) + 1
            raw = (row.get("STATUS") or "").strip()
            if raw in ("InWareHouse", "已入库") or STATUS_MAP.get(raw) == "已入库":
                continue
            start = _parse_start_date(row.get("STARTDATE"))
            if start is None:
                bad += 1
                continue
            active.append((
                (today - start).days,
                (row.get("MFGORDERNAME") or "").strip(),
                (row.get("LOTID") or "").strip(),
                pkg, pkg.upper(),
                (row.get("PRODUCTNAME") or "").strip(),
                start.isoformat(),
                (row.get("SPECNAME") or "").strip(),
                (row.get("SPECDESC") or "").strip(),
                STATUS_MAP.get(raw, raw),
                _wip_qty(row.get("QTY")),
                (row.get("PRIORITY") or "").strip(),
                (row.get("OWNERNAME") or "").strip(),
                (row.get("WORKFLOWNAME") or "").strip(),
            ))
        # 同一 CSV 只保留最新快照,避免 mtime 变化后缓存堆积
        for k in list(_wip_active_cache):
            if k[0] == key[0] and k != key:
                _wip_active_cache.pop(k, None)
        result = (active, products, bad, len(rows_all))
        _wip_active_cache[key] = result
        return result

def _resolve_wip_owners(lookup, product_name, pkg, spec_name, status):
    """按 LOT 解析三类 Owner,返回 (Assy PC, MPC, 现行 Owner, 类型)。

    统一以细分项(PACKAGESIZE)为锚点:
      · Assy PC Owner:按 PACKAGESIZE;
      · MPC Owner:按 PACKAGESIZE;
      · Inline/ENG:MPC 表提供 PACKAGESIZE→TECHNOLOGY,
        再按 PACKAGESIZE+SPECNAME 查。找不到就留空,不按 SPECNAME 猜。
    HOLD 取 ENG,其余取 Inline。
    """
    if not lookup:
        return "", "", "", ""
    ku = (pkg or "").strip().upper()
    su = (spec_name or "").strip().upper()
    assy = ((lookup.get("assy") or {}).get(ku) or {}).get("owner", "")
    mpc_rec = (lookup.get("mpc") or {}).get(ku) or {}
    mpc_owner = mpc_rec.get("owner", "")
    rec = None
    if ku and su:
        rec = (lookup.get("inline_by_pkg") or {}).get(ku + "|" + su)
    is_hold = "HOLD" in (status or "").upper()
    kind = "ENG" if is_hold else "Inline"
    active = ""
    if rec:
        active = (rec.get("eng") if is_hold else rec.get("inline")) or ""
    return assy, mpc_owner, active, (kind if active else "")


# 单条缓存:同一筛选条件下翻页/导出直接复用(56k 条重算约 2s)。
# 只保留最近一次,避免多组筛选把内存撑大。
_wip_collect_last = {"key": None, "recs": None, "stats": None}


def _collect_wip_overdue(threshold_days=60, bucket_edges=(15, 30, 60, 120),
                         series_map=None, series="", product="", keyword="",
                         bucket=None, spec="", family="", aowner="", mowner="",
                         iowner="", owner_map=None, owner_ver="", hidden=None,
                         path=None):
    """扫描全量 CSV,返回筛选后的超期 LOT(元组,按超期天数降序)+ 统计。
    只用于页面/导出,不写任何库。

    联动口径(与页面一致):
      · summary / 明细 / 导出 = 所有条件同时生效;
      · 每个图表忽略「自己这一维」的筛选,这样选中后仍能看到完整分布,
        其余条件照常生效(标准 cross-filter 行为)。
    维度:系列 / 档位 / 细分型号(PACKAGESIZE) / 工序 / 顶层型号(TECHNOLOGY) /
          Assy Owner / MPC Owner / Inline-ENG Owner。
    """
    global _wip_collect_last
    import os as _os
    import datetime as _dt
    path = path or DEFAULT_CSV
    today = _dt.date.today()
    t = int(threshold_days)
    smap = {(k or "").strip().upper(): v for k, v in (series_map or {}).items()}
    series_f = (series or "").strip()
    product_f = (product or "").strip().upper()
    spec_f = (spec or "").strip()
    family_f = (family or "").strip().upper()
    aowner_f = (aowner or "").strip()
    mowner_f = (mowner or "").strip()
    iowner_f = (iowner or "").strip()
    hidden_set = {str(x).strip().upper() for x in (hidden or []) if str(x).strip()}
    kw = (keyword or "").strip().lower()
    edges_key = []
    for e in (bucket_edges or []):
        try:
            e = int(e)
        except (ValueError, TypeError):
            continue
        if e > 0:
            edges_key.append(e)
    edges_key = tuple(sorted(set(edges_key)))
    ranges = _wip_bucket_ranges(t, edges_key)
    bucket_f = None
    if bucket is not None:
        try:
            bucket_f = int(bucket)
        except (ValueError, TypeError):
            bucket_f = None
        if bucket_f is not None and not (0 <= bucket_f < len(ranges)):
            bucket_f = None
    cache_key = (_os.path.abspath(path), _mtime(path), today.isoformat(), t,
                 edges_key, series_f, product_f, spec_f, family_f, bucket_f,
                 aowner_f, mowner_f, iowner_f, kw,
                 tuple(sorted(smap.items())), owner_ver,
                 tuple(sorted(hidden_set)))
    # 取当前缓存快照的引用;下面只替换整个 dict,不原地修改,
    # 避免 A 判断命中后、读取前被 B 的筛选结果覆盖(并发串数据)。
    hit = _wip_collect_last
    if hit.get("key") == cache_key and hit.get("recs") is not None:
        return hit["recs"], dict(hit["stats"])
    active, products_all, bad_date_count, csv_rows = _wip_active_snapshot(path, today)

    overdue = []
    order_set = set()
    qty_total = 0
    days_sum = 0
    max_days = 0
    full_series = {k: {"lots": 0, "orders": set(), "qty": 0, "days_sum": 0, "max_days": 0}
                   for k in ("molding", "potting", "unknown")}
    chart_series = {k: {"lots": 0, "orders": set(), "qty": 0, "days_sum": 0, "max_days": 0}
                    for k in ("molding", "potting", "unknown")}
    chart_buckets = [{"lo": lo, "hi": hi, "label": lab, "lots": 0,
                      "orders": set(), "qty": 0, "days_sum": 0, "max_days": 0}
                     for lo, hi, lab in ranges]
    chart_products, chart_processes, chart_families = {}, {}, {}
    chart_assy, chart_mpc, chart_inline = {}, {}, {}
    mpc_map = (owner_map or {}).get("mpc") or {}

    def _put(acc, wo, qty, days):
        acc["lots"] += 1
        acc["orders"].add(wo)
        acc["qty"] += qty
        acc["days_sum"] += days
        if days > acc["max_days"]:
            acc["max_days"] = days

    def _owner_acc(acc, key, owner, wo, qty, days):
        rec = acc.get(key)
        if rec is None:
            rec = {"key": key, "owner": owner, "lots": 0, "orders": set(),
                   "qty": 0, "days_sum": 0, "max_days": 0}
            acc[key] = rec
        _put(rec, wo, qty, days)

    # 位掩码:某一维不匹配就置位;图表条件 = 除自己这一位外无其它位
    B_SERIES, B_BUCKET, B_PRODUCT, B_SPEC, B_FAMILY, B_AOWNER, B_MOWNER, B_IOWNER = (
        2, 4, 8, 16, 32, 64, 128, 256)

    for rec0 in active:
        (days, wo, lot, pkg, pkg_key, product_name, start_iso,
         spec_name, desc, status, qty, priority, owner, workflow) = rec0
        if days <= t:
            continue
        # 管理员取消勾选的型号:全页面/全导出都不参与
        if pkg_key in hidden_set:
            continue
        if kw and kw not in wo.lower() and kw not in lot.lower():
            continue
        ser = smap.get(pkg_key) if smap.get(pkg_key) in ("molding", "potting") else "unknown"
        mpc_rec = mpc_map.get(pkg_key) or {}
        tech_display = (mpc_rec.get("technology") or "").strip()
        family_label = tech_display or pkg or "未分类"
        family_key = family_label.upper()
        assy_o, mpc_o, act_o, kind_o = _resolve_wip_owners(
            owner_map, product_name, pkg, spec_name, status)
        bi = -1
        for idx, (lo, hi, _lab) in enumerate(ranges):
            if lo <= days and (hi is None or days <= hi):
                bi = idx
                break

        mask = 0
        if series_f and ser != series_f:
            mask |= B_SERIES
        if bucket_f is not None and bi != bucket_f:
            mask |= B_BUCKET
        if product_f and pkg_key != product_f:
            mask |= B_PRODUCT
        if spec_f and spec_name != spec_f:
            mask |= B_SPEC
        if family_f and family_key != family_f:
            mask |= B_FAMILY
        if aowner_f:
            hit = (assy_o == "") if aowner_f == "__none__" else (assy_o == aowner_f)
            if not hit:
                mask |= B_AOWNER
        if mowner_f:
            hit = (mpc_o == "") if mowner_f == "__none__" else (mpc_o == mowner_f)
            if not hit:
                mask |= B_MOWNER
        if iowner_f:
            hit = (act_o == "") if iowner_f == "__none__" else (act_o == iowner_f)
            if not hit:
                mask |= B_IOWNER

        # 汇总 / 明细 / 导出:全部条件
        if mask == 0:
            overdue.append((days, wo, lot, pkg, product_name, start_iso,
                            spec_name, desc, status, qty, ser, priority, owner,
                            workflow, assy_o, mpc_o, act_o, kind_o, family_label))
            order_set.add(wo)
            qty_total += qty
            days_sum += days
            if days > max_days:
                max_days = days
            _put(full_series[ser], wo, qty, days)

        # 系列是硬隔离:选了塑封/灌封后,系列图也只保留该系列
        if (mask & ~B_SERIES) == 0 and (not series_f or ser == series_f):
            _put(chart_series[ser], wo, qty, days)

        if (mask & ~B_BUCKET) == 0 and bi >= 0:
            _put(chart_buckets[bi], wo, qty, days)

        if (mask & ~B_PRODUCT) == 0:
            pa = chart_products.get(pkg_key)
            if pa is None:
                pa = {"product": pkg, "lots": 0, "orders": set(), "qty": 0,
                      "days_sum": 0, "max_days": 0}
                chart_products[pkg_key] = pa
            _put(pa, wo, qty, days)

        if (mask & ~B_SPEC) == 0:
            sp = chart_processes.get(spec_name)
            if sp is None:
                sp = {"spec": spec_name, "desc": desc or spec_name, "lots": 0,
                      "orders": set(), "qty": 0, "days_sum": 0, "max_days": 0}
                chart_processes[spec_name] = sp
            _put(sp, wo, qty, days)

        if (mask & ~B_FAMILY) == 0:
            fa = chart_families.get(family_key)
            if fa is None:
                fa = {"key": family_key, "family": family_label, "lots": 0,
                      "orders": set(), "qty": 0, "days_sum": 0, "max_days": 0}
                chart_families[family_key] = fa
            _put(fa, wo, qty, days)

        if (mask & ~B_AOWNER) == 0:
            _owner_acc(chart_assy, assy_o or "__none__", assy_o or "未分配",
                       wo, qty, days)
        if (mask & ~B_MOWNER) == 0:
            _owner_acc(chart_mpc, mpc_o or "__none__", mpc_o or "未分配",
                       wo, qty, days)
        if (mask & ~B_IOWNER) == 0:
            _owner_acc(chart_inline, act_o or "__none__", act_o or "未分配",
                       wo, qty, days)

    overdue.sort(key=lambda r: (-r[0], r[1], r[2]))

    def _grp_out(d):
        return {"lots": d["lots"], "orders": len(d["orders"]), "qty": d["qty"],
                "avg_days": round(d["days_sum"] / d["lots"], 1) if d["lots"] else 0,
                "max_days": d["max_days"]}

    def _top_owner(acc, selected):
        items = sorted(acc.values(), key=lambda x: (-x["lots"], -x["qty"]))
        out = [dict(_grp_out(v), key=v["key"], owner=v["owner"])
               for v in items[:20]]
        if selected and not any(x["key"] == selected for x in out):
            extra = next((dict(_grp_out(v), key=v["key"], owner=v["owner"])
                          for v in items if v["key"] == selected), None)
            if extra:
                out.append(extra)
        return out

    chart_series_out = {k: _grp_out(v) for k, v in chart_series.items()}
    chart_buckets_out = [{"label": b["label"], "lo": b["lo"], "hi": b["hi"],
                          "lots": b["lots"], "orders": len(b["orders"]), "qty": b["qty"]}
                         for b in chart_buckets]
    prod_items = sorted((dict(_grp_out(v), product=v["product"])
                         for v in chart_products.values()),
                        key=lambda x: (-x["lots"], -x["qty"]))
    top_products = prod_items[:20]
    if product_f and not any((x["product"] or "").upper() == product_f
                             for x in top_products):
        extra = next((x for x in prod_items
                      if (x["product"] or "").upper() == product_f), None)
        if extra:
            top_products.append(extra)

    proc_items = sorted((dict(_grp_out(v), spec=v["spec"], desc=v["desc"])
                         for v in chart_processes.values()),
                        key=lambda x: (-x["lots"], -x["qty"]))
    top_processes = proc_items[:20]
    if spec_f and not any(x["spec"] == spec_f for x in top_processes):
        extra = next((x for x in proc_items if x["spec"] == spec_f), None)
        if extra:
            top_processes.append(extra)

    fam_items = sorted((dict(_grp_out(v), key=v["key"], family=v["family"])
                        for v in chart_families.values()),
                       key=lambda x: (-x["lots"], -x["qty"]))
    top_families = fam_items[:20]
    if family_f and not any(x["key"] == family_f for x in top_families):
        extra = next((x for x in fam_items if x["key"] == family_f), None)
        if extra:
            top_families.append(extra)

    # 顶层型号筛选下拉:按全部在制 PACKAGESIZE 汇总(不受超期/筛选影响)
    fam_counts, prod_options = {}, {}
    for pk, cnt in products_all.items():
        pk_key = (pk or "").strip().upper()
        if pk_key in hidden_set:
            continue
        ser_pk = smap.get(pk_key)
        if ser_pk not in ("molding", "potting"):
            ser_pk = "unknown"
        if series_f and ser_pk != series_f:
            continue
        m = mpc_map.get(pk_key) or {}
        tech = (m.get("technology") or "").strip()
        label = tech or pk or "未分类"
        key = label.upper()
        rec = fam_counts.setdefault(key, {"key": key, "family": label, "count": 0})
        rec["count"] += cnt
        if family_f and key != family_f:
            continue
        prod_options[pk] = cnt
    families = sorted(fam_counts.values(), key=lambda x: (-x["count"], x["family"]))

    bucket_label = ranges[bucket_f][2] if bucket_f is not None else ""
    spec_desc = chart_processes.get(spec_f, {}).get("desc", "") if spec_f else ""
    fam_label_map = {x["key"]: x["family"] for x in families}
    stats = {
        "summary": {
            "lots": len(overdue),
            "orders": len(order_set),
            "qty": qty_total,
            "avg_days": round(days_sum / len(overdue), 1) if overdue else 0,
            "max_days": max_days,
            "bad_date": bad_date_count,
            "series": {k: _grp_out(v) for k, v in full_series.items()},
        },
        "charts": {
            "series": chart_series_out,
            "buckets": chart_buckets_out,
            "by_product": top_products,
            "by_process": top_processes,
            "families": top_families,
            "assy": _top_owner(chart_assy, aowner_f),
            "mpc": _top_owner(chart_mpc, mowner_f),
            "inline": _top_owner(chart_inline, iowner_f),
        },
        "chart_totals": {
            "families": len(chart_families),
            "products": len(chart_products),
            "processes": len(chart_processes),
            "assy": len(chart_assy),
            "mpc": len(chart_mpc),
            "inline": len(chart_inline),
            "buckets": len(chart_buckets),
        },
        "filters": {
            "series": series_f,
            "bucket": bucket_f,
            "bucket_label": bucket_label,
            "product": product_f,
            "spec": spec_f,
            "spec_desc": spec_desc,
            "family": family_f,
            "family_label": fam_label_map.get(family_f, family_f),
            "aowner": aowner_f,
            "aowner_label": "未分配" if aowner_f == "__none__" else aowner_f,
            "mowner": mowner_f,
            "mowner_label": "未分配" if mowner_f == "__none__" else mowner_f,
            "iowner": iowner_f,
            "iowner_label": "未分配" if iowner_f == "__none__" else iowner_f,
            "kw": kw,
        },
        # 兼容旧字段
        "buckets": chart_buckets_out,
        "by_product": top_products,
        "by_process": top_processes,
        "by_family": top_families,
        "by_assy": _top_owner(chart_assy, aowner_f),
        "by_mpc": _top_owner(chart_mpc, mowner_f),
        "by_inline": _top_owner(chart_inline, iowner_f),
        "products": [{"product": k, "count": v} for k, v in
                     sorted(prod_options.items(), key=lambda x: (-x[1], x[0]))],
        "families": families,
        "csv_rows": csv_rows,
        "csv_exists": bool(csv_rows),
        "csv_mtime": "",
    }
    import os as _os
    import datetime as _dt2
    try:
        stats["csv_mtime"] = _dt2.datetime.fromtimestamp(_os.path.getmtime(path)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except OSError:
        stats["csv_mtime"] = ""
    # 整体替换而不是原地 update:并发请求各自持有旧快照,不会串数据。
    _wip_collect_last = {"key": cache_key, "recs": overdue, "stats": dict(stats)}
    return overdue, stats

def _wip_rec_dict(r):
    return {"days": r[0], "wo": r[1], "lot": r[2], "product": r[3],
            "product_name": r[4], "start": r[5], "spec": r[6],
            "spec_desc": r[7], "status": r[8], "qty": r[9],
            "series": r[10], "priority": r[11], "mes_owner": r[12],
            "workflow": r[13], "assy_owner": r[14], "mpc_owner": r[15],
            "owner": r[16], "owner_kind": r[17], "family": r[18],
            "sub_model": r[3]}


def wip_overdue_analysis(threshold_days=60, bucket_edges=(15, 30, 60, 120),
                         series_map=None, series="", product="", keyword="",
                         bucket=None, spec="", family="", aowner="", mowner="",
                         iowner="", page=1, page_size=100,
                         owner_map=None, owner_ver="", hidden=None, path=None):
    """页面用:返回统计 + 当前页 LOT 明细(服务端分页)。"""
    import datetime as _dt
    recs, stats = _collect_wip_overdue(
        threshold_days=threshold_days, bucket_edges=bucket_edges,
        series_map=series_map, series=series, product=product, keyword=keyword,
        bucket=bucket, spec=spec, family=family, aowner=aowner,
        mowner=mowner, iowner=iowner, owner_map=owner_map,
        owner_ver=owner_ver, hidden=hidden, path=path)
    total = len(recs)
    ps = max(10, min(int(page_size or 100), 500))
    pages = max(1, (total + ps - 1) // ps)
    pg = max(1, min(int(page or 1), pages))
    lo, hi = (pg - 1) * ps, pg * ps
    edges = []
    for e in (bucket_edges or []):
        try:
            e = int(e)
        except (ValueError, TypeError):
            continue
        if e > 0:
            edges.append(e)
    stats.update({
        "ok": True,
        "today": _dt.date.today().isoformat(),
        "threshold": int(threshold_days),
        "edges": sorted(set(edges)),
        "total": total, "page": pg, "page_size": ps, "pages": pages,
        "rows": [_wip_rec_dict(r) for r in recs[lo:hi]],
    })
    return stats


def wip_overdue_export(threshold_days=60, bucket_edges=(15, 30, 60, 120),
                       series_map=None, series="", product="", keyword="",
                       bucket=None, spec="", family="", aowner="", mowner="",
                       iowner="", owner_map=None, owner_ver="", hidden=None,
                       path=None):
    """导出用:返回 (表头, 全量筛选结果行)。行内系列已转中文。"""
    recs, _ = _collect_wip_overdue(
        threshold_days=threshold_days, bucket_edges=bucket_edges,
        series_map=series_map, series=series, product=product, keyword=keyword,
        bucket=bucket, spec=spec, family=family, aowner=aowner,
        mowner=mowner, iowner=iowner, owner_map=owner_map,
        owner_ver=owner_ver, hidden=hidden, path=path)
    labels = {"molding": "塑封", "potting": "灌封", "unknown": "未分类"}
    rows = []
    for r in recs:
        rows.append([r[1], r[2], r[18], r[3], r[4], r[5], r[0], r[6], r[7],
                     r[8], r[9], labels.get(r[10], r[10]), r[11], r[12],
                     r[14], r[15], r[16], r[17], r[13]])
    return list(WIP_EXPORT_HEADERS), rows


if __name__ == "__main__":
    prods = list_products()
    print(f"PACKAGESIZE 取值数: {len(prods)}")
    print("前8:", prods[:8])
    for model in ("MC1", "MB1", "MB12", "MB99"):
        agg, mode = aggregate_by_model(model)
        total_lots = sum(o["lot_count"] for o in agg)
        print(f"\n录入 {model} → 匹配模式={mode} → 工单数 {len(agg)} / 总批次 {total_lots}")
        if agg:
            o = agg[0]
            print(f"  示例工单 {o['wo_no']} (PACKAGESIZE={o['product']}): "
                  f"{o['lot_count']}批 / {len(o['processes'])}个在制工序")
