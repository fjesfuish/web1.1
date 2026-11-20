# -*- coding: utf-8 -*-
"""SWR 查询:公盘上 PIE(工艺工程师)投放的「特殊作业要求 PDF」的匹配与预览。

工作方式:
- PIE 把《要求.pdf》放在共享盘目录,文件名一般含工单号或尾号(如 *60000003599.pdf);
- 用户输入工单号(*60000003599)或尾号(03599),按文件名子串匹配(大小写不敏感,
  输入去 * 与空格);文件名以输入开头的命中排在前,其余按修改时间降序;
- 只读扫描 .pdf;目录遍历限制深度与文件数,防止误配整盘导致卡死;
- 扫描结果 60 秒缓存;resolve() 校验相对路径,防止目录穿越。

【配置】SWR_ROOT = 下面一行:填公盘路径(保留 r'' 前缀写 Windows 路径):
    r"\\\\YOUR_FILE_SERVER\\\\swr"    (UNC 共享路径)
    r"Z:\\\\SWR"                  (已映射盘符)
留空 = 页面显示"未配置公盘路径"提示,不会报错。
"""
import os
import time

SWR_ROOT = r""    # ← 在这里填公盘路径(保留 r"" 前缀,如 r"\\\\YOUR_FILE_SERVER\\\\swr")

MAX_DEPTH = 3          # 扫描子目录深度(含根目录)
MAX_FILES = 20000      # 最多收集的 pdf 数量
SCAN_TTL = 60.0        # 扫描结果缓存秒数


def configured():
    return bool(SWR_ROOT and os.path.isdir(SWR_ROOT))


def _scan():
    """扫描 SWR_ROOT 下(含最多 MAX_DEPTH 层子目录)的全部 pdf,带 TTL 缓存。
    返回 [{name, rel(相对路径,swr/file 用), mtime, size}]。"""
    root = SWR_ROOT
    if not root or not os.path.isdir(root):
        return []
    cached = _scan_cache.get(root)
    if cached and time.time() - cached[0] < SCAN_TTL:
        return cached[1]
    files = []

    def walk(d, depth):
        if len(files) >= MAX_FILES:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if len(files) >= MAX_FILES:
                break
            try:
                if e.is_file(follow_symlinks=False):
                    if e.name.lower().endswith(".pdf"):
                        try:
                            st = e.stat()
                            files.append({"name": e.name,
                                          "rel": os.path.relpath(e.path, root),
                                          "mtime": st.st_mtime,
                                          "size": st.st_size})
                        except OSError:
                            pass
                elif e.is_dir(follow_symlinks=False) and depth < MAX_DEPTH:
                    walk(e.path, depth + 1)
            except OSError:
                continue

    walk(root, 0)
    _scan_cache[root] = (time.time(), files)
    return files


_scan_cache = {}   # root -> (ts, files)


def normalize(kw):
    """输入归一:去 * 前缀与空格,小写(匹配大小写不敏感)。"""
    return (kw or "").strip().replace("*", "").replace(" ", "").lower()


def search(kw):
    """按工单号/尾号匹配 pdf。返回 {name, rel, mtime, size} 列表:
    匹配位置越靠近文件名开头越靠前(*60000003599 的 03599 在第 2 位 → 最前;
    前缀命中位置 0 最优),同位置按修改时间降序;最多 200 条。
    提示:子串匹配会命中同样含该号的其它工单(如 03599 命中 103599),页面上
    以"文件名 + 修改时间"核对即可。"""
    key = normalize(kw)
    if not key or not configured():
        return []
    out = []
    for f in _scan():
        nm = f["name"].lower()
        pos = nm.find(key)
        if pos >= 0:
            f = dict(f)
            f["pos"] = pos
            out.append(f)
    out.sort(key=lambda x: (x["pos"], -x["mtime"], x["name"]))
    return out[:200]


def resolve(rel):
    """把前端传来的相对路径解析为 SWR_ROOT 下的 pdf 绝对路径。
    相对路径穿越(../)、非 pdf 后缀、文件不存在 → None(守卫,防目录穿越)。"""
    root = os.path.abspath(SWR_ROOT) if SWR_ROOT else None
    if not root or not isinstance(rel, str) or not rel.strip():
        return None
    p = os.path.abspath(os.path.join(root, rel.replace("\\", "/").lstrip("/")))
    if not p.startswith(root + os.sep):
        return None
    if not p.lower().endswith(".pdf"):
        return None
    if not os.path.isfile(p):
        return None
    return p
