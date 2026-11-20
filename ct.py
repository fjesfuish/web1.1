# -*- coding: utf-8 -*-
"""CT 表管理:存储各型号 CT 表,按当前工序计算进度%,并据交期判风险。
进度 = 当前工序累计CT / 总CT
匹配兜底:原样 SPECNAME → 去 -retest 等后缀 → 用 SEQNO 往前找上一个能匹配的工序
风险:富余天数 = 距交期天数 − 剩余需要天数
"""
import datetime

# 风险按 buffer(富余)天数分级,保守口径(统一按天数,不再用相对百分比):
#   buffer = 计划交付日 − 预计完工日
#   buffer < 0(有滞后)        → 高风险
#   0 ≤ buffer < 1(几乎无buffer)→ 低风险
#   1 ≤ buffer < 2             → 正常
#   buffer ≥ 2                 → 超前
RISK_AHEAD = 2.0     # ≥2 天 → 超前
RISK_NORMAL = 1.0    # 1~2 天 → 正常
RISK_LOW = 0.0       # 0~1 天 → 低风险;<0(有滞后)→ 高风险
HOURS_PER_DAY = 24.0


def parse_ct_table(headers, rows):
    """把 CT 表(已解析的 headers+rows)转成结构化数据。
    返回 {
      "total_ct": 总CT,
      "specs": [{"seqno":int, "spec":SPECNAME, "ct":float, "cum_ct":累计,
                 "stepname":中文名, "stage":工段}...](按 SEQNO 升序),
      "spec_cum": {SPECNAME: 累计CT},
      "spec_start": {SPECNAME: 起点累计CT},
      "spec_ct": {SPECNAME: 单工序CT},
    }
    """
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
        try:
            ct = float(col(r, "OTDO_CT") or 0)
        except (ValueError, TypeError):
            ct = 0.0
        items.append({
            "seqno": seqno,
            "spec": (col(r, "SPECNAME") or "").strip(),
            "ct": ct,
            "stepname": (col(r, "STEPNAME") or "").strip(),
            "stage": (col(r, "STAGE") or "").strip(),
        })
    items.sort(key=lambda x: x["seqno"])
    cum = 0.0
    spec_cum = {}
    spec_start = {}
    spec_ct = {}
    for it in items:
        spec_start[it["spec"]] = cum    # 该工序起点 = 做完上一道工序的累计
        cum += it["ct"]
        it["cum_ct"] = cum
        spec_cum[it["spec"]] = cum
        spec_ct[it["spec"]] = it["ct"]
    return {"total_ct": cum, "specs": items,
            "spec_cum": spec_cum, "spec_start": spec_start, "spec_ct": spec_ct}


def blend_ct(ctab, learned):
    """用学习到的实测 CT 覆盖上传 CT 表里各工序的加工时长,返回新表(不改原表)。
    learned: {spec: {"run": h, "wait": h}}(见 ct_learn.learned_ct)。
    有实测样本(run>0)的工序用实测值替换其 ct,无样本工序保留上传值;
    随后按相同算法重算 cum_ct/total_ct/spec_start/spec_ct,使进度分段、进度%、
    ETA、FVI 截点都一致采用「学习后 CT」。无 learned 时原样返回。
    """
    if not ctab or not ctab.get("specs") or not learned:
        return ctab
    items = []
    for it in ctab["specs"]:
        spec = it.get("spec", "")
        lk = learned.get(spec) or learned.get(_strip_suffix(spec))
        ni = dict(it)
        if lk:
            cyc = lk.get("cycle")
            if cyc is None:
                cyc = (lk.get("run") or 0) + (lk.get("wait") or 0)
            if cyc and cyc > 0:
                ni["ct"] = float(cyc)
        items.append(ni)
    cum = 0.0
    spec_cum, spec_start, spec_ct = {}, {}, {}
    for it in items:
        spec_start[it["spec"]] = cum
        cum += it["ct"]
        it["cum_ct"] = cum
        spec_cum[it["spec"]] = cum
        spec_ct[it["spec"]] = it["ct"]
    return {"total_ct": cum, "specs": items,
            "spec_cum": spec_cum, "spec_start": spec_start, "spec_ct": spec_ct}


def _strip_suffix(spec):
    """去掉 -retest / -rework 等后缀。"""
    for sep in ("-retest", "-rework", "-RETEST", "-REWORK"):
        if spec.endswith(sep):
            return spec[: -len(sep)]
    # 通用:去掉末尾 -xxx
    if "-" in spec:
        return spec.rsplit("-", 1)[0]
    return spec


def match_cum_ct(cur_spec, cur_seq, ct):
    """求当前工序的累计 CT(用于进度)。
    **只按 SPECNAME 匹配**(MES 的 seq 与 CT 表 seqno 不是同一套编号)。
    返回的是该工序的【起点累计】(= 做完上一道工序的累计),
    因为 lot 当前【正在做】这道工序、还没做完,完成位置应是它的起点。
    匹配兜底:1.原样命中 2.去后缀命中;匹配不上→None(虚拟站等不计)。
    返回 (起点累计CT 或 None, 匹配方式, 命中的 spec 名)
    """
    spec_start = ct.get("spec_start", {})
    if cur_spec in spec_start:
        return spec_start[cur_spec], "exact", cur_spec
    base = _strip_suffix(cur_spec)
    if base in spec_start:
        return spec_start[base], "stripped", base
    return None, "none", None


def fvi_cutoff(ct):
    """该型号「到达第一个 FVI 工序」的起点累计CT(= 做完 FVI 前一道工序的累计)。
    厂内验证工单作业到 FVI 即视为完成,用它作为 compute 的 target_cum。
    SPECNAME 含 'FVI' 即认定为 FVI 工序;无 FVI 返回 None。
    """
    specs = ct.get("specs", [])
    fvis = [s for s in specs if "FVI" in (s.get("spec") or "").upper()]
    if not fvis:
        return None
    first = min(fvis, key=lambda s: s.get("cum_ct", 0) or 0)
    # 起点累计 = 该 FVI 工序累计 − 本工序CT(= 做完它前一道的累计)
    return (first.get("cum_ct", 0) or 0) - (first.get("ct", 0) or 0)


def _remain_hours_real(ct, cur_cum, matched_spec, learned, cap_cum=None):
    """估算从当前位置到完工的【真实经过小时】(加工 RUNTIME + 排队 WAITTIME)。
    learned: {spec: {"run": 中位运行h, "wait": 中位等待h}},缺某工序则退回该工序标准 CT。
    剩余范围 = 当前正在做的工序 + 其后所有工序(cum_ct > cur_cum 的工序)。
    cap_cum: 截止累计CT(到 FVI 即完成);只累计 cum_ct ≤ cap_cum 的工序(FVI 及其后不计)。
    返回 (剩余真实小时, 是否用到了实测数据 bool)。
    """
    specs = ct.get("specs", [])
    used = False
    total_h = 0.0
    eps = 1e-6
    for sp in specs:
        cum = sp.get("cum_ct", 0) or 0
        if cum <= cur_cum + eps:    # 已完成的工序跳过
            continue
        if cap_cum is not None and cum > cap_cum + eps:   # 截止点(FVI)之后不计入
            continue
        name = sp.get("spec", "")
        lk = (learned or {}).get(name) or (learned or {}).get(_strip_suffix(name))
        if lk and (lk.get("run") or lk.get("wait")):
            total_h += float(lk.get("run") or 0) + float(lk.get("wait") or 0)
            used = True
        else:
            total_h += float(sp.get("ct", 0) or 0)   # 退回标准 CT
    return total_h, used


def _grade_abs(slack):
    if slack >= RISK_AHEAD:
        return "超前"
    if slack >= RISK_NORMAL:
        return "正常"
    if slack >= RISK_LOW:
        return "低风险"
    return "高风险"


def _grade_rel(slack, total_days):
    # 已统一为按 buffer 天数分级(保守口径),不再用相对百分比;保留签名兼容旧调用。
    return _grade_abs(slack)


def compute(cur_spec, cur_seq, ct, plan_end=None, today=None,
            learned=None, plan_start=None, cur_run_h=None, target_cum=None,
            resume_date=None, cur_dev_rem_h=None):
    """综合计算:进度% + 风险等级 + 预计完工日(ETA)。
    cur_spec/cur_seq: MES 当前工序名与工序号(取最慢分身)
    ct: parse_ct_table 的结果
    plan_end: 计划交付日 'YYYY-MM-DD'
    plan_start: 计划开始日 'YYYY-MM-DD'(给出后启用相对富余度阈值)
    learned: 实测 CT {spec: {"run":h,"wait":h}}(给出后用真实流转周期估剩余时间)
    cur_run_h: 当前工序已耗运行小时(给出后做工序内进度插值)
    target_cum: 截止累计CT(厂内验证工单到 FVI 即完成,见 fvi_cutoff)。给出后
                进度% 与剩余时间/ETA 都按截止点折算;到达截止点即 100%、剩余 0。
    resume_date: 人工「恢复作业日」'YYYY-MM-DD'(挂起:等料/设备/其他可协调异常)。
                给出且晚于今天时,作业从该日起算 —— 等待期间不推进,ETA 与风险据此顺延。
    cur_dev_rem_h: 当前工序「设备校准剩余纯加工小时」(调用方按设备 UPH 样本算出:
                  max(0, 当前批颗数÷设备UPH − 已运行))。None=无修正(行为=现状)。
                  给出时:实测(learned)分支补入当前工序此剩余(此前因进度插值被跳过);
                  标准 CT 分支把它替换掉当前工序整段 CT(该段此前全额计入)。口径:纯加工,
                  不含等待/停机,与 learned 的「加工+等待」口径分开标注。
    返回 {progress, risk_level, risk_note, remain_days, slack_days, matched,
          match_how, eta, predict_basis}
    """
    total = ct["total_ct"] or 1
    # 有效终点:验证工单到 FVI 即完成,否则按整条 CT
    eff_total = target_cum if (target_cum and target_cum > 0) else total
    cum, how, hit_spec = match_cum_ct(cur_spec, cur_seq, ct)
    if cum is None:
        return {"progress": 0, "risk_level": "", "risk_note": "当前工序不在CT表",
                "remain_days": None, "slack_days": None, "matched": False,
                "eta": None, "predict_basis": ""}

    # 进度:默认按当前工序起点累计;若提供已耗运行小时,则在本工序区间内插值
    pos = cum
    if cur_run_h is not None and hit_spec:
        cur_ct = (ct.get("spec_ct", {}) or {}).get(hit_spec, 0) or 0
        if cur_ct > 0:
            pos = cum + max(0.0, min(float(cur_run_h), cur_ct))
    # 进度按有效终点折算:已到/越过截止点(FVI)即 100%
    progress = max(0, min(100, round(pos / eff_total * 100)))

    # 已到达有效终点:验证单到 FVI、普通单到整条 CT 末端,工序已全部做完,
    # 没有剩余作业,因此不再按计划交期判「超期/滞后」,直接视为完成。
    if pos >= eff_total:
        done_note = ("已到目标/完成工序,作业完成" if (target_cum and target_cum > 0)
                     else "已完成全部 CT 工序")
        done_day = (today or datetime.date.today()).isoformat()
        return {"progress": 100, "risk_level": "超前", "risk_note": done_note,
                "remain_days": 0.0, "slack_days": None, "matched": True,
                "match_how": how, "eta": done_day, "predict_basis": "完成"}

    # 剩余时间:有实测数据用「加工+等待」真实小时;否则退回纯 CT ÷ 24
    # 验证工单只累计截止点(FVI)之前尚未完成的工序
    cap = target_cum if (target_cum and target_cum > 0) else None
    remain_h_real, used_learned = _remain_hours_real(ct, cum, hit_spec, learned, cap_cum=cap)
    if used_learned:
        remain_days = remain_h_real / HOURS_PER_DAY
        basis = "实测(含等待)"
    else:
        remain_ct = max(0.0, eff_total - cum)
        remain_days = remain_ct / HOURS_PER_DAY
        basis = "标准CT"
    # 设备能力修正:当前工序的剩余用「设备校准值」计量(无修正则保持现状)。
    #   · 实测分支:当前工序此前完全未计入剩余(进度按已耗插值)→ 补上;
    #   · 标准分支:当前工序整段 CT 曾全额计入 → 用校准剩余替换它(替换不到时不动)。
    if cur_dev_rem_h is not None:
        try:
            dev_rem = max(0.0, float(cur_dev_rem_h))
        except (TypeError, ValueError):
            dev_rem = None
        if dev_rem is not None:
            if used_learned:
                remain_days = (remain_h_real + dev_rem) / HOURS_PER_DAY
                basis = "实测(含等待)+设备UPH"
            else:
                cur_ct = None
                if hit_spec:
                    _sc = ct.get("spec_ct", {}) or {}
                    cur_ct = _sc.get(hit_spec) or _sc.get(_strip_suffix(hit_spec))
                if cur_ct and float(cur_ct) > 0:
                    remain_ct = max(0.0, eff_total - cum - float(cur_ct) + dev_rem)
                    remain_days = remain_ct / HOURS_PER_DAY
                    basis = "标准CT+设备UPH"
                # cur_ct 取不到(工序名对不上)→ 无法精确替换,不改动

    risk_level, risk_note, slack, eta = "", "", None, None
    t = today or datetime.date.today()
    # 作业起算点:正常=今天;若设了「恢复作业日」(挂起协调)且晚于今天,从恢复日起算。
    # 等待期间(今天→恢复日)不推进,ETA、剩余与风险据此整体顺延。
    # resume_date 支持 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'。
    base, wait_days = t, 0
    if resume_date:
        try:
            rd_str = str(resume_date).strip()
            rsd = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    rsd = datetime.datetime.strptime(rd_str, fmt).date()
                    break
                except ValueError:
                    continue
            if rsd and rsd > t:
                base, wait_days = rsd, (rsd - t).days
        except (ValueError, TypeError):
            pass
    # ETA:不依赖计划交期,正向预测预计完工日(从作业起算点 base 算起)。剩余天数取整,得整天 ETA。
    eta_date = None
    try:
        eta_date = base + datetime.timedelta(days=round(remain_days))
        eta = eta_date.isoformat()
    except (ValueError, OverflowError):
        eta = None

    if plan_end:
        try:
            due = datetime.date.fromisoformat(plan_end)
            due_days = (due - t).days
            # Buffer(富余)= 计划交付日 − 预计完工日(ETA)的【整天数】,与看板显示的 Buffer 完全一致。
            # 关键:必须用 ETA 整天算,而非"距交期 − 剩余小数天"——否则 ETA 正好=交期(看着没buffer、
            # 没滞后)时,会因剩余天数的小数(如 -0.4)被误判成滞后/高风险。
            if eta_date is not None:
                slack = (due - eta_date).days
            else:
                slack = due_days - wait_days - remain_days
            # 按 buffer 天数分级(保守口径):<0 高 / 0 低(没buffer) / 1 正常 / ≥2 超前
            risk_level = _grade_abs(slack)
            if slack >= 0:
                slack_text = f"超前 {slack:.0f} 天"
            else:
                slack_text = f"滞后 {abs(slack):.0f} 天"
            wait_text = f" / 挂起待恢复 {wait_days} 天" if wait_days > 0 else ""
            risk_note = (f"剩余需 {remain_days:.1f} 天({basis}){wait_text} / 距交期 {due_days} 天 "
                         f"/ {slack_text} / 预计完工 {eta}")
        except (ValueError, TypeError):
            pass

    return {"progress": progress, "risk_level": risk_level, "risk_note": risk_note,
            "remain_days": round(remain_days, 1),
            "slack_days": round(slack, 1) if slack is not None else None,
            "matched": True, "match_how": how, "eta": eta, "predict_basis": basis}
