# -*- coding: utf-8 -*-
"""设备能力统计(UPH):跨快照定稿每台设备的产能样本。

数据流:MES数据库.csv(RUNNING/HOLD 行,非虚拟站) → eqp_pending(跨快照跟踪)
      → eqp_samples(定稿:批次离开工序时,用最终 RUNTIME 定稿)。

样本口径:同一 Lot 在同一工序的 RUNTIME 是累计运行时长,快照间只增不减;
批次离开工序(快照中消失)前的最后一次观测 ≈ 完整加工时长,
UPH 样本 = QTY ÷ 最终RUNTIME(片/小时)。按 (型号, 工序, 设备) 分组取中位数即设备能力。
- 只收 RUNNING / HOLD 状态、带设备号(EQPID)、非虚拟站的行。
- HOLD 行保留在制存在性(防误定稿),运行时长取峰值(HOLD 期间设备停,时长冻结不涨);
  定稿时若批次最后观测状态是 HOLD(被卡住后移走,时长冻结失真)→ 不产生样本。
- 同一 Lot 中途换设备 → 合并到最终设备(不重复计样本)。
- 样本 60 天滚动清理(设备能力随换线/改造漂移,旧样本不具代表性;
  与 CT 实测学习 ct_learn 的样本窗口一致,ETA 组合的两处来源时间窗统一)。
"""
import os
import time

import mes
import db

SAMPLE_DAYS = 60                     # 样本保留天数(滚动清理,与 ct_learn 同口径)
RUN_MIN_H = 0.1                      # 定稿门槛:不足 0.1h(6分钟)的运行片段多为快照截断(刚上机即转走/
                                     #   同步间隔内完成),RUNTIME 严重低估导致 UPH 虚高,不计样本。
                                     #   长运行不受限:慢工序/小批次跑几小时~几十小时是真实加工。
_last_observe = {}                   # (category, path) -> mtime,防同快照重复处理


def _parse_h(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def observe_snapshot(path=None, models=None):
    """扫描一张 MES 快照,跨快照定稿设备 UPH 样本。每次 MES 同步调用一次。
    返回本次新定稿样本数。
    - models:型号白名单(后台已录入型号);None=学全表。型号规整为 PACKAGESIZE
      级精确匹配,只对命中的型号建样本(塑封/灌封各自只收自己的型号)。
    """
    path = path or mes.DEFAULT_CSV
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return 0
    if _last_observe.get((db.get_category(), path)) == mt:
        return 0
    try:
        if os.path.getsize(path) < 100:      # 空/损坏快照不处理,防止把全部在制批次误定稿
            return 0
    except OSError:
        return 0
    wl = None
    if models is not None:
        wl = {mes.base_model(m).upper() for m in models}
        wl.discard("")
    # 1) 扫快照:(model, spec, lot) -> 观测值(最后观测状态用于 HOLD 定稿剔除)
    snap = {}
    defn = {}    # (model, spec, workflow) -> {seq: 出现次数}:全状态行的流程定义
    try:
        for row in mes._load_rows(path):
            model = mes.base_model(row.get("PACKAGESIZE") or "")
            spec = (row.get("SPECNAME") or "").strip()
            wf = (row.get("WORKFLOWNAME") or "").strip()
            seq = (row.get("SPECSEQUENCE") or "").strip()
            # 定义收集不区分状态(WaitingTrackIn 等行也定义工序在流程中的位置)
            if model and spec and wf and seq and (wl is None or model.upper() in wl):
                m = defn.setdefault((model, spec, wf), {})
                m[seq] = m.get(seq, 0) + 1
            lid = (row.get("LOTID") or "").strip()
            if not model or not spec or not lid:
                continue
            if wl is not None and model.upper() not in wl:
                continue
            st = (row.get("STATUS") or "").strip().upper()
            if st not in ("RUNNING", "HOLD"):
                continue
            desc = (row.get("SPECDESC") or "").strip()
            if mes.spec_is_virtual(spec, desc):
                continue
            eqp = (row.get("EQPID") or "").strip()
            rt = _parse_h(row.get("RUNTIME"))
            qty = _parse_h(row.get("QTY"))
            key = (model, spec, lid)
            cur = snap.get(key)
            if cur is None:
                snap[key] = {"rt": rt, "qty": qty, "eqp": eqp,
                             "product": (row.get("PRODUCTNAME") or "").strip(),
                             "seq": (row.get("SPECSEQUENCE") or "").strip(),
                             "workflow": (row.get("WORKFLOWNAME") or "").strip(),
                             "desc": desc,
                             "status": st}
            else:
                if rt > cur["rt"]:       # 运行时长取峰值
                    cur["rt"] = rt
                if qty > 0:              # 数量/设备/料号取最新观测
                    cur["qty"] = qty
                if eqp:
                    cur["eqp"] = eqp
                if desc:
                    cur["desc"] = desc
                if (row.get("WORKFLOWNAME") or "").strip():
                    cur["workflow"] = (row.get("WORKFLOWNAME") or "").strip()
                cur["status"] = st       # 状态取最后观测
    except OSError:
        return 0

    conn = db.get_conn()
    now = time.time()
    try:
        # 2) 读出现有 pending(旧库可能无 status 列 → 视为 RUNNING,行为兼容)
        pending = {}
        for r in conn.execute(
                "SELECT model, product, spec, seq, workflow, eqp, lot, run_h, qty, status"
                " FROM eqp_pending"):
            pending[(r["model"], r["spec"], r["lot"])] = {
                "product": r["product"], "seq": r["seq"], "eqp": r["eqp"],
                "run": r["run_h"], "qty": r["qty"], "desc": "",
                "workflow": (r["workflow"] or ""),
                "status": (r["status"] or "RUNNING").upper()}
        # 3) 合并本次快照(新 lot 建记录;换设备/数量/料号取最新;run 取峰值)
        for key, v in snap.items():
            pv = pending.get(key)
            if pv is None:
                if not v["eqp"]:   # 无历史且无设备号 → 无法归属,跳过
                    continue
                pending[key] = {"product": v["product"], "seq": v["seq"],
                                "eqp": v["eqp"], "run": v["rt"],
                                "qty": v["qty"], "desc": v["desc"],
                                "workflow": v["workflow"],
                                "status": v["status"]}
            else:
                if v["rt"] > pv["run"]:
                    pv["run"] = v["rt"]
                if v["qty"] > 0:
                    pv["qty"] = v["qty"]
                if v["eqp"]:
                    pv["eqp"] = v["eqp"]
                if v["product"]:
                    pv["product"] = v["product"]
                if v["seq"]:
                    pv["seq"] = v["seq"]
                if v["desc"]:
                    pv["desc"] = v["desc"]
                if v["workflow"]:
                    pv["workflow"] = v["workflow"]
                pv["status"] = v["status"]
        # 4) 定稿:快照中消失(=已流转离开工序)的 lot → UPH 样本入库
        #    口径:HOLD 冻结的时长不代表加工能力(被卡住后移走),不产生样本;
        #        RUN_MIN_H 以下为快照截断碎片;长运行不限(慢工序真实)。
        finalized = 0
        for key in [k for k in list(pending) if k not in snap]:
            pv = pending.pop(key)
            run = pv["run"]
            qty = pv["qty"]
            if (pv["status"] != "HOLD" and run >= RUN_MIN_H
                    and qty > 0 and pv["eqp"]):
                conn.execute(
                    "INSERT INTO eqp_samples (model, product, spec, spec_desc, seq, workflow,"
                    " eqp, lot, qty, run_h, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (key[0], pv["product"], key[1], pv["desc"], pv["seq"],
                     pv["workflow"], pv["eqp"], key[2], int(qty), round(run, 3), round(now)))
                finalized += 1
            conn.execute("DELETE FROM eqp_pending WHERE model=? AND spec=? AND lot=?",
                         (key[0], key[1], key[2]))
        # 5) 剩余 pending 全量写回(在制 lot 量级远小于全表,单事务内快)
        conn.execute("DELETE FROM eqp_pending")
        conn.executemany(
            "INSERT INTO eqp_pending (model, product, spec, seq, workflow, eqp, lot, run_h,"
            " qty, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(k[0], pv["product"], k[1], pv["seq"], pv["workflow"], pv["eqp"], k[2],
              round(pv["run"], 3), int(pv["qty"]), pv["status"])
             for k, pv in pending.items()])
        # 6) 用本快照的最新工序描述回填缺失的 spec_desc(历史样本立即显示中文/英文工序名)
        desc_map = {}
        for (_m, spec, _lot), v in snap.items():
            if v["desc"]:
                desc_map[spec] = v["desc"]
        for spec, d in desc_map.items():
            conn.execute("UPDATE eqp_samples SET spec_desc=? WHERE spec=? AND (spec_desc IS NULL OR spec_desc='')",
                         (d, spec))
        # 6b) 用本快照的工作流回填缺失的 workflow(旧样本立即参与主流程排序;
        #     同一型号×工序取样本最多的工作流;60 天滚动清理会自然淘汰误判)
        wf_map = {}
        for (m, spec, _lot), v in snap.items():
            if not v["workflow"]:
                continue
            cnt = wf_map.setdefault((m, spec), {})
            cnt[v["workflow"]] = cnt.get(v["workflow"], 0) + 1
        for (m, spec), cnt in wf_map.items():
            wf = max(cnt, key=cnt.get)
            conn.execute("UPDATE eqp_samples SET workflow=? WHERE model=? AND spec=?"
                         " AND (workflow IS NULL OR workflow='')", (wf, m, spec))
        # 7) 滚动清理 SAMPLE_DAYS 天前样本
        cutoff = now - SAMPLE_DAYS * 86400
        conn.execute("DELETE FROM eqp_samples WHERE finished_at < ?", (cutoff,))
        # 8) 刷新工作流定义表(每张快照全量重建,量级 ~2000 行,单事务内快;
        #    同 (型号,工作流,工序) 多个序号时取出现最多者)
        conn.execute("DELETE FROM eqp_wfdef")
        conn.executemany(
            "INSERT INTO eqp_wfdef (model, workflow, spec, seq) VALUES (?,?,?,?)",
            [(m, wf, spec, max(cnt, key=cnt.get))
             for (m, spec, wf), cnt in defn.items()])
        conn.commit()
    finally:
        conn.close()
    _last_observe[(db.get_category(), path)] = mt
    return finalized


def _median(vals):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return None
    if n % 2:
        return vals[n // 2]
    return (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def uph_index():
    """一次性全表聚合的 UPH 索引(行级 ETA / 工单风险预测共用,避免逐行查询)。
    返回 {(model, spec, eqp): {"uph": 中位, "n": 样本数}},
    并附工序级回退键 (model, spec, "")(该工序全部设备样本的中位)。
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT model, spec, eqp, qty, run_h FROM eqp_samples WHERE run_h > 0")
        by_eqp = {}    # (m, s, e) -> [uph]
        by_spec = {}   # (m, s)   -> [uph]
        for r in rows:
            m = (r["model"] or "").strip()
            s = (r["spec"] or "").strip()
            e = (r["eqp"] or "").strip()
            if not m or not s:
                continue
            uph = r["qty"] / r["run_h"]
            by_eqp.setdefault((m, s, e), []).append(uph)
            by_spec.setdefault((m, s), []).append(uph)
        idx = {}

        def put(key, vals):
            med = _median(vals)
            if med:
                idx[key] = {"uph": med, "n": len(vals)}

        for (m, s, e), vals in by_eqp.items():
            put((m, s, e), vals)
        for (m, s), vals in by_spec.items():
            put((m, s, ""), vals)
        return idx
    finally:
        conn.close()


def finish_info(index, model, spec, eqp, qty, run_h):
    """用 UPH 索引估算某批次「当前工序剩余加工小时」(纯加工,不含等待/停机)。
    返回 {"rem_h": 剩余小时, "basis": "设备"|"工序"} 或 None = 无样本不可估。
    回退链:设备级样本 → 工序级(全部设备)样本;rem_h 恒 ≥ 0(已超出样本能力的按 0)。"""
    if not index:
        return None
    m = (model or "").strip()
    s = (spec or "").strip()
    e = (eqp or "").strip()
    k = (m, s, "" if not e else e)
    got = None
    if k in index:
        got = index[k]
    else:
        ks = (m, s, "")
        if ks in index:
            got = index[ks]
    if not got or not got.get("uph"):
        return None
    try:
        rem = (float(qty or 0) / got["uph"]) - (float(run_h or 0) or 0.0)
    except (ValueError, TypeError):
        return None
    return {"rem_h": max(0.0, rem), "basis": "设备" if k in index else "工序"}


def eta_group(reps, index, model, spec, run_med=None, now=None):
    """同型号×工序的一批在制 lot 的 ETA 群算,带【同炉】识别。
    reps: [{rid, eqp, st, qty, run_h}](rid 由调用方给,用于回填);
          预筛:无设备 / 未上机(等待进站)的项不参与,不入返回。
    run_med: 该(型号×工序)的实测加工时长中位(即「炉时/典型批时长」,来自 ct_learn
             学习样本;用于同炉站的剩余=炉时−已烘时长)。None=无炉时来源。
    同炉判据(烘烤/回流等批量站点):同一设备 + RUNTIME 聚簇(相邻差<
    max(0.05h, 10%×簇内中位))。同一炉的多个 lot 完成时刻相同,剩余统一取
    max(0, 炉时 − 簇内最大已烘时长)——数据验证:同炉 lot 的 RUNTIME 完全一致
    (如 MEOV030 同炉 13 个 lot 全部 4.17h)。
    单 lot(无同炉)走 finish_info(设备 UPH 回退链)。
    返回 {rid: {"rem_h","basis","at"} | None}; None 值 = 同炉但无炉时来源,不可估。"""
    now = now or time.time()
    out = {}
    active = [r for r in reps if r.get("eqp") and r.get("st") in ("运行中", "等待出站")]
    if not active:
        return out
    # 同炉阈值:相邻差 < max(0.05h, 10%×该设备聚类样本中位)
    # (簇内 max 递推:排序后,新 lot 与上一簇最大 run 之差小于阈值 = 同簇)
    from collections import defaultdict
    by_eqp = defaultdict(list)
    for r in active:
        by_eqp[r["eqp"]].append(r)
    for eqp, rs in by_eqp.items():
        rs = sorted(rs, key=lambda r: r["run_h"])
        med = _median([r["run_h"] for r in rs] or [0.0]) or 1.0
        gap = max(0.05, 0.10 * med)
        clusters = []
        for r in rs:
            if clusters and r["run_h"] - clusters[-1][1] < gap:
                clusters[-1][0].append(r)
                clusters[-1][1] = r["run_h"]
            else:
                clusters.append([[r], r["run_h"]])
        for members, cl_max in clusters:
            if len(members) >= 2:          # 同炉
                if run_med and run_med > 0:
                    rem = max(0.0, run_med - cl_max)
                    for r in members:
                        out[r["rid"]] = {"rem_h": rem, "basis": "同炉",
                                         "at": now + rem * 3600}
                else:
                    for r in members:      # 同炉但缺炉时 → 不估,显示 —
                        out[r["rid"]] = None
            else:                          # 单 lot:设备 UPH 回退链
                r = members[0]
                fi = finish_info(index, model, spec, r["eqp"], r["qty"], r["run_h"])
                if fi:
                    out[r["rid"]] = {"rem_h": fi["rem_h"], "basis": fi["basis"],
                                     "at": now + fi["rem_h"] * 3600}
    return out


def models():
    """型号列表:每个型号的样本数 / 工序数 / 设备数 / UPH 中位数(设备 UPH 中位数的中位数)。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT model, COUNT(*) AS samples, COUNT(DISTINCT spec) AS specs,"
            " COUNT(DISTINCT eqp) AS eqps"
            " FROM eqp_samples GROUP BY model ORDER BY model").fetchall()
        out = []
        for r in rows:
            devs = {}
            for qty, run_h, eqp in conn.execute(
                    "SELECT qty, run_h, eqp FROM eqp_samples WHERE model=?",
                    (r["model"],)):
                if run_h > 0:
                    devs.setdefault(eqp, []).append(qty / run_h)
            dev_uphs = [_median(v) for v in devs.values() if v]
            out.append({"model": r["model"], "samples": r["samples"],
                        "specs": r["specs"], "eqps": r["eqps"],
                        "uph": _median(dev_uphs)})
        return out
    finally:
        conn.close()


def _seq_key(seq):
    """流程序号排序键:数字序号升序在前,空/非数字序号排最后。"""
    return (0 if seq.isdigit() else 999999, int(seq) if seq.isdigit() else 0)


def specs_of(model, product=None):
    """某型号(可限料号)的工序列表:设备数 / 样本数 / UPH 中位数(设备 UPH 中位数的中位数)。
    同名工序跨流程重复(如 MB1 在一次回流在 MB_SA01/MB_SA02 流程里序号不同)
    → 合并为一行,位置取样本最多的工作流(主流程)的序号,样本/UPH 按全部样本重算。
    排序:以【后台CT表管理·上传的CT表】工序顺序为准(与 CT 数据校准页一致);
    不在 CT 表内的(返工/专用)排最后,再按样本序号(主流程优先)→ 工序名。"""
    conn = db.get_conn()
    try:
        if product:
            rows = conn.execute(
                "SELECT spec, spec_desc, seq, workflow, eqp, qty, run_h FROM eqp_samples"
                " WHERE model=? AND product=?", (model, product))
        else:
            rows = conn.execute(
                "SELECT spec, spec_desc, seq, workflow, eqp, qty, run_h FROM eqp_samples"
                " WHERE model=?", (model,))
        agg = {}
        wf_n = {}    # 工作流 -> 样本数(主流程优先的排序锚点)
        swf = {}     # (工序, 工作流) -> {n, seqs:{seq:n}} 各流程内该工序的位置分布
        for r in rows:
            if r["run_h"] <= 0:
                continue
            wf = (r["workflow"] or "")
            if wf:
                wf_n[wf] = wf_n.get(wf, 0) + 1
                m = swf.setdefault((r["spec"], wf), {"n": 0, "seqs": {}})
                m["n"] += 1
                if r["seq"]:
                    seqs = m["seqs"]
                    seqs[r["seq"]] = seqs.get(r["seq"], 0) + 1
            devs = agg.setdefault(r["spec"], {})
            if r["spec_desc"]:
                devs["__desc"] = r["spec_desc"]
            devs.setdefault(r["eqp"], []).append(r["qty"] / r["run_h"])
        out = []
        global_main = max(wf_n, key=wf_n.get) if wf_n else ""
        for spec, devs in agg.items():
            # 同名工序合并:位置锚点取【全型号主流程】(样本最多的流程)的定义序号;
            # 主流程定义里没有该工序(返工/专用工序)时,退回该工序样本最多的流程
            cands = [(k[1], m) for k, m in swf.items() if k[0] == spec]
            main_wf = ""
            if cands:
                if global_main and conn.execute(
                        "SELECT 1 FROM eqp_wfdef WHERE model=? AND workflow=? AND spec=?",
                        (model, global_main, spec)).fetchone():
                    main_wf = global_main
                else:
                    main_wf = max(cands, key=lambda x: x[1]["n"])[0]
            main_m = next((m for k, m in swf.items()
                           if k[0] == spec and k[1] == main_wf), None) if main_wf else None
            main_seq = ""
            if main_m:
                main_seq = max(main_m["seqs"].items(), key=lambda x: x[1])[0] if main_m["seqs"] else ""
            if main_wf:
                dr = conn.execute("SELECT seq FROM eqp_wfdef WHERE model=? AND workflow=? AND spec=?",
                                  (model, main_wf, spec)).fetchone()
                if dr and dr["seq"]:
                    main_seq = dr["seq"]
            dev_uphs = [_median(v) for v in devs.values() if isinstance(v, list) and v]
            out.append({"spec": spec,
                        "seq": main_seq,
                        "desc": devs.get("__desc", ""),
                        "eqp_cnt": len(devs) - (1 if "__desc" in devs else 0),
                        "samples": sum(len(v) for v in devs.values() if isinstance(v, list)),
                        "uph": _median(dev_uphs),
                        "_wf": main_wf})
        # 排序:以「后台 CT 表管理上传的 CT 表」工序顺序为准(用户口径:与 CT 表/CT 校准一致);
        # CT 表里没有的工序(返工/专用)排最后,按样本序号(主流程优先)→ 工序名稳定。
        ct_pos = {}
        try:
            ctab = db.get_ct_table(model)
        except Exception:
            ctab = None
        if ctab:
            for it in sorted(ctab.get("specs") or [],
                             key=lambda y: y.get("seqno") or 0):
                it_spec = (it.get("spec") or "").strip().split("-retest")[0]
                if it_spec and it_spec not in ct_pos:
                    ct_pos[it_spec] = len(ct_pos)
        out.sort(key=lambda x: (ct_pos.get(x["spec"].split("-retest")[0], 999999),
                                _seq_key(x["seq"]),
                                -wf_n.get(x["_wf"], 0),
                                x["spec"]))
        for o in out:
            o.pop("_wf", None)
        return out
    finally:
        conn.close()


def products_of(model):
    """某型号下出现过的具体料号(PRODUCTNAME),供型号内细分。"""
    conn = db.get_conn()
    try:
        return [r["product"] for r in conn.execute(
            "SELECT DISTINCT product FROM eqp_samples"
            " WHERE model=? AND product!='' ORDER BY product", (model,))]
    finally:
        conn.close()


def _agg_by_eqp(rows):
    """按设备聚合样本:UPH 中位数 / 样本数 / 最近定稿时间 / 样本明细。
    明细按料号(product)合并:相同料号只保留一行,该行 UPH 为其下全部样本的
    平均数(样本越多越准确),同时给出该料号样本数与最近定稿时间。"""
    agg = {}
    for r in rows:
        run_h = r["run_h"]
        if run_h <= 0:
            continue
        a = agg.setdefault(r["eqp"],
                           {"samples": 0, "uphs": [], "last_at": 0, "detail": {}})
        a["samples"] += 1
        uph = r["qty"] / run_h
        a["uphs"].append(uph)
        a["last_at"] = max(a["last_at"], r["finished_at"])
        p = (r["product"] or "")
        pd = a["detail"].setdefault(p, {"product": p, "samples": 0,
                                        "uph_sum": 0.0, "at": 0})
        pd["samples"] += 1
        pd["uph_sum"] += uph
        pd["at"] = max(pd["at"], r["finished_at"])
    out = []
    for eqp, a in agg.items():
        a["detail"] = sorted(
            ({"product": pd["product"], "samples": pd["samples"],
              "uph": round(pd["uph_sum"] / pd["samples"], 1), "at": pd["at"]}
             for pd in a["detail"].values()), key=lambda x: -x["at"])
        out.append({"eqp": eqp, "samples": a["samples"],
                    "uph": _median(a["uphs"]), "last_at": a["last_at"],
                    "detail": a["detail"]})
    out.sort(key=lambda x: -x["samples"])
    return out


def eqps_of(model, spec, product=None):
    """某型号某工序(可限料号)的设备明细:每台的 UPH 中位数 / 样本数 / 最近定稿
    / 样本明细(按料号合并:每料号一行,UPH 为该料号下全部样本的平均数)。"""
    conn = db.get_conn()
    try:
        if product:
            rows = conn.execute(
                "SELECT eqp, product, qty, run_h, finished_at FROM eqp_samples"
                " WHERE model=? AND spec=? AND product=? ORDER BY finished_at DESC",
                (model, spec, product))
        else:
            rows = conn.execute(
                "SELECT eqp, product, qty, run_h, finished_at FROM eqp_samples"
                " WHERE model=? AND spec=? ORDER BY finished_at DESC",
                (model, spec))
        return _agg_by_eqp(rows)
    finally:
        conn.close()


def search_eqp(kw):
    """设备反查:该设备做过的 型号×工序,含 UPH 中位数 / 样本数 / 最近定稿
    / 样本明细(按料号合并:相同料号一行,UPH 取该料号下全部样本的平均数)。"""
    like = "%" + kw + "%"
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT model, spec, spec_desc, seq, workflow, eqp, product,"
            " qty, run_h, finished_at"
            " FROM eqp_samples WHERE eqp LIKE ?", (like,))
        agg = {}
        wf_n = {}    # (型号, 工作流) -> 样本数(主流程优先的排序锚点)
        swf = {}     # (型号, 工序, 工作流) -> {n, seqs:{seq:n}} 各流程内该工序的位置分布
        for r in rows:
            run_h = r["run_h"]
            if run_h <= 0:
                continue
            wf = (r["workflow"] or "")
            if wf:
                wf_n[(r["model"], wf)] = wf_n.get((r["model"], wf), 0) + 1
                m = swf.setdefault((r["model"], r["spec"], wf), {"n": 0, "seqs": {}})
                m["n"] += 1
                if r["seq"]:
                    seqs = m["seqs"]
                    seqs[r["seq"]] = seqs.get(r["seq"], 0) + 1
            key = (r["model"], r["spec"])
            a = agg.setdefault(key,
                               {"samples": 0, "uphs": [], "last_at": 0,
                                "desc": "", "workflow": "", "detail": {}})
            a["samples"] += 1
            uph = r["qty"] / run_h
            a["uphs"].append(uph)
            a["last_at"] = max(a["last_at"], r["finished_at"])
            if r["spec_desc"]:
                a["desc"] = r["spec_desc"]
            if wf:
                a["workflow"] = wf
            p = (r["product"] or "")
            pd = a["detail"].setdefault(p, {"product": p, "samples": 0,
                                            "uph_sum": 0.0, "at": 0})
            pd["samples"] += 1
            pd["uph_sum"] += uph
            pd["at"] = max(pd["at"], r["finished_at"])
        out = []
        gmain = {}    # 型号 -> (主流程, 样本数):全型号样本最多的流程
        for (m, wf), n in wf_n.items():
            if n > gmain.get(m, (None, -1))[1]:
                gmain[m] = (wf, n)
        for (model, spec), a in agg.items():
            a["detail"] = sorted(
                ({"product": pd["product"], "samples": pd["samples"],
                  "uph": round(pd["uph_sum"] / pd["samples"], 1), "at": pd["at"]}
                 for pd in a["detail"].values()), key=lambda x: -x["at"])
            # 同名工序合并:位置锚点取【该型号主流程】的定义序号;
            # 主流程定义里没有该工序时,退回该工序样本最多的流程
            cands = [(k[2], m) for k, m in swf.items() if k[0] == model and k[1] == spec]
            main_wf = ""
            if cands:
                gm = gmain.get(model, ("", 0))[0]
                if gm and conn.execute(
                        "SELECT 1 FROM eqp_wfdef WHERE model=? AND workflow=? AND spec=?",
                        (model, gm, spec)).fetchone():
                    main_wf = gm
                else:
                    main_wf = max(cands, key=lambda x: x[1]["n"])[0]
            main_m = next((m for k, m in swf.items()
                           if k[0] == model and k[1] == spec and k[2] == main_wf), None) if main_wf else None
            main_seq = ""
            if main_m:
                main_seq = max(main_m["seqs"].items(), key=lambda x: x[1])[0] if main_m["seqs"] else ""
            if main_wf:
                dr = conn.execute("SELECT seq FROM eqp_wfdef WHERE model=? AND workflow=? AND spec=?",
                                  (model, main_wf, spec)).fetchone()
                if dr and dr["seq"]:
                    main_seq = dr["seq"]
            out.append({"model": model, "spec": spec,
                        "seq": main_seq,
                        "desc": a["desc"],
                        "samples": a["samples"], "uph": _median(a["uphs"]),
                        "last_at": a["last_at"], "detail": a["detail"],
                        "_wf": main_wf})
        # 按型号 + 流程序号升序(空序号排最后),同序号按工作流样本量(主流程最前)再按工序名
        out.sort(key=lambda x: (x["model"], _seq_key(x["seq"]),
                                -wf_n.get((x["model"], x["_wf"]), 0),
                                x["spec"]))
        for o in out:
            o.pop("_wf", None)
        return out
    finally:
        conn.close()
