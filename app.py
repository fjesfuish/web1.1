 # -*- coding: utf-8 -*-
"""WIP 工单生产进度管理系统 —— 主入口。
纯标准库实现。运行: python3 app.py  然后访问 http://localhost:8000
"""
import http.server
import socketserver
import urllib.parse
import json
import io
import csv
import os
import time
import sqlite3
import threading

import db
import views
import importer
import mes
import auth

PORT = int(os.environ.get("PORT", "8000") or "8000")

CAT_COOKIE = "wip_cat"          # 记录用户选择的类别(塑封/灌封)的 cookie 名
CAT_COOKIE_AGE = 180 * 24 * 3600

# ===== WIP 导出并发保护 + 排队状态 =====
# 同时最多 2 个导出;其余请求登记 ticket 后等待。页面右下角轮询
# /wip_analysis/export_status?ticket=... 显示名次和预计等待时间。
_EXPORT_CONCURRENCY = 2
_EXPORT_SEM = threading.BoundedSemaphore(_EXPORT_CONCURRENCY)
_export_q_lock = threading.Lock()
_export_q = {}            # ticket -> {state, kind, enqueued, started, finished, ok}
_export_q_order = []      # 活跃 ticket 的到达顺序(waiting/running)
_export_avg_sec = {"csv": 1.5, "xlsx": 8.0}


def _export_ticket(v):
    v = (v or "").strip()
    if not v or len(v) > 48:
        return ""
    return "".join(ch for ch in v if ch.isalnum() or ch in "-_")


def _export_register(ticket, kind):
    if not ticket:
        return
    with _export_q_lock:
        rec = _export_q.get(ticket)
        if rec and rec.get("state") in ("waiting", "running"):
            return
        _export_q[ticket] = {"state": "waiting", "kind": kind,
                             "enqueued": time.monotonic()}
        if ticket in _export_q_order:
            _export_q_order.remove(ticket)
        _export_q_order.append(ticket)


def _export_mark_running(ticket):
    if not ticket:
        return
    with _export_q_lock:
        rec = _export_q.get(ticket)
        if rec:
            rec["state"] = "running"
            rec["started"] = time.monotonic()


def _export_finish(ticket, ok, elapsed=None):
    if not ticket:
        return
    with _export_q_lock:
        rec = _export_q.get(ticket)
        if not rec:
            return
        rec["state"] = "done" if ok else "failed"
        rec["finished"] = time.monotonic()
        rec["ok"] = bool(ok)
        if ticket in _export_q_order:
            _export_q_order.remove(ticket)
        if ok and elapsed and elapsed > 0:
            kind = rec.get("kind") or "xlsx"
            old = _export_avg_sec.get(kind, 8.0)
            _export_avg_sec[kind] = max(0.5, old * 0.8 + float(elapsed) * 0.2)


def _export_status(ticket):
    now = time.monotonic()
    with _export_q_lock:
        # 清理 60 秒前已完成的记录,避免字典无限增长
        for t, r in list(_export_q.items()):
            if r.get("state") in ("done", "failed") and \
                    now - (r.get("finished") or now) > 60:
                _export_q.pop(t, None)
                if t in _export_q_order:
                    _export_q_order.remove(t)
        running = sum(1 for x in _export_q.values() if x.get("state") == "running")
        waiting = sum(1 for x in _export_q.values() if x.get("state") == "waiting")
        rec = _export_q.get(ticket) if ticket else None
        if not rec:
            return {"ok": True, "ticket": ticket, "state": "unknown",
                    "position": 0, "ahead": 0, "eta_seconds": 0,
                    "running": running, "waiting": waiting}
        state = rec.get("state")
        ahead = 0
        position = 0
        eta = 0
        avg = _export_avg_sec.get(rec.get("kind"), 8.0)
        if state == "waiting":
            try:
                ahead = _export_q_order.index(ticket)
            except ValueError:
                ahead = 0
            waves = (ahead + _EXPORT_CONCURRENCY - 1) // _EXPORT_CONCURRENCY
            eta = max(1, int(round(waves * avg))) if ahead > 0 else 1
            position = ahead + 1
        elif state == "running":
            eta = max(1, int(round(avg)))
        return {"ok": True, "ticket": ticket, "state": state,
                "position": position, "ahead": ahead, "eta_seconds": eta,
                "running": running, "waiting": waiting}

# 导入时的列名映射:表头里出现这些词 -> 工单字段
COL_MAP = {
    "工单号": "wo_no", "wo_no": "wo_no", "工单": "wo_no",
    "产品型号": "product", "product": "product", "型号": "product",
    "客户": "customer", "customer": "customer",
    "状态": "status", "status": "status",
    "优先级": "priority", "priority": "priority",
    "计划开始": "plan_start", "计划开始日期": "plan_start", "plan_start": "plan_start",
    "计划交付": "plan_end", "计划交付日期": "plan_end", "plan_end": "plan_end",
    "负责人": "owner", "owner": "owner",
    "当前工序": "cur_process", "工序": "cur_process", "cur_process": "cur_process",
    "进度": "progress", "progress": "progress",
    "风险等级": "risk_level", "风险": "risk_level", "risk_level": "risk_level",
    "风险提示": "risk_note", "risk_note": "risk_note",
}


def _xlsx_bytes(headers, rows, sheet_name="WIP", widths=None):
    """用标准库生成最小可用 .xlsx(内联字符串,不依赖 openpyxl/pandas)。
    headers: 表头;rows: 二维列表;数字写成数值单元格,其余写字符串。
    56k 行导出是热点:转义/列号都做了预编译与缓存,避免逐字符 Python 循环。"""
    import zipfile
    import re as _re

    _ctrl = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

    def _esc(v):
        s = str(v if v is not None else "")
        if len(s) > 32000:
            s = s[:32000]
        # 绝大多数单元格没有特殊字符:先做 C 层扫描,命中才分配新串
        if _ctrl.search(s):
            s = _ctrl.sub("", s)
        if "&" not in s and "<" not in s and ">" not in s and '"' not in s:
            return s
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    def _col(n):
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    ncol = len(headers)
    col_letters = [_col(i + 1) for i in range(ncol)]
    is_num = (int, float)

    def _cell(ref, v, style=0):
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, is_num):
            return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'
        return (f'<c r="{ref}" s="{style}" t="inlineStr"><is>'
                f'<t xml:space="preserve">{_esc(v)}</t></is></c>')

    widths = widths or [16, 14, 12, 10, 18, 12, 10, 18, 22, 10, 10, 10, 10,
                        12, 14, 14, 16, 10, 24]
    cols = "".join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
                   for i, w in enumerate(widths))
    out = []
    out.append('<row r="1">' + "".join(
        _cell(f"{col_letters[i]}1", h, 1) for i, h in enumerate(headers)) + "</row>")
    for ri, row in enumerate(rows, start=2):
        cells = []
        for ci, v in enumerate(row):
            cells.append(_cell(f"{col_letters[ci]}{ri}", v))
        out.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
    last = len(rows) + 1
    dim = f'<dimension ref="A1:{col_letters[-1] if col_letters else "A"}{last}"/>'
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'{dim}'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<cols>{cols}</cols><sheetData>' + "".join(out) + '</sheetData>'
        f'<autoFilter ref="A1:{col_letters[-1] if col_letters else "A"}{last}"/>'
        '</worksheet>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{_esc(sheet_name)[:31]}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>')
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>')
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="10"/><name val="Calibri"/></font>'
        '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/>'
        '<bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" '
        'applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    MAX_UPLOAD = 20 * 1024 * 1024   # 上传文件大小上限(20MB),防止超大请求占满内存

    def _send(self, content, status=200, ctype="text/html; charset=utf-8", extra=None):
        data = content.encode("utf-8") if isinstance(content, str) else content
        # gzip 压缩:文本类大页面(如甘特图 ~530KB)且客户端支持时,压到约 1/8,
        # 大幅减小传输与客户端解析开销(浏览器透明解压)。下载类(csv)不压。
        enc = None
        ae = self.headers.get("Accept-Encoding", "") or ""
        if ("gzip" in ae and len(data) > 1024
                and any(t in ctype for t in ("html", "json", "javascript"))):
            import gzip as _gz
            data = _gz.compress(data, 5)
            enc = "gzip"
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            # 动态页面禁缓存:内网浏览器/代理会缓存 GET 页面,导致"保存后跳回看到旧数据"
            # (如计划开始日改完又变回去)。所有页面都是实时生成,统一 no-store 最稳。
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            if enc:
                self.send_header("Content-Encoding", enc)
            self.send_header("Content-Length", str(len(data)))
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass   # 客户端提前断开连接,忽略

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _redirect_cookie(self, location, set_cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def _is_authed(self):
        token = auth.parse_cookie(self.headers.get("Cookie", ""))
        return auth.valid_session(token)

    def _current_user(self):
        """当前登录的账号名(未登录返回空串)。"""
        token = auth.parse_cookie(self.headers.get("Cookie", ""))
        return auth.session_user(token)

    def _require_admin(self):
        """未登录则跳转登录页,返回 False;已登录返回 True。"""
        if self._is_authed():
            return True
        self._redirect("/login")
        return False

    def _current_role(self):
        """当前登录账号角色:'super' / 'normal';未登录返回 ''。"""
        u = self._current_user()
        return auth.user_role(u) if u else ""

    def _cat_cookie(self):
        """从 cookie 取用户选择的类别(molding/potting);无效/未选返回空串。"""
        raw = auth.cookie_value(self.headers.get("Cookie", ""), CAT_COOKIE)
        return raw if raw in db.CATEGORIES else ""

    def _apply_category(self):
        """按 cookie 设定本请求线程的业务类别(无则默认 molding)。返回所选类别(可能为空)。"""
        cat = self._cat_cookie()
        db.set_category(cat or "molding")
        return cat

    def _is_super(self):
        return self._is_authed() and auth.is_super(self._current_user())

    def _require_super(self):
        """需超级管理员:未登录跳登录;已登录但非超管跳回后台并提示。"""
        if not self._is_authed():
            self._redirect("/login")
            return False
        if not auth.is_super(self._current_user()):
            self._redirect("/admin?msg=" + urllib.parse.quote("需要超级管理员权限"))
            return False
        return True

    def _wip_params(self, qs):
        """解析 WIP 超期分析页的查询参数(容错,非法值回退默认)。"""
        def _int(name, default, lo, hi):
            try:
                v = int((qs.get(name, [""])[0] or "").strip())
            except (ValueError, TypeError):
                v = default
            return max(lo, min(v, hi))

        days = _int("days", 60, 1, 3650)
        raw = (qs.get("edges", ["15,30,60,120"])[0] or "").replace("，", ",")
        edges = set()
        for part in raw.replace(" ", ",").split(","):
            try:
                e = int(part.strip())
            except (ValueError, TypeError):
                continue
            if 0 < e <= 3650:
                edges.add(e)
        series = (qs.get("series", [""])[0] or "").strip()
        if series not in ("molding", "potting", "unknown"):
            series = ""
        bucket = None
        raw_b = (qs.get("bucket", [""])[0] or "").strip()
        if raw_b:
            try:
                bucket = int(raw_b)
            except (ValueError, TypeError):
                bucket = None
            if bucket is not None and not (0 <= bucket <= 20):
                bucket = None
        return {
            "days": days,
            "edges": sorted(edges)[:8],
            "series": series,
            "product": (qs.get("product", [""])[0] or "").strip()[:80],
            "spec": (qs.get("spec", [""])[0] or "").strip()[:120],
            "family": (qs.get("family", [""])[0] or "").strip()[:120],
            "aowner": (qs.get("aowner", [""])[0] or "").strip()[:80],
            "mowner": (qs.get("mowner", [""])[0] or "").strip()[:80],
            "iowner": (qs.get("iowner", [""])[0] or "").strip()[:80],
            "bucket": bucket,
            "kw": (qs.get("kw", [""])[0] or "").strip()[:80],
            "page": _int("page", 1, 1, 100000),
            "size": _int("size", 100, 10, 500),
        }

    def _safe_next(self, v):
        """登录后的本地跳转目标:只允许站内绝对路径,防开放重定向。"""
        v = (v or "").strip()
        if (v.startswith("/") and not v.startswith("//")
                and "\\" not in v and "\r" not in v and "\n" not in v
                and len(v) <= 300):
            return v
        return ""

    def _handle_500(self):
        """视图未捕获异常的兜底:记录堆栈并回 500,避免客户端拿到空白断连。"""
        import traceback
        traceback.print_exc()
        try:
            self._send("服务器内部错误,请稍后重试或联系管理员。",
                       status=500, ctype="text/plain; charset=utf-8")
        except Exception:
            pass

    def _recalc_async(self):
        """把「全量重算」丢到后台线程后立即返回(不阻塞 HTTP 响应)。

        写库本身很快,慢的是随后的 db.sync_manual_orders() —— 它要抢全局同步锁
        (_sync_lock_for),而该锁可能被后台自动刷新线程持有数秒~数十秒。若在请求
        里串行等待,浏览器会先超时报「服务器错误」,可数据其实已经写进去了 ——
        用户刷新页面才发现「刚才明明成功了」。v2.25.10 修「忽略残留LOT」时首次
        采用此法,此处统一收口给后台各写操作共用。

        重算失败不影响已写入的数据;下一轮自动同步(每 3 分钟)会兜底再算一次。
        """
        def _run():
            try:
                db.sync_manual_orders()
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def do_GET(self):
        try:
            self._dispatch_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._handle_500()

    def _dispatch_get(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.strip("/") or "stats"   # 默认首页:统计分析
        qs = urllib.parse.parse_qs(parsed.query)

        # ===== 类别中间件:按 cookie 设定本请求业务类别(塑封/灌封) =====
        cat = self._apply_category()
        if route == "select":
            # 类别选择页(公开:匿名也能选要看哪个看板);背景神经网络复用 MES 聚合数据
            self._send(views.render_select(db.CATEGORIES, db.CATEGORY_NAMES,
                                           current=cat, msg=qs.get("msg", [""])[0],
                                           stats=db.login_pulse_stats()))
            return
        if route == "select/set":
            pick = qs.get("cat", [""])[0]
            if pick in db.CATEGORIES:
                # 选完类别后落地到「统计分析」页(原为 /gantt 生产进度看板)
                self._redirect_cookie(
                    "/stats",
                    set_cookie=f"{CAT_COOKIE}={pick}; Path=/; "
                               f"Max-Age={CAT_COOKIE_AGE}; SameSite=Lax")
            else:
                self._redirect("/select")
            return
        # ===== WIP 超期分析(顶层独立模块,公开浏览) =====
        # 说明:本页跨塑封/灌封两个业务库,不依赖类别 cookie,必须放在
        # 「未选类别 → /select」中间件之前。
        if route == "wip_guide":
            # 使用指南(静态页,公开;与 WIP 分析页同目录)
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "wip_guide.html")
            try:
                with open(p, encoding="utf-8") as f:
                    page = f.read()
            except OSError:
                self._send("使用指南文件缺失：wip_guide.html", status=404,
                           ctype="text/plain; charset=utf-8")
                return
            self._send(page)
            return
        if route == "wip_analysis":
            self._send(views.render_wip_analysis(self._is_authed()))
            return
        if route == "wip_analysis/data":
            p = self._wip_params(qs)
            try:
                data = mes.wip_overdue_analysis(
                    threshold_days=p["days"], bucket_edges=p["edges"],
                    series_map=db.series_map_effective(),
                    series=p["series"], product=p["product"], keyword=p["kw"],
                    bucket=p["bucket"], spec=p["spec"], family=p["family"],
                    aowner=p["aowner"], mowner=p["mowner"], iowner=p["iowner"],
                    owner_map=db.owner_map_lookup(),
                    owner_ver=db.owner_map_version(),
                    hidden=db.series_hidden_products(),
                    page=p["page"], page_size=p["size"])
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
                return
            self._send(json.dumps(data, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
            return
        if route == "wip_analysis/export_status":
            ticket = _export_ticket(qs.get("ticket", [""])[0])
            self._send(json.dumps(_export_status(ticket), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
            return
        if route in ("wip_analysis/export_csv", "wip_analysis/export_xlsx"):
            kind = "csv" if route.endswith("export_csv") else "xlsx"
            ticket = _export_ticket(qs.get("ticket", [""])[0])
            _export_register(ticket, kind)
            if not _EXPORT_SEM.acquire(timeout=120):
                _export_finish(ticket, False)
                self._send("导出排队时间过长，请稍后重试", status=503,
                           ctype="text/plain; charset=utf-8")
                return
            _export_mark_running(ticket)
            started = time.monotonic()
            ok = False
            try:
                p = self._wip_params(qs)
                try:
                    headers, rows = mes.wip_overdue_export(
                        threshold_days=p["days"], bucket_edges=p["edges"],
                        series_map=db.series_map_effective(),
                        series=p["series"], product=p["product"], keyword=p["kw"],
                        bucket=p["bucket"], spec=p["spec"], family=p["family"],
                        aowner=p["aowner"], mowner=p["mowner"], iowner=p["iowner"],
                        owner_map=db.owner_map_lookup(),
                        owner_ver=db.owner_map_version(),
                        hidden=db.series_hidden_products())
                except Exception as e:
                    self._send("导出失败: " + str(e), status=500,
                               ctype="text/plain; charset=utf-8")
                    return
                stamp = time.strftime("%Y%m%d")
                if kind == "csv":
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(headers)
                    w.writerows(rows)
                    self._send("﻿" + buf.getvalue(),
                               ctype="text/csv; charset=utf-8",
                               extra={"Content-Disposition":
                                      f"attachment; filename=wip_overdue_{p['days']}d_{stamp}.csv"})
                else:
                    data = _xlsx_bytes(headers, rows, sheet_name="WIP超期分析")
                    self._send(data,
                               ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               extra={"Content-Disposition":
                                      f"attachment; filename=wip_overdue_{p['days']}d_{stamp}.xlsx"})
                ok = True
            finally:
                _export_finish(ticket, ok, time.monotonic() - started)
                _EXPORT_SEM.release()
            return
        if route == "wip_analysis/map":
            # 管理员维护面板数据(未登录回 JSON 401,便于前端提示)
            if not self._is_authed():
                self._send(json.dumps({"ok": False, "msg": "未登录"},
                                      ensure_ascii=False), status=401,
                           ctype="application/json; charset=utf-8")
                return
            payload = db.series_map_panel()
            payload["owners"] = db.owner_map_info()
            self._send(json.dumps(payload, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
            return
        # 敏感接口统一收进登录墙:数据导出/明细查询(内网匿名可扒全套数据)。
        # 注意:/refresh 不再需要登录(业务要求:匿名也可手动触发同步,与看板浏览一致)。
        if route in ("export/json", "export/csv", "exceptions/export",
                     "ct_ai/export", "admin/lookup_mes"):
            if not self._is_authed():
                self._redirect("/login")
                return
        if route == "refresh":
            # 与后台「数据源→立即刷新」同一实现(当前类别抓取+状态记录+反馈文案)
            ok, msg = self._do_mes_sync()
            self._send(json.dumps({"ok": ok, "msg": msg}),
                       ctype="application/json; charset=utf-8")
            return
        if route == "sync_ts":
            # 轻量只读接口:回报「最后一次同步时间」,供页面判断数据是否已更新。
            # 不触发抓取、不碰数据库写锁,可高频轮询(页面默认 60 秒问一次)。
            # 有它之后页面就不必盲目定时重载:只有数据真的变了才刷。
            # 读库失败就回空串(前端当作"未知",不会误判为有新数据)。
            try:
                _at = db.meta_get("last_sync_at", "")
            except Exception:
                _at = ""
            self._send(json.dumps({"at": _at}, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
            return
        # 其余内容页必须先选类别;仅登录/登出页例外
        if not cat and route not in ("login", "logout"):
            self._redirect("/select")
            return

        if route == "admin/audit":
            if not self._require_super():
                return
            self._send(views.render_audit(db.list_audit(300), self._current_user()))
            return

        if route == "dashboard":
            # 仪表盘已并入排产看板(甘特图),重定向
            self._redirect("/gantt")
        elif route == "orders":
            filters = {k: qs.get(k, [""])[0]
                       for k in ("status", "product", "owner", "priority", "risk_level", "demander", "kw")}
            self._send(views.render_orders(db.all_orders(filters, exclude_done=True),
                                           db.filter_options(), filters,
                                           self._is_authed()))
        elif route == "gantt":
            self._send(views.render_gantt(db.gantt_data(), db.dashboard_stats(),
                                          self._is_authed(),
                                          attention=db.attention_orders()))
        elif route == "stats":
            self._send(views.render_stats(db.stats_data(), self._is_authed()))
        elif route == "help":
            self._send(views.render_help())
        elif route == "done":
            self._send(views.render_done(db.done_orders(), self._is_authed()))
        elif route == "owner_orders":
            owner = qs.get("owner", [""])[0]
            self._send(json.dumps(db.orders_by_owner(owner), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "owner_load":
            # 统计页搜索联动:按关键词重算「负责人工作量统计」块(与 WIP 图同口径)
            q = qs.get("q", [""])[0].strip()
            wl = db.owner_lot_workload(q or None)
            self._send(json.dumps({"html": views.owner_load_html(wl["owners"], wl["by_status"],
                                                                 empty_msg="无匹配该关键词的在制 Lot",
                                                                 empty_sub="换个关键词试试")},
                                  ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "owner_lots":
            owner = qs.get("owner", [""])[0]
            status = qs.get("status", [""])[0]
            self._send(json.dumps(db.lots_of_owner(owner, status), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "model_orders":
            # 统计页「产品型号分布」点柱下钻:该型号(精确匹配 product)的在制工单
            model = qs.get("model", [""])[0]
            ords = db.all_orders({"product": model}, exclude_done=True)
            keep = ("wo_no", "product", "status", "priority", "demander",
                    "plan_start", "plan_end", "cur_process", "owner", "progress",
                    "risk_level", "risk_note")
            out = [{k: (o.get(k) if o.get(k) is not None else "") for k in keep} for o in ords]
            self._send(json.dumps(out, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "seg_lots":
            # 甘特图点击色块:取该工单该工序下的 LOT 明细(含是否已忽略)
            wo = qs.get("wo", [""])[0]
            spec = qs.get("spec", [""])[0]
            self._send(json.dumps(db.lots_of_wo_spec(wo, spec), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "ct_ai":
            import ct_learn
            msg = qs.get("msg", [""])[0]
            # 仅展示已上传 CT 表的型号(与学习范围一致)
            ct_models = [t["model"] for t in db.list_ct_tables()]
            summ = []
            for m in ct_models:
                rows = ct_learn.summary(m)
                # SEQNO/排序一律以「上传的 CT 表」为准(实测样本里的 MES 工序号
                # 与上传 SEQNO 不是同一套,不可靠)。按 SPECNAME 从上传 CT 表取真实
                # SEQNO 与中文描述(STEPNAME),再按上传 SEQNO 升序排列;CT 表里没有
                # 的工序(实测多出来的)SEQNO 置空、排到最后。
                ctab = db.get_ct_table(m)
                specmap = {}
                if ctab:
                    for s in ctab.get("specs", []):
                        specmap[s.get("spec", "")] = (
                            s.get("seqno"), s.get("stepname", "") or "")
                seen = set()
                for r in rows:
                    sq, desc = specmap.get(r["spec"], (None, ""))
                    r["ct_seqno"] = sq
                    r["ct_desc"] = desc
                    seen.add(r["spec"])
                # 上传的 CT 框架里【尚未采到实测样本】的工序也补成占位行,
                # 使整张工序骨架按 SEQNO 顺序完整呈现(否则没数据的工序在页面上看不到)。
                for sp, (sq, desc) in specmap.items():
                    if sp in seen:
                        continue
                    rows.append({
                        "seq": sq, "spec": sp, "samples": 0,
                        "median_h": 0.0, "run_median_h": 0.0, "wait_median_h": 0.0,
                        "reliable": False, "confidence": 0, "concentration": 0,
                        "cv": 0, "src": "none", "ct_seqno": sq, "ct_desc": desc,
                    })
                rows.sort(key=lambda r: (r.get("ct_seqno") is None,
                                         r.get("ct_seqno") if r.get("ct_seqno") is not None else 0))
                summ.append({"model": m, "rows": rows,
                             "total_samples": sum(r["samples"] for r in rows)})
            self._send(views.render_ct_ai(summ, msg))
        elif route == "monitor":
            # 工序监控页(所有登录用户可见):直接读 MES 快照,按型号×OWNERNAME×工单前缀跟踪在制
            self._send(views.render_monitor())
        elif route == "health":
            # 一键体检(需登录,只读)
            if not self._is_authed():
                self._redirect("/login")
                return
            self._send(views.render_health(db.health_check()))
        elif route == "swr":
            # SWR 要求查询页(浏览类,匿名可用;数据=公盘 PIE 投放的 PDF,只读)
            self._send(views.render_swr())
        elif route == "swr/data":
            import swr
            kw = qs.get("kw", [""])[0]
            files = swr.search(kw) if swr.configured() else []
            self._send(json.dumps({"ok": True, "configured": swr.configured(),
                                   "files": files}, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "swr/file":
            import swr
            p = swr.resolve(qs.get("p", [""])[0])
            if not p:
                self._send("404 Not Found", status=404, ctype="text/plain; charset=utf-8")
                return
            try:
                with open(p, "rb") as fh:
                    data = fh.read(64 * 1024 * 1024)   # 上限 64MB/文件
            except OSError:
                self._send("404 Not Found", status=404, ctype="text/plain; charset=utf-8")
                return
            self._send(data, ctype="application/pdf")
        elif route == "monitor/data":
            import monitor
            try:
                # owners:参数存在(即使为空串)= 显式空集合(一个都不匹配);参数缺失 = 全部。
                # 注意 parse_qs 默认丢弃空值参数(owners= → 键消失),故用原始 query 判定
                if "owners=" in parsed.query:
                    owners = [x.strip() for x in qs.get("owners", [""])[0].split(",") if x.strip()]
                else:
                    owners = None
                if "products=" in parsed.query:
                    products = [x.strip() for x in qs.get("products", [""])[0].split(",") if x.strip()]
                else:
                    products = None
                models = [x.strip() for x in qs.get("models", [""])[0].split(",") if x.strip()]
                kw = qs.get("kw", [""])[0].strip()
                prefix = qs.get("prefix", [""])[0].strip()
                full = "full" in qs
                # 工序骨架(单选型号时):优先 Flow 流程表(监控页上传),其次 CT 表;
                # 监控页不分类别(读全量 CSV),两个类别库(塑封/灌封)里任一个有就用
                skel = None
                if len(models) == 1:
                    for _tbl in ("flow_tables", "ct_tables"):
                        for _cat in db.CATEGORIES:
                            try:
                                _conn = sqlite3.connect(db.wip_path(_cat), timeout=5)
                                _r = _conn.execute(
                                    f"SELECT data FROM {_tbl} WHERE model=?",
                                    (models[0],)).fetchone()
                                _conn.close()
                            except Exception:
                                continue
                            if not _r:
                                continue
                            try:
                                _tab = json.loads(_r[0])
                            except (ValueError, TypeError):
                                continue
                            if _tab and _tab.get("specs"):
                                skel = [{"spec": s.get("spec", ""),
                                         "name": s.get("stepname", "") or s.get("spec", "")}
                                        for s in _tab["specs"] if s.get("spec")]
                                break
                        if skel:
                            break
                data = monitor.profile(owners=owners, models=models or None,
                                       kw=kw or None, prefix=prefix or None,
                                       products=products, full=full, ct_skel=skel)
                self._send(json.dumps(data, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}),
                           ctype="application/json; charset=utf-8")
        elif route == "monitor/match":
            # 搜索自动切型号:输入可识别为 型号/产品型号/工单号 → 返回对应型号
            import monitor
            try:
                kw = qs.get("kw", [""])[0].strip()
                model = monitor.match_model(kw) if kw else None
                self._send(json.dumps({"model": model}, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}),
                           ctype="application/json; charset=utf-8")
        elif route == "monitor/lot":
            # 完整 Lot 号精确查找:命中返回该 lot 所在型号/工序,前端自动跳转并高亮
            import monitor
            try:
                lot = qs.get("lot", [""])[0].strip()
                info = monitor.lot_lookup(lot) if lot else None
                self._send(json.dumps({"ok": bool(info), "lot": info}, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}),
                           ctype="application/json; charset=utf-8")
        elif route == "monitor/options":
            # 筛选候选联动:OWNERNAME + 产品型号(PRODUCTNAME)列表。
            # owners/products 参数为当前已选,作为互相约束(空=不限,避免死锁)
            import monitor
            try:
                models = [x.strip() for x in qs.get("models", [""])[0].split(",") if x.strip()]
                owners = [x.strip() for x in qs.get("owners", [""])[0].split(",") if x.strip()]
                products = [x.strip() for x in qs.get("products", [""])[0].split(",") if x.strip()]
                data = monitor.options(models=models or None,
                                       owners=owners or None, products=products or None)
                self._send(json.dumps(data, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}),
                           ctype="application/json; charset=utf-8")
        elif route == "monitor/lots":
            import monitor
            try:
                spec = qs.get("spec", [""])[0].strip()
                owners = [x.strip() for x in qs.get("owners", [""])[0].split(",") if x.strip()]
                models = [x.strip() for x in qs.get("models", [""])[0].split(",") if x.strip()]
                kw = qs.get("kw", [""])[0].strip()
                prefix = qs.get("prefix", [""])[0].strip()
                if "products=" in parsed.query:
                    products = [x.strip() for x in qs.get("products", [""])[0].split(",") if x.strip()]
                else:
                    products = None
                if not spec:
                    data = {"ok": False, "msg": "缺少 spec 参数"}
                else:
                    data = monitor.lots_of_spec(spec, owners=owners or None,
                                                models=models or None, kw=kw or None,
                                                prefix=prefix or None, products=products)
                self._send(json.dumps(data, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}),
                           ctype="application/json; charset=utf-8")
        elif route == "equip":
            # 设备能力统计页(所有登录用户可见)
            self._send(views.render_equip())
        elif route == "equip/data":
            import equip
            try:
                if "eqp" in qs:
                    data = {"matches": equip.search_eqp(qs.get("eqp", [""])[0].strip())}
                elif "models" in qs:
                    data = {"models": equip.models()}
                else:
                    model = qs.get("model", [""])[0].strip()
                    if not model:
                        data = {"ok": False, "msg": "缺少 model 参数"}
                    elif "products" in qs:
                        data = {"products": equip.products_of(model)}
                    elif "spec" in qs:
                        spec = qs.get("spec", [""])[0].strip()
                        product = qs.get("product", [""])[0].strip() or None
                        data = {"eqps": equip.eqps_of(model, spec, product)}
                    else:
                        product = qs.get("product", [""])[0].strip() or None
                        specs = equip.specs_of(model, product)
                        # CT 骨架补全(与 CT 校准页同款):上传 CT 表里尚未采到
                        # 样本的工序也补成占位行,并按上传 SEQNO 排序——否则工序
                        # 清单缺行、序号与上传表对不上,易误判"顺序乱了"。
                        ctab = db.get_ct_table(model)
                        if ctab:
                            specmap = {}
                            for s in ctab.get("specs", []):
                                specmap[(s.get("spec") or "").strip()] = s
                            seen = {s["spec"] for s in specs}
                            for sp, s in specmap.items():
                                if sp in seen:
                                    continue
                                specs.append({"spec": sp,
                                              "seq": s.get("seqno"),
                                              "desc": (s.get("stepname") or ""),
                                              "eqp_cnt": 0, "samples": 0,
                                              "uph": None})
                            # 序号列统一 = 上传 CT 表 SEQNO(有样本行原先显示的是
                            # MES 序号,与上传表对不上,看起来就会"乱序")
                            for sp, s in specmap.items():
                                for x in specs:
                                    if x["spec"] == sp:
                                        x["seq"] = s.get("seqno")
                                        if not x.get("desc") and s.get("stepname"):
                                            x["desc"] = s.get("stepname")
                                        break
                            specs.sort(key=lambda x: (
                                specmap.get(x["spec"])["seqno"]
                                if x["spec"] in specmap else 999999,
                                x["spec"]))
                        data = {"specs": specs}
                self._send(json.dumps(data, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)}),
                           ctype="application/json; charset=utf-8")
        elif route == "ct_ai/export":
            import ct_learn
            model = qs.get("model", [""])[0]
            csv_text = ct_learn.export_csv(model)
            fname = f"CT_actual_{model}.csv"
            # utf-8-sig 让 Excel 正确识别中文
            self._send("\ufeff" + csv_text,
                       ctype="text/csv; charset=utf-8",
                       extra={"Content-Disposition": f"attachment; filename={fname}"})
        elif route == "admin/check_wo":
            if not self._require_admin():
                return
            wo = qs.get("wo", [""])[0]
            self._send(json.dumps({"exists": db.wo_exists(wo)}),
                       ctype="application/json; charset=utf-8")
        elif route == "data":
            if not self._require_admin():
                return
            msg = qs.get("msg", [""])[0]
            csv_ok = os.path.exists(mes.DEFAULT_CSV)
            self._send(views.render_data(
                db.all_orders(),
                db.model_prefixes(),
                db.mes_orders(),
                db.mes_synced_products(),
                msg, csv_ok,
                last_sync_at=db.meta_get("last_sync_at", "尚未刷新"),
                last_sync_status=db.meta_get("last_sync_status", "")))
        elif route == "login":
            nxt = self._safe_next(qs.get("next", [""])[0])
            if self._is_authed():
                self._redirect(nxt or "/admin")
                return
            msg = qs.get("msg", [""])[0]
            self._send(views.render_login(msg, db.login_pulse_stats(),
                                          next_path=nxt))
        elif route == "logout":
            token = auth.parse_cookie(self.headers.get("Cookie", ""))
            auth.drop_session(token)
            self._redirect_cookie(
                "/login", set_cookie=f"{auth.COOKIE_NAME}=; Path=/; Max-Age=0")
        elif route == "admin":
            if not self._require_admin():
                return
            edit_id = self._safe_int(qs.get("edit", [None])[0])
            edit_order = db.get_order(edit_id) if edit_id is not None else None
            msg = qs.get("msg", [""])[0]
            # CT 表附「按校准后CT」总时长,供后台两个天数并排对比
            ct_tables = db.list_ct_tables()
            for t in ct_tables:
                t["calibrated_total"] = db.calibrated_total_ct(t["model"])
            self._send(views.render_admin(db.all_orders(), db.base_list("product"),
                                          edit_order, msg, ct_tables,
                                          db.model_prefixes(),
                                          db.mes_orders(),
                                          db.mes_synced_products(),
                                          os.path.exists(mes.DEFAULT_CSV),
                                          db.meta_get("last_sync_at", "尚未刷新"),
                                          db.meta_get("last_sync_status", ""),
                                          db.owner_table_info(),
                                          auth.list_users(),
                                          self._current_user(),
                                          self._current_role(),
                                          *db.get_exc_thresholds(),
                                          exc_models=db.base_list("product"),
                                          exc_rows=db.list_exc_thresholds(),
                                          urgent_batches=db.list_urgent_batches(),
                                          exc_wo_rows=db.list_wo_thresholds(),
                                          mes_products=db.mes_product_options(),
                                          ct_specs=db.ct_specs_map()))
        elif route == "exceptions":
            self._send(views.render_exceptions(
                db.list_exception_wos(), self._is_authed(), *db.get_exc_thresholds()))
        elif route == "exceptions_of_wo":
            wo = qs.get("wo", [""])[0]
            self._send(json.dumps(db.exceptions_of_wo(wo), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "urgent_detail":
            bid = self._safe_int(qs.get("batch", [None])[0])
            res = db.urgent_batches_with_status(only_batch=bid) if bid is not None else []
            self._send(json.dumps(res[0] if res else {}, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "urgent_gantt":
            self._send(views.render_urgent_gantt(db.urgent_batches_with_status()))
        elif route == "ct_models":
            # 加急建单「型号」下拉:数据来自已上传的 CT 文件型号(如 MB6/MB12)
            self._send(json.dumps([t["model"] for t in db.list_ct_tables()],
                                  ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "wo_specs":
            # 异常工单阈值:按工单号取其产品对应的 CT 工序(供工序多选)
            wo = qs.get("wo", [""])[0]
            self._send(json.dumps(db.wo_ct_specs(wo), ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "ct_specs":
            model = qs.get("model", [""])[0]
            # 归一到基础封装型号:MB12/4in1、MB12/1in1 等子项目沿用 MB12 的 CT 工序
            model = mes.base_model(model)
            ctab = db.get_ct_table(model) or {}
            specs = [{"spec": s.get("spec", ""),
                      "name": s.get("stepname", "") or s.get("spec", "")}
                     for s in ctab.get("specs", [])]
            self._send(json.dumps(specs, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "exceptions/export":
            etmap = {"WAIT": "等待超时", "HOLD": "HOLD超时"}
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["工单号", "Lot号", "工序", "类型", "状态", "进入时间",
                        "流出时间", "时长(h)", "责任Owner", "备注"])
            for r in db.list_exceptions():
                w.writerow([r.get("wo_no", ""), r.get("lot_id", ""),
                            r.get("spec_name") or r.get("spec", ""),
                            etmap.get(r.get("etype"), r.get("etype") or ""),
                            r.get("status", ""), r.get("enter_at", ""),
                            r.get("exit_at", ""),
                            round(r.get("peak_hours") or 0, 1),
                            r.get("owner", ""), r.get("remark", "")])
            self._send("﻿" + buf.getvalue(), ctype="text/csv; charset=utf-8",
                       extra={"Content-Disposition": "attachment; filename=exceptions.csv"})
        elif route == "export/json":
            payload = json.dumps(db.export_rows(), ensure_ascii=False, indent=2)
            self._send(payload, ctype="application/json; charset=utf-8",
                       extra={"Content-Disposition": "attachment; filename=work_orders.json"})
        elif route == "export/csv":
            buf = io.StringIO()
            rows = db.export_rows()
            if rows:
                w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            data = "\ufeff" + buf.getvalue()
            self._send(data, ctype="text/csv; charset=utf-8",
                       extra={"Content-Disposition": "attachment; filename=work_orders.csv"})
        elif route == "order_detail":
            wo = qs.get("wo", [""])[0]
            try:
                info = mes.lookup_order(wo) if wo else None
                info = self._enrich_ct_names(info)
                info = self._enrich_owner(info)
                info = self._enrich_ignored(info)
            except Exception as e:
                print(f"[order_detail] wo={wo!r} 解析失败: {e}")
                info = None
            self._send(json.dumps(info or {"variants": []}, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "lot_search":
            lot = qs.get("lot", [""])[0]
            try:
                res = mes.lookup_by_lot(lot) if lot else []
                for item in res:
                    item["detail"] = self._enrich_owner(
                        self._enrich_ct_names(item.get("detail")))
            except Exception as e:
                print(f"[lot_search] lot={lot!r} 查询失败: {e}")
                res = []
            self._send(json.dumps(res, ensure_ascii=False),
                       ctype="application/json; charset=utf-8")
        elif route == "admin/lookup_mes":
            wo = qs.get("wo", [""])[0]
            try:
                result = mes.lookup_order(wo) if wo else None
            except Exception as e:
                print(f"[admin/lookup_mes] wo={wo!r} 查询失败: {e}")
                result = None
            payload = json.dumps(result or {"variants": []}, ensure_ascii=False)
            self._send(payload, ctype="application/json; charset=utf-8")
        else:
            self._send("404 Not Found", status=404, ctype="text/plain; charset=utf-8")

    def do_POST(self):
        try:
            self._dispatch_post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._handle_500()

    def _dispatch_post(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.strip("/")
        # 类别中间件:后台操作写入当前 cookie 选定的类别库
        self._apply_category()
        # WIP 超期分析:系列归属保存(需管理员登录)
        if route == "wip_analysis/map":
            if not self._is_authed():
                self._send(json.dumps({"ok": False, "msg": "未登录，无法保存"},
                                      ensure_ascii=False), status=401,
                           ctype="application/json; charset=utf-8")
                return
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(min(n, 1024 * 1024)) if n else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
                hidden = payload.get("hidden")
                if hidden is not None and not isinstance(hidden, list):
                    raise ValueError("hidden 必须是数组")
                saved = db.series_map_save(payload.get("map") or {},
                                           self._current_user(),
                                           hidden=hidden,
                                           expected_rev=payload.get("rev"))
                hidden_count = len(db.series_hidden_products())
                new_rev = db.series_map_rev()
                db.log_action(self._current_user(), "WIP系列归属",
                              f"保存 {saved} 个型号归属 / 隐藏 {hidden_count} 个型号 / rev={new_rev}")
                self._send(json.dumps({"ok": True, "saved": saved,
                                       "hidden_count": hidden_count,
                                       "rev": new_rev},
                                      ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)},
                                      ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            return
        # WIP 超期分析:三类 Owner 表导入(需管理员登录)
        if route == "wip_analysis/owners_import":
            if not self._is_authed():
                self._send(json.dumps({"ok": False, "msg": "未登录，无法导入"},
                                      ensure_ascii=False), status=401,
                           ctype="application/json; charset=utf-8")
                return
            try:
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in ctype:
                    raise ValueError("请用文件上传方式提交")
                boundary = None
                for seg in ctype.split(";"):
                    seg = seg.strip()
                    if seg.startswith("boundary="):
                        boundary = seg[9:].strip('"')
                length = int(self.headers.get("Content-Length", 0) or 0)
                if not boundary or length <= 0:
                    raise ValueError("上传格式错误")
                if length > self.MAX_UPLOAD:
                    try:
                        self.rfile.read(length)
                    except Exception:
                        pass
                    raise ValueError(
                        f"文件过大(上限 {self.MAX_UPLOAD // 1024 // 1024}MB)")
                body = self.rfile.read(length)
                fields = importer.parse_multipart(body, boundary)
                f = fields.get("file")
                if not f or not f.get("value"):
                    raise ValueError("未选择文件")
                filename = f.get("filename") or ""
                parsed = importer.parse_owner_workbook(filename, f.get("value"))
                _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                import_rev = _q.get("rev", [""])[0]
                summary = db.owner_map_import(
                    parsed, self._current_user(), filename,
                    expected_rev=import_rev or None)
                db.log_action(
                    self._current_user(), "WIP Owner导入",
                    filename + " · " + ", ".join(
                        f"{k}:{v}" for k, v in summary.get("imported", {}).items()))
                self._send(json.dumps({"ok": True, **summary},
                                      ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": str(e)},
                                      ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            return
        # 登录提交无需鉴权
        if route == "login":
            self._handle_login()
            return
        # 以下后台操作均需登录
        ADMIN_POSTS = {
            "data/import", "admin/upload_ct", "admin/upload_owner",
            "data/sync_mes", "admin/create", "admin/update", "admin/delete",
            "admin/base_add", "admin/base_delete", "admin/sync_products",
            "admin/change_password",
            "admin/account_update",
            "admin/user_add", "admin/user_delete", "admin/user_reset_pw",
            "admin/ct_method", "admin/ct_delete", "admin/set_role", "admin/rename_self",
            "ignore_lot", "set_order_done", "set_wait", "resolve_hold", "edit_hold", "exc_update", "admin/exc_thresholds",
            "admin/exc_thr_set", "admin/exc_thr_del",
            "admin/urgent_create", "admin/urgent_delete",
            "admin/exc_wo_set", "admin/exc_wo_del",
        }
        if route in ADMIN_POSTS and not self._is_authed():
            self._redirect("/login")
            return
        # 超管专属:仅账号管理(增删/改密/角色)与「改自己账户名」;业务操作普通管理员即可做
        SUPER_POSTS = {
            "admin/user_add", "admin/user_delete", "admin/user_reset_pw",
            "admin/set_role", "admin/rename_self",
        }
        if route in SUPER_POSTS and not self._is_super():
            self._redirect("/admin?msg=" + urllib.parse.quote("需要超级管理员权限"))
            return
        _user = self._current_user()
        if route == "data/import":
            self._handle_import()
        elif route == "monitor/upload_flow":
            # Flow 流程表上传(实时WIP 页右上角,交互同后台 CT 表管理):
            # 文件名去扩展名=型号,列 SEQNO/SPECNAME/STEPNAME。
            # 未登录返回 JSON 提示(不走 303,避免前端 fetch 误报网络错误)
            if not self._is_authed():
                self._send(json.dumps({"ok": False, "msg": "未登录:请先到「后台管理」登录后再上传 Flow"},
                                       ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
                return
            filename, raw = self._read_upload("/monitor")
            if raw is None:
                self._send(json.dumps({"ok": False, "msg": "上传失败(文件为空或格式不正确)"},
                                       ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
                return
            try:
                import monitor
                headers, rows = importer.parse_upload(filename, raw)
                parsed = monitor.parse_flow_table(headers, rows)
                if not parsed["specs"]:
                    raise ValueError("无有效工序行(检查列名 SEQNO/SPECNAME)")
                # 型号解析:文件名精确匹配,否则从文件名提取已知型号(如 MB23流程表.xlsx → MB23)
                model = monitor.resolve_flow_model(filename)
                if not model:
                    raise ValueError("无法从文件名识别型号(请用型号命名文件,如 MB23.xlsx)")
                db.save_flow_table(model, parsed)
                self._send(json.dumps(
                    {"ok": True, "msg": f"Flow 已上传,型号={model}:{len(parsed['specs'])} 道工序"},
                    ensure_ascii=False),
                    ctype="application/json; charset=utf-8")
            except Exception as e:
                self._send(json.dumps({"ok": False, "msg": f"Flow 上传失败: {e}"},
                                       ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
        elif route == "admin/upload_ct":
            self._handle_upload_ct()
        elif route == "admin/ct_method":
            data = self._form_fields()
            use_cal = data.get("method", "0") == "1"
            db.set_ct_method(data.get("model", ""), use_cal)
            db.log_action(_user, "切换CT计算方式",
                          f"{data.get('model','')} → {'按校准后' if use_cal else '按上传CT'}")
            # 切换计算方式后立即按新 CT 重算进度/风险/ETA,否则甘特图仍显示旧风险提示
            self._recalc_async()
            self._redirect("/admin?msg=" + urllib.parse.quote("CT 计算方式已更新,已重算进度与风险"))
        elif route == "admin/ct_delete":
            data = self._form_fields()
            model = (data.get("model", "") or "").strip()
            if not model:
                self._redirect("/admin?msg=" + urllib.parse.quote("无效的型号"))
                return
            deleted = db.delete_ct_table(model)
            if deleted:
                db.log_action(_user, "删除CT表", model)
                # 删除后该型号工单失去 CT 依据,重算进度/风险(将标为「未找到CT表」)
                self._recalc_async()
                msg = f"CT表 {model} 已删除,相关工单进度已重算"
            else:
                msg = f"未找到型号 {model} 的 CT 表"
            self._redirect("/admin?msg=" + urllib.parse.quote(msg))
        elif route == "ignore_lot":
            # 勾选/取消「忽略残留 LOT」→ 记录并重算进度/风险/ETA,返回 JSON
            data = self._form_fields()
            lot_id = data.get("lot_id", "").strip()
            ignore = data.get("ignore", "0") == "1"
            if not lot_id:
                self._send(json.dumps({"ok": False, "msg": "缺少 lot_id"}),
                           ctype="application/json; charset=utf-8")
                return
            db.set_lot_ignored(lot_id, data.get("wo", ""), data.get("spec", ""),
                               ignore, _user, data.get("note", ""))
            db.log_action(_user, "忽略残留LOT" if ignore else "取消忽略LOT",
                          f"{data.get('wo','')} / {data.get('spec','')} / {lot_id}")
            # 重算 head/tail/进度/风险/ETA:转后台线程(写库已即时生效,页面灰显/恢复
            # 立即可见;全量重算可被自动刷新锁阻塞数秒~数十秒,串行会让人以为"忽略掉"、
            # 随手中断/刷新页面时提交未完成)。下次自动同步也会兜底再算。
            self._recalc_async()
            self._send(json.dumps({"ok": True}),
                       ctype="application/json; charset=utf-8")
        elif route == "set_wait":
            # 管理员挂起工单(等料/设备等可协调异常):填恢复作业日 + 原因。
            # resume 传空 = 解除挂起。db 内部据 resume_date 顺延重算 ETA/风险并留痕。
            data = self._form_fields()
            wo = (data.get("wo", "") or "").strip()
            if not wo:
                self._send(json.dumps({"ok": False, "msg": "缺少工单号"}),
                           ctype="application/json; charset=utf-8")
                return
            db.set_wait_hold(wo, data.get("resume", ""), data.get("reason", ""),
                             hold_spec=data.get("hold_spec", ""), operator=_user,
                             start_date=data.get("start", ""))
            self._send(json.dumps({"ok": True}),
                       ctype="application/json; charset=utf-8")
        elif route == "edit_hold":
            # 编辑某一条已有挂起(按 wo_holds.id):改工序/开始挂起日期/恢复作业日/原因。
            data = self._form_fields()
            hid = self._safe_int(data.get("id"))
            if hid is None:
                self._send(json.dumps({"ok": False, "msg": "缺少挂起 id"}),
                           ctype="application/json; charset=utf-8")
                return
            db.edit_wo_hold(hid, data.get("hold_spec", ""), data.get("resume", ""),
                            data.get("reason", ""), start_date=data.get("start", ""),
                            operator=_user)
            self._send(json.dumps({"ok": True}),
                       ctype="application/json; charset=utf-8")
        elif route == "resolve_hold":
            # 解除某一条挂起(按 wo_holds.id),保留为历史记录;db 内部刷新缓存并重算 ETA
            data = self._form_fields()
            hid = self._safe_int(data.get("id"))
            if hid is None:
                self._send(json.dumps({"ok": False, "msg": "缺少挂起 id"}),
                           ctype="application/json; charset=utf-8")
                return
            db.resolve_wo_hold(hid, operator=_user)
            self._send(json.dumps({"ok": True}),
                       ctype="application/json; charset=utf-8")
        elif route == "set_order_done":
            # 管理员人工勾选/取消「工单已完成」→ 移入/移出「已完成工单」页,返回 JSON
            data = self._form_fields()
            oid = self._safe_int(data.get("id"))
            if oid is None:
                self._send(json.dumps({"ok": False, "msg": "缺少工单 id"}),
                           ctype="application/json; charset=utf-8")
                return
            done = data.get("done", "0") == "1"
            o = db.get_order(oid)
            db.set_order_done(oid, done)
            db.log_action(_user, "人工标记完成" if done else "取消人工完成",
                          (o or {}).get("wo_no", str(oid)))
            self._send(json.dumps({"ok": True}),
                       ctype="application/json; charset=utf-8")
        elif route == "exc_update":
            # 管理员填写/修改异常记录的责任Owner与备注,返回 JSON
            data = self._form_fields()
            eid = self._safe_int(data.get("id"))
            if eid is None:
                self._send(json.dumps({"ok": False, "msg": "缺少 id"}),
                           ctype="application/json; charset=utf-8")
                return
            db.update_exception(eid, data.get("owner", ""), data.get("remark", ""))
            self._send(json.dumps({"ok": True}),
                       ctype="application/json; charset=utf-8")
        elif route == "admin/exc_thresholds":
            data = self._form_fields()
            def _num(v, d):
                try:
                    return max(0.0, float(v))
                except (ValueError, TypeError):
                    return d
            wh = _num(data.get("wait_h"), 24.0)
            hh = _num(data.get("hold_h"), 24.0)
            db.set_exc_thresholds(wh, hh)
            db.log_action(_user, "设置异常阈值", f"Wait>{wh:g}h HOLD>{hh:g}h")
            db.scan_exceptions()   # 立即按新阈值重扫
            self._redirect("/admin?msg=" + urllib.parse.quote(
                f"系统默认异常阈值已更新:Wait>{wh:g}h、HOLD>{hh:g}h"))
        elif route == "admin/exc_thr_set":
            data = self._form_fields()
            def _n(v, d=0.0):
                try:
                    return max(0.0, float(v))
                except (ValueError, TypeError):
                    return d
            model = (data.get("model") or "").strip()
            spec = (data.get("spec") or "").strip()
            if not model:
                self._redirect("/admin?msg=" + urllib.parse.quote("请先选择型号") + "#exc")
                return
            db.set_exc_threshold(model, spec, _n(data.get("wait_h")), _n(data.get("hold_h")))
            db.log_action(_user, "设置分级异常阈值",
                          f"{model}/{spec or '(型号全局)'} Wait>{_n(data.get('wait_h')):g}h HOLD>{_n(data.get('hold_h')):g}h")
            db.scan_exceptions()
            self._redirect("/admin?msg=" + urllib.parse.quote(
                f"已设置 {model}/{spec or '型号全局'} 的异常阈值"))
        elif route == "admin/exc_thr_del":
            data = self._form_fields()
            db.del_exc_threshold(data.get("model", ""), data.get("spec", ""))
            db.log_action(_user, "删除分级异常阈值",
                          f"{data.get('model','')}/{data.get('spec','') or '(型号全局)'}")
            db.scan_exceptions()
            self._redirect("/admin?msg=" + urllib.parse.quote("已删除该阈值,恢复按上级阈值") + "#exc")
        elif route == "admin/exc_wo_set":
            data = self._form_fields()
            def _n2(v, d=0.0):
                try:
                    return max(0.0, float(v))
                except (ValueError, TypeError):
                    return d
            wo = (data.get("wo_no") or "").strip()
            if not wo:
                self._redirect("/admin?msg=" + urllib.parse.quote("请填写工单号") + "#exc")
                return
            wh, hh = _n2(data.get("wait_h")), _n2(data.get("hold_h"))
            # specs: 多选工序(换行分隔);为空=整单。兼容旧单 spec 字段。
            specs = [x.strip() for x in (data.get("specs") or data.get("spec") or "").splitlines() if x.strip()]
            if specs:
                for sp in specs:
                    db.set_wo_threshold(wo, sp, wh, hh)
                scope = f"{len(specs)} 个工序"
            else:
                db.set_wo_threshold(wo, "", wh, hh)
                scope = "整单"
            db.log_action(_user, "设置工单异常阈值", f"{wo}/{scope}")
            db.scan_exceptions()
            self._redirect("/admin?msg=" + urllib.parse.quote(f"已设置工单 {wo} 异常阈值({scope})") + "#exc")
        elif route == "admin/exc_wo_del":
            data = self._form_fields()
            db.del_wo_threshold(data.get("wo_no", ""), data.get("spec", ""))
            db.log_action(_user, "删除工单异常阈值", f"{data.get('wo_no','')}/{data.get('spec','') or '(整单)'}")
            db.scan_exceptions()
            self._redirect("/admin?msg=" + urllib.parse.quote("已删除该工单阈值") + "#exc")
        elif route == "admin/urgent_create":
            data = self._form_fields()
            batch = (data.get("batch_name") or "").strip()
            product = (data.get("product") or "").strip()
            tspec = (data.get("target_spec") or "").strip()
            lots = [x.strip() for x in (data.get("lots") or "").replace(",", "\n").splitlines() if x.strip()]
            if not (batch and product and tspec and lots):
                self._redirect("/admin?msg=" + urllib.parse.quote("批次名称/型号/目标工序/Lot号 不能为空") + "#urgent")
                return
            # 「型号」直接就是 CT 表型号(MB6/MB12),归一去掉子项目后取该 CT 表
            model = mes.base_model(product)
            ctab = db.get_ct_table(model)
            tname = tspec
            for s in (ctab or {}).get("specs", []):
                if s.get("spec") == tspec:
                    tname = s.get("stepname", "") or tspec
                    break
            bid = self._safe_int(data.get("batch_id"))
            if bid is not None:
                db.update_urgent_batch(bid, batch, product, tspec, tname,
                                       data.get("plan_end", ""), lots, ct_model=model)
                db.log_action(_user, "编辑加急验证Lot", f"#{bid} {batch}/{product}(CT:{model})/{len(lots)}Lot")
                self._redirect("/admin?msg=" + urllib.parse.quote(f"已更新加急批次「{batch}」") + "#urgent")
            else:
                db.create_urgent_batch(batch, product, tspec, tname, data.get("plan_end", ""),
                                       lots, ct_model=model)
                db.log_action(_user, "新建加急验证Lot",
                              f"{batch}/{product}(CT:{model})/到{tname}/{len(lots)}个Lot")
                self._redirect("/admin?msg=" + urllib.parse.quote(f"已新建加急批次「{batch}」({len(lots)} 个 Lot)") + "#urgent")
        elif route == "admin/urgent_delete":
            data = self._form_fields()
            bid = self._safe_int(data.get("id"))
            if bid is not None:
                db.delete_urgent_batch(bid)
                db.log_action(_user, "删除加急验证Lot", f"batch_id={bid}")
            self._redirect("/admin?msg=" + urllib.parse.quote("已删除加急批次") + "#urgent")
        elif route == "admin/upload_owner":
            self._handle_upload_owner()
        elif route == "data/sync_mes":
            self._handle_sync_mes()
        elif route == "admin/change_password":
            data = self._form_fields()
            ok, m = auth.change_password(_user,
                                         data.get("old_pw", ""), data.get("new_pw", ""))
            if ok:
                db.log_action(_user, "修改密码", "本人")
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/account_update":
            # 账号安全:一个表单统一处理「改账号名 + 改密码」,原密码做身份验证,填了哪项改哪项。
            data = self._form_fields()
            old_pw = data.get("old_pw", "")
            new_pw = (data.get("new_pw") or "").strip()
            new_name = (data.get("new_name") or "").strip()
            if not new_pw and not new_name:
                self._redirect("/admin?msg=" + urllib.parse.quote("未填写要修改的内容(新账号名或新密码)") + "#acct")
                return
            if not auth.verify(_user, old_pw):
                self._redirect("/admin?msg=" + urllib.parse.quote("原密码错误,未做任何修改") + "#acct")
                return
            msgs = []
            if new_pw:   # 先改密码(已用原密码校验身份)
                ok, m = auth.change_password(_user, old_pw, new_pw)
                if not ok:
                    self._redirect("/admin?msg=" + urllib.parse.quote(m) + "#acct")
                    return
                db.log_action(_user, "修改密码", "本人")
                msgs.append(m)
            if new_name:   # 再改账号名(仅超管;token 不变,无需重新登录)
                if not self._is_super():
                    pre = (" · ".join(msgs) + " · ") if msgs else ""
                    self._redirect("/admin?msg=" + urllib.parse.quote(pre + "仅超级管理员可修改账号名") + "#acct")
                    return
                ok, m, newname = auth.change_username(_user, new_name)
                if not ok:
                    pre = (" · ".join(msgs) + " · ") if msgs else ""
                    self._redirect("/admin?msg=" + urllib.parse.quote(pre + m) + "#acct")
                    return
                db.log_action(newname, "修改账号名", f"{_user} → {newname}")
                msgs.append(m)
            self._redirect("/admin?msg=" + urllib.parse.quote(" · ".join(msgs) or "已保存") + "#acct")
        elif route == "admin/user_add":
            data = self._form_fields()
            ok, m = auth.add_user(data.get("username", ""), data.get("pw", ""),
                                  data.get("role", "normal"))
            if ok:
                db.log_action(_user, "添加账号",
                              f"{data.get('username','')}({data.get('role','normal')})")
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/user_delete":
            data = self._form_fields()
            ok, m = auth.delete_user(_user, data.get("username", ""))
            if ok:
                db.log_action(_user, "删除账号", data.get("username", ""))
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/user_reset_pw":
            data = self._form_fields()
            ok, m = auth.reset_password(data.get("username", ""), data.get("new_pw", ""))
            if ok:
                db.log_action(_user, "重置密码", data.get("username", ""))
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/set_role":
            data = self._form_fields()
            ok, m = auth.set_role(_user, data.get("username", ""),
                                  data.get("role", "normal"))
            if ok:
                db.log_action(_user, "设置角色",
                              f"{data.get('username','')} → {data.get('role','normal')}")
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/rename_self":
            data = self._form_fields()
            ok, m, newname = auth.change_username(_user, data.get("new_name", ""))
            if ok:
                db.log_action(newname, "修改账号名", f"{_user} → {newname}")
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/create":
            data = self._merge_product(self._form_fields())
            self._collect_eng_times(data)   # 从 eng_h_* 字段拼 eng_times JSON
            ok, m = db.create_order(data)
            if ok:
                self._recalc_async()      # 按新工单(及计划日期)算进度/风险/ETA,转后台免阻塞
            self._redirect("/admin?msg=" + urllib.parse.quote(m))
        elif route == "admin/update":
            data = self._merge_product(self._form_fields())
            # AJAX(后台批量维护:编辑不刷新列表)→ 返回 JSON;否则沿用整页重定向
            is_ajax = (self.headers.get("X-Requested-With") == "XMLHttpRequest"
                       or data.get("ajax") == "1")

            def _upd_json(ok, msg, oid=None):
                payload = {"ok": ok, "msg": msg}
                if ok and oid is not None:
                    o = db.get_order(oid)
                    if o:
                        payload.update({"id": oid, "row": views.admin_order_row(o),
                                        "order": views.admin_order_js(o)})
                self._send(json.dumps(payload, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")

            oid = self._safe_int(data.get("id"))
            if oid is None:
                if is_ajax:
                    _upd_json(False, "无效的工单ID")
                else:
                    self._redirect("/admin?msg=" + urllib.parse.quote("无效的工单ID"))
                return
            # 「清除计划日期」:空值会被表单解析丢弃,故用 clear_xxx 标记显式置空
            if data.get("clear_plan_start") == "1":
                data["plan_start"] = ""
            if data.get("clear_plan_end") == "1":
                data["plan_end"] = ""
            db.update_order(oid, data)
            # 改了计划开始/交付日后,必须重算 ETA/风险/进度,否则看板仍按旧日期显示(=没生效)
            self._recalc_async()
            if is_ajax:
                _upd_json(True, "工单已更新", oid)
            else:
                self._redirect("/admin?msg=" + urllib.parse.quote("工单已更新"))
        elif route == "admin/delete":
            data = self._form_fields()
            oid = self._safe_int(data.get("id"))
            if oid is None:
                self._redirect("/admin?msg=" + urllib.parse.quote("无效的工单ID"))
                return
            db.delete_order(oid)
            db.log_action(_user, "删除工单", str(data.get("id", "")))
            self._redirect("/admin?msg=" + urllib.parse.quote("工单已删除"))
        elif route == "admin/base_add":
            data = self._form_fields()
            kind = data.get("kind", "product")
            value = data.get("value", "").strip()
            db.base_add(kind, value)
            # 产品型号:提示是否在 MES 中精确存在
            if kind == "product" and value:
                if mes.model_exists(value):
                    msg = f"已添加 {value}(MES 中存在,将精确抓取)"
                else:
                    msg = f"已添加 {value},但 MES 中无此封装,抓取时会按前两字符“{value[:2].upper()}”兜底,可能捞入同系列其它型号"
            else:
                msg = "已添加"
            self._redirect("/admin?msg=" + urllib.parse.quote(msg))
        elif route == "admin/base_delete":
            data = self._form_fields()
            bid = self._safe_int(data.get("id"))
            if bid is None:
                self._redirect("/admin?msg=" + urllib.parse.quote("无效的ID"))
                return
            db.base_delete(bid)
            self._redirect("/admin?msg=" + urllib.parse.quote("已删除"))
        elif route == "admin/sync_products":
            # 从 MES 自动读取的产品型号里按勾选更新抓取白名单,随后立即重抓一次
            data = self._form_multi()
            selected = data.get("prod", [])
            added, removed = db.sync_product_whitelist(selected)
            db.log_action(_user, "更新抓取白名单(MES勾选)",
                          f"新增{added}/移除{removed},共选{len(selected)}个")
            note = ""
            if added or removed:
                try:
                    total, _ = db.run_mes_sync()
                    note = f",已按新白名单重新抓取 {total} 个工单"
                except Exception as e:
                    note = f",但重新抓取失败:{e}"
            self._redirect("/admin?msg=" + urllib.parse.quote(
                f"白名单已更新:新增 {added} 个、移除 {removed} 个型号{note}") + "#ct")
        else:
            self._send("404 Not Found", status=404, ctype="text/plain; charset=utf-8")

    def _enrich_ct_names(self, info):
        """用 CT 表的中文步骤名(stepname)覆盖详情里每个工序的描述,
        并把「步骤」列与工序行顺序一律按上传的 CT 表(seqno)排列。
        CT 表没有的工序(如虚拟站)保留 MES 原描述/原序号,排到最后。"""
        if not info or not info.get("variants"):
            return info
        product = info.get("product") or ""
        model = mes.base_model(product)
        ctab = db.get_ct_table(model)
        if not ctab:
            return info
        specs = ctab.get("specs", [])
        name_map = {s.get("spec", ""): (s.get("stepname", "") or "") for s in specs}
        step_map = {s.get("spec", ""): s.get("seqno") for s in specs}   # CT 表步骤号
        order_map = {s.get("spec", ""): i for i, s in enumerate(specs)}  # CT 表顺序
        big = len(specs) + 100000   # CT 表没有的工序(虚拟站等)排到最后
        # 完整工艺路线骨架(供详情页「工序/Lot 分布图」作 X 轴):
        # 含产品尚未到达、以及已流过的工序,使图能展示前后全流程而非只有当前有 lot 的工序。
        info["ct_specs"] = [
            {"spec": s.get("spec", ""), "name": (s.get("stepname", "") or s.get("spec", ""))}
            for s in specs
        ]
        for v in info["variants"]:
            procs = v.get("processes", [])
            for p in procs:
                sp = p.get("spec", "")
                cn = name_map.get(sp)
                if cn:
                    p["desc"] = cn               # 用 CT 中文步骤名覆盖
                if step_map.get(sp) is not None:
                    p["seq"] = step_map[sp]      # 步骤号用 CT 表 seqno
            # 按 CT 表顺序重排;不在 CT 表的工序保持原相对顺序、置于末尾
            v["processes"] = [p for _, p in sorted(
                enumerate(procs),
                key=lambda t: (order_map.get(t[1].get("spec", ""), big), t[0]))]
        return info

    def _enrich_owner(self, info):
        """按每个 Lot 的工序(SPECNAME)+ 该 Lot 状态判定负责人,写入 lot.owner。
        HOLD → ENG Owner;WaitingMoveOut/WaitingTrackIn/RUNNING → Inline Owner。
        同时给每个工序行附 process 级 owner(取该工序 SPECNAME + 工序状态)。"""
        if not info or not info.get("variants"):
            return info
        product = info.get("product") or ""
        model = mes.base_model(product)
        for v in info["variants"]:
            for p in v.get("processes", []):
                spec = p.get("spec", "")
                p["owner"] = db.owner_for(model, spec, p.get("status", ""))
                for l in p.get("lot_list", []):
                    l["owner"] = db.owner_for(model, spec, l.get("status", ""))
        return info

    def _enrich_ignored(self, info):
        """给每个 Lot 标记当前是否被忽略(ignored),供工序/Lot 分布图把整站被忽略的工序画成斜纹。"""
        if not info or not info.get("variants"):
            return info
        try:
            ignored = db.get_ignored_lot_ids()
        except Exception:
            ignored = set()
        for v in info["variants"]:
            for p in v.get("processes", []):
                for l in p.get("lot_list", []):
                    l["ignored"] = 1 if l.get("lot_id") in ignored else 0
        return info

    @staticmethod
    def _safe_int(val, default=None):
        """容错整数转换:非法/缺失返回 default(默认 None),避免裸 int() 抛错致 500。"""
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    MAX_FORM_BODY = 4 * 1024 * 1024   # 普通表单(urlencoded)请求体上限,防止超大请求占内存

    def _form_fields(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length > self.MAX_FORM_BODY:
            try:
                self.rfile.read(length)   # 读掉请求体,避免污染后续请求
            except Exception:
                pass
            return {}                     # 超限按空表单处理,交由各路由的必填校验拦截
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def _form_multi(self):
        """同 _form_fields,但保留同名多值(如复选框)为列表,供白名单勾选等使用。"""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length > self.MAX_FORM_BODY:
            try:
                self.rfile.read(length)
            except Exception:
                pass
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return urllib.parse.parse_qs(raw)

    def _read_upload(self, redirect_to):
        """解析 multipart 单文件上传,返回 (filename, raw_bytes)。
        校验 Content-Type/boundary/大小;失败时已发重定向并返回 (None, None)。"""
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._redirect(redirect_to + "?msg=" + urllib.parse.quote("无效的上传"))
            return None, None
        boundary = None
        for seg in ctype.split(";"):
            seg = seg.strip()
            if seg.startswith("boundary="):
                boundary = seg[9:].strip('"')
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if not boundary or length <= 0:
            self._redirect(redirect_to + "?msg=" + urllib.parse.quote("上传格式错误"))
            return None, None
        if length > self.MAX_UPLOAD:
            try:
                self.rfile.read(length)   # 读掉请求体,避免污染后续请求
            except Exception:
                pass
            self._redirect(redirect_to + "?msg=" + urllib.parse.quote(
                f"文件过大(上限 {self.MAX_UPLOAD // 1024 // 1024}MB)"))
            return None, None
        body = self.rfile.read(length)
        fields = importer.parse_multipart(body, boundary)
        if "file" not in fields:
            self._redirect(redirect_to + "?msg=" + urllib.parse.quote("未选择文件"))
            return None, None
        return fields["file"]["filename"] or "", fields["file"]["value"]
    def _merge_product(self, data):
        """把 product_main + product_sub 合成 product 字段。
        MB12 选了子项目 → 'MB12 / 4in1';其他型号 → 主型号本身。
        """
        main = (data.get("product_main") or "").strip()
        sub = (data.get("product_sub") or "").strip()
        if main == "MB12" and sub:
            data["product"] = f"{main} / {sub}"
        elif main:
            data["product"] = main
        return data

    def _collect_eng_times(self, data):
        """遍历表单 eng_h_* 字段,拼成 eng_times JSON 数组(仅保留 eng_h>0 的工序)。"""
        import json as _json
        items = []
        for k, v in list(data.items()):
            if k.startswith("eng_h_") and v:
                try:
                    eh = float(v)
                except (ValueError, TypeError):
                    continue
                if eh <= 0:
                    continue
                spec = k[6:]   # 去掉 'eng_h_' 前缀
                note = data.pop("eng_note_" + spec, "") or ""
                data.pop(k, None)
                items.append({"spec": spec, "eng_h": eh, "note": (note or "").strip()})
        if items:
            data["eng_times"] = _json.dumps(items, ensure_ascii=False)
        else:
            # 清除残留的 eng_note_* 字段,避免污染数据库
            for k in list(data.keys()):
                if k.startswith("eng_note_"):
                    data.pop(k, None)

    def _handle_import(self):
        filename, raw = self._read_upload("/data")
        if raw is None:
            return
        try:
            headers, rows = importer.parse_upload(filename, raw)
            count = self._import_rows(headers, rows)
            self._redirect("/data?msg=" + urllib.parse.quote(f"成功导入 {count} 条工单"))
        except Exception as e:
            self._redirect("/data?msg=" + urllib.parse.quote(f"导入失败: {e}"))

    def _handle_upload_ct(self):
        """上传 CT 表(xlsx/csv),文件名(去扩展名)= 型号。解析后存库。"""
        filename, raw = self._read_upload("/admin")
        if raw is None:
            return
        # 文件名去扩展名 = 型号
        model = os.path.splitext(os.path.basename(filename))[0].strip()
        try:
            import ct as ctmod
            headers, rows = importer.parse_upload(filename, raw)
            parsed = ctmod.parse_ct_table(headers, rows)
            if not parsed["specs"]:
                raise ValueError("CT表无有效工序行(检查列名 SEQNO/SPECNAME/OTDO_CT)")
            db.save_ct_table(model, parsed)
            # 上传后立即重算一次进度
            self._recalc_async()
            msg = (f"CT表 {model} 已上传:{len(parsed['specs'])} 道工序,"
                   f"总CT {parsed['total_ct']:.0f}h,已重算进度")
        except Exception as e:
            msg = f"CT表上传失败: {e}"
        self._redirect("/admin?msg=" + urllib.parse.quote(msg))

    def _handle_login(self):
        data = self._form_fields()
        user = (data.get("user") or "").strip()
        pw = data.get("pw") or ""
        ip = self.client_address[0]
        # 登录后回跳:优先表单隐藏字段,其次 URL 参数;只允许站内路径
        nxt = self._safe_next(data.get("next", ""))
        if not nxt:
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nxt = self._safe_next(_q.get("next", [""])[0])

        def _login_fail(msg):
            loc = "/login?msg=" + urllib.parse.quote(msg)
            if nxt:
                loc += "&next=" + urllib.parse.quote(nxt)
            self._redirect(loc)

        if auth.is_locked(user, ip):
            _login_fail("连续失败已锁定 15 分钟,请稍后再试")
            return
        if auth.verify(user, pw):
            auth.clear_fails(user, ip)
            token = auth.new_session(user)
            cookie = (f"{auth.COOKIE_NAME}={token}; Path=/; HttpOnly; "
                      f"Max-Age={auth.SESSION_TTL}; SameSite=Lax")
            # 有 next 回原页面(WIP 分析);否则未选类别去选择页、已选进后台
            dest = nxt or ("/admin" if self._cat_cookie() else "/select")
            self._redirect_cookie(dest, set_cookie=cookie)
        else:
            auth.mark_fail(user, ip)   # 用户名轨 verify 内已计,这里补 IP 轨
            _login_fail("账号或密码错误")

    def _handle_upload_owner(self):
        """上传 Owner 表(xlsx/csv),列:TECHNOLOGY/SPECNAME/ENG Owner/Inline Owner。"""
        filename, raw = self._read_upload("/admin")
        if raw is None:
            return
        try:
            headers, rows = importer.parse_upload(filename, raw)
            parsed = importer.parse_owner_table(headers, rows)
            if not parsed["row_count"]:
                raise ValueError("Owner表无有效行")
            db.save_owner_table(parsed)
            self._recalc_async()   # 立即按新负责人规则重算
            msg = f"Owner表已上传:{parsed['row_count']} 条工序映射,已按当前工序+状态重算负责人"
        except Exception as e:
            msg = f"Owner表上传失败: {e}"
        self._redirect("/admin?msg=" + urllib.parse.quote(msg))

    def _do_mes_sync(self):
        """手动触发一次抓取(后台数据源「立即刷新」与侧边栏「刷新数据」共用):
        按当前类别、后台录入型号从严到松抓取 MES 并写库,同步记录状态。
        返回 (ok, msg)。"""
        import os
        if not os.path.exists(mes.DEFAULT_CSV):
            try:
                db.meta_set("last_sync_status", "未找到 MES数据库.csv")
            except Exception:
                pass
            return False, "未找到 MES数据库.csv"
        try:
            total, details = db.run_mes_sync()
            return True, f"抓取完成,共 {total} 个工单 | {details}"
        except Exception as e:
            try:
                db.meta_set("last_sync_status", f"失败: {e}")
            except Exception:
                pass
            return False, f"抓取失败: {e}"

    def _handle_sync_mes(self):
        """POST /data/sync_mes:手动触发(行为与侧边栏刷新一致,页面跳转回数据源)。"""
        length = self._safe_int(self.headers.get("Content-Length", 0), 0) or 0
        if length:
            self.rfile.read(length)
        ok, msg = self._do_mes_sync()
        self._redirect("/data?msg=" + urllib.parse.quote(msg))

    def _import_rows(self, headers, rows):
        idx_map = {}
        for i, h in enumerate(headers):
            key = (h or "").strip()
            if key in COL_MAP:
                idx_map[i] = COL_MAP[key]
        count = 0
        for row in rows:
            if not any(str(c).strip() for c in row):
                continue
            data = {}
            for i, field in idx_map.items():
                if i < len(row):
                    data[field] = str(row[i]).strip()
            if not data.get("wo_no"):
                continue
            try:
                data["progress"] = int(float(data.get("progress", 0) or 0))
            except (ValueError, TypeError):
                data["progress"] = 0
            ok, _ = db.create_order(data)
            if ok:
                count += 1
        return count

    def log_message(self, *args):
        pass


REFRESH_INTERVAL = 180  # 自动刷新间隔(秒)= 3 分钟(检查 MES csv 是否有更新)


def _sync_all_categories():
    """对每个类别(塑封/灌封)各跑一次 MES 同步:同一份全量 CSV,各按自己白名单过滤。
    单进程内逐类别串行,互不锁库。返回 {类别: 工单数}。"""
    result = {}
    for cat in db.CATEGORIES:
        db.set_category(cat)
        try:
            total, _ = db.run_mes_sync()
            result[cat] = total
        except Exception as e:
            print(f"[同步:{cat}] 失败: {e}")
            try:
                db.meta_set("last_sync_status", f"自动刷新失败: {e}")
            except Exception:
                pass
    return result


def _auto_refresh_loop():
    """后台守护线程:每 5 分钟自动抓取一次 MES csv(两个类别各刷一次)。"""
    # 启动后先等 10 秒(让服务先起来),再首刷,之后每 5 分钟一次
    time.sleep(10)
    last_mt = None
    while True:
        try:
            if os.path.exists(mes.DEFAULT_CSV):
                # csv 未变化(mtime 相同)则跳过整轮全量重扫,省 CPU
                mt = os.path.getmtime(mes.DEFAULT_CSV)
                if mt != last_mt:
                    res = _sync_all_categories()
                    last_mt = mt
                    print(f"[自动刷新] {time.strftime('%H:%M:%S')} " +
                          " / ".join(f"{k}:{v}" for k, v in res.items()))
                else:
                    print(f"[自动刷新] {time.strftime('%H:%M:%S')} csv 未变化,跳过")
            else:
                for cat in db.CATEGORIES:
                    db.set_category(cat)
                    db.meta_set("last_sync_status", "未找到 MES数据库.csv")
        except Exception as e:
            print(f"[自动刷新] 失败: {e}")
        time.sleep(REFRESH_INTERVAL)


def main():
    db.init_all()                 # 初始化两个业务库 + 共享账号库
    auth.ensure_default_account()
    # 启动时若有 csv,两个类别各同步一次,使页面立即显示 MES 数据
    if os.path.exists(mes.DEFAULT_CSV):
        try:
            res = _sync_all_categories()
            print("启动同步完成 · " + " / ".join(f"{k}:{v}" for k, v in res.items()))
        except Exception as e:
            print(f"启动同步跳过: {e}")
    # 启动自动刷新线程(守护线程,主程序退出即结束)
    t = threading.Thread(target=_auto_refresh_loop, daemon=True)
    t.start()
    print(f"WIP 系统已启动 → http://localhost:{PORT}")
    print(f"MES 自动刷新:每 {REFRESH_INTERVAL//60} 分钟一次")
    print("按 Ctrl+C 停止")
    socketserver.ThreadingTCPServer.allow_reuse_address = True  # 重启时复用端口
    socketserver.ThreadingTCPServer.request_queue_size = 128    # 连接 backlog(默认5偏小),提升突发并发容忍
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        httpd.daemon_threads = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
