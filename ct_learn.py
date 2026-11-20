# -*- coding: utf-8 -*-
"""CT 动态学习(AI动态分析):
长期累积 MES 各 (产品型号 × 工序SPECNAME) 的实际 RUNTIME / WAITTIME 样本,
用中位数得出更精确的实测 CT,供人工导出后再上传使用,并回灌交期风险预测。

约束与设计:
- 只对「后台 CT 管理里已上传的型号」累积,控制体积(model 白名单由 db 提供)。
- 样本库存本地文件夹 ct_samples/<MODEL>.json,逐型号独立,迭代更新。
- 去重:同一 Lot 在同一工序,只在它「离开该工序」后记一次它的最终(最大)时长。
- 代表值用中位数(抗异常)。样本 < MIN_SAMPLES 标注「样本不足,仅供参考」。
- 纯标准库实现。

【v1.1 优化】
1. 样本带时间戳(ts,epoch 秒):支持「近 RECENT_DAYS 天优先」,抵抗工艺改善/产线
   变化带来的节拍漂移;近段样本不足时自动退回全量。
2. 同时学习 WAITTIME(排队等待),供风险预测用「加工+等待」真实流转时间估剩余天数。
3. 置信度改用稳健离散度 MAD(中位绝对偏差)/中位数,代替对长尾异常敏感的 CV,
   制造周期数据(长尾、偶发 HOLD)下更合理。
4. 新增 learned_ct(model):产出 {spec:{run,wait}} 供 db 交期风险预测回灌。
全部兼容旧版 JSON 样本库(纯数值 samples / 旧 pending 结构会自动迁移)。
"""
import json
import mes
import os
import csv
import time

import db

def _sample_dir():
    """当前类别(塑封/灌封)的 CT 实测样本目录,两类隔离避免互相覆盖。"""
    return os.path.join(db.DATA_DIR, "ct_samples_%s" % db.get_category())
MIN_SAMPLES = 20            # 低于此样本数视为不可信,导出时标注
MAX_SAMPLES_KEEP = 1000    # 每工序最多保留的样本数(滚动,FIFO:新样本更替最老)
RECENT_DAYS = 60           # 代表值优先取近多少天的样本(不足则退回全量)
SAMPLE_DAYS = 60           # 入库样本保留天数(滚动清理,与设备能力样本同口径、与近段优先
                           #   窗口一致——回退全量时数据寿命也止于此,节拍漂移反映更快)
RUN_MIN_H = 0.1            # 定稿门槛:运行不足 0.1h(6分钟)的多为快照截断碎片
                           #   (刚上机即转走/同步间隔内完成),RUNTIME 严重低估,不收样本。
FRAG_MIN_H = 0.05          # 代表值计算前碎片下限(绝对):低于此视为截断碎片
FRAG_RATIO = 0.3           # 代表值计算前碎片剔除比例:小于该工序粗中位 30% 的样本剔除
                           #   (自适应:快速检验工序真实节拍可低至 0.15h,固定门槛分不开)

# ===== 实测 CT 校准(v1.3:observe 主路 + scan 兜底)=====
# 准确性主路 = observe_snapshot:跨快照定稿「已流出工序批次的完整 RUNTIME+WAITTIME」,
#   无删失、无长度偏倚(详见 observe_snapshot 文档)。learned_ct/summary 优先用它。
# 兜底 = scan_all:单张 MES 快照按 PACKAGESIZE×SPECNAME 聚合,但做口径修正——
#   RUNTIME 只取已加工/加工中状态、WAITTIME 只取等待上机状态、HOLD 整行剔除。
#   单快照仍有删失/长度偏倚,故仅在 observe 样本不足的工序作过渡估计。
# 校准面向 NTO 工单(优先级高,工序等待一般 ≤3h)。量产数据里单条记录 WAITTIME 超过
# WAIT_CAP_H 的,视为低优先级长排队/异常,不代表 NTO,该条 wait 失效不收纳。
WAIT_CAP_H = 3.0                      # 单条等待失效阈值(小时):面向NTO,>3h的等待不收纳
# 「过 FVI 工序后」(打码/包装/包装检查/入库等)单条 RUNTIME 失效阈值(小时):
# 这类收尾工序批次常因等待发货/集货长时间挂在站上,RUNTIME 被异常拉高,不代表真实
# 加工节拍。超过此值的 run 样本在校准时整条剔除(防止把 CT 中位数污染拉高)。适用所有型号。
POST_FVI_RUN_CAP_H = 3.0
LEARN_MIN_SAMPLES = 5                 # 进度分段/ETA 回灌所需最小样本(不足则退回上传CT)
# 有效性口径:run 与 wait 各自独立判定——
#   · run 有效 = RUNTIME>0(已加工/加工中);
#   · wait 有效 = 0 ≤ WAITTIME ≤ WAIT_CAP_H。
# 单条记录 WAITTIME 超过 WAIT_CAP_H 只让「该条的 wait」失效(不计入该工序 wait 校准),
# 它的 run 仍然有效、其他工序与其他样本照常计算。两者不绑定、不互相作废。
_scan_cache = {}                      # path -> (mtime, {model: {spec: {...}}})
_last_observe = {}                    # (category, path) -> mtime(observe 去重;按类别分:塑封/灌封各自独立写样本,不能互相跳过)

# STATUS 口径(scan 兜底用):RUNTIME 只在已加工/加工中状态才有意义;
# WaitingTrackIn 阶段尚未上机 RUNTIME≈0,WAITTIME 才是真实排队;HOLD 为异常停留。
RUN_STATUS = {"RUNNING", "WAITINGMOVEOUT", "运行中", "等待出站"}
WAIT_STATUS = {"WAITINGTRACKIN", "等待进站"}


def _ensure_dir():
    if not os.path.isdir(_sample_dir()):
        os.makedirs(_sample_dir(), exist_ok=True)


def _path(model):
    safe = "".join(c for c in (model or "") if c.isalnum() or c in "_-").upper()
    return os.path.join(_sample_dir(), f"{safe}.json")


def _migrate_rec(rec):
    """把单个工序记录升级到 v1.1 结构(兼容旧版)。
    旧:{"seq","samples":[num,...],"pending":{lid:num}}
    新:{"seq","samples":[num,...],"wait_samples":[num,...],"ts":[epoch|None,...],
        "pending":{lid:{"run":h,"wait":h}}}
    """
    rec.setdefault("samples", [])
    rec.setdefault("wait_samples", [])
    rec.setdefault("ts", [])
    # 时间戳列表与 samples 对齐(旧样本无时间戳,补 None,视为最老)
    if len(rec["ts"]) < len(rec["samples"]):
        rec["ts"] = [None] * (len(rec["samples"]) - len(rec["ts"])) + rec["ts"]
    elif len(rec["ts"]) > len(rec["samples"]):
        rec["ts"] = rec["ts"][-len(rec["samples"]):]
    # wait_samples 不足补 0(旧版未采集等待)
    if len(rec["wait_samples"]) < len(rec["samples"]):
        rec["wait_samples"] = ([0.0] * (len(rec["samples"]) - len(rec["wait_samples"]))
                               + rec["wait_samples"])
    # pending 旧格式 {lid:num} → {lid:{"run":num,"wait":0}}
    pend = rec.get("pending", {})
    newp = {}
    for lid, v in pend.items():
        if isinstance(v, dict):
            newp[lid] = {"run": float(v.get("run") or 0), "wait": float(v.get("wait") or 0)}
        else:
            newp[lid] = {"run": float(v or 0), "wait": 0.0}
    rec["pending"] = newp
    return rec


def _load(model):
    p = _path(model)
    if not os.path.exists(p):
        return {"model": model, "specs": {}}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {"model": model, "specs": {}}
    for rec in data.get("specs", {}).values():
        _migrate_rec(rec)
    return data


def _save(model, data):
    _ensure_dir()
    tmp = _path(model) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _path(model))


def _median(nums):
    if not nums:
        return 0.0
    s = sorted(nums)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _purge_frags(vals):
    """计算代表值前的碎片净化:剔除 < max(FRAG_MIN_H, FRAG_RATIO×粗中位) 的样本。
    污染机理:跨快照定稿的快照截断碎片(近 0 时长)与真实短节拍样本混在一起,
    MAD 被拉大 → 样本再多集中度也差(Eq: 0.2h 工序混 15% 的 0.02h 碎片)。
    自适应下限:快检工序(真实节拍 0.15~0.2h)与碎片同一数值区,固定门槛切不开,
    用该工序自身粗中位的 30% 切。样本 < MIN_SAMPLES 时不动(样本少,剔除风险大于收益)。
    """
    if len(vals) < MIN_SAMPLES:
        return list(vals)
    med0 = _median(vals)
    if med0 <= 0:
        return list(vals)
    cut = max(FRAG_MIN_H, FRAG_RATIO * med0)
    return [v for v in vals if v >= cut]


def _recent(samples, ts, waits, days=RECENT_DAYS):
    """取近 days 天的 (run, wait) 样本;若近段不足 MIN_SAMPLES,退回全量。
    ts 中为 None 的(旧无时间戳样本)视为「老」,只在退回全量时纳入。
    """
    if not samples:
        return [], []
    cutoff = time.time() - days * 86400.0
    runs_r, waits_r = [], []
    for i, v in enumerate(samples):
        t = ts[i] if i < len(ts) else None
        if t is not None and t >= cutoff:
            runs_r.append(v)
            waits_r.append(waits[i] if i < len(waits) else 0.0)
    if len(runs_r) >= MIN_SAMPLES:
        return runs_r, waits_r
    # 近段不足:用全量
    return list(samples), list(waits[:len(samples)]) + [0.0] * max(0, len(samples) - len(waits))


def _confidence(nums):
    """可信度百分比(0-100):基于样本 CT 的集中度 + 样本量。
    集中度改用稳健离散度:rCV = 1.4826 * MAD / 中位数(MAD=中位绝对偏差),
    对长尾/偶发异常比标准差-CV 更不敏感。
    concentration = max(0, 1 - rCV);样本量权重 size_w = min(1, n/MIN_SAMPLES)。
    返回 (confidence_pct, concentration_pct, rcv)。
    """
    n = len(nums)
    if n == 0:
        return (0.0, 0.0, None)
    med = _median(nums)
    if med <= 0:
        return (0.0, 0.0, None)
    if n == 1:
        return (round(100 * (1 / MIN_SAMPLES), 1), 100.0, 0.0)
    mad = _median([abs(x - med) for x in nums])
    rcv = (1.4826 * mad) / med          # 稳健变异系数
    concentration = max(0.0, 1.0 - rcv)
    size_w = min(1.0, n / float(MIN_SAMPLES))
    conf = concentration * size_w * 100.0
    return (round(conf, 1), round(concentration * 100, 1), round(rcv, 3))


def observe(model, info):
    """用一次 MES 快照更新该型号的样本库(迭代)。
    info = mes.lookup_order 的返回(含 variants→processes→lot_list)。
    每个工序结构需含 spec、seq、lot_list[{lot_id, run_time, wait_time}]。
    返回本次新「定稿」的样本数。
    """
    if not model or not info or not info.get("variants"):
        return 0
    data = _load(model)
    specs = data.setdefault("specs", {})

    # 1) 收集本次快照中:每个 spec 下,各 lot 当前的 RUNTIME/WAITTIME(小时)
    seen = {}    # spec -> {lot_id: {"run":h,"wait":h}}
    seq_of = {}  # spec -> seqno
    for v in info["variants"]:
        for p in v.get("processes", []):
            spec = p.get("spec") or ""
            if not spec:
                continue
            if "HTTVS" in spec.upper():   # 虚拟报废工序,计算无意义,跳过
                continue
            seq_of.setdefault(spec, p.get("seq") or "")
            d = seen.setdefault(spec, {})
            for lot in p.get("lot_list", []):
                lid = lot.get("lot_id") or ""
                rt = _parse_h(lot.get("run_time"))
                wt = _parse_h(lot.get("wait_time"))
                if lid and rt is not None and rt > 0:
                    cur = d.setdefault(lid, {"run": 0.0, "wait": 0.0})
                    cur["run"] = max(cur["run"], rt)
                    if wt is not None and wt > 0:
                        cur["wait"] = max(cur["wait"], wt)

    finalized = 0
    now = time.time()
    all_specs = set(specs.keys()) | set(seen.keys())
    for spec in all_specs:
        rec = specs.setdefault(spec, {"seq": seq_of.get(spec, ""), "samples": [],
                                      "wait_samples": [], "ts": [], "pending": {}})
        _migrate_rec(rec)
        if seq_of.get(spec):
            rec["seq"] = seq_of[spec]
        cur = seen.get(spec, {})            # 本次仍在该工序的 lot
        pending = rec["pending"]
        # 更新仍在制 lot 的最大 runtime/waittime
        for lid, vals in cur.items():
            pv = pending.setdefault(lid, {"run": 0.0, "wait": 0.0})
            pv["run"] = max(pv["run"], vals["run"])
            pv["wait"] = max(pv["wait"], vals["wait"])
        # 已消失的 lot → 定稿
        gone = [lid for lid in pending.keys() if lid not in cur]
        for lid in gone:
            v = pending.pop(lid)
            run = v.get("run") or 0.0
            if run >= RUN_MIN_H:      # <0.1h 多为快照截断碎片,不收(与设备能力同口径)
                rec["samples"].append(round(run, 3))
                rec["wait_samples"].append(round(v.get("wait") or 0.0, 3))
                rec["ts"].append(round(now))
                finalized += 1
        # 滚动裁剪:先清 SAMPLE_DAYS 天前样本(旧无时间戳的视为过期一并清),再按 FIFO 裁到上限
        cutoff = now - SAMPLE_DAYS * 86400.0
        keep = [i for i, t in enumerate(rec["ts"] or []) if (t or 0) >= cutoff]
        if len(keep) != len(rec["samples"]):
            rec["samples"] = [rec["samples"][i] for i in keep]
            rec["wait_samples"] = [rec["wait_samples"][i] for i in keep]
            rec["ts"] = [rec["ts"][i] for i in keep]
        if len(rec["samples"]) > MAX_SAMPLES_KEEP:
            rec["samples"] = rec["samples"][-MAX_SAMPLES_KEEP:]
            rec["wait_samples"] = rec["wait_samples"][-MAX_SAMPLES_KEEP:]
            rec["ts"] = rec["ts"][-MAX_SAMPLES_KEEP:]

    if finalized or seen:
        _save(model, data)
    return finalized


def _parse_h(s):
    """把 '1.98h' / '1.98' / '-' 解析为小时浮点;无效返回 None。"""
    if s is None:
        return None
    t = str(s).strip().lower().rstrip("h").strip()
    if not t or t == "-":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def summary(model):
    """返回该型号各工序的实测 CT 概况(供页面展示)。
    [{seq, spec, samples, median_h, wait_median_h, pending, reliable,
      confidence, concentration, cv}]
    """
    out = []
    for spec, rec in _calibrated_specs(model).items():
        # 可信度:基于【加工节拍(RUNTIME)】的集中度(净化后样本)——等待时长大量为 0 是
        # 真实快速流转,不是污染,混入会把集中度砸低;加工节拍才是工艺能力的度量主体。
        # (旧口径的 cycles 数组从未填充;wait 0 值样本会让 MAD 虚高,故固定用 run。)
        base = rec["runs"]
        conf, concentration, cv = _confidence(base)
        reliable = rec["src"] == "observe" and rec["n"] >= MIN_SAMPLES
        if rec["src"] == "scan":
            conf = round(conf * 0.7, 1)    # 单快照兜底估计,可信度打折
        out.append({
            "seq": rec["seq"],
            "spec": spec,
            "samples": rec["n"],                 # 净化后有效样本数
            "raw_n": rec.get("raw_n", rec["n"]), # 净化前(近段原始)样本数
            "median_h": round(rec["cycle_med"], 2),       # 实测周期CT(加工+等待)
            "run_median_h": round(rec["run_med"], 2),     # 其中加工
            "wait_median_h": round(rec["wait_med"], 2),   # 其中等待(单条>WAIT_CAP_H(3h)已失效剔除)
            "reliable": reliable,
            "confidence": conf,            # 可信度百分比 0-100
            "concentration": concentration,  # 集中度百分比 0-100
            "cv": cv,                      # 稳健变异系数(供参考)
            "src": rec["src"],             # observe=跨快照定稿(准)/ scan=单快照兜底
        })
    # 排序:以上传 CT 表(后台CT表管理)的工序顺序为准(与设备能力页一致);
    # 不在表内的(返工/专用)排最后,再按 MES 序号 → 工序名稳定。
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
    out.sort(key=lambda r: (ct_pos.get(r["spec"].split("-retest")[0], 999999),
                            _seq_int(r["seq"]), r["spec"]))
    return out


def scan_all(path=None):
    """一次扫 MES 全表,按 PACKAGESIZE(型号)× SPECNAME(工序)聚合实测时长。
    返回 {model: {spec: {"seq": SPECSEQUENCE, "runs": [...], "waits": [...]}}}。
    口径修正:RUNTIME 只取 RUN_STATUS(已加工/加工中)行,WAITTIME 只取 WAIT_STATUS
    (等待上机)行;HOLD 行整条剔除;排除含 HTTVS 的虚拟工序。仅作 observe 的兜底。
    按 csv mtime 缓存(全表 5 万+ 行,避免 gantt/sync 多次重扫)。
    """
    import mes   # 延迟导入,避免任何潜在循环引用
    path = path or mes.DEFAULT_CSV
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _scan_cache.get(path)
    if cached and cached[0] == mt:
        return cached[1]
    try:
        enc = mes._detect_open(path)
    except Exception:
        enc = "utf-8-sig"
    out = {}
    try:
        with open(path, encoding=enc, errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                model = (row.get("PACKAGESIZE") or "").strip()
                spec = (row.get("SPECNAME") or "").strip()
                if not model or not spec or "HTTVS" in spec.upper():
                    continue
                st = (row.get("STATUS") or "").strip().upper()
                if "HOLD" in st:            # HOLD 异常停留(等料/品质/Q-TIME),整行剔除
                    continue
                rt = _parse_h(row.get("RUNTIME")) or 0.0
                wt = _parse_h(row.get("WAITTIME")) or 0.0
                seq = (row.get("SPECSEQUENCE") or "").strip()
                srec = out.setdefault(model, {}).setdefault(
                    spec, {"seq": seq, "runs": [], "waits": []})
                if not srec["seq"] and seq:
                    srec["seq"] = seq
                # 口径修正:RUNTIME 只取已加工/加工中状态(否则 WaitingTrackIn 的
                # run≈0 会把加工CT 拉低);WAITTIME 只取等待上机状态(真实排队)。
                if st in RUN_STATUS and rt > 0:
                    srec["runs"].append(rt)
                if st in WAIT_STATUS and 0 < wt <= WAIT_CAP_H:
                    srec["waits"].append(wt)
    except OSError:
        return _scan_cache.get(path, (mt, {}))[1]
    _scan_cache[path] = (mt, out)
    return out


def _model_specs(model):
    """取某型号的扫描聚合结果(按 PACKAGESIZE 精确匹配,大小写不敏感)。"""
    allm = scan_all()
    mu = (model or "").strip().upper()
    for k, v in allm.items():
        if k.upper() == mu:
            return v
    return {}


def learned_ct(model):
    """供进度分段 + 交期风险回灌:返回 {spec: {"run":中位加工h, "wait":中位等待h,
    "cycle":中位周期h}}。cycle = 工序周期(加工+正常等待),供进度分段/ETA 使用。
    数据源:_calibrated_specs(observe 跨快照定稿为主,scan 单快照口径修正兜底);
    样本不足的工序不输出,由上层 blend_ct 退回上传 CT。
    run/wait 各自独立剔除无效样本(单条 wait>WAIT_CAP_H(3h) 失效不连累 run),取中位数。
    """
    out = {}
    for spec, rec in _calibrated_specs(model).items():
        if rec["cycle_med"] <= 0:
            continue
        out[spec] = {"run": round(rec["run_med"], 3),
                     "wait": round(rec["wait_med"], 3),
                     "cycle": round(rec["cycle_med"], 3)}
    return out


def export_rows(model):
    """导出实测 CT(套 OTDO_CT 格式)。
    返回 (headers, rows)。rows 列:SEQNO, SPECNAME, OTDO_CT, 等待CT, 样本数, 可信度。
    OTDO_CT 用中位数(小时)。样本不足的也导出,但「可信度」标注「仅供参考」。
    """
    headers = ["SEQNO", "SPECNAME", "OTDO_CT", "等待CT(h)", "样本数",
               "可信度(%)", "集中度(%)", "建议"]
    rows = []
    for s in summary(model):
        rows.append([
            s["seq"], s["spec"], s["median_h"], s["wait_median_h"], s["samples"],
            s["confidence"], s["concentration"],
            "可用" if s["reliable"] else f"样本不足(<{MIN_SAMPLES}),仅供参考",
        ])
    return headers, rows


def export_csv(model):
    """生成导出用 CSV 文本(utf-8-sig,Excel 友好)。"""
    import io
    headers, rows = export_rows(model)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def models_with_samples():
    """有实测样本的型号列表(observe 样本库 ∪ scan 快照,按 PACKAGESIZE)。"""
    s = set(scan_all().keys())
    if os.path.isdir(_sample_dir()):
        for fn in os.listdir(_sample_dir()):
            if fn.endswith(".json"):
                s.add(fn[:-5])
    return sorted(s)


def _seq_int(x):
    try:
        return int(x)
    except (ValueError, TypeError):
        return 999999


def _post_fvi_specs(model):
    """返回该型号「FVI 检验全部完成之后」的 SPECNAME 集合(打码/包装/包装检查/入库等)。
    依据后台上传的 CT 表:取「最后一个 FVI 工序」的累计CT,其后(cum_ct 更大)的工序即为
    post-FVI。用最后一个 FVI 作边界,可避免把 FVI 检验站本身(可能多道 FVI1/FVI2)误纳入,
    只截取真正的收尾包装段。无 CT 表 / 该型号无 FVI 工序时返回空集合(即不对该型号启用过滤)。
    用于 _calibrated_specs:这些工序单条 RUNTIME>POST_FVI_RUN_CAP_H 的样本不计入校准。
    """
    try:
        import db   # 延迟导入,避免模块级循环引用
        ctab = db.get_ct_table(model)
    except Exception:
        return set()
    if not ctab or not ctab.get("specs"):
        return set()
    specs = ctab["specs"]
    fvi_cums = [(s.get("cum_ct", 0) or 0) for s in specs
                if "FVI" in (s.get("spec") or "").upper()]
    if not fvi_cums:
        return set()
    fvi_cum = max(fvi_cums)          # 最后一个 FVI 工序的累计CT(FVI 检验整体终点)
    eps = 1e-6
    return {(s.get("spec") or "").strip() for s in specs
            if (s.get("cum_ct", 0) or 0) > fvi_cum + eps and (s.get("spec") or "").strip()}


def _calibrated_specs(model):
    """统一实测工序时长数据源,带分层 fallback,供 learned_ct / summary 共用。
      第一层:observe_snapshot 跨快照定稿样本(无删失/无长度偏倚,最准);
      第二层:scan_all 单快照口径修正估计(仅补 observe 未覆盖/样本不足的工序)。
    返回 {spec: {seq, src, runs[], waits[], cycles[], run_med, wait_med,
                 cycle_med, n}}。cycles 仅 observe 层有(同批次 run+wait 配对)。
    """
    out = {}
    post_fvi = _post_fvi_specs(model)   # 过 FVI 后工序:单条 run>POST_FVI_RUN_CAP_H 不计入校准
    # ---- 第一层:observe 定稿样本(近 RECENT_DAYS 天优先)----
    for spec, rec in _load(model).get("specs", {}).items():
        runs = rec.get("samples", [])
        waits = rec.get("wait_samples", [])
        runs_r, waits_r = _recent(runs, rec.get("ts", []), waits)
        # run / wait 各自独立筛有效样本:单条 wait>WAIT_CAP_H(3h) 只让该条 wait 失效,不连累 run
        runs_valid = [r for r in runs_r if r > 0]
        raw_n = len(runs_valid)
        # 过 FVI 后工序(打码/包装/包装检查/入库等):剔除单条 RUNTIME>3h 的停滞污染样本
        if spec in post_fvi:
            runs_valid = [r for r in runs_valid if r <= POST_FVI_RUN_CAP_H]
        waits_valid = [w for w in waits_r if 0 <= w <= WAIT_CAP_H]
        # 碎片净化:近 0 截断样本剔除后重算(见 _purge_frags 说明),再判样本是否充足
        runs_valid = _purge_frags(runs_valid)
        if len(runs_valid) < LEARN_MIN_SAMPLES:    # run 样本不足→该工序退回 scan/上传CT
            continue
        run_med = _median(runs_valid)
        wait_med = _median(waits_valid) if waits_valid else 0.0
        out[spec] = {"seq": rec.get("seq", ""), "src": "observe",
                     "runs": runs_valid, "waits": waits_valid, "cycles": [],
                     "run_med": run_med, "wait_med": wait_med,
                     "cycle_med": run_med + wait_med,
                     "n": len(runs_valid), "raw_n": raw_n}
    # ---- 第二层:scan_all 兜底(只补第一层缺失的工序)----
    for spec, rec in _model_specs(model).items():
        if spec in out:
            continue
        runs = rec.get("runs", [])
        waits = rec.get("waits", [])
        raw_n = len(runs)
        # 过 FVI 后工序(打码/包装/包装检查/入库等):剔除单条 RUNTIME>3h 的停滞污染样本
        if spec in post_fvi:
            runs = [r for r in runs if r <= POST_FVI_RUN_CAP_H]
        # 碎片净化(同观察层):近 0 截断样本剔除后在判充足
        runs = _purge_frags(runs)
        # run 是校准主体(主要耗时在 runtime):样本不足则整道工序不校准,退回上传 CT
        # (避免单样本噪声,也避免 run 归零只剩 wait)。wait 不足则该工序 wait 取 0。
        if len(runs) < LEARN_MIN_SAMPLES:
            continue
        run_med = _median(runs)
        wait_med = _median(waits) if len(waits) >= LEARN_MIN_SAMPLES else 0.0
        out[spec] = {"seq": rec.get("seq", ""), "src": "scan",
                     "runs": runs, "waits": waits, "cycles": [],
                     "run_med": run_med, "wait_med": wait_med,
                     "cycle_med": run_med + wait_med,
                     "n": max(len(runs), len(waits)), "raw_n": raw_n}
    return out


def observe_snapshot(path=None, models=None):
    """【CT 准确性主路 v1.3】用一张 MES 全表快照,跨快照定稿各型号工序的完整周期样本。

    与 scan_all(单快照直接统计、有删失+长度偏倚)不同:本函数为每个在制 LOTID 维护其
    在当前工序的 RUNTIME/WAITTIME 峰值(pending,持久化于 ct_samples/<MODEL>.json);
    当某 LOTID 从该工序快照中消失(=已流转去下一工序)时,把它「离开前的完整
    RUNTIME+WAITTIME」定稿成一个无偏样本。每次 MES 刷新(每 3~5 分钟)调用一次即可
    持续累积。批次离开前最后一张快照≈完整停留,故 run/wait 峰值即完整加工/等待时长。

    - models:学习白名单(后台已录入/已上传型号);None=学全表。型号规整为 PACKAGESIZE
      级前缀匹配,只对命中的型号建样本库以控体积。
    - HOLD 状态行:仍保留其在制存在性(避免被误判离开而提前定稿),但不更新 wait 峰值,
      使异常 HOLD 停留不污染等待样本;run 仍可更新。
    - 定稿时 WAITTIME>WAIT_CAP_H 的样本视为异常,整条剔除。
    返回本次新定稿样本总数。
    """
    import mes
    path = path or mes.DEFAULT_CSV
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return 0
    if _last_observe.get((db.get_category(), path)) == mt:   # 本类别已扫过该快照才跳过(塑封/灌封互不影响)
        return 0
    wl = None
    if models is not None:
        wl = {mes.base_model(m).upper() for m in models}
        wl.discard("")
    try:
        enc = mes._detect_open(path)
    except Exception:
        enc = "utf-8-sig"
    # 1) 扫快照:model -> spec -> {lot: {"run","wait"}};seqmap:model -> spec -> seq
    snap, seqmap = {}, {}
    try:
        with open(path, encoding=enc, errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                model = (row.get("PACKAGESIZE") or "").strip()
                spec = (row.get("SPECNAME") or "").strip()
                lid = (row.get("LOTID") or "").strip()
                if not model or not spec or not lid or "HTTVS" in spec.upper():
                    continue
                if wl is not None and model.upper() not in wl:
                    continue
                st = (row.get("STATUS") or "").strip().upper()
                rt = _parse_h(row.get("RUNTIME")) or 0.0
                wt = _parse_h(row.get("WAITTIME")) or 0.0
                d = snap.setdefault(model, {}).setdefault(spec, {})
                cur = d.setdefault(lid, {"run": 0.0, "wait": 0.0})
                cur["run"] = max(cur["run"], rt)
                if "HOLD" not in st:         # HOLD 期间不累计等待峰值
                    cur["wait"] = max(cur["wait"], wt)
                seqmap.setdefault(model, {}).setdefault(
                    spec, (row.get("SPECSEQUENCE") or "").strip())
    except OSError:
        return 0
    # 2) 逐型号定稿(含「样本库已有 pending 但本次快照该型号消失」的型号)
    models_seen = set(snap.keys())
    if os.path.isdir(_sample_dir()):
        for fn in os.listdir(_sample_dir()):
            if fn.endswith(".json"):
                mm = fn[:-5]
                if wl is None or mm.upper() in wl:
                    models_seen.add(mm)
    now = time.time()
    total_final = 0
    for model in models_seen:
        data = _load(model)
        specs = data.setdefault("specs", {})
        cur_specs = snap.get(model, {})
        finalized = 0
        for spec in set(specs.keys()) | set(cur_specs.keys()):
            rec = specs.setdefault(spec, {"seq": "", "samples": [],
                                          "wait_samples": [], "ts": [], "pending": {}})
            _migrate_rec(rec)
            sq = seqmap.get(model, {}).get(spec, "")
            if sq:
                rec["seq"] = sq
            cur = cur_specs.get(spec, {})        # 本次仍在该工序的 lot
            pending = rec["pending"]
            for lid, vals in cur.items():
                pv = pending.setdefault(lid, {"run": 0.0, "wait": 0.0})
                pv["run"] = max(pv["run"], vals["run"])
                pv["wait"] = max(pv["wait"], vals["wait"])
            for lid in [l for l in pending if l not in cur]:   # 已离开工序 → 定稿
                v = pending.pop(lid)
                run = v.get("run") or 0.0
                wait = v.get("wait") or 0.0
                # run≥0.1h 才定稿(快照截断碎片不收);wait 原样存,超 WAIT_CAP_H(3h) 的在统计时单独失效
                if run >= RUN_MIN_H:
                    rec["samples"].append(round(run, 3))
                    rec["wait_samples"].append(round(wait, 3))
                    rec["ts"].append(round(now))
                    finalized += 1
            # 滚动裁剪:先清 SAMPLE_DAYS 天前样本(同 observe 主路),再按 FIFO 裁到上限
            cutoff = now - SAMPLE_DAYS * 86400.0
            keep = [i for i, t in enumerate(rec["ts"] or []) if (t or 0) >= cutoff]
            if len(keep) != len(rec["samples"]):
                rec["samples"] = [rec["samples"][i] for i in keep]
                rec["wait_samples"] = [rec["wait_samples"][i] for i in keep]
                rec["ts"] = [rec["ts"][i] for i in keep]
            if len(rec["samples"]) > MAX_SAMPLES_KEEP:
                rec["samples"] = rec["samples"][-MAX_SAMPLES_KEEP:]
                rec["wait_samples"] = rec["wait_samples"][-MAX_SAMPLES_KEEP:]
                rec["ts"] = rec["ts"][-MAX_SAMPLES_KEEP:]
        if finalized or cur_specs:
            _save(model, data)
        total_final += finalized
    _last_observe[(db.get_category(), path)] = mt
    return total_final
