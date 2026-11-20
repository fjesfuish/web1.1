# -*- coding: utf-8 -*-
"""版本与更新信息(语义化版本 major.minor.patch)。
- 版本号存本地 version.json,改完代码后用命令进位:
    python version.py 大改   → 主版本+1(超大改动,V 后那位)  例 1.2.3 → 2.0.0
    python version.py 中改   → 次版本+1(大改动,第2位)        例 1.2.3 → 1.3.0
    python version.py 小改   → 修订+1(小改动,最后一位)       例 1.2.3 → 1.2.4
- 更新时间在每次进位时自动写入(取当前时间),侧边栏实时显示。
- 公司 / 部门固定显示。
"""
import json
import os
import datetime
import sys

VERSION_FILE = os.path.join(os.path.dirname(__file__), "version.json")

DEFAULT = {
    "major": 1, "minor": 0, "patch": 0,
    "updated_at": "2026-05-29 14:30:25",
    "company": "示例公司",
    "dept": "示例部门",
}


def _load():
    if not os.path.exists(VERSION_FILE):
        _save(DEFAULT)
        return dict(DEFAULT)
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            d = json.load(f)
        # 补齐缺失字段
        for k, v in DEFAULT.items():
            d.setdefault(k, v)
        return d
    except (ValueError, OSError):
        return dict(DEFAULT)


def _save(d):
    tmp = VERSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VERSION_FILE)


def get_version():
    """返回 {version, updated_at, company, dept} 供页面显示。"""
    d = _load()
    return {
        "version": f'v{d["major"]}.{d["minor"]}.{d["patch"]}',
        "updated_at": d.get("updated_at", ""),
        "company": d.get("company", ""),
        "dept": d.get("dept", ""),
    }


def bump(level):
    """进位并更新时间。level: 'major'/'minor'/'patch' 或 中文 大改/中改/小改。"""
    d = _load()
    m = {"大改": "major", "中改": "minor", "小改": "patch",
         "major": "major", "minor": "minor", "patch": "patch"}
    lv = m.get(str(level).strip())
    if not lv:
        raise ValueError("level 须为 大改/中改/小改(或 major/minor/patch)")
    if lv == "major":
        d["major"] += 1
        d["minor"] = 0
        d["patch"] = 0
    elif lv == "minor":
        d["minor"] += 1
        d["patch"] = 0
    else:
        d["patch"] += 1
    d["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save(d)
    return get_version()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        v = get_version()
        print(f'当前版本 {v["version"]} · 更新时间 {v["updated_at"]}')
        print("用法: python version.py 大改|中改|小改")
    else:
        nv = bump(sys.argv[1])
        print(f'已更新 → {nv["version"]} · {nv["updated_at"]}')
