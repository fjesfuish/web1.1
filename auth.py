# -*- coding: utf-8 -*-
"""后台管理登录鉴权:纯标准库实现的简易会话(支持多账号,带角色:超管/普通)。
- 账号存于 admin_users 表(每个账号独立 salt + 密码慢哈希)。
- 登录成功后下发随机 session token,服务端内存保存(token -> 用户名/过期),
  请求带 Cookie 校验。
"""
import hashlib
import os
import secrets
import time

import db

SESSION_TTL = 8 * 3600             # 会话有效期(秒)
COOKIE_NAME = "wip_admin"
PBKDF2_ROUNDS = 100000             # 密码哈希迭代次数(慢哈希,抗暴力/彩虹表)

# 默认超管账号:源码中不存放任何默认账号口令。
# 首次部署需创建超管时,显式设置环境变量 WIP_DEFAULT_ADMIN=1,
# 并同时设置 WIP_DEFAULT_ADMIN_USER 与 WIP_DEFAULT_ADMIN_PASS
# (口令须满足注册校验:≥8 位且含字母+数字);创建后强制首登改密,
# 建好后应立即取消这两个环境变量并重启。
# 线上正常运行不创建任何默认账号(用各自的账号登录,不留通用默认管理员=更安全)。

# 内存会话表:token -> (用户名, 过期时间戳)
_sessions = {}


def _hash(pw, salt):
    """PBKDF2-HMAC-SHA256 慢哈希,输出带算法/迭代前缀,便于校验兼容与日后调参。"""
    dk = hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"),
                             (salt or "").encode("utf-8"), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${dk.hex()}"


def _verify_hash(pw, salt, stored):
    """校验密码。兼容旧的单次 sha256 哈希(无前缀);用 compare_digest 抗时序侧信道。"""
    stored = stored or ""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, hexdk = stored.split("$", 2)
            dk = hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"),
                                     (salt or "").encode("utf-8"), int(rounds))
            return secrets.compare_digest(dk.hex(), hexdk)
        except (ValueError, TypeError):
            return False
    # 旧格式:单次 sha256(salt:pw)
    old = hashlib.sha256(((salt or "") + ":" + (pw or "")).encode("utf-8")).hexdigest()
    return secrets.compare_digest(old, stored)


def ensure_default_account():
    """保证账号体系可用:
    1) 若 admin_users 已有账号,确保至少一个超管,然后返回;
    2) 否则尝试迁移旧的单账号(app_meta);
    3) 账号库仍为空:默认【不创建】任何账号(线上不留默认管理员);
       仅当显式设环境变量 WIP_DEFAULT_ADMIN=1 且同时给出
       WIP_DEFAULT_ADMIN_USER / WIP_DEFAULT_ADMIN_PASS 时,才创建默认超管并强制首登改密。
    """
    if db.count_admin_users() > 0:
        if db.count_super_admins() == 0:
            users = db.list_admin_users()
            if users:
                db.set_admin_role(users[0]["username"], "super")
        return
    old_user = db.meta_get("admin_user", "")
    old_salt = db.meta_get("admin_salt", "")
    old_pass = db.meta_get("admin_pass", "")
    if old_user and old_salt and old_pass:
        db.add_admin_user(old_user, old_salt, old_pass, role="super")
    elif os.environ.get("WIP_DEFAULT_ADMIN") == "1":
        _du = (os.environ.get("WIP_DEFAULT_ADMIN_USER") or "").strip()
        _dp = os.environ.get("WIP_DEFAULT_ADMIN_PASS") or ""
        if not _du or not _dp:
            raise RuntimeError("需同时设置 WIP_DEFAULT_ADMIN_USER 与 "
                               "WIP_DEFAULT_ADMIN_PASS 才能创建默认超管")
        _ok, _m = _valid_password(_dp)
        if not _ok:
            raise RuntimeError("默认超管口令不符合要求:" + _m)
        salt = secrets.token_hex(8)
        # 仅首次部署场景:按环境变量创建超管,首登强制改密
        db.add_admin_user(_du, salt, _hash(_dp, salt),
                          role="super", must_change_pw=1)


def verify(user, pw):
    """校验账号密码,成功返回 True,并对失败记数、成功清数(联动限速)。"""
    user = (user or "").strip()
    u = db.get_admin_user(user)
    if not u:
        mark_fail(user)     # 不存在的账号同样计数,防用户名枚举探测
        return False
    if not _verify_hash(pw or "", u["salt"], u["pass_hash"]):
        mark_fail(user)
        return False
    clear_fails(user)
    if not (u["pass_hash"] or "").startswith("pbkdf2_sha256$"):
        salt = secrets.token_hex(8)
        db.set_admin_password(user, salt, _hash(pw or "", salt))
    return True


# ---------- 登录失败限速(内存态,与登录会话同生命周期) ----------
# 连续失败 LOCK_AFTER 次锁定 LOCK_SECONDS 秒;按「用户名」与「来源 IP」两条轨
# 分别计数:单号防爆破(5 次)、多账号探测走 IP 轨(各账号各自计数+IP 累计,
# 但 is_locked 按任一轨命中即锁)。重启后计数清零(与 session 一致,可接受)。
LOCK_AFTER = 5
LOCK_SECONDS = 15 * 60
_fails = {}    # key -> [已失败次数, 锁定截止 epoch 秒(0=未锁)]


def _key_locked(key):
    e = _fails.get(key)
    return bool(e and e[1] and e[1] > time.time())


def is_locked(user, ip=""):
    """该用户名 或 该 IP 任一条轨处于锁定期即返回 True。"""
    u = (user or "").strip()
    return bool((u and _key_locked(u)) or (ip and _key_locked("ip:" + ip)))


def mark_fail(user, ip=""):
    """记一次失败;连续 LOCK_AFTER 次即锁定(计数跨解锁后重置)。"""
    now = time.time()
    for key in ([k for k in ((user or "").strip(),) if k] + (["ip:" + ip] if ip else [])):
        e = _fails.get(key)
        if e is None or (e[1] and e[1] <= now):
            e = [0, 0.0]
        e[0] += 1
        if e[0] >= LOCK_AFTER:
            e[1] = now + LOCK_SECONDS
        _fails[key] = e


def clear_fails(user, ip=""):
    """登录成功时清除该用户(及该 IP)的失败记录。"""
    u = (user or "").strip()
    for key in [k for k in _fails.keys() if k == u or (ip and k == "ip:" + ip)]:
        del _fails[key]


# ---------- 角色 / 权限 ----------
def user_role(username):
    """返回账号角色:'super' 或 'normal';不存在返回 'normal'。"""
    u = db.get_admin_user((username or "").strip())
    return (u or {}).get("role") or "normal"


def is_super(username):
    """是否超级管理员。"""
    return user_role(username) == "super"


def needs_pw_change(username):
    """该账号是否需强制改密(默认密码账号首次登录)。"""
    u = db.get_admin_user((username or "").strip())
    return bool(u and u.get("must_change_pw"))


def change_username(operator, new_name):
    """修改自己的账户名(超管用)。返回 (ok, msg, new_username)。
    token 不变,会话用户名同步更新,无需重新登录。"""
    operator = (operator or "").strip()
    new_name = (new_name or "").strip()
    ok, m = _valid_username(new_name)
    if not ok:
        return False, m, operator
    if not db.get_admin_user(operator):
        return False, "当前账号不存在", operator
    if new_name == operator:
        return False, "新账号名与当前相同", operator
    if db.get_admin_user(new_name):
        return False, "该账号名已存在", operator
    db.rename_admin_user(operator, new_name)
    rename_sessions(operator, new_name)
    return True, f"账号名已改为 {new_name}", new_name


def set_role(operator, username, role):
    """超管变更某账号角色。不能把最后一个超管降级。"""
    username = (username or "").strip()
    u = db.get_admin_user(username)
    if not u:
        return False, "账号不存在"
    role = "super" if role == "super" else "normal"
    if u.get("role") == "super" and role == "normal" and db.count_super_admins() <= 1:
        return False, "至少保留一个超级管理员"
    db.set_admin_role(username, role)
    drop_user_sessions(username)   # 角色变更后强制重新登录生效
    return True, f"已将 {username} 设为{'超级管理员' if role == 'super' else '管理员'}"


# ---------- 账号管理 ----------
def list_users():
    return db.list_admin_users()


def _valid_username(name):
    if not name:
        return False, "账号不能为空"
    if len(name) > 32:
        return False, "账号不超过 32 位"
    if not all(c.isalnum() or c in "_-.@" for c in name):
        return False, "账号仅支持字母、数字及 _ - . @"
    return True, ""


def _valid_password(pw):
    """密码强度:至少 8 位,且同时含字母与数字。"""
    if not pw or len(pw) < 8:
        return False, "密码至少 8 位"
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        return False, "密码需同时包含字母和数字"
    return True, ""


def add_user(username, pw, role="normal"):
    username = (username or "").strip()
    ok, m = _valid_username(username)
    if not ok:
        return False, m
    ok, m = _valid_password(pw)
    if not ok:
        return False, m
    if db.get_admin_user(username):
        return False, "账号已存在"
    role = "super" if role == "super" else "normal"
    salt = secrets.token_hex(8)
    db.add_admin_user(username, salt, _hash(pw, salt), role=role)
    return True, f"已添加账号 {username}({'超级管理员' if role == 'super' else '管理员'})"


def delete_user(operator, username):
    username = (username or "").strip()
    u = db.get_admin_user(username)
    if not u:
        return False, "账号不存在"
    if username == operator:
        return False, "不能删除当前登录的账号"
    if db.count_admin_users() <= 1:
        return False, "至少保留一个账号"
    if u.get("role") == "super" and db.count_super_admins() <= 1:
        return False, "至少保留一个超级管理员"
    db.delete_admin_user(username)
    drop_user_sessions(username)   # 同步踢掉该账号已有会话
    return True, f"已删除账号 {username}"


def reset_password(username, new_pw):
    """管理员重置某账号密码(无需原密码)。"""
    username = (username or "").strip()
    if not db.get_admin_user(username):
        return False, "账号不存在"
    ok, m = _valid_password(new_pw)
    if not ok:
        return False, m
    salt = secrets.token_hex(8)
    db.set_admin_password(username, salt, _hash(new_pw, salt))
    return True, f"已重置 {username} 的密码"


def change_password(username, old_pw, new_pw):
    """当前登录账号修改自己的密码(需校验原密码)。"""
    u = db.get_admin_user((username or "").strip())
    if not u:
        return False, "账号不存在"
    if not _verify_hash(old_pw or "", u["salt"], u["pass_hash"]):
        return False, "原密码错误"
    ok, m = _valid_password(new_pw)
    if not ok:
        return False, m
    if _verify_hash(new_pw, u["salt"], u["pass_hash"]):
        return False, "新密码不能与原密码相同"
    salt = secrets.token_hex(8)
    db.set_admin_password(u["username"], salt, _hash(new_pw, salt))
    return True, "密码已修改"


# ---------- 会话 ----------
def new_session(username):
    token = secrets.token_urlsafe(24)
    _sessions[token] = (username, time.time() + SESSION_TTL)
    return token


def valid_session(token):
    return bool(session_user(token))


def session_user(token):
    """返回 token 对应的登录用户名;无效/过期返回空串。"""
    if not token:
        return ""
    rec = _sessions.get(token)
    if not rec:
        return ""
    username, exp = rec
    if time.time() > exp:
        _sessions.pop(token, None)
        return ""
    return username


def drop_session(token):
    _sessions.pop(token, None)


def drop_user_sessions(username):
    """删除某账号的所有会话(改密/删号时调用)。"""
    for t in [t for t, (u, _e) in _sessions.items() if u == username]:
        _sessions.pop(t, None)


def rename_sessions(old, new):
    """账户改名后,把其在线会话的用户名同步更新(token 不变,无需重新登录)。"""
    for t, (u, e) in list(_sessions.items()):
        if u == old:
            _sessions[t] = (new, e)


def cookie_value(cookie_header, name):
    """从 Cookie 头取出指定名字的值,不存在返回空串(通用)。"""
    if not cookie_header:
        return ""
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return ""


def parse_cookie(cookie_header):
    """从 Cookie 头取出登录 token。"""
    return cookie_value(cookie_header, COOKIE_NAME)
