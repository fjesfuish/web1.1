# -*- coding: utf-8 -*-
"""纯标准库解析上传的 .xlsx / .csv 文件。
xlsx 本质是 zip 包含 xml,用 zipfile + xml.etree 解析,无需 openpyxl/pandas。
返回统一格式: (headers:list, rows:list[list])
"""
import io
import csv
import zipfile
import xml.etree.ElementTree as ET

# xlsx 的命名空间
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_to_idx(ref):
    """单元格引用如 'B3' -> 列索引 1(0基)。"""
    col = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _shared_strings(zf):
    """读取 xlsx 共享字符串表。"""
    shared = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            # 字符串可能被拆成多个 <t>(富文本),全部拼起来
            texts = [t.text or "" for t in si.iter("{%s}t" % NS["m"])]
            shared.append("".join(texts))
    return shared


def _parse_sheet_xml(zf, sheet_path, shared):
    """解析一个 worksheet XML,返回 (headers, rows)。"""
    root = ET.fromstring(zf.read(sheet_path))
    sheet_data = root.find("m:sheetData", NS)
    if sheet_data is None:
        return [], []
    matrix = []
    for row in sheet_data.findall("m:row", NS):
        cells = {}
        max_idx = -1
        for c in row.findall("m:c", NS):
            ref = c.get("r", "A1")
            idx = _col_to_idx(ref)
            t = c.get("t")  # 't="s"' 表示值是共享字符串索引
            v_el = c.find("m:v", NS)
            is_el = c.find("m:is", NS)  # 内联字符串
            if v_el is not None:
                val = v_el.text or ""
                if t == "s":
                    val = shared[int(val)] if val.isdigit() and int(val) < len(shared) else val
            elif is_el is not None:
                val = "".join(t.text or "" for t in is_el.iter("{%s}t" % NS["m"]))
            else:
                val = ""
            cells[idx] = val
            max_idx = max(max_idx, idx)
        matrix.append([cells.get(i, "") for i in range(max_idx + 1)])

    if not matrix:
        return [], []
    headers = matrix[0]
    rows = matrix[1:]
    # 补齐每行长度
    width = len(headers)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return headers, rows


def _workbook_sheets(zf):
    """按 workbook.xml 的 sheet 顺序返回 [(sheet_name, zip_path), ...]。
    失败时退回按文件名排序的工作表。"""
    out = []
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = {}
        rel_path = "xl/_rels/workbook.xml.rels"
        if rel_path in zf.namelist():
            rr = ET.fromstring(zf.read(rel_path))
            for rel in rr:
                rels[rel.get("Id")] = rel.get("Target") or ""
        rid_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        for sh in wb.findall(".//m:sheets/m:sheet", NS):
            nm = sh.get("name") or ""
            tgt = rels.get(sh.get(rid_attr), "")
            if not tgt:
                continue
            if tgt.startswith("/"):
                path = tgt.lstrip("/")
            elif tgt.startswith("xl/"):
                path = tgt
            else:
                path = "xl/" + tgt.lstrip("/")
            if path in zf.namelist():
                out.append((nm, path))
    except (KeyError, ET.ParseError):
        out = []
    if not out:
        sheet_files = sorted(n for n in zf.namelist()
                             if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        out = [(f"Sheet{i+1}", n) for i, n in enumerate(sheet_files)]
    return out


def parse_xlsx_sheets(raw_bytes):
    """解析 xlsx 的全部工作表,返回 {sheet_name: (headers, rows)}(按工作簿顺序)。"""
    zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    shared = _shared_strings(zf)
    out = {}
    for name, path in _workbook_sheets(zf):
        try:
            headers, rows = _parse_sheet_xml(zf, path, shared)
        except (KeyError, ET.ParseError):
            continue
        if headers:
            out[name] = (headers, rows)
    return out


def parse_xlsx(raw_bytes):
    """解析 xlsx 字节,返回第一个工作表的 (headers, rows)。"""
    sheets = parse_xlsx_sheets(raw_bytes)
    if not sheets:
        return [], []
    return next(iter(sheets.values()))


def parse_csv(raw_bytes):
    """解析 csv 字节,返回 (headers, rows)。尝试 utf-8-sig 再退 gbk。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw_bytes.decode("utf-8", errors="replace")
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    return reader[0], reader[1:]


def parse_upload(filename, raw_bytes):
    """根据文件名后缀选择解析器。"""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return parse_csv(raw_bytes)
    if name.endswith(".xlsx"):
        return parse_xlsx(raw_bytes)
    raise ValueError("仅支持 .xlsx 或 .csv 文件")


def parse_owner_table(headers, rows):
    """解析 Owner 表(列:TECHNOLOGY、SPECNAME、ENG Owner、Inline Owner)。
    返回 {"map": {"TECH|SPEC": {"eng":..,"inline":..}}, "row_count": int}。
    键的 TECH 统一大写,SPEC 原样。
    """
    H = {}
    for i, h in enumerate(headers):
        key = (h or "").strip().lower()
        H[key] = i

    def idx(*names):
        for n in names:
            if n in H:
                return H[n]
        return None

    i_tech = idx("technology", "tech", "技术", "工艺")
    i_spec = idx("specname", "spec", "工序", "工序名")
    i_eng = idx("eng owner", "engowner", "eng", "eng负责人")
    i_inline = idx("inline owner", "inlineowner", "inline", "inline负责人")
    if i_spec is None or i_eng is None or i_inline is None:
        raise ValueError("缺少必要列:SPECNAME / ENG Owner / Inline Owner")

    def cell(r, i):
        return (r[i].strip() if i is not None and i < len(r) and r[i] else "")

    omap = {}
    n = 0
    for r in rows:
        spec = cell(r, i_spec)
        if not spec:
            continue
        tech = cell(r, i_tech).upper()
        omap[f"{tech}|{spec}"] = {"eng": cell(r, i_eng), "inline": cell(r, i_inline)}
        n += 1
    return {"map": omap, "row_count": n}


# ======================================================================
# WIP 超期分析 · Owner 表导入
# ----------------------------------------------------------------------
# 三张表(可放在同一个 .xlsx 的三个 sheet,也可分三次导 .csv):
#   Assy PC Owner : Product + TECHNOLOGY + PACKAGESIZE + Owner(按 PACKAGESIZE 匹配)
#   MPC Owner     : TECHNOLOGY + PACKAGESIZE + MPC Owner + 封装类型
#   Inline Owner  : TECHNOLOGY + SPECNAME + ENG Owner + Inline Owner
# 识别按表头关键字,不依赖 sheet 名称。
# ======================================================================

_OWNER_ALIASES = {
    "product": ("product", "productname", "产品", "产品代码", "料号"),
    "pkg": ("packagesize", "封装尺寸", "封装型号", "封装代码"),
    "tech": ("technology", "tech", "技术", "工艺", "技术平台"),
    "owner": ("owner", "负责人", "assypcowner"),
    "mpc": ("mpcowner", "mpc负责人"),
    "pkg_type": ("封装类型", "packagetype"),
    "spec": ("specname", "spec", "工序", "工序名", "工序代码"),
    "eng": ("engowner", "eng", "工程负责人"),
    "inline": ("inlineowner", "inline", "在线负责人"),
}


def _norm_header(h):
    h = (h or "").replace("﻿", "").strip().lower()
    for ch in (" ", "_", "-", "　"):
        h = h.replace(ch, "")
    return h


def _find_header(headers, key):
    """按别名找列下标;找不到返回 None。"""
    wanted = {_norm_header(x) for x in _OWNER_ALIASES[key]}
    for i, h in enumerate(headers):
        if _norm_header(h) in wanted:
            return i
    return None


def _cell(row, i):
    if i is None or i < 0 or i >= len(row):
        return ""
    return str(row[i] if row[i] is not None else "").replace("　", " ").strip()


def _row_has_data(row):
    return any(_cell(row, i) for i in range(len(row)))


def _series_code(v):
    s = (v or "").strip().lower()
    if "塑封" in s or "molding" in s:
        return "molding"
    if "灌封" in s or "potting" in s:
        return "potting"
    return ""


def owner_sheet_kind(headers):
    """按表头判断 sheet 类型:assy / mpc / inline;识别不了返回 None。"""
    if (_find_header(headers, "spec") is not None
            and _find_header(headers, "eng") is not None
            and _find_header(headers, "inline") is not None):
        return "inline"
    i_prod = _find_header(headers, "product")
    i_pkg = _find_header(headers, "pkg")
    i_owner = _find_header(headers, "owner")
    i_mpc = _find_header(headers, "mpc")
    i_ptype = _find_header(headers, "pkg_type")
    if i_mpc is not None:
        return "mpc"
    # 有的 MPC 导出把列名写成 Owner,用“PACKAGESIZE + 封装类型 + Owner 且无 Product”兜底
    if (i_pkg is not None and i_ptype is not None
            and i_owner is not None and i_prod is None):
        return "mpc"
    if i_prod is not None and i_owner is not None:
        return "assy"
    return None


def _parse_owner_rows(kind, headers, rows):
    """解析单一类型的 Owner 行,返回 map + 统计。键统一大写。"""
    i_prod = _find_header(headers, "product")
    i_pkg = _find_header(headers, "pkg")
    i_tech = _find_header(headers, "tech")
    i_owner = _find_header(headers, "owner")
    i_mpc = _find_header(headers, "mpc")
    i_ptype = _find_header(headers, "pkg_type")
    i_spec = _find_header(headers, "spec")
    i_eng = _find_header(headers, "eng")
    i_inline = _find_header(headers, "inline")

    out = {}
    parsed = skipped = conflicts = 0
    samples = []
    tech_prev = ""
    pkg_prev = ""
    for row in rows:
        if not _row_has_data(row):
            continue
        parsed += 1
        tech = _cell(row, i_tech)
        if tech:
            tech_prev = tech
        else:
            tech = tech_prev
        if kind == "assy":
            prod = _cell(row, i_prod)
            pkg = _cell(row, i_pkg)
            if pkg:
                pkg_prev = pkg
            else:
                pkg = pkg_prev
            # 统一以细分项 PACKAGESIZE 为锚点;Product 只作上下文,不再参与匹配。
            if not pkg:
                skipped += 1
                continue
            key = pkg.upper()
            rec = {"owner": _cell(row, i_owner), "technology": tech,
                   "package": pkg, "product": prod}
        elif kind == "mpc":
            pkg = _cell(row, i_pkg)
            if not pkg:
                skipped += 1
                continue
            ptype = _cell(row, i_ptype)
            key = pkg.upper()
            rec = {"owner": _cell(row, i_mpc if i_mpc is not None else i_owner),
                   "technology": tech, "package_type": ptype,
                   "series": _series_code(ptype)}
        else:  # inline
            spec = _cell(row, i_spec)
            if not spec:
                skipped += 1
                continue
            t = (tech or "").strip().upper()
            key = (t + "|" + spec.upper()) if t else ("|" + spec.upper())
            rec = {"eng": _cell(row, i_eng), "inline": _cell(row, i_inline),
                   "technology": tech, "spec": spec}
        old = out.get(key)
        if old is not None and old != rec:
            conflicts += 1
            if len(samples) < 5:
                samples.append(key)
        out[key] = rec
    return {"map": out, "row_count": len(out), "input_rows": parsed,
            "skipped": skipped, "conflicts": conflicts,
            "conflict_samples": samples}


def parse_owner_workbook(filename, raw_bytes):
    """解析 Owner 导入文件(一个 xlsx 多 sheet,或单 sheet xlsx/csv)。

    返回 {"types": {assy/mpc/inline: 解析结果}, "sheets": [...], "warnings": [...]}。
    同一类型出现多个 sheet 时合并(后出现的键覆盖先出现的)。
    """
    name = (filename or "").lower()
    if name.endswith(".csv"):
        headers, rows = parse_csv(raw_bytes)
        sheets = {"CSV": (headers, rows)}
    elif name.endswith(".xlsx"):
        sheets = parse_xlsx_sheets(raw_bytes)
    else:
        raise ValueError("仅支持 .xlsx 或 .csv；.xls 请先另存为 .xlsx")

    result = {"types": {}, "sheets": [], "warnings": []}
    for sname, (headers, rows) in sheets.items():
        if not headers:
            continue
        kind = owner_sheet_kind(headers)
        if not kind:
            preview = " / ".join(str(h) for h in headers[:8])
            result["warnings"].append(
                f"工作表「{sname}」未识别为 Owner 表，已跳过（表头：{preview}）")
            continue
        parsed = _parse_owner_rows(kind, headers, rows)
        result["sheets"].append({"name": sname, "type": kind,
                                 "rows": parsed["input_rows"],
                                 "unique": parsed["row_count"],
                                 "skipped": parsed["skipped"]})
        if kind in result["types"]:
            prev = result["types"][kind]
            prev["map"].update(parsed["map"])
            prev["row_count"] += parsed["row_count"]
            prev["input_rows"] += parsed["input_rows"]
            prev["skipped"] += parsed["skipped"]
            prev["conflicts"] += parsed["conflicts"]
            prev["conflict_samples"].extend(parsed["conflict_samples"])
            result["warnings"].append(
                f"工作表「{sname}」与前一个 {kind} 表重复，已合并")
        else:
            result["types"][kind] = parsed
    return result


def parse_multipart(body, boundary):
    """不依赖 cgi 模块的简易 multipart/form-data 解析。
    返回 {field_name: {"filename": str|None, "value": bytes}}。
    适用于本系统的单文件上传场景。
    """
    if isinstance(boundary, str):
        boundary = boundary.encode("latin-1")
    delim = b"--" + boundary
    result = {}
    # 按分隔符切分,去掉首尾(前导空段与结尾的 "--")
    parts = body.split(delim)
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        # 头与体以空行分隔
        if b"\r\n\r\n" not in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        head_text = head.decode("latin-1", errors="replace")
        # 解析 Content-Disposition
        name = None
        filename = None
        for line in head_text.split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition"):
                for seg in line.split(";"):
                    seg = seg.strip()
                    if seg.startswith("name="):
                        name = seg[5:].strip('"')
                    elif seg.startswith("filename="):
                        filename = seg[9:].strip('"')
        if name is None:
            continue
        # 去掉体末尾可能多出的 \r\n
        if value.endswith(b"\r\n"):
            value = value[:-2]
        result[name] = {"filename": filename, "value": value}
    return result
