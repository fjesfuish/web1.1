"""工序监控数据层:直接读 MES数据库.csv 快照,按 型号(PACKAGESIZE)×OWNERNAME 过滤,
聚合各工序在制 Lot 分布,供导航栏「工序监控」页使用。
数据流:MES(ODBC) → xlsm 刷新 → MES数据库.csv → 本模块按需读取(复用 mes._load_rows 缓存)
与 equip/mes 同源,每次 MES 同步刷新 CSV 后本页数据自动更新,无需写库。
"""
import datetime
import os
import time
from collections import defaultdict

import equip  # 设备能力 UPH 索引(行级「预计加工完成」ETA 用)
import mes  # 复用 _load_rows(整表解析+mtime 缓存)/ STATUS_MAP / DEFAULT_CSV


def _num_h(v):
    """RUNTIME/WAITTIME/HOLDTIME 显示口径:与 mes.lookup_all_lots 一致('1.23h' / '-')。"""
    v = (v or "").strip()
    try:
        return f"{float(v):.2f}h"
    except (ValueError, TypeError):
        return v or "-"


def _int0(v):
    try:
        return int(float(v or 0))
    except (ValueError, TypeError):
        return 0


def _seq_key(seq):
    try:
        return int(seq)
    except (ValueError, TypeError):
        return 9999


def options(models=None, owners=None, products=None, path=None):
    """筛选候选:型号(PACKAGESIZE)/OWNERNAME/产品型号(PRODUCTNAME),按出现行数降序。
    联动规则(models 非空时按型号统计;owners/products 作为互相约束的过滤条件):
      - owners 候选   = 该型号 × 已选产品型号 条件下出现的 OWNERNAME
      - products 候选 = 该型号 × 已选 OWNERNAME 条件下出现的 PRODUCTNAME
      - 约束维度为空集合视为「不限」(候选不因另一维度清空而死锁)
    """
    rows = mes._load_rows(path)
    ms = {str(m).upper() for m in (models or [])} if models else None
    os_ = {str(o).upper() for o in owners} if owners else None
    ps_ = {str(x).upper() for x in products} if products else None
    m_cnt = defaultdict(int)
    o_cnt = defaultdict(int)
    p_cnt = defaultdict(int)
    for row in rows:
        p = (row.get("PACKAGESIZE") or "").strip()
        if ms and p.upper() not in ms:
            continue
        o = (row.get("OWNERNAME") or "").strip()
        pr = (row.get("PRODUCTNAME") or "").strip()
        if p:
            m_cnt[p] += 1
        # owners 候选:受已选产品型号约束(ps_ 为空=不限)
        if o and (not ps_ or (pr and pr.upper() in ps_)):
            o_cnt[o] += 1
        # products 候选:受已选 OWNERNAME 约束(os_ 为空=不限)
        if pr and (not os_ or (o and o.upper() in os_)):
            p_cnt[pr] += 1
    models = [m for m, _ in sorted(m_cnt.items(), key=lambda kv: -kv[1])]
    owners = [o for o, _ in sorted(o_cnt.items(), key=lambda kv: -kv[1])]
    products = [pr for pr, _ in sorted(p_cnt.items(), key=lambda kv: -kv[1])]
    return {"models": models, "owners": owners, "products": products}


def _filter(rows, owners, models, products, kw, prefix):
    """按 OWNERNAME×PACKAGESIZE×料号×工单前缀×关键字 过滤行(不区分大小写)。
    kw 统一按工单号前缀匹配(1 位起,越长越精准;无命中时由 _kw_rows 回退包含匹配)。
    语义:owners/models/products 为 None = 不过滤(全部);为空集合 = 过滤掉所有行。"""
    os_ = {str(o).upper() for o in owners} if owners is not None else None
    ms = {str(m).upper() for m in models} if models is not None else None
    ps_ = {str(x).upper() for x in products} if products is not None else None
    kw = (kw or "").strip().lower()
    pf = (prefix or "").strip().upper()
    for row in rows:
        psize = (row.get("PACKAGESIZE") or "").strip()
        if ms is not None and psize.upper() not in ms:
            continue
        prod = (row.get("PRODUCTNAME") or "").strip()
        if ps_ is not None and prod.upper() not in ps_:
            continue
        ow = (row.get("OWNERNAME") or "").strip()
        if os_ is not None and ow.upper() not in os_:
            continue
        full_wo = (row.get("MFGORDERNAME") or "").strip()
        if pf and not full_wo.upper().startswith(pf):
            continue
        if kw:
            # 统一按工单号前缀匹配(1 位起,E/F/0 → E/F/0 开头;越长越精准)
            if not full_wo.lower().startswith(kw):
                continue
        yield row


def _kw_rows(rows, owners, models, products, kw, prefix):
    """kw 过滤,对【工单号】三级匹配(逐级回退):
      1) 工单号前缀(1 位起,E/F/0 → 对应开头,越长越精准);
      2) 工单号末尾(如 E4***3582 输入 3582);
      3) 包含匹配(工单号/型号)。
    Lot 号不进 kw 过滤:Lot 必须完整输入,由 lot_lookup 精确匹配后自动跳转型号并高亮。"""
    kw = (kw or "").strip().lower()
    if not kw:
        return _filter(rows, owners, models, products, None, prefix)
    # 1) 工单号前缀
    hit = list(_filter(rows, owners, models, products, kw, prefix))
    if hit:
        return hit
    base = list(_filter(rows, owners, models, products, None, prefix))
    # 2) 工单号末尾
    tail = [r for r in base
            if (r.get("MFGORDERNAME") or "").lower().endswith(kw)]
    if tail:
        return tail
    # 3) 包含匹配(工单号 / 型号)
    return [r for r in base
            if (kw in (r.get("MFGORDERNAME") or "").lower()
                or kw in (r.get("PACKAGESIZE") or "").lower())]


def parse_flow_table(headers, rows):
    """把 Flow 流程表(已解析的 headers+rows)转成 {specs:[{seqno,spec,stepname}]}。
    列:SEQNO(序号)/SPECNAME(工序号)/STEPNAME(工序名,可空),按 SEQNO 升序。
    与 CT 表上传同款解析(文件名去扩展名=型号)。"""
    H = {h: i for i, h in enumerate(headers)}

    def col(r, n):
        i = H.get(n)
        return r[i] if i is not None and i < len(r) else ""

    items = []
    for r in rows:
        try:
            seqno = int(col(r, "SEQNO"))
        except (ValueError, TypeError):
            continue
        spec = (col(r, "SPECNAME") or "").strip()
        if not spec:
            continue
        items.append({"seqno": seqno, "spec": spec,
                      "stepname": (col(r, "STEPNAME") or "").strip()})
    items.sort(key=lambda x: x["seqno"])
    return {"specs": items}


def match_model(kw, path=None):
    """搜索自动切型号:输入能识别为 型号名/产品型号/工单号 时返回对应型号(PACKAGESIZE)。
    优先级:型号精确 > 产品型号包含 > 工单号前缀/包含(>2 位才生效) > 型号包含。
    完整 Lot 号由 lot_lookup 处理(在调用本函数前先试),不在此列。"""
    kw = (kw or "").strip()
    if not kw:
        return None
    kwl = kw.lower()
    rows = mes._load_rows(path)
    from collections import Counter
    psize_cnt = Counter()
    prod_cnt = Counter()
    wo_cnt = Counter()
    for row in rows:
        p = (row.get("PACKAGESIZE") or "").strip()
        if not p:
            continue
        psize_cnt[p] += 1
        pr = (row.get("PRODUCTNAME") or "").strip()
        if pr and kwl in pr.lower():
            prod_cnt[p] += 1
        wo = (row.get("MFGORDERNAME") or "").strip()
        if wo and (wo.lower().startswith(kwl) or kwl in wo.lower()):
            wo_cnt[p] += 1
    # 1) 型号名精确
    for p in psize_cnt:
        if p.lower() == kwl:
            return p
    # 2) 产品型号包含(如 TEST00000XXX / 2070 片段):≥3 位才生效(短输入是工单前缀意图)
    if len(kw) >= 3 and prod_cnt:
        return prod_cnt.most_common(1)[0][0]
    # 3) 工单号匹配:仅 ≥3 位生效(≤2 位是工单前缀过滤意图,不切型号)
    if len(kw) >= 3 and wo_cnt:
        return wo_cnt.most_common(1)[0][0]
    # 4) 型号名包含
    for p, _c in psize_cnt.most_common():
        if kwl in p.lower():
            return p
    return None


def resolve_flow_model(filename, path=None):
    """Flow 上传的型号解析:文件名去扩展名,优先精确匹配型号;
    否则从文件名中提取已知型号名(如 'MB23流程表.xlsx' / 'flow_MB23.xlsx' → MB23)。
    仍无匹配则按文件名原名。"""
    base = os.path.splitext(os.path.basename(filename or ""))[0].strip()
    if not base:
        return ""
    models = options(path)["models"]
    if base in models:
        return base
    hits = sorted((m for m in models if m.lower() in base.lower()),
                  key=len, reverse=True)   # 取最长匹配,避免 MB1 误配 MB12
    return hits[0] if hits else base


def lot_lookup(lot, path=None):
    """完整 Lot 号精确查找(搜索自动跳转用):
    返回 {model, owner, spec, spec_desc, status, wo, eqp, qty} 或 None。"""
    lot = (lot or "").strip()
    if not lot:
        return None
    rows = mes._load_rows(path)
    for row in rows:
        if (row.get("LOTID") or "").strip() == lot:
            raw = (row.get("STATUS") or "").strip()
            return {
                "model": (row.get("PACKAGESIZE") or "").strip(),
                "owner": (row.get("OWNERNAME") or "").strip(),
                "spec": (row.get("SPECNAME") or "").strip(),
                "spec_desc": (row.get("SPECDESC") or "").strip(),
                "status": mes.STATUS_MAP.get(raw, raw),
                "wo": (row.get("MFGORDERNAME") or "").strip(),
                "eqp": (row.get("EQPID") or "").strip(),
                "qty": _int0(row.get("QTY")),
            }
    return None


def _shift_elapsed(now=None):
    """本班次已过时长(小时):白班 07:30-19:30、夜班 19:30-次日07:30(与 wip_profile 同口径)。
    用于 MOVE 判定:某 lot 在当前工序停留时长(run+wait+hold)≤ 已过时长 → 视为本班次流入。"""
    now = now or datetime.datetime.now()
    _day = now.replace(hour=7, minute=30, second=0, microsecond=0)
    _night = now.replace(hour=19, minute=30, second=0, microsecond=0)
    if _day <= now < _night:
        shift_start = _day
    elif now >= _night:
        shift_start = _night
    else:                                   # 凌晨(00:00-07:30):夜班从昨日 19:30 起
        shift_start = _night - datetime.timedelta(days=1)
    return max(0.0, (now - shift_start).total_seconds() / 3600.0)


def _ph(v):
    """时长数值(小时):'5338.39h' → 5338.39;无效 → 0。"""
    try:
        return float(str(v).replace("h", "").strip())
    except (ValueError, TypeError):
        return 0.0


def profile(owners=None, models=None, kw=None, prefix=None, products=None,
           full=False, ct_skel=None, path=None):
    """按当前 CSV 快照聚合:各工序在制 Lot 分布(状态/颗数/本班次流入 MOVE)。
    排序:ct_skel(单选型号的工序骨架,优先 Flow 表,其次 CT 表)优先,其余按 SPECSEQUENCE。
    full=True 时把骨架中无在制数据的工序也补成占位行(搜索时显示完整流程)。
    返回 {summary, specs}:
      summary: {lots, qty, wo(工单数), hold(HOLD lot数), specs(工序数)}
      specs:   [{spec, desc, seq, run, wi, wo, hold, lots, qty, move, move_lots}]
    """
    elapsed_h = _shift_elapsed()
    rows = mes._load_rows(path)
    specs = {}
    wos = set()
    for row in _kw_rows(rows, owners, models, products, kw, prefix):
        spec = (row.get("SPECNAME") or "").strip()
        if not spec:
            continue
        raw = (row.get("STATUS") or "").strip()
        status = mes.STATUS_MAP.get(raw, raw)
        e = specs.get(spec)
        if e is None:
            e = specs[spec] = {
                "spec": spec, "desc": (row.get("SPECDESC") or "").strip(),
                "seq": (row.get("SPECSEQUENCE") or "").strip(),
                "run": 0, "wi": 0, "wo": 0, "hold": 0, "lots": 0, "qty": 0,
                "run_q": 0, "wi_q": 0, "wo_q": 0, "hold_q": 0,
                "move": 0, "move_lots": 0,
            }
        qty = _int0(row.get("QTY"))
        e["lots"] += 1
        e["qty"] += qty
        # MOVE(本班次流入该工序):停留时长 ≤ 班次已过时长(颗数与批数两种口径)
        if (_ph(row.get("RUNTIME")) + _ph(row.get("WAITTIME"))
                + _ph(row.get("HOLDTIME"))) <= elapsed_h:
            e["move"] += qty
            e["move_lots"] += 1
        if status == "运行中":
            e["run"] += 1
            e["run_q"] += qty
        elif status == "等待进站":
            e["wi"] += 1
            e["wi_q"] += qty
        elif status == "等待出站":
            e["wo"] += 1
            e["wo_q"] += qty
        elif status == "HOLD":
            e["hold"] += 1
            e["hold_q"] += qty
        wos.add((row.get("MFGORDERNAME") or "").strip())
    if ct_skel:
        pos = {x["spec"]: i for i, x in enumerate(ct_skel)}
        if full:
            # 完整流程:骨架里没有在制数据的工序补成占位行(柱高 0,可点看无数据)
            have = set(specs.keys())
            for x in ct_skel:
                if x["spec"] not in have:
                    specs[x["spec"]] = {
                        "spec": x["spec"], "desc": x.get("name", ""),
                        "seq": "", "run": 0, "wi": 0, "wo": 0, "hold": 0,
                        "lots": 0, "qty": 0,
                        "run_q": 0, "wi_q": 0, "wo_q": 0, "hold_q": 0,
                        "move": 0, "move_lots": 0,
                    }
        spec_list = sorted(specs.values(),
                           key=lambda s: (pos.get(s["spec"], 10 ** 9),
                                          _seq_key(s["seq"]), s["spec"]))
    else:
        spec_list = sorted(specs.values(),
                           key=lambda s: (_seq_key(s["seq"]), s["spec"]))
    total_lots = sum(s["lots"] for s in spec_list)
    return {
        "summary": {
            "lots": total_lots,
            "qty": sum(s["qty"] for s in spec_list),
            "wo": len(wos),
            "hold": sum(s["hold"] for s in spec_list),
            "specs": len(spec_list),
        },
        "specs": spec_list,
    }


def lots_of_spec(spec, owners=None, models=None, kw=None, prefix=None, products=None, path=None):
    """某工序下匹配的 Lot 明细(下钻弹窗用),含工单号/Lot/型号/料号/设备/状态/颗数/时长/
    HOLD原因,并附「预计加工完成」ETA(用设备能力 UPH 估算当前工序剩余加工量)。
    ETA 字段:eta_at(epoch 秒,None=不可估)、eta_rem(剩余小时)、eta_basis。
    """
    rows = mes._load_rows(path)
    out = []
    now = time.time()
    # 同工序一次性群算 ETA:先收集(含同炉识别),再回填,避免逐行各自算导致
    # 同炉多个 lot 的完成时刻不一致(烘烤/回流等批量站点同设备同炉同时完成)。
    rid_of = {}          # 行下标 -> 行序号
    rows_hit = []
    for row in _kw_rows(rows, owners, models, products, kw, prefix):
        if (row.get("SPECNAME") or "").strip() != spec:
            continue
        rows_hit.append(row)
    def _row_ph(row, col):
        return _ph(row.get(col))
    reps = [{"rid": i, "eqp": (r.get("EQPID") or "").strip(),
             "st": mes.STATUS_MAP.get((r.get("STATUS") or "").strip(),
                                      (r.get("STATUS") or "").strip()),
             "qty": _int0(r.get("QTY")),
             "run_h": _ph(r.get("RUNTIME"))} for i, r in enumerate(rows_hit)]
    idx = None
    if any(x["eqp"] for x in reps):
        need = [x for x in reps if x.get("st") in ("运行中", "等待出站") and x.get("eqp")]
        if need:
            idx = equip.uph_index()
            # 炉时来源:该(型号×工序)实测加工中位(ct_learn);无则 None(同炉不估)
            ring = None
            try:
                import ct_learn
                lc = ct_learn.learned_ct((rows_hit[0].get("PACKAGESIZE") or "").strip())
                if lc:
                    r_spec = lc.get(spec) or lc.get(spec.replace("-retest", ""))
                    if r_spec:
                        ring = (r_spec.get("run") or 0) or None
            except Exception:
                ring = None
            eta_map = equip.eta_group(reps, idx, (rows_hit[0].get("PACKAGESIZE") or "").strip(),
                                      spec, run_med=ring, now=now)
        else:
            eta_map = {}
    else:
        eta_map = {}
    for i, row in enumerate(rows_hit):
        raw = (row.get("STATUS") or "").strip()
        hold_reason = (row.get("HOLDREASONNAME") or "").strip()
        comment = (row.get("TXNCOMMENTS") or "").strip()
        st = mes.STATUS_MAP.get(raw, raw)
        qty = _int0(row.get("QTY"))
        run_h = _num_h(row.get("RUNTIME"))
        eqp = (row.get("EQPID") or "").strip()
        eta_at = eta_rem = eta_basis = ""
        r_eta = eta_map.get(i)
        if r_eta is not None:
            if r_eta:
                eta_rem = round(r_eta["rem_h"], 1)
                eta_basis = r_eta["basis"]
                eta_at = round(r_eta["at"])
        out.append({
            "full_wo": (row.get("MFGORDERNAME") or "").strip(),
            "lot_id": (row.get("LOTID") or "").strip(),
            "psize": (row.get("PACKAGESIZE") or "").strip(),
            "product": (row.get("PRODUCTNAME") or "").strip(),
            "owner": (row.get("OWNERNAME") or "").strip(),
            "eqp": eqp,
            "status": st,
            "qty": qty,
            "run_time": run_h,
            "wait_time": _num_h(row.get("WAITTIME")),
            "hold_time": _num_h(row.get("HOLDTIME")),
            "hold_reason": hold_reason,
            "comment": comment,
            "eta_at": eta_at,
            "eta_rem": eta_rem,
            "eta_basis": eta_basis,
        })
    out.sort(key=lambda l: (l["status"] == "运行中", l["status"] == "HOLD",
                            l["full_wo"], l["lot_id"]))
    return out
