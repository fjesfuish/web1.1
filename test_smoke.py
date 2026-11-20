#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTO 生产进度系统 — 冒烟 / 回归测试(纯标准库,无第三方依赖)

用法:
    python3 test_smoke.py              # 进程内自检:编译 + 渲染 + 空库 + 边界 + 数据层
    python3 test_smoke.py --http       # 额外做 HTTP 冒烟(默认 http://localhost:8000,需服务已启动)
    python3 test_smoke.py --http http://主机:端口

退出码: 0 = 全部通过, 1 = 有失败。可在每次改完代码后一键自检,或接入 CI。

说明:
  · 进程内测试直接 import db/views/mes,用「当前两个业务库」的真实数据驱动渲染,只读不写,
    不会污染任何数据;会临时切换线程类别(molding/potting),与运行中的服务进程互不影响。
  · HTTP 冒烟只校验状态码:公开页应 200、鉴权页应 303(重定向登录),出现 500 即判失败。
"""
import os
import sys
import glob
import py_compile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

PASS = 0
FAIL = []


def _ok(name):
    global PASS
    PASS += 1
    print("  \033[32m✓\033[0m " + name)


def _no(name, err):
    FAIL.append((name, err))
    print("  \033[31m✗\033[0m " + name + " → " + err[:110])


def check(name, fn):
    try:
        fn()
        _ok(name)
    except Exception as e:  # noqa: BLE001  (测试需捕获一切失败)
        _no(name, repr(e))


def _assert(cond, msg="断言失败"):
    if not cond:
        raise AssertionError(msg)
    return True


def html_ok(s):
    _assert(isinstance(s, str) and len(s) > 200 and "<" in s, "返回的不是有效 HTML")


def section(t):
    print("\n=== " + t + " ===")


# ----------------------------------------------------------------------
def test_compile():
    section("1. 语法编译(全部 .py)")
    for f in sorted(glob.glob("*.py")):
        if os.path.abspath(f) == os.path.abspath(__file__):
            continue
        check("compile " + f, lambda f=f: py_compile.compile(f, doraise=True))


def test_render_real():
    import db
    import views
    section("2. 核心页面渲染(molding 真实数据)")
    db.set_category("molding")
    check("render_gantt", lambda: html_ok(
        views.render_gantt(db.gantt_data(), db.dashboard_stats(), True)))
    check("render_stats", lambda: html_ok(
        views.render_stats(db.stats_data())))
    check("render_orders", lambda: html_ok(
        views.render_orders(db.all_orders({}, exclude_done=True),
                            db.filter_options(), {}, True)))
    check("render_done", lambda: html_ok(views.render_done(db.done_orders())))
    check("render_help", lambda: html_ok(views.render_help()))
    check("render_login", lambda: html_ok(
        views.render_login(stats=db.login_pulse_stats())))


def test_render_empty():
    import db
    import views
    section("3. 空库优雅降级(potting / 可能 0 工单)")
    db.set_category("potting")
    check("gantt 空库", lambda: html_ok(
        views.render_gantt(db.gantt_data(), db.dashboard_stats(), True)))
    check("stats 空库", lambda: html_ok(views.render_stats(db.stats_data())))
    check("orders 空库", lambda: html_ok(
        views.render_orders(db.all_orders({}, exclude_done=True),
                            db.filter_options(), {}, True)))


def test_boundary():
    import db
    import mes
    section("4. 边界 / 异常输入(不应崩溃)")
    db.set_category("molding")
    check("不存在的工单 lookup_order", lambda: mes.lookup_order("WO_NOT_EXIST_999"))
    check("不存在型号 get_ct_table", lambda: db.get_ct_table("NOPE_MODEL_X"))
    check("非法类别退回 molding",
          lambda: (db.set_category("garbage"), _assert(db.get_category() == "molding"))[1])
    check("gantt_data 不抛错", lambda: db.gantt_data())
    check("wip_profile 不抛错", lambda: db.wip_profile())


def test_datalayer():
    import db
    section("5. 数据层(返回类型正确)")
    db.set_category("molding")
    check("dashboard_stats", lambda: _assert(isinstance(db.dashboard_stats(), dict)))
    check("stats_data", lambda: _assert(isinstance(db.stats_data(), dict)))
    check("wip_profile 结构", lambda: _assert(
        set(db.wip_profile()) >= {"wip_lots", "wip_ct", "wip_models"}))
    check("owner_lot_workload", lambda: _assert(isinstance(db.owner_lot_workload(), dict)))
    check("all_orders", lambda: _assert(isinstance(db.all_orders({}), list)))


def test_equip_ct_combo():
    """设备能力×CT/ETA 组合(内存 fixture,不依赖业务库)。
    覆盖:明细按料号聚合、finish_info 回退链、ct.compute 设备修正、ct_learn 碎片净化。
    与 2026-08-28 交付的 ETA/校准优化同批。"""
    section("6. 设备能力×CT/ETA 组合(内存 fixture)")
    import equip
    import ct as ctmod
    import ct_learn

    # 6a. finish_info:设备级 → 工序级(回退)→ 无样本
    idx = {("MB12", "S1", "E01"): {"uph": 150.0, "n": 2},
           ("MB12", "S1", ""): {"uph": 100.0, "n": 5}}
    fin = equip.finish_info(idx, "MB12", "S1", "E01", 1500, 2.0)
    check("finish_info 设备级", lambda: _assert(
        fin and fin["rem_h"] == 8.0 and fin["basis"] == "设备"))
    check("finish_info 回退工序级", lambda: _assert(
        equip.finish_info(idx, "MB12", "S1", "E99", 1000, 0)["basis"] == "工序"))
    check("finish_info 无样本→None", lambda: _assert(
        equip.finish_info(idx, "MB12", "S9", "E01", 1000, 0) is None))

    # 6b. _agg_by_eqp 明细按料号合并 + 平均 UPH(内存行模拟,字典列访问与 sqlite Row 同接口)
    def _row(vals):
        return dict(zip(["eqp", "product", "qty", "run_h", "finished_at"], vals))

    rows = [_row(["E01", "A", 1000, 10.0, 1]),
            _row(["E01", "A", 1000, 5.0, 2]),
            _row(["E01", "B", 40, 1.0, 3])]
    det = equip._agg_by_eqp(rows)[0]["detail"]
    check("明细按料号合并", lambda: _assert(
        len(det) == 2
        and next(d for d in det if d["product"] == "A")["uph"] == 150.0
        and next(d for d in det if d["product"] == "A")["samples"] == 2
        and next(d for d in det if d["product"] == "B")["uph"] == 40.0))

    # 6c. ct.compute 设备修正:标准CT 分支替换当前工序整段;无修正=现状
    ctab = ctmod.parse_ct_table(["SEQNO", "SPECNAME", "OTDO_CT"],
                                [["1", "S1", "10"], ["2", "S2", "20"]])
    r0 = ctmod.compute("S1", "", ctab, cur_run_h=5.0)
    r1 = ctmod.compute("S1", "", ctab, cur_run_h=5.0, cur_dev_rem_h=5.0)
    check("compute 无修正=现状", lambda: _assert(
        abs(r0["remain_days"] - round(30.0 / 24, 1)) < 1e-9))
    check("compute 标准CT+设备UPH", lambda: _assert(
        abs(r1["remain_days"] - round(25.0 / 24, 1)) < 1e-9
        and r1["predict_basis"] == "标准CT+设备UPH"))
    learned = {"S1": {"run": 8.0, "wait": 2.0}, "S2": {"run": 15.0, "wait": 3.0}}
    r2 = ctmod.compute("S1", "", ctab, cur_run_h=5.0, learned=learned)
    r3 = ctmod.compute("S1", "", ctab, cur_run_h=5.0, learned=learned, cur_dev_rem_h=4.0)
    check("compute 实测+设备UPH", lambda: _assert(
        abs(r3["remain_days"] - round(32.0 / 24, 1)) < 1e-9
        and r3["predict_basis"] == "实测(含等待)+设备UPH"
        and abs(r2["remain_days"] - round(28.0 / 24, 1)) < 1e-9))

    # 6d. ct_learn 碎片净化:<30%×粗中位 的近0 样本剔除;样本<20 不动
    mixed = [0.02] * 10 + [0.5] * 20
    clean = ct_learn._purge_frags(mixed)
    check("碎片净化剔除近0", lambda: _assert(len(clean) == 20 and min(clean) >= 0.05))
    small = [0.02] * 5
    check("碎片净化样本少的保留", lambda: _assert(ct_learn._purge_frags(small) == small))

    # 6e. 可信度在集中样本上应高
    conf = ct_learn._confidence([0.5] * 20 + [0.52] * 10)
    check("可信度集中度高", lambda: _assert(conf[0] > 70, str(conf)))

    # 6f. 同炉识别:同设备 RUNTIME 相近的多个 lot 共享同一完成时刻;单 lot 走回退链
    reps = [{"rid": 0, "eqp": "OV", "st": "运行中", "qty": 48, "run_h": 4.17},
            {"rid": 1, "eqp": "OV", "st": "运行中", "qty": 48, "run_h": 4.18},
            {"rid": 2, "eqp": "S", "st": "运行中", "qty": 48, "run_h": 1.74}]
    em = equip.eta_group(reps, idx, "MB12", "S1", run_med=5.0, now=100.0)
    check("同炉共享完成时刻", lambda: _assert(
        em[0]["at"] == em[1]["at"] and em[0]["basis"] == "同炉"
        and abs(em[0]["rem_h"] - 0.82) < 1e-6, str({k: em.get(k) for k in (0, 1)})))
    em2 = equip.eta_group(reps, idx, "MB12", "S1", run_med=None, now=100.0)
    check("同炉无炉时不估", lambda: _assert(em2[0] is None and em2[1] is None))


def test_wip_analysis():
    section("8. WIP 超期分析")
    import mes
    import views
    import db
    import csv as _csv
    import tempfile
    import datetime

    today = datetime.date.today()

    def _d(days):
        return (today - datetime.timedelta(days=days)).strftime("%Y/%m/%d 09:00:00")

    header = ["PACKAGESIZE", "WORKFLOWNAME", "STARTDATE", "MFGORDERNAME",
              "OWNERNAME", "PRODUCTNAME", "SPECSEQUENCE", "LOTID", "PRIORITY",
              "QTY", "STATUS", "SPECNAME", "NEXT_SPECNAME", "SPECDESC",
              "RUNTIME", "WAITTIME", "HOLDTIME"]
    data = [
        # 超期 100 天,塑封
        ["MB12", "MB_SL03", _d(100), "WO_A", "u1", "P_A", "1", "LOT_A", "Normal",
         "10", "WaitingTrackIn", "S1", "S2", "工序一", "", "1", ""],
        # 超期 70 天,灌封
        ["HD8 SIC", "HD_GO03", _d(70), "WO_B", "u2", "P_B", "2", "LOT_B", "火箭批",
         "5", "HOLD", "S2", "S3", "工序二", "1", "", "2"],
        # 未超期
        ["MB12", "MB_SL03", _d(10), "WO_C", "u1", "P_C", "1", "LOT_C", "Normal",
         "7", "RUNNING", "S1", "S2", "工序一", "1", "", ""],
        # 已入库不算在制
        ["MB12", "MB_SL03", _d(200), "WO_D", "u1", "P_D", "1", "LOT_D", "Normal",
         "3", "InWareHouse", "S3", "", "入库", "", "", ""],
    ]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "mes_test.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.writer(f)
            w.writerow(header)
            w.writerows(data)
        smap = {"MB12": "molding", "HD8 SIC": "potting"}
        out = mes.wip_overdue_analysis(60, [15, 30], smap,
                                       path=p, page=1, page_size=100)
        check("WIP 超期基本统计", lambda: _assert(
            out["ok"] and out["total"] == 2 and out["summary"]["orders"] == 2
            and out["summary"]["qty"] == 15, str(out["summary"])))
        check("WIP 已入库剔除", lambda: _assert(
            all(r["lot"] != "LOT_D" for r in out["rows"]), "InWareHouse 被计入"))
        check("WIP 图表总数", lambda: _assert(
            out["chart_totals"]["families"] >= 2
            and out["chart_totals"]["products"] >= 2,
            str(out.get("chart_totals"))))
        check("WIP 系列归属", lambda: _assert(
            out["summary"]["series"]["molding"]["lots"] == 1
            and out["summary"]["series"]["potting"]["lots"] == 1, "系列统计不符"))
        check("WIP 天档分档", lambda: _assert(
            out["buckets"][-1]["lots"] == 1 and out["buckets"][0]["lots"] == 1,
            str(out["buckets"])))
        check("WIP 明细按超期降序", lambda: _assert(
            out["rows"][0]["days"] >= out["rows"][-1]["days"], "排序不符"))
        f1 = mes.wip_overdue_analysis(60, [15, 30], smap, product="MB12", path=p)
        check("WIP 型号筛选", lambda: _assert(
            f1["total"] == 1 and f1["rows"][0]["lot"] == "LOT_A", "型号筛选不符"))
        f2 = mes.wip_overdue_analysis(60, [15, 30], smap, keyword="LOT_B", path=p)
        check("WIP 工单/LOT 搜索", lambda: _assert(
            f2["total"] == 1 and f2["rows"][0]["wo"] == "WO_B", "搜索不符"))
        # 联动:图表各自忽略本维筛选,其余条件照常生效
        sx = mes.wip_overdue_analysis(60, [15, 30], smap, series="molding", path=p)
        check("WIP 系列硬隔离", lambda: _assert(
            sx["total"] == 1 and sx["summary"]["series"]["molding"]["lots"] == 1
            and sx["summary"]["series"]["potting"]["lots"] == 0
            and sx["charts"]["series"]["potting"]["lots"] == 0, "系列隔离不符"))
        bx = mes.wip_overdue_analysis(60, [15, 30], smap, bucket=2, path=p)
        check("WIP 档位筛选 + 档位图保留全档", lambda: _assert(
            bx["total"] == 1 and bx["rows"][0]["lot"] == "LOT_A"
            and bx["charts"]["buckets"][0]["lots"] == 1, "档位联动不符"))
        px = mes.wip_overdue_analysis(60, [15, 30], smap, spec="S2", path=p)
        check("WIP 工序筛选", lambda: _assert(
            px["total"] == 1 and px["rows"][0]["lot"] == "LOT_B"
            and px["charts"]["series"]["potting"]["lots"] == 1, "工序筛选不符"))
        # Owner 解析:HOLD→ENG,其余→Inline;Assy/MPC 分别按 Product/PACKAGESIZE
        raw_owner = {
            "assy": {"MB12": {"owner": "AssyA", "package": "MB12"},
                     "HD8 SIC": {"owner": "AssyB", "package": "HD8 SIC"}},
            "mpc": {"MB12": {"owner": "MpcA", "technology": "MB1"},
                    "HD8 SIC": {"owner": "MpcB", "technology": "HD1 SIC"}},
            "inline": {"MB1|S1": {"eng": "EngA", "inline": "InlineA"},
                       "HD1 SIC|S2": {"eng": "EngB", "inline": "InlineB"}},
        }
        lk = db.build_owner_lookup(raw_owner)
        ow = mes.wip_overdue_analysis(60, [15, 30], smap, path=p,
                                      owner_map=lk, owner_ver="v1")
        by_lot = {r["lot"]: r for r in ow["rows"]}
        check("WIP Owner 非HOLD取Inline", lambda: _assert(
            by_lot["LOT_A"]["owner"] == "InlineA"
            and by_lot["LOT_A"]["owner_kind"] == "Inline", str(by_lot["LOT_A"])))
        check("WIP Owner HOLD取ENG", lambda: _assert(
            by_lot["LOT_B"]["owner"] == "EngB"
            and by_lot["LOT_B"]["owner_kind"] == "ENG", str(by_lot["LOT_B"])))
        check("WIP Assy/MPC Owner", lambda: _assert(
            by_lot["LOT_A"]["assy_owner"] == "AssyA"
            and by_lot["LOT_A"]["mpc_owner"] == "MpcA"
            and by_lot["LOT_B"]["assy_owner"] == "AssyB"
            and by_lot["LOT_B"]["mpc_owner"] == "MpcB", "Assy/MPC 不符"))
        # 顶层型号 = MPC TECHNOLOGY;系列 = 硬隔离
        fx = mes.wip_overdue_analysis(60, [15, 30], smap, path=p,
                                      owner_map=lk, owner_ver="v1",
                                      family="MB1")
        check("WIP 顶层型号筛选", lambda: _assert(
            fx["total"] == 1 and fx["rows"][0]["lot"] == "LOT_A"
            and fx["rows"][0]["family"] == "MB1"
            and fx["rows"][0]["sub_model"] == "MB12", str(fx["rows"])))
        check("WIP 顶层图忽略本维", lambda: _assert(
            any(x["family"] == "HD1 SIC" for x in fx["charts"]["families"]),
            str(fx["charts"]["families"])))
        strict = mes.wip_overdue_analysis(60, [15, 30], smap, path=p,
                                          owner_map=lk, owner_ver="v1",
                                          series="molding")
        check("WIP 系列选项硬隔离", lambda: _assert(
            [x["product"] for x in strict["products"]] == ["MB12"]
            and [x["key"] for x in strict["families"]] == ["MB1"],
            f'{strict["products"]} {strict["families"]}'))
        ao = mes.wip_overdue_analysis(60, [15, 30], smap, path=p,
                                      owner_map=lk, owner_ver="v1",
                                      aowner="AssyB")
        check("WIP Assy Owner 筛选", lambda: _assert(
            ao["total"] == 1 and ao["rows"][0]["lot"] == "LOT_B", str(ao["total"])))
        io = mes.wip_overdue_analysis(60, [15, 30], smap, path=p,
                                      owner_map=lk, owner_ver="v1",
                                      iowner="EngB")
        check("WIP Inline/ENG Owner 筛选", lambda: _assert(
            io["total"] == 1 and io["rows"][0]["lot"] == "LOT_B"
            and io["rows"][0]["owner_kind"] == "ENG", str(io["rows"])))
        hid = mes.wip_overdue_analysis(60, [15, 30], smap, path=p,
                                       owner_map=lk, owner_ver="v1",
                                       hidden=["MB12"])
        check("WIP 隐藏型号排除", lambda: _assert(
            hid["total"] == 1 and hid["rows"][0]["lot"] == "LOT_B"
            and all(x["product"] != "MB12" for x in hid["products"])
            and hid["chart_totals"]["families"] == 1,
            f'{hid["total"]} {hid["products"]} {hid["chart_totals"]}'))
        _h3, rows3 = mes.wip_overdue_export(60, [15, 30], smap, path=p,
                                            hidden=["MB12"])
        check("WIP 隐藏型号导出排除", lambda: _assert(
            len(rows3) == 1 and rows3[0][0] == "WO_B", str(rows3)))
        heads, rows = mes.wip_overdue_export(60, [15, 30], smap, path=p)
        check("WIP 导出数据", lambda: _assert(
            len(rows) == 2 and len(heads) == 19 and rows[0][0] == "WO_A"
            and len(rows[0]) == 19, "导出不符"))
        _h2, rows2 = mes.wip_overdue_export(60, [15, 30], smap, bucket=2, path=p)
        check("WIP 导出带档位筛选", lambda: _assert(
            len(rows2) == 1 and rows2[0][0] == "WO_A", "导出档位筛选不符"))
    missing = mes.wip_overdue_analysis(60, [15], {}, path="/no/such/mes.csv")
    check("WIP 缺 CSV 不抛错", lambda: _assert(
        missing["ok"] and missing["total"] == 0 and not missing["csv_exists"],
        str(missing)))
    check("render_wip_analysis", lambda: html_ok(views.render_wip_analysis(False)))
    check("render_wip_analysis 管理员入口",
          lambda: _assert("CAN_EDIT = true" in views.render_wip_analysis(True)))
    check("render_wip_analysis 使用指南入口", lambda: _assert(
        'href="/wip_guide"' in views.render_wip_analysis(False),
        "缺少使用指南入口"))
    _gp = os.path.join(HERE, "wip_guide.html")
    check("wip_guide.html 存在", lambda: _assert(
        os.path.exists(_gp) and
        "WIP超期分析使用指南" in open(_gp, encoding="utf-8").read(),
        "使用指南文件缺失或标题不符"))


def test_wip_owner_import():
    section("9. Owner 表导入")
    import importer
    import db
    import mes
    import tempfile

    csv_ass = (
        "Product,TECHNOLOGY,PACKAGESIZE,Owner\n"
        "TEST00001AAA,HD1 SIC,HD1 SIC,张三\n"
        "TEST00002BBB,Housing,62mm,李四\n")
    csv_mpc = (
        "TECHNOLOGY,PACKAGESIZE,MPC Owner,封装类型\n"
        "Housing,62mm,李四,灌封\n"
        "HD1 SIC,HD1 SIC,王五,灌封\n"
        "HD,HD1,赵六,封塑\n")
    csv_inl = (
        "TECHNOLOGY,SPECNAME,ENG Owner,Inline Owner\n"
        "HD1 SIC,HD_GI01,张三,李四\n"
        "MB1,MB_AGBWS01,孙七,周八\n")
    parsed = {"types": {}, "warnings": []}
    for name, text, kind in (("assy.csv", csv_ass, "assy"),
                             ("mpc.csv", csv_mpc, "mpc"),
                             ("inline.csv", csv_inl, "inline")):
        p = importer.parse_owner_workbook(name, text.encode("utf-8"))
        check("Owner 识别 " + kind, lambda p=p, kind=kind: _assert(
            kind in p["types"] and p["types"][kind]["row_count"] > 0,
            str(p.get("sheets"))))
        parsed["types"].update(p["types"])
        parsed["warnings"].extend(p["warnings"])

    # 冲突检测:同一 Inline 键出现不同负责人
    dup = ("TECHNOLOGY,SPECNAME,ENG Owner,Inline Owner\n"
           "HD1 SIC,HD_GI01,张三,李四\n"
           "HD1 SIC,HD_GI01,王五,赵六\n")
    pd = importer.parse_owner_workbook("dup.csv", dup.encode("utf-8"))
    check("Owner 冲突计数", lambda: _assert(
        pd["types"]["inline"]["conflicts"] == 1, str(pd["types"]["inline"])))

    old_path = db.OWNER_MAP_PATH
    with tempfile.TemporaryDirectory() as td:
        db.OWNER_MAP_PATH = os.path.join(td, "owners.json")
        try:
            summary = db.owner_map_import(parsed, "tester", "owners.xlsx")
            check("Owner 保存", lambda: _assert(
                summary["imported"] == {"assy": 2, "mpc": 3, "inline": 2},
                str(summary)))
            lk = db.owner_map_lookup()
            check("Owner 查找结构", lambda: _assert(
                lk["assy"]["HD1 SIC"]["owner"] == "张三"
                and lk["assy"]["62MM"]["owner"] == "李四"
                and lk["mpc"]["HD1 SIC"]["owner"] == "王五"
                and lk["tech_by_pkg"]["HD1"] == "HD"
                and lk["inline"]["HD1 SIC|HD_GI01"]["inline"] == "李四",
                "查找结构不符"))
            legacy = db.build_owner_lookup({
                "assy": {"PROD_LEGACY": {"owner": "Legacy",
                                          "package": "PKG_LEGACY"}}})
            check("Owner 旧数据按细分项归一", lambda: _assert(
                legacy["assy"]["PKG_LEGACY"]["owner"] == "Legacy",
                str(legacy["assy"])))
            _a, _m, act1, kind1 = mes._resolve_wip_owners(
                lk, "TEST00001AAA", "HD1 SIC", "HD_GI01", "RUNNING")
            _a, _m, act2, kind2 = mes._resolve_wip_owners(
                lk, "TEST00001AAA", "HD1 SIC", "HD_GI01", "HOLD")
            check("Owner HOLD 判定", lambda: _assert(
                act1 == "李四" and kind1 == "Inline"
                and act2 == "张三" and kind2 == "ENG", f"{act1}/{kind1} {act2}/{kind2}"))
            # 所有 Owner 以细分项为锚点:PKG1 能查到,PKG2 不在 MPC 表就留空
            lk2 = db.build_owner_lookup({
                "mpc": {"PKG1": {"technology": "T1"}},
                "inline": {"T1|SPEC1": {"eng": "E", "inline": "I",
                                        "technology": "T1", "spec": "SPEC1"}}})
            _a, _m, act3, kind3 = mes._resolve_wip_owners(
                lk2, "", "PKG1", "SPEC1", "RUNNING")
            _a, _m, act4, kind4 = mes._resolve_wip_owners(
                lk2, "", "PKG2", "SPEC1", "RUNNING")
            check("Owner 细分项锚点", lambda: _assert(
                act3 == "I" and kind3 == "Inline" and act4 == "",
                f"{act3}/{kind3} {act4}/{kind4}"))
            o_rev = db.owner_map_info()["rev"]
            o_conflict = False
            try:
                db.owner_map_import(parsed, "tester", "owners.xlsx",
                                    expected_rev=o_rev - 1)
            except ValueError as e:
                o_conflict = "其他管理员" in str(e)
            check("Owner 导入版本冲突", lambda: _assert(
                o_conflict, "未拦截旧版本导入"))
            db.owner_map_import(parsed, "tester", "owners.xlsx",
                                expected_rev=o_rev)
            check("Owner 导入版本匹配", lambda: _assert(
                db.owner_map_info()["rev"] == o_rev + 1, "版本未递增"))
            # MPC 封装类型 → 系列归属
            mapping, _meta = db.series_map_load()
            check("Owner 封装类型补系列", lambda: _assert(
                mapping.get("HD1 SIC") == "potting"
                and mapping.get("62MM") == "potting", str(mapping)))
            # 封塑(错别字)不误判为塑封
            check("Owner 封装类型严格识别", lambda: _assert(
                mapping.get("HD1") != "molding", str(mapping.get("HD1"))))
            # 手工「未分类」不应覆盖 Owner 封装类型;手工塑封/灌封优先
            import json
            old_series = db.SERIES_MAP_PATH
            db.SERIES_MAP_PATH = os.path.join(td, "series.json")
            try:
                with open(db.SERIES_MAP_PATH, "w", encoding="utf-8") as f:
                    json.dump({"map": {"HD1 SIC": "", "62MM": "molding"}}, f)
                mapping2, _ = db.series_map_load()
                check("Owner 封装类型不被手工未分类覆盖", lambda: _assert(
                    mapping2.get("HD1 SIC") == "potting", str(mapping2.get("HD1 SIC"))))
                check("手工塑封优先于 Owner", lambda: _assert(
                    mapping2.get("62MM") == "molding", str(mapping2.get("62MM"))))
                db.series_map_save({"MB12": "molding"}, "tester",
                                   hidden=["MB12", "HD8 SIC"])
                check("隐藏型号保存", lambda: _assert(
                    db.series_hidden_products() == {"MB12", "HD8 SIC"},
                    str(db.series_hidden_products())))
                _m3, meta3 = db.series_map_load()
                check("隐藏数量元信息", lambda: _assert(
                    meta3.get("hidden_count") == 2, str(meta3)))
                rev1 = db.series_map_rev()
                conflict = False
                try:
                    db.series_map_save({"MB12": "molding"}, "tester",
                                       expected_rev=rev1 - 1)
                except ValueError as e:
                    conflict = "其他管理员" in str(e)
                check("归属保存版本冲突", lambda: _assert(
                    conflict, "未拦截旧版本保存"))
                db.series_map_save({"MB12": "molding"}, "tester",
                                   expected_rev=rev1)
                check("归属保存版本匹配", lambda: _assert(
                    db.series_map_rev() == rev1 + 1, "版本未递增"))
            finally:
                db.SERIES_MAP_PATH = old_series
        finally:
            db.OWNER_MAP_PATH = old_path
            db._wip_owner_cache["ver"] = object()
            db._wip_owner_cache["data"] = None


def test_wip_concurrency():
    section("10. 并发隔离 / 排队")
    import mes
    import app
    import threading
    import tempfile
    import datetime
    import csv as _csv

    today = datetime.date.today()

    def _d(days):
        return (today - datetime.timedelta(days=days)).strftime("%Y/%m/%d 09:00:00")

    header = ["PACKAGESIZE", "WORKFLOWNAME", "STARTDATE", "MFGORDERNAME",
              "OWNERNAME", "PRODUCTNAME", "SPECSEQUENCE", "LOTID", "PRIORITY",
              "QTY", "STATUS", "SPECNAME", "NEXT_SPECNAME", "SPECDESC",
              "RUNTIME", "WAITTIME", "HOLDTIME"]
    data = [
        ["MB12", "MB_SL03", _d(100), "WO_A", "u1", "P_A", "1", "LOT_A",
         "Normal", "10", "RUNNING", "S1", "", "工序一", "", "", ""],
        ["HD8 SIC", "HD_GO03", _d(80), "WO_B", "u2", "P_B", "2", "LOT_B",
         "Normal", "5", "HOLD", "S2", "", "工序二", "", "", ""],
    ]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "concurrent.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.writer(f)
            w.writerow(header)
            w.writerows(data)
        smap = {"MB12": "molding", "HD8 SIC": "potting"}
        barrier = threading.Barrier(6)
        errors = []

        def worker(prod):
            try:
                barrier.wait(timeout=10)
                for _ in range(15):
                    d = mes.wip_overdue_analysis(
                        60, [15], smap, product=prod, path=p,
                        page=1, page_size=10)
                    if (d["filters"]["product"] != prod.upper()
                            or d["total"] != 1
                            or d["rows"][0]["lot"] not in
                            (("LOT_A",) if prod == "MB12" else ("LOT_B",))):
                        errors.append((prod, d["filters"], d["total"]))
            except Exception as e:  # noqa: BLE001
                errors.append((prod, repr(e)))

        threads = [threading.Thread(target=worker, args=(p_,))
                   for p_ in ("MB12", "HD8 SIC") * 3]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        check("并发请求不串筛选结果", lambda: _assert(
            not errors, str(errors[:3])))

    app._export_register("smoke-t1", "xlsx")
    app._export_register("smoke-t2", "xlsx")
    s2 = app._export_status("smoke-t2")
    check("导出排队名次", lambda: _assert(
        s2["state"] == "waiting" and s2["position"] == 2
        and s2["ahead"] == 1 and s2["eta_seconds"] >= 1, str(s2)))
    app._export_mark_running("smoke-t1")
    app._export_finish("smoke-t1", True, 2.0)
    s2b = app._export_status("smoke-t2")
    check("导出排队前移", lambda: _assert(
        s2b["state"] == "waiting" and s2b["position"] == 1
        and s2b["ahead"] == 0, str(s2b)))
    app._export_finish("smoke-t2", True, 2.0)
    check("导出完成状态", lambda: _assert(
        app._export_status("smoke-t2")["state"] == "done", "状态不是 done"))


def test_http(base):
    import urllib.request
    import urllib.error
    section("7. HTTP 冒烟(" + base + ")")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None  # 不自动跟随重定向,以便读到真实状态码

    opener = urllib.request.build_opener(_NoRedirect)

    def status(path):
        try:
            # WIP 分析首次请求要建 7 万行快照,慢机器可能 >10s;给足 30s 避免误报
            r = opener.open(base.rstrip("/") + "/" + path, timeout=30)
            return getattr(r, "status", r.getcode())
        except urllib.error.HTTPError as e:
            return e.code

    # 公开页应 200;鉴权页应 303(重定向登录);任意 500 判失败
    routes = [
        ("login", {200}), ("select", {200}), ("wip_analysis", {200}),
        ("wip_analysis/data", {200}), ("wip_analysis/map", {401}),
        ("wip_analysis/export_status?ticket=smoke", {200}),
        ("wip_guide", {200}),
        ("stats", {200, 303}), ("gantt", {200, 303}), ("orders", {200, 303}),
        ("exceptions", {200, 303}), ("done", {200, 303}), ("ct_ai", {200, 303}),
        ("admin", {200, 303}), ("help", {200, 303}),
        # 2026-08 起敏感接口收进登录墙:匿名一律 303(重定向登录);
        # refresh 例外(匿名可手动触发同步,业务要求)—— 2026-09-02 起改回公开
        ("refresh", {200}), ("export/json", {303}), ("export/csv", {303}),
        ("exceptions/export", {303}), ("ct_ai/export", {303}),
        ("admin/lookup_mes", {303}),
    ]
    for path, ok in routes:
        check("GET /" + path,
              lambda path=path, ok=ok: _assert(status(path) in ok, "状态码不在预期 " + str(ok)))


def main():
    print("NTO 生产进度系统 · 冒烟/回归测试")
    test_compile()
    try:
        test_render_real()
        test_render_empty()
        test_boundary()
        test_datalayer()
        test_equip_ct_combo()
        test_wip_analysis()
        test_wip_owner_import()
        test_wip_concurrency()
    except Exception as e:
        # 函数级未捕获异常也必须可见地计数失败(此前 print_exc 后静默,结果假绿)
        traceback.print_exc()
        FAIL.append(("(函数级异常)", repr(e)))
    if "--http" in sys.argv:
        base = "http://localhost:8000"
        for a in sys.argv:
            if a.startswith("http://") or a.startswith("https://"):
                base = a
        test_http(base)

    print("\n" + "=" * 42)
    print("结果: 通过 %d · 失败 %d" % (PASS, len(FAIL)))
    if FAIL:
        print("失败项:")
        for n, e in FAIL:
            print("  - %s: %s" % (n, e[:120]))
        sys.exit(1)
    print("\033[32m✅ 全部通过\033[0m")
    sys.exit(0)


if __name__ == "__main__":
    main()
