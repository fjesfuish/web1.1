# -*- coding: utf-8 -*-
"""数据库层:仅用标准库 sqlite3。
工单表 (work_orders) 与基础数据表 (base_data) 在首次运行时自动建表并填充模拟数据。
"""
import sqlite3
import mes
import os
import time as _time
import datetime
import json
import threading

# ===== 多项目(塑封/灌封)分库 =====
# 业务数据按「类别」分库:common/wip_molding.db、common/wip_potting.db;
# 账号(admin_users)与审计走共享库 common/auth.db,实现「一套账号通用」。
# 当前类别用线程局部变量传递:每个 HTTP 请求按 cookie 设定,后台刷新线程逐类别设定;
# 线程局部互不干扰,单进程内 _sync_lock 串行写,避免 SQLite 跨进程锁库问题。
CATEGORIES = ("molding", "potting")            # 塑封 / 灌封
CATEGORY_NAMES = {"molding": "塑封系列NTO", "potting": "灌封系列NTO"}
DATA_DIR = os.environ.get(
    "WIP_DATA_DIR", os.path.join(os.path.dirname(__file__), "common"))
AUTH_DB = os.path.join(DATA_DIR, "auth.db")

_ctx = threading.local()

# CT 表跨请求缓存:{(category, model): 解析后的表或 None}。
# CT 表很少变(仅后台上传/切换计算方式),缓存可省掉 gantt 等热点每请求的反复 json 解析。
# 写入点(save_ct_table / set_ct_method)调用 _invalidate_ct_cache() 清空即可。
_ct_table_cache = {}
_CT_MISS = object()   # 哨兵:区分「未缓存」与「缓存了 None(无此型号CT表)」


def _invalidate_ct_cache():
    _ct_table_cache.clear()


def set_category(cat):
    """设置「当前线程」的业务类别(molding/potting)。非法值退回 molding。"""
    _ctx.category = cat if cat in CATEGORIES else "molding"


def get_category():
    """取当前线程类别;未设定时默认 molding(兼容登录页等无类别上下文的调用)。"""
    return getattr(_ctx, "category", None) or "molding"


def wip_path(cat=None):
    return os.path.join(DATA_DIR, "wip_%s.db" % (cat or get_category()))


# 兼容旧引用:默认指向塑封库(仅用于 __main__ 打印等,不参与实际连接选择)
DB_PATH = wip_path("molding")

# MES 同步写锁:串行化「自动刷新线程」「手动同步」与「切换CT方式/上传CT/Owner」
# 触发的重算,避免并发写 ct_samples/*.json(实测样本)与 sqlite 互相覆盖丢失。
# 【按业务类别分桶】每个类别(塑封/灌封)一把锁,塑封刷新不再阻塞灌封,二者互不干扰。
# (各类别 wip_*.db 与 ct_samples_* 均按 category 隔离,本无需跨类别串行。)
# 用可重入锁(RLock):run_mes_sync 持锁后内部还会再调 sync_manual_orders,同类别需可重入。
_sync_locks = {}                       # category -> RLock
_sync_locks_guard = threading.Lock()   # 仅保护 _sync_locks 字典本身


def _sync_lock_for(cat=None):
    """取某业务类别(塑封/灌封)的写锁;每类别独立,互不干扰。"""
    cat = cat or get_category()
    with _sync_locks_guard:
        lk = _sync_locks.get(cat)
        if lk is None:
            lk = threading.RLock()
            _sync_locks[cat] = lk
        return lk


def get_conn():
    """连接「当前类别」的业务库。"""
    conn = sqlite3.connect(wip_path(), timeout=10)
    conn.row_factory = sqlite3.Row  # 让查询结果可按列名访问
    conn.execute("PRAGMA busy_timeout=10000")   # 并发等锁最多 10 秒(per-connection)
    return conn


def get_auth_conn():
    """连接共享账号库 common/auth.db(账号/审计,类别无关,一套账号通用)。"""
    conn = sqlite3.connect(AUTH_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_auth_db():
    """初始化共享账号库:admin_users(账号) + audit_log(审计)。
    首次运行若 auth.db 无账号、而旧塑封库里有账号,则把账号迁移过来(平滑升级)。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_auth_conn()
    try:
        conn.execute("PRAGMA journal_mode=WAL")   # 账号库开 WAL:登录/审计读写并发,减少跨系统阻塞
    except sqlite3.OperationalError:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT UNIQUE NOT NULL,
            salt       TEXT NOT NULL,
            pass_hash  TEXT NOT NULL,
            created_at TEXT,
            role       TEXT DEFAULT 'normal',
            must_change_pw INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT, username TEXT, action TEXT, detail TEXT
        )
    """)
    conn.commit()
    # 账号迁移:auth.db 还没有账号时,从旧塑封业务库 admin_users 搬过来
    empty = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0] == 0
    if empty:
        legacy = wip_path("molding")
        if os.path.exists(legacy):
            try:
                src = sqlite3.connect(legacy)
                src.row_factory = sqlite3.Row
                rows = src.execute(
                    "SELECT username,salt,pass_hash,created_at,role,must_change_pw "
                    "FROM admin_users").fetchall()
                src.close()
                for r in rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO admin_users"
                        "(username,salt,pass_hash,created_at,role,must_change_pw)"
                        " VALUES(?,?,?,?,?,?)",
                        (r["username"], r["salt"], r["pass_hash"],
                         r["created_at"], r["role"], r["must_change_pw"]))
                conn.commit()
            except Exception:
                pass
    conn.close()


def init_all():
    """初始化全部库:两个业务类别库 + 共享账号库。app 启动时调用。"""
    for _cat in CATEGORIES:
        set_category(_cat)
        init_db()
    init_auth_db()


def init_db():
    """建表。已存在则跳过。"""
    conn = get_conn()
    try:
        conn.execute("PRAGMA journal_mode=WAL")   # 持久设置,只需建库时设一次:读写不互斥
    except sqlite3.OperationalError:
        pass
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            wo_no        TEXT NOT NULL,          -- 工单号
            product      TEXT,                   -- 产品型号
            customer     TEXT,                   -- 客户
            demander     TEXT,                   -- 需求方(纯人工填)
            status       TEXT,                   -- 待开始/进行中/已完成/已延期
            priority     TEXT,                   -- 火箭批/1-0/1-1/Normal
            plan_start   TEXT,                   -- 计划开始日期 YYYY-MM-DD
            plan_end     TEXT,                   -- 计划交付日期
            owner        TEXT,                   -- 负责人
            cur_process  TEXT,                   -- 当前工序
            progress     INTEGER DEFAULT 0,      -- 进度 0-100
            risk_level   TEXT,                   -- 超前/正常/低风险/高风险
            risk_note    TEXT,                   -- 风险提示文字
            mes_status   TEXT,                   -- MES同步状态: 已同步 / 无MES数据 / (空=未同步)
            remark       TEXT,                   -- 备注(用户手填)
            head_pct     REAL,                   -- 首批(最快批)CT累计进度%
            tail_pct     REAL,                   -- 尾批(最慢批)CT累计进度%
            head_spec    TEXT,                   -- 首批(最快批)当前工序 SPECNAME
            tail_spec    TEXT,                   -- 尾批(最慢批)当前工序 SPECNAME
            lot_specs    TEXT                    -- JSON: 当前真有LOT(在制)的工序SPECNAME列表,供甘特图区分空工序
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS base_data (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            kind  TEXT,   -- product / owner / status / priority / process
            value TEXT
        )
    """)
    # MES 聚合工单表(来源于 csv,与手动工单分开存储)
    c.execute("""
        CREATE TABLE IF NOT EXISTS mes_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            wo_no       TEXT,
            prefix      TEXT,   -- 抓取用的前两字符前缀
            product     TEXT,
            priority    TEXT,
            owner       TEXT,
            lot_count   INTEGER,
            qty         INTEGER,
            cur_process TEXT,
            status      TEXT,
            processes   TEXT,   -- JSON: 各在制工序明细
            synced_at   TEXT,   -- 拉取时间
            match_mode  TEXT    -- exact / prefix
        )
    """)
    # 被忽略的残留 LOT(管理员勾选)。lot_id 全局唯一,作主键。
    # 甘特图据此把「整站 LOT 全被忽略」的工序刷成浅粉,并从 head/tail/进度 计算中剔除。
    c.execute("""
        CREATE TABLE IF NOT EXISTS ignored_lots (
            lot_id     TEXT PRIMARY KEY,
            wo_no      TEXT,   -- 所属工单(work_orders.wo_no)
            spec       TEXT,   -- 该 LOT 当前工序 SPECNAME
            ignored_at TEXT,
            ignored_by TEXT
        )
    """)
    # 异常记录:Lot 在工序中 Wait/HOLD 超阈值自动生成,记录进入/流出时间。
    # 责任Owner、备注由管理员手填。Lot 离开该状态后标记已解除并填流出时间(保留为历史)。
    c.execute("""
        CREATE TABLE IF NOT EXISTS exceptions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id     TEXT,
            wo_no      TEXT,
            spec       TEXT,      -- 工序 SPECNAME
            spec_name  TEXT,      -- 工序描述/中文名
            etype      TEXT,      -- WAIT / HOLD
            enter_at   TEXT,      -- 状态进入时间(估算: 检测时刻 − 已用时长)
            exit_at    TEXT,      -- 状态流出时间(检测到离开时填)
            peak_hours REAL,      -- 记录到的最长超时时长(h)
            status     TEXT,      -- 进行中 / 已解除
            owner      TEXT,      -- 责任Owner(管理员手填)
            remark     TEXT,      -- 备注(管理员)
            created_at TEXT,
            updated_at TEXT
        )
    """)
    # 分级异常阈值:(model, spec) 精确 > (model, '') 型号全局 > 系统默认(app_meta)。
    c.execute("""
        CREATE TABLE IF NOT EXISTS exc_thresholds (
            model   TEXT,
            spec    TEXT,      -- '' 表示该型号全局
            wait_h  REAL,
            hold_h  REAL,
            PRIMARY KEY (model, spec)
        )
    """)
    # 工单级异常阈值(优先级高于型号级):spec='' 为整单。
    c.execute("""
        CREATE TABLE IF NOT EXISTS exc_wo_thresholds (
            wo_no   TEXT,
            spec    TEXT,
            wait_h  REAL,
            hold_h  REAL,
            PRIMARY KEY (wo_no, spec)
        )
    """)
    # 加急验证Lot:与 NTO 工单平行的独立子系统,以 Lot 号为主键,作业到选定目标工序即完成。
    c.execute("""
        CREATE TABLE IF NOT EXISTS urgent_batches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_name  TEXT,          -- 批次名称(自定义,代替工单号)
            product     TEXT,          -- 产品(PRODUCTNAME)
            ct_model    TEXT,          -- 自动匹配的 CT 表型号(算进度/目标工序用)
            target_spec TEXT,          -- 作业到的目标工序 SPECNAME(到此完成)
            target_name TEXT,          -- 目标工序中文名
            plan_end    TEXT,          -- 计划交付日
            created_at  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS urgent_lots (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id  INTEGER,
            lot_id    TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_meta (
            k TEXT PRIMARY KEY,
            v TEXT
        )
    """)
    # 后台管理账号表(多账号,带角色:super=超级管理员 / normal=普通用户)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT UNIQUE NOT NULL,
            salt       TEXT NOT NULL,
            pass_hash  TEXT NOT NULL,
            created_at TEXT,
            role       TEXT DEFAULT 'normal',  -- super=超管 / normal=管理员
            must_change_pw INTEGER DEFAULT 0   -- 1=登录后强制改密(默认密码账号)
        )
    """)
    for _col, _ddl in (("role", "role TEXT DEFAULT 'normal'"),
                       ("must_change_pw", "must_change_pw INTEGER DEFAULT 0")):
        try:    # 兼容旧库:补列
            c.execute(f"ALTER TABLE admin_users ADD COLUMN {_ddl}")
        except Exception:
            pass
    # 操作审计日志(谁、何时、做了什么)
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT,
            username TEXT,
            action   TEXT,
            detail   TEXT
        )
    """)
    # CT 表:每个型号一份(上传的 CT 表解析后整存为 JSON)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ct_tables (
            model      TEXT PRIMARY KEY,   -- 型号(=上传文件名,如 MB12)
            total_ct   REAL,
            data       TEXT,               -- JSON: parse_ct_table 的结果
            uploaded_at TEXT,
            use_calibrated INTEGER DEFAULT 0  -- 0=按上传CT计算 1=按校准后CT计算
        )
    """)
    try:    # 兼容旧库:补 use_calibrated 列
        c.execute("ALTER TABLE ct_tables ADD COLUMN use_calibrated INTEGER DEFAULT 0")
    except Exception:
        pass
    # Owner 表:全局一份(TECHNOLOGY+SPECNAME -> ENG/Inline 负责人)
    c.execute("""
        CREATE TABLE IF NOT EXISTS owner_table (
            id          INTEGER PRIMARY KEY CHECK (id=1),
            data        TEXT,              -- JSON: {"tech|spec": {"eng":..,"inline":..}}
            row_count   INTEGER,
            uploaded_at TEXT
        )
    """)
    conn.commit()

    # 迁移:旧库可能没有 mes_status / remark 列,补上
    cols = [r[1] for r in c.execute("PRAGMA table_info(work_orders)").fetchall()]
    if "demander" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN demander TEXT")
        conn.commit()
    if "mes_status" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN mes_status TEXT")
        conn.commit()
    if "remark" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN remark TEXT")
        conn.commit()
    if "head_pct" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN head_pct REAL")
        conn.commit()
    if "tail_pct" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN tail_pct REAL")
        conn.commit()
    if "head_spec" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN head_spec TEXT")
        conn.commit()
    if "tail_spec" not in cols:
        c.execute("ALTER TABLE work_orders ADD COLUMN tail_spec TEXT")
        conn.commit()
    if "lot_specs" not in cols:   # 在制(有LOT)工序列表,甘特图据此把空工序刷浅
        c.execute("ALTER TABLE work_orders ADD COLUMN lot_specs TEXT")
        conn.commit()
    if "ignored_specs" not in cols:   # 整站LOT全被忽略的工序,甘特图刷浅粉
        c.execute("ALTER TABLE work_orders ADD COLUMN ignored_specs TEXT")
        conn.commit()
    # 加急批次 ct_model 列(旧版 urgent_batches 可能没有)
    ucols = [r[1] for r in c.execute("PRAGMA table_info(urgent_batches)").fetchall()]
    if ucols and "ct_model" not in ucols:
        c.execute("ALTER TABLE urgent_batches ADD COLUMN ct_model TEXT")
        conn.commit()
    if "eta" not in cols:        # 预计产出日(按实测CT正向预测,验证单到FVI为止)
        c.execute("ALTER TABLE work_orders ADD COLUMN eta TEXT")
        conn.commit()
    if "is_verify" not in cols:  # 是否厂内验证工单(MES 单号 05 开头,作业到 FVI 即完成)
        c.execute("ALTER TABLE work_orders ADD COLUMN is_verify INTEGER DEFAULT 0")
        conn.commit()
    if "created_at" not in cols:  # 工单创建日期(统计「新增趋势」用)
        c.execute("ALTER TABLE work_orders ADD COLUMN created_at TEXT")
        conn.commit()
    if "done_at" not in cols:     # 首次进度达100%的完成日期(完成趋势/准时完成率用)
        c.execute("ALTER TABLE work_orders ADD COLUMN done_at TEXT")
        conn.commit()
    if "final_process" not in cols:  # 完成工序(工单做到哪道工序即完工;空=做完整条CT)
        c.execute("ALTER TABLE work_orders ADD COLUMN final_process TEXT")
        conn.commit()
    if "cur_qty" not in cols:       # 当前在制颗数(MES 实时同步:各在制工序 QTY 合计)
        c.execute("ALTER TABLE work_orders ADD COLUMN cur_qty INTEGER")
        conn.commit()
    if "deliver_qty" not in cols:   # 交付数量(新建工单时手工填写的目标交付颗数)
        c.execute("ALTER TABLE work_orders ADD COLUMN deliver_qty INTEGER")
        conn.commit()
    if "manual_done" not in cols:   # 管理员人工标记完成(=1 即归入「已完成工单」页,不受进度<100影响)
        c.execute("ALTER TABLE work_orders ADD COLUMN manual_done INTEGER DEFAULT 0")
        conn.commit()
    # 人工「挂起/等待」字段:等料、设备占用等可协调异常。管理员填恢复作业日+原因(备注),
    # 甘特图据此画「等待·异常」间隙、未来工序从恢复日按CT顺延,ETA/风险据 resume_date 顺延重算。
    if "resume_date" not in cols:   # 恢复作业日 YYYY-MM-DD 或 YYYY-MM-DD HH:MM(空=未挂起)
        c.execute("ALTER TABLE work_orders ADD COLUMN resume_date TEXT")
        conn.commit()
    if "wait_reason" not in cols:   # 等待/挂起原因(管理员备注:缺料/设备/其他)
        c.execute("ALTER TABLE work_orders ADD COLUMN wait_reason TEXT")
        conn.commit()
    if "hold_spec" not in cols:     # 被挂起的工序 SPEC(做不了的那道工序;甘特据此定位间隙)
        c.execute("ALTER TABLE work_orders ADD COLUMN hold_spec TEXT")
        conn.commit()
    if "wait_by" not in cols:       # 挂起操作人(留痕)
        c.execute("ALTER TABLE work_orders ADD COLUMN wait_by TEXT")
        conn.commit()
    if "wait_at" not in cols:       # 挂起操作时间(留痕)
        c.execute("ALTER TABLE work_orders ADD COLUMN wait_at TEXT")
        conn.commit()
    # 多次/多工序挂起:一个工单可同时有多道工序挂起、也可先后挂起多次(历史留存)。
    # work_orders 的单组 resume_date/wait_reason/hold_spec 仅作「最晚恢复日」那条的缓存,
    # 供甘特图/ETA/需要关注沿用原逻辑;真正的多条记录存于本表。status: active / resolved。
    c.execute("""
        CREATE TABLE IF NOT EXISTS wo_holds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wo_no       TEXT,
            hold_spec   TEXT,
            resume_date TEXT,
            reason      TEXT,
            wait_by     TEXT,
            wait_at     TEXT,
            status      TEXT DEFAULT 'active',
            created_at  TEXT,
            resolved_at TEXT,
            resolved_by TEXT
        )""")
    # 设备能力统计:eqp_pending(跨快照跟踪在制 lot 的运行峰值) + eqp_samples(定稿 UPH 样本)。
    # 由 equip.observe_snapshot 每次 MES 同步时维护;样本 60 天滚动清理。
    c.execute("""
        CREATE TABLE IF NOT EXISTS eqp_pending (
            model    TEXT,   -- PACKAGESIZE 基础型号(MB12)
            product  TEXT,   -- PRODUCTNAME 具体料号
            spec     TEXT,   -- 工序 SPECNAME
            seq      TEXT,   -- 工序顺序号
            workflow TEXT,   -- 工作流 WORKFLOWNAME(工序顺序的锚点,同流程内 seq 唯一)
            eqp      TEXT,   -- 设备号(EQPID)
            lot      TEXT,   -- 批次(LOTID)
            run_h    REAL,   -- 该批次在本工序的运行时长峰值(小时)
            qty      INTEGER,-- 批次数量
            status   TEXT,   -- 最后观测状态(RUNNING/HOLD),HOLD 冻结时长不定稿
            PRIMARY KEY (model, spec, lot)
        )""")
    # 旧库升级:补 status 列(已有列则跳过)
    try:
        c.execute("ALTER TABLE eqp_pending ADD COLUMN status TEXT")
    except Exception:
        pass
    # 旧库升级:补工作流列(工序顺序锚点,已有列则跳过)
    try:
        c.execute("ALTER TABLE eqp_pending ADD COLUMN workflow TEXT")
    except Exception:
        pass
    # 旧库升级:补工序描述列 spec_desc(SPECDESC 中文/英文工序名)
    try:
        c.execute("ALTER TABLE eqp_samples ADD COLUMN spec_desc TEXT")
    except Exception:
        pass
    # 旧库升级:补工作流列(工序顺序锚点,已有列则跳过)
    try:
        c.execute("ALTER TABLE eqp_samples ADD COLUMN workflow TEXT")
    except Exception:
        pass
    c.execute("""
        CREATE TABLE IF NOT EXISTS eqp_samples (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            model       TEXT,   -- PACKAGESIZE 基础型号
            product     TEXT,   -- PRODUCTNAME 具体料号
            spec        TEXT,   -- 工序 SPECNAME
            spec_desc   TEXT,   -- 工序描述 SPECDESC(中文/英文工序名)
            seq         TEXT,   -- 工序顺序号
            workflow    TEXT,   -- 工作流 WORKFLOWNAME(工序顺序的锚点,同流程内 seq 唯一)
            eqp         TEXT,   -- 设备号
            lot         TEXT,   -- 批次
            qty         INTEGER,-- 批次数量
            run_h       REAL,   -- 定稿完整运行时长(小时)
            finished_at INTEGER -- 定稿时间(epoch 秒)
        )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_eqp_samples_q ON eqp_samples(model, spec, eqp)")
    # 工作流定义表:快照全状态行提取的 (型号, 工作流, 工序) -> 序号。
    # 工序列表合并同名工序时,位置优先取主流程定义里的序号(样本驱动可能缺失,
    # 如 MB_ACPACH101 在主流程 MB_SA02 的 24 号位只有 WaitingTrackIn 无样本)。
    c.execute("""
        CREATE TABLE IF NOT EXISTS eqp_wfdef (
            model    TEXT,   -- PACKAGESIZE 基础型号
            workflow TEXT,   -- 工作流 WORKFLOWNAME
            spec     TEXT,   -- 工序 SPECNAME
            seq      TEXT,   -- 该工序在该流程中的序号(出现最多者)
            PRIMARY KEY (model, workflow, spec)
        )""")
    # 【工序监控】Flow 流程表:上传的完整工序流程(文件名去扩展名=型号),工序骨架来源之一
    c.execute("""CREATE TABLE IF NOT EXISTS flow_tables (
        model       TEXT PRIMARY KEY,   -- 型号(文件名去扩展名)
        data        TEXT NOT NULL,      -- JSON {specs:[{seqno,spec,stepname}]}
        uploaded_at TEXT NOT NULL
    )""")
    # 开始挂起日期(YYYY-MM-DD 或 YYYY-MM-DD HH:MM):记录挂起从哪天/何时开始;旧表无此列时补加(幂等)。
    _hcols = {r[1] for r in c.execute("PRAGMA table_info(wo_holds)").fetchall()}
    if "start_date" not in _hcols:
        c.execute("ALTER TABLE wo_holds ADD COLUMN start_date TEXT")
    conn.commit()
    # 一次性迁移:旧的单组挂起 → wo_holds(仅当该单尚无任何挂起记录,幂等)
    _seeded = {r[0] for r in c.execute("SELECT DISTINCT wo_no FROM wo_holds")}
    _now0 = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for wr in c.execute(
            "SELECT wo_no, hold_spec, resume_date, wait_reason, wait_by, wait_at "
            "FROM work_orders WHERE COALESCE(resume_date,'')<>''").fetchall():
        if wr[0] in _seeded:
            continue
        c.execute(
            "INSERT INTO wo_holds(wo_no,hold_spec,resume_date,reason,wait_by,wait_at,status,created_at) "
            "VALUES(?,?,?,?,?,?, 'active', ?)",
            (wr[0], wr[1] or "", wr[2] or "", wr[3] or "", wr[4] or "", wr[5] or "", wr[5] or _now0))
    conn.commit()
    # 忽略 LOT 备注列(标注异常/不作业原因,WIP 分布图灰显时展示给生产)
    igcols = [r[1] for r in c.execute("PRAGMA table_info(ignored_lots)").fetchall()]
    if igcols and "note" not in igcols:
        c.execute("ALTER TABLE ignored_lots ADD COLUMN note TEXT")
        conn.commit()

    wocols = [r[1] for r in c.execute("PRAGMA table_info(work_orders)").fetchall()]
    if wocols and "eng_times" not in wocols:
        c.execute("ALTER TABLE work_orders ADD COLUMN eng_times TEXT")
        conn.commit()

    # 仅「塑封」空库时填充模拟数据;灌封等新类别保持空白,由后台录入(避免塞入塑封型号)
    if (get_category() == "molding"
            and c.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 0):
        _seed(conn)
    conn.close()


def _seed(conn):
    
    rows = [
        # wo_no, product, customer, status, priority, start, end, owner, process, progress, risk, note
        ("*50000010919", "MB6",          "洛宇", "进行中", "1-0",    "2026-05-29", "2026-06-06", "郭芸芸", "TC",          73, "超前",   "剩7天 / 应剥:浮温抗水测试"),
        ("*50000011018", "MB1",          "永顺", "进行中", "Normal", "2026-05-29", "2026-06-06", "谢昊伦", "X_Ray",       30, "高风险", "剩7天 / 应剥:整封后固化"),
        ("*50000011017", "MB1",          "永顺", "进行中", "Normal", "2026-05-29", "2026-06-06", "谢昊伦", "一次回流",     22, "高风险", "剩7天 / 应剥:整封后固化"),
        ("*50000010807", "MB1",          "安乡", "进行中", "Normal", "2026-05-29", "2026-06-08", "谢昊伦", "贴前印刷前烘烤", 0,  "高风险", "剩7天 / 应剥:表面贴线"),
        ("*50000010912", "MB12 / 4in1",  "洛宇", "进行中", "1-0",    "2026-05-22", "2026-06-10", "胡尚远", "coating",     24, "高风险", "剩10天 / 应剥:激光打号SN"),
        ("*50000010913", "MB12 / 4in1",  "洛宇", "进行中", "1-0",    "2026-05-22", "2026-06-10", "胡尚远", "一次回流焊装",  9,  "高风险", "剩10天 / 应剥:激光打号SN"),
        ("*50000010915", "MB12 / 4in1",  "洛宇", "进行中", "1-0",    "2026-05-23", "2026-06-12", "胡尚远", "一次回流焊装",  9,  "高风险", "剩1天 / 应剥:电感测试"),
        ("*50000010914", "MB12 / 4in1",  "洛宇", "进行中", "1-0",    "2026-05-23", "2026-06-12", "胡尚远", "热帖路绑后目检", 7,  "高风险", "剩1天 / 应剥:电感测试"),
    ]
    conn.executemany("""
        INSERT INTO work_orders
        (wo_no, product, customer, status, priority, plan_start, plan_end, owner, cur_process, progress, risk_level, risk_note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)

    # 仅录 MES 中真实存在的单个 PACKAGESIZE,保证都走精确匹配
    products = ["MB1", "MB12", "MB6", "MB4", "MB24", "MB3", "MB21", "MB23", "MB15",
                "MC1", "MC3", "PA2", "MF1", "MF2"]
    owners   = ["吴磊","谢昊伦","朱新刚","胡尚远","许子为","郭志阅","程亦舟","郭芸芸"]
    statuses = ["待开始","进行中","已完成","已延期"]
    prios    = ["火箭批","1-0","1-1","Normal"]
    for p in products:
        conn.execute("INSERT INTO base_data(kind,value) VALUES('product',?)", (p,))
    for o in owners:
        conn.execute("INSERT INTO base_data(kind,value) VALUES('owner',?)", (o,))
    for s in statuses:
        conn.execute("INSERT INTO base_data(kind,value) VALUES('status',?)", (s,))
    for pr in prios:
        conn.execute("INSERT INTO base_data(kind,value) VALUES('priority',?)", (pr,))
    conn.commit()


# ---------- 查询辅助 ----------
def all_orders(filters=None, exclude_done=False):
    conn = get_conn()
    sql = "SELECT * FROM work_orders WHERE 1=1"
    args = []
    if exclude_done:   # 已完成(进度≥100% 或管理员人工标记完成)工单归入「已完成」页,不在工单明细显示
        sql += " AND COALESCE(progress,0) < 100 AND COALESCE(manual_done,0) = 0"
    if filters:
        for k in ("status", "product", "owner", "priority", "risk_level", "demander"):
            v = filters.get(k)
            if v:
                sql += f" AND {k}=?"
                args.append(v)
        kw = filters.get("kw")
        if kw:
            sql += (" AND (wo_no LIKE ? OR product LIKE ? OR owner LIKE ?"
                    " OR cur_process LIKE ?)")
            args += [f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"]
    sql += " ORDER BY id"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows


def done_orders():
    """已完成工单(单独归集于「已完成」页,不在看板/工单明细显示)。
    口径:进度达 100% 自动完成,或管理员人工勾选「已完成」(manual_done=1)。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM work_orders "
        "WHERE COALESCE(progress,0) >= 100 OR COALESCE(manual_done,0) = 1 "
        "ORDER BY plan_end").fetchall()]
    conn.close()
    return rows


def set_order_done(order_id, done):
    """管理员人工把工单标记/取消「已完成」。
    标记:manual_done=1;若尚无完成日则记当天(供已完成页完工日与统计口径一致)。
    取消:manual_done=0;若进度未达100%(并非自动完成),一并清掉人工记下的完成日。"""
    conn = get_conn()
    today = datetime.date.today().isoformat()
    if done:
        conn.execute(
            "UPDATE work_orders SET manual_done=1, "
            "done_at=COALESCE(NULLIF(done_at,''), ?) WHERE id=?", (today, order_id))
    else:
        conn.execute(
            "UPDATE work_orders SET manual_done=0, "
            "done_at=CASE WHEN COALESCE(progress,0) >= 100 THEN done_at ELSE '' END "
            "WHERE id=?", (order_id,))
    conn.commit()
    conn.close()


def dashboard_stats():
    conn = get_conn()
    # 看板只统计未完成工单(进度<100 且未被人工标记完成);已完成单独归入「已完成」页
    UND = "COALESCE(progress,0) < 100 AND COALESCE(manual_done,0) = 0"
    total      = conn.execute(f"SELECT COUNT(*) FROM work_orders WHERE {UND}").fetchone()[0]
    today      = datetime.date.today().isoformat()
    new_today  = conn.execute(f"SELECT COUNT(*) FROM work_orders WHERE {UND} AND plan_start=?", (today,)).fetchone()[0]

    def risk_count(level):
        return conn.execute(
            f"SELECT COUNT(*) FROM work_orders WHERE {UND} AND risk_level=?", (level,)).fetchone()[0]
    ahead  = risk_count("超前")
    normal = risk_count("正常")
    low    = risk_count("低风险")
    high   = risk_count("高风险")

    all_list = [dict(r) for r in conn.execute(
        f"SELECT * FROM work_orders WHERE {UND} ORDER BY "
        "CASE risk_level WHEN '高风险' THEN 0 WHEN '低风险' THEN 1 "
        "WHEN '正常' THEN 2 WHEN '超前' THEN 3 ELSE 4 END, id").fetchall()]
    conn.close()
    return {
        "total": total, "new_today": new_today,
        "ahead": ahead, "normal": normal, "low": low, "high": high,
        "orders": all_list,
    }


def login_pulse_stats():
    """登录页神经网络背景所需的聚合数据(仅非敏感聚合数字,不含任何工单明细)。
    返回:total 总工单数, high 高风险数, running 在制(运行中)数, avg_progress 平均进度。
    """
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    high = conn.execute(
        "SELECT COUNT(*) FROM work_orders WHERE risk_level='高风险'").fetchone()[0]
    running = conn.execute(
        "SELECT COUNT(*) FROM work_orders WHERE status='运行中'").fetchone()[0]
    avg = conn.execute(
        "SELECT COALESCE(AVG(progress),0) FROM work_orders").fetchone()[0]
    conn.close()
    return {
        "total": int(total or 0),
        "high": int(high or 0),
        "running": int(running or 0),
        "avg_progress": round(float(avg or 0), 1),
    }


_all_lots_cache = {}   # category(molding/potting) -> {"data": [...全量lot...], "ts": float}
_OWNER_LOTS_TTL = 30   # 秒;统计页加载与紧随的点击下钻共用,避免重复扫 CSV


def _collect_all_lots(force=False):
    """一次性扫 MES CSV,返回「当前类别」全部 Lot(不按负责人过滤)。
    每条附:owner(责任负责人,可空)、base(基础型号,用于Owner/CT匹配)、
    model(原始录入型号,显示用)、in_progress(所属工单是否未完成 progress<100)。
    负责人工作量统计 与「产品在制WIP」图 共用本结果(30秒缓存),统计页只读一遍 CSV。"""
    import time
    now = time.time()
    cat = get_category()          # 按当前业务类别(塑封/灌封)分桶,避免跨库缓存污染
    ent = _all_lots_cache.get(cat)
    if (not force and ent is not None
            and now - ent["ts"] < _OWNER_LOTS_TTL):
        return ent["data"]

    import mes
    conn = get_conn()
    orders = [dict(r) for r in conn.execute(
        "SELECT wo_no, product, COALESCE(progress,0) AS prog, "
        "COALESCE(manual_done,0) AS mdone FROM work_orders").fetchall()]
    conn.close()
    base_of = {o["wo_no"]: mes.base_model(o.get("product"))
               for o in orders}                                  # 基础型号(Owner/CT 匹配)
    full_of = {o["wo_no"]: (o.get("product") or "").strip() for o in orders}  # 显示型号
    prog_of = {o["wo_no"]: o.get("prog", 0) for o in orders}
    # 「未完成」口径与看板/明细一致:进度<100 且 未被人工标记完成(manual_done)。
    # 否则人工标完成的工单其 Lot 仍会漏进 WIP 分布图 / 负责人工作量统计。
    done_of = {o["wo_no"]: ((o.get("prog", 0) or 0) >= 100 or (o.get("mdone", 0) or 0) == 1)
               for o in orders}
    wo_keys = list(base_of.keys())

    out = []
    try:
        lots = mes.lookup_all_lots(wo_keys)   # 只读一遍 CSV
    except Exception:
        lots = []
    for l in lots:
        wk = l.get("wo_key", "")
        base = base_of.get(wk, "")
        out.append({
            "owner": owner_for(base, l["spec"], l["status"]),   # 责任负责人(可空)
            "status": l["status"],
            "lot_id": l["lot_id"], "wo_no": wk,
            "full_wo": l.get("full_wo", wk),  # MES 真实工单号(E/F/0首位),用于详情展示
            "base": base,
            "model": full_of.get(wk, "") or base,               # 显示用型号 如 MB12 / 4in1
            "product": l.get("product", ""),
            "spec": l["spec"], "desc": l.get("desc", ""),
            "qty": l["qty"],
            "eqpld": l.get("eqpld", ""),                        # 当前所在设备号(EQPID),显示为 EQP ID
            "run_time": l.get("run_time", "-"),
            "wait_time": l.get("wait_time", "-"),
            "hold_time": l.get("hold_time", "-"),
            "hold_reason": l.get("hold_reason", ""),    # HOLD原因(HOLDREASONNAME)
            "txn_comment": l.get("txn_comment", ""),    # MES操作备注(TXNCOMMENTS)
            "in_progress": not done_of.get(wk, False),
        })
    _all_lots_cache[cat] = {"data": out, "ts": now}
    return out


def _collect_owner_lots(force=False):
    """负责人工作量/下钻用:从全量 lot 里筛出「有责任负责人」且「所属工单未完成」的。
    已完成(进度≥100 或人工标记完成)的工单已移出看板/明细,其残留 Lot 不计入工作量。"""
    return [l for l in _collect_all_lots(force) if l["owner"] and l.get("in_progress")]


def owner_lot_workload(q=None):
    """按 Lot 数统计每个负责人各状态的 Lot 数,供工作量条形图。
    q:搜索关键词(与统计页 WIP 图搜索同口径:wo_no/产品/型号/基础型号/Lot号
    小写后缀匹配,忽略大小写);空/None=全量。
    返回 {owners:[...], by_status:{status:[count...]}}(顺序对齐 owners)。"""
    lots = _collect_owner_lots()
    if q:
        q = str(q).lower().strip()
        if q:
            lots = [l for l in lots
                    if any((l.get(f) or "").lower().endswith(q)
                           for f in ("wo_no", "product", "model", "base", "lot_id"))]
    STAT_KEYS = ["运行中", "等待进站", "等待出站", "HOLD"]
    agg = {}   # owner -> {status -> count}
    for l in lots:
        st = l["status"] if l["status"] in STAT_KEYS else "其他"
        agg.setdefault(l["owner"], {}).setdefault(st, 0)
        agg[l["owner"]][st] += 1
    owners = sorted(agg.keys())
    keys = STAT_KEYS + (["其他"] if any("其他" in v for v in agg.values()) else [])
    by_status = {k: [agg[o].get(k, 0) for o in owners] for k in keys}
    return {"owners": owners, "by_status": by_status}


def lots_of_owner(owner, status=None):
    """某负责人(可指定状态)的 Lot 明细,供点击下钻。"""
    if not owner:
        return []
    lots = [l for l in _collect_owner_lots() if l["owner"] == owner]
    if status:
        if status == "其他":
            STAT_KEYS = ["运行中", "等待进站", "等待出站", "HOLD"]
            lots = [l for l in lots if l["status"] not in STAT_KEYS]
        else:
            lots = [l for l in lots if l["status"] == status]
    return lots


# ---------- 残留 LOT 忽略 ----------
def get_ignored_lot_ids():
    """返回被忽略的 lot_id 集合。"""
    conn = get_conn()
    ids = {r[0] for r in conn.execute("SELECT lot_id FROM ignored_lots").fetchall()}
    conn.close()
    return ids


def ignored_lots_by_wo():
    """{wo_no: set(lot_id)};供 sync 重算时按工单剔除忽略 LOT。无忽略时返回空 dict。"""
    conn = get_conn()
    out = {}
    for lot_id, wo_no in conn.execute("SELECT lot_id, wo_no FROM ignored_lots").fetchall():
        out.setdefault(wo_no, set()).add(lot_id)
    conn.close()
    return out


def set_lot_ignored(lot_id, wo_no, spec, ignore, user="", note=""):
    """勾选/取消忽略某 LOT。ignore 为真则记入(含备注 note),否则移除。
    备注用于标注该 LOT 异常/不作业原因,看板与统计页共用同一份记录。"""
    lot_id = (lot_id or "").strip()
    if not lot_id:
        return
    conn = get_conn()
    if ignore:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO ignored_lots(lot_id, wo_no, spec, ignored_at, ignored_by, note) "
            "VALUES (?,?,?,?,?,?)", (lot_id, wo_no, spec, now, user, (note or "").strip()))
    else:
        conn.execute("DELETE FROM ignored_lots WHERE lot_id=?", (lot_id,))
    conn.commit()
    conn.close()


def ignored_lots_note_map():
    """{lot_id: note} 全部被忽略 LOT 的备注,供看板/统计页展示忽略原因。"""
    conn = get_conn()
    try:
        out = {r[0]: (r[1] or "") for r in
               conn.execute("SELECT lot_id, note FROM ignored_lots").fetchall()}
    except sqlite3.OperationalError:
        # 旧库尚未迁移 note 列:退回仅取 lot_id(备注空),避免只读路径报错
        out = {r[0]: "" for r in
               conn.execute("SELECT lot_id FROM ignored_lots").fetchall()}
    conn.close()
    return out


def lots_of_wo_spec(wo_no, spec):
    """某工单某工序(站点)下的全部 LOT 明细,供甘特图点击色块下钻。
    复用 owner 弹层的字段口径,并附带每个 LOT 当前是否被忽略(ignored)。"""
    import mes
    if not wo_no:
        return []
    try:
        lots = [l for l in mes.lookup_all_lots([wo_no]) if l["wo_key"] == wo_no]
    except Exception:
        lots = []
    if spec:
        lots = [l for l in lots if l["spec"] == spec]
    note_map = ignored_lots_note_map()
    out = []
    for l in lots:
        d = dict(l)
        d["wo_no"] = l["wo_key"]
        d["ignored"] = 1 if l["lot_id"] in note_map else 0
        d["ignore_note"] = note_map.get(l["lot_id"], "")
        out.append(d)
    return out


# ---------- 异常记录(Wait/HOLD 超阈值) ----------
def get_exc_thresholds():
    """返回 (wait_h, hold_h) 系统默认阈值(小时)。默认 0 = 不监控,需显式配置。"""
    def _f(k, d):
        try:
            return float(meta_get(k, "") or d)
        except (ValueError, TypeError):
            return d
    return _f("exc_wait_h", 0.0), _f("exc_hold_h", 0.0)


def set_exc_thresholds(wait_h, hold_h):
    """系统默认阈值(所有型号/工序的兜底)。"""
    meta_set("exc_wait_h", str(wait_h))
    meta_set("exc_hold_h", str(hold_h))


def set_exc_threshold(model, spec, wait_h, hold_h):
    """设定某型号(spec='' 为型号全局)或某型号某工序的阈值。"""
    model = (model or "").strip()
    spec = (spec or "").strip()
    if not model:
        return
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO exc_thresholds(model,spec,wait_h,hold_h) "
                 "VALUES (?,?,?,?)", (model, spec, float(wait_h), float(hold_h)))
    conn.commit()
    conn.close()


def del_exc_threshold(model, spec):
    conn = get_conn()
    conn.execute("DELETE FROM exc_thresholds WHERE model=? AND spec=?",
                 ((model or "").strip(), (spec or "").strip()))
    conn.commit()
    conn.close()


def list_exc_thresholds():
    """全部分级阈值,供后台「已设置阈值的工序」列表。型号全局排前。"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT model, spec, wait_h, hold_h FROM exc_thresholds "
        "ORDER BY model, (spec!=''), spec").fetchall()]
    conn.close()
    return rows


def _threshold_map():
    """读全部分级阈值为 {(model,spec): (wait_h,hold_h)},供扫描时按 Lot 解析。"""
    conn = get_conn()
    m = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT model, spec, wait_h, hold_h FROM exc_thresholds").fetchall()}
    conn.close()
    return m


def _wo_threshold_map():
    """工单级阈值 {(wo_no,spec): (wait_h,hold_h)}。spec='' 为整单。"""
    conn = get_conn()
    m = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT wo_no, spec, wait_h, hold_h FROM exc_wo_thresholds").fetchall()}
    conn.close()
    return m


def set_wo_threshold(wo_no, spec, wait_h, hold_h):
    wo_no = (wo_no or "").strip()
    if not wo_no:
        return
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO exc_wo_thresholds(wo_no,spec,wait_h,hold_h) "
                 "VALUES (?,?,?,?)", (wo_no, (spec or "").strip(), float(wait_h), float(hold_h)))
    conn.commit()
    conn.close()


def del_wo_threshold(wo_no, spec):
    conn = get_conn()
    conn.execute("DELETE FROM exc_wo_thresholds WHERE wo_no=? AND spec=?",
                 ((wo_no or "").strip(), (spec or "").strip()))
    conn.commit()
    conn.close()


def wo_ct_specs(wo_no):
    """按工单号取其产品对应的 CT 工序列表(供异常工单阈值的工序多选)。
    工单 product 归一到基础封装型号 → 该型号 CT 表的工序。"""
    conn = get_conn()
    r = conn.execute("SELECT product FROM work_orders WHERE wo_no=?", (wo_no,)).fetchone()
    conn.close()
    if not r:
        return {"model": "", "specs": []}
    model = mes.base_model(r[0])
    ctab = get_ct_table(model) or {}
    specs = [{"spec": s.get("spec", ""), "name": s.get("stepname", "") or s.get("spec", "")}
             for s in ctab.get("specs", [])]
    return {"model": model, "specs": specs}


def list_wo_thresholds():
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT wo_no, spec, wait_h, hold_h FROM exc_wo_thresholds "
        "ORDER BY wo_no, (spec!=''), spec").fetchall()]
    conn.close()
    return rows


def resolve_threshold(model, spec, tmap=None, sys_default=None, wo_no=None, womap=None):
    """优先级:(wo,spec) > (wo,'') > (model,spec) > (model,'') > 系统默认。返回 (wait_h, hold_h)。"""
    if tmap is None:
        tmap = _threshold_map()
    if sys_default is None:
        sys_default = get_exc_thresholds()
    model = (model or "").strip()
    spec = (spec or "").strip()
    wo_no = (wo_no or "").strip()
    if wo_no and womap:
        if (wo_no, spec) in womap:
            return womap[(wo_no, spec)]
        if (wo_no, "") in womap:
            return womap[(wo_no, "")]
    if (model, spec) in tmap:
        return tmap[(model, spec)]
    if (model, "") in tmap:
        return tmap[(model, "")]
    return sys_default


def _parse_hours(s):
    """'3.77h' / '3.77' / '-' → float 小时。"""
    try:
        return float(str(s).replace("h", "").strip())
    except (ValueError, TypeError):
        return 0.0


def scan_exceptions():
    """扫描当前在制 Lot,对 Wait/HOLD 超阈值者生成/更新异常记录;
    已不再超时(离开该状态或离开 MES)的进行中记录 → 标记已解除并填流出时间。
    并按「当前阈值」复核全表:记录须「有阈值(>0)且峰值达标」才保留,否则(未设监控
    或峰值低于现行阈值)清除;已人工填写 Owner/备注的记录豁免保留(视为已确认·有追溯价值)。
    返回当前命中(进行中)的异常数。"""
    import mes
    sys_default = get_exc_thresholds()
    tmap = _threshold_map()
    womap = _wo_threshold_map()
    conn = get_conn()
    wo_model = {r[0]: (mes.base_model(r[1]))
                for r in conn.execute("SELECT wo_no, product FROM work_orders").fetchall()}
    conn.close()
    wos = list(wo_model.keys())
    if not wos:
        return 0
    now = datetime.datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        lots = mes.lookup_all_lots(wos)
    except Exception:
        return 0
    seen = set()
    conn = get_conn()
    # 清掉任何虚拟站(建批首站/测试后虚拟站等)已有的异常记录:这些站是占位/待销账,
    # 在此等待不算真实异常(等 05 不入库工单销账后会从 MES 消失)。
    conn.execute("DELETE FROM exceptions WHERE spec_name LIKE '%虚拟站%' OR spec='MB_AGITEM03' "
                 "OR spec_name LIKE '%送报废仓%' OR spec='TOPURGELOCATION'")
    for l in lots:
        lot_id, spec = l.get("lot_id", ""), l.get("spec", "")
        if not lot_id:
            continue
        desc = l.get("desc", "")
        # 虚拟站 / 送报废仓等占位暂存站不计异常,与进度计算同一判定口径(spec_is_virtual)。
        if mes.spec_is_virtual(spec, desc):
            continue   # 虚拟工序 / 送报废仓 不记录异常
        wo = l.get("wo_key", "")
        model = wo_model.get(wo, "")
        wait_th, hold_th = resolve_threshold(model, spec, tmap, sys_default, wo_no=wo, womap=womap)
        checks = []
        wh, hh = _parse_hours(l.get("wait_time")), _parse_hours(l.get("hold_time"))
        if wait_th > 0 and wh > wait_th:
            checks.append(("WAIT", wh))
        if hold_th > 0 and hh > hold_th:
            checks.append(("HOLD", hh))
        for etype, hrs in checks:
            seen.add((lot_id, spec, etype))
            row = conn.execute(
                "SELECT id, peak_hours FROM exceptions "
                "WHERE lot_id=? AND spec=? AND etype=? AND status='进行中'",
                (lot_id, spec, etype)).fetchone()
            if row:
                peak = max(row[1] or 0, hrs)
                conn.execute("UPDATE exceptions SET peak_hours=?, updated_at=? WHERE id=?",
                             (peak, now_s, row[0]))
            else:
                enter_at = (now - datetime.timedelta(hours=hrs)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO exceptions(lot_id,wo_no,spec,spec_name,etype,enter_at,"
                    "exit_at,peak_hours,status,owner,remark,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lot_id, l.get("wo_key", ""), spec, l.get("desc", ""), etype,
                     enter_at, "", hrs, "进行中", "", "", now_s, now_s))
    # 解除:进行中但本轮未再命中的 → 已解除 + 流出时间
    for rid, lid, sp, et in conn.execute(
            "SELECT id, lot_id, spec, etype FROM exceptions WHERE status='进行中'").fetchall():
        if (lid, sp, et) not in seen:
            conn.execute("UPDATE exceptions SET status='已解除', exit_at=?, updated_at=? WHERE id=?",
                         (now_s, now_s, rid))
    # 按「当前阈值」复核全表:异常记录以现行阈值为唯一标准。一条记录要成立,必须
    # 「该工序当前确有阈值(>0) 且 历史峰值 peak_hours 已达到该阈值」;否则——无论是
    # 未配置监控(阈值=0)还是峰值已低于现行阈值(含早期阈值设小的误报)——都视为不再
    # 成立而清除。即:监控没设/已撤,异常记录不残留。
    # 豁免:已人工填写 Owner 或备注的记录视为已确认·有追溯价值,永久保留,不随复核删除。
    for rid, wo, sp, et, peak, owner, remark in conn.execute(
            "SELECT id, wo_no, spec, etype, peak_hours, owner, remark FROM exceptions").fetchall():
        if (owner or "").strip() or (remark or "").strip():
            continue
        w_th, h_th = resolve_threshold(wo_model.get(wo, ""), sp, tmap, sys_default,
                                       wo_no=wo, womap=womap)
        cur_th = w_th if et == "WAIT" else h_th
        if not (cur_th > 0 and (peak or 0) >= cur_th):
            conn.execute("DELETE FROM exceptions WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return len(seen)


def list_exceptions():
    """全部异常记录:进行中优先,再按最近更新倒序。"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM exceptions "
        "ORDER BY (status='进行中') DESC, COALESCE(updated_at, created_at) DESC").fetchall()]
    conn.close()
    return rows


def list_exception_wos():
    """异常记录按工单号汇总:总异常/Wait/HOLD 时长(含已解除·累计) + 工单备注 + 进行中数。"""
    conn = get_conn()
    remarks = {r[0]: (r[1] or "") for r in
               conn.execute("SELECT wo_no, remark FROM work_orders").fetchall()}
    agg = {}
    for wo, etype, peak, status in conn.execute(
            "SELECT wo_no, etype, peak_hours, status FROM exceptions").fetchall():
        wo = wo or ""
        a = agg.setdefault(wo, {"wo_no": wo, "total_h": 0.0, "wait_h": 0.0,
                                "hold_h": 0.0, "active": 0, "cnt": 0})
        h = peak or 0
        a["total_h"] += h
        if etype == "WAIT":
            a["wait_h"] += h
        elif etype == "HOLD":
            a["hold_h"] += h
        if status == "进行中":
            a["active"] += 1
        a["cnt"] += 1
    conn.close()
    out = list(agg.values())
    for a in out:
        a["remark"] = remarks.get(a["wo_no"], "")
    out.sort(key=lambda a: (a["active"] > 0, a["total_h"]), reverse=True)
    return out


def _ph_hours(s):
    """把 '36.00h' / '-' 形式的时长解析成小时数(float),解析不了返回 0。"""
    try:
        return float(str(s).replace("h", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _slack_from_note(note):
    """从 risk_note 文本里解析富余天数:超前=正、滞后=负;取不到返回 None(排末尾)。"""
    import re
    note = note or ""
    m = re.search(r"滞后\s*([\d.]+)\s*天", note)
    if m:
        return -float(m.group(1))
    m = re.search(r"超前\s*([\d.]+)\s*天", note)
    if m:
        return float(m.group(1))
    return None


def _bottleneck_by_wo():
    """每个未完成工单的「瓶颈工序」=在制 lot 里停留最久的那道工序(卡在哪步·卡多久)。
    HOLD(做不了)比 Wait(排队)更严重,优先取 HOLD;同类取停留最久。
    返回 {wo_no: {kind(HOLD/WAIT), spec, desc, max_h, qty(该工序同类卡滞总颗数), lots}}。"""
    ct_cache = {}   # base_model -> CT表工序集合(无CT表为 None)
    def _ct_specs_of(base):
        if base not in ct_cache:
            ctab = get_ct_table(base)
            ct_cache[base] = (set(s.get("spec", "") for s in ctab.get("specs", []))
                              if ctab else None)
        return ct_cache[base]

    try:
        ignored_ids = get_ignored_lot_ids()      # 已忽略(视同报废)的 lot,不算卡点
    except Exception:
        ignored_ids = set()

    groups = {}   # (wo, kind, spec) -> 聚合
    for l in _collect_all_lots():
        if not l.get("in_progress"):
            continue
        if l.get("lot_id") in ignored_ids:
            continue                       # 已忽略的 LOT 不计入瓶颈/需要关注
        st = l.get("status", "")
        if st == "HOLD":
            kind, hrs = "HOLD", _ph_hours(l.get("hold_time"))
        elif st in ("等待进站", "等待出站"):
            kind, hrs = "WAIT", _ph_hours(l.get("wait_time"))
        else:
            continue                       # 运行中/已出站 不算卡滞
        if hrs <= 0:
            continue
        # 虚拟站(不在该型号 CT 表里的非生产工序,如测试虚拟站)不算卡点,不进「需要关注」
        cs = _ct_specs_of(l.get("base", ""))
        if cs is not None and l.get("spec", "") not in cs:
            continue
        wo = l.get("wo_no", "")
        key = (wo, kind, l.get("spec", ""))
        g = groups.setdefault(key, {
            "wo": wo, "kind": kind, "spec": l.get("spec", ""),
            "desc": l.get("desc", "") or l.get("spec", ""),
            "max_h": 0.0, "qty": 0, "lots": 0})
        g["max_h"] = max(g["max_h"], hrs)
        try:
            g["qty"] += int(float(l.get("qty", 0) or 0))
        except (ValueError, TypeError):
            pass
        g["lots"] += 1
    best = {}
    for g in groups.values():
        rank = 0 if g["kind"] == "HOLD" else 1     # HOLD 优先
        score = (rank, -g["max_h"])
        cur = best.get(g["wo"])
        if cur is None or score < cur["_score"]:
            g2 = dict(g)
            g2["_score"] = score
            best[g["wo"]] = g2
    return best


def attention_orders():
    """看板顶部「需要关注」清单:把分散在各处的问题信号(高风险 / 挂起 / 进行中异常)
    按工单合并成一屏可读的清单,并附「卡在哪步·卡多久」瓶颈工序。只返回确有问题的工单。
    严重度排序:高风险 > 挂起 > 异常;同级滞后越多越靠前。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT wo_no,product,owner,plan_end,risk_level,risk_note,eta,"
        "resume_date,wait_reason FROM work_orders "
        "WHERE COALESCE(progress,0)<100 AND COALESCE(manual_done,0)=0").fetchall()]
    conn.close()
    # 进行中异常计数:排除已忽略(视同报废)的 LOT,使忽略后的工序不再上榜
    try:
        ignored_ids = get_ignored_lot_ids()
    except Exception:
        ignored_ids = set()
    conn2 = get_conn()
    active_exc_by_wo = {}
    for ewo, elot in conn2.execute(
            "SELECT wo_no, lot_id FROM exceptions WHERE status='进行中'").fetchall():
        if elot in ignored_ids:
            continue
        active_exc_by_wo[ewo] = active_exc_by_wo.get(ewo, 0) + 1
    conn2.close()
    bott = _bottleneck_by_wo()
    _today = datetime.date.today().isoformat()   # YYYY-MM-DD,与 resume_date 同格式可直接比较
    out = []
    for r in rows:
        wo = r.get("wo_no", "")
        rl = (r.get("risk_level") or "").strip()
        rd = (r.get("resume_date") or "").strip()
        held = bool(rd)
        # 挂起且恢复作业日还没到 → 已有恢复计划,暂不需要关注,整单不上榜(到恢复日当天起再显示)
        if held and rd > _today:
            continue
        active_exc = active_exc_by_wo.get(wo, 0)
        reasons = []
        sev = 9
        if rl == "高风险":
            reasons.append({"type": "risk", "label": "高风险",
                            "detail": (r.get("risk_note") or "")})
            sev = min(sev, 0)
        # 挂起恢复日已过(未到的上面已跳过)→ 不再当「挂起」看;它现在 HOLD 住了,
        # 按「异常」显示;若已恢复(不再 HOLD 且无异常记录)则自动从清单消失。
        b = bott.get(wo)
        held_stuck = held and bool(b) and b.get("kind") == "HOLD"
        if active_exc or held_stuck:
            detail = ("%d 条进行中" % active_exc) if active_exc else "工序 HOLD 中"
            reasons.append({"type": "exc", "label": "异常", "detail": detail})
            sev = min(sev, 2)
        if not reasons:
            continue                       # 没问题的不进清单
        out.append({
            "wo_no": wo,
            "model": mes.base_model(r.get("product")),
            "product": r.get("product") or "",
            "owner": r.get("owner") or "",
            "plan_end": r.get("plan_end") or "",
            "eta": r.get("eta") or "",
            "risk_level": rl,
            "risk_note": r.get("risk_note") or "",
            "reasons": reasons,
            "sev": sev,
            "bottleneck": ({"kind": b["kind"], "desc": b["desc"], "spec": b["spec"],
                            "hours": round(b["max_h"], 1), "qty": b["qty"]} if b else None),
            "_slack": _slack_from_note(r.get("risk_note") or ""),
        })
    # 排序:① 高风险 在前(sev:高风险0 < 异常2);② 同级按 HOLD/卡滞时间从长到短;
    #       ③ 再按滞后天数。即「高风险 > 异常」,组内 HOLD 越久越靠前。
    def _hold_h(x):
        b = x.get("bottleneck")
        return b["hours"] if b else 0
    out.sort(key=lambda x: (x["sev"], -_hold_h(x),
                            x["_slack"] if x["_slack"] is not None else 9999))
    for x in out:
        x.pop("_slack", None)
    return out


def exceptions_of_wo(wo_no):
    """某工单下的全部异常记录(Lot 级),供工单展开/弹层查看。"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM exceptions WHERE wo_no=? "
        "ORDER BY (status='进行中') DESC, COALESCE(updated_at, created_at) DESC",
        (wo_no,)).fetchall()]
    conn.close()
    return rows


def update_exception(eid, owner, remark):
    """管理员填写/修改异常记录的责任Owner与备注。
    若把 Owner 与备注都清空(撤销人工确认),该记录即失去豁免:按当前阈值复核,
    不再成立(未配置监控 或 峰值未达现行阈值)则连同记录一并删除,与扫描复核口径一致。"""
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE exceptions SET owner=?, remark=?, updated_at=? WHERE id=?",
                 (owner or "", remark or "", now, eid))
    if not (owner or "").strip() and not (remark or "").strip():
        row = conn.execute(
            "SELECT wo_no, spec, etype, peak_hours FROM exceptions WHERE id=?",
            (eid,)).fetchone()
        if row:
            wo, sp, et, peak = row[0], row[1], row[2], row[3]
            prow = conn.execute(
                "SELECT product FROM work_orders WHERE wo_no=?", (wo,)).fetchone()
            model = (mes.base_model(prow[0])) if prow else ""
            w_th, h_th = resolve_threshold(model, sp, wo_no=wo)
            cur_th = w_th if et == "WAIT" else h_th
            if not (cur_th > 0 and (peak or 0) >= cur_th):
                conn.execute("DELETE FROM exceptions WHERE id=?", (eid,))
    conn.commit()
    conn.close()


# ---------- 加急验证 Lot ----------
def detect_ct_model(product):
    """按 PRODUCTNAME 在 MES 的工序名,匹配到 specs 重合最多的已上传 CT 表型号。
    返回 (model, ctab) 或 ('', None)。用于加急 Lot 自动判定该产品用哪张 CT 表。"""
    import mes
    try:
        psp = mes.product_specs(product)
    except Exception:
        psp = []
    pspecs = {p["spec"] for p in psp if p.get("spec")}
    if not pspecs:
        return "", None
    best, best_n, best_ct = "", 0, None
    for t in list_ct_tables():
        ctab = get_ct_table(t["model"])
        if not ctab:
            continue
        ctspecs = {s.get("spec", "") for s in ctab.get("specs", [])}
        n = len(pspecs & ctspecs)
        if n > best_n:
            best, best_n, best_ct = t["model"], n, ctab
    return best, best_ct


def create_urgent_batch(batch_name, product, target_spec, target_name, plan_end, lot_ids,
                        ct_model=None):
    """新建加急验证 Lot 批次 + 其 Lot 列表。ct_model 未给则按产品自动匹配。返回 batch_id。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not ct_model:
        ct_model, _ = detect_ct_model(product)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO urgent_batches(batch_name,product,ct_model,target_spec,target_name,plan_end,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ((batch_name or "").strip(), (product or "").strip(), (ct_model or "").strip(),
         (target_spec or "").strip(), (target_name or "").strip(), (plan_end or "").strip(), now))
    bid = cur.lastrowid
    for lid in lot_ids:
        lid = (lid or "").strip()
        if lid:
            conn.execute("INSERT INTO urgent_lots(batch_id,lot_id) VALUES (?,?)", (bid, lid))
    conn.commit()
    conn.close()
    return bid


def update_urgent_batch(batch_id, batch_name, product, target_spec, target_name,
                        plan_end, lot_ids, ct_model=None):
    """编辑加急批次:更新批次字段并以新列表替换其 Lot。ct_model 未给则按产品重判。"""
    if not ct_model:
        ct_model, _ = detect_ct_model(product)
    conn = get_conn()
    conn.execute(
        "UPDATE urgent_batches SET batch_name=?,product=?,ct_model=?,target_spec=?,"
        "target_name=?,plan_end=? WHERE id=?",
        ((batch_name or "").strip(), (product or "").strip(), (ct_model or "").strip(),
         (target_spec or "").strip(), (target_name or "").strip(),
         (plan_end or "").strip(), batch_id))
    conn.execute("DELETE FROM urgent_lots WHERE batch_id=?", (batch_id,))
    for lid in lot_ids:
        lid = (lid or "").strip()
        if lid:
            conn.execute("INSERT INTO urgent_lots(batch_id,lot_id) VALUES (?,?)", (batch_id, lid))
    conn.commit()
    conn.close()


def delete_urgent_batch(batch_id):
    conn = get_conn()
    conn.execute("DELETE FROM urgent_lots WHERE batch_id=?", (batch_id,))
    conn.execute("DELETE FROM urgent_batches WHERE id=?", (batch_id,))
    conn.commit()
    conn.close()


def list_urgent_batches():
    """轻量列表(不查 MES):批次 + Lot 数,供后台列表展示。"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    batches = [dict(b) for b in conn.execute(
        "SELECT * FROM urgent_batches ORDER BY id DESC").fetchall()]
    cnt = {r[0]: r[1] for r in conn.execute(
        "SELECT batch_id, COUNT(*) FROM urgent_lots GROUP BY batch_id").fetchall()}
    conn.close()
    for b in batches:
        b["lot_count"] = cnt.get(b["id"], 0)
    return batches


def _urgent_segs_and_target(ctab, target_spec):
    """从 CT 表生成工序段(供进度条) + 目标工序的截止累计CT/占比。"""
    segs, target_cum, target_ratio = [], None, None
    if not (ctab and ctab.get("total_ct")):
        return segs, target_cum, target_ratio
    total = ctab["total_ct"]
    specs = ctab.get("specs", [])
    for i, sp in enumerate(specs):
        cum = sp.get("cum_ct", 0) or 0
        prev = (specs[i - 1].get("cum_ct", 0) or 0) if i > 0 else 0
        segs.append({"spec": sp.get("spec", ""),
                     "name": sp.get("stepname", "") or sp.get("spec", ""),
                     "cum_ratio": (prev / total) if total else 0,
                     "ratio": max(0, (cum - prev) / total) if total else 0})
        if sp.get("spec") == target_spec:
            target_cum, target_ratio = cum, (cum / total if total else None)
    return segs, target_cum, target_ratio


def urgent_batches_with_status(only_batch=None):
    """全部(或指定)加急批次 + 每个 Lot 的当前工序/状态/进度(到目标工序折算)。
    only_batch 给定时只算该批次。"""
    import ct as ctmod
    import ct_learn
    import mes
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM urgent_batches"
    args = []
    if only_batch is not None:
        sql += " WHERE id=?"
        args.append(only_batch)
    sql += " ORDER BY id DESC"
    batches = [dict(b) for b in conn.execute(sql, args).fetchall()]
    lots_by_b = {}
    for r in conn.execute("SELECT batch_id, lot_id FROM urgent_lots").fetchall():
        lots_by_b.setdefault(r["batch_id"], []).append(r["lot_id"])
    conn.close()

    all_ids = set()
    for b in batches:
        all_ids.update(lots_by_b.get(b["id"], []))
    try:
        live = mes.lookup_lots_by_ids(list(all_ids)) if all_ids else {}
    except Exception:
        live = {}

    ct_method = ct_method_map()
    ct_cache = {}

    def get_ct_blend(model):
        if model not in ct_cache:
            ctab = get_ct_table(model)
            if ctab and ct_method.get(model):
                ctab = ctmod.blend_ct(ctab, ct_learn.learned_ct(model))
            ct_cache[model] = ctab
        return ct_cache[model]

    for b in batches:
        model = (b.get("ct_model") or "").strip()
        if not model:   # 老批次没存 ct_model:按产品名即时匹配
            model, _ = detect_ct_model(b.get("product"))
        ctab = get_ct_blend(model)
        segs, target_cum, target_ratio = _urgent_segs_and_target(ctab, b.get("target_spec"))
        b["segs"] = segs
        b["target_ratio"] = target_ratio
        lots = []
        for lid in lots_by_b.get(b["id"], []):
            info = live.get(lid)
            lot = {"lot_id": lid, "product": "", "wo_no": "", "qty": 0, "spec": "", "spec_name": "",
                   "status": "未在MES找到", "progress": 0, "done": False, "eqpld": "",
                   "run_time": "-", "wait_time": "-", "hold_time": "-",
                   "hold_reason": "", "txn_comment": ""}
            if info:
                lot["product"] = info.get("product", "")
                lot["wo_no"] = info.get("full_wo", "")   # MES 真实工单号(MFGORDERNAME)
                lot["qty"] = info.get("qty", 0)
                lot["eqpld"] = info.get("eqpld", "")     # 当前所在设备号(EQP ID)
                lot["spec"] = info.get("spec", "")
                lot["spec_name"] = info.get("desc", "") or info.get("spec", "")
                lot["status"] = info.get("status", "")
                lot["run_time"] = info.get("run_time", "-")
                lot["wait_time"] = info.get("wait_time", "-")
                lot["hold_time"] = info.get("hold_time", "-")
                lot["hold_reason"] = info.get("hold_reason", "")
                lot["txn_comment"] = info.get("txn_comment", "")
                if ctab and target_cum:
                    r = ctmod.compute(info.get("spec", ""), "", ctab, target_cum=target_cum)
                    lot["progress"] = r.get("progress", 0)
                    lot["done"] = (r.get("progress", 0) >= 100)
                    lot["matched"] = r.get("matched", False)
            lots.append(lot)
        b["lots"] = lots
        b["lot_count"] = len(lots)
        b["done_count"] = sum(1 for l in lots if l["done"])
    return batches


def orders_by_owner(owner):
    """某负责人名下的工单(用于统计页点击查看)。"""
    if not owner:
        return []
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT wo_no, product, status, cur_process FROM work_orders "
        "WHERE owner=? ORDER BY id", (owner,)).fetchall()]
    conn.close()
    return rows


def filter_options():
    """返回筛选栏所需的各维度可选值(来自实际工单 + 基础数据,去重)。"""
    conn = get_conn()
    def distinct(col):
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {col} FROM work_orders WHERE {col} IS NOT NULL AND {col}!='' ORDER BY {col}").fetchall()]
    opts = {
        "status":   ["待开始", "进行中", "已完成", "已延期"],
        "product":  [r[0] for r in conn.execute(
                      "SELECT value FROM base_data WHERE kind='product' ORDER BY id").fetchall()] or distinct("product"),
        "owner":    [r[0] for r in conn.execute(
                      "SELECT value FROM base_data WHERE kind='owner' ORDER BY id").fetchall()] or distinct("owner"),
        "priority": ["火箭批", "1-0", "1-1", "Normal"],
        "risk_level": ["超前", "正常", "低风险", "高风险"],
        "demander": distinct("demander"),
    }
    conn.close()
    return opts


# ---------- 工单 CRUD ----------
WO_FIELDS = ("wo_no", "product", "customer", "demander", "status", "priority",
             "plan_start", "plan_end", "owner", "cur_process",
             "progress", "risk_level", "risk_note", "remark", "final_process",
             "deliver_qty", "eng_times")


def get_order(oid):
    conn = get_conn()
    r = conn.execute("SELECT * FROM work_orders WHERE id=?", (oid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def wo_exists(wo):
    """按归一化工单号(忽略前导*)判断是否已存在。"""
    norm = (wo or "").strip().lstrip("*")
    if not norm:
        return False
    conn = get_conn()
    r = conn.execute(
        "SELECT 1 FROM work_orders WHERE REPLACE(wo_no,'*','')=? LIMIT 1",
        (norm,)).fetchone()
    conn.close()
    return bool(r)


def create_order(data):
    conn = get_conn()
    data = dict(data)
    data.setdefault("status", "待同步")     # 首次同步前的占位
    data.setdefault("progress", 0)
    # 工单号强制以 * 开头(* 为通配首位)
    wo = (data.get("wo_no") or "").strip()
    if wo and not wo.startswith("*"):
        wo = "*" + wo.lstrip("*")
    data["wo_no"] = wo
    if not wo:
        conn.close()
        return (False, "工单号不能为空")
    # 查重:按归一化工单号(去掉前导*后比较),已存在则提示
    norm = wo.lstrip("*")
    exist = conn.execute(
        "SELECT wo_no FROM work_orders WHERE REPLACE(wo_no,'*','')=?",
        (norm,)).fetchone()
    if exist:
        conn.close()
        return (False, f"工单 {wo} 已添加,请勿重复创建")
    vals = [data.get(f, "") for f in WO_FIELDS]
    cols = ",".join(WO_FIELDS)
    qs = ",".join("?" * len(WO_FIELDS))
    today = datetime.date.today().isoformat()
    conn.execute(f"INSERT INTO work_orders ({cols},created_at) VALUES ({qs},?)",
                 vals + [today])
    conn.commit()
    conn.close()
    return (True, f"工单 {wo} 已创建")


def update_order(oid, data):
    conn = get_conn()
    # 只更新表单实际提交的字段,避免把 MES 同步字段
    # (status/progress/cur_process/risk_*/mes_status)清空
    editable = [f for f in WO_FIELDS if f in data]
    if editable:
        sets = ",".join(f"{f}=?" for f in editable)
        vals = [data.get(f, "") for f in editable] + [oid]
        conn.execute(f"UPDATE work_orders SET {sets} WHERE id=?", vals)
        conn.commit()
    conn.close()


def _recompute_eta_for_resume(conn, wo_no, rd):
    """据「最晚恢复日 rd」(可空)立即重算该单 ETA/风险并写回。与原 set_wait_hold 内逻辑一致。"""
    import ct as ctmod
    import ct_learn
    r = conn.execute(
        "SELECT product, plan_start, plan_end, tail_spec, final_process "
        "FROM work_orders WHERE wo_no=?", (wo_no,)).fetchone()
    if not (r and (r["tail_spec"] or "").strip()):
        return
    model = mes.base_model(r["product"])
    ctab = get_ct_table(model)
    use_cal = bool(ct_method_map().get(model))
    if ctab and use_cal:
        ctab = ctmod.blend_ct(ctab, ct_learn.learned_ct(model))
    if not ctab:
        return
    target_cum = None
    fp = (r["final_process"] or "").strip()
    if fp:
        _ss = ctab.get("spec_start") or {}
        target_cum = _ss.get(fp) or _ss.get(ctmod._strip_suffix(fp)) or None
    # 设备能力修正:尾批工序若当前批次(MES 实时)带设备号且不是 HOLD,
    # 剩余加工量按该设备 UPH 样本估;无样本/无设备保持现状。
    cur_dev_rem_h = None
    try:
        _lots = mes.lookup_all_lots([wo_no])
        _best = None
        for _l in _lots:
            if (_l.get("spec") or "") == r["tail_spec"] and (_l.get("eqpld") or ""):
                try:
                    _rh = float(str(_l.get("run_time") or "0").rstrip("h"))
                except (ValueError, TypeError):
                    continue
                if (_l.get("status") or "") != "HOLD" and _rh >= (_best[0] if _best else 0.0):
                    _best = (_rh, _l)
        if _best:
            import equip
            _fi = equip.finish_info(equip.uph_index(), model, r["tail_spec"],
                                    _best[1].get("eqpld"), _best[1].get("qty"),
                                    _best[0])
            if _fi:
                cur_dev_rem_h = _fi["rem_h"]
    except Exception:
        cur_dev_rem_h = None
    res = ctmod.compute(
        r["tail_spec"], "", ctab,
        plan_end=r["plan_end"], plan_start=r["plan_start"],
        learned=(ct_learn.learned_ct(model) if use_cal else None),
        target_cum=target_cum, resume_date=(rd or None),
        cur_dev_rem_h=cur_dev_rem_h)
    if res.get("matched"):
        conn.execute(
            "UPDATE work_orders SET eta=?, risk_level=?, risk_note=? WHERE wo_no=?",
            (res.get("eta") or "", res.get("risk_level") or "",
             res.get("risk_note") or "", wo_no))


def _apply_governing_hold(conn, wo_no):
    """从 wo_holds 取该单所有 active 挂起,选「恢复日最晚」那条,把它的值同步回 work_orders
    单组缓存字段(resume_date/wait_reason/hold_spec/wait_by/wait_at),供甘特/ETA/需要关注沿用;
    无 active 挂起则清空缓存。随后据该恢复日重算 ETA。"""
    rows = conn.execute(
        "SELECT hold_spec, resume_date, reason, wait_by, wait_at FROM wo_holds "
        "WHERE wo_no=? AND status='active'", (wo_no,)).fetchall()
    gov = None
    for r in rows:
        rd = (r["resume_date"] or "").strip()
        if gov is None or rd > (gov["resume_date"] or ""):
            gov = r
    if gov is not None:
        conn.execute(
            "UPDATE work_orders SET resume_date=?, wait_reason=?, hold_spec=?, wait_by=?, wait_at=? WHERE wo_no=?",
            ((gov["resume_date"] or ""), (gov["reason"] or ""), (gov["hold_spec"] or ""),
             (gov["wait_by"] or ""), (gov["wait_at"] or ""), wo_no))
        _recompute_eta_for_resume(conn, wo_no, (gov["resume_date"] or "").strip())
    else:
        conn.execute(
            "UPDATE work_orders SET resume_date='', wait_reason='', hold_spec='', wait_by='', wait_at='' WHERE wo_no=?",
            (wo_no,))
        _recompute_eta_for_resume(conn, wo_no, None)


def add_wo_hold(wo_no, hold_spec, resume_date, reason, operator="", start_date=""):
    """新增一条挂起(支持同一工单多道工序、先后多次)。每次挂起都新增独立记录,
    同一工序多次挂起全部保留为独立历史,不会互相覆盖。
    随后刷新「最晚恢复日」缓存并重算 ETA。start_date=开始挂起日期(空则默认今天)。"""
    wo_no = (wo_no or "").strip()
    rd = (resume_date or "").strip()
    hspec = (hold_spec or "").strip()
    rsn = (reason or "").strip()
    sd = (start_date or "").strip() or datetime.date.today().strftime("%Y-%m-%d")
    if not wo_no or not rd:
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute(
        "INSERT INTO wo_holds(wo_no,hold_spec,resume_date,reason,start_date,wait_by,wait_at,status,created_at) "
        "VALUES(?,?,?,?,?,?,?, 'active', ?)",
        (wo_no, hspec, rd, rsn, sd, operator or "", now, now))
    _apply_governing_hold(conn, wo_no)
    conn.commit()
    conn.close()
    log_action(operator, "工单挂起·新增",
               f"{wo_no} 工序={hspec or '-'} 开始={sd} 恢复日={rd} 原因={rsn or '-'}")


def edit_wo_hold(hold_id, hold_spec, resume_date, reason, start_date="", operator=""):
    """编辑某一条已有挂起(按 id):可改工序/开始挂起日期/恢复作业日/原因。
    随后刷新「最晚恢复日」缓存并重算 ETA。"""
    rd = (resume_date or "").strip()
    hspec = (hold_spec or "").strip()
    rsn = (reason or "").strip()
    sd = (start_date or "").strip()
    if not rd:
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    row = conn.execute("SELECT wo_no FROM wo_holds WHERE id=?", (hold_id,)).fetchone()
    if not row:
        conn.close()
        return
    wo_no = row["wo_no"]
    conn.execute(
        "UPDATE wo_holds SET hold_spec=?, resume_date=?, reason=?, start_date=?, wait_by=?, wait_at=? WHERE id=?",
        (hspec, rd, rsn, sd, operator or "", now, hold_id))
    _apply_governing_hold(conn, wo_no)
    conn.commit()
    conn.close()
    log_action(operator, "工单挂起·编辑",
               f"{wo_no} 工序={hspec or '-'} 开始={sd or '-'} 恢复日={rd} 原因={rsn or '-'}")


def resolve_wo_hold(hold_id, operator=""):
    """解除某一条挂起(按 id),保留为历史(status=resolved)。刷新缓存并重算 ETA。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    row = conn.execute("SELECT wo_no, hold_spec, resume_date FROM wo_holds WHERE id=?",
                       (hold_id,)).fetchone()
    if not row:
        conn.close()
        return
    wo_no = row["wo_no"]
    conn.execute("UPDATE wo_holds SET status='resolved', resolved_at=?, resolved_by=? WHERE id=?",
                 (now, operator or "", hold_id))
    _apply_governing_hold(conn, wo_no)
    conn.commit()
    conn.close()
    log_action(operator, "工单解除挂起",
               f"{wo_no} 工序={row['hold_spec'] or '-'} 恢复日={row['resume_date'] or '-'}")


def list_wo_holds(wo_no, active_only=True):
    """列出某工单的挂起记录(默认只 active)。表未建时返回空(空库/未初始化容错)。"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM wo_holds WHERE wo_no=?"
    if active_only:
        sql += " AND status='active'"
    sql += " ORDER BY resume_date"
    try:
        out = [dict(r) for r in conn.execute(sql, (wo_no,)).fetchall()]
    except sqlite3.OperationalError:
        out = []
    conn.close()
    return out


def holds_map(active_only=True):
    """一次取全部挂起,返回 {wo_no: [hold,...]},供甘特页一次性挂载,避免逐单查询。
    表未建时返回空 dict(空库/未初始化容错)。"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM wo_holds"
    if active_only:
        sql += " WHERE status='active'"
    sql += " ORDER BY resume_date"
    m = {}
    try:
        for r in conn.execute(sql).fetchall():
            m.setdefault(r["wo_no"], []).append(dict(r))
    except sqlite3.OperationalError:
        pass
    conn.close()
    return m


def set_wait_hold(wo_no, resume_date, reason, hold_spec="", operator="", start_date=""):
    """兼容旧入口:有恢复日 = 新增/更新该工序挂起;恢复日为空 = 解除该工单全部 active 挂起。
    内部统一走多挂起表 wo_holds,并刷新「最晚恢复日」缓存。"""
    rd = (resume_date or "").strip()
    if rd:
        add_wo_hold(wo_no, hold_spec, rd, reason, operator, start_date=start_date)
        return
    # 解除:把该单所有 active 挂起标记为 resolved
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("UPDATE wo_holds SET status='resolved', resolved_at=?, resolved_by=? "
                 "WHERE wo_no=? AND status='active'", (now, operator or "", wo_no))
    _apply_governing_hold(conn, wo_no)
    conn.commit()
    conn.close()
    log_action(operator, "工单解除挂起", f"{wo_no} 全部")


def delete_order(oid):
    conn = get_conn()
    conn.execute("DELETE FROM work_orders WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    _all_lots_cache.clear()   # 删除后清缓存,避免统计页读到已删工单的脏数据


# ---------- 基础数据(产品型号等)管理 ----------
def base_list(kind):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,value FROM base_data WHERE kind=? ORDER BY id", (kind,)).fetchall()]
    conn.close()
    return rows


def base_add(kind, value):
    if not value:
        return
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM base_data WHERE kind=? AND value=?", (kind, value)).fetchone()
    if not exists:
        conn.execute("INSERT INTO base_data(kind,value) VALUES(?,?)", (kind, value))
        conn.commit()
    conn.close()


def base_delete(bid):
    conn = get_conn()
    conn.execute("DELETE FROM base_data WHERE id=?", (bid,))
    conn.commit()
    conn.close()


def mes_product_options():
    """读取 MES 数据库里实际存在的产品型号(PACKAGESIZE),供后台复选框选择。
    返回 [{"name": 型号, "count": 批次数, "checked": 是否已在白名单}, ...](按批次数降序)。
    name 直接来自 MES,勾选即精确匹配,避免手输型号匹配不到。
    """
    import mes
    try:
        prods = mes.list_products()
    except Exception:
        prods = []
    conn = get_conn()
    wl_upper = {(r[0] or "").strip().upper() for r in conn.execute(
        "SELECT value FROM base_data WHERE kind='product'").fetchall()}
    conn.close()
    opts = []
    for name, cnt in prods:
        nm = (name or "").strip()
        if not nm:
            continue
        opts.append({"name": nm, "count": cnt,
                     "checked": nm.upper() in wl_upper})
    return opts


def sync_product_whitelist(selected):
    """按后台勾选的 MES 产品型号更新抓取白名单(base_data kind='product')。
    selected: 本次勾选要抓取的 PACKAGESIZE 列表。
    规则:
      · 勾选但不在白名单 → 新增;
      · 白名单里「精确等于某个 MES 产品型号」却未勾选 → 删除;
      · 与任何 MES 产品都不精确对应的自定义/前缀型号(如手输的 'MB12 / 4in1')→ 保持不动,
        仍由文本框单独管理,避免误删。
    返回 (新增数, 删除数)。
    """
    import mes
    try:
        mes_set = {(p or "").strip().upper() for p, _ in mes.list_products()}
    except Exception:
        mes_set = set()
    sel, seen = [], set()
    for s in selected or []:
        v = (s or "").strip()
        if v and v.upper() not in seen:
            seen.add(v.upper())
            sel.append(v)
    sel_upper = {v.upper() for v in sel}
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        "SELECT id, value FROM base_data WHERE kind='product'").fetchall()]
    cur_upper = {(r["value"] or "").strip().upper() for r in cur}
    added = removed = 0
    for v in sel:
        if v.upper() not in cur_upper:
            conn.execute("INSERT INTO base_data(kind,value) VALUES('product',?)", (v,))
            added += 1
    for r in cur:
        vu = (r["value"] or "").strip().upper()
        if vu in mes_set and vu not in sel_upper:
            conn.execute("DELETE FROM base_data WHERE id=?", (r["id"],))
            removed += 1
    conn.commit()
    conn.close()
    return added, removed


# ---------- 统计分析数据 ----------
def stats_data():
    """返回统计分析页所需的聚合数据。"""
    conn = get_conn()
    # 产品型号分布:只统计未完成工单(与看板/明细口径一致;已完成的归「已完成工单」页,不计入)
    prod_dist = conn.execute(
        "SELECT product, COUNT(*) c FROM work_orders "
        "WHERE COALESCE(progress,0) < 100 AND COALESCE(manual_done,0) = 0 "
        "GROUP BY product ORDER BY c DESC").fetchall()
    # 负责人工作量改为「按 Lot 数」统计(逐工单查 MES,得到每个负责人各状态的 Lot 数)
    wl = owner_lot_workload()
    owners = wl["owners"]
    owner_load = wl["by_status"]
    # 准时完成率:以「已完成工单」(进度≥100 或人工标记完成,与「已完成工单」页同口径)为分母,
    # 按时 = 完成日期(done_at,缺失时按今天兜底)不晚于计划交付日 plan_end。
    DONE = "(COALESCE(progress,0) >= 100 OR COALESCE(manual_done,0) = 1)"
    done = conn.execute(
        f"SELECT COUNT(*) FROM work_orders WHERE {DONE}").fetchone()[0]
    on_time = conn.execute(
        f"SELECT COUNT(*) FROM work_orders WHERE {DONE} "
        "AND plan_end != '' AND plan_end IS NOT NULL "
        "AND date(plan_end) >= date(COALESCE(NULLIF(done_at,''), 'now'))").fetchone()[0]
    rate = round(on_time / done * 100) if done else 0
    conn.close()
    return {
        "prod_dist": [(r[0], r[1]) for r in prod_dist],
        "owners": owners, "owner_load": owner_load,
        "on_time_rate": rate,
        "done_total": done, "on_time_total": on_time,
        "wip": _safe_wip_profile(),
        "urgent_wip": _safe_urgent_wip_profile(),
    }


def _safe_wip_profile():
    """容错包装:WIP 数据出问题(如 MES/CT 异常)时返回空,不影响统计页其它部分。"""
    try:
        return wip_profile()
    except Exception:
        return {"wip_lots": [], "wip_ct": {}, "wip_models": []}


def wip_profile():
    """产品在制 WIP 分布所需数据(供统计页「产品在制WIP」图)。
    返回:
      wip_lots:   全量在制 lot 精简明细 [{base,model,wo_no,product,spec,desc,status,qty,lot_id,run_time,wait_time,hold_time}]
      wip_ct:     {base_model: [{spec,name}...]}  各基础型号的 CT 工序顺序(作 X 轴骨架)
      wip_models: [{model(base), count}]          有在制 lot 的基础型号列表(按 lot 数降序)
    口径:只统计未完成工单(progress<100)的 lot;复用 _collect_all_lots 的全量 lot 缓存,
    与负责人工作量统计共用同一次 CSV 读取(不再单独扫一遍)。"""
    note_map = ignored_lots_note_map()   # {lot_id: note};被忽略的 LOT → 图中灰显 + 展示备注
    # MOVE(本班次流入量)判定:白班 07:30-19:30、夜班 19:30-次日07:30。
    # 某 LOT 在当前工序的停留时长(run+wait+hold)≤ 本班次已过时长 → 视为本班次流入该工序。
    now = datetime.datetime.now()
    _day = now.replace(hour=7, minute=30, second=0, microsecond=0)
    _night = now.replace(hour=19, minute=30, second=0, microsecond=0)
    if _day <= now < _night:
        shift_start = _day
    elif now >= _night:
        shift_start = _night
    else:                                  # 凌晨(00:00-07:30):夜班从昨日 19:30 起
        shift_start = _night - datetime.timedelta(days=1)
    elapsed_h = max(0.0, (now - shift_start).total_seconds() / 3600.0)

    def _ph(s):
        try:
            return float(str(s).replace("h", "").strip())
        except (ValueError, TypeError):
            return 0.0

    out_lots = [{
        "base":  l["base"],
        "model": l["model"],
        "wo_no": l["wo_no"],
        "full_wo": l.get("full_wo", l["wo_no"]),  # MES 真实工单号,用于详情展示
        "product": l["product"],
        "spec":  l["spec"],
        "desc":  l["desc"],
        "status": l["status"],
        "qty":   l["qty"],
        "lot_id": l["lot_id"],
        "eqpld": l.get("eqpld", ""),                 # 当前所在设备号(EQPID),显示为 EQP ID
        "run_time":  l["run_time"],
        "wait_time": l["wait_time"],
        "hold_time": l["hold_time"],
        "hold_reason": l.get("hold_reason", ""),     # HOLD原因(HOLDREASONNAME),WIP下钻弹窗显示
        "txn_comment": l.get("txn_comment", ""),     # MES操作备注(TXNCOMMENTS+HOLDCOMMENT)
        "ignored": 1 if l["lot_id"] in note_map else 0,
        "ignore_note": note_map.get(l["lot_id"], ""),
        "moved": 1 if (_ph(l["run_time"]) + _ph(l["wait_time"]) + _ph(l["hold_time"])) <= elapsed_h else 0,
        "eta_at": "", "eta_rem": "", "eta_basis": "",   # 预计加工完成(群算后回填)
    } for l in _collect_all_lots() if l.get("in_progress")]
    # ETA 群算(含同炉):按 (基础型号, 工序) 分组;炉时=实测加工中位(ct_learn)。
    # 同炉多个 lot 共享同一完成时刻(烘烤/回流等批量站点,数据验证同炉 RUNTIME 一致)。
    import equip as _eqp
    from collections import defaultdict as _dd
    grp = _dd(list)
    for i, l in enumerate(out_lots):
        grp[(l.get("base") or "", l.get("spec") or "")].append(i)
    _uph_idx = None
    _lc_cache = {}
    for (bm, sp), ids in grp.items():
        if not sp or not bm:
            continue
        reps = [{"rid": i, "eqp": out_lots[i].get("eqpld") or "",
                 "st": out_lots[i].get("status") or "",
                 "qty": out_lots[i].get("qty") or 0,
                 "run_h": _ph(out_lots[i].get("run_time"))} for i in ids]
        if not any(r["eqp"] and r["st"] in ("运行中", "等待出站") for r in reps):
            continue
        if _uph_idx is None:
            _uph_idx = _eqp.uph_index()
        if bm not in _lc_cache:
            try:
                import ct_learn
                _lc_cache[bm] = ct_learn.learned_ct(bm)
            except Exception:
                _lc_cache[bm] = {}
        ring = None
        _ri = (_lc_cache.get(bm) or {}).get(sp) or (_lc_cache.get(bm) or {}).get(sp.replace("-retest", ""))
        if _ri:
            ring = (_ri.get("run") or 0) or None
        for rid, e in (_eqp.eta_group(reps, _uph_idx, bm, sp, run_med=ring) or {}).items():
            if e:
                out_lots[rid]["eta_rem"] = round(e["rem_h"], 1)
                out_lots[rid]["eta_basis"] = e["basis"]
                out_lots[rid]["eta_at"] = round(e["at"])
    # 各基础型号的 CT 工序顺序骨架(只取实际有在制 lot 的型号,省数据量)
    bases = {l["base"] for l in out_lots if l["base"]}
    wip_ct = {}
    for bm in bases:
        ctab = get_ct_table(bm)
        if ctab and ctab.get("specs"):
            wip_ct[bm] = [{"spec": s.get("spec", ""),
                           "name": (s.get("stepname", "") or s.get("spec", ""))}
                          for s in ctab["specs"]]
    # 型号(基础型号)列表,按在制 lot 数降序
    from collections import Counter
    cnt = Counter(l["base"] for l in out_lots if l["base"])
    wip_models = [{"model": m, "count": c} for m, c in cnt.most_common()]
    return {"wip_lots": out_lots, "wip_ct": wip_ct, "wip_models": wip_models}


def _safe_urgent_wip_profile():
    """容错包装:加急 WIP 数据出问题时返回空,不影响统计页。"""
    try:
        return urgent_wip_profile()
    except Exception:
        return {"wip_lots": [], "wip_ct": {}, "wip_models": [], "wip_target": {}}


def urgent_wip_profile():
    """加急批次的在制 WIP 分布数据(供统计页「加急Lot」模式),结构同 wip_profile:
    以【批次名】为分组键(base):
      wip_lots:   各加急批次在制 lot 精简明细(只取仍在制的:运行中/等待/HOLD)
      wip_ct:     {批次名: [{spec,name}...]}  该批次产品 CT 工序顺序(X 轴骨架)
      wip_models: [{model(批次名), count}]    有在制 lot 的批次列表
    复用 urgent_batches_with_status 的 MES 实时取数;MOVE 本班次判定与 wip_profile 同口径。"""
    note_map = ignored_lots_note_map()
    now = datetime.datetime.now()
    _day = now.replace(hour=7, minute=30, second=0, microsecond=0)
    _night = now.replace(hour=19, minute=30, second=0, microsecond=0)
    if _day <= now < _night:
        shift_start = _day
    elif now >= _night:
        shift_start = _night
    else:
        shift_start = _night - datetime.timedelta(days=1)
    elapsed_h = max(0.0, (now - shift_start).total_seconds() / 3600.0)

    def _ph(s):
        try:
            return float(str(s).replace("h", "").strip())
        except (ValueError, TypeError):
            return 0.0

    WIP_STATUS = {"运行中", "等待进站", "等待出站", "HOLD"}
    out_lots = []
    wip_ct = {}
    wip_models = []
    wip_target = {}      # 批次名 → 作业到工序(target_spec),供图上画绿色目标线
    for b in urgent_batches_with_status():
        bn = (b.get("batch_name") or "").strip()
        if not bn:
            continue
        wip_target[bn] = (b.get("target_spec") or "").strip()
        model = (b.get("ct_model") or "").strip()
        if not model:
            model, _ = detect_ct_model(b.get("product"))
        ctab = get_ct_table(model)
        if ctab and ctab.get("specs"):
            wip_ct[bn] = [{"spec": s.get("spec", ""),
                           "name": (s.get("stepname", "") or s.get("spec", ""))}
                          for s in ctab["specs"]]
        n = 0
        for l in b.get("lots", []):
            st = (l.get("status") or "").strip()
            if st not in WIP_STATUS or not l.get("spec"):   # 只取仍在制的 lot
                continue
            lid = l.get("lot_id", "")
            held = _ph(l.get("run_time")) + _ph(l.get("wait_time")) + _ph(l.get("hold_time"))
            out_lots.append({
                # base=批次名(图表分组键/X轴骨架/下拉,保持不变);
                # model/wo_no 改为按 MES 实际匹配:型号=该批次产品对应的基础型号(ct_model),
                # 工单号=该 Lot 在 MES 的真实工单号(MFGORDERNAME),不再用批次名占位。
                "base": bn, "model": (model or detect_ct_model(l.get("product"))[0] or l.get("product", "")),
                "wo_no": l.get("wo_no", ""),
                "product": l.get("product", ""),
                "spec": l.get("spec", ""),
                "desc": l.get("spec_name", "") or l.get("spec", ""),
                "status": st, "qty": l.get("qty", 0), "lot_id": lid, "eqpld": l.get("eqpld", ""),
                "run_time": l.get("run_time", "-"),
                "wait_time": l.get("wait_time", "-"),
                "hold_time": l.get("hold_time", "-"),
                "hold_reason": l.get("hold_reason", ""),
                "txn_comment": l.get("txn_comment", ""),
                "ignored": 1 if lid in note_map else 0,
                "ignore_note": note_map.get(lid, ""),
                "moved": 1 if held <= elapsed_h else 0,
            })
            n += 1
        if n:
            wip_models.append({"model": bn, "count": n})
    wip_models.sort(key=lambda m: -m["count"])
    return {"wip_lots": out_lots, "wip_ct": wip_ct, "wip_models": wip_models,
            "wip_target": wip_target}


def gantt_data():
    """返回甘特图所需:每条工单的起止日期与进度,并附带该型号
    CT 表的工序分段(供进度条内按 CT 时长比例画小格)。"""
    import ct as ctmod
    import ct_learn
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT wo_no,product,owner,priority,cur_process,plan_start,plan_end,progress,"
        "status,risk_level,risk_note,head_pct,tail_pct,head_spec,tail_spec,"
        "lot_specs,ignored_specs,eta,final_process,resume_date,wait_reason,hold_spec,"
        "eng_times "
        "FROM work_orders WHERE COALESCE(progress,0) < 100 "
        "AND COALESCE(manual_done,0) = 0 ORDER BY plan_start").fetchall()]
    conn.close()
    # 给每行附 CT 工序段。关键:每段宽度用"下一段累计 − 本段累计"算,
    # 保证段段首尾相接(CT 表里单工序 ct 之和可能 ≠ 累计,直接用 ct 会留透明空隙)。
    ct_cache = {}      # model -> {"segs":..., "total":..., "spec_cum":...}
    ct_method = ct_method_map()    # 各型号 CT 计算方式:仅"按校准后"的型号才 blend
    for r in rows:
        model = mes.base_model(r.get("product"))
        if model not in ct_cache:
            ctab = get_ct_table(model)
            if ctab and ct_method.get(model):
                # 选了「按校准后CT」:用实测工序周期(MES,剔异常等待)覆盖上传 CT
                ctab = ctmod.blend_ct(ctab, ct_learn.learned_ct(model))
            segs = []
            total = (ctab.get("total_ct") if ctab else 0) or 0
            spec_cum = (ctab.get("spec_cum") if ctab else {}) or {}
            spec_start = (ctab.get("spec_start") if ctab else {}) or {}
            specs = []
            if total:
                specs = ctab.get("specs", [])
                for i, sp in enumerate(specs):
                    cum = (sp.get("cum_ct", 0) or 0)
                    # 本段起点 = 上一段累计;终点 = 本段累计
                    prev_cum = (specs[i - 1].get("cum_ct", 0) or 0) if i > 0 else 0
                    start = prev_cum / total if total else 0
                    end = cum / total if total else 0
                    segs.append({
                        "spec": sp.get("spec", ""),
                        "name": sp.get("stepname", "") or sp.get("spec", ""),
                        "cum_ratio": start,            # 段左边 = 上一段累计占比
                        "ratio": max(0, end - start),  # 段宽 = 累计差(段段相接无缝)
                        "ct_h": round(cum - prev_cum, 1),  # 本工序 CT 时长(小时)
                        "cum_h": round(cum, 1),            # 累计到此(小时)
                    })
            ct_cache[model] = {"segs": segs, "total": total,
                               "spec_cum": spec_cum, "spec_start": spec_start,
                               "specs": specs}
        _cm = ct_cache[model]
        r["ct_segs"] = _cm["segs"]
        # 总CT(小时)→ 甘特图据此把工序条长度按 CT 推算(而非拉伸到交期)
        r["total_ct_h"] = _cm["total"]
        # ===== ENG 验证时间:该工单指定的工序覆盖(JSON [{spec,eng_h,note}]) =====
        # 有 ENG 时重建 ct_segs(调整总长+宽),颜色由前端据 eng 标记切换为 teal
        _eng_raw = (r.get("eng_times") or "").strip()
        _eng_map = {}      # {spec: eng_h}
        _eng_notes = {}    # {spec: note}
        if _eng_raw:
            try:
                for e in json.loads(_eng_raw):
                    sp = (e.get("spec") or "").strip()
                    eh = e.get("eng_h") or 0
                    if sp and float(eh) > 0:
                        _eng_map[sp] = float(eh)
                        _eng_notes[sp] = (e.get("note") or "").strip()
            except (ValueError, TypeError):
                pass
        r["adj_total_ct_h"] = _cm["total"]
        if _eng_map and _cm["total"]:
            _specs = _cm.get("specs", [])
            _adj = _cm["total"]
            for sp in _specs:
                sn = sp.get("spec", "")
                if sn in _eng_map:
                    _adj = _adj - (sp.get("ct", 0) or 0) + _eng_map[sn]
            _eng_segs = []
            _ac = 0.0
            for sp in _specs:
                sn = sp.get("spec", "")
                _cth = _eng_map.get(sn, (sp.get("ct", 0) or 0))
                _prv = _ac
                _ac += _cth
                _sg = {
                    "spec": sn,
                    "name": sp.get("stepname", "") or sn,
                    "cum_ratio": _prv / _adj if _adj else 0,
                    "ratio": max(0, (_ac - _prv) / _adj) if _adj else 0,
                    "ct_h": round(_cth, 1),
                    "cum_h": round(_ac, 1),
                }
                if sn in _eng_map:
                    _sg["eng"] = True
                    _sg["eng_note"] = _eng_notes.get(sn, "")
                _eng_segs.append(_sg)
            r["ct_segs"] = _eng_segs
            r["adj_total_ct_h"] = _adj
        # 完成工序(后台手选)在甘特图上的目标占比:「做到该工序即完工」=到达该工序即 100%,
        # 故取该工序【起点累计】(做完上一道的累计)/ 整条CT,与进度计算、系统原 fvi_cutoff 同口径;
        # 该工序及其后工序「不作业·不计入」。未选或匹配不到 → None(整条CT为终点)。
        _fp = (r.get("final_process") or "").strip()
        _tot = _cm["total"]
        _sst = _cm.get("spec_start", {})
        target_ratio = None
        if _fp and _tot:
            _fc = _sst.get(_fp)
            if _fc is None:
                _fc = _sst.get(ctmod._strip_suffix(_fp))
            if _fc:
                target_ratio = _fc / _tot
        r["target_ratio"] = target_ratio
        # 甘特图【显示截止占比】= 完成工序【终点累计】/整条CT。「到这一站即完成」→
        # 看板只画到完成工序为止(含该站),其后工序不显示(不再画斜纹)。
        # 与 target_ratio(起点累计=进度100%口径)区分:disp_ratio 仅控制甘特显示裁切,不动进度/ETA/风险。
        disp_ratio = None
        if _fp and _tot:
            _scum = _cm.get("spec_cum", {})
            _ec = _scum.get(_fp)
            if _ec is None:
                _ec = _scum.get(ctmod._strip_suffix(_fp))
            if _ec:
                disp_ratio = _ec / _tot
        r["disp_ratio"] = disp_ratio
        # 在制工序列表(有 LOT 的 SPECNAME):甘特图据此把首尾间空工序刷浅
        try:
            r["lot_specs"] = json.loads(r.get("lot_specs") or "[]")
        except (ValueError, TypeError):
            r["lot_specs"] = []
        # 整站 LOT 全被忽略的工序:甘特图刷浅粉(残留)
        try:
            r["ignored_specs"] = json.loads(r.get("ignored_specs") or "[]")
        except (ValueError, TypeError):
            r["ignored_specs"] = []
    return rows


def export_rows():
    """导出用:全部工单为 dict 列表。"""
    return all_orders()


# ---------- MES 工单(来自 csv 聚合) ----------
def sync_mes_model(model, agg_orders, mode):
    """把某录入型号聚合好的工单写入 mes_orders(先清该型号旧数据)。
    model: 录入的完整型号(作为存储 key, 存在 prefix 列)
    mode: 'exact'/'prefix' 匹配模式,存入 cur_process 旁的备注无需单列,记在 synced 概况
    """
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM mes_orders WHERE prefix=?", (model,))
    for o in agg_orders:
        conn.execute("""
            INSERT INTO mes_orders
            (wo_no, prefix, product, priority, owner, lot_count, qty, cur_process, status, processes, synced_at, match_mode)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (o["wo_no"], model, o["product"], o["priority"], o["owner"],
              o["lot_count"], o["qty"], o["cur_process"], o["status"],
              json.dumps(o["processes"], ensure_ascii=False), now, mode))
    conn.commit()
    conn.close()
    return len(agg_orders)


def mes_orders(prefix=None, kw=None):
    """查询已同步的 MES 工单。"""
    conn = get_conn()
    sql = "SELECT * FROM mes_orders WHERE 1=1"
    args = []
    if prefix:
        sql += " AND prefix=?"
        args.append(prefix)
    if kw:
        sql += " AND (wo_no LIKE ? OR product LIKE ?)"
        args += [f"%{kw}%", f"%{kw}%"]
    sql += " ORDER BY lot_count DESC"
    rows = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        try:
            d["processes"] = json.loads(d.get("processes") or "[]")
        except (ValueError, TypeError):
            d["processes"] = []
        rows.append(d)
    conn.close()
    return rows


def mes_synced_products():
    """已同步过的录入型号 + 工单数 + 匹配模式 + 最近同步时间。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("""
        SELECT prefix AS model, COUNT(*) wo_count,
               MAX(match_mode) match_mode, MAX(synced_at) synced_at
        FROM mes_orders GROUP BY prefix ORDER BY synced_at DESC
    """).fetchall()]
    conn.close()
    return rows


def model_prefixes():
    """从后台录入的产品型号中提取去重的前两字符前缀(MES 抓取白名单)。
    返回 [(prefix, [该前缀下录入的型号...]), ...]
    """
    conn = get_conn()
    models = [r[0] for r in conn.execute(
        "SELECT value FROM base_data WHERE kind='product' ORDER BY id").fetchall()]
    conn.close()
    groups = {}
    for m in models:
        p = (m or "").strip()[:2].upper()
        if p:
            groups.setdefault(p, []).append(m)
    return sorted(groups.items())


def meta_set(k, v):
    conn = get_conn()
    conn.execute("INSERT INTO app_meta(k,v) VALUES(?,?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    conn.commit()
    conn.close()


def meta_get(k, default=""):
    conn = get_conn()
    r = conn.execute("SELECT v FROM app_meta WHERE k=?", (k,)).fetchone()
    conn.close()
    return r[0] if r else default


# ---------- 后台管理账号 ----------
def list_admin_users():
    """返回所有账号(不含密码字段),按创建顺序。"""
    conn = get_auth_conn()
    rows = conn.execute(
        "SELECT id, username, created_at, role, must_change_pw "
        "FROM admin_users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_admin_user(username):
    """按用户名取单个账号(含 salt/pass_hash),不存在返回 None。"""
    conn = get_auth_conn()
    r = conn.execute("SELECT * FROM admin_users WHERE username=?",
                     (username,)).fetchone()
    conn.close()
    return dict(r) if r else None


def count_admin_users():
    conn = get_auth_conn()
    n = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    conn.close()
    return n


def add_admin_user(username, salt, pass_hash, role="normal", must_change_pw=0):
    conn = get_auth_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO admin_users(username,salt,pass_hash,created_at,role,must_change_pw) "
        "VALUES(?,?,?,?,?,?)",
        (username, salt, pass_hash, now, role, 1 if must_change_pw else 0))
    conn.commit()
    conn.close()


def delete_admin_user(username):
    conn = get_auth_conn()
    conn.execute("DELETE FROM admin_users WHERE username=?", (username,))
    conn.commit()
    conn.close()


def set_admin_password(username, salt, pass_hash):
    conn = get_auth_conn()
    # 改密即视为完成首次设置,清除强制改密标记
    conn.execute("UPDATE admin_users SET salt=?, pass_hash=?, must_change_pw=0 WHERE username=?",
                 (salt, pass_hash, username))
    conn.commit()
    conn.close()


def rename_admin_user(old, new):
    """修改账号的用户名(超管改自己名用)。"""
    conn = get_auth_conn()
    conn.execute("UPDATE admin_users SET username=? WHERE username=?", (new, old))
    conn.commit()
    conn.close()


def set_admin_role(username, role):
    """设置账号角色:'super' 或 'normal'。"""
    conn = get_auth_conn()
    conn.execute("UPDATE admin_users SET role=? WHERE username=?",
                 (role if role in ("super", "normal") else "normal", username))
    conn.commit()
    conn.close()


def count_super_admins():
    conn = get_auth_conn()
    n = conn.execute("SELECT COUNT(*) FROM admin_users WHERE role='super'").fetchone()[0]
    conn.close()
    return n


# ---------- 操作审计日志 ----------
def log_action(username, action, detail=""):
    """记录一条后台操作审计。"""
    conn = get_auth_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO audit_log(ts,username,action,detail) VALUES(?,?,?,?)",
                 (now, username or "", action, detail))
    conn.commit()
    conn.close()


def list_audit(limit=200):
    """返回最近 limit 条审计日志(按时间倒序)。"""
    conn = get_auth_conn()
    rows = conn.execute(
        "SELECT ts, username, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- CT 表存取 ----------
def save_ct_table(model, parsed):
    """保存某型号的 CT 表(parsed = ct.parse_ct_table 结果)。"""
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO ct_tables(model,total_ct,data,uploaded_at) "
                 "VALUES(?,?,?,?) ON CONFLICT(model) DO UPDATE SET "
                 "total_ct=excluded.total_ct, data=excluded.data, uploaded_at=excluded.uploaded_at",
                 (model, parsed["total_ct"], json.dumps(parsed, ensure_ascii=False), now))
    conn.commit()
    conn.close()
    _invalidate_ct_cache()


def save_flow_table(model, parsed):
    """保存某型号的 Flow 流程表(parsed = monitor.parse_flow_table 结果)。"""
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO flow_tables(model,data,uploaded_at) "
                 "VALUES(?,?,?) ON CONFLICT(model) DO UPDATE SET "
                 "data=excluded.data, uploaded_at=excluded.uploaded_at",
                 (model, json.dumps(parsed, ensure_ascii=False), now))
    conn.commit()
    conn.close()


def get_flow_table(model):
    """读某型号的 Flow 流程表(当前类别库),返回 dict 或 None。"""
    conn = get_conn()
    r = conn.execute("SELECT data FROM flow_tables WHERE model=?", (model,)).fetchone()
    conn.close()
    if not r:
        return None
    try:
        return json.loads(r[0])
    except (ValueError, TypeError):
        return None


def delete_ct_table(model):
    """删除某型号已上传的 CT 表。返回 True=确有删除,False=无此型号。
    仅删 ct_tables 行(含计算方式开关);实测学习样本(ct_samples_*)保留,
    以便日后重新上传同型号时沿用历史校准。"""
    conn = get_conn()
    cur = conn.execute("DELETE FROM ct_tables WHERE model=?", (model,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    _invalidate_ct_cache()
    return deleted


def get_ct_table(model):
    # 跨请求缓存(按类别+型号):命中直接返回,免去反复查库与 json 解析。
    # 返回的表仅被只读使用(blend_ct 另建新表),共享安全。
    key = (get_category(), model)
    hit = _ct_table_cache.get(key, _CT_MISS)   # 单次原子读,避免 in+[] 之间被 clear() 的竞态
    if hit is not _CT_MISS:
        return hit
    conn = get_conn()
    r = conn.execute("SELECT data FROM ct_tables WHERE model=?", (model,)).fetchone()
    conn.close()
    if not r:
        _ct_table_cache[key] = None
        return None
    try:
        ctab = json.loads(r[0])
    except (ValueError, TypeError):
        _ct_table_cache[key] = None
        return None
    # 兼容旧表:没有 spec_start 就按 specs 现算(工序起点=做完上一道的累计)
    if ctab and "spec_start" not in ctab and ctab.get("specs"):
        spec_start = {}
        cum = 0.0
        for it in sorted(ctab["specs"], key=lambda x: x.get("seqno", 0)):
            spec_start[it.get("spec", "")] = cum
            cum += (it.get("ct", 0) or 0)
        ctab["spec_start"] = spec_start
    _ct_table_cache[key] = ctab
    return ctab


def list_ct_tables():
    """已上传的 CT 表概况(含 use_calibrated:CT 计算方式开关)。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT model, total_ct, uploaded_at, use_calibrated "
        "FROM ct_tables ORDER BY model").fetchall()]
    conn.close()
    return rows


def health_check():
    """一键体检(后台/导航「🩺 健康检查」页数据):
    - MES 快照:存在性 / 新鲜度(分钟) / 行数 / 最后同步状态
    - 数据泵日志:refresh_mes.log 是否存在 / mtime 新鲜度 / 尾部 3 行(GBK 兼容)
    - 型号配置:白名单型号 × {CT 表? / 设备样本数 / 实测工序数}(缺 CT=红)
    - 存储:两个业务库 + auth.db 体积(MB)
    全部只读,无副作用。"""
    import equip as _eqp
    import os as _os

    def _n(v, d=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    now = _time.time()
    # 1) MES 快照
    csv_ok = _os.path.exists(mes.DEFAULT_CSV)
    csv = {"ok": csv_ok, "age_min": None, "rows": 0,
           "last_at": meta_get("last_sync_at", "尚未刷新"),
           "status": meta_get("last_sync_status", "")}
    if csv_ok:
        csv["age_min"] = max(0, round((now - _os.path.getmtime(mes.DEFAULT_CSV)) / 60))
        try:
            csv["rows"] = sum(1 for _row in mes._load_rows())
        except Exception:
            csv["rows"] = -1
    # 2) 数据泵日志(与应用同目录,GBK 编码,尾部几行展示)
    log = {"exists": False, "age_min": None, "tail": []}
    log_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "refresh_mes.log")
    if _os.path.exists(log_path):
        log["exists"] = True
        log["age_min"] = max(0, round((now - _os.path.getmtime(log_path)) / 60))
        try:
            with open(log_path, "rb") as f:
                raw = f.read()[-2000:] if _os.path.getsize(log_path) > 2000 else f.read()
            for enc in ("gbk", "utf-8", "latin-1"):
                try:
                    txt = raw.decode(enc)
                    break
                except (ValueError, UnicodeDecodeError):
                    continue
            log["tail"] = [l for l in txt.splitlines() if l.strip()][-3:]
        except OSError:
            pass
    # 3) 型号配置:白名单 × CT 表 × 设备能力样本
    #    变体归属:白名单里 'MB12 / 4in1'、'MB12 / 1in1' 等变体按 base_model
    #    归一到 'MB12'——与全局口径一致(变体与基础型号共用 CT 表与样本)。
    ct_models = {r["model"] for r in list_ct_tables()}
    try:
        eqp_models = {m["model"]: m for m in _eqp.models()}
    except Exception:
        eqp_models = {}
    models = []
    for bv in base_list("product"):
        mm = (bv.get("value") or "").strip()
        if not mm:
            continue
        m_key = mes.base_model(mm)
        m_info = eqp_models.get(m_key, {})
        models.append({
            "model": mm,
            "ct": m_key in ct_models,
            "ct_under": (m_key if (m_key != mm and m_key in ct_models) else ""),
            "samples": int(m_info.get("samples", 0) or 0),
            "uph": m_info.get("uph"),
        })
    # 4) 存储
    stores = []
    for fname in ("wip_molding.db", "wip_potting.db", "auth.db"):
        p = _os.path.join(DATA_DIR, fname)
        if _os.path.exists(p):
            stores.append((fname, round(_os.path.getsize(p) / 1048576, 1)))
    return {"csv": csv, "log": log, "models": models, "stores": stores,
            "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def ct_specs_map():
    """{model: [{"spec":SPECNAME, "name":中文工序名}, ...]} 各型号 CT 表的工序流(按 seqno 顺序),
    供后台新建工单的「完成工序」下拉按所选型号联动。下拉显示中文名,提交值用 SPECNAME。"""
    conn = get_conn()
    rows = conn.execute("SELECT model, data FROM ct_tables").fetchall()
    conn.close()
    out = {}
    for model, data in rows:
        try:
            ctab = json.loads(data)
        except (ValueError, TypeError):
            continue
        specs = sorted(ctab.get("specs", []), key=lambda x: x.get("seqno", 0) or 0)
        seen, items = set(), []
        for s in specs:
            sp = (s.get("spec") or "").strip()
            if sp and sp not in seen:
                seen.add(sp)
                items.append({"spec": sp, "name": (s.get("stepname") or "").strip() or sp})
        if items:
            out[model] = items
    return out


def ct_method_map():
    """{model: bool} CT 计算方式开关一次读全(True=按校准后,False=按上传CT),
    供进度/ETA 批量计算时在循环外读一次,避免逐行查库。"""
    conn = get_conn()
    rows = conn.execute("SELECT model, use_calibrated FROM ct_tables").fetchall()
    conn.close()
    return {r[0]: bool(r[1]) for r in rows}


def set_ct_method(model, use_calibrated):
    """设置某型号的 CT 计算方式:True=按校准后CT,False=按上传CT。"""
    conn = get_conn()
    conn.execute("UPDATE ct_tables SET use_calibrated=? WHERE model=?",
                 (1 if use_calibrated else 0, model))
    conn.commit()
    conn.close()
    _invalidate_ct_cache()


def calibrated_total_ct(model):
    """该型号「按校准后CT」的总时长(小时);无 CT 表返回 None。供后台两个天数对比展示。"""
    import ct as ctmod
    import ct_learn
    ctab = get_ct_table(model)
    if not ctab:
        return None
    blended = ctmod.blend_ct(ctab, ct_learn.learned_ct(model))
    return blended.get("total_ct")


# ---------- Owner 表存取 ----------
def _owner_key(tech, spec):
    return f"{(tech or '').strip().upper()}|{(spec or '').strip()}"


def save_owner_table(parsed):
    """保存全局 Owner 表。parsed = owner.parse_owner_table 结果
    {"map": {"tech|spec": {"eng":..,"inline":..}}, "row_count": int}。
    """
    conn = get_conn()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO owner_table(id,data,row_count,uploaded_at) VALUES(1,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET data=excluded.data, "
        "row_count=excluded.row_count, uploaded_at=excluded.uploaded_at",
        (json.dumps(parsed["map"], ensure_ascii=False), parsed["row_count"], now))
    conn.commit()
    conn.close()
    _oc()["map"] = None
    _oc()["spec_idx"] = None


_owner_cache = {}   # category(molding/potting) -> {"map":..., "spec_idx":...}


def _oc():
    """取当前业务类别(塑封/灌封)的 Owner 缓存桶;owner_table 存于各 category 库,
    缓存必须按 category 区分,否则跨库污染。"""
    return _owner_cache.setdefault(
        get_category(), {"map": None, "spec_idx": None})


def get_owner_map():
    if _oc()["map"] is not None:
        return _oc()["map"]
    conn = get_conn()
    r = conn.execute("SELECT data FROM owner_table WHERE id=1").fetchone()
    conn.close()
    try:
        _oc()["map"] = json.loads(r[0]) if r and r[0] else {}
    except (ValueError, TypeError):
        _oc()["map"] = {}
    _oc()["spec_idx"] = None
    return _oc()["map"]


def _owner_spec_index():
    """SPECNAME → 第一条匹配记录 的索引(供回退匹配 O(1) 命中,缓存)。
    键 'TECH|SPEC' 取 '|' 之后的 SPEC 段;保留首条(与原顺序遍历取首条一致)。
    """
    if _oc()["spec_idx"] is not None:
        return _oc()["spec_idx"]
    idx = {}
    for k, v in get_owner_map().items():
        spec = k.split("|", 1)[1] if "|" in k else k
        idx.setdefault(spec, v)
    _oc()["spec_idx"] = idx
    return idx


def owner_table_info():
    conn = get_conn()
    r = conn.execute("SELECT row_count, uploaded_at FROM owner_table WHERE id=1").fetchone()
    conn.close()
    return dict(r) if r else None


def owner_for(model, spec, mes_status):
    """按当前工序(SPECNAME)+ MES 状态决定负责人。
    HOLD → ENG Owner;WaitingMoveOut/WaitingTrackIn/RUNNING → Inline Owner。
    匹配键优先 (型号, 工序),回退到 任意型号下同名工序。匹配不到返回 ""。
    """
    omap = get_owner_map()
    if not omap or not spec:
        return ""
    rec = omap.get(_owner_key(model, spec))
    if not rec:                       # 回退:忽略型号,按 SPECNAME 找第一条(索引 O(1))
        rec = _owner_spec_index().get((spec or "").strip())
    if not rec:
        return ""
    s = (mes_status or "").strip().upper()
    is_hold = ("HOLD" in s)
    return (rec.get("eng") if is_hold else rec.get("inline")) or ""


def run_mes_sync():
    """按后台录入的全部型号,单次扫描 csv 抓取并写库。
    供手动按钮与定时线程共用。返回 (总工单数, 明细字符串) 或抛异常。
    【并发安全】全局锁串行化:自动刷新线程与手动「同步」不会同时写
    ct_samples/*.json(observe 实测样本)与 sqlite,避免样本互相覆盖丢失。
    """
    with _sync_lock_for():
        return _run_mes_sync_impl()


def _run_mes_sync_impl():
    import mes  # 延迟导入避免循环依赖
    conn = get_conn()
    models = [r[0] for r in conn.execute(
        "SELECT value FROM base_data WHERE kind='product' ORDER BY id").fetchall()]
    conn.close()
    if not models:
        meta_set("last_sync_status", "后台未录入产品型号")
        meta_set("last_sync_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return 0, "后台未录入产品型号"
    results = mes.aggregate_many(models)
    total = 0
    details = []
    for m in models:
        agg, mode = results.get(m, ([], "none"))
        n = sync_mes_model(m, agg, mode)
        total += n
        details.append(f"{m}({mode}):{n}")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_set("last_sync_at", now)
    meta_set("last_sync_status", f"成功 · {total} 个工单")
    # 同步:用 MES 数据回填所有手动工单的当前工序/进度/状态
    matched, unmatched = sync_manual_orders()
    # 扫描 Wait/HOLD 超阈值异常,自动生成/解除异常记录
    try:
        scan_exceptions()
    except Exception:
        pass
    # 【CT 准确性 v1.3】用本次快照跨快照定稿实测工序周期(observe 主路,准确性核心)。
    # learned_ct 优先读它,样本不足才退回 scan_all 单快照口径修正估计。
    learned_note = ""
    try:
        import ct_learn
        n_final = ct_learn.observe_snapshot(models=models)
        learned_note = f" · 实测定稿+{n_final}"
    except Exception as e:
        learned_note = f" · 实测学习异常:{e}"
    # 【设备能力】跨快照定稿设备 UPH 样本(批次离开工序 → QTY÷最终RUNTIME)。
    eqp_note = ""
    try:
        import equip
        n_eqp = equip.observe_snapshot(models=models)
        if n_eqp:
            eqp_note = f" · 设备定稿+{n_eqp}"
    except Exception as e:
        eqp_note = f" · 设备样本异常:{e}"
    meta_set("last_sync_status",
             f"成功 · MES{total}单 · 手动工单匹配{matched}/无MES{unmatched}{learned_note}{eqp_note}")
    return total, " ".join(details)


def _mes_to_sys_status(mes_status):
    """MES 状态 → 系统状态(进行中/已完成/已延期/待开始)。"""
    m = (mes_status or "").strip()
    if m in ("运行中", "RUNNING"):
        return "进行中"
    if m in ("已入库", "InWareHouse"):
        return "已完成"
    if m in ("HOLD",):
        return "进行中"   # HOLD 仍算在制
    if m in ("等待进站", "等待出站", "WaitingTrackIn", "WaitingMoveOut"):
        return "进行中"
    return "进行中"


def sync_manual_orders():
    """遍历所有手动工单,按工单号在 MES 查找(取最慢分身),
    用对应型号的 CT 表计算进度% 与风险,回填到工单。
    返回 (匹配数, 无匹配数)。
    进锁串行:避免与自动刷新线程并发写 work_orders(被 ct_method/上传 等裸调时尤甚)。
    """
    with _sync_lock_for():
        return _sync_manual_orders_impl()


def _sync_manual_orders_impl():
    import mes
    import ct as ctmod
    import ct_learn
    conn = get_conn()
    today = datetime.date.today().isoformat()
    _uph_idx = None    # 设备 UPH 索引(懒加载:仅当有工单可用设备校准才查)
    orders = [dict(r) for r in conn.execute(
        "SELECT id, wo_no, product, plan_start, plan_end, progress, done_at, final_process, mes_status, resume_date FROM work_orders").fetchall()]

    # 【性能】一次扫描 CSV 取所有工单的在制信息(替代逐工单各扫一遍整表)
    raw_info = mes.scan_orders([o["wo_no"] for o in orders])
    # 被忽略的残留 LOT:仅当存在忽略记录时,才额外扫一遍取 LOT 级明细
    # (用于判断「整站 LOT 是否全被忽略」→ 从 head/tail/进度 剔除 + 标粉)。
    ignored_by_wo = ignored_lots_by_wo()
    lots_by_wo = {}
    if ignored_by_wo:
        try:
            for l in mes.lookup_all_lots(list(ignored_by_wo.keys())):
                lots_by_wo.setdefault(l["wo_key"], []).append(l)
        except Exception:
            lots_by_wo = {}
    # CT 表 / 实测学习数据 按型号缓存,避免在工单循环里反复开库与读样本
    ct_cache = {}
    learned_cache = {}
    ct_method = ct_method_map()    # 各型号 CT 计算方式:仅"按校准后"的型号才 blend

    def get_ct(model):
        if model not in ct_cache:
            ct_cache[model] = get_ct_table(model)
        return ct_cache[model]

    def get_learned(model):
        if model not in learned_cache:
            try:
                learned_cache[model] = ct_learn.learned_ct(model)
            except Exception:
                learned_cache[model] = {}
        return learned_cache[model]

    matched = unmatched = 0
    for o in orders:
        raw = raw_info.get(o["wo_no"])
        info = mes.sync_lookup(o["wo_no"], info=raw)
        if not info["matched"]:
            # 工单在 MES 已查不到,需区分两种情况(都表现为 not matched):
            #   A 还没下线:从未进过 MES(未同步过 且 进度=0)→ 维持原状(待开始),仅标「无MES数据」。
            #   B 中途销账:之前进过 MES(同步过 已同步·xxx,或 进度>0),现在数据消失
            #              → 视同已完成(产品已离场/销账)。progress=100、status=已完成、记完成日,
            #                自动移出看板、归入「已完成工单」页,纳入完成趋势/准时率统计。
            was_in_mes = (str(o.get("mes_status") or "").startswith("已同步")
                          or (o.get("progress") or 0) > 0)
            if was_in_mes:
                conn.execute(
                    "UPDATE work_orders SET mes_status='无MES数据·已销账', "
                    "status='已完成', progress=100, "
                    "done_at=COALESCE(NULLIF(done_at,''), ?) WHERE id=?",
                    (today, o["id"]))
            else:
                conn.execute(
                    "UPDATE work_orders SET mes_status='无MES数据' WHERE id=?", (o["id"],))
            unmatched += 1
            continue

        # 状态列:按该工单所有 Lot 颗数(QTY)占比最多的状态
        # 例:60颗运行中/30颗等待/10颗HOLD → 显示「运行中」。无 qty 时退化为尾批状态。
        live_status = info.get("qty_status") or info["cur_status"]
        # 用 CT 表算进度+风险:型号取工单的 product(取前段,如 'MB12 / 4in1' → 'MB12')
        model = mes.base_model(o["product"])
        ctab = get_ct(model)
        learned = get_learned(model)
        use_cal = bool(ct_method.get(model))   # 该型号是否「按校准后CT」
        if ctab and use_cal:
            # 选了「按校准后CT」:进度/分段/ETA/FVI 用实测工序周期覆盖上传 CT
            ctab = ctmod.blend_ct(ctab, learned)
        # 截止累计CT:仅由「完成工序」(后台手选)决定——工单只做到该工序即完工。
        # 口径:「做到该工序即完工」=Lot 到达(进入/等待该工序)即视为完成,故取该工序
        # 【起点累计】(spec_start,= 做完上一道工序的累计),与系统原 fvi_cutoff「到FVI即完成」一致;
        # 到达该工序进度即 100%,该工序及其后工序不计入剩余作业。未选则按整条 CT。
        # (已取消 05 工单到 FVI 自动截断:既然可手选完成工序,无需再按 FVI 特判。)
        target_cum = None
        final_proc = (o.get("final_process") or "").strip()
        if final_proc and ctab:
            fc = (ctab.get("spec_start") or {}).get(final_proc)
            if fc is None:   # 原样未命中→去 -retest 等后缀再试
                fc = (ctab.get("spec_start") or {}).get(ctmod._strip_suffix(final_proc))
            if fc:
                target_cum = fc
        progress = 0
        risk_level = risk_note = eta = ""
        head_pct = tail_pct = None
        head_spec = tail_spec = None
        real_specs = list(dict.fromkeys(info.get("real_specs") or []))
        # 各工序「当前已运行小时」(RUNTIME):产品已在该工序 Run 的时间。
        # 取该工序内运行最久的 Lot(代表该工序已推进多少),供 compute 做工序内进度插值,
        # 让「已在 Run 的时间」也计入进度(否则进度只停在当前工序起点,不随作业推进)。
        spec_run_h = {}
        spec_lot = {}    # spec -> 运行最久 lot 的 {eqp, qty, run_h, status}(设备校准 ETA 用)
        for v in info.get("variants", []):
            for p in v.get("processes", []):
                sp_name = (p.get("spec") or "").strip()
                if not sp_name:
                    continue
                best_h = 0.0
                best_lot = None
                for lot in p.get("lot_list", []):
                    rt = str(lot.get("run_time") or "").strip().rstrip("h")
                    try:
                        h = float(rt)
                    except (ValueError, TypeError):
                        continue
                    if h > best_h:
                        best_h = h
                        best_lot = lot
                if best_h > spec_run_h.get(sp_name, 0.0):
                    spec_run_h[sp_name] = best_h
                if best_lot is not None and best_lot.get("eqpld"):
                    spec_lot[sp_name] = {"eqp": best_lot.get("eqpld"),
                                         "qty": best_lot.get("qty"),
                                         "run_h": best_h,
                                         "status": best_lot.get("status", "")}
        # 残留 LOT 忽略:找出「该工序的 LOT 全被忽略」的工序(整站全忽略才算)。
        # 这些工序从 head/tail/进度 计算中剔除(不算尾批),并标记为粉色残留站。
        fully_ignored = set()
        ig = ignored_by_wo.get(o["wo_no"])
        if ig:
            spec_total, spec_ign = {}, {}
            for l in lots_by_wo.get(o["wo_no"], []):
                sp = l["spec"]
                spec_total[sp] = spec_total.get(sp, 0) + 1
                if l["lot_id"] in ig:
                    spec_ign[sp] = spec_ign.get(sp, 0) + 1
            fully_ignored = {sp for sp, tot in spec_total.items()
                             if tot > 0 and spec_ign.get(sp, 0) >= tot}
        # 有效在制工序(剔除整站全忽略的)→ 甘特图深蓝;空工序浅蓝;全忽略站浅粉。
        valid_specs = [s for s in real_specs if s not in fully_ignored]
        # 当前在制颗数:按 LOT 粒度合计良品颗数。三类不计入:
        #   1) 报废站/不良品/测试后虚拟站等非良品工序(spec_is_virtual 已含「虚拟站」「送报废仓」,
        #      另兜底排除命名未带虚拟站的报废/不良站);
        #   2) 被人工忽略的 LOT(=视同报废,该 LOT 所有数据含数量一律不计);
        #   3) 整站 LOT 全被忽略的残留工序(其 LOT 均在忽略集合内,自动排除)。
        ig_ids = ignored_by_wo.get(o["wo_no"]) or set()
        cur_qty = 0
        for v in info.get("variants", []):
            for p in v.get("processes", []):
                sp = (p.get("spec") or "").strip()
                dsc = p.get("desc") or ""
                if not sp:
                    continue
                if mes.spec_is_virtual(sp, dsc):
                    continue
                if any(k in dsc for k in ("报废", "不良", "废品")):
                    continue
                for lot in p.get("lot_list", []):
                    if lot.get("lot_id") in ig_ids:   # 被忽略=报废,不计入
                        continue
                    try:
                        cur_qty += int(float(lot.get("qty") or 0))
                    except (ValueError, TypeError):
                        pass
        lot_specs_json = json.dumps(valid_specs, ensure_ascii=False)
        ignored_specs_json = json.dumps(sorted(fully_ignored), ensure_ascii=False)
        # 无有效在制工序(valid_specs 为空)时,判定整单是否已完成。两种情形都算完成:
        #   情形①「全忽略」:本有真实在制工序,但其 Lot 已被管理员全部忽略(视同报废/不作业),
        #            残站可能是 Die Bank / FVI / 报废站等普通站(非虚拟站)→ 整单已无有效在制 → 完成。
        #   情形②「全虚拟」:剩余 Lot 全停在虚拟/暂存站,真实作业已完成的 Lot 已入库离开 MES:
        #            · 残留站并非全是「建批首站」开工前占位 → 视为整单已完成(进度100,移出看板);
        #            · 若全是建批首站(尚未开工) → 维持原状,不误判为完成。
        all_virtual_done = False
        if not valid_specs and info.get("matched"):
            if real_specs:
                # 情形①:real_specs 非空而 valid_specs 为空 ⟺ 全部真实在制工序均整站被忽略。
                all_virtual_done = True
                progress = 100
                live_status = "已完成"
            else:
                # 情形②:无真实在制工序,剩余 Lot 全停在虚拟/暂存站。
                residuals = [(p.get("spec", ""), p.get("desc", ""))
                             for v in info.get("variants", [])
                             for p in v.get("processes", [])
                             if mes.spec_is_virtual(p.get("spec"), p.get("desc"))]
                if residuals and not all(mes.spec_is_start_virtual(sp, ds)
                                         for sp, ds in residuals):
                    all_virtual_done = True
                    progress = 100
                    live_status = "已完成"
        if ctab and not all_virtual_done:
            # 按 CT 累计位置(SPECNAME 匹配)给每个真实分身算进度%,
            # 首批=最大(走得最远),尾批=最小(最慢)。不用 MES seq。
            # 去重工序名,减少重复计算。
            # 注:head_pct/tail_pct 仍按整条 CT 折算(甘特图按工序段定位用),
            #     验证单的「到 FVI 折算」只作用于整单进度/风险/ETA。
            specs = valid_specs or [info.get("slowest_spec", "")]
            pairs = []   # (pct, spec);第一轮只取进度(不解析日期)
            for sp in specs:
                r = ctmod.compute(sp, "", ctab, cur_run_h=spec_run_h.get(sp))
                if r["matched"]:
                    pairs.append((r["progress"], sp))
            if pairs:
                head_pair = max(pairs, key=lambda p: p[0])   # 首批:走得最远
                tail_pair = min(pairs, key=lambda p: p[0])   # 尾批:最慢
                head_pct, head_spec = head_pair               # 首批进度% + 实际工序
                tail_pct, tail_spec = tail_pair               # 尾批进度% + 实际工序
                # 风险/进度/ETA 按真正的尾批工序(pct 最小那个)算:
                # 计划开始日(相对富余度阈值)+ 截止点(验证单到 FVI)。
                # 关键:剩余时间/ETA 是否回灌实测CT,跟随 CT 计算方式——
                #   「按校准后」传 learned(剩余用实测含等待);
                #   「按上传CT」传 None(剩余退回上传 CT),否则两种方式 ETA 不变。
                # 设备能力修正:尾批工序当前批次的剩余加工量按该设备 UPH 样本估
                #   (HOLD 批次时长冻结不可作准、无样本时保持现状)。
                cur_dev_rem_h = None
                _slt = spec_lot.get(tail_spec)
                if _slt and (_slt.get("status") or "") != "HOLD":
                    try:
                        _rh = float(str(_slt.get("run_h") or "0").strip())
                    except (ValueError, TypeError):
                        _rh = 0.0
                    if _uph_idx is None:
                        import equip
                        _uph_idx = equip.uph_index()
                    _fi = equip.finish_info(_uph_idx, model, tail_spec, _slt.get("eqpld"),
                                            _slt.get("qty"), _rh)
                    if _fi:
                        cur_dev_rem_h = _fi["rem_h"]
                rslow = ctmod.compute(tail_spec, "", ctab,
                                      plan_end=o["plan_end"], plan_start=o.get("plan_start"),
                                      learned=(learned if use_cal else None),
                                      target_cum=target_cum,
                                      cur_run_h=spec_run_h.get(tail_spec),
                                      resume_date=(o.get("resume_date") or None),
                                      cur_dev_rem_h=cur_dev_rem_h)
                if rslow["matched"]:
                    progress = rslow["progress"]   # 整单进度(验证单到 FVI 即 100%)
                    risk_level = rslow["risk_level"]
                    risk_note = rslow["risk_note"]
                    eta = rslow.get("eta") or ""
                else:
                    progress = tail_pct
        # 宏观负责人:按「占比最多状态所在工序」+ 该状态决定(与状态列口径一致)
        owner_spec = info.get("qty_status_spec") or tail_spec or info.get("slowest_spec", "")
        new_owner = owner_for(model, owner_spec, live_status)
        # 完成日期:工单进度达 100% 且尚未记过完成日时,记下当天(收到已完成的日期);
        # 已有完成日期则不覆盖。兼顾历史已完成但缺完成日的旧单——下次同步即补记。
        # 「已完成工单」页完工日与统计分析(完成趋势/准时完成率)同口径,均以此为准。
        done_at_set = today if (progress >= 100 and not o.get("done_at")) else ""
        conn.execute("""
            UPDATE work_orders
            SET cur_process=?, status=?, progress=?, mes_status=?,
                owner=COALESCE(NULLIF(?, ''), owner),
                risk_level=COALESCE(NULLIF(?, ''), risk_level),
                risk_note=COALESCE(NULLIF(?, ''), risk_note),
                head_pct=?, tail_pct=?, head_spec=?, tail_spec=?,
                lot_specs=?, ignored_specs=?,
                eta=COALESCE(NULLIF(?, ''), eta), is_verify=0,
                done_at=COALESCE(NULLIF(?, ''), done_at),
                cur_qty=?
            WHERE id=?
        """, (info["cur_process"], live_status, progress,
              f'已同步·{info["cur_status"]}', new_owner, risk_level, risk_note,
              head_pct, tail_pct, head_spec, tail_spec,
              lot_specs_json, ignored_specs_json,
              eta, done_at_set, cur_qty, o["id"]))
        matched += 1
        # 注:CT 实测校准已改为「MES 全量扫描」(ct_learn.scan_all),
        # 由 learned_ct 实时读取,无需在此逐单累积样本。
    conn.commit()
    conn.close()
    return matched, unmatched


# ======================================================================
# WIP 超期分析:产品型号(PACKAGESIZE)→ 塑封/灌封 归属
# ----------------------------------------------------------------------
# 顶层分析页跨两个类别库,归属配置放在 DATA_DIR/wip_series_map.json:
#   · 首次没有配置文件时,自动读取两个类别库 base_data(kind='product') 白名单;
#   · 同一型号同时出现在两库白名单 → 冲突,归「未分类」,交管理员在页面裁决;
#   · 管理员在页面保存后,JSON 为准(空串 = 显式未分类),白名单只作缺失项的兜底。
# ======================================================================

SERIES_MAP_PATH = os.path.join(DATA_DIR, "wip_series_map.json")
_series_map_lock = threading.Lock()
_SERIES_VALUES = ("molding", "potting", "")


def _series_map_whitelist():
    """读两个类别库的产品白名单,返回 (映射, 冲突键集合)。不创建/不修改任何库。"""
    found = {}
    conflicts = set()
    for cat in CATEGORIES:
        p = wip_path(cat)
        if not os.path.exists(p):
            continue
        try:
            conn = sqlite3.connect(p, timeout=5)
            rows = conn.execute(
                "SELECT value FROM base_data WHERE kind='product'").fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        for (v,) in rows:
            key = (v or "").strip().upper()
            if not key:
                continue
            if key in found and found[key] != cat:
                conflicts.add(key)
            else:
                found[key] = cat
    for k in conflicts:
        found.pop(k, None)
    return found, conflicts


def _series_map_read_file():
    try:
        with open(SERIES_MAP_PATH, encoding="utf-8") as f:
            d = json.load(f)
        raw = d.get("map") if isinstance(d, dict) else {}
        out = {}
        for k, v in (raw or {}).items():
            key = (k or "").strip().upper()
            val = (v or "").strip()
            if key and val in _SERIES_VALUES:
                out[key] = val
        return out, d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}, {}


def series_map_load():
    """返回 (生效映射, 元信息)。生效映射 = 白名单兜底 + JSON 覆盖。"""
    with _series_map_lock:
        wl, conflicts = _series_map_whitelist()
        owner_series = _owner_series_map()
        manual, raw_meta = _series_map_read_file()
    merged = dict(wl)
    merged.update(owner_series)   # Owner 表的「封装类型」补全系列
    # 手工选择塑封/灌封优先级最高;手工「未分类」不覆盖 Owner/白名单已有归属,
    # 否则导入 Owner 表后,之前保存过的空值会把封装类型全部压回未分类。
    for k, v in manual.items():
        if v:
            merged[k] = v
        elif k not in merged:
            merged[k] = ""
    hidden = {str(x).strip().upper() for x in (raw_meta.get("hidden") or [])
              if str(x).strip()}
    try:
        rev = int(raw_meta.get("rev") or 0)
    except (ValueError, TypeError):
        rev = 0
    return merged, {
        "source": "file" if manual else ("owner" if owner_series else "whitelist"),
        "json_exists": os.path.exists(SERIES_MAP_PATH),
        "updated_at": raw_meta.get("updated_at", ""),
        "updated_by": raw_meta.get("updated_by", ""),
        "manual_count": len(manual),
        "owner_series_count": len(owner_series),
        "hidden_count": len(hidden),
        "rev": rev,
        "conflicts": sorted(k for k in conflicts
                            if not manual.get(k) and k not in owner_series),
    }


def series_map_effective():
    """分析页只关心映射本身。"""
    return series_map_load()[0]


def series_map_save(raw_map, user="", hidden=None, expected_rev=None):
    """管理员保存归属。raw_map: {型号: 'molding'/'potting'/''}。
    hidden: 不参与 WIP 超期分析的产品型号(PACKAGESIZE)列表;
            传 None 表示保持原有隐藏列表不变。
    expected_rev: 页面打开时读到的配置版本;不一致说明别的管理员已改过,
                  拒绝覆盖。返回保存条数。"""
    if not isinstance(raw_map, dict):
        raise ValueError("map 必须是对象")
    clean = {}
    for k, v in raw_map.items():
        key = str(k or "").strip().upper()[:100]
        val = str(v or "").strip()
        if not key:
            continue
        if val not in _SERIES_VALUES:
            raise ValueError(f"非法归属值: {val!r}")
        clean[key] = val

    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SERIES_MAP_PATH + ".tmp"
    with _series_map_lock:
        _, old = _series_map_read_file()
        try:
            current_rev = int(old.get("rev") or 0)
        except (ValueError, TypeError):
            current_rev = 0
        if expected_rev is not None:
            try:
                exp = int(expected_rev)
            except (ValueError, TypeError):
                exp = -1
            if exp != current_rev:
                raise ValueError("配置已被其他管理员修改，请关闭后重新打开再保存")
        if hidden is None:
            hidden = old.get("hidden") or []
        clean_hidden = []
        seen_hidden = set()
        for x in (hidden or []):
            key = str(x or "").strip().upper()[:100]
            if key and key not in seen_hidden:
                seen_hidden.add(key)
                clean_hidden.append(key)
        payload = {
            "version": 1,
            "rev": current_rev + 1,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_by": user or "",
            "map": clean,
            "hidden": clean_hidden,
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SERIES_MAP_PATH)
    return len(clean)


def series_map_rev():
    _, raw = _series_map_read_file()
    try:
        return int(raw.get("rev") or 0)
    except (ValueError, TypeError):
        return 0


def series_hidden_products():
    """返回被管理员取消勾选、不参与 WIP 分析的产品型号集合(大写)。"""
    _, raw = _series_map_read_file()
    return {str(x).strip().upper() for x in (raw.get("hidden") or [])
            if str(x).strip()}


def series_map_panel():
    """管理员维护面板数据:全部 MES 型号(含在制 LOT 数)+ 已有归属。"""
    try:
        counts = dict(mes.list_products())
    except Exception:
        counts = {}
    mapping, meta = series_map_load()
    hidden_set = series_hidden_products()
    rows, seen = [], set()
    for name, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        key = name.strip().upper()
        if not key:
            continue
        seen.add(key)
        rows.append({"product": name, "count": cnt,
                     "series": mapping.get(key, ""),
                     "mapped": key in mapping,
                     "conflict": key in meta["conflicts"],
                     "hidden": key in hidden_set})
    # 配置文件里有、但当前 MES 里已不存在的型号也列出来,便于清理
    for key in sorted(set(mapping) - seen):
        rows.append({"product": key, "count": 0,
                     "series": mapping.get(key, ""), "mapped": True,
                     "conflict": False, "hidden": key in hidden_set})
    out = dict(meta)
    out.update({"ok": True, "rows": rows, "total": len(rows)})
    return out


# ======================================================================
# WIP 超期分析:三类 Owner 表(Assy PC / MPC / Inline)
# ----------------------------------------------------------------------
# 全局配置放 DATA_DIR/wip_owner_map.json,与类别库解耦;
# 关联:所有 Owner 以 PACKAGESIZE(细分项)为锚点;
#       Assy/MPC 直接按 PACKAGESIZE,Inline 通过 MPC 的 PACKAGESIZE→TECHNOLOGY
#       桥再按 SPECNAME 匹配(HOLD 取 ENG)。
# ======================================================================

OWNER_MAP_PATH = os.path.join(DATA_DIR, "wip_owner_map.json")
_owner_map_lock = threading.Lock()
_wip_owner_cache = {"ver": object(), "data": None}
_wip_owner_lock = threading.Lock()


def _owner_map_read_file():
    try:
        with open(OWNER_MAP_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def owner_map_version():
    """文件指纹;内容变化后让 mes 的分析缓存失效。"""
    try:
        st = os.stat(OWNER_MAP_PATH)
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return ""


def owner_map_info():
    """导入状态(供页面显示)。"""
    d = _owner_map_read_file()
    try:
        rev = int(d.get("rev") or 0)
    except (ValueError, TypeError):
        rev = 0
    return {
        "rev": rev,
        "updated_at": d.get("updated_at", ""),
        "updated_by": d.get("updated_by", ""),
        "sources": d.get("sources") or {},
        "counts": {
            "assy": len(d.get("assy") or {}),
            "mpc": len(d.get("mpc") or {}),
            "inline": len(d.get("inline") or {}),
        },
        "conflicts": d.get("conflicts") or {},
    }


def build_owner_lookup(raw=None):
    """把 JSON 原始映射整理成逐 LOT 查找结构。纯函数,便于测试。

    所有 Owner 统一以细分项(PACKAGESIZE)为锚点:
      · Assy PC Owner:按 PACKAGESIZE;
      · MPC Owner:按 PACKAGESIZE;
      · Inline/ENG Owner:用 MPC 表的 PACKAGESIZE→TECHNOLOGY 桥,
        再按 PACKAGESIZE+SPECNAME 查。
    旧版 Assy 表是按 Product 键存的,这里用记录里的 package 字段归一化;
    不再按 Product 匹配,也不再按 SPECNAME 单独猜测 Inline。
    """
    raw = raw if isinstance(raw, dict) else _owner_map_read_file()

    # Assy:旧数据可能是按 Product 键存的;优先用记录里的 package 重建。
    assy = {}
    for k, v in (raw.get("assy") or {}).items():
        rec = v if isinstance(v, dict) else {}
        key = (rec.get("package") or k or "").strip().upper()
        if key:
            assy[key] = rec

    mpc = {(k or "").strip().upper(): v
           for k, v in (raw.get("mpc") or {}).items() if k}
    inline = {(k or "").strip().upper(): v
              for k, v in (raw.get("inline") or {}).items() if k}

    tech_by_pkg = {}
    for pkg, rec in mpc.items():
        if not isinstance(rec, dict):
            continue
        tech = (rec.get("technology") or "").strip().upper()
        if tech:
            tech_by_pkg[pkg] = tech

    # Inline:把 TECHNOLOGY+SPECNAME 展开成 PACKAGESIZE+SPECNAME
    inline_by_pkg = {}
    for key, rec in inline.items():
        if not isinstance(rec, dict):
            continue
        tech = (rec.get("technology") or
                (key.split("|", 1)[0] if "|" in key else "")).strip().upper()
        spec = (rec.get("spec") or
                (key.split("|", 1)[1] if "|" in key else key)).strip().upper()
        if not tech or not spec:
            continue
        for pkg, pkg_tech in tech_by_pkg.items():
            if pkg_tech == tech:
                inline_by_pkg[pkg + "|" + spec] = rec

    return {"assy": assy, "mpc": mpc, "inline": inline,
            "tech_by_pkg": tech_by_pkg, "inline_by_pkg": inline_by_pkg}


def owner_map_lookup():
    """带 mtime 缓存的查找结构。"""
    ver = owner_map_version()
    with _wip_owner_lock:
        if _wip_owner_cache["ver"] == ver and _wip_owner_cache["data"] is not None:
            return _wip_owner_cache["data"]
        data = build_owner_lookup()
        _wip_owner_cache["ver"] = ver
        _wip_owner_cache["data"] = data
        return data


def _owner_series_map():
    """从 MPC Owner 表的封装类型提取 PACKAGESIZE→塑封/灌封。"""
    raw = _owner_map_read_file()
    out = {}
    for pkg, rec in (raw.get("mpc") or {}).items():
        ser = (rec.get("series") or "").strip()
        if ser in CATEGORIES:
            out[(pkg or "").strip().upper()] = ser
    return out


def owner_map_import(parsed, user="", filename="", expected_rev=None):
    """保存 Owner 导入结果。parsed = importer.parse_owner_workbook 的返回。
    同一类型整体覆盖;空表不覆盖旧数据。
    expected_rev: 页面打开时读到的版本;不一致说明别的管理员已改过,拒绝覆盖。"""
    types = parsed.get("types") or {}
    if not types:
        raise ValueError("没有识别到 Owner 表（需要 Assy PC Owner / MPC Owner / "
                         "Inline Owner 任一表头）")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    warnings = list(parsed.get("warnings") or [])
    with _owner_map_lock:
        raw = _owner_map_read_file()
        try:
            current_rev = int(raw.get("rev") or 0)
        except (ValueError, TypeError):
            current_rev = 0
        if expected_rev is not None:
            try:
                exp = int(expected_rev)
            except (ValueError, TypeError):
                exp = -1
            if exp != current_rev:
                raise ValueError("Owner 表配置已被其他管理员修改，请关闭后重新打开再导入")
        sources = dict(raw.get("sources") or {})
        imported, conflicts = {}, dict(raw.get("conflicts") or {})
        for kind in ("assy", "mpc", "inline"):
            p = types.get(kind)
            if not p:
                continue
            rows = p.get("map") or {}
            if not rows:
                warnings.append(f"{kind} 表没有有效数据行，保留旧数据")
                continue
            raw[kind] = rows
            sources[kind] = {
                "file": filename,
                "rows": p.get("row_count", len(rows)),
                "input_rows": p.get("input_rows", 0),
                "skipped": p.get("skipped", 0),
                "conflicts": p.get("conflicts", 0),
                "imported_at": now,
            }
            imported[kind] = len(rows)
            conflicts[kind] = p.get("conflicts", 0)
        if not imported:
            raise ValueError("识别到的 Owner 表没有有效数据行，已取消导入")
        raw.update({
            "version": 1,
            "rev": current_rev + 1,
            "updated_at": now,
            "updated_by": user or "",
            "sources": sources,
            "conflicts": conflicts,
        })
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = OWNER_MAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, OWNER_MAP_PATH)
    with _wip_owner_lock:
        _wip_owner_cache["ver"] = object()
        _wip_owner_cache["data"] = None
    return {
        "imported": imported,
        "conflicts": {k: types[k].get("conflicts", 0) for k in imported},
        "warnings": warnings,
        "updated_at": now,
        "owner_info": owner_map_info(),
    }


if __name__ == "__main__":
    init_db()
    print("DB initialized at", DB_PATH)
    print("Orders:", len(all_orders()))
    print("Filter options:", {k: len(v) for k, v in filter_options().items()})
