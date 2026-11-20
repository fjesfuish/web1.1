# -*- coding: utf-8 -*-

import html as _html
import mes
import charts


NAV = [
    ("stats",     "📊 统计分析"),
    ("monitor",   "🔍 实时WIP"),
    ("gantt",     "📈 生产进度看板"),
    ("orders",    "📋 工单明细"),
    ("exceptions","⚠️ 异常记录"),
    ("done",      "✅ 已完成工单"),
    ("ct_ai",     "🧮 CT数据校准"),
    ("equip",     "🏭 设备能力"),
    ("swr",       "📄 SWR要求查询"),
    ("admin",     "⚙️ 后台管理"),
    ("health",    "🩺 健康检查"),
    ("help",      "📖 使用说明"),
]


# 全局快捷键 JS(普通字符串常量,避免 f-string 花括号转义):/ 聚焦搜索 /
# Esc 关闭弹窗 / 字母跳页(不在输入框时生效)
_SHORTCUTS_JS = """<script>
document.addEventListener('keydown',function(ev){
  var tag=(ev.target&&ev.target.tagName)||'';
  var inInput=(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||(ev.target&&ev.target.isContentEditable));
  if(ev.key==='Escape'){
    var masks=document.querySelectorAll('[id$="Mask"]');
    for(var i=0;i<masks.length;i++){ if(masks[i]&&masks[i].style&&masks[i].style.display!=='none'){ try{masks[i].click();}catch(e0){} break; } }
    return;
  }
  if(inInput) return;
  if(ev.key==='/'){
    ev.preventDefault();
    var f=document.querySelector('.mon-search input, #wip_search, .eq-search input, input[placeholder*="搜"]');
    if(!f){ var ins=document.querySelectorAll('input'); for(var i2=0;i2<ins.length;i2++){ if(ins[i2].id&&ins[i2].id.indexOf('search')>=0){ f=ins[i2]; break; } } }
    if(f){ f.focus(); f.select(); }
    return;
  }
  var k=ev.key.toLowerCase();
  var m={g:'gantt',s:'stats',m:'monitor',e:'equip',c:'ct_ai',o:'orders',x:'exceptions',d:'done',a:'admin',h:'help'}[k];
  if(m && !ev.ctrlKey && !ev.metaKey && !ev.altKey){ location.href='/'+m; }
});
</script>"""


# 页面自动跟新后台数据:轮询 /sync_ts 比对「最后同步时间」,只有数据真的
# 变了才重载 —— 不用盲等固定周期,数据一更新最多 1 分钟界面就跟上。
# 三种情况跳过本轮,避免打断操作:正在输入 / 弹窗开着 / 标签页在后台。
# __SYNC_AT__ 由 layout() 替换成本页加载时的同步时间。
_AUTOREFRESH_JS = """<script>
(function(){
  var POLL_MS = 60000;            // 每 1 分钟问一次「数据更新了吗」(只读,很轻)
  var PAGE_SYNC = "__SYNC_AT__";  // 本页加载时后台的最后同步时间
  function busy(){
    var a = document.activeElement;
    if(a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;   // 正在输入
    var ms = document.querySelectorAll('[id$="Mask"]');
    for(var i = 0; i < ms.length; i++){                                 // 弹窗遮罩可见
      var d = ms[i].style.display;
      if(d && d !== 'none') return true;
    }
    return false;
  }
  function tick(){
    if(document.hidden) return;   // 标签页在后台,等切回来再看
    if(busy()) return;            // 正在操作,本轮跳过
    fetch('/sync_ts').then(function(r){ return r.json(); }).then(function(d){
      if(d && d.at && d.at !== PAGE_SYNC) location.reload();   // 数据变了才刷
    }).catch(function(){});       // 网络抖动就跳过,下轮再试
  }
  setInterval(tick, POLL_MS);
})();
</script>"""


def layout(active, title, body):
    import version as _ver
    import db as _db
    import os as _os
    import time as _time
    v = _ver.get_version()
    _sysname = _db.CATEGORY_NAMES.get(_db.get_category(), "塑封系列NTO")
    nav_html = "".join(
        f'<a class="nav-item{" active" if key==active else ""}" href="/{key}">{label}</a>'
        for key, label in NAV
    )
    # MES 快照新鲜度:CSV mtime 距今。>5min 黄、>15min 红(提示检查 refresh_mes_loop.vbs),
    # 防止"页面在显示、数据已停更"的静默陈旧——这是内网单点数据泵的主要风险阵面。
    try:
        _age = max(0, _time.time() - _os.path.getmtime(mes.DEFAULT_CSV))
    except OSError:
        _fresh_html = '<div style="font-size:11px;color:#fca5a5;" title="未找到 MES数据库.csv">⚠️ 无 MES 快照文件</div>'
    else:
        _mins = int(_age // 60)
        if _age > 900:
            _fresh_html = (f'<div style="font-size:11px;color:#fca5a5;" title="MES 快照已超过 15 分钟未更新,'
                           f'请检查 refresh_mes_loop.vbs 是否运行(任务管理器→进程/refresh_mes.log)">'
                           f'⚠️ 数据已停更 {_mins} 分钟</div>')
        elif _age > 300:
            _fresh_html = (f'<div style="font-size:11px;color:#fbbf24;" '
                           f'title="超过 5 分钟未更新,正常约 60~120 秒一幕">⏱ 数据 {_mins} 分钟未更新</div>')
        else:
            _fresh_html = f'<div style="font-size:11px;color:#64748b;">快照 {_mins} 分钟前</div>'
    # 页面自动刷新:把"本页加载时的同步时间"烤进脚本,前端拿它跟 /sync_ts
    # 比对,只有后台真的同步过新数据才重载(避免盲目定时刷)。
    # 必须兜异常:数据库异常时不能连累页面渲染 —— 拿不到就留空,自动刷新
    # 自动退化为不刷,页面照常打开(原先 layout 只读内存,不碰库)。
    try:
        _sync_at = _db.meta_get("last_sync_at", "")
    except Exception:
        _sync_at = ""
    _autorefresh_html = _AUTOREFRESH_JS.replace(
        "__SYNC_AT__", _sync_at.replace("\\", "\\\\").replace('"', '\\"'))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)} · {_html.escape(_sysname)}</title>
<style>
* {{ box-sizing: border-box; margin:0; padding:0; }}
body {{ font-family:-apple-system,"Microsoft YaHei",sans-serif; background:#f1f5f9; color:#1e293b; }}
.app {{ display:flex; height:100vh; overflow:hidden; }}
.sidebar {{ width:200px; background:#0f172a; color:#cbd5e1; flex-shrink:0; padding:16px 0;
            height:100vh; position:sticky; top:0; overflow-y:auto;
            display:flex; flex-direction:column; }}
.brand {{ padding:0 18px 18px; border-bottom:1px solid #1e293b; }}
.brand b {{ color:#fff; font-size:16px; }}
.brand span {{ display:block; font-size:11px; color:#64748b; margin-top:4px; }}
.ver {{ margin-top:auto; padding:14px 18px 4px; border-top:1px solid #1e293b; }}
.ver .vlbl {{ font-size:11px; color:#64748b; }}
.ver .vno {{ font-size:15px; color:#7dd3fc; font-weight:700; letter-spacing:.5px; font-variant-numeric:tabular-nums; }}
/* —— 表格统一规范:吸顶表头+斑马纹+hover(普通 panel 表格) —— */
.panel table {{ border-collapse:collapse; }}
.panel thead th {{ position:sticky; top:0; background:#eef2f7; z-index:2; }}
.panel tbody tr td {{ border-bottom:1px solid #f1f5f9; }}
.panel tbody tr:nth-child(even) td {{ background:#fafbfd; }}
.panel tbody tr:hover td {{ background:#f0f6ff; }}
.ver .vtime {{ font-size:11px; color:#64748b; margin-top:4px; }}
.ver .vorg {{ font-size:10px; color:#475569; margin-top:8px; line-height:1.5; }}
.nav-item {{ display:block; padding:11px 18px; color:#cbd5e1; text-decoration:none; font-size:14px; transition:background .15s, color .15s; }}
.nav-item:hover {{ background:#1e293b; }}
.nav-item.active {{ background:#2563eb; color:#fff; border-radius:0 8px 8px 0; margin-right:12px; font-weight:600; }}
.main {{ flex:1; padding:24px 28px; overflow:auto; }}
.page-title {{ font-size:20px; font-weight:700; }}
.page-sub {{ font-size:13px; color:#64748b; margin:4px 0 20px; }}
.cards {{ display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin-bottom:18px; }}
.card {{ background:#fff; border-radius:10px; padding:14px 18px; box-shadow:0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.05); }}
.card .k {{ font-size:12px; color:#64748b; }}
.card .v {{ font-size:26px; font-weight:700; margin-top:4px; }}
.panel {{ background:#fff; border-radius:10px; padding:18px; box-shadow:0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.05); margin-bottom:18px; }}
.panel h3 {{ font-size:15px; margin-bottom:14px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.risk-row {{ background:#fef2f2; border-left:3px solid #ef4444; border-radius:6px; padding:10px 14px; margin-bottom:8px; font-size:13px; }}
.risk-row .no {{ font-weight:600; }}
.risk-row .note {{ color:#dc2626; font-size:12px; margin-top:3px; }}
.tag {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; }}
.tag-risk {{ background:#fee2e2; color:#dc2626; }}
.tag-normal {{ background:#e2e8f0; color:#475569; }}
.tag-run {{ background:#dbeafe; color:#2563eb; }}
.tag-done {{ background:#dcfce7; color:#16a34a; }}
.tag-delay {{ background:#fee2e2; color:#dc2626; }}
.tag-low {{ background:#fef9c3; color:#ca8a04; }}
.rnote {{ font-size:11px; color:#dc2626; margin-top:3px; }}
.orders-layout {{ display:grid; grid-template-columns:210px 1fr; gap:18px; align-items:stretch;
                  height:calc(100vh - 150px); }}
.orders-layout .filter {{ height:100%; overflow-y:auto; }}
.orders-table-panel {{ height:100%; display:flex; flex-direction:column; overflow:hidden; }}
.orders-table-scroll {{ flex:1; overflow:auto; margin-top:10px; }}
.admin-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; align-items:stretch;
               height:calc(100vh - 190px); margin-bottom:18px; }}
.admin-list-panel, .admin-form-panel {{ height:100%; display:flex; flex-direction:column; overflow:hidden; }}
.admin-list-scroll {{ flex:1; overflow:auto; margin-top:10px; }}
/* 工单列表表头固定,下方内容滚动 */
.admin-list-scroll thead th {{ position:sticky; top:0; z-index:2; background:#eef2f7; }}
.admin-form-scroll {{ flex:1; overflow:auto; margin-top:6px; }}
.ct-layout {{ display:grid; grid-template-columns:170px 1fr; gap:18px; align-items:start; }}
.ct-side {{ background:#fff; border-radius:10px; padding:8px; box-shadow:0 1px 3px rgba(0,0,0,.06);
            position:sticky; top:16px; max-height:calc(100vh - 120px); overflow-y:auto; }}
.ctm-item {{ display:block; padding:9px 12px; border-radius:7px; font-size:13px; color:#334155;
             text-decoration:none; cursor:pointer; margin-bottom:2px; }}
.ctm-item:hover {{ background:#f1f5f9; }}
.ctm-item.active {{ background:#2563eb; color:#fff; font-weight:600; }}
.ctm-item.active span {{ color:#dbeafe !important; }}
.ct-main {{ min-width:0; }}
/* 负责人工作量条形段:hover 动效,提示可点击 */
.wl-track {{ flex:1; display:flex; height:20px; border-radius:5px; background:#f1f5f9; align-items:stretch; justify-content:flex-start; }}
.wl-track > div {{ flex:none; }}
.wl-seg {{ height:100%; cursor:pointer; position:relative;
           transition:transform .15s ease, filter .15s ease, box-shadow .15s ease;
           transform-origin:center bottom; }}
.wl-seg:first-child {{ border-radius:5px 0 0 5px; }}
.wl-seg:last-child {{ border-radius:0 5px 5px 0; }}
.wl-seg:only-child {{ border-radius:5px; }}
.wl-seg:hover {{ transform:scaleY(1.5); filter:brightness(1.12) saturate(1.1);
                 box-shadow:0 3px 10px rgba(15,23,42,.25); z-index:2;
                 border-radius:5px; animation:wlpulse 1s ease-in-out infinite; }}
@keyframes wlpulse {{ 0%,100% {{ filter:brightness(1.12) saturate(1.1); }}
                      50% {{ filter:brightness(1.28) saturate(1.2); }} }}
.filter {{ background:#fff; border-radius:10px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,.06); font-size:13px; }}
.filter .search {{ width:100%; padding:7px 10px; border:1px solid #e2e8f0; border-radius:6px; font-size:13px; margin-bottom:14px; }}
.fgroup {{ margin-bottom:14px; }}
.ftitle {{ font-weight:600; color:#334155; margin-bottom:6px; font-size:12px; }}
.fopt {{ display:block; padding:3px 0; color:#475569; cursor:pointer; }}
.fopt input {{ margin-right:6px; vertical-align:middle; }}
.fbtns {{ display:flex; gap:8px; margin-top:6px; }}
.btn-primary {{ flex:1; background:#2563eb; color:#fff; border:none; padding:7px; border-radius:6px; cursor:pointer; font-size:13px; }}
.btn-reset {{ flex:1; text-align:center; background:#f1f5f9; color:#475569; padding:7px; border-radius:6px; text-decoration:none; font-size:13px; }}
.btn-export {{ display:inline-flex; align-items:center; padding:6px 13px; font-size:13px;
    color:#2563eb; background:#eff6ff; border:1px solid #bfdbfe; border-radius:7px;
    text-decoration:none; white-space:nowrap; transition:background .15s, border-color .15s; }}
.btn-export:hover {{ background:#2563eb; color:#fff; border-color:#2563eb; }}
/* 甘特图 */
.ghead {{ display:flex; border-bottom:1px solid #e2e8f0; margin-bottom:4px; }}
.gcol {{ flex-shrink:0; text-align:center; font-size:10px; color:#94a3b8; padding:4px 0; border-left:1px solid #f1f5f9; }}
.grow {{ display:flex; align-items:center; min-height:48px; border-bottom:1px solid #f8fafc; }}
.glabel {{ width:200px; flex-shrink:0; font-size:13px; padding-right:10px; }}
.gtrack {{ position:relative; height:24px; flex-shrink:0; }}
.gbar {{ position:absolute; top:2px; height:20px; border-radius:4px; overflow:hidden; }}
.gfill {{ height:100%; opacity:.85; }}
/* 工单表单 */
.wo-form .frow {{ display:flex; align-items:center; margin-bottom:9px; font-size:13px; }}
.wo-form label {{ width:72px; flex-shrink:0; color:#64748b; }}
.wo-form input, .wo-form select {{ flex:1; padding:6px 9px; border:1px solid #e2e8f0; border-radius:6px; font-size:13px; }}
.op-edit {{ color:#2563eb; text-decoration:none; margin-right:8px; font-size:12px; }}
.op-del {{ background:none; border:none; color:#dc2626; cursor:pointer; font-size:12px; padding:0; }}
/* 基础数据标签 */
.btags {{ display:flex; flex-wrap:wrap; gap:8px; }}
.btag {{ background:#eff6ff; color:#2563eb; padding:4px 6px 4px 10px; border-radius:6px; font-size:13px; display:inline-flex; align-items:center; }}
.btag-x {{ background:none; border:none; color:#94a3b8; cursor:pointer; font-size:14px; padding:0 2px; margin-left:2px; }}
.btag-x:hover {{ color:#dc2626; }}
.flash {{ background:#dcfce7; color:#16a34a; padding:10px 16px; border-radius:8px; margin-bottom:16px; font-size:13px; }}
.mes-wo {{ border:1px solid #f1f5f9; border-radius:8px; margin-bottom:6px; }}
.mes-wo summary {{ padding:10px 14px; cursor:pointer; font-size:13px; list-style:none; }}
.mes-wo summary::-webkit-details-marker {{ display:none; }}
.mes-wo summary:hover {{ background:#f8fafc; }}
.mes-wo[open] summary {{ border-bottom:1px solid #f1f5f9; background:#f8fafc; }}
.bar-bg {{ background:#e2e8f0; border-radius:4px; height:6px; overflow:hidden; margin-top:6px; }}
.bar-fg {{ background:#2563eb; height:100%; }}
.legend {{ font-size:12px; color:#64748b; text-align:center; margin-top:8px; }}
.legend i {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin:0 4px 0 12px; vertical-align:middle; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid #f1f5f9; }}
th {{ color:#64748b; font-weight:600; font-size:12px; }}
tr:hover td {{ background:#f8fafc; }}
/* —— 风险卡片增强:小色点 + 底部迷你占比条 —— */
.card .k {{ display:flex; align-items:center; gap:6px; }}
.cdot {{ width:8px; height:8px; border-radius:50%; flex:0 0 auto; box-shadow:0 0 0 3px rgba(0,0,0,.04); }}
.cbar {{ height:4px; background:#f1f5f9; border-radius:2px; overflow:hidden; margin-top:10px; }}
.cbar > div {{ height:100%; border-radius:2px; transition:width .5s cubic-bezier(.4,0,.2,1); }}
/* —— 页面/面板入场过渡,消除白屏突现 —— */
.main {{ animation:pageIn .26s ease both; }}
@keyframes pageIn {{ from {{ opacity:0; transform:translateY(6px); }} to {{ opacity:1; transform:none; }} }}
.panel, .cards {{ animation:fadeUp .32s ease both; }}
.cards {{ animation-delay:.02s; }}
.grid2 > .panel:nth-child(2) {{ animation-delay:.06s; }}
@keyframes fadeUp {{ from {{ opacity:0; transform:translateY(8px); }} to {{ opacity:1; transform:none; }} }}
/* —— 轻量加载 spinner(供甘特图/WIP 渲染前占位) —— */
.spin {{ width:22px; height:22px; border:3px solid #e2e8f0; border-top-color:#2563eb; border-radius:50%;
         display:inline-block; animation:spin .7s linear infinite; vertical-align:middle; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
.loadbox {{ padding:48px 20px; text-align:center; color:#94a3b8; font-size:13px; }}
/* —— 友好空状态 —— */
.empty {{ padding:46px 20px; text-align:center; color:#94a3b8; }}
.empty .ico {{ font-size:42px; line-height:1; margin-bottom:12px; opacity:.65; }}
.empty .tt {{ font-size:15px; color:#475569; font-weight:600; }}
.empty .sb {{ font-size:13px; margin-top:6px; }}
a, span[onclick] {{ text-decoration: none !important; }}
a:hover {{ text-decoration: none !important; }}
</style></head>
<body>
<script>function wipToast(t,type){{var e=document.getElementById('wipToast');if(!e){{e=document.createElement('div');e.id='wipToast';e.style.cssText='position:fixed;top:18px;left:50%;transform:translate(-50%,0);color:#fff;font-size:13px;padding:10px 22px;border-radius:8px;z-index:999;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .2s,background .2s;pointer-events:none;max-width:80vw;';document.body.appendChild(e);}}var bg={{ok:'#15803d',err:'#b91c1c',warn:'#b45309',info:'#0f172a'}}[type]||'#0f172a';e.style.background=bg;e.textContent=t;e.style.opacity=1;clearTimeout(e._t);e._t=setTimeout(function(){{e.style.opacity=0;}},4000);}}</script>
{_SHORTCUTS_JS}
{_autorefresh_html}

<div class="app">
<div class="sidebar">
  <div class="brand"><b>{_html.escape(_sysname)}</b>
    <span>工单排产看板 · <a href="/select" style="color:#7dd3fc;text-decoration:none;">切换类别 ⇄</a></span></div>
  {nav_html}
  <div style="padding:8px 18px;">
    <button id="refresh_btn" onclick="var b=document.getElementById('refresh_btn');b.textContent='⏳ 同步中…';b.disabled=true;fetch('/refresh').then(function(r){{return r.json().then(function(d){{return {{_d:d}};}}).catch(function(){{return {{_d:null}};}});}}).then(function(x){{var d=x&&x._d;if(d&&d.ok){{wipToast(d.msg);location.reload(true);}}else{{wipToast(d&&d.msg||'同步失败(未登录或服务异常,登录后重试)');b.textContent='🔄 刷新数据';b.disabled=false;}}}}).catch(function(){{wipToast('同步失败(未登录或服务异常,登录后重试)');b.textContent='🔄 刷新数据';b.disabled=false;}});" title="触发一次同步(与后台「数据源→立即刷新」相同)"
            style="width:100%;padding:8px 0;font-size:13px;font-weight:600;
                   background:#1e293b;color:#7dd3fc;border:1px solid #334155;
                   border-radius:8px;cursor:pointer;
                   transition:background .15s,color .15s,transform .1s;"
            onmouseover="this.style.background='#2563eb';this.style.color='#fff';this.style.borderColor='#2563eb'"
            onmouseout="this.style.background='#1e293b';this.style.color='#7dd3fc';this.style.borderColor='#334155'"
            onmousedown="this.style.transform='scale(.97)'"
            onmouseup="this.style.transform='scale(1)'">🔄 刷新数据</button>
    <div style="text-align:center;margin-top:6px;">{_fresh_html}</div>
  </div>
  <div class="ver">
    <div class="vlbl">最新版本</div>
    <div class="vno">{_html.escape(v["version"].upper())}</div>
  </div>
</div>
<div class="main">{body}</div>
</div>
<script>
function escapeHtml(s){{if(s==null)return '';return String(s).replace(/[&<>"']/g,function(c){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c];}});}}
// 备注文本框实时自适应:宽随最长行(16~36字符)、高随行数(34~200px),输入即变
function _noteAutosize(ta){{
  if(!ta) return;
  var v=ta.value||'', maxLen=0;
  v.split('\\n').forEach(function(s){{ if(s.length>maxLen) maxLen=s.length; }});
  ta.style.width=Math.min(36, Math.max(16, maxLen+2))+'ch';
  ta.style.height='auto';
  ta.style.height=Math.min(200, Math.max(34, ta.scrollHeight+2))+'px';
}}
</script>
</body></html>"""


# ---------- 工单明细页 ----------
def _status_tag(s):
    """工单状态徽标。现在状态=MES实时状态,按状态着色:
    等待进站=黄,等待出站=淡蓝,运行中=绿,HOLD=红,出站/入库=蓝。
    """
    s = (s or "").strip()
    color_map = {
        "等待进站": ("#fde047", "#1f2937"),
        "等待出站": ("#60a5fa", "#1f2937"),
        "运行中":   ("#bbf7d0", "#15803d"),
        "HOLD":     ("#fecaca", "#1f2937"),
        "出站":     ("#bfdbfe", "#1f2937"),
        "已出站":   ("#bfdbfe", "#1f2937"),
        "已入库":   ("#bfdbfe", "#1f2937"),
    }
    bg, fg = color_map.get(s, ("#e2e8f0", "#475569"))
    return (f'<span style="padding:2px 8px;border-radius:4px;font-size:12px;'
            f'background:{bg};color:{fg};white-space:nowrap;">{_html.escape(s)}</span>')


def _prio_tag(p):
    # 与「生产进度看板」保持一致:火箭批红底黄字 / 1-0红底黑字 / 1-1黄底黑字 / Normal浅蓝底黑字
    p = (p or "").strip()
    colors = {
        "火箭批": ("#dc2626", "#fde047"),
        "1-0":    ("#dc2626", "#1f2937"),
        "1-1":    ("#fde047", "#1f2937"),
        "Normal": ("#bfdbfe", "#1f2937"),
    }
    bg, fg = colors.get(p, ("#e2e8f0", "#475569"))
    return (f'<span class="tag" style="background:{bg};color:{fg};font-weight:600;">'
            f'{_html.escape(p)}</span>')


def _risk_tag(r):
    cls = {"高风险": "tag-risk", "低风险": "tag-low",
           "超前": "tag-done", "正常": "tag-normal"}.get(r, "tag-normal")
    return f'<span class="tag {cls}">{_html.escape(r or "")}</span>'


def _risk_note_color(risk_level):
    """风险提示文字颜色:高风险/滞后=红,低风险=黄,超前=绿,正常=黑。"""
    return {
        "高风险": "#dc2626",   # 红
        "低风险": "#ca8a04",   # 黄
        "超前":   "#16a34a",   # 绿
        "正常":   "#1f2937",   # 黑
    }.get(risk_level, "#1f2937")


def _risk_note_html(note, show_eta=True):
    """风险提示文字转 HTML:把「预计完工 …」拆到下一行显示(其余保持一行)。
    show_eta=False(匿名浏览者):隐藏「预计完工 …」预测,与甘特图对匿名隐藏虚线框一致。"""
    esc = _html.escape(note or "")
    # risk_note 形如「剩余需X天 / 距交期Y天 / 超前Z天 / 预计完工 YYYY-MM-DD」
    if not show_eta:
        idx = esc.find(" / 预计完工")        # 去掉尾部预测完工日整段
        return esc[:idx] if idx >= 0 else esc
    return esc.replace(" / 预计完工", "<br>预计完工")


def _slack_attr(note):
    """从 risk_note 文本解析富余天数,作工单明细「按风险排序」的二级键。
    超前=正,滞后=负;无明确天数(已完成/不在CT表)返回空串 → 前端按 +∞ 排末尾。"""
    import re
    if note:
        m = re.search(r"滞后\s*([\d.]+)\s*天", note)
        if m:
            return f"-{m.group(1)}"
        m = re.search(r"超前\s*([\d.]+)\s*天", note)
        if m:
            return m.group(1)
    return ""


def _mes_badge(mes_status):
    """当前工序旁的 MES 来源标记,按状态着色。
    等待进站=黄底黑字,运行中=绿底,HOLD=红底,出站/MoveOut=蓝底。
    """
    m = (mes_status or "").strip()
    if not m:
        return ""
    if m == "无MES数据":
        return ('<span style="font-size:10px;color:#94a3b8;'
                'padding:1px 6px;background:#f1f5f9;border-radius:3px;">无MES数据</span>')
    if m == "无MES数据·已销账":   # 中途销账离场=已完成
        return ('<span style="font-size:10px;color:#15803d;'
                'padding:1px 6px;background:#dcfce7;border-radius:3px;'
                'white-space:nowrap;">已销账·完成</span>')
    # 已同步·xxx → 取状态文字
    detail = m.split("·", 1)[1] if "·" in m else m
    d = detail.strip()
    # 配色:背景, 文字
    color_map = {
        "等待进站": ("#fde047", "#1f2937"),   # 黄底黑字
        "等待出站": ("#60a5fa", "#1f2937"),   # 蓝底黑字
        "运行中":   ("#bbf7d0", "#15803d"),   # 绿底绿字
        "HOLD":     ("#fecaca", "#1f2937"),   # 红底黑字
        "出站":     ("#bfdbfe", "#1f2937"),   # 蓝底黑字
        "已出站":   ("#bfdbfe", "#1f2937"),
        "MoveOut":  ("#bfdbfe", "#1f2937"),
        "已入库":   ("#bfdbfe", "#1f2937"),
    }
    bg, fg = color_map.get(d, ("#dcfce7", "#16a34a"))
    return (f'<span style="font-size:10px;color:{fg};'
            f'padding:1px 6px;background:{bg};border-radius:3px;white-space:nowrap;">'
            f'{_html.escape(d)}</span>')


def _filter_group(title, name, values, current):
    """渲染一组单选筛选项(含"全部"选项)。current 为当前选中值。"""
    items = [('', '全部')] + [(v, v) for v in values]
    radios = "".join(
        f'<label class="fopt"><input type="radio" name="{name}" value="{_html.escape(v)}"'
        f'{" checked" if v == (current or "") else ""} onchange="this.form.submit()"> '
        f'{_html.escape(label)}</label>'
        for v, label in items
    )
    return f'<div class="fgroup"><div class="ftitle">{title}</div>{radios}</div>'


def _detail_modal():
    """工单详情弹层(HTML+JS),供工单明细页和甘特图页共用。"""
    return """
    <div id="detailMask" onclick="hideDetail()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:50;"></div>
    <div id="detailBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:1100px;max-width:94vw;height:78vh;background:#fff;border-radius:12px;
         z-index:51;box-shadow:0 10px 40px rgba(0,0,0,.2);display:none;flex-direction:column;">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:18px 22px 12px;border-bottom:1px solid #eef2f7;flex:0 0 auto;">
        <h3 id="detailTitle" style="margin:0;">工单详情</h3>
        <button onclick="hideDetail()" style="border:none;background:#f1f5f9;border-radius:6px;padding:6px 12px;cursor:pointer;">关闭</button>
      </div>
      <div id="detailBody" style="flex:1 1 auto;overflow:auto;padding:16px 22px;">加载中…</div>
    </div>
    <script>
    function hideDetail() {
      document.getElementById('detailMask').style.display='none';
      document.getElementById('detailBox').style.display='none';
      if(window._detailReturn==='owner'){
        window._detailReturn='';
        var ob=document.getElementById('ownerBox');
        if(ob){ document.getElementById('ownerMask').style.display='block'; ob.style.display='flex'; }
      }
    }
    function showDetail(wo) {
      document.getElementById('detailMask').style.display='block';
      document.getElementById('detailBox').style.display='flex';
      window._detailWo=wo; document.getElementById('detailTitle').textContent='工单详情 工单号：'+wo;
      var bd=document.getElementById('detailBody'); bd.innerHTML='加载中…';
      fetch('/order_detail?wo='+encodeURIComponent(wo))
        .then(function(r){return r.json();})
        .then(function(d){ renderDetail(bd, d); })
        .catch(function(){bd.innerHTML='<span style="color:#dc2626;">加载失败</span>';});
    }
    function renderDetail(bd, d) {
      if(!d.variants||d.variants.length===0){bd.innerHTML='<span style="color:#94a3b8;">MES 中无该工单的 lot 数据</span>';return;}
      {var tt=document.getElementById('detailTitle'); tt.textContent=(d.product?(' 型号：'+d.product):'')+' 工单号：'+(window._detailWo||'');}
      var h='<div style="font-size:13px;color:#64748b;margin-bottom:10px;">'+escapeHtml(d.key)+' 共 '+d.variants.length+' 个分身(0/E/F首位)</div>';
      h += buildProcChart(d);   // 工序/Lot 分布图(X=工序,Y=Lot数)
      var thStyle='text-align:center;padding:5px 8px;';
      d.variants.forEach(function(v){
        h+='<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:12px;overflow:hidden;">';
        var vstat = v.qty_status || v.cur_status;
        h+='<div style="padding:8px 12px;background:#f8fafc;font-size:13px;font-weight:600;"> ('+escapeHtml(v.first)+'工单) '+escapeHtml(v.full_wo)+' 产品型号：'+escapeHtml(v.product_name)+'</div>';
        h+='<table style="width:100%;font-size:12px;border-collapse:collapse;table-layout:fixed;">';
        h+='<colgroup>'
          +'<col style="width:6%;"><col style="width:12%;"><col style="width:15%;">'
          +'<col style="width:15%;"><col style="width:8%;"><col style="width:8%;">'
          +'<col style="width:7%;"><col style="width:7%;"><col style="width:7%;"><col style="width:6%;"><col style="width:7%;"><col style="width:13%;"><col style="width:9%;">'
          +'</colgroup>';
        h+='<tr style="color:#64748b;background:#fafbfc;">'
          +'<td style="'+thStyle+'">步骤</td>'
          +'<td style="'+thStyle+'">Lot ID</td>'
          +'<td style="'+thStyle+'">工序(SPECNAME)</td>'
          +'<td style="'+thStyle+'">描述</td>'
          +'<td style="'+thStyle+'">状态</td>'
          +'<td style="'+thStyle+'">EQP ID</td>'
          +'<td style="'+thStyle+'">Run Time</td>'
          +'<td style="'+thStyle+'">Wait Time</td>'
          +'<td style="'+thStyle+'">Hold Time</td>'
          +'<td style="'+thStyle+'">数量</td>'
          +'<td style="'+thStyle+'white-space:nowrap;">HOLD原因</td>'
          +'<td style="'+thStyle+'">MES备注</td>'
          +'<td style="'+thStyle+'">负责人</td></tr>';
        v.processes.forEach(function(p){
          var lots = p.lot_list || [];
          var tdC='text-align:center;vertical-align:middle;padding:5px 8px;word-break:break-all;';
          if (lots.length === 0) {
            h+='<tr style="border-top:1px solid #f1f5f9;"><td style="'+tdC+'">'+escapeHtml(p.seq)+'</td><td style="'+tdC+'">-</td><td style="'+tdC+'">'+escapeHtml(p.spec)+'</td><td style="'+tdC+'">'+escapeHtml(p.desc)+'</td><td colspan="9" style="'+tdC+'color:#94a3b8;">无 lot 明细</td></tr>';
            return;
          }
          lots.forEach(function(l, idx){
            h+='<tr style="border-top:1px solid #f1f5f9;">';
            if (idx===0) {
              h+='<td style="'+tdC+'" rowspan="'+lots.length+'">'+escapeHtml(p.seq)+'</td>';
            }
            h+='<td style="'+tdC+'">'+escapeHtml(l.lot_id)+'</td>';
            if (idx===0) {
              h+='<td style="'+tdC+'" rowspan="'+lots.length+'">'+escapeHtml(p.spec)+'</td>';
              h+='<td style="'+tdC+'" rowspan="'+lots.length+'">'+escapeHtml(p.desc)+'</td>';
            }
            h+='<td style="'+tdC+'">'+statusBadge(l.status)+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.eqpld)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.run_time)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.wait_time)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.hold_time)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+escapeHtml(l.qty)+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.hold_reason)||'-')+'</td>';
            h+='<td style="text-align:left;vertical-align:middle;padding:5px 8px;word-break:break-word;color:#334155;">'+(escapeHtml(l.txn_comment)||'-')+'</td>';
            h+='<td style="'+tdC+'font-weight:600;">'+(escapeHtml(l.owner)||'-')+'</td>';
            h+='</tr>';
          });
        });
        h+='</table></div>';
      });
      bd.innerHTML=h;
      // 弹窗宽度随工艺路线长短自适应(柱状图不滚动):工序多则加宽(不设上限,封顶到屏宽);
      // 工序短时不低于最小宽度,保证窗口美观、下方工单表格各列完整。
      var box=document.getElementById('detailBox');
      if(box){
        var nSt=(d.ct_specs&&d.ct_specs.length)?d.ct_specs.length:0;
        var maxW=Math.floor((window.innerWidth||1200)*0.94);
        // 宽度下限(美观):取屏宽 70% 与 1100 的较大者,且不超过屏宽上限
        var minW=Math.min(maxW, Math.max(1100, Math.floor((window.innerWidth||1200)*0.7)));
        var want=nSt? (nSt*17+80) : 1080;    // 每站约17px + 容器留白
        box.style.width=Math.max(minW, Math.min(maxW, want))+'px';
      }
    }
    function statusBadge(s) {
      var map={'等待进站':['#fde047','#1f2937'],'等待出站':['#60a5fa','#1f2937'],'运行中':['#bbf7d0','#15803d'],'HOLD':['#fecaca','#1f2937'],'出站':['#bfdbfe','#1f2937'],'已出站':['#bfdbfe','#1f2937'],'已入库':['#bfdbfe','#1f2937']};
      var c=map[s]||['#f1f5f9','#475569'];
      return '<span style="padding:1px 6px;border-radius:3px;background:'+c[0]+';color:'+c[1]+';white-space:nowrap;">'+escapeHtml(s)+'</span>';
    }
    // 工序/Lot 分布柱状图:X=完整工艺路线(CT表全工序,含产品未到达/已过的),Y=Lot数(按状态堆叠)。
    // 一眼看出产品堆在哪几站、前后哪些工序还没作业。
    function buildProcChart(d) {
      // 工序 / Lot 分布图(与「统计分析·产品在制WIP」同款效果:青底网格+双轴+CWIP黑线+跟随光标浮层)
      var agg={};
      function bump(spec,name,status,ignored,qty){
        var m=agg[spec]||(agg[spec]={name:name||spec,run:0,wi:0,wo:0,hold:0,out:0,other:0,total:0,ig:0,qty:0});
        if(name&&(!m.name||m.name===spec)) m.name=name;
        var s=(status||'').trim();
        if(s==='运行中') m.run++;
        else if(s==='等待进站') m.wi++;
        else if(s==='等待出站') m.wo++;
        else if(s==='HOLD') m.hold++;
        else if(s==='出站'||s==='已出站'||s==='已入库') m.out++;
        else m.other++;
        m.total++;
        if(ignored) m.ig++;
        else { var q=parseInt(qty,10); m.qty+=(isNaN(q)?0:q); }   // CWIP 累计用颗数,排除已忽略(=报废)
      }
      (d.variants||[]).forEach(function(v){
        (v.processes||[]).forEach(function(p){
          (p.lot_list||[]).forEach(function(l){ bump(p.spec,(p.desc||p.spec),l.status,l.ignored,l.qty); });
        });
      });
      // X 轴骨架:优先完整 CT 工艺路线;CT 表没有但实际有 lot 的工序(虚拟站)追加末尾
      var skel=[];
      if(d.ct_specs && d.ct_specs.length){
        var inCt={};
        d.ct_specs.forEach(function(c){ skel.push({spec:c.spec,name:(c.name||c.spec)}); inCt[c.spec]=1; });
        Object.keys(agg).forEach(function(sp){ if(!inCt[sp]) skel.push({spec:sp,name:agg[sp].name||sp,virt:true}); });
      } else {
        (d.variants||[]).forEach(function(v){ (v.processes||[]).forEach(function(p){
          if(!skel.some(function(x){return x.spec===p.spec;})) skel.push({spec:p.spec,name:(p.desc||p.spec)});
        }); });
      }
      if(!skel.length) return '';
      var maxV=1, lotTotal=0, firstLotIdx=-1;
      skel.forEach(function(x,i){ var m=agg[x.spec]; if(m&&m.total>0&&!x.virt){ if(m.total>maxV) maxV=m.total; lotTotal+=m.total; if((m.total-m.ig)>0 && firstLotIdx<0) firstLotIdx=i; } });
      // CWIP:该工序及之后所有在制【颗数】之和(从右往左累加),不含已忽略
      var cwip=[], rsum=0;
      for(var ci=skel.length-1;ci>=0;ci--){ var mm=agg[skel[ci].spec]; rsum+=(mm?mm.qty:0); cwip[ci]=rsum; }
      var maxC=cwip.length?cwip[0]:1; if(maxC<1) maxC=1;
      var H=240, N=skel.length;
      function _sw(c){ return '<i style=\\'display:inline-block;width:11px;height:11px;border-radius:2px;background:'+c+';\\'></i>'; }
      function _ln(c){ return '<i style=\\'display:inline-block;width:15px;border-top:2px solid '+c+';position:relative;top:-3px;\\'></i>'; }
      function _row(mk,v){ return '<div style=\\'display:flex;align-items:center;gap:8px;padding:1px 0;\\'>'+mk+'<span style=\\'font-weight:600;\\'>'+v+'</span></div>'; }
      var bars='';
      skel.forEach(function(x,i){
        var m=agg[x.spec]||{run:0,wi:0,wo:0,hold:0,out:0,other:0,total:0,ig:0,qty:0}, has=m.total>0;
        var valid=m.total-m.ig, skipped=!!x.virt;
        var ignored=!skipped&&has&&valid<=0;
        var passed=!skipped&&(valid<=0)&&firstLotIdx>=0&&(i<firstLotIdx)&&!ignored;
        var greyHatch=skipped||passed;
        var cellBg=ignored?'background:repeating-linear-gradient(45deg,#fdf2f8,#fdf2f8 3px,#fbcfe8 3px,#fbcfe8 6px);'
                  :greyHatch?'background:repeating-linear-gradient(45deg,#f1f5f9,#f1f5f9 3px,#e2e8f0 3px,#e2e8f0 6px);':'';
        function seg(n,color){ if(n<=0) return ''; var hh=Math.max(2,n/maxV*H); return '<div style="width:100%;height:'+hh+'px;background:'+color+';"></div>'; }
        var stack=skipped?'':(seg(m.run,'#22c55e')+seg(m.wi,'#ffdd00')+seg(m.wo,'#ffdd00')+seg(m.hold,'#ef4444')+seg(m.out,'#3b82f6')+seg(m.other,'#94a3b8'));
        if(ignored) stack='<div style="width:100%;opacity:.45;">'+stack+'</div>';
        var qwip=m.wi+m.wo;
        var _ti='<div style=\\'font-weight:700;margin-bottom:4px;border-bottom:1px solid #e2e8f0;padding-bottom:3px;\\'>'+escapeHtml(x.name)+'</div>';
        var tip = skipped ? _ti+'<div style=\\'color:#94a3b8;\\'>非CT虚拟站 · 略过</div>'
                : has ? _ti+(m.run?_row(_sw('#22c55e'),m.run+' 运行中'):'')+(qwip?_row(_sw('#ffdd00'),qwip+' 等待'):'')+(m.hold?_row(_sw('#ef4444'),m.hold+' HOLD'):'')+(m.out?_row(_sw('#3b82f6'),m.out+' 已出站'):'')+(m.ig?_row(_sw('#cbd5e1'),m.ig+' 已忽略'):'')+_row(_ln('#0f172a'),cwip[i]+' 颗 (累计)')+(ignored?'<div style=\\'margin-top:3px;font-size:10px;color:#be185d;\\'>该工序LOT已全部忽略</div>':'')
                : _ti+'<div style=\\'color:#94a3b8;\\'>'+(passed?'尾批已跑完':'暂无在制 lot')+'</div>'+_row(_ln('#0f172a'),cwip[i]+' 颗 (累计)');
        var nameCol=ignored?'#be185d':(greyHatch?'#94a3b8':(has?'#0f172a':'#94a3b8'));
        bars += '<div class="pseg" data-tip="'+tip+'" style="display:flex;flex-direction:column;align-items:center;flex:1 1 0;min-width:0;cursor:default;">'
              + '<div class="pbar" style="display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:'+H+'px;width:80%;max-width:20px;border-bottom:1px solid #cbd5e1;overflow:visible;'+cellBg+'">'
                  + ((has&&!skipped)?('<div style="font-size:11px;color:'+(ignored?'#be185d':'#0f172a')+';line-height:1.1;text-align:center;white-space:nowrap;margin-bottom:1px;font-weight:600;">'+m.total+'</div>'):'')
                  + '<div style="width:100%;display:flex;flex-direction:column;">'+stack+'</div>'
              + '</div>'
              + '<div title="'+escapeHtml(x.name)+'" style="font-size:'+(has?'12px':'10px')+';writing-mode:vertical-rl;text-orientation:mixed;height:110px;overflow:hidden;margin-top:3px;white-space:nowrap;font-weight:'+(has?'700':'500')+';color:'+nameCol+';">'+escapeHtml(x.name)+'</div>'
              + '</div>';
      });
      var pts=cwip.map(function(c,i){ var y=H-(c/maxC*H); return (i+0.5)+','+y.toFixed(1); }).join(' ');
      var svg='<svg viewBox="0 0 '+N+' '+H+'" preserveAspectRatio="none" style="position:absolute;left:0;top:0;width:100%;height:'+H+'px;pointer-events:none;overflow:visible;">'
            + '<polyline points="'+pts+'" fill="none" stroke="#0f172a" stroke-width="1.4" vector-effect="non-scaling-stroke" opacity="0.7"/></svg>';
      var ticks=5, yaxis='', raxis='', grid='';
      for(var hi=0;hi<=10;hi++){ var hy=H*hi/10; grid+='<div style="position:absolute;left:0;right:0;top:'+hy+'px;border-top:1px dashed rgba(120,120,120,.25);"></div>'; }
      for(var vi=0;vi<=N;vi++){ var vx=vi/N*100; grid+='<div style="position:absolute;top:0;left:'+vx+'%;height:'+H+'px;border-left:1px dashed rgba(120,120,120,.25);"></div>'; }
      for(var k=0;k<ticks;k++){ var frac=k/(ticks-1), gy=H*frac;
        yaxis+='<div style="position:absolute;right:4px;top:'+(gy-6)+'px;font-size:9px;color:#334155;line-height:1;">'+Math.round(maxV*(1-frac))+'</div>';
        raxis+='<div style="position:absolute;left:4px;top:'+(gy-6)+'px;font-size:9px;color:#0f172a;line-height:1;">'+Math.round(maxC*(1-frac))+'</div>';
        grid +='<div style="position:absolute;left:0;right:0;top:'+gy+'px;border-top:1px '+(k===ticks-1?'solid rgba(100,100,100,.5)':'dashed rgba(120,120,120,.4)')+';"></div>';
      }
      function lg(c,t){ return '<span style="margin-right:10px;white-space:nowrap;"><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:'+c+';vertical-align:middle;"></i> '+t+'</span>'; }
      var legend=lg('#22c55e','运行中')+lg('#ffdd00','等待进站')+lg('#60a5fa','等待出站')+lg('#ef4444','HOLD')+lg('#3b82f6','已出站')
               +'<span style="margin-right:10px;white-space:nowrap;"><i style="display:inline-block;width:14px;border-top:2px solid #0f172a;vertical-align:middle;"></i> CWIP累计颗数</span>'
               +'<span style="margin-right:10px;white-space:nowrap;"><i style="display:inline-block;width:9px;height:9px;border-radius:2px;border:1px solid #f9a8d4;vertical-align:middle;background:repeating-linear-gradient(45deg,#fdf2f8,#fdf2f8 2px,#fbcfe8 2px,#fbcfe8 4px);"></i> 已忽略</span>'
               +'<span style="white-space:nowrap;"><i style="display:inline-block;width:9px;height:9px;border-radius:2px;border:1px solid #e2e8f0;vertical-align:middle;background:repeating-linear-gradient(45deg,#f1f5f9,#f1f5f9 2px,#e2e8f0 2px,#e2e8f0 4px);"></i> 尾批已跑完/虚拟站</span>';
      return '<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:14px;padding:12px 14px;background:#fff;">'
           + '<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:8px;">'
           + '<div style="font-size:13px;font-weight:600;color:#0f172a;">📊 工序 / Lot 分布 <span style="font-weight:normal;color:#94a3b8;font-size:11px;">— 完整工艺路线('+skel.length+'站) · 柱高=该工序在制Lot数 · 黑线=CWIP累计颗数 · 共 '+lotTotal+' 个在制Lot · 悬停看明细</span></div>'
           + '<div style="font-size:11px;color:#475569;">'+legend+'</div>'
           + '</div>'
           + '<div style="display:flex;align-items:stretch;">'
             + '<div style="position:relative;flex:0 0 30px;width:30px;">'+yaxis+'</div>'
             + '<div style="position:relative;flex:1 1 auto;border-left:1px solid #64748b;border-right:1px solid #64748b;">'
               + '<div style="position:absolute;left:0;right:0;top:0;height:'+H+'px;background:linear-gradient(180deg,#fdffff 0%,#b0eeee 100%);"></div>'
               + grid
               + '<div style="position:relative;z-index:1;display:flex;align-items:flex-start;gap:1px;">'+bars+'</div>'
               + svg
             + '</div>'
             + '<div style="position:relative;flex:0 0 40px;width:40px;">'+raxis+'</div>'
           + '</div>'
           + '<div style="font-size:10px;color:#94a3b8;margin-top:4px;text-align:right;">左轴=在制Lot数 · 右轴=CWIP累计颗数(峰值 '+maxC+' 颗) · 单序峰值 '+maxV+' Lot</div>'
           + '</div>';
    }
    // 工序柱悬停:跟随光标的浮动提示(与统计分析WIP图同款),事件委托挂在 #detailBody 上
    (function(){
      var box=document.getElementById('detailBody'); if(!box) return;
      var tip=document.getElementById('proc_tip');
      if(!tip){ tip=document.createElement('div'); tip.id='proc_tip'; document.body.appendChild(tip); }
      if(!document.getElementById('proc_tip_css')){
        var st=document.createElement('style'); st.id='proc_tip_css';
        st.textContent='.pseg{transition:filter .15s ease;}'
          +'.pseg:hover .pbar{filter:brightness(1.06) saturate(1.05);box-shadow:0 4px 14px rgba(15,23,42,.18);border-radius:3px;}'
          +'#proc_tip{display:none;position:fixed;z-index:70;pointer-events:none;background:#fff;color:#0f172a;font-size:12px;line-height:1.5;padding:8px 12px;border:1px solid #cbd5e1;border-radius:7px;box-shadow:0 6px 18px rgba(0,0,0,.18);white-space:nowrap;transform:translate(-50%,-100%);opacity:0;transition:opacity .1s ease;}'
          +'#proc_tip::after{content:"";position:absolute;left:50%;bottom:-6px;transform:translateX(-50%);border:6px solid transparent;border-top-color:#fff;border-bottom:0;}';
        document.head.appendChild(st);
      }
      function place(e){ tip.style.left=e.clientX+'px'; tip.style.top=(e.clientY-12)+'px'; }
      box.addEventListener('mouseover', function(e){
        var t=e.target.closest && e.target.closest('.pseg'); if(!t) return;
        var tx=t.getAttribute('data-tip')||''; if(!tx) return;
        tip.innerHTML=tx; tip.style.display='block'; place(e);
        requestAnimationFrame(function(){ tip.style.opacity='1'; });
      });
      box.addEventListener('mousemove', function(e){
        if(tip.style.display==='none') return;
        if(e.target.closest && e.target.closest('.pseg')) place(e);
      });
      box.addEventListener('mouseout', function(e){
        var t=e.target.closest && e.target.closest('.pseg'); if(!t) return;
        var to=e.relatedTarget;
        if(to && to.closest && to.closest('.pseg')) return;
        tip.style.opacity='0'; tip.style.display='none';
      });
    })();
    </script>
    """


def render_orders(orders, opts, filters, is_authed=False):
    f = filters or {}
    kw = f.get("kw", "") or ""

    # 左侧筛选栏(整体是一个 form,任何选择即提交)
    sidebar = f"""
    <form method="get" action="/orders" class="filter">
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <input id="lot_search_input" class="search" style="margin:0;flex:1;" placeholder="工单号即时筛 / Lot号点查"
               oninput="filterByWo()"
               onkeydown="if(event.key==='Enter'){{event.preventDefault();lotSearch();}}">
        <button type="button" class="btn-primary" style="flex:0 0 auto;width:auto;padding:6px 12px;"
                onclick="lotSearch()">查</button>
        <a href="/orders" class="btn-reset" style="flex:0 0 auto;width:auto;padding:6px 12px;">重置</a>
      </div>
      {_filter_group("产品型号", "product",    opts["product"],    f.get("product"))}
      {_filter_group("需求方",   "demander",   opts.get("demander", []), f.get("demander"))}
      {_filter_group("优先级",   "priority",   opts["priority"],   f.get("priority"))}
      {_filter_group("风险提示", "risk_level", opts["risk_level"], f.get("risk_level"))}
    </form>
    """

    # 右侧表格。除 工单号/进度/风险提示 外其余列居中。
    def _qty_cell(o):
        # 当前数量(MES 在制颗数,已排除报废/不良/虚拟站) / 交付数量(后台手填目标颗数)
        cq = o.get("cur_qty")
        dq = o.get("deliver_qty")
        cq_s = str(cq) if cq not in (None, "") else "—"
        dq_s = str(dq) if dq not in (None, "") else "—"
        # 交付数量 > 当前数量 → 数量不足:当前数量标红底 + 提示
        short = False
        try:
            if cq not in (None, "") and dq not in (None, "") and float(dq) > float(cq):
                short = True
        except (ValueError, TypeError):
            short = False
        cur_html = (f'<span style="background:#fecaca;color:#b91c1c;font-weight:700;'
                    f'border-radius:4px;padding:1px 7px;">{_html.escape(cq_s)}</span>'
                    if short else _html.escape(cq_s))
        hint = ('<div style="font-size:12px;color:#dc2626;font-weight:600;margin-top:2px;">⚠ 交付数量不足</div>'
                if short else '')
        return ('<td style="text-align:center;white-space:nowrap;">'
                f'<div>当前数量：{cur_html}</div>'
                f'<div style="margin-top:2px;">交付数量：{_html.escape(dq_s)}</div>'
                f'{hint}</td>')

    # 「完成」列:仅登录管理员可见;勾选即把该工单人工标记完成(移入「已完成工单」页)
    can_done = bool(is_authed)
    done_th = '<th style="text-align:center;">完成</th>' if can_done else ''
    ncols = 11 if can_done else 10

    def _done_cell(o):
        if not can_done:
            return ''
        return ('<td style="text-align:center;">'
                f'<input type="checkbox" title="勾选=人工标记该工单已完成(移入「已完成工单」页)" '
                f'onchange="markOrderDone({int(o["id"])}, this)" '
                'style="width:16px;height:16px;cursor:pointer;"></td>')

    risk_rank = {"高风险": 0, "低风险": 1, "正常": 2, "超前": 3}
    prio_rank = {"火箭批": 0, "1-0": 1, "1-1": 2, "Normal": 3}
    rows = "".join(f"""
      <tr data-end="{_html.escape(o["plan_end"] or "9999-12-31")}" data-risk="{risk_rank.get(o["risk_level"] or "", 4)}" data-prio="{prio_rank.get(o["priority"] or "", 4)}" data-slack="{_slack_attr(o["risk_note"])}" data-wo="{_html.escape((o["wo_no"] or "").lower())}">
        <td><button type="button" class="op-edit" style="border:none;background:none;cursor:pointer;padding:0;font-size:13px;color:#2563eb;font-weight:600;"
            onclick="showDetail('{_html.escape(o["wo_no"])}')">{_html.escape(o["wo_no"])}</button></td>
        <td style="text-align:center;">{_html.escape(o["product"] or "")}</td>
        <td style="text-align:center;">{_status_tag(o["status"])}</td>
        <td style="text-align:center;">{_prio_tag(o["priority"])}</td>
        <td style="text-align:center;">{_html.escape(o.get("demander") or "")}</td>
        <td style="text-align:center;">{_html.escape(o["plan_start"] or "")}</td>
        <td style="text-align:center;">{_html.escape(o["plan_end"] or "")}</td>
        {_qty_cell(o)}
        <td style="min-width:120px;">
          <div style="font-size:11px;color:#64748b;">{o["progress"]}%</div>
          <div class="bar-bg"><div class="bar-fg" style="width:{o["progress"]}%"></div></div>
        </td>
        <td>{_risk_tag(o["risk_level"])}<div class="rnote" style="color:{_risk_note_color(o["risk_level"])};">{_risk_note_html(o["risk_note"], is_authed)}</div></td>
        {_done_cell(o)}
      </tr>
    """ for o in orders) or f'<tr><td colspan="{ncols}"><div class="empty"><div class="ico">📭</div><div class="tt">暂无工单</div><div class="sb">调整左侧筛选条件,或从「数据源」同步 MES 工单</div></div></td></tr>'

    table = f"""
    <div class="panel orders-table-panel" style="margin:0;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
        <div>
          <h3 style="margin:0;">工单明细 · 共 {len(orders)} 条记录</h3>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;">点击工单号查看 Lot 明细与负责人</div>
        </div>
        <select id="ord_sort" onchange="sortOrders()" title="排序方式"
                style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;background:#fff;cursor:pointer;text-align:center;text-align-last:center;">
          <option value="due">按交期排序</option>
          <option value="risk">按风险排序</option>
          <option value="prio">按优先级排序</option>
        </select>
      </div>
      <div class="orders-table-scroll">
      <table>
        <thead><tr>
          <th>工单号</th><th style="text-align:center;">产品型号</th>
          <th style="text-align:center;">状态</th><th style="text-align:center;">优先级</th>
          <th style="text-align:center;">需求方</th>
          <th style="text-align:center;">计划开始</th><th style="text-align:center;">计划交付</th>
          <th style="text-align:center;">当前数量 / 交付数量</th>
          <th>进度</th><th>风险提示</th>{done_th}
        </tr></thead>
        <tbody id="ord_tbody">{rows}</tbody>
      </table>
      </div>
    </div>
    <script>
    function sortOrders(){{
      var by=document.getElementById('ord_sort').value;
      var tb=document.getElementById('ord_tbody');
      var rs=Array.prototype.slice.call(tb.querySelectorAll('tr[data-end]'));
      function _sv(el){{ var f=parseFloat(el.dataset.slack); return isNaN(f)?Infinity:f; }}
      rs.sort(function(a,b){{
        if(by==='risk'){{
          var d=(+a.dataset.risk)-(+b.dataset.risk);
          if(d!==0) return d;                                  // ① 风险等级:高>低>正常>超前
          var sd=_sv(a)-_sv(b);
          if(sd!==0) return sd;                                // ② 滞后天数:滞后越多越靠前
          var dp=(+a.dataset.prio)-(+b.dataset.prio);
          if(dp!==0) return dp;                                // ③ 优先级:火箭批>1-0>1-1>Normal
          return a.dataset.end.localeCompare(b.dataset.end);   // ④ 交期近优先(兜底)
        }}
        if(by==='prio'){{
          var dp=(+a.dataset.prio)-(+b.dataset.prio);
          if(dp!==0) return dp;
          return a.dataset.end.localeCompare(b.dataset.end);  // 同优先级按交期近优先
        }}
        return a.dataset.end.localeCompare(b.dataset.end);     // 交期最近优先
      }});
      rs.forEach(function(r){{ tb.appendChild(r); }});
    }}
    function filterByWo(){{
      var el=document.getElementById('lot_search_input');
      var kw=(el?el.value:'').trim().toLowerCase();
      var tb=document.getElementById('ord_tbody');
      tb.querySelectorAll('tr[data-wo]').forEach(function(tr){{
        tr.style.display = (!kw || tr.dataset.wo.endsWith(kw)) ? '' : 'none';
      }});
    }}
    function markOrderDone(id, cb){{
      if(!cb.checked) return;
      if(!confirm('确认把该工单人工标记为「已完成」?\\n标记后将从工单明细与看板移出,归入「已完成工单」页(可在该页取消)。')){{ cb.checked=false; return; }}
      cb.disabled=true;
      var body='id='+encodeURIComponent(id)+'&done=1';
      fetch('/set_order_done',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:body}})
        .then(function(r){{return r.json();}})
        .then(function(d){{ if(d.ok){{ location.reload(); }} else {{ wipToast(d.msg||'操作失败','err'); cb.checked=false; cb.disabled=false; }} }})
        .catch(function(){{ wipToast('操作失败,请重试','err'); cb.checked=false; cb.disabled=false; }});
    }}
    sortOrders();   // 默认按交期最近优先
    </script>
    """

    body = f"""
    <div class="page-title">工单明细</div>
    <div class="page-sub">查看和管理所有 NTO 工单排产数据</div>
    <div class="orders-layout">{sidebar}{table}</div>
    <div id="detailMask" onclick="hideDetail()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:50;"></div>
    <div id="detailBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:1100px;max-width:94vw;height:78vh;background:#fff;border-radius:12px;
         z-index:51;box-shadow:0 10px 40px rgba(0,0,0,.2);display:none;flex-direction:column;">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:18px 22px 12px;border-bottom:1px solid #eef2f7;flex:0 0 auto;">
        <h3 id="detailTitle" style="margin:0;">工单详情</h3>
        <button onclick="hideDetail()" style="border:none;background:#f1f5f9;border-radius:6px;padding:6px 12px;cursor:pointer;">关闭</button>
      </div>
      <div id="detailBody" style="flex:1 1 auto;overflow:auto;padding:16px 22px;">加载中…</div>
    </div>
    <script>
    function hideDetail() {{
      document.getElementById('detailMask').style.display='none';
      document.getElementById('detailBox').style.display='none';
    }}
    function showDetail(wo) {{
      document.getElementById('detailMask').style.display='block';
      document.getElementById('detailBox').style.display='flex';
      window._detailWo=wo; document.getElementById('detailTitle').textContent='工单详情 工单号：'+wo;
      var bd=document.getElementById('detailBody'); bd.innerHTML='加载中…';
      fetch('/order_detail?wo='+encodeURIComponent(wo))
        .then(function(r){{return r.json();}})
        .then(function(d){{ renderDetail(bd, d); }})
        .catch(function(){{bd.innerHTML='<span style="color:#dc2626;">加载失败</span>';}});
    }}
    function renderDetail(bd, d) {{
      if(!d.variants||d.variants.length===0){{bd.innerHTML='<span style="color:#94a3b8;">MES 中无该工单的 lot 数据</span>';return;}}
      {{var tt=document.getElementById('detailTitle'); tt.textContent=(d.product?(' 型号：'+d.product):'')+' 工单号：'+(window._detailWo||'');}}
      var h='<div style="font-size:13px;color:#64748b;margin-bottom:10px;">'+escapeHtml(d.key)+' 共 '+d.variants.length+' 个分身(0/E/F首位)</div>';
      var thStyle='text-align:center;padding:5px 8px;';
      d.variants.forEach(function(v){{
        h+='<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:12px;overflow:hidden;">';
        var vstat = v.qty_status || v.cur_status;
        h+='<div style="padding:8px 12px;background:#f8fafc;font-size:13px;font-weight:600;"> ('+escapeHtml(v.first)+'工单) '+escapeHtml(v.full_wo)+'产品型号：'+escapeHtml(v.product_name||'')+'</div>';
        h+='<table style="width:100%;font-size:12px;border-collapse:collapse;table-layout:fixed;">';
        h+='<colgroup>'
          +'<col style="width:6%;"><col style="width:12%;"><col style="width:15%;">'
          +'<col style="width:15%;"><col style="width:8%;"><col style="width:8%;">'
          +'<col style="width:7%;"><col style="width:7%;"><col style="width:7%;"><col style="width:6%;"><col style="width:7%;"><col style="width:13%;"><col style="width:9%;">'
          +'</colgroup>';
        h+='<tr style="color:#64748b;background:#fafbfc;">'
          +'<td style="'+thStyle+'">步骤</td>'
          +'<td style="'+thStyle+'">Lot ID</td>'
          +'<td style="'+thStyle+'">工序(SPECNAME)</td>'
          +'<td style="'+thStyle+'">描述</td>'
          +'<td style="'+thStyle+'">状态</td>'
          +'<td style="'+thStyle+'">EQP ID</td>'
          +'<td style="'+thStyle+'">Run Time</td>'
          +'<td style="'+thStyle+'">Wait Time</td>'
          +'<td style="'+thStyle+'">Hold Time</td>'
          +'<td style="'+thStyle+'">数量</td>'
          +'<td style="'+thStyle+'white-space:nowrap;">HOLD原因</td>'
          +'<td style="'+thStyle+'">MES备注</td>'
          +'<td style="'+thStyle+'">负责人</td></tr>';
        v.processes.forEach(function(p){{
          var lots = p.lot_list || [];
          var tdC='text-align:center;vertical-align:middle;padding:5px 8px;word-break:break-all;';
          if (lots.length === 0) {{
            h+='<tr style="border-top:1px solid #f1f5f9;"><td style="'+tdC+'">'+escapeHtml(p.seq)+'</td><td style="'+tdC+'">-</td><td style="'+tdC+'">'+escapeHtml(p.spec)+'</td><td style="'+tdC+'">'+escapeHtml(p.desc)+'</td><td colspan="9" style="'+tdC+'color:#94a3b8;">无 lot 明细</td></tr>';
            return;
          }}
          lots.forEach(function(l, idx){{
            h+='<tr style="border-top:1px solid #f1f5f9;">';
            // 同一工序的多 lot,步骤/工序/描述只在第一行显示;Lot ID 紧随步骤
            if (idx===0) {{
              h+='<td style="'+tdC+'" rowspan="'+lots.length+'">'+escapeHtml(p.seq)+'</td>';
            }}
            h+='<td style="'+tdC+'">'+escapeHtml(l.lot_id)+'</td>';
            if (idx===0) {{
              h+='<td style="'+tdC+'" rowspan="'+lots.length+'">'+escapeHtml(p.spec)+'</td>';
              h+='<td style="'+tdC+'" rowspan="'+lots.length+'">'+escapeHtml(p.desc)+'</td>';
            }}
            h+='<td style="'+tdC+'">'+statusBadge(l.status)+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.eqpld)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.run_time)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.wait_time)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.hold_time)||'-')+'</td>';
            h+='<td style="'+tdC+'">'+escapeHtml(l.qty)+'</td>';
            h+='<td style="'+tdC+'">'+(escapeHtml(l.hold_reason)||'-')+'</td>';
            h+='<td style="text-align:left;vertical-align:middle;padding:5px 8px;word-break:break-word;color:#334155;">'+(escapeHtml(l.txn_comment)||'-')+'</td>';
            h+='<td style="'+tdC+'font-weight:600;">'+(escapeHtml(l.owner)||'-')+'</td>';
            h+='</tr>';
          }});
        }});
        h+='</table></div>';
      }});
      bd.innerHTML=h;
    }}
    function statusBadge(s) {{
      var map={{'等待进站':['#fde047','#1f2937'],'等待出站':['#60a5fa','#1f2937'],'运行中':['#bbf7d0','#15803d'],'HOLD':['#fecaca','#1f2937'],'出站':['#bfdbfe','#1f2937'],'已出站':['#bfdbfe','#1f2937'],'已入库':['#bfdbfe','#1f2937']}};
      var c=map[s]||['#f1f5f9','#475569'];
      return '<span style="padding:1px 6px;border-radius:3px;background:'+c[0]+';color:'+c[1]+';white-space:nowrap;">'+escapeHtml(s)+'</span>';
    }}
    function lotSearch() {{
      var kw = document.getElementById('lot_search_input').value.trim();
      if (!kw) return;
      document.getElementById('detailMask').style.display='block';
      document.getElementById('detailBox').style.display='flex';
      document.getElementById('detailTitle').textContent='Lot 搜索 · '+kw;
      var bd=document.getElementById('detailBody'); bd.innerHTML='查询中…';
      fetch('/lot_search?lot='+encodeURIComponent(kw))
        .then(function(r){{return r.json();}})
        .then(function(arr){{
          if(!arr||arr.length===0){{bd.innerHTML='<span style="color:#94a3b8;">未找到含该 Lot ID 的工单</span>';return;}}
          var h='<div style="font-size:13px;color:#64748b;margin-bottom:10px;">共找到 '+arr.length+' 个工单含 Lot「'+escapeHtml(kw)+'」</div>';
          var box=document.createElement('div');
          arr.forEach(function(item){{
            h+='<div style="font-size:12px;color:#0369a1;margin:6px 0 2px;">工单关键段 '+escapeHtml(item.wo_key)+' · 命中 Lot:'+escapeHtml(item.matched_lots.join(', '))+'</div>';
            // 复用 renderDetail 的结构,直接渲染 item.detail
            var tmp=document.createElement('div');
            renderDetail(tmp, item.detail);
            h+=tmp.innerHTML;
          }});
          bd.innerHTML=h;
        }})
        .catch(function(){{bd.innerHTML='<span style="color:#dc2626;">查询失败</span>';}});
    }}
    </script>
    """
    return layout("orders", "工单明细", body)


def render_done(orders, is_authed=False):
    """已完成工单页:进度达 100% 自动完成、或管理员人工标记完成的工单归集于此
    (均已从看板与工单明细移出)。顶部提供查询窗口,可按 工单号/需求方/产品型号/负责人 即时筛选。
    登录管理员可对「人工标记完成」的工单一键撤销,使其退回工单明细。"""
    def _search_key(o):
        # 拼接可检索字段(小写),供前端关键字匹配:工单号/产品型号/需求方/负责人
        return _html.escape(" ".join(str(o.get(k) or "") for k in
            ("wo_no", "product", "demander", "owner")).lower())

    can_done = bool(is_authed)
    op_th = '<th style="text-align:center;">操作</th>' if can_done else ''
    ncols = 10 if can_done else 9

    def _is_manual(o):
        # 人工完成:manual_done=1 且进度未到 100%(纯人工归集,可撤销);否则视为自动完成
        return bool(o.get("manual_done")) and (o.get("progress") or 0) < 100

    def _done_status(o):
        if _is_manual(o):
            return '<span class="tag tag-done" title="管理员人工标记完成">人工完成</span>'
        return '<span class="tag tag-done">已完成 100%</span>'

    def _op_cell(o):
        if not can_done:
            return ''
        if not _is_manual(o):
            # 自动完成(进度100)无需撤销入口,撤销也不会改变其归属
            return '<td style="text-align:center;color:#cbd5e1;">—</td>'
        return ('<td style="text-align:center;">'
                f'<button type="button" onclick="unmarkDone({int(o["id"])}, this)" '
                'style="border:1px solid #fca5a5;background:#fef2f2;color:#dc2626;'
                'border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer;">取消完成</button></td>')

    rows = "".join(f"""
      <tr data-search="{_search_key(o)}">
        <td style="font-weight:600;"><span onclick="showDetail('{_html.escape(o["wo_no"] or "")}')" title="点击查看工单详情" style="color:#2563eb;cursor:pointer;text-decoration:underline;">{_html.escape(o["wo_no"] or "")}</span></td>
        <td style="text-align:center;">{_html.escape(o["product"] or "")}</td>
        <td style="text-align:center;">{_html.escape(o.get("demander") or "")}</td>
        <td style="text-align:center;">{_html.escape(o["owner"] or "")}</td>
        <td style="text-align:center;">{_html.escape(o["plan_start"] or "")}</td>
        <td style="text-align:center;">{_html.escape(o["plan_end"] or "")}</td>
        <td style="text-align:center;">{_html.escape(o.get("done_at") or o.get("plan_end") or "")}</td>
        <td style="text-align:left;color:#475569;">{_html.escape(o.get("remark") or "")}</td>
        <td style="text-align:center;">{_done_status(o)}</td>
        {_op_cell(o)}
      </tr>""" for o in orders) or (f'<tr><td colspan="{ncols}" style="text-align:center;color:#94a3b8;'
        'padding:36px;">暂无已完成工单(进度达 100% 或人工标记完成的工单会归集到这里)</td></tr>')
    body = f"""
    <div class="page-title">已完成工单</div>
    <div class="page-sub">进度达 100% 的工单已从「生产进度看板」与「工单明细」移出,归集于此 · 共 <span id="done_total">{len(orders)}</span> 条</div>
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
        <input id="done_search" class="search" style="margin:0;width:340px;max-width:100%;"
               placeholder="查询:工单号 / 需求方 / 产品型号(如 MB6) / 负责人"
               oninput="filterDone()">
        <a href="#" class="btn-reset" style="flex:none;width:auto;padding:5px 14px;font-size:13px;"
           onclick="document.getElementById('done_search').value='';filterDone();return false;">重置</a>
        <span id="done_count" style="font-size:12px;color:#94a3b8;"></span>
      </div>
      <style>#done_scroll thead th{{position:sticky;top:0;background:#fff;z-index:2;box-shadow:inset 0 -1px 0 #e2e8f0;}}</style>
      <div id="done_scroll" style="overflow:auto;">
      <table>
        <thead><tr>
          <th>工单号</th><th style="text-align:center;">产品型号</th>
          <th style="text-align:center;">需求方</th><th style="text-align:center;">负责人</th>
          <th style="text-align:center;">计划开始</th><th style="text-align:center;">计划交付</th>
          <th style="text-align:center;">完工日(实际)</th><th style="text-align:center;">备注</th>
          <th style="text-align:center;">状态</th>{op_th}
        </tr></thead>
        <tbody id="done_tbody">{rows}</tbody>
      </table>
      </div>
    </div>
    <script>
    function filterDone() {{
      var raw=(document.getElementById('done_search').value||'').trim().toLowerCase();
      var toks=raw?raw.split(/\\s+/):[];   // 多关键字:空格分隔,需全部命中
      var tb=document.getElementById('done_tbody');
      var rows=tb.querySelectorAll('tr[data-search]');
      var shown=0;
      rows.forEach(function(tr){{
        var s=tr.dataset.search;
        var hit=toks.every(function(t){{ return s.split(/\\s+/).some(function(w){{return w.endsWith(t);}}); }});
        tr.style.display=hit?'':'none';
        if(hit) shown++;
      }});
      var c=document.getElementById('done_count');
      c.textContent = toks.length ? ('匹配 '+shown+' 条') : '';
    }}
    function unmarkDone(id, btn){{
      if(!confirm('确认取消该工单的「人工完成」标记?\\n取消后将退回工单明细与看板。')) return;
      btn.disabled=true; btn.textContent='处理中…';
      var body='id='+encodeURIComponent(id)+'&done=0';
      fetch('/set_order_done',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:body}})
        .then(function(r){{return r.json();}})
        .then(function(d){{ if(d.ok){{ location.reload(); }} else {{ wipToast(d.msg||'操作失败','err'); btn.disabled=false; btn.textContent='取消完成'; }} }})
        .catch(function(){{ wipToast('操作失败,请重试','err'); btn.disabled=false; btn.textContent='取消完成'; }});
    }}
    // 表格区固定为"填满到视口底部"、内部滚动(表头 sticky 固定),标题/搜索保持在上方不动。
    function fitDone(){{
      var w=document.getElementById('done_scroll'); if(!w) return;
      var t=w.getBoundingClientRect().top;
      w.style.height=Math.max(200,(window.innerHeight - t - 24))+'px';
    }}
    window.addEventListener('resize', fitDone);
    window.addEventListener('load', function(){{ fitDone(); setTimeout(fitDone,200); }});
    document.addEventListener('DOMContentLoaded', fitDone);
    </script>
    """ + _detail_modal()
    return layout("done", "已完成工单", body)


# ---------- 异常记录页 ----------
def render_exceptions(wos, is_authed=False, wait_h=24, hold_h=24):
    """异常记录页:以工单号汇总(总异常/Wait/HOLD 时长 + 工单备注,含已解除累计)。
    点工单号弹出该工单下各 Lot 的异常明细;Lot 的责任Owner/备注由管理员填写。"""
    def _sk(w):
        return _html.escape((str(w.get("wo_no") or "") + " " + str(w.get("remark") or "")).lower())
    js_authed = 'true' if is_authed else 'false'
    rows = ""
    for w in wos:
        active = w.get("active", 0)
        st = (f'<span class="tag" style="background:#fee2e2;color:#dc2626;">{active} 条进行中</span>'
              if active > 0 else
              '<span class="tag" style="background:#dcfce7;color:#16a34a;">已全部解除</span>')
        rows += f"""<tr data-search="{_sk(w)}">
          <td style="text-align:center;"><span onclick="excWo('{_html.escape(w['wo_no'])}')" title="点击查看该工单各 Lot 异常明细" style="color:#2563eb;cursor:pointer;text-decoration:underline;font-weight:600;">{_html.escape(w['wo_no'])}</span></td>
          <td style="text-align:center;font-weight:600;">{w['total_h']:.1f}h</td>
          <td style="text-align:center;color:#d97706;">{w['wait_h']:.1f}h</td>
          <td style="text-align:center;color:#dc2626;">{w['hold_h']:.1f}h</td>
          <td style="text-align:center;">{st}</td>
          <td style="text-align:left;">{_html.escape(w.get('remark') or '')}</td>
        </tr>"""
    if not rows:
        rows = ('<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:36px;">'
                '暂无异常记录(Lot 在工序中 Wait/HOLD 超阈值后自动归集到这里)</td></tr>')
    active_wo = sum(1 for w in wos if w.get("active", 0) > 0)
    auth_note = ('' if is_authed else ' · <span style="color:#f59e0b;">责任Owner/备注 需登录管理员才能填写</span>')
    body = f"""
    <div class="page-title">异常记录
      <span onclick="var e=document.getElementById('excHelp');e.style.display=(e.style.display==='none'?'block':'none');"
            style="margin-left:10px;font-size:13px;font-weight:normal;color:#2563eb;cursor:pointer;
                   border:1px solid #bfdbfe;background:#eff6ff;border-radius:12px;padding:2px 12px;
                   vertical-align:middle;white-space:nowrap;">ⓘ 工作原理</span>
    </div>
    <div class="page-sub">以工单号汇总(时长为该工单全部异常的峰值累计,含已解除) · 共 <span id="exc_total">{len(wos)}</span> 个工单有异常,其中 <span style="color:#dc2626;">{active_wo}</span> 个仍有进行中{auth_note}</div>
    <div id="excHelp" style="display:none;background:#f8fafc;border:1px solid #e2e8f0;border-left:4px solid #2563eb;
         border-radius:8px;padding:14px 18px;margin-bottom:14px;font-size:13px;color:#334155;line-height:1.9;">
      <div style="font-weight:700;color:#1e293b;margin-bottom:6px;">异常记录是怎么工作的</div>
      <b>1. 自动生成：</b>Lot 在某工序的 <b>Wait / HOLD</b> 等待时长超过设定阈值时,每次 MES 同步会自动在此生成一条记录,登记峰值时长与进入/流出时间。<br>
      <b>2. 阈值分级</b>(优先级 高→低)：<b>工单级 ＞ 型号+工序级 ＞ 型号级 ＞ 系统默认</b>;某项阈值设 <b>0</b> 表示该项不监控。<br>
      <b>3. 始终以当前阈值为准：</b>每次扫描都用现行阈值复核全部记录,<b>峰值已低于当前阈值的旧记录会被自动清除</b>(例如早期把阈值设小产生的误报,在调高阈值后即被清理);<b>未配置监控(阈值为 0)则不产生、也不残留异常记录</b>。<br>
      <b>4. 备注永久保留：</b>凡是填写过 <b>「责任 Owner」或「备注」</b> 的记录,视为已人工确认 · 有追溯价值,<b>永久保留</b>,不受阈值调整影响,不会被自动删除。<br>
      <b>5. 已解除：</b>Lot 离开超时状态或离开 MES 后,进行中记录转为「已解除」,其时长仍计入工单累计供追溯。<br>
      <b>6. 自动忽略：</b>虚拟站(建批首站 / 测试后虚拟站等)与 <b>「送报废仓」</b> 等暂存站点的等待 <b>不计为异常</b>。
    </div>
    <div class="panel">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
        <input id="exc_search" class="search" style="margin:0;width:320px;max-width:100%;"
               placeholder="搜索:工单号 / 备注" oninput="filterExc()">
        <a href="#" class="btn-reset" style="width:auto;padding:7px 14px;"
           onclick="document.getElementById('exc_search').value='';filterExc();return false;">重置</a>
        <a href="/exceptions/export" class="btn-reset" style="width:auto;padding:7px 14px;text-decoration:none;">导出 CSV(全部明细)</a>
        <span id="exc_count" style="font-size:12px;color:#94a3b8;"></span>
      </div>
      <div style="overflow:auto;">
      <table>
        <thead><tr>
          <th style="text-align:center;">工单号</th><th style="text-align:center;">总异常时长</th>
          <th style="text-align:center;">总Wait时长</th><th style="text-align:center;">总HOLD时长</th>
          <th style="text-align:center;">状态</th><th style="text-align:center;">备注(工单)</th>
        </tr></thead>
        <tbody id="exc_tbody">{rows}</tbody>
      </table>
      </div>
    </div>

    <div id="excMask" onclick="hideExc()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:50;"></div>
    <div id="excBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:max-content;min-width:min(760px,90vw);max-width:84vw;max-height:85vh;background:#fff;border-radius:12px;z-index:51;
         box-shadow:0 20px 60px rgba(0,0,0,.3);flex-direction:column;overflow:hidden;">
      <div style="position:relative;padding:16px 22px 8px;flex:0 0 auto;">
        <h3 id="excTitle" style="margin:0;">工单异常明细</h3>
        <div id="excHint" style="font-size:12px;color:#94a3b8;margin-top:4px;"></div>
        <button onclick="hideExc()" style="position:absolute;top:16px;right:20px;border:none;background:#f1f5f9;border-radius:6px;padding:6px 12px;cursor:pointer;">关闭</button>
      </div>
      <div id="excBody" style="font-size:13px;color:#475569;overflow:auto;padding:0 20px 18px;flex:1 1 auto;">加载中…</div>
    </div>
    <script>
    var IS_AUTHED_EXC = {js_authed};
    var ETYPE_EXC = {{'WAIT':'等待超时','HOLD':'HOLD超时'}};
    function filterExc() {{
      var raw=(document.getElementById('exc_search').value||'').trim().toLowerCase();
      var toks=raw?raw.split(/\\s+/):[];
      var rows=document.getElementById('exc_tbody').querySelectorAll('tr[data-search]');
      var shown=0;
      rows.forEach(function(tr){{
        var s=tr.dataset.search||'';
        var hit=toks.every(function(t){{return s.split(/\\s+/).some(function(w){{return w.endsWith(t);}});}});
        tr.style.display=hit?'':'none'; if(hit) shown++;
      }});
      document.getElementById('exc_count').textContent = toks.length?('匹配 '+shown+' 个工单'):'';
    }}
    var _excWo='';
    function _esc(s){{return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
    function excWo(wo) {{
      _excWo=wo;
      document.getElementById('excTitle').textContent='工单 '+wo+' · 异常明细';
      document.getElementById('excMask').style.display='block';
      document.getElementById('excBox').style.display='flex';
      var b=document.getElementById('excBody'); b.innerHTML='加载中…';
      document.getElementById('excHint').textContent='';
      fetch('/exceptions_of_wo?wo='+encodeURIComponent(wo))
        .then(function(r){{return r.json();}})
        .then(function(list){{ renderExcLots(list||[]); }})
        .catch(function(){{ b.innerHTML='<div style="padding:14px;color:#dc2626;">加载失败</div>'; }});
    }}
    function renderExcLots(list) {{
      var b=document.getElementById('excBody');
      if(!list.length){{ b.innerHTML='<div style="padding:14px;color:#94a3b8;">该工单暂无异常记录</div>'; return; }}
      document.getElementById('excHint').textContent='共 '+list.length+' 条 Lot 异常 · 责任Owner/备注'+(IS_AUTHED_EXC?' 可编辑,点保存':' 需管理员登录');
      var th='text-align:center;padding:6px 10px;background:#eef2f7;position:sticky;top:0;';
      var h='<table style="border-collapse:collapse;font-size:13px;width:100%;white-space:nowrap;"><thead><tr style="color:#475569;">'
        +'<td style="'+th+'">Lot号</td><td style="'+th+'">工序</td><td style="'+th+'">类型</td>'
        +'<td style="'+th+'">状态</td><td style="'+th+'">进入时间</td><td style="'+th+'">流出时间</td>'
        +'<td style="'+th+'">时长</td><td style="'+th+'">责任Owner</td><td style="'+th+'">备注</td></tr></thead><tbody>';
      list.forEach(function(l){{
        var et=ETYPE_EXC[l.etype]||l.etype||'';
        var etc=(l.etype==='HOLD')?'#dc2626':'#d97706';
        var sttag=(l.status==='进行中')?'<span style="color:#dc2626;font-weight:600;">进行中</span>':'<span style="color:#64748b;">已解除</span>';
        var td='padding:6px 10px;border-top:1px solid #f1f5f9;text-align:center;';
        var ow, rm;
        if(IS_AUTHED_EXC){{
          ow='<input class="el-owner" data-id="'+l.id+'" value="'+_esc(l.owner)+'" placeholder="责任人" style="width:84px;padding:3px 5px;border:1px solid #cbd5e1;border-radius:4px;">';
          rm='<textarea class="el-remark" data-id="'+l.id+'" placeholder="备注(支持换行)" rows="1" oninput="excGrow(this)" style="width:200px;min-width:160px;max-width:360px;padding:3px 6px;border:1px solid #cbd5e1;border-radius:4px;resize:both;font:inherit;font-size:13px;vertical-align:middle;overflow:hidden;line-height:1.4;">'+_esc(l.remark)+'</textarea><button onclick="saveExcLot('+l.id+')" style="margin-left:5px;border:none;background:#2563eb;color:#fff;border-radius:4px;padding:3px 9px;cursor:pointer;font-size:12px;vertical-align:top;">保存</button>';
        }} else {{ ow=_esc(l.owner)||'-'; rm=_esc(l.remark)||'-'; }}
        h+='<tr><td style="'+td+'">'+_esc(l.lot_id)+'</td><td style="'+td+'">'+_esc(l.spec_name||l.spec)+'</td>'
          +'<td style="'+td+'color:'+etc+';font-weight:600;">'+et+'</td><td style="'+td+'">'+sttag+'</td>'
          +'<td style="'+td+'">'+_esc(l.enter_at)+'</td><td style="'+td+'">'+(_esc(l.exit_at)||'-')+'</td>'
          +'<td style="'+td+'font-weight:600;">'+(l.peak_hours||0).toFixed(1)+'h</td>'
          +'<td style="'+td+'">'+ow+'</td><td style="'+td+'text-align:left;">'+rm+'</td></tr>';
      }});
      h+='</tbody></table>';
      b.innerHTML=h;
      b.querySelectorAll('textarea.el-remark').forEach(function(t){{ excGrow(t); }});
    }}
    function excGrow(t) {{ t.style.height='auto'; t.style.height=(t.scrollHeight)+'px'; }}
    function saveExcLot(id) {{
      var ow=document.querySelector('.el-owner[data-id="'+id+'"]');
      var rm=document.querySelector('.el-remark[data-id="'+id+'"]');
      var body='id='+id+'&owner='+encodeURIComponent(ow?ow.value:'')+'&remark='+encodeURIComponent(rm?rm.value:'');
      fetch('/exc_update',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body}})
        .then(function(r){{return r.json();}})
        .then(function(res){{ var hn=document.getElementById('excHint');
          if(res&&res.ok){{ var o=hn.textContent; hn.textContent='✓ 已保存'; setTimeout(function(){{hn.textContent=o;}},1200);}}
          else wipToast((res&&res.msg)||'保存失败'); }})
        .catch(function(){{ wipToast('网络错误,未保存','err'); }});
    }}
    function hideExc(){{ document.getElementById('excMask').style.display='none'; document.getElementById('excBox').style.display='none'; }}
    </script>
    """
    return layout("exceptions", "异常记录", body)


# ---------- 加急验证 Lot 甘特页(轻量,以 Lot 为主键) ----------
def render_urgent_gantt(batches):
    """加急验证 Lot 甘特(轻量·带日期轴):每个 Lot 一行,时间条从建单日→计划交付日,
    按"到目标工序"的进度填充;today 竖线;不算 ETA/风险。"""
    import datetime as _d

    def _pd(s):
        try:
            p = (s or "")[:10].split("-")
            return _d.date(int(p[0]), int(p[1]), int(p[2]))
        except (ValueError, IndexError):
            return None

    today = _d.date.today()
    ds = [today]
    for b in batches:
        c, e = _pd(b.get("created_at")), _pd(b.get("plan_end"))
        if c:
            ds.append(c)
        if e:
            ds.append(e)
    d0, d1 = min(ds), max(ds)
    span = max((d1 - d0).days + 1, 1)
    dayW = 34
    axisW = span * dayW

    def _off(dt):
        return (dt - d0).days if dt else 0

    # 日期表头:每天一格,每天都标出日期(今日紫色高亮)
    head = ""
    for i in range(span):
        day = d0 + _d.timedelta(days=i)
        is_today = (day == today)
        label = f'{day.month}/{day.day}'
        col = '#7c3aed' if is_today else '#94a3b8'
        wt = '700' if is_today else 'normal'
        bg = '#ede9fe' if is_today else ('#fafbfc' if i % 2 == 0 else '#fff')
        head += (f'<div style="position:absolute;left:{i*dayW}px;width:{dayW}px;top:0;bottom:0;'
                 f'background:{bg};border-right:1px solid #f1f5f9;font-size:10px;color:{col};'
                 f'font-weight:{wt};text-align:center;padding-top:3px;box-sizing:border-box;">{label}</div>')
    today_off = _off(today)
    blocks = ""
    total_lots = sum(b.get("lot_count", 0) for b in batches)
    for b in batches:
        c, e = _pd(b.get("created_at")), _pd(b.get("plan_end"))
        bs = _off(c) if c else 0
        be = _off(e) if e else (today_off)
        if be < bs:
            be = bs
        barL = bs * dayW
        barW = max((be - bs + 1) * dayW, dayW)
        lot_rows = ""
        # 工序骨架:把「到目标工序」的各工序按 CT 占比铺满整条(目标工序=满条),
        # 每段右侧一道浅灰分隔线,让进度条直观显示工序分段(与主看板甘特一致)。
        segs = b.get("segs") or []
        tr = b.get("target_ratio") or 0
        seg_cells = ""
        if segs and tr:
            for s in segs:
                cr = s.get("cum_ratio", 0)
                rr = s.get("ratio", 0)
                if cr >= tr:           # 超过目标工序的不画
                    continue
                left_pct = min(cr / tr, 1.0) * 100
                w_pct = (rr / tr) * 100
                nm = _html.escape(s.get("name") or s.get("spec") or "")
                seg_cells += (f'<div title="{nm}" style="position:absolute;top:0;bottom:0;'
                              f'left:{left_pct:.2f}%;width:{w_pct:.2f}%;'
                              f'box-shadow:inset -1px 0 0 rgba(148,163,184,.55);box-sizing:border-box;"></div>')
        for l in b.get("lots", []):
            prog = l.get("progress", 0)
            pcol = "#16a34a" if l.get("done") else "#3b82f6"
            done = " ✓" if l.get("done") else ""
            fillW = max(barW * prog / 100.0, 2) if prog else 0
            # 时间条:浅底 + 进度填充 + 工序分隔 + today 竖线
            track = (f'<div style="position:relative;height:18px;width:{axisW}px;">'
                     f'<div style="position:absolute;left:{barL}px;width:{barW}px;top:0;bottom:0;'
                     f'background:#f1f5f9;border:1px solid #cbd5e1;border-radius:3px;overflow:hidden;">'
                     f'<div style="position:absolute;left:0;top:0;bottom:0;width:{fillW}px;background:{pcol};opacity:.85;"></div>'
                     f'{seg_cells}'
                     f'<div style="position:absolute;left:0;right:0;top:0;text-align:center;font-size:10px;line-height:18px;color:#1f2937;">{prog}%{done}</div>'
                     f'</div>'
                     f'<div title="今日" style="position:absolute;left:{today_off*dayW}px;top:-2px;bottom:-2px;width:2px;background:#7c3aed;z-index:3;"></div>'
                     f'</div>')
            lot_rows += f"""<tr>
              <td style="padding:5px 10px;font-weight:600;white-space:nowrap;text-align:center;">{_html.escape(l.get('lot_id') or '')}</td>
              <td style="padding:5px 10px;white-space:nowrap;color:#475569;text-align:center;">{_html.escape(l.get('product') or '-')}</td>
              <td style="padding:5px 10px;white-space:nowrap;text-align:center;">{l.get('qty', 0)}</td>
              <td style="padding:5px 10px;white-space:nowrap;color:#475569;text-align:center;">{_html.escape(l.get('hold_reason') or '-')}</td>
              <td style="padding:5px 10px;text-align:left;color:#334155;white-space:normal;word-break:break-word;min-width:240px;max-width:340px;">{_html.escape(l.get('txn_comment') or '-')}</td>
              <td style="padding:5px 10px;white-space:nowrap;color:#475569;text-align:center;">{_html.escape(l.get('spec_name') or l.get('spec') or '-')}</td>
              <td style="padding:5px 10px;white-space:nowrap;text-align:center;">{_status_tag(l.get('status') or '')}</td>
              <td style="padding:5px 10px;white-space:nowrap;text-align:center;color:#475569;">{_html.escape(str(l.get('eqpld') or '-'))}</td>
              <td style="padding:5px 10px;white-space:nowrap;text-align:center;">{_html.escape(str(l.get('run_time') or '-'))}</td>
              <td style="padding:5px 10px;white-space:nowrap;text-align:center;">{_html.escape(str(l.get('wait_time') or '-'))}</td>
              <td style="padding:5px 10px;white-space:nowrap;text-align:center;">{_html.escape(str(l.get('hold_time') or '-'))}</td>
              <td style="padding:5px 10px;text-align:center;">{track}</td></tr>"""
        if not lot_rows:
            lot_rows = '<tr><td colspan="12" style="padding:14px;color:#94a3b8;text-align:center;">该批次暂无 Lot</td></tr>'
        cstr = c.isoformat() if c else "-"
        estr = e.isoformat() if e else "-"
        _bid = b.get('id')
        blocks += f"""
        <div class="panel" style="text-align:center;">
          <h3 onclick="toggleUrgB({_bid})" title="点击展开 / 收起该批次 Lot 明细"
              style="margin:0;text-align:center;cursor:pointer;user-select:none;">
            <span id="urgbcaret_{_bid}" style="display:inline-block;width:16px;color:#94a3b8;">▶</span>⚡ {_html.escape(b.get('batch_name') or '')}
            <span style="font-size:13px;color:#64748b;font-weight:normal;">· {_html.escape(b.get('product') or '')}
            (CT:{_html.escape(b.get('ct_model') or '-')}) · 作业到「{_html.escape(b.get('target_name') or b.get('target_spec') or '')}」即完成
            · 建单 {cstr} → 计划交付 {estr} · 完成 {b.get('done_count',0)}/{b.get('lot_count',0)}</span></h3>
          <div id="urgbbody_{_bid}" style="display:none;margin-top:8px;overflow-x:auto;"><table style="font-size:13px;border-collapse:collapse;margin:0 auto;">
            <thead><tr style="color:#64748b;">
              <th style="text-align:center;padding:5px 10px;">Lot号</th><th style="text-align:center;padding:5px 10px;">产品型号</th>
              <th style="text-align:center;padding:5px 10px;">数量</th>
              <th style="text-align:center;padding:5px 10px;">HOLD原因</th>
              <th style="text-align:center;padding:5px 10px;">MES备注</th>
              <th style="text-align:center;padding:5px 10px;">当前工序</th>
              <th style="text-align:center;padding:5px 10px;">状态</th>
              <th style="text-align:center;padding:5px 10px;">EQP ID</th>
              <th style="text-align:center;padding:5px 10px;">Run Time</th>
              <th style="text-align:center;padding:5px 10px;">Wait Time</th>
              <th style="text-align:center;padding:5px 10px;">Hold Time</th>
              <th style="text-align:center;padding:5px 10px;">
                <div style="position:relative;height:16px;width:{axisW}px;border-bottom:1px solid #e2e8f0;">{head}</div>
              </th>
            </tr></thead><tbody>{lot_rows}</tbody>
          </table></div>
        </div>"""
    if not blocks:
        blocks = ('<div class="panel" style="text-align:center;color:#94a3b8;padding:40px;">'
                  '暂无加急验证 Lot · 请在「后台管理 → 加急验证Lot」新建</div>')
    body = f"""
    <div class="page-title" style="text-align:center;">⚡ 加急验证 Lot</div>
    <div class="page-sub" style="text-align:center;">独立于 NTO 工单,以 Lot 号为主键 · 时间条:建单日→计划交付日,按到目标工序进度填充 ·
      共 {len(batches)} 批 / {total_lots} 个 Lot · <a href="/gantt" style="color:#2563eb;">← 返回生产进度看板</a></div>
    <div style="margin-bottom:8px;font-size:12px;color:#64748b;text-align:center;">
      <span style="display:inline-block;width:22px;height:10px;background:#3b82f6;opacity:.85;vertical-align:middle;"></span> 进度(在制)
      <span style="display:inline-block;width:22px;height:10px;background:#16a34a;opacity:.85;vertical-align:middle;margin-left:10px;"></span> 已完成
      <span style="color:#7c3aed;margin-left:10px;">┃</span> 今日
      <span style="margin-left:14px;">
        <a href="javascript:void(0)" onclick="toggleUrgAll(true)" style="color:#2563eb;">展开全部</a> ·
        <a href="javascript:void(0)" onclick="toggleUrgAll(false)" style="color:#64748b;">收起全部</a>
      </span>
    </div>
    {blocks}
    <script>
    // 加急批次折叠:点批次名展开/收起该批次 Lot 明细(默认收起)
    function toggleUrgB(id){{
      var b=document.getElementById('urgbbody_'+id), c=document.getElementById('urgbcaret_'+id);
      if(!b) return;
      var open=(b.style.display==='none');
      b.style.display=open?'':'none';
      if(c) c.textContent=open?'▼':'▶';
    }}
    function toggleUrgAll(open){{
      var bs=document.querySelectorAll('[id^="urgbbody_"]');
      for(var i=0;i<bs.length;i++){{
        bs[i].style.display=open?'':'none';
        var c=document.getElementById('urgbcaret_'+bs[i].id.slice(9));
        if(c) c.textContent=open?'▼':'▶';
      }}
    }}
    </script>
    """
    return layout("gantt", "加急验证Lot", body)


# ---------- 甘特图页 ----------
import datetime as _dt


def _attention_panel_html(attention):
    """看板顶部「需要关注」清单面板(服务端渲染)。
    attention = db.attention_orders():每条 = 一个有问题的工单 + 问题标签 + 瓶颈工序。
    把分散在高风险/挂起/异常里的信号合并成一屏直读,问题对所有人可见。"""
    items = attention or []
    if not items:
        return ('<div class="panel" style="margin-bottom:14px;border-left:4px solid #16a34a;'
                'padding:12px 16px;color:#15803d;font-size:13px;">✅ 暂无需要关注的工单'
                '(无高风险 / 异常)</div>')
    chip_style = {
        "risk": ("#fee2e2", "#dc2626", "🔴 高风险"),
        "hold": ("#fef3c7", "#b45309", "⏸ 挂起"),
        "exc":  ("#ffedd5", "#c2410c", "⚠ 异常"),
    }
    rows_html = []
    for x in items:
        chips = []
        for rsn in x.get("reasons", []):
            bg, fg, lbl = chip_style.get(rsn["type"], ("#f1f5f9", "#475569", rsn.get("label", "")))
            det = _html.escape(rsn.get("detail") or "")
            chips.append('<span title="' + det + '" style="display:inline-block;background:' + bg
                         + ';color:' + fg + ';border-radius:5px;font-size:11px;padding:1px 7px;'
                         'margin:1px 3px 1px 0;white-space:nowrap;">' + lbl + '</span>')
        chips_html = "".join(chips)
        b = x.get("bottleneck")
        if b:
            kindtxt = "已HOLD" if b["kind"] == "HOLD" else "已等待"
            bcolor = "#dc2626" if b["kind"] == "HOLD" else "#b45309"
            hrs = b["hours"]
            htxt = ("%.1f天" % (hrs / 24)) if hrs >= 48 else ("%.0fh" % hrs)
            bott = ('<span style="color:' + bcolor + ';font-weight:600;">' + _html.escape(b["desc"])
                    + '</span><span style="color:#94a3b8;"> · ' + kindtxt + ' ' + htxt
                    + ' · ' + str(b["qty"]) + '颗</span>')
        else:
            bott = '<span style="color:#cbd5e1;">—</span>'
        wo = _html.escape(x.get("wo_no") or "")
        rows_html.append(
            '<tr style="border-top:1px solid #f1f5f9;">'
            '<td style="padding:7px 10px;white-space:nowrap;"><span onclick="showDetail(\'' + wo + '\')" '
            'style="color:#2563eb;cursor:pointer;text-decoration:underline;font-weight:600;">' + wo + '</span></td>'
            '<td style="padding:7px 10px;white-space:nowrap;font-weight:600;color:#0f172a;">' + _html.escape(x.get("model") or "") + '</td>'
            '<td style="padding:7px 10px;">' + chips_html + '</td>'
            '<td style="padding:7px 10px;font-size:12px;">' + bott + '</td>'
            '<td style="padding:7px 10px;white-space:nowrap;">' + _html.escape(x.get("owner") or "-") + '</td>'
            '<td style="padding:7px 10px;white-space:nowrap;color:#64748b;">' + _html.escape(x.get("plan_end") or "-") + '</td>'
            '</tr>')
    th = 'text-align:left;padding:7px 10px;color:#64748b;font-weight:600;background:#fafbfc;white-space:nowrap;'
    toggle_js = ("<script>function toggleAttn(){var b=document.getElementById('attnBody'),"
                 "a=document.getElementById('attnArrow');if(b.style.display==='none')"
                 "{b.style.display='block';a.textContent='\\u25be';}"
                 "else{b.style.display='none';a.textContent='\\u25b8';}}</script>")
    return toggle_js + (
        '<div class="panel" id="attnPanel" style="margin-bottom:14px;border-left:4px solid #dc2626;overflow:hidden;">'
        '<div style="display:flex;justify-content:space-between;align-items:center;padding:11px 16px;cursor:pointer;" onclick="toggleAttn()">'
        '<h3 style="margin:0;font-size:15px;color:#b91c1c;display:flex;align-items:center;gap:8px;">⚠️ 需要关注 '
        '<span style="background:#fee2e2;color:#dc2626;border-radius:10px;font-size:12px;padding:1px 9px;">' + str(len(items)) + ' 单</span></h3>'
        '<span style="font-size:12px;color:#94a3b8;">高风险 / 异常(含HOLD卡滞) · 点工单号看详情 <span id="attnArrow">▾</span></span>'
        '</div>'
        '<div id="attnBody" style="overflow:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr>'
        '<td style="' + th + '">工单号</td><td style="' + th + '">型号</td><td style="' + th + '">问题</td>'
        '<td style="' + th + '">卡在哪步 · 卡了多久</td><td style="' + th + '">负责人</td><td style="' + th + '">计划交付</td>'
        '</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table></div></div>')


def render_gantt(rows, stats=None, is_authed=False, attention=None):
    import json as _json
    import db
    stats = stats or {"total": 0, "ahead": 0, "normal": 0, "low": 0, "high": 0}
    # 序列化工单数据给前端 JS
    data = []
    products = set()
    owners = set()
    _hmap = db.holds_map()   # {wo_no:[active 挂起]} 一次取全,逐单挂载给前端
    for r in rows:
        prod = r.get("product") or ""
        if prod:
            products.add(mes.base_model(prod))
        _ow = (r.get("owner") or "").strip()
        if _ow:
            owners.add(_ow)
        data.append({
            "wo_no": r.get("wo_no") or "",
            "product": prod,
            "owner": r.get("owner") or "",
            "cur_process": r.get("cur_process") or "",
            "plan_start": r.get("plan_start") or "",
            "plan_end": r.get("plan_end") or "",
            "progress": r.get("progress") or 0,
            "status": r.get("status") or "",
            "priority": r.get("priority") or "",
            "risk_level": r.get("risk_level") or "",
            "risk_note": r.get("risk_note") or "",
            "ct_segs": r.get("ct_segs") or [],
            "total_ct_h": r.get("total_ct_h") or 0,   # 总CT(小时):应到进度计算用标准CT
            "adj_total_ct_h": r.get("adj_total_ct_h") or r.get("total_ct_h") or 0,  # ENG调整后总CT:条宽用
            "head_pct": r.get("head_pct") if r.get("head_pct") is not None else r.get("progress") or 0,
            "tail_pct": r.get("tail_pct") if r.get("tail_pct") is not None else r.get("progress") or 0,
            "head_spec": r.get("head_spec") or "",
            "tail_spec": r.get("tail_spec") or "",
            "lot_specs": r.get("lot_specs") or [],   # 当前有LOT(在制)的工序;无者刷浅蓝
            "ignored_specs": r.get("ignored_specs") or [],   # 整站LOT全被忽略的残留站;刷浅粉

            # 预计产出日(ETA)→ 工序条长度按 plan_start→ETA 铺,必须对所有人下发,
            # 否则匿名无 ETA 会退回"按整条CT铺",条长/进度位置与管理员不一致(两个进度)。
            # 预测类信息的可见性在前端按 IS_AUTHED 控制:Buffer 仅管理员,滞后所有人可见。
            "eta": r.get("eta") or "",
            "target_ratio": r.get("target_ratio"),           # 完成工序占比:到此即100%,其后工序不作业
            "disp_ratio": r.get("disp_ratio"),               # 甘特显示截止占比:只画到完成工序为止,其后工序不显示
            # 人工挂起(等料/设备等可协调异常):恢复作业日 + 原因(管理员备注)。
            # 甘特据此画「今日→恢复日」等待间隙;ETA 已在后端按 resume_date 顺延。
            # 挂起「状态」对所有人可见(含匿名,只读);仅「设置/编辑/解除」操作需管理员权限。
            "resume_date": r.get("resume_date") or "",
            "wait_reason": r.get("wait_reason") or "",
            "hold_spec": r.get("hold_spec") or "",   # 被挂起的工序SPEC(甘特据此定位间隙)
            # 多挂起明细(active):前端弹窗据此列出多条、各自解除。甘特/ETA 仍用上面的单组缓存(最晚恢复日)。
            "holds": [
                {"id": h["id"], "hold_spec": h.get("hold_spec") or "",
                 "resume_date": h.get("resume_date") or "", "reason": h.get("reason") or "",
                 "start_date": h.get("start_date") or ""}
                for h in _hmap.get(r.get("wo_no") or "", [])
            ],
        })
    data_json = _json.dumps(data, ensure_ascii=False)
    prod_checks = "".join(
        f'<label class="g-prodopt"><input type="checkbox" class="g-prodcb" value="{_html.escape(p)}" '
        f'onchange="gantt.onProd()"> {_html.escape(p)}</label>'
        for p in sorted(products))
    owner_checks = "".join(
        f'<label class="g-prodopt"><input type="checkbox" class="g-ownercb" value="{_html.escape(o)}" '
        f'onchange="gantt.onOwner()"> {_html.escape(o)}</label>'
        for o in sorted(owners))
    today = _dt.date.today().isoformat()
    _now = _dt.datetime.now()                        # 服务器(=车间Windows,北京时间)当下时刻
    now_min = _now.hour * 60 + _now.minute           # 当日分钟数,供「现在」线小时精度定位
    authed_js = 'true' if is_authed else 'false'   # 管理员才显示「忽略」复选框
    # 预计产出/Buffer 图例:仅登录管理员可见(匿名浏览者看不到虚线框与 Buffer)
    _eta_legend = ('<span style="border-left:1px solid #e2e8f0;padding-left:16px;">'
                   '<i class="gleg" style="border:1px dashed #0ea5e9;background:transparent;"></i>'
                   '预计产出(标准CT,今日→ETA)</span>') if is_authed else ''
    # 计算逻辑说明里的 ②预计产出 ③Buffer 段:同样仅登录可见
    _eta_notes = ('''
        <b>② 预计产出日(ETA)</b> = 今天 + 剩余天数。剩余天数 = 从<b>当前所在工序</b>到完工,
        <b>剩余各工序的标准CT之和 ÷ 24</b>(按 CT 表,随当下进度走;后台若开「按校准后CT」则该型号改用实测时间)。
        若工单设了<b>完成工序</b>,只累计到该工序为止。甘特图上<b>今日→ETA 的虚线框</b>即此预测:蓝框=能在交付日前产出,红框=预计滞后(此预测与工序条本身的长度无关,工序条按真实CT铺,见⑥)。<br>
        <b>③ 富余(Buffer)</b> = 计划交付日 − 预计产出日(ETA)。&gt;0 为有富余(蓝色"Buffer X天"),&lt;0 为滞后(红色"滞后X天")。交期由 MPC 外部商定,交期给得越晚 Buffer 越大、风险越小 —— 产出预测本身不受交期影响。<br>''') if is_authed else ''

    # 顶部风险卡片(原仪表盘),点击筛选甘特图
    cards = [
        ("总工单数", stats.get("total", 0), "#475569", "all"),
        ("超前",    stats.get("ahead", 0),  "#16a34a", "超前"),
        ("正常",    stats.get("normal", 0), "#3b82f6", "正常"),
        ("低风险",  stats.get("low", 0),    "#ca8a04", "低风险"),
        ("高风险",  stats.get("high", 0),   "#dc2626", "高风险"),
    ]
    _ctot = stats.get("total", 0) or 1   # 占比条分母:总工单数
    cards_html = "".join(
        f'<div class="card dash-card" data-risk="{r}" onclick="setRisk(this)"'
        f'{" data-active=1" if r=="all" else ""} style="cursor:pointer;">'
        f'<div class="k"><span class="cdot" style="background:{c};"></span>{k}</div>'
        f'<div class="v" style="color:{c};">{v}</div>'
        f'<div class="cbar"><div style="width:{(100 if r=="all" else v/_ctot*100):.0f}%;background:{c};"></div></div>'
        f'</div>'
        for k, v, c, r in cards
    )

    body = f"""
    <div class="page-title">生产进度看板
      <a href="/urgent_gantt" style="float:right;font-size:13px;font-weight:normal;background:#dc2626;color:#fff;
         text-decoration:none;border-radius:6px;padding:6px 14px;">⚡ 加急Lot</a></div>
    <div class="page-sub">工单进度与交期风险全局概览 · 点击下方卡片按风险等级筛选甘特图</div>
    <div class="cards">{cards_html}</div>
    {_attention_panel_html(attention)}
    <div class="panel" style="padding:0;overflow:hidden;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;padding:14px 18px;border-bottom:1px solid #eef2f7;">
        <h3 style="margin:0;display:flex;align-items:center;gap:8px;">📊 工单进度甘特图 <span id="g_riskname" style="font-size:12px;color:#94a3b8;font-weight:normal;"></span>
          <button type="button" onclick="toggleGanttNote()" title="查看进度/风险/交期的计算逻辑"
            style="font-size:12px;font-weight:normal;color:#475569;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:7px;padding:4px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;">📖 风险计算说明 <span id="gNoteArrow" style="color:#94a3b8;">▾</span></button></h3>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;">
          <div class="g-multi">
            <button type="button" id="g_prodbtn" class="g-multibtn" onclick="gantt.toggleProd(event)">全部产品型号 ▾</button>
            <div id="g_proddrop" class="g-multidrop">
              <div style="display:flex;justify-content:space-between;padding:4px 8px 6px;border-bottom:1px solid #eef2f7;margin-bottom:4px;">
                <a href="javascript:void(0)" onclick="gantt.prodAll(true)" style="color:#2563eb;font-size:12px;">全选</a>
                <a href="javascript:void(0)" onclick="gantt.prodAll(false)" style="color:#64748b;font-size:12px;">清空</a>
              </div>
              {prod_checks}
            </div>
          </div>
          <div class="g-multi">
            <button type="button" id="g_ownerbtn" class="g-multibtn" onclick="gantt.toggleOwner(event)">全部负责人 ▾</button>
            <div id="g_ownerdrop" class="g-multidrop">
              <div style="display:flex;justify-content:space-between;padding:4px 8px 6px;border-bottom:1px solid #eef2f7;margin-bottom:4px;">
                <a href="javascript:void(0)" onclick="gantt.ownerAll(true)" style="color:#2563eb;font-size:12px;">全选</a>
                <a href="javascript:void(0)" onclick="gantt.ownerAll(false)" style="color:#64748b;font-size:12px;">清空</a>
              </div>
              {owner_checks}
            </div>
          </div>
          <select id="g_sort" onchange="gantt.render()" title="排序方式">
            <option value="due">按交期排序</option>
            <option value="risk">按风险排序</option>
            <option value="prio">按优先级排序</option>
          </select>
          <input id="g_search" placeholder="搜工单号 / 产品型号" oninput="gantt.render()"
                 style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;width:180px;">
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
            <input type="checkbox" id="g_due" checked onchange="gantt.render()"> 标注交付日</label>
          <button class="gnav" onclick="gantt.shift(-7)">‹</button>
          <button class="gnav" onclick="gantt.shift(7)">›</button>
        </div>
      </div>
      <div id="gNoteBox" style="display:none;padding:12px 18px;font-size:12px;color:#475569;background:#f8fafc;border-bottom:1px solid #eef2f7;line-height:1.85;">
        <div style="font-weight:600;color:#0f172a;margin-bottom:4px;">进度 / 风险 / 交期 的计算逻辑</div>
        <b>① 进度%</b> = 当前工序累计 CT ÷ 总 CT(按上传的 CT 表分段)。同一工单取最慢分身(尾批)为整单进度。
        若工单在后台设了<b>完成工序</b>,则<b>到该工序即视为完工</b>,进度/风险/ETA 均按"到该工序"折算,到达即 100%;甘特图<b>只画到完成工序为止,其后的工序不显示</b>。<br>
        {_eta_notes}
        <b>④ 风险等级</b>按富余天数分级:填了<b>计划开始日</b>时用<b>相对富余度</b>(富余 ÷ 计划总周期)——
        ≥15% 超前、0~15% 正常、−10%~0 低风险、低于 −10% 高风险(大单小单不再用同一把绝对尺子);
        未填计划开始日时退回<b>绝对阈值</b>——富余 ≥2天 超前、0~2天 正常、−1~0天 低风险、低于 −1天 高风险。<br>
        <b>⑤ 现在线</b>(橙色竖线 + 顶端"现在 HH:MM"标签,每分钟自动右移)= 北京当下真实时刻在日历轴的位置,也就是此刻<b>应到</b>的进度。
        <b>一天按生产作业周期算:每天从早上 7:30 起、到次日 7:30 为 24H</b>(不是午夜),线的日内位置据此换算,贴合实际班次。
        看它落在工序条的哪一段,就是此刻本该做到的工序;<b>若甩到工序条右边一大截,说明早该完工却还压在表上 = 异常,一眼可见</b>。<br>
        <b>⑥ 工序条铺设</b>:从<b>计划开始日</b>(为空则从<b>今天</b>)起,严格按<b>上传CT</b>铺 ——<b>1天 = 24H</b>,每道工序宽度 = 该工序CT ÷ 24 天,工序间无间隙(等待时间请自行计入各工序CT)。
        条总长 = 总CT ÷ 24 天,<b>尽头 = 计划开始 + 总CT(按CT本该完工的日子)</b>。所以 7H 的工序就是一小块,绝不横跨整天;条子<b>不随交期、也不随实测拉伸</b>(交期只作紫色参考线,文字"预计产出"另按今天+剩余CT给现实预测)。
      </div>
      <div style="padding:10px 18px;font-size:12px;color:#64748b;display:flex;gap:16px;flex-wrap:wrap;border-bottom:1px solid #f8fafc;">
        图例(按 CT 工序):
        <span><i class="gleg" style="background:#bbf7d0;"></i>已完成工序(首尾批都过)</span>
        <span><i class="gleg" style="background:#3b82f6;"></i>在制·有产品(该工序有LOT)</span>
        <span><i class="gleg" style="background:#bfdbfe;"></i>在制·无产品(尾首批之间的空工序)</span>
        <span><i class="gleg" style="background:#fbcfe8;"></i>残留·已忽略(不计入尾批)</span>
        <span><i class="gleg" style="background:#f5c518;"></i>未作业工序</span>
        <span style="border-left:1px solid #e2e8f0;padding-left:16px;"><i class="gleg" style="background:#ddd6fe;"></i>今日(整列加深)</span>
        <span><i class="gleg" style="background:#a855f7;"></i>交付日</span>
        <span><i class="gleg" style="background:#eab308;"></i>交期延误风险(早于标准CT完工)</span>
        <span><i class="gleg" style="background:#ea580c;"></i>现在线(应到此刻 · 每分钟右移)</span>
        {_eta_legend}
      </div>
      <div id="gantt_wrap" style="overflow:auto;height:calc(100vh - 290px);min-height:360px;"><div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在加载甘特图…</div></div></div>
      <div id="gseg_tip"></div>
      <div id="hold_tip"></div>
    </div>
    <style>
    .dash-card {{ transition:box-shadow .15s, transform .15s; position:relative; }}
    .dash-card:hover {{ box-shadow:0 4px 16px rgba(15,23,42,.12); transform:translateY(-2px); }}
    .dash-card::after {{ content:"点击筛选"; position:absolute; top:14px; right:16px; font-size:10px; color:#cbd5e1; }}
    .dash-card[data-active="1"] {{ outline:2px solid #2563eb; outline-offset:-2px; }}
    .dash-card[data-active="1"]::after {{ content:"已选中"; color:#2563eb; }}
    #g_sort {{ padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;background:#fff;cursor:pointer;text-align:center;text-align-last:center; }}
    #g_sort option {{ text-align:center; }}
    .g-multi {{ position:relative;display:inline-block; }}
    .g-multibtn {{ padding:6px 12px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;background:#fff;cursor:pointer;min-width:130px;text-align:left;color:#334155; }}
    .g-multibtn:hover {{ background:#f8fafc; }}
    .g-multidrop {{ display:none;position:absolute;top:110%;left:0;z-index:30;background:#fff;border:1px solid #cbd5e1;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.12);padding:6px;min-width:180px;max-height:300px;overflow:auto; }}
    .g-multidrop.open {{ display:block; }}
    .g-prodopt {{ display:flex;align-items:center;gap:6px;padding:5px 8px;font-size:13px;color:#334155;cursor:pointer;border-radius:5px;white-space:nowrap; }}
    .g-prodopt:hover {{ background:#f1f5f9; }}
    .gnav {{ width:30px;height:30px;border:1px solid #cbd5e1;background:#fff;border-radius:6px;cursor:pointer;font-size:16px;color:#475569; }}
    .gnav:hover {{ background:#f1f5f9; }}
    .gleg {{ display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:middle;margin-right:4px; }}
    .gantt-table {{ border-collapse:collapse;font-size:12px;white-space:nowrap; }}
    .gantt-table th, .gantt-table td {{ border:1px solid #f1f5f9; }}
    .g-datehead {{ text-align:center;font-size:10px;color:#64748b;padding:5px 0;font-weight:normal;min-width:42px;background:#fafbfc; }}
    .g-datehead.today {{ background:#ede9fe;color:#7c3aed;font-weight:600; }}
    .g-wolabel {{ position:sticky;left:0;z-index:2;background:#fff;padding:8px 12px;min-width:190px;border-right:2px solid #e2e8f0;vertical-align:top; }}
    .g-rowcell {{ position:relative;height:0;padding:0; }}
    /* 进度条:平时裁切圆角(段不露方角);仅当某工序段被 hover 时放开溢出,让上浮阴影可见 */
    .gbar {{ overflow:hidden; }}
    .gbar:has(.gengtri) {{ overflow:visible; }}
    .gbar:has(.gseg:hover) {{ overflow:visible; }}
    /* 工序小格:鼠标经过动态高亮(提亮+描边+浮起),配合上方自定义浮层显示工序与CT */
    .gseg {{ transition:filter .15s ease, box-shadow .15s ease, transform .15s ease; }}
    .gseg:hover {{ filter:brightness(1.08) saturate(1.06);
      box-shadow:0 5px 16px rgba(15,23,42,.22), inset 0 0 0 1.5px rgba(255,255,255,.9) !important;
      transform:translateY(-2px); z-index:9 !important; cursor:pointer; border-radius:3px; }}
    #gseg_tip {{ display:none;position:fixed;z-index:60;pointer-events:none;background:#0f172a;color:#fff;
      font-size:12px;line-height:1.5;padding:6px 11px;border-radius:7px;box-shadow:0 6px 18px rgba(0,0,0,.32);
      white-space:pre-line;transform:translate(-50%,-100%);opacity:0;transition:opacity .1s ease; }}
    #gseg_tip::after {{ content:"";position:absolute;left:50%;bottom:-5px;transform:translateX(-50%);
      border:5px solid transparent;border-top-color:#0f172a;border-bottom:0; }}
    /* 挂起斜纹带 hover 浮层:粉色背景(与斜纹带同色系),区别于工序的深色浮层 */
    #hold_tip {{ display:none;position:fixed;z-index:61;pointer-events:none;background:#fee2e2;color:#b91c1c;
      border:1px solid #f87171;font-size:12px;line-height:1.5;padding:6px 11px;border-radius:7px;
      box-shadow:0 6px 18px rgba(185,28,28,.28);white-space:pre-line;max-width:300px;
      transform:translate(-50%,-100%);opacity:0;transition:opacity .1s ease; }}
    #hold_tip::after {{ content:"";position:absolute;left:50%;bottom:-6px;transform:translateX(-50%);
      border:6px solid transparent;border-top-color:#f87171;border-bottom:0; }}
    </style>
    <script>
    var GANTT_DATA = {data_json};
    var TODAY = "{today}";
    var NOW_MIN0 = {now_min};          // 服务器(北京)渲染时刻的当日分钟数 —— 现在线以此为锚,不依赖客户端时区/绝对时钟
    var PAGE_LOAD_TS = Date.now();     // 仅用客户端时钟测"页面打开后流逝的时长",不取其绝对值
    var IS_AUTHED = {authed_js};
    var RISK_FILTER = "all";
    function toggleGanttNote() {{
      var box=document.getElementById('gNoteBox'), ar=document.getElementById('gNoteArrow');
      var open=box.style.display==='none';
      box.style.display=open?'block':'none'; if(ar) ar.textContent=open?'▴':'▾';
    }}
    function setRisk(card) {{
      RISK_FILTER = card.getAttribute('data-risk');
      document.querySelectorAll('.dash-card').forEach(function(b){{ b.removeAttribute('data-active'); }});
      card.setAttribute('data-active','1');
      var nm = (RISK_FILTER==='all') ? '' : ('· 仅显示「'+RISK_FILTER+'」');
      document.getElementById('g_riskname').textContent = nm;
      gantt.render();
    }}
    var gantt = (function() {{
      var DAY_W = 42, viewStart = null, lastSig = null;
      var _nowD0 = null, _nowDayW = 42, _nowGridW = 0;   // 供「现在」线每分钟自动右移用(render 时刷新)
      function parseD(s) {{ if(!s) return null; var parts=s.split(' '); var dp=parts[0].split('-'); var tp=(parts[1]||'00:00').split(':'); return new Date(+dp[0],+dp[1]-1,+dp[2], +tp[0]||0, +tp[1]||0); }}
      // 公司一天从 07:30 开始算(= 450 分钟),24H 起点不是午夜而是 7:30。
      var DAY_START_MIN = 450;
      var DAY_START_MS = DAY_START_MIN * 60 * 1000;   // 7:30 毫秒偏移
      // 返回北京当下的真实 Date(服务器时刻 + 页面打开后流逝分钟),不依赖客户端时区
      function nowDate() {{
        var elapsed = Math.max(0, (Date.now()-PAGE_LOAD_TS)/60000);
        var base = parseD(TODAY) || new Date();
        return new Date(base.getTime() + (NOW_MIN0+elapsed)*60000);
      }}
      // 取时刻所属的班次日起点(7:30)——早于 7:30 归前一天
      function prodFloor(d) {{
        var f = new Date(d);
        f.setHours(7, 30, 0, 0);
        if (f.getTime() > d.getTime()) f.setDate(f.getDate() - 1);
        return f;
      }}
      // 纯日期(无时分秒)→ 补 7:30 业务起点;已有时间的保持不变
      function toBizStart(d) {{
        if (!d) return d;
        if (d.getHours()===0 && d.getMinutes()===0 && d.getSeconds()===0)
          return new Date(d.getTime() + DAY_START_MS);
        return d;
      }}
      function _hm(min) {{ var h=Math.floor(min/60)%24, m=Math.floor(min%60); return (h<10?'0':'')+h+':'+(m<10?'0':'')+m; }}
      function fmtMD(d) {{ return (d.getMonth()+1)+'月'+d.getDate()+'日'; }}
      // 挂起/恢复日显示格式:2026-06-25 → 2026/06/25;含时间则 2026/06/25 14:30
      function fmtSlash(s) {{ if(!s) return s; var parts=s.split(' '); var d=parts[0].replace(/-/g,'/'); return parts[1] ? (d+' '+parts[1]) : d; }}
      function dayDiff(a,b) {{ return (b-a)/86400000; }}
      function addDays(d,n) {{ var r=new Date(d); r.setDate(r.getDate()+n); return r; }}

      function dataRange(list) {{
        var starts=[], ends=[];
        list.forEach(function(o){{
          var s=toBizStart(parseD(o.plan_start)), e=toBizStart(parseD(o.plan_end));
          if(s) starts.push(s); if(e) ends.push(e);
          // 工序条铺到「开工→ETA」,ETA(真实完工)可能晚于交期 → 轴右界要把 ETA 算进去,避免条被裁掉;
          // 无 ETA 时退回「开工+标准CT天」作右界估计。
          var etaD2=o.eta?toBizStart(parseD(o.eta)):null;
          if(etaD2) ends.push(etaD2);
          var dR=(o.disp_ratio!=null && o.disp_ratio>0)?o.disp_ratio:1;
          var ctD=(o.adj_total_ct_h>0)?Math.round(o.adj_total_ct_h/24*dR):0;
          if(s && ctD>0) ends.push(addDays(s, ctD));
        }});
        var today = parseD(TODAY);
        if(today) ends.push(today);   // 把"今天"纳入右界,保证「现在」线在拖很久的异常单上也不被裁掉
        var d0 = starts.length? prodFloor(new Date(Math.min.apply(null,starts))) : prodFloor(today||new Date());
        var d1 = ends.length? new Date(Math.max.apply(null,ends)) : addDays(prodFloor(today||new Date()),14);
        // 起始=最早计划开始日(不留白);结束=最晚交期+2天
        d1 = addDays(d1, 2);
        return [d0,d1];
      }}

      function selectedProds() {{
        var arr=[];
        document.querySelectorAll('.g-prodcb:checked').forEach(function(cb){{ arr.push(cb.value); }});
        return arr;
      }}
      function selectedOwners() {{
        var arr=[];
        document.querySelectorAll('.g-ownercb:checked').forEach(function(cb){{ arr.push(cb.value); }});
        return arr;
      }}
      function riskRank(rl) {{
        // 风险排序权重:高风险最前
        return {{'高风险':0,'低风险':1,'正常':2,'超前':3}}[rl||''] != null
             ? {{'高风险':0,'低风险':1,'正常':2,'超前':3}}[rl||''] : 4;
      }}
      function prioRank(p) {{
        // 优先级排序权重:火箭批最前
        var m = {{'火箭批':0,'1-0':1,'1-1':2,'Normal':3}};
        return m[p||''] != null ? m[p||''] : 4;
      }}
      function slackVal(o) {{
        // 从 risk_note 文本解析富余天数:超前=正,滞后=负。滞后越多值越小 → 升序时越靠前。
        // 无明确天数(已完成 / 当前工序不在CT表)→ +∞,排在该风险等级末尾。
        var n = o.risk_note || '';
        var m = n.match(/滞后\\s*([\\d.]+)\\s*天/);
        if(m) return -parseFloat(m[1]);
        m = n.match(/超前\\s*([\\d.]+)\\s*天/);
        if(m) return parseFloat(m[1]);
        return Infinity;
      }}
      function filtered() {{
        var prods=selectedProds();
        var owners=selectedOwners();
        var kw=document.getElementById('g_search').value.trim().toLowerCase();
        var list = GANTT_DATA.filter(function(o){{
          if(RISK_FILTER!=='all' && (o.risk_level||'')!==RISK_FILTER) return false;
          if(prods.length>0 && prods.indexOf(o.product.split('/')[0].trim())<0) return false;
          if(owners.length>0 && owners.indexOf((o.owner||'').trim())<0) return false;
          if(kw && !o.wo_no.toLowerCase().endsWith(kw) && !o.product.toLowerCase().endsWith(kw)) return false;
          return true;
        }});
        // 排序
        var sortBy = document.getElementById('g_sort').value;
        function dueVal(o){{ if(!o.plan_end) return Infinity; var p=o.plan_end.split('-'); return new Date(+p[0],+p[1]-1,+p[2]).getTime(); }}
        // 不再让高风险/问题工单强制浮顶:完全按所选排序方式排列(高亮仍保留)。
        // 想优先看风险,选「按风险排序」即可。
        list.sort(function(a,b){{
          if(sortBy==='risk') {{
            var d = riskRank(a.risk_level) - riskRank(b.risk_level);
            if(d!==0) return d;                          // ① 风险等级:高>低>正常>超前
            var sd = slackVal(a) - slackVal(b);
            if(sd!==0) return sd;                        // ② 滞后天数:滞后越多越靠前
            var dp = prioRank(a.priority) - prioRank(b.priority);
            if(dp!==0) return dp;                        // ③ 优先级:火箭批>1-0>1-1>Normal
            return dueVal(a) - dueVal(b);                // ④ 交期近优先(兜底)
          }}
          if(sortBy==='prio') {{
            var dp = prioRank(a.priority) - prioRank(b.priority);
            if(dp!==0) return dp;
            return dueVal(a) - dueVal(b);   // 同优先级按交期近优先
          }}
          // 默认:交期最近优先(plan_end 升序)
          return dueVal(a) - dueVal(b);
        }});
        return list;
      }}

      function statusColor(o) {{
        if(o.status==='出站'||o.status==='已入库') return '#22c55e';
        if(o.risk_level==='高风险') return '#ef4444';
        if(o.status==='运行中') return '#3b82f6';
        if(o.status==='等待进站'||o.status==='HOLD') return '#3b82f6';
        return '#cbd5e1';
      }}

      function updateCards() {{
        // 顶部卡片随【产品型号 / 搜索】筛选实时重算(不含风险卡片自身的筛选,
        // 否则点了某张卡片后其它卡片会全部归零)。口径与甘特图一致:GANTT_DATA 即未完成工单。
        var prods=selectedProds();
        var kw=document.getElementById('g_search').value.trim().toLowerCase();
        var cnt={{'all':0,'超前':0,'正常':0,'低风险':0,'高风险':0}};
        GANTT_DATA.forEach(function(o){{
          if(prods.length>0 && prods.indexOf(o.product.split('/')[0].trim())<0) return;
          if(kw && !o.wo_no.toLowerCase().endsWith(kw) && !o.product.toLowerCase().endsWith(kw)) return;
          cnt.all++;
          var rl=o.risk_level||''; if(cnt[rl]!=null) cnt[rl]++;
        }});
        document.querySelectorAll('.dash-card').forEach(function(c){{
          var r=c.getAttribute('data-risk'), v=c.querySelector('.v');
          if(v && cnt[r]!=null) v.textContent=cnt[r];
        }});
      }}

      function render() {{
        updateCards();
        var list = filtered();
        // 时间轴范围按【当前筛选后的工单】计算,使进度条左对齐到筛选结果的最早计划开始日
        var rng = dataRange(list);
        // 筛选条件(产品/风险/搜索)变化时重置平移,重新左对齐;手动翻页(‹ ›)不在此列
        var sig = selectedProds().join(',') + '|' + RISK_FILTER + '|'
                + document.getElementById('g_search').value.trim().toLowerCase();
        if(sig !== lastSig) {{ viewStart = null; lastSig = sig; }}
        if(!viewStart) viewStart = rng[0];
        var d0 = viewStart;
        // span = 数据范围总天数(动态)
        var span = dayDiff(rng[0], rng[1]) + 1;
        var today = parseD(TODAY);
        var todayProd = prodFloor(nowDate());   // 当前班次日起点(7:30)
        var LABEL_W = 210;
        // 自适应:日宽 = (容器可用宽 - 工单列) / 天数,最小 28px 保证可读
        // 天数少时整体铺满窗口;天数多时退回最小宽度并允许横向滚动
        var MIN_DAY_W = 28;
        var wrap = document.getElementById('gantt_wrap');
        var avail = (wrap ? wrap.clientWidth : 0) - LABEL_W - 2;  // 减边框
        var dayW = (span > 0 && avail > 0) ? (avail / span) : DAY_W;
        if (dayW < MIN_DAY_W) dayW = MIN_DAY_W;
        var gridW = span * dayW;
        _nowD0 = d0; _nowDayW = dayW; _nowGridW = gridW;   // 缓存给「现在」线定时器
        var showDue = document.getElementById('g_due').checked;

        // ===== 表头(日期轴) =====
        // 日期格用绝对定位 left=i*dayW —— 与下方轨道天格(同样 left=gi*dayW)共用一套坐标。
        // 原来表头用 flex 逐格排布,当 dayW 为小数时浏览器会把每格四舍五入到整数像素,
        // 多列累积后整体右移,导致顶部日期与下方格子错位。改绝对定位后两者像素级对齐。
        var HEAD_H = 38, DATE_H = 24;   // 日期格仍 24px(紧凑不变),只在其上方留一条窄带放「现在」标签
        // 表头外层补 4px 透明左边框:与数据行的「问题工单红色左边条」(border-left:4px)等宽,
        // 否则数据行被这 4px 整体右推,导致顶部日期与下面每一列恒定错位 ~4px。
        // 表头层级必须高于正文里的「现在」线(z-index:7)等元素,否则滚动时正文竖线会盖到表头、穿过日期格
        var head = '<div style="display:flex;position:sticky;top:0;z-index:15;background:#fafbfc;border-bottom:1px solid #e2e8f0;border-left:4px solid transparent;">';
        head += '<div style="width:'+LABEL_W+'px;flex:0 0 '+LABEL_W+'px;padding:6px 12px;font-size:12px;color:#64748b;border-right:2px solid #e2e8f0;position:sticky;left:0;background:#fafbfc;z-index:16;">工单</div>';
        head += '<div style="position:relative;width:'+gridW+'px;flex:0 0 '+gridW+'px;height:'+HEAD_H+'px;">';
        for(var i=0;i<span;i++) {{
          var d=addDays(d0,i);
          var isToday = todayProd && d.getTime()===todayProd.getTime();
          // 窄列时只显示日号(并在每月1号或首列标注月份),宽列显示完整 X月X日
          var lbl;
          if(dayW < 40) {{
            lbl = (d.getDate()===1 || i===0) ? ((d.getMonth()+1)+'/'+d.getDate()) : (''+d.getDate());
          }} else {{
            lbl = fmtMD(d);
          }}
          // 相邻日期交替底色(奇数日略深),与轨道区天格对齐,让天与天一眼区分
          var hbg = isToday ? '#ede9fe' : (i%2===1 ? '#e3f3ea' : '#fafbfc');
          head += '<div style="position:absolute;top:'+(HEAD_H-DATE_H)+'px;left:'+(i*dayW)+'px;width:'+dayW+'px;height:'+DATE_H+'px;box-sizing:border-box;display:flex;align-items:center;justify-content:center;font-size:10px;color:'+(isToday?'#7c3aed':'#64748b')+';font-weight:'+(isToday?'600':'normal')+';border-left:1px solid #f1f5f9;background:'+hbg+';overflow:hidden;">'+lbl+'</div>';
        }}
        // 「现在」时间标签:贴在现在线顶端,显示北京 HH:MM,便于一眼核对线的位置是否对得上墙上的钟
        {{
          var _hNowD=nowDate();
          var _hNowX=dayDiff(d0,_hNowD)*dayW;
          if(_hNowX>=0 && _hNowX<=gridW)
            head += '<div class="gnowlbl" style="position:absolute;top:1px;left:'+_hNowX+'px;transform:translateX(-50%);background:#ea580c;color:#fff;font-size:10px;font-weight:700;padding:0 4px;border-radius:3px;white-space:nowrap;z-index:6;pointer-events:none;">现在 '+_hm(_hNowD.getHours()*60+_hNowD.getMinutes())+'</div>';
        }}
        head += '</div></div>';

        // ===== 数据行 =====
        var bodyH = '';
        if(list.length===0) {{
          bodyH = '<div class="empty"><div class="ico">📭</div><div class="tt">没有符合条件的工单</div>'
                + '<div class="sb">试试切换上方风险筛选 / 清空搜索,或确认已从 MES 同步工单</div></div>';
        }}
        list.forEach(function(o, ri){{
          var s=toBizStart(parseD(o.plan_start)), e=toBizStart(parseD(o.plan_end));
          // 问题工单(高风险 / 预计产出晚于交付日):整行淡红底 + 左侧红边条,一眼定位
          var etaLate = !!(o.eta && e && toBizStart(parseD(o.eta)) && toBizStart(parseD(o.eta)).getTime()>e.getTime());
          var prob = (o.risk_level==='高风险') || etaLate;
          var rowBg = prob ? '#fff5f5' : ((ri % 2 === 1) ? '#fbfcfe' : '#ffffff');
          var row = '<div style="display:flex;border-bottom:1px solid #eef2f7;border-left:4px solid '+(prob?'#dc2626':'transparent')+';min-height:52px;background:'+rowBg+';">';
          // 左侧标签(sticky)—— 紧凑布局
          row += '<div style="width:'+LABEL_W+'px;flex:0 0 '+LABEL_W+'px;padding:7px 12px;border-right:2px solid #e2e8f0;position:sticky;left:0;background:'+rowBg+';z-index:2;font-size:11px;line-height:1.5;">';
          // 风险配色
          var rl=o.risk_level||'';
          var rc={{'高风险':'#dc2626','低风险':'#ca8a04','超前':'#16a34a','正常':'#1f2937'}}[rl]||'#94a3b8';
          var rbg={{'高风险':'#fee2e2','低风险':'#fef9c3','超前':'#dcfce7','正常':'#f1f5f9'}}[rl]||'#f1f5f9';
          // 优先级徽章:火箭批红底黄字 / 1-0红底黑字 / 1-1黄底黑字 / Normal浅蓝底黑字
          var pri=(o.priority||'').trim();
          var pc={{'火箭批':['#dc2626','#fde047'],'1-0':['#dc2626','#1f2937'],'1-1':['#fde047','#1f2937'],'Normal':['#bfdbfe','#1f2937']}}[pri];
          var priHtml = pc ? ('<span title="优先级" style="background:'+pc[0]+';color:'+pc[1]+';padding:1px 5px;border-radius:3px;font-size:10px;font-weight:700;white-space:nowrap;">'+pri+'</span>') : '';
          // 运行状态徽章:运行中绿 / 等待进站黄 / HOLD红 / 等待出站淡蓝
          var st=(o.status||'').trim();
          var sc={{'运行中':['#bbf7d0','#15803d'],'等待进站':['#fde047','#1f2937'],'HOLD':['#fecaca','#b91c1c'],'等待出站':['#60a5fa','#1e40af']}}[st]||['#f1f5f9','#475569'];
          var stHtml = st ? ('<span title="运行状态" style="background:'+sc[0]+';color:'+sc[1]+';padding:0 4px;border-radius:3px;white-space:nowrap;font-weight:600;">'+st+'</span>') : '';
          // 交付日(紫色,与交付日竖线呼应)
          var dueHtml = o.plan_end ? ('<span style="color:#a855f7;font-size:10px;font-weight:600;white-space:nowrap;" title="计划交期 '+o.plan_end+'">交期 '+o.plan_end.slice(5)+'</span>') : '';
          // 第1行:优先级徽章(单独一行,无则不占行)
          if(priHtml) row += '<div style="margin-bottom:2px;">'+priHtml+'</div>';
          // 第2行:工单号(可点) + 交付日
          row += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;flex-wrap:wrap;"><b style="color:#2563eb;cursor:pointer;font-size:13px;" onclick="showDetail(\\''+o.wo_no+'\\')">'+o.wo_no+'</b>'+dueHtml+'</div>';
          // 第2行(次信息):状态徽章 + 风险徽标 + 产品型号 + 进度
          row += '<div style="display:flex;justify-content:space-between;align-items:center;gap:4px;">';
          row += '<span style="display:flex;align-items:center;gap:3px;min-width:0;flex-wrap:wrap;">'+stHtml+(rl? ('<span style="background:'+rbg+';color:'+rc+';padding:0 4px;border-radius:3px;white-space:nowrap;font-weight:600;">'+rl+'</span>') : '')
               + '<span style="color:#475569;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'+(o.product||'')+'</span></span>';
          row += '<span style="color:#16a34a;flex:0 0 auto;font-weight:600;">'+o.progress+'%</span></div>';
          // 应到位置:按【标准CT】从计划开始日走到【今天】本该到的进度(计划应到),不依赖交期。
          //   = 已过天数(计划开始→今天)÷ 标准CT全程天数 × 终点占比。
          //   与「实到」对比即可直读快/慢于计划:应到>实到=慢于计划,应到<实到=超前。
          //   交期只用于算 Buffer,绝不参与应到/产出预测,避免循环论证(交期越晚 Buffer 越大)。
          var segs0=o.ct_segs||[];
          var expName='', expPct=null;
          // 终点:选了完成工序则到该工序占比即 100%,否则整条 CT
          var endPct0=(o.target_ratio!=null) ? o.target_ratio*100 : 100;
          var ctDaysExp=(o.total_ct_h>0)?(o.total_ct_h/24):0;   // 标准CT全程天数(不乘 disp)
          if(s && today && ctDaysExp>0) {{
            var elapsedExp=dayDiff(s, today);                   // 计划开始 → 今天 已过天数
            expPct=Math.max(0, Math.min(endPct0, elapsedExp/ctDaysExp*100));
          }}
          // 退化:无 CT 表时回退到原日历线性口径,至少给个参照
          if(expPct==null && s && e && segs0.length>0 && today) {{
            var totalD=dayDiff(s,e)+1, elapsed=dayDiff(s,today);
            expPct=Math.max(0,Math.min(100, elapsed/totalD*100));
            if(o.target_ratio!=null) expPct=Math.min(expPct, o.target_ratio*100);
          }}
          if(expPct!=null && segs0.length>0) {{
            for(var k=0;k<segs0.length;k++) {{
              if((segs0[k].cum_ratio+segs0[k].ratio)*100 >= expPct) {{ expName=segs0[k].name||segs0[k].spec; break; }}
            }}
            if(!expName && segs0.length) expName=segs0[segs0.length-1].name||segs0[segs0.length-1].spec;
          }}
          if(expName) row += '<div style="color:#b45309;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="应到: '+expName+'">应到: '+expName+'</div>';
          // 实到:用首批(head_spec)的实际工序名,segs 里按 SPECNAME 取中文名
          var curCn='-';
          if(o.head_spec) {{
            for(var k2=0;k2<segs0.length;k2++) {{
              if(segs0[k2].spec===o.head_spec) {{ curCn=segs0[k2].name||segs0[k2].spec; break; }}
            }}
            if(curCn==='-') curCn=o.head_spec;
          }} else if(segs0.length>0) {{
            var hp0=o.head_pct||0;
            for(var k3=0;k3<segs0.length;k3++) {{
              if((segs0[k3].cum_ratio+segs0[k3].ratio)*100 >= hp0) {{ curCn=segs0[k3].name||segs0[k3].spec; break; }}
            }}
            if(curCn==='-' && segs0.length) curCn=segs0[segs0.length-1].name;
          }} else {{
            var cur=o.cur_process||'-'; var m=cur.match(/\\(([^)]+)\\)/); curCn=m?m[1]:cur;
          }}
          row += '<div style="color:#475569;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="实到: '+curCn+'">实到: '+curCn+'</div>';
          // Buffer:预计产出日(ETA,按实测CT)与交付日的天数差,直观看交期能否达成
          if(o.eta && e) {{
            var etaD0=toBizStart(parseD(o.eta));
            if(etaD0) {{
              var buf=dayDiff(etaD0, e);   // 交付日 − 预计产出日;>0 表示有富余
              var etaMD=(etaD0.getMonth()+1)+'/'+etaD0.getDate();
              var vtag=(o.target_ratio!=null)? ' →完成工序':'';
              // Buffer(有富余/早于交付)仅管理员可见;滞后(晚于交付)所有人可见
              if(buf>=0) {{ if(IS_AUTHED) row += '<div style="color:#0284c7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="预计产出 '+o.eta+vtag+',早于交付 '+buf+' 天">预计产出'+etaMD+' · Buffer '+buf+'天</div>'; }}
              else row += '<div style="color:#dc2626;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="预计产出 '+o.eta+vtag+',晚于交付 '+(-buf)+' 天">预计产出'+etaMD+' · 滞后'+(-buf)+'天</div>';
            }}
          }}
          // 挂起状态:所有人可见(含匿名,只读徽标)。
          // 管理员额外可操作:挂起中点击编辑/解除;空闲时点击发起挂起(填恢复日+原因)。
          var hb='';
          if(o.resume_date) {{
            // 徽标只保留一行「⏸挂起·恢复日」(精简不拥挤);工序+原因移到甘特色块 hover 浮层显示
            var _hn=(o.holds&&o.holds.length)||0;
            var holdTxt='⏸挂起'+(_hn>1?('·'+_hn+'处'):'')+'·恢复'+fmtSlash(o.resume_date);
            if(IS_AUTHED) {{
              hb='<button onclick="holdWO(event,\\''+o.wo_no+'\\')" title="挂起中,点击编辑或解除" style="margin-top:3px;border:1px solid #f87171;background:#fef2f2;color:#b91c1c;border-radius:5px;font-size:11px;padding:1px 6px;cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+holdTxt+'</button>';
            }} else {{
              // 匿名:只读徽标(无点击,无编辑权限),但挂起状态一目了然
              hb='<span title="该工单已挂起,等待恢复作业" style="display:inline-block;margin-top:3px;border:1px solid #f87171;background:#fef2f2;color:#b91c1c;border-radius:5px;font-size:11px;padding:1px 6px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+holdTxt+'</span>';
            }}
          }} else if(IS_AUTHED) {{
            hb='<button onclick="holdWO(event,\\''+o.wo_no+'\\')" title="标记挂起:填恢复作业日+原因" style="margin-top:3px;border:1px solid #cbd5e1;background:#f8fafc;color:#64748b;border-radius:5px;font-size:11px;padding:1px 6px;cursor:pointer;">⏸挂起</button>';
          }}
          if(hb) row += '<div>'+hb+'</div>';
          row += '</div>';
          // 轨道区
          row += '<div style="position:relative;width:'+gridW+'px;flex:0 0 '+gridW+'px;background:#ffffff;">';

          // 天与天交替着色:相邻日期一白一灰白(中性底,让绿色进度条凸显)+ 右侧浅灰分隔线,
          // 让每一天在视觉上清晰分隔、醒目区分(原来只有一条细竖线不够明显)。
          for(var gi=0; gi<span; gi++) {{
            var dayBg = (gi % 2 === 1) ? '#f8fafc' : '#ffffff';
            row += '<div style="position:absolute;top:0;bottom:0;left:'+(gi*dayW)+'px;width:'+dayW+'px;box-sizing:border-box;background:'+dayBg+';border-right:1px solid #e2e8f0;"></div>';
          }}
          // 计划范围背景:中性浅蓝(避免与绿色进度条混淆)
          if(s && e) {{
            var off=dayDiff(d0,s), len=dayDiff(s,e)+1;
            if(off+len>0 && off<span) {{
              var L=Math.max(off,0)*dayW;
              var W=(Math.min(off+len,span)-Math.max(off,0))*dayW;
              row += '<div style="position:absolute;top:0;bottom:0;left:'+L+'px;width:'+W+'px;background:#dbeafe;opacity:.45;"></div>';
            }}
          }}
          // 今日竖列高亮(整列,加深)
          if(todayProd) {{
            var toff=Math.round(dayDiff(d0,todayProd));
            if(toff>=0 && toff<span)
              row += '<div style="position:absolute;top:0;bottom:0;left:'+(toff*dayW)+'px;width:'+dayW+'px;background:#ddd6fe;opacity:.6;"></div>';
          }}
          // 「现在」线:北京当下真实时刻在日历轴的位置 = 此刻"应到"的进度;每分钟右移。
          // 锚在服务器北京时刻(nowDate),与客户端时区/电脑时钟无关;甩到工序条右边一大截 = 异常。
          {{
            var _nowD=nowDate();
            var nowX=dayDiff(d0,_nowD)*dayW;
            if(nowX>=0 && nowX<=gridW)
              row += '<div class="gnow" title="现在 '+_hm(_nowD.getHours()*60+_nowD.getMinutes())+'(北京)· 应到此刻的进度" style="position:absolute;top:0;bottom:0;left:'+nowX+'px;width:2px;background:#ea580c;z-index:7;pointer-events:none;"></div>';
          }}
          // 进度条(铺满计划区间)。缺计划日期(无交期/无开始日)时同样要画工序图:
          // 用 today(无则 d0)兜底锚点 + 默认约2周宽度占位,横向位置非按日历,仅示意工序进度。
          {{
            var hasDates=!!(s && e), DEF_SPAN=13;   // 缺日期时默认约2周宽度占位
            // 【工序条铺设口径】真实CT尺:从开工日(plan_start,无则今天)起,严格按【上传CT】铺设——
            //  1天 = 24H,每道工序宽度 = 该工序CT ÷ 24 天,工序间无间隙。条总长 = 总CT ÷ 24 天,
            //  尽头 = 计划开始 + 总CT(= 按CT本该完工的日子)。故条子绝不被拉伸:7H 的工序就画成
            //  7/24 ≈ 0.3 天的小块,不再横跨整天。等待时间已由维护者自行计入各工序CT,故不另加间隙。
            //  这样「现在」线一旦甩到条子右边一大截,即一眼暴露异常(早该完工却还压在表上)。
            //  设了「完成工序」时只画到该工序(见 disp_ratio);有挂起则 holdOn 分支把后程顺延。
            //  仅在「无 CT 表」时才退回 ETA / 计划区间 兜底。
            var dispR=(o.disp_ratio!=null && o.disp_ratio>0)?o.disp_ratio:1;
            var ctDays=(o.adj_total_ct_h>0)?(o.adj_total_ct_h/24*dispR):0;   // ENG调整后CT天数(1天=24H)
            var etaD=o.eta?toBizStart(parseD(o.eta)):null;
            var sEff, len2;
            // 起点:有计划开始日就从它起;为空则从【今天】起。
            if(s) {{ sEff=s; }}
            else {{ sEff=todayProd||d0; }}
            if(ctDays>0) {{ len2=ctDays; }}                                      // 主口径:开工 + 真实总CT(1天=24H,不取整,保证小块按真实CT占位)
            else if(etaD && dayDiff(sEff, etaD)>=1) {{ len2=dayDiff(sEff, etaD)+1; }}  // 无CT表:退回 开工→ETA
            else if(s && e) {{ len2=dayDiff(s,e)+1; }}                            // 退回原计划区间宽度
            else {{ len2=DEF_SPAN+1; }}
            var off2=dayDiff(d0,sEff);
            if(len2<1) len2=1;
            var barL=off2*dayW, barW=len2*dayW;
            var segs=o.ct_segs||[];
            var tailP = o.tail_pct||0;   // 尾批(最慢)完成到此
            var headP = o.head_pct||0;   // 首批(最快)完成到此
            if(headP < tailP) {{ var t=headP; headP=tailP; tailP=t; }}  // 保证 head>=tail
            // ===== 人工挂起(按工序):被挂起工序及其之后整体推到恢复日,之前正常显示 =====
            // 间隙(粉斜纹)= 被挂起工序的自然起点 → 恢复日;被挂起工序+其后 = 恢复日 → 顺延后ETA。
            // 例:3454挂"翘曲测量"恢复6/21 → 翘曲及之后推到6/21,粉斜纹填到6/21前;
            //     产品还在PMC(翘曲之前)→ 蓝色在制块正常显示;走到翘曲才落到斜纹之后。
            // 无CT数据 / 找不到该工序 / 未设恢复日 → holdOn=false,退回下方"叠加斜纹带"兜底。
            // ===== 人工挂起(可多条):每条挂起的工序起点→恢复日各插一段间隙(粉斜纹) =====
            // 累计算法:沿 CT 推进,到每个被挂起工序时,若当前显示时间早于其恢复日,则跳到恢复日(插间隙),再继续。
            // 多条间隙逐段累加;进度条容器 = 开工 → 顺延后ETA。无有效挂起时退回原线性映射。
            var holdOn=false, holdEtaOff=null, totBarDaysH=0, gapsH=[];
            var EPSH=1e-9;
            var ctDaysFull=(o.total_ct_h>0)?(o.total_ct_h/24):0;
            var planH = s || (todayProd||d0);          // 挂起锚点:计划开工日(无则今天)
            var planOffH=dayDiff(d0, planH);
            var holdsH=[];
            if(ctDaysFull>0 && segs.length>0 && (o.holds||[]).length>0) {{
              (o.holds||[]).forEach(function(h){{
                if(!h.hold_spec || !h.resume_date) return;
                var hf=-1, hnm='';
                for(var hi=0; hi<segs.length; hi++) {{
                  if(segs[hi].spec===h.hold_spec) {{ hf=Math.max(0,Math.min(dispR,segs[hi].cum_ratio)); hnm=segs[hi].name||segs[hi].spec; break; }}
                }}
                if(hf<0) return;                                   // 工序不在CT路线,无法定位
                holdsH.push({{frac:hf, resumeOff:dayDiff(d0,toBizStart(parseD(h.resume_date))), name:hnm, resume:h.resume_date, reason:h.reason||''}});
              }});
              holdsH.sort(function(a,b){{ return a.frac-b.frac; }});
              var cur=planOffH, last=0;                            // 走一遍算各间隙(显示天偏移)与顺延ETA
              holdsH.forEach(function(hh){{
                cur += ctDaysFull*(hh.frac-last);
                if(hh.resumeOff>cur+EPSH) {{ hh._dayStart=cur; hh._dayEnd=hh.resumeOff; cur=hh.resumeOff; }}
                else {{ hh._dayStart=null; }}                       // 恢复日早于自然到达 → 不产生间隙
                last=hh.frac;
              }});
              cur += ctDaysFull*(dispR-last);
              holdOn = holdsH.some(function(hh){{ return hh._dayStart!=null; }});
              if(holdOn) {{
                holdEtaOff=cur; totBarDaysH=cur-planOffH;
                barL=planOffH*dayW; barW=totBarDaysH*dayW;          // bar 容器=开工→顺延后ETA
                holdsH.forEach(function(hh){{
                  if(hh._dayStart!=null) gapsH.push({{ l:((hh._dayStart-planOffH)/totBarDaysH)*100,
                       w:((hh._dayEnd-hh._dayStart)/totBarDaysH)*100, name:hh.name, resume:hh.resume, reason:hh.reason }});
                }});
              }}
            }}
            // 累计间隙天数:某点之前(工序起点 a 之前/含位于 a 的挂起)已发生的间隙总和。
            var gapDaysBefore=function(a){{
              var s=0;
              for(var gi=0; gi<holdsH.length; gi++) {{
                if(holdsH[gi]._dayStart!=null && holdsH[gi].frac<=a+EPSH) s+=(holdsH[gi]._dayEnd-holdsH[gi]._dayStart);
              }}
              return s;
            }};
            // CT 分数(+该点之前累计间隙)→ bar 容器百分比。无挂起时按 dispR 线性。
            var segPct=function(frac, gapd){{
              if(!holdOn) return (Math.min(frac,dispR)/dispR)*100;
              return ((ctDaysFull*Math.min(frac,dispR)+gapd)/totBarDaysH)*100;
            }};
            // 单点(如"应到")映射:含该点之前所有间隙。
            var mapFrac=function(frac){{ return segPct(frac, gapDaysBefore(frac)); }};
            // 用实际工序名定位首/尾批所在段(优先,避免 pct 反查漂移)
            var tailIdx=-1, headIdx=-1;
            if(segs.length>0) {{
              for(var si=0; si<segs.length; si++) {{
                if(o.tail_spec && segs[si].spec===o.tail_spec) tailIdx=si;
                if(o.head_spec && segs[si].spec===o.head_spec) headIdx=si;
              }}
            }}
            var DONE='#bbf7d0', BLUE='#3b82f6', LIGHTBLUE='#bfdbfe', YELLOW='#f5c518', PINK='#fbcfe8', ENG_TEAL='#0d9488';
            // 当前真有 LOT 的工序集合:首尾间「在制」段里,无 LOT 的空工序刷浅蓝,
            // 一眼看出产品实际堆在哪几站。lot_specs 为空(老数据未重抓)时退回整段深蓝。
            var lotSet={{}}, hasLot=(o.lot_specs||[]).length>0;
            (o.lot_specs||[]).forEach(function(s){{ lotSet[s]=1; }});
            // 整站 LOT 全被忽略的残留站 → 浅粉(不计入尾批)
            var pinkSet={{}};
            (o.ignored_specs||[]).forEach(function(s){{ pinkSet[s]=1; }});
            var inner='';
            if(segs.length>0) {{
              segs.forEach(function(sg, idx){{
                // 完成工序之后的工序:甘特图不显示(到这一站即完成,其后站点直接不画)。
                // 含完成工序本身:其段起点 < disp_ratio(disp_ratio=完成工序终点累计),故保留。
                if(o.disp_ratio!=null && sg.cum_ratio >= o.disp_ratio-1e-6) return;
                var c, stat;
                // 有实际工序名锚点时按段序号判定:< 尾批=已完成, 尾批~首批=在制, > 首批=未作业
                if(tailIdx>=0 && headIdx>=0) {{
                  if(idx < tailIdx) {{ c=DONE; stat='已完成'; }}
                  else if(idx <= headIdx) {{ c=BLUE; stat='在制'; }}
                  else {{ c=YELLOW; stat='未作业'; }}
                }} else {{
                  var mid=(sg.cum_ratio + sg.ratio/2)*100;
                  c = (mid <= tailP) ? DONE : (mid <= headP ? BLUE : YELLOW);
                  stat = (mid <= tailP) ? '已完成' : (mid <= headP ? '在制' : '未作业');
                }}
                // 在制段细分:该工序当前没有 LOT → 浅蓝「无产品」
                if(c===BLUE && hasLot && !lotSet[sg.spec]) {{ c=LIGHTBLUE; stat='在制·无产品'; }}
                // 残留站覆盖:整站 LOT 全被忽略 → 浅粉,不论其它判定
                if(pinkSet[sg.spec]) {{ c=PINK; stat='残留·已忽略(不计)'; }}
                // ENG 验证时间:保持原状态颜色,仅在上面加红色小圆点标记
                // 位置按 disp_ratio 重新缩放:显示区间[0, disp_ratio]映射满整条工序条
                // 挂起时:被挂起工序起点之前正常,该工序及之后整段跳过间隙(推到恢复日之后)
                var segStart, segW;
                if(holdOn) {{
                  var _gd=gapDaysBefore(sg.cum_ratio);
                  segStart=segPct(sg.cum_ratio, _gd);
                  segW=segPct(sg.cum_ratio+sg.ratio, _gd)-segStart;
                }} else {{
                  segStart=sg.cum_ratio/dispR*100; segW=sg.ratio/dispR*100;
                }}
                // tooltip:工序名 + 时长(ENG优先,否则标准CT)
                var hh;
                if(sg.eng) {{
                  hh = '\\nENG Time：'+sg.ct_h+'h';
                  if(sg.eng_note) hh += '\\nENG需求：'+sg.eng_note;
                }} else {{
                  hh = (sg.ct_h!=null? (' · CT '+sg.ct_h+'h') : '');
                }}
                var bg=c;
                var tip=(sg.name||sg.spec||'')+' ('+stat+')'+hh;
                // 深蓝(有产品)/ 浅粉(残留)色块可点击,下钻该站 LOT 明细
                var clickable=(c===BLUE || c===PINK);
                var extra='', curs='';
                // 有 LOT 的可点色块给最小像素宽度+置顶:否则 0 时长工序(如良品 Bank、
                // 暂存/虚拟站)段宽为 0 → 色块画不出来、看不见也点不到,残留 lot 无法忽略。
                var minw=(clickable && c!==LIGHTBLUE) ? 'min-width:8px;z-index:2;' : '';
                if(clickable) {{
                  curs='cursor:pointer;';
                  var sn=String(sg.name||sg.spec||'').replace(/'/g,'');
                  extra=' onclick="segLots(event,\\''+o.wo_no+'\\',\\''+(sg.spec||'')+'\\',\\''+sn+'\\')"';
                  tip+=' · 点击看LOT';
                }}
                // 尾批(决定交期的最慢批)所在段:问题工单时红框圈出 → 视觉重心落到真正的风险源
                var ring=(idx===tailIdx && prob) ? 'outline:2px solid #dc2626;outline-offset:-2px;z-index:4;' : '';
                // 卡站站点:红色内描边(替代默认右白线),并叠左上角⏱角标
                var isOver=(o.over_specs||[]).indexOf(sg.spec)>=0;
                if(isOver) tip='⏱卡站超时 · '+tip;
                var shadow=isOver ? 'box-shadow:inset 0 0 0 2px #dc2626,inset -1px 0 0 rgba(255,255,255,.6);z-index:4;'
                                  : 'box-shadow:inset -1px 0 0 rgba(255,255,255,.6);';
                inner += '<div class="gseg" data-tip="'+tip+'"'+extra+' style="position:absolute;top:0;bottom:0;left:'+segStart+'%;width:'+segW+'%;'
                       + minw+ring+'background:'+bg+';'+curs+shadow+'box-sizing:border-box;"></div>';
                if(isOver) inner += '<div title="该工序已运行超过标准CT,疑似卡站/卡料" style="position:absolute;top:-2px;left:'+segStart+'%;font-size:10px;line-height:1;z-index:5;color:#dc2626;pointer-events:none;">⏱</div>';
                // ENG 验证时间:红色小圆点标记在色块顶部
                // ENG 验证时间:红色倒三角悬浮在色块顶部上方
                if(sg.eng) inner += '<div class="gengtri" title="ENG验证'+(sg.eng_note?(': '+sg.eng_note):'')+'" style="position:absolute;top:-7px;left:'+(segStart+segW/2)+'%;transform:translateX(-50%);width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:7px solid #dc2626;z-index:6;pointer-events:none;"></div>';
              }});
            }} else {{
              inner += '<div title="已完成工序" style="position:absolute;top:0;bottom:0;left:0;width:'+tailP+'%;background:'+DONE+';"></div>';
              inner += '<div title="在制工序" style="position:absolute;top:0;bottom:0;left:'+tailP+'%;width:'+(headP-tailP)+'%;background:'+BLUE+';"></div>';
              inner += '<div title="未作业工序" style="position:absolute;top:0;bottom:0;left:'+headP+'%;width:'+(100-headP)+'%;background:'+YELLOW+';"></div>';
            }}
            // 挂起间隙(可多条):每段挂起在 bar 容器内画粉色斜纹带 + ⏸角标 + 恢复日绿线;
            // hover 弹出粉色浮层显示工序+恢复日+原因。多条挂起逐段并列显示。
            gapsH.forEach(function(g){{
              var gtip=('⏸ 工序「'+g.name+'」做不了 · 恢复作业 '+fmtSlash(g.resume)
                        +(g.reason?('\\n原因：'+g.reason):''))
                        .replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
              inner += '<div class="ghold" data-tip="'+gtip+'" '
                     + 'style="position:absolute;top:0;bottom:0;left:'+g.l+'%;width:'+g.w+'%;cursor:help;'
                     + 'background:repeating-linear-gradient(45deg,#fca5a5,#fca5a5 5px,#fee2e2 5px,#fee2e2 10px);'
                     + 'border-left:1px solid #f87171;border-right:1px solid #f87171;z-index:3;opacity:.92;"></div>';
              inner += '<div style="position:absolute;top:-1px;left:'+g.l+'%;font-size:11px;line-height:1;z-index:5;color:#b91c1c;pointer-events:none;">⏸</div>';
              inner += '<div title="恢复作业日 '+fmtSlash(g.resume)+'(工序「'+g.name+'」起)" style="position:absolute;top:0;bottom:0;left:'+(g.l+g.w)+'%;width:2px;background:#16a34a;z-index:5;pointer-events:none;"></div>';
            }});
            // 进度条容器:垂直居中(整行高度内),白底,细边框。无计划日期时用虚线边框示意"非按日历"。
            // 「应到」不再画飘动竖标——改由全行高的「现在」线(上方 gnow)统一表示:现在线落在
            // 工序条内哪一段,即此刻应到的工序;落在条子右边 = 早该完工却还压在表上(异常)。
            row += '<div class="gbar"'+(hasDates?'':' title="该工单未设置计划交期,工序进度按比例示意(横向位置非按日历)"')+' style="position:absolute;top:50%;transform:translateY(-50%);height:28px;left:'+barL+'px;width:'+barW+'px;background:#ffffff;border:1px '+(hasDates?'solid #cbd5e1':'dashed #f59e0b')+';border-radius:4px;">'+inner+'</div>';
          }}
          // 预计产出虚线框:今日 → 预计产出日(ETA,按实测CT正向预测)。
          // 框尾即 ETA;与交付日紫线对比即可直观看出 buffer / 滞后。
          // 框颜色:ETA ≤ 交付日 → 蓝(有富余);晚于交付日 → 红(滞后)。
          if(o.eta && today) {{
            var etaD=toBizStart(parseD(o.eta));
            // 挂起并推后时:虚线框/竖标用推后后的 ETA(与上方推后的工序条一致)
            if(holdOn && holdEtaOff!=null) etaD=addDays(d0, Math.round(holdEtaOff));
            if(etaD) {{
              var bs=dayDiff(d0, todayProd), be=dayDiff(d0, etaD);
              var lo=Math.max(Math.min(bs,be),0), hi=Math.min(Math.max(bs,be),span-1);
              if(hi>=lo) {{
                var within=(!e)||(etaD.getTime()<=e.getTime());
                // 蓝框(按期/有富余)仅管理员可见;红框(滞后)所有人可见
                var showBox = within ? IS_AUTHED : true;
                if(showBox) {{
                  var bc=within?'#0ea5e9':'#dc2626';
                  var bgc=within?'rgba(14,165,233,.06)':'rgba(220,38,38,.06)';
                  row += '<div title="预计产出日 '+o.eta+'(按实测CT'+(o.target_ratio!=null?'·到完成工序':'')+')" '
                       + 'style="position:absolute;top:50%;transform:translateY(-50%);height:32px;left:'+(lo*dayW)+'px;width:'+((hi-lo+1)*dayW)+'px;'
                       + 'border:1px dashed '+bc+';border-radius:5px;background:'+bgc+';pointer-events:none;z-index:2;"></div>';
                  // ETA 当日竖标(框尾),便于精确读数
                  var ex=dayDiff(d0, etaD);
                  if(ex>=0 && ex<span)
                    row += '<div style="position:absolute;top:0;bottom:0;left:'+((ex+1)*dayW)+'px;width:2px;background:'+bc+';z-index:3;"></div>';
                }}
              }}
            }}
          }}
          // 兜底:无 CT 数据(holdOn=false,如匿名无ETA)时,仍在原位叠加「今日→恢复日」斜纹带做标记。
          // 有 CT 数据时已由上方 bar 内的挂起间隙+推后渲染,这里不再重复画。
          if(!holdOn && o.resume_date && today) {{
            var rsd=toBizStart(parseD(o.resume_date));
            if(rsd && rsd.getTime()>today.getTime()) {{
              var ws=dayDiff(d0,todayProd), we=dayDiff(d0,rsd);
              var wlo=Math.max(Math.min(ws,we),0), whi=Math.min(Math.max(ws,we),span-1);
              if(whi>=wlo) {{
                var rsn=(o.wait_reason||'').replace(/"/g,'&quot;');
                row += '<div title="⏸挂起 · 等待恢复作业 → '+fmtSlash(o.resume_date)+(rsn?(' · 原因：'+rsn):'')+'" '
                     + 'style="position:absolute;top:50%;transform:translateY(-50%);height:24px;left:'+(wlo*dayW)+'px;width:'+((whi-wlo+1)*dayW)+'px;'
                     + 'background:repeating-linear-gradient(45deg,#fca5a5,#fca5a5 5px,#fee2e2 5px,#fee2e2 10px);'
                     + 'border:1px solid #f87171;border-radius:4px;z-index:4;opacity:.92;"></div>';
                // 挂起角标⏸ + 恢复作业日竖标(绿)
                row += '<div style="position:absolute;top:50%;transform:translate(2px,-50%);left:'+(wlo*dayW)+'px;font-size:11px;line-height:1;z-index:5;color:#b91c1c;pointer-events:none;">⏸</div>';
                var rx=dayDiff(d0,rsd);
                if(rx>=0 && rx<span)
                  row += '<div title="恢复作业日 '+fmtSlash(o.resume_date)+'" style="position:absolute;top:0;bottom:0;left:'+((rx+1)*dayW)+'px;width:2px;background:#16a34a;z-index:5;"></div>';
              }}
            }}
          }}
          // 今日竖线已移除,改用日期列背景加深标识今日(见下方今日整列高亮)
          // 交付日竖标:默认紫线;若「交期」早于按标准CT推算的完工日(含挂起顺延)→ 转黄 + ⚠延误X天提示。
          if(showDue && e) {{
            var eoff=dayDiff(d0,e);
            // 标准CT完工日(相对基准日的天偏移):挂起时取顺延后完工,否则 计划开始 + 标准CT。
            var ctEndOff = holdOn ? holdEtaOff : (planOffH + ctDays);
            var lateDays = (ctEndOff!=null && (ctDays>0||holdOn)) ? Math.round(ctEndOff - eoff) : 0;
            var lateRisk = lateDays > 0;   // 交期早于CT完工日 → 有延误风险
            if(eoff>=0 && eoff<span) {{
              var dueColor = lateRisk ? '#eab308' : '#a855f7';
              var dueTip = lateRisk ? ('交期 '+fmtSlash(o.plan_end)+' 早于标准CT完工日,预计延误 '+lateDays+' 天')
                                    : ('计划交付日 '+fmtSlash(o.plan_end));
              row += '<div title="'+dueTip+'" style="position:absolute;top:0;bottom:0;left:'+((eoff+1)*dayW)+'px;width:2px;background:'+dueColor+';z-index:4;"></div>';
              if(lateRisk)
                row += '<div title="'+dueTip+'" style="position:absolute;top:-3px;left:'+((eoff+1)*dayW)+'px;transform:translateX(-50%);font-size:10px;line-height:1.25;color:#854d0e;background:#fef9c3;border:1px solid #facc15;border-radius:3px;padding:0 3px;white-space:nowrap;z-index:6;pointer-events:none;">⚠延误'+lateDays+'天</div>';
            }}
          }}
          row += '</div></div>';
          bodyH += row;
        }});

        document.getElementById('gantt_wrap').innerHTML =
          '<div style="min-width:'+(LABEL_W+gridW)+'px;">'+head+bodyH+'</div>';
      }}

      function updateProdBtn() {{
        var sel=selectedProds();
        var btn=document.getElementById('g_prodbtn');
        if(!btn) return;
        if(sel.length===0) btn.textContent='全部产品型号 ▾';
        else if(sel.length===1) btn.textContent=sel[0]+' ▾';
        else btn.textContent='已选 '+sel.length+' 个型号 ▾';
      }}
      function updateOwnerBtn() {{
        var sel=selectedOwners();
        var btn=document.getElementById('g_ownerbtn');
        if(!btn) return;
        if(sel.length===0) btn.textContent='全部负责人 ▾';
        else if(sel.length===1) btn.textContent=sel[0]+' ▾';
        else btn.textContent='已选 '+sel.length+' 位负责人 ▾';
      }}
      // 「现在」线每分钟自动右移:不重渲染整图,只挪动所有 .gnow 竖线与 .gnowlbl 标签(轻量、不打断滚动/悬浮)。
      // 以服务器北京时刻 NOW_MIN0 + 页面打开后流逝分钟为准,跨午夜自动进位到次日。
      setInterval(function(){{
        if(_nowD0==null) return;
        var nd=nowDate();
        var nx=dayDiff(_nowD0,nd)*_nowDayW;
        var vis=(nx>=0 && nx<=_nowGridW), hm=_hm(nd.getHours()*60+nd.getMinutes());
        document.querySelectorAll('.gnow').forEach(function(el){{
          el.style.left=nx+'px'; el.style.display=vis?'block':'none';
        }});
        document.querySelectorAll('.gnowlbl').forEach(function(el){{
          el.style.left=nx+'px'; el.style.display=vis?'block':'none'; el.textContent='现在 '+hm;
        }});
      }}, 60000);
      return {{
        render: render,
        shift: function(n){{ if(!viewStart) viewStart=dataRange(filtered())[0]; viewStart=addDays(viewStart,n); render(); }},
        toggleProd: function(e){{ e.stopPropagation(); document.getElementById('g_proddrop').classList.toggle('open'); }},
        onProd: function(){{ updateProdBtn(); render(); }},
        prodAll: function(v){{ document.querySelectorAll('.g-prodcb').forEach(function(cb){{ cb.checked=v; }}); updateProdBtn(); render(); }},
        toggleOwner: function(e){{ e.stopPropagation(); document.getElementById('g_ownerdrop').classList.toggle('open'); }},
        onOwner: function(){{ updateOwnerBtn(); render(); }},
        ownerAll: function(v){{ document.querySelectorAll('.g-ownercb').forEach(function(cb){{ cb.checked=v; }}); updateOwnerBtn(); render(); }},
      }};
    }})();
    gantt.render();
    // 工序小格:鼠标经过时在格子上方弹出自定义浮层(工序名 + CT 时间),跟随鼠标移动。
    // 用事件委托挂在 gantt_wrap 上(其 innerHTML 每次重绘,但容器本身不变),hover 的高亮由 CSS .gseg:hover 负责。
    (function(){{
      var darkTip=document.getElementById('gseg_tip');   // 工序块:深色浮层
      var pinkTip=document.getElementById('hold_tip');   // 挂起斜纹带:粉色浮层
      var wrap=document.getElementById('gantt_wrap');
      if(!wrap) return;
      // 浮层移到 body 下:.main/.panel 带 transform 动画(fill:both)会成为 fixed 定位容器,
      // 导致 tip 的 clientX/clientY 相对面板而非视口 → 与鼠标错位。挂到 body 即恒相对视口。
      if(darkTip) document.body.appendChild(darkTip);
      if(pinkTip) document.body.appendChild(pinkTip);
      var curTip=null;
      function tipFor(el){{ return (el.classList && el.classList.contains('ghold')) ? pinkTip : darkTip; }}
      function place(e){{ if(curTip){{ curTip.style.left=e.clientX+'px'; curTip.style.top=(e.clientY-12)+'px'; }} }}
      wrap.addEventListener('mouseover', function(e){{
        var t=e.target.closest && e.target.closest('[data-tip]'); if(!t) return;
        var tx=t.getAttribute('data-tip')||''; if(!tx) return;
        curTip=tipFor(t); if(!curTip) return;
        // 切换浮层类型时先隐藏另一个,避免两个浮层同时出现
        if(darkTip && darkTip!==curTip){{ darkTip.style.opacity='0'; darkTip.style.display='none'; }}
        if(pinkTip && pinkTip!==curTip){{ pinkTip.style.opacity='0'; pinkTip.style.display='none'; }}
        curTip.textContent=tx; curTip.style.display='block'; place(e);
        requestAnimationFrame(function(){{ curTip.style.opacity='1'; }});
      }});
      wrap.addEventListener('mousemove', function(e){{
        if(!curTip || curTip.style.display==='none') return;
        if(e.target.closest && e.target.closest('[data-tip]')) place(e);
      }});
      wrap.addEventListener('mouseout', function(e){{
        var t=e.target.closest && e.target.closest('[data-tip]'); if(!t) return;
        var to=e.relatedTarget;
        if(to && to.closest && to.closest('[data-tip]')) return;   // 相邻元素间移动不闪烁
        if(curTip){{ curTip.style.opacity='0'; curTip.style.display='none'; curTip=null; }}
      }});
    }})();
    // 点击下拉外部关闭产品多选框
    document.addEventListener('click', function(e){{
      var drop=document.getElementById('g_proddrop'), btn=document.getElementById('g_prodbtn');
      if(drop && drop.classList.contains('open') && !drop.contains(e.target) && e.target!==btn) {{
        drop.classList.remove('open');
      }}
      var odrop=document.getElementById('g_ownerdrop'), obtn=document.getElementById('g_ownerbtn');
      if(odrop && odrop.classList.contains('open') && !odrop.contains(e.target) && e.target!==obtn) {{
        odrop.classList.remove('open');
      }}
    }});
    // 首次布局完成后再渲染一次(确保拿到容器实际宽度);窗口缩放时自适应重绘
    window.addEventListener('load', function(){{ gantt.render(); }});
    (function(){{
      var _t;
      window.addEventListener('resize', function(){{
        clearTimeout(_t); _t = setTimeout(function(){{ gantt.render(); }}, 150);
      }});
    }})();
    </script>
    <div id="segMask" onclick="hideSeg()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49;"></div>
    <div id="segBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:max-content;max-width:84vw;min-width:min(480px,90vw);max-height:85vh;background:#fff;border-radius:12px;
         z-index:50;box-shadow:0 20px 60px rgba(0,0,0,.3);overflow:hidden;flex-direction:column;">
      <div style="position:relative;padding:18px 24px 8px;flex:0 0 auto;">
        <h3 id="segTitle" style="margin:0;text-align:center;">工序 LOT 明细</h3>
        <div id="segHint" style="text-align:center;font-size:12px;color:#94a3b8;margin-top:6px;"></div>
        <button onclick="hideSeg()" style="position:absolute;top:16px;right:20px;border:none;background:none;cursor:pointer;font-size:14px;color:#64748b;">关闭</button>
      </div>
      <div id="segBody" style="font-size:13px;color:#64748b;text-align:center;overflow:auto;padding:0 24px 20px;flex:1 1 auto;">加载中…</div>
    </div>
    <div id="holdMask" onclick="closeHold()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:51;"></div>
    <div id="holdBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:380px;max-width:94vw;background:#fff;border-radius:12px;z-index:52;box-shadow:0 20px 60px rgba(0,0,0,.3);flex-direction:column;overflow:hidden;">
      <div style="padding:16px 20px;border-bottom:1px solid #f1f5f9;">
        <h3 style="margin:0;font-size:15px;color:#b91c1c;">⏸ 工序挂起 <span id="holdWoNo" style="font-size:12px;color:#94a3b8;font-weight:normal;"></span></h3>
      </div>
      <div style="padding:14px 20px;font-size:13px;color:#334155;max-height:60vh;overflow:auto;">
        <div id="holdList" style="margin-bottom:14px;"></div>
        <div style="border-top:1px dashed #e2e8f0;padding-top:12px;">
          <div id="holdFormTitle" style="font-weight:600;color:#b45309;margin-bottom:8px;">➕ 新增一条挂起(可对多道工序、多次挂起)</div>
          <div style="margin-bottom:10px;">
            <div style="margin-bottom:5px;color:#64748b;">做不了的工序</div>
            <select id="holdSpec" style="width:100%;box-sizing:border-box;padding:7px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;background:#fff;"></select>
          </div>
          <div style="margin-bottom:10px;">
            <div style="margin-bottom:5px;color:#64748b;">开始挂起日期</div>
            <input id="holdStart" placeholder="默认今天(可改,如 2026/6/21 14:30)" style="width:100%;box-sizing:border-box;padding:7px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;">
          </div>
          <div style="margin-bottom:10px;">
            <div style="margin-bottom:5px;color:#64748b;">恢复作业日</div>
            <input id="holdDate" placeholder="如 2026/6/21 14:30 或 2026-06-21(精确到分钟)" style="width:100%;box-sizing:border-box;padding:7px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;">
          </div>
          <div>
            <div style="margin-bottom:5px;color:#64748b;">原因(缺料/设备占用/其他)</div>
            <input id="holdReason" placeholder="如:EMC未到" style="width:100%;box-sizing:border-box;padding:7px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;">
          </div>
        </div>
      </div>
      <div style="padding:12px 20px;border-top:1px solid #f1f5f9;display:flex;align-items:center;gap:8px;">
        <span style="flex:1;"></span>
        <button onclick="closeHold()" style="border:1px solid #cbd5e1;background:#fff;color:#64748b;border-radius:6px;font-size:13px;padding:7px 14px;cursor:pointer;">关闭</button>
        <button id="holdSubmitBtn" onclick="submitHold()" style="border:none;background:#dc2626;color:#fff;border-radius:6px;font-size:13px;padding:7px 16px;cursor:pointer;">新增挂起</button>
      </div>
    </div>
    <script>
    var _segLots=[], _segWo='', _segSpec='', _segDirty=false;
    var _segSortKey='', _segSortAsc=true, _segHintBase='';
    var _segCols=[
      {{k:'lot_id',  t:'Lot号'}},
      {{k:'status',  t:'状态'}},
      {{k:'eqpld',   t:'EQP ID'}},
      {{k:'qty',     t:'数量', num:true}},
      {{k:'hold_reason',t:'HOLD原因'}},
      {{k:'txn_comment',t:'MES备注'}},
      {{k:'run_time',t:'Run Time', num:true}},
      {{k:'wait_time',t:'Wait Time',num:true}},
      {{k:'hold_time',t:'Hold Time',num:true}}
    ];
    function escSeg(s){{ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }}
    // MES 操作备注(TXNCOMMENTS)单元格:有则原文,无则灰显占位。
    function _cmtCell(v){{ var t=(v||'').trim(); return t?escSeg(t):'<span style="color:#cbd5e1;">—</span>'; }}
    function _segNumv(x){{ var v=parseFloat(String(x).replace('h','')); return isNaN(v)?-1:v; }}
    // 整列复制(带降级)+ 在 segHint 处给 1.5s 反馈
    function _segFallbackCopy(text){{
      var ta=document.createElement('textarea'); ta.value=text;
      ta.style.position='fixed'; ta.style.top='-1000px'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      try{{ document.execCommand('copy'); }}catch(e){{}}
      document.body.removeChild(ta);
    }}
    function _segToClip(text, okmsg){{
      var hint=document.getElementById('segHint');
      function done(){{ var old=_segHintBase; hint.textContent=okmsg;
        setTimeout(function(){{ hint.textContent=old; }},1500); }}
      if(navigator.clipboard && navigator.clipboard.writeText){{
        navigator.clipboard.writeText(text).then(done, function(){{ _segFallbackCopy(text); done(); }});
      }} else {{ _segFallbackCopy(text); done(); }}
    }}
    function copySegCol(ev, key){{
      if(ev) ev.stopPropagation();
      var col=_segCols.find(function(c){{return c.k===key;}});
      var vals=_segLots.map(function(l){{ return String(l[key]==null?'':l[key]); }});
      _segToClip(vals.join('\\n'), '已复制「'+(col?col.t:key)+'」共 '+vals.length+' 行');
    }}
    function sortSeg(key){{
      if(_segSortKey===key){{ _segSortAsc=!_segSortAsc; }}
      else {{ _segSortKey=key; _segSortAsc=true; }}
      var col=_segCols.find(function(c){{return c.k===key;}});
      _segLots.sort(function(a,b){{
        var x=a[key], y=b[key];
        if(col&&col.num){{ x=_segNumv(x); y=_segNumv(y); return _segSortAsc?x-y:y-x; }}
        x=String(x||''); y=String(y||'');
        return _segSortAsc? x.localeCompare(y) : y.localeCompare(x);
      }});
      renderSegLots();
    }}
    // 全局 fmtSlash(供 holdWO 弹窗用;gantt IIFE 内另有同名私有版,作用域隔离不冲突)
    function fmtSlash(s){{ if(!s) return s; var parts=String(s).split(' '); var d=parts[0].replace(/-/g,'/'); return parts[1] ? (d+' '+parts[1]) : d; }}
    // 恢复作业日输入兼容:斜杠/点/横杠分隔均可,月日可不补零,不填年默认今年,可选时分。
    // 统一规整为后端格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM;无法识别返回 null。
    //   2026/6/19 · 2026.6.19 · 2026-06-19 · 6/19(今年) · 2026/06/19 14:30 → 2026-06-19 14:30
    function normDate(s){{
      s=(s||'').trim();
      if(!s) return '';
      // 分离日期和时间部分:先按空格拆,时间部分最后一段含冒号即为时间
      var parts=s.split(/\\s+/);
      var datePart='', timePart='';
      for(var i=0;i<parts.length;i++){{
        if(parts[i].indexOf(':')>=0) {{ timePart=parts[i]; }}
        else {{ datePart+=(datePart?' ':'')+parts[i]; }}
      }}
      datePart=datePart.replace(/[\\/.年月]/g,'-').replace(/日/g,'');
      var p=datePart.split('-').filter(function(x){{ return x!==''; }});
      if(p.length===2) p.unshift(String(new Date().getFullYear()));
      if(p.length!==3) return null;
      var y=+p[0], m=+p[1], d=+p[2];
      if(!y||!m||!d||m<1||m>12||d<1||d>31) return null;
      var out=y+'-'+('0'+m).slice(-2)+'-'+('0'+d).slice(-2);
      // 解析时间:HH:MM 或 HH:MM:SS
      if(timePart){{
        var tp=timePart.split(':');
        var hh=+tp[0], mm=+tp[1]||0;
        if(!isNaN(hh) && !isNaN(mm) && hh>=0 && hh<24 && mm>=0 && mm<60){{
          out+=' '+('0'+hh).slice(-2)+':'+('0'+mm).slice(-2);
        }}
      }}
      return out;
    }}
    // 管理员:挂起工单(等料/设备等可协调异常)。填恢复作业日+原因 → 后端据此顺延 ETA/风险。
    // 留空恢复日 = 解除挂起。仅工单级(整单顺延)。
    // ===== 工序挂起弹窗:选做不了的工序 + 恢复日 + 原因;已挂起可解除 =====
    var _holdWo='', _holdObj=null, _holdEditId=null;
    // 今天(YYYY/M/D);normDate 可识别,用作「开始挂起日期」默认值。
    function _todayInput(){{ var d=new Date(); return d.getFullYear()+'/'+(d.getMonth()+1)+'/'+d.getDate()+' '+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2); }}
    // 表单复位为「新增」模式(清空 editId、还原标题与按钮、开始挂起日期默认今天)。
    function _resetHoldForm(){{
      _holdEditId=null;
      var ft=document.getElementById('holdFormTitle'); if(ft) ft.innerHTML='➕ 新增一条挂起(可对多道工序、多次挂起)';
      var sb=document.getElementById('holdSubmitBtn'); if(sb) sb.textContent='新增挂起';
      document.getElementById('holdStart').value=_todayInput();
      document.getElementById('holdDate').value='';
      document.getElementById('holdReason').value='';
    }}
    // spec → 中文工序名(用该单 ct_segs 反查)
    function _specName(o, spec){{
      var segs=(o&&o.ct_segs)||[];
      for(var i=0;i<segs.length;i++){{ if(segs[i].spec===spec) return segs[i].name||segs[i].spec; }}
      return spec||'(未指定工序)';
    }}
    // 渲染当前工单的多条 active 挂起,每条可单独解除
    function renderHoldList(o){{
      var hs=(o&&o.holds)||[];
      var box=document.getElementById('holdList');
      if(hs.length===0){{ box.innerHTML='<div style="color:#94a3b8;">当前没有挂起,可在下方新增。</div>'; return; }}
      var html='<div style="font-weight:600;color:#b91c1c;margin-bottom:8px;">当前挂起（'+hs.length+' 处)</div>';
      hs.forEach(function(h){{
        var sd=h.start_date?('挂起 '+fmtSlash(h.start_date)+' · '):'';
        html+='<div style="display:flex;align-items:center;gap:8px;padding:7px 9px;margin-bottom:6px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;">'
            +'<div style="flex:1;min-width:0;"><div style="font-weight:600;color:#b91c1c;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+escSeg(_specName(o,h.hold_spec))+'</div>'
            +'<div style="color:#64748b;font-size:12px;">'+sd+'恢复 '+fmtSlash(h.resume_date)+(h.reason?(' · '+escSeg(h.reason)):'')+'</div></div>'
            +'<button onclick="editHold('+h.id+')" style="border:1px solid #93c5fd;background:#eff6ff;color:#1d4ed8;border-radius:5px;font-size:12px;padding:4px 9px;cursor:pointer;white-space:nowrap;">编辑</button>'
            +'<button onclick="resolveHold('+h.id+')" style="border:1px solid #cbd5e1;background:#fff;color:#64748b;border-radius:5px;font-size:12px;padding:4px 9px;cursor:pointer;white-space:nowrap;">解除</button>'
            +'</div>';
      }});
      box.innerHTML=html;
    }}
    function holdWO(ev, wo){{
      if(ev) ev.stopPropagation();
      var o=null;
      for(var i=0;i<GANTT_DATA.length;i++){{ if(GANTT_DATA[i].wo_no===wo){{ o=GANTT_DATA[i]; break; }} }}
      if(!o) return;
      _holdWo=wo; _holdObj=o;
      document.getElementById('holdWoNo').textContent=wo+(o.product?(' · '+o.product):'');
      renderHoldList(o);
      // 新增表单的工序下拉(显示中文名,value=spec)
      var segs=o.ct_segs||[];
      var opts='';
      if(segs.length===0) opts='<option value="">(无工序数据,无法按工序挂起)</option>';
      segs.forEach(function(sg){{
        var nm=(sg.name||sg.spec||''), sp=(sg.spec||'');
        opts+='<option value="'+escSeg(sp)+'">'+escSeg(nm)+'</option>';
      }});
      document.getElementById('holdSpec').innerHTML=opts;
      _resetHoldForm();
      document.getElementById('holdMask').style.display='block';
      document.getElementById('holdBox').style.display='flex';
    }}
    // 编辑某条已有挂起:把其工序/开始日/恢复日/原因回填到下方表单,切换为「保存修改」模式。
    function editHold(id){{
      var hs=(_holdObj&&_holdObj.holds)||[], h=null;
      for(var i=0;i<hs.length;i++){{ if(hs[i].id===id){{ h=hs[i]; break; }} }}
      if(!h) return;
      _holdEditId=id;
      var sel=document.getElementById('holdSpec'); if(sel) sel.value=h.hold_spec||'';
      document.getElementById('holdStart').value=h.start_date?fmtSlash(h.start_date):_todayInput();
      document.getElementById('holdDate').value=h.resume_date?fmtSlash(h.resume_date):'';
      document.getElementById('holdReason').value=h.reason||'';
      var ft=document.getElementById('holdFormTitle'); if(ft) ft.innerHTML='✏️ 编辑挂起';
      var sb=document.getElementById('holdSubmitBtn'); if(sb) sb.textContent='保存修改';
      document.getElementById('holdStart').focus();
    }}
    function closeHold(){{ document.getElementById('holdMask').style.display='none'; document.getElementById('holdBox').style.display='none'; }}
    function submitHold(){{
      var spec=document.getElementById('holdSpec').value;
      if(!spec){{ wipToast('请选择做不了的工序','warn'); return; }}
      var sd=normDate(document.getElementById('holdStart').value);
      if(sd===null){{ wipToast('开始挂起日期格式无法识别,请输入如 2026/6/21 14:30 或 2026-06-21','warn'); return; }}
      var rd=normDate(document.getElementById('holdDate').value);
      if(!rd){{ wipToast('恢复作业日格式无法识别,请输入如 2026/6/21 14:30 或 2026-06-21','warn'); return; }}
      var rsn=(document.getElementById('holdReason').value||'').trim();
      var url, body='resume='+encodeURIComponent(rd)+'&start='+encodeURIComponent(sd||'')
             +'&reason='+encodeURIComponent(rsn||'')+'&hold_spec='+encodeURIComponent(spec||'');
      if(_holdEditId){{ url='/edit_hold'; body+='&id='+encodeURIComponent(_holdEditId); }}
      else {{ url='/set_wait'; body+='&wo='+encodeURIComponent(_holdWo); }}
      fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body}})
        .then(function(r){{return r.json();}})
        .then(function(d){{ if(d.ok){{ location.reload(); }} else {{ wipToast(d.msg||'操作失败','err'); }} }})
        .catch(function(){{ wipToast('网络错误','err'); }});
    }}
    function resolveHold(id){{
      if(!confirm('确定解除这条挂起?(保留为历史记录)')) return;
      fetch('/resolve_hold',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:'id='+encodeURIComponent(id)}})
        .then(function(r){{return r.json();}})
        .then(function(d){{ if(d.ok){{ location.reload(); }} else {{ wipToast(d.msg||'操作失败','err'); }} }})
        .catch(function(){{ wipToast('网络错误','err'); }});
    }}
    // 点击甘特图深蓝(有产品)/浅粉(残留)色块 → 下钻该工单该工序的 LOT 明细
    function segLots(ev, wo, spec, name){{
      if(ev) ev.stopPropagation();
      _segWo=wo; _segSpec=spec; _segDirty=false;   // 本次弹层是否有改动(关闭时再刷新)
      _segSortKey=''; _segSortAsc=true;
      document.getElementById('segTitle').textContent = (name||spec)+' · LOT 明细';
      document.getElementById('segMask').style.display='block';
      document.getElementById('segBox').style.display='flex';
      var body=document.getElementById('segBody'); body.innerHTML='加载中…';
      document.getElementById('segHint').textContent='';
      fetch('/seg_lots?wo='+encodeURIComponent(wo)+'&spec='+encodeURIComponent(spec))
        .then(function(r){{return r.json();}})
        .then(function(list){{ _segLots=list||[]; renderSegLots(); }})
        .catch(function(){{ body.innerHTML='<div style="padding:14px;color:#dc2626;">加载失败</div>'; }});
    }}
    function renderSegLots(){{
      var body=document.getElementById('segBody'), hint=document.getElementById('segHint');
      if(!_segLots.length){{ hint.textContent=''; body.innerHTML='<div style="padding:14px;color:#94a3b8;">该工序暂无 LOT</div>'; return; }}
      var nIgn=_segLots.filter(function(l){{return l.ignored;}}).length;
      _segHintBase='共 '+_segLots.length+' 个 LOT'+(nIgn?(' · 已忽略 '+nIgn+' 个'):'')
        +' · 点表头排序 · 点表头 ⧉ 复制整列'
        +(IS_AUTHED?' · 点「忽略」剔除残留 LOT(不计入尾批)并填备注':' · 仅管理员可忽略')
        +(_segDirty?' · 已保存,关闭后刷新看板':'');
      hint.textContent=_segHintBase;
      var th='text-align:center;padding:6px 12px;background:#eef2f7;position:sticky;top:0;z-index:1;user-select:none;white-space:nowrap;';
      var h='<table style="border-collapse:collapse;text-align:center;font-size:13px;margin:0 auto;white-space:nowrap;"><thead><tr style="color:#475569;">';
      _segCols.forEach(function(c){{
        var arrow = _segSortKey===c.k ? (_segSortAsc?' ▲':' ▼') : ' ⇅';
        h+='<td style="'+th+'"><span style="cursor:pointer;" onclick="sortSeg(\\''+c.k+'\\')">'+c.t
          +'<span style="color:#94a3b8;font-size:11px;">'+arrow+'</span></span>'
          +'<span onclick="copySegCol(event,\\''+c.k+'\\')" title="复制整列" '
          +'style="margin-left:6px;cursor:pointer;color:#2563eb;font-size:12px;">⧉</span></td>';
      }});
      h+='<td style="'+th+'">忽略</td></tr></thead><tbody>';
      _segLots.forEach(function(l,i){{
        var rowbg=l.ignored?'background:#fdf2f8;color:#9d174d;':'';
        h+='<tr style="border-top:1px solid #f1f5f9;'+rowbg+'" '
          +'onmouseover="if(!'+(l.ignored?'true':'false')+')this.style.background=\\'#f8fafc\\'" '
          +'onmouseout="this.style.background=\\''+(l.ignored?'#fdf2f8':'')+'\\'">'
          +'<td style="padding:6px 15px;text-align:center;">'+escSeg(l.lot_id)+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+statusBadge(l.status||'')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escSeg(l.eqpld)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;font-weight:600;">'+(l.qty||0)+'</td>'
          +'<td style="padding:6px 12px;text-align:center;color:#475569;">'+(escSeg(l.hold_reason)||'<span style="color:#cbd5e1;">—</span>')+'</td>'
          +'<td style="padding:6px 12px;text-align:left;max-width:300px;white-space:normal;word-break:break-word;color:#334155;line-height:1.4;">'+_cmtCell(l.txn_comment)+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escSeg(l.run_time)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escSeg(l.wait_time)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escSeg(l.hold_time)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;white-space:nowrap;">'
            +(IS_AUTHED
                ? '<input type="checkbox" '+(l.ignored?'checked':'')+' onchange="segToggleIgnore('+i+',this.checked)" title="勾选=忽略(视同报废),取消=恢复" style="vertical-align:middle;cursor:pointer;width:16px;height:16px;">'
                  +(l.ignored
                      ? '<textarea class="ignnote" rows="1" oninput="_noteAutosize(this)" onchange="segSaveNote('+i+',this.value)" placeholder="忽略备注(可换行)" style="vertical-align:middle;margin-left:6px;width:16ch;min-height:34px;font-size:12px;line-height:1.4;padding:3px 6px;border:1px solid #cbd5e1;border-radius:5px;resize:none;overflow:hidden;font-family:inherit;">'+escSeg(l.ignore_note)+'</textarea>'
                      : '')
                : (l.ignored?('已忽略'+(l.ignore_note?' · '+escSeg(l.ignore_note):'')):'—'))
          +'</td></tr>';
      }});
      h+='</tbody></table>';
      if(IS_AUTHED) h+='<div style="font-size:12px;color:#94a3b8;margin-top:10px;">可连续操作多个批次;<b>关闭此窗口后</b>看板统一刷新并重算进度/风险/ETA。</div>';
      body.innerHTML=h;
      body.querySelectorAll('.ignnote').forEach(_noteAutosize);   // 初始化备注框自适应尺寸
    }}
    // 复选框:勾选=忽略(视同报废),取消=恢复;勾选后其后内联出现备注框可直接编辑
    function segToggleIgnore(i, checked){{
      var l=_segLots[i]; if(!l) return;
      _segSubmitIgnore(l, checked?1:0, checked?(l.ignore_note||''):'');
    }}
    function segSaveNote(i, val){{
      var l=_segLots[i]; if(!l || !l.ignored) return;
      _segSubmitIgnore(l, 1, (val||'').trim());
    }}
    function _segSubmitIgnore(l, ig, note){{
      var prevI=l.ignored, prevN=l.ignore_note||'';
      l.ignored=ig; l.ignore_note=ig?note:''; renderSegLots();
      // 用 urlencoded 提交(后端 _form_fields 按 urlencoded 解析,不支持 multipart)
      var body='lot_id='+encodeURIComponent(l.lot_id)
             +'&wo='+encodeURIComponent(_segWo)
             +'&spec='+encodeURIComponent(l.spec||_segSpec)
             +'&ignore='+(ig?'1':'0')
             +'&note='+encodeURIComponent(note||'');
      fetch('/ignore_lot', {{method:'POST',
            headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},
            body:body}})
        .then(function(r){{return r.json();}})
        .then(function(res){{
          // 已保存但不立即刷新:支持连续操作多个批次,关闭弹层时再统一刷新看板
          if(res && res.ok){{ _segDirty=true; renderSegLots(); }}
          else {{ wipToast((res&&res.msg)||'操作失败','err'); l.ignored=prevI; l.ignore_note=prevN; renderSegLots(); }}
        }})
        .catch(function(){{ wipToast('网络错误,未保存','err'); l.ignored=prevI; l.ignore_note=prevN; renderSegLots(); }});
    }}
    function hideSeg(){{
      document.getElementById('segMask').style.display='none';
      document.getElementById('segBox').style.display='none';
      if(_segDirty){{ _segDirty=false; location.reload(); }}   // 有改动才在关闭后刷新
    }}
    </script>
    """ + _detail_modal() + """
    """
    return layout("gantt", "生产进度看板", body)


# ---------- 统计分析页 ----------
def _wip_chart_js(lots_json, ct_json, urg_lots_json="[]", urg_ct_json="{}", urg_target_json="{}"):
    """「产品在制 WIP 分布」图的脚本(纯 JS,单括号),数据用占位符注入避免大段 {{ }} 转义。
    X=工序(CT顺序),堆叠柱=4状态WIP,叠加 CWIP 累计折线;点击柱复用统计页 ownerBox 弹窗看 lot 明细。
    含 NTO 型号 / 加急Lot 批次 两套数据,wip.toggleUrg() 切换(独立开关,与 NTO 区分)。"""
    js = r"""
    <script>
    var WIP_LOTS = __WIP_LOTS__;
    var WIP_CT = __WIP_CT__;
    var URG_LOTS = __URG_LOTS__;
    var URG_CT = __URG_CT__;
    var URG_TARGET = __URG_TARGET__;            // {批次名: 作业到工序} → 图上绿色目标线
    var WIP_MODE = 'nto';                       // 'nto'=NTO在制型号 | 'urgent'=加急Lot批次
    function fmtEta(t){ if(!t) return '—';
      var d=new Date(t*1000),mo=String(d.getMonth()+1).padStart(2,'0'),
          da=String(d.getDate()).padStart(2,'0'),hh=String(d.getHours()).padStart(2,'0'),
          mi=String(d.getMinutes()).padStart(2,'0');
      return mo+'-'+da+' '+hh+':'+mi; }
    function etaCell(l){
      if(l.status==='HOLD') return '<span style="color:#ef4444;">已暂停</span>';
      if(!l.eta_at) return '<span style="color:#cbd5e1;">—</span>';
      var t='<span style="color:#2563eb;font-weight:600;">'+fmtEta(l.eta_at)+'</span>';
      return t+(l.eta_basis==='同炉'?'<div style="font-size:10px;color:#94a3b8;" title="同炉多个 Lot 共享同一完成时刻(烘烤/批量站)">同炉共享</div>':'');
    }
    var wip = (function(){
      function isUrg(){ return WIP_MODE==='urgent'; }
      function curLots(){ return isUrg()? URG_LOTS : WIP_LOTS; }
      function curCT(){ return isUrg()? URG_CT : WIP_CT; }
      function elModel(){ return document.getElementById(isUrg()?'wip_urg':'wip_model'); }
      function curBase(){ var e=elModel(); return e? (e.value||'') : ''; }
      function curKwRaw(){ var e=document.getElementById('wip_search'); return e? (e.value||'').trim() : ''; }
      function curKw(){ return curKwRaw().toLowerCase(); }
      function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
      function filterLots(){
        var base=curBase(), kw=curKw();
        return curLots().filter(function(l){
          if(kw){
            return (String(l.wo_no||'').toLowerCase().endsWith(kw))
                || (String(l.product||'').toLowerCase().endsWith(kw))
                || (String(l.model||'').toLowerCase().endsWith(kw))
                || (String(l.base||'').toLowerCase().endsWith(kw))
                || (String(l.lot_id||'').toLowerCase().endsWith(kw));
          }
          return l.base===base;
        });
      }
      function buildSkel(lots){
        // 过滤结果若只含单一 base 且该型号有 CT → 用 CT 工序顺序;否则按 spec 出现顺序
        var bases={}; lots.forEach(function(l){ if(l.base) bases[l.base]=1; });
        var arr=Object.keys(bases), useBase=(arr.length===1)?arr[0]:'';
        var CT=curCT();
        var skel=[];
        if(useBase && CT[useBase] && CT[useBase].length){
          var inct={};
          CT[useBase].forEach(function(c){ skel.push({spec:c.spec,name:c.name||c.spec}); inct[c.spec]=1; });
          var extra={};
          lots.forEach(function(l){ if(l.spec && !inct[l.spec]) extra[l.spec]=l.desc||l.spec; });
          Object.keys(extra).forEach(function(sp){ skel.push({spec:sp,name:extra[sp]}); });
        } else {
          var seen={};
          lots.forEach(function(l){ if(l.spec && !seen[l.spec]){ seen[l.spec]=1; skel.push({spec:l.spec,name:l.desc||l.spec}); } });
        }
        return skel;
      }
      function aggregate(lots, skel){
        // *_q = 各状态【颗数(QTY)】之和(柱高/分段按颗数,见 render);run/wi/.. = 对应 Lot 批数(仅悬停展示)
        var m={}; skel.forEach(function(x){ m[x.spec]={run:0,wi:0,wo:0,hold:0,ign:0,move:0,qty:0,total:0,
                                                        run_q:0,wi_q:0,wo_q:0,hold_q:0,ign_q:0,barq:0}; });
        lots.forEach(function(l){
          var e=m[l.spec]; if(!e) return;
          var q=(+l.qty)||0;
          if(l.ignored){ e.ign++; e.ign_q+=q; e.total++; return; }   // 已忽略=报废:不计入颗数/MOVE/CWIP
          e.qty+=q;               // 非忽略颗数(供 CWIP 累计)
          if(l.moved) e.move+=q;  // 本班次流入该工序的颗数(供 MOVE)
          var s=l.status;
          if(s==='运行中'){ e.run++; e.run_q+=q; }
          else if(s==='等待进站'){ e.wi++; e.wi_q+=q; }
          else if(s==='等待出站'){ e.wo++; e.wo_q+=q; }
          else if(s==='HOLD'){ e.hold++; e.hold_q+=q; }
          else return;
          e.total++;
        });
        return m;
      }
      function render(){
        var box=document.getElementById('wip_chart'); if(!box) return;
        var lots=filterLots(), skel=buildSkel(lots), m=aggregate(lots, skel);
        var totLot=0; skel.forEach(function(x){ totLot+=(m[x.spec]?m[x.spec].total:0); });
        var sub=document.getElementById('wip_sub');
        if(sub) sub.textContent='· '+skel.length+' 站 · 在制 '+totLot+' 个lot'+(curKwRaw()?(' · 搜索「'+curKwRaw()+'」'):'');
        if(!skel.length || totLot===0){ var _u=isUrg(); box.innerHTML='<div class="empty"><div class="ico">'+(_u?'⚡':'📊')+'</div><div class="tt">'+(curKwRaw()?'没有匹配的在制 lot':(_u?'该加急批次暂无在制 Lot':'该型号暂无在制 WIP'))+'</div><div class="sb">'+(curKwRaw()?'换个工单号 / 型号试试':(_u?'换个加急批次,或等 MES 同步在制数据':'换个型号,或等 MES 同步在制数据'))+'</div></div>'; return; }
        // 柱高 = 该工序在制【颗数(QTY)】(含各状态,含已忽略灰段);maxBar=单序最大颗数 → 缩放基准。
        // 改前按 Lot 批数,堆料多但批少的工序看不出来;现按颗数,真实反映料堆。
        var maxBar=1; skel.forEach(function(x){ var e=m[x.spec]; var t=e.run_q+e.wi_q+e.wo_q+e.hold_q+e.ign_q; e.barq=t; if(t>maxBar) maxBar=t; });
        // CWIP:该工序及其之后所有在制【颗数(QTY)】之和(从右往左累加),左高右低递减。
        // 按 Lot 中的颗数计,且【不含被忽略(=报废)的 lot】。
        var cwip=[], run=0;
        for(var i=skel.length-1;i>=0;i--){ run+=m[skel[i].spec].qty; cwip[i]=run; }
        var maxC=cwip.length?cwip[0]:1; if(maxC<1) maxC=1;
        var H=280;
        var bars='';
        skel.forEach(function(x, idx){
          var e=m[x.spec], has=e.total>0, sp=String(x.spec).replace(/'/g,'');
          function seg(n,color,lbl){ if(n<=0) return ''; var hh=Math.max(2,n/maxBar*H);
            return '<div style="width:100%;height:'+hh+'px;background:'+color+';"></div>'; }
          // 分段高度按各状态【颗数】堆叠(不再按批数)
          var stack=seg(e.run_q,'#22c55e','运行中')+seg(e.wi_q,'#ffdd00','等待进站')+seg(e.wo_q,'#60a5fa','等待出站')+seg(e.hold_q,'#ef4444','HOLD')+seg(e.ign_q,'#cbd5e1','已忽略');
          // 悬停提示:色块颜色与柱段完全一致。data-tip 用双引号包裹,故内联样式一律用单引号(\'),
          // 严禁出现双引号(否则截断属性导致 onclick 失效、HTML 泄漏)。
          function _sw(c){ return '<i style=\'display:inline-block;width:11px;height:11px;border-radius:2px;background:'+c+';\'></i>'; }
          function _ln(c){ return '<i style=\'display:inline-block;width:15px;border-top:2px solid '+c+';position:relative;top:-3px;\'></i>'; }
          function _fmt(n){ n=n||0; try{ return n.toLocaleString('en-US'); }catch(_){ return ''+n; } }
          function _row(mk,v){ return '<div style=\'display:flex;align-items:center;gap:8px;padding:1px 0;\'>'+mk+'<span style=\'font-weight:600;\'>'+_fmt(v)+'</span></div>'; }
          // MOVE(本工序产出):= 本班次流入「下一道工序」的量,即本工序做下来交到下道的数量
          var mo = (idx+1<skel.length) ? (m[skel[idx+1].spec]?m[skel[idx+1].spec].move:0) : 0;
          var qwip_q = e.wi_q + e.wo_q;   // QWIP 颗数 = 等待进站 + 等待出站(参考图合并为一项)
          var _ti = '<div style=\'font-weight:700;margin-bottom:4px;border-bottom:1px solid #e2e8f0;padding-bottom:3px;\'>'+esc(x.name)+'</div>';
          // 提示数值改为【颗数】,与柱段(按颗数)一致;末行附该工序在制 Lot 批数,批数信息不丢失
          var tipHtml = has
            ? _ti+_row(_sw('#22c55e'),e.run_q)+_row(_sw('#ffdd00'),qwip_q)+_row(_sw('#ef4444'),e.hold_q)+(e.ign_q?_row(_sw('#cbd5e1'),e.ign_q):'')+_row(_ln('#2563eb'),mo)+_row(_ln('#0f172a'),cwip[idx])+'<div style=\'margin-top:3px;font-size:10px;color:#94a3b8;\'>共 '+e.total+' 个lot · 数值=颗数</div>'+'<div style=\'margin-top:2px;font-size:10px;color:#94a3b8;text-align:center;\'>点击看明细</div>'
            : _ti+'<div style=\'color:#94a3b8;padding:1px 0;\'>无在制 lot</div>'+_row(_ln('#2563eb'),mo)+_row(_ln('#0f172a'),cwip[idx]);
          bars += '<div class="wseg" data-tip="'+tipHtml+'" onclick="wip.drill(\''+sp+'\')" '
                + 'style="display:flex;flex-direction:column;align-items:center;flex:1 1 0;min-width:0;cursor:'+(has?'pointer':'default')+';">'
                + '<div class="wbar" style="display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:'+H+'px;width:80%;max-width:20px;border-bottom:1px solid #cbd5e1;overflow:visible;">'
                    + (has?('<div style="line-height:1.05;text-align:center;white-space:nowrap;margin-bottom:1px;">'
                            +'<div style="font-size:12px;color:#0f172a;font-weight:600;">'+_fmt(e.barq)+'</div>'   // 上:该工序在制颗数
                            +'<div style="font-size:10px;color:#64748b;">('+e.total+')</div>'                       // 下:(该工序在制Lot批数)
                          +'</div>'):'')
                    + '<div style="width:100%;display:flex;flex-direction:column;">'+stack+'</div>'
                + '</div>'
                + '<div style="font-size:'+(has?'13px':'10px')+';writing-mode:vertical-rl;text-orientation:mixed;height:'+(has?'118px':'96px')+';overflow:hidden;margin-top:3px;white-space:nowrap;font-weight:'+(has?'700':'500')+';color:'+(has?'#0f172a':'#334155')+';">'+esc(x.name)+'</div>'
                + '</div>';
        });
        var N=skel.length;
        var pts=cwip.map(function(c,i){ var y=H-(c/maxC*H); return (i+0.5)+','+y.toFixed(1); }).join(' ');
        // MOVE(本工序产出·颗数)折线:取「下一道工序的本班次流入颗数」归到本工序(=本工序做下来多少);
        // 末道工序无下游(产出已出库离开MES)→ 记 0。与 CWIP 同为颗数,按右轴 maxC 缩放(MOVE≤累计,必落轴内)。
        var mpts=skel.map(function(x,i){ var mv=(i+1<skel.length)?(m[skel[i+1].spec]?m[skel[i+1].spec].move:0):0; var y=H-(maxC?mv/maxC*H:H); return (i+0.5)+','+y.toFixed(1); }).join(' ');
        var svg='<svg viewBox="0 0 '+N+' '+H+'" preserveAspectRatio="none" style="position:absolute;left:0;top:0px;width:100%;height:'+H+'px;pointer-events:none;overflow:visible;">'
              + '<polyline points="'+mpts+'" fill="none" stroke="#2563eb" stroke-width="1.6" vector-effect="non-scaling-stroke" opacity="0.95"/>'
              + '<polyline points="'+pts+'" fill="none" stroke="#0f172a" stroke-width="1.4" vector-effect="non-scaling-stroke" opacity="0.7"/></svg>';
        // 左Y轴=在制lot数、右Y轴=CWIP累计颗数(双轴) + 灰色网格(水平加密副线 + 每工序垂直线),还原参考图
        var ticks=5, yaxis='', raxis='', grid='';
        // 细网格副线:水平每 1/10、垂直对齐每个工序(灰色虚线,同参考图)
        var hsub=10;
        for(var hi=0;hi<=hsub;hi++){ var hy=H*hi/hsub; grid += '<div style="position:absolute;left:0;right:0;top:'+hy+'px;border-top:1px dashed rgba(120,120,120,.30);"></div>'; }
        for(var vi=0;vi<=N;vi++){ var vx=vi/N*100; grid += '<div style="position:absolute;top:0;left:'+vx+'%;height:'+H+'px;border-left:1px dashed rgba(120,120,120,.30);"></div>'; }
        // 加急Lot 模式:在「作业到工序」处画一条绿色目标线(到该工序即视为完成)
        if(isUrg()){
          var _tsp=(URG_TARGET&&URG_TARGET[curBase()])||'', _ti=-1;
          if(_tsp){ for(var _i=0;_i<skel.length;_i++){ if(skel[_i].spec===_tsp){ _ti=_i; break; } } }
          if(_ti>=0){ var _gx=(_ti+0.5)/N*100;
            grid += '<div title="作业到此工序即完成" style="position:absolute;top:0;left:'+_gx+'%;height:'+H+'px;border-left:2px solid #16a34a;z-index:3;"></div>'
                  + '<div style="position:absolute;top:-1px;left:'+_gx+'%;transform:translateX(-50%);background:#16a34a;color:#fff;font-size:9px;line-height:1.3;padding:0 4px;border-radius:3px;white-space:nowrap;z-index:3;">作业到</div>';
          }
        }
        for(var k=0;k<ticks;k++){
          var frac=k/(ticks-1), gy=H*frac;
          var valL=Math.round(maxBar*(1-frac));   // 左轴:在制颗数(柱高)
          var valR=Math.round(maxC*(1-frac));      // 右轴:CWIP 累计颗数(黑线)
          yaxis += '<div style="position:absolute;right:4px;top:'+(gy-6)+'px;font-size:9px;color:#334155;line-height:1;">'+valL+'</div>';
          raxis += '<div style="position:absolute;left:4px;top:'+(gy-6)+'px;font-size:9px;color:#0f172a;line-height:1;">'+valR+'</div>';
          grid  += '<div style="position:absolute;left:0;right:0;top:'+gy+'px;border-top:1px '+(k===ticks-1?'solid rgba(100,100,100,.55)':'dashed rgba(120,120,120,.45)')+';"></div>';
        }
        box.innerHTML =
          '<div style="display:flex;align-items:stretch;">'
          + '<div style="position:relative;flex:0 0 32px;width:32px;">'+yaxis+'</div>'
          + '<div style="position:relative;flex:1 1 auto;border-left:1px solid #64748b;border-right:1px solid #64748b;background:#f0ffff;">'
            + '<div style="position:absolute;left:0;right:0;top:0;height:'+H+'px;background:linear-gradient(180deg,#fdffff 0%,#b0eeee 100%);"></div>'
            + grid
            + '<div style="position:relative;z-index:1;display:flex;align-items:flex-start;gap:1px;">'+bars+'</div>'
            + svg
          + '</div>'
          + '<div style="position:relative;flex:0 0 42px;width:42px;">'+raxis+'</div>'
          + '</div>'
          + '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;font-size:10px;color:#94a3b8;margin-top:3px;">'
          +   '<span>左轴=在制颗数(柱高) · 右轴=CWIP 累计颗数</span>'
          +   '<span>柱高=在制颗数(QTY) · 柱顶 颗数/(批数) · RWIP绿/QWIP黄/HWIP红 · <span style="color:#2563eb;">蓝线=MOVE(产出·颗,做下来交到下道的量)</span> · 黑线=CWIP累计 · CWIP峰值 '+maxC+' 颗 · 单序峰值 '+maxBar+' 颗 · 点柱看明细</span>'
          + '</div>';
      }
      function onModel(){ var s=document.getElementById('wip_search'); if(s) s.value=''; refreshOwnerLoad(); render(); }
      // 右下角「负责人工作量统计」跟随搜索框联动:防抖 200ms 请求 /owner_load 重算条形图
      var _ownerDeb=null;
      function refreshOwnerLoad(){
        var kw=(function(){ var e=document.getElementById('wip_search'); return e?(e.value||'').trim():''; })();
        if(_ownerDeb) clearTimeout(_ownerDeb);
        _ownerDeb=setTimeout(function(){
          fetch('/owner_load?q='+encodeURIComponent(kw)).then(function(r){return r.json();}).then(function(d){
            var p=document.getElementById('ownerPanel');
            if(p && d.html) p.innerHTML=d.html;
          }).catch(function(){});
        },200);
      }
      function onSearch(){
        // 搜索时把左侧型号下拉自动切到「匹配工单 lot 最多的型号」,使下拉与搜索结果一致
        var kw=curKw();
        if(kw){
          var cnt={};
          curLots().forEach(function(l){
            var hit=(String(l.wo_no||'').toLowerCase().endsWith(kw))
                  ||(String(l.product||'').toLowerCase().endsWith(kw))
                  ||(String(l.model||'').toLowerCase().endsWith(kw))
                  ||(String(l.base||'').toLowerCase().endsWith(kw))
                  ||(String(l.lot_id||'').toLowerCase().endsWith(kw));
            if(hit && l.base) cnt[l.base]=(cnt[l.base]||0)+1;
          });
          var keys=Object.keys(cnt);
          if(keys.length){
            keys.sort(function(a,b){ return cnt[b]-cnt[a]; });
            var sel=elModel(); if(sel && sel.value!==keys[0]) sel.value=keys[0];
          }
        }
        refreshOwnerLoad();
        render();
      }
      function drill(spec){
        var lots=filterLots().filter(function(l){ return l.spec===spec; });
        if(!lots.length) return;
        _ownerIgnoreMode=true;   // WIP 下钻:显示忽略列(管理员可操作)
        _ownerLots = lots.map(function(l){ return {
          model:l.model, wo_no:l.wo_no, full_wo:l.full_wo, lot_id:l.lot_id, product:l.product, spec:l.spec, desc:l.desc,
          status:l.status, eqpld:l.eqpld,
          run_time:l.run_time, wait_time:l.wait_time, hold_time:l.hold_time, qty:l.qty,
          eta_at:l.eta_at, eta_rem:l.eta_rem, eta_basis:l.eta_basis,   // 预计加工完成(同炉共享)
          hold_reason:l.hold_reason||'', txn_comment:l.txn_comment||'',
          ignored:l.ignored?1:0, ignore_note:l.ignore_note||'' }; });
        _ownerSortKey=''; _ownerSortAsc=true;
        var nm=(lots[0] && lots[0].desc) || spec;
        document.getElementById('ownerTitle').textContent = nm+' · 在制 LOT 明细';
        document.getElementById('ownerMask').style.display='block';
        document.getElementById('ownerBox').style.display='flex';
        renderOwnerLots();
      }
      function toggleUrg(){
        // 独立开关:NTO 在制型号 ⇄ 加急Lot 批次。切换数据源、下拉、按钮高亮后重绘。
        WIP_MODE = isUrg() ? 'nto' : 'urgent';
        var on=isUrg();
        var btn=document.getElementById('wip_urg_btn');
        if(btn){ btn.style.background=on?'#f59e0b':'#fff'; btn.style.color=on?'#fff':'#b45309';
                 btn.textContent=on?'↩ NTO工单':'⚡ 加急Lot'; }   // 文案=点它会切到的目标
        var ln=document.getElementById('wip_lbl_nto'), lu=document.getElementById('wip_lbl_urg');
        if(ln) ln.style.display=on?'none':'';
        if(lu) lu.style.display=on?'':'none';
        var s=document.getElementById('wip_search'); if(s) s.value='';
        refreshOwnerLoad();
        render();
      }
      return {render:render, onModel:onModel, onSearch:onSearch, drill:drill, toggleUrg:toggleUrg};
    })();
    try { wip.render(); } catch(e) {}
    window.addEventListener('load', function(){ try{ wip.render(); }catch(e){} });
    // 工序柱悬停交互(与生产进度看板工序块看齐):跟随光标的浮动提示框 + 柱体高亮。
    // #wip_chart 会整体重绘,故用事件委托挂在容器上;浮层与样式只注入一次。
    (function(){
      var box=document.getElementById('wip_chart'); if(!box) return;
      var tip=document.getElementById('wip_tip');
      if(!tip){ tip=document.createElement('div'); tip.id='wip_tip'; document.body.appendChild(tip); }
      if(!document.getElementById('wip_tip_css')){
        var st=document.createElement('style'); st.id='wip_tip_css';
        st.textContent='.wseg{transition:filter .15s ease;}'
          +'.wseg:hover .wbar{filter:brightness(1.06) saturate(1.05);box-shadow:0 4px 14px rgba(15,23,42,.18);border-radius:3px;}'
          +'#wip_tip{display:none;position:fixed;z-index:60;pointer-events:none;background:#fff;color:#0f172a;font-size:12px;line-height:1.5;padding:8px 12px;border:1px solid #cbd5e1;border-radius:7px;box-shadow:0 6px 18px rgba(0,0,0,.18);white-space:nowrap;transform:translate(-50%,-100%);opacity:0;transition:opacity .1s ease;}'
          +'#wip_tip::after{content:"";position:absolute;left:50%;bottom:-6px;transform:translateX(-50%);border:6px solid transparent;border-top-color:#fff;border-bottom:0;}';
        document.head.appendChild(st);
      }
      function place(e){ tip.style.left=e.clientX+'px'; tip.style.top=(e.clientY-12)+'px'; }
      box.addEventListener('mouseover', function(e){
        var t=e.target.closest && e.target.closest('.wseg'); if(!t) return;
        var tx=t.getAttribute('data-tip')||''; if(!tx) return;
        tip.innerHTML=tx; tip.style.display='block'; place(e);
        requestAnimationFrame(function(){ tip.style.opacity='1'; });
      });
      box.addEventListener('mousemove', function(e){
        if(tip.style.display==='none') return;
        if(e.target.closest && e.target.closest('.wseg')) place(e);
      });
      box.addEventListener('mouseout', function(e){
        var t=e.target.closest && e.target.closest('.wseg'); if(!t) return;
        var to=e.relatedTarget;
        if(to && to.closest && to.closest('.wseg')) return;   // 相邻柱间移动不闪烁
        tip.style.opacity='0'; tip.style.display='none';
      });
    })();
    </script>
    """
    return (js.replace("__WIP_LOTS__", lots_json).replace("__WIP_CT__", ct_json)
              .replace("__URG_LOTS__", urg_lots_json).replace("__URG_CT__", urg_ct_json)
              .replace("__URG_TARGET__", urg_target_json))


_owner_status_colors = {
    "运行中": "#22c55e", "等待进站": "#ffdd00", "等待出站": "#60a5fa",
    "HOLD": "#ef4444", "其他": "#94a3b8",
}


def owner_load_html(owners, owner_load, empty_msg="暂无负责人数据", empty_sub="从 MES 同步在制 lot 后显示"):
    """负责人工作量统计块(名字在左、条形图按 Lot 总数比例显示长短,
    共用同一刻度,最多者满格)。统计页初始渲染与 /owner_load 搜索接口共用。"""
    status_keys = list(owner_load.keys())
    rows_html = ""
    totals_all = [sum(owner_load[k][i] for k in status_keys)
                  for i in range(len(owners))]
    max_total = max(totals_all) if totals_all else 0
    max_total = max_total or 1
    for idx, ow in enumerate(owners):
        counts = {k: owner_load[k][idx] for k in status_keys}
        total = sum(counts.values())
        bar_pct = total / max_total * 100      # 该负责人占最大值的比例 → 条形总长
        segs = "".join(
            (f'<div class="wl-seg" onclick="showOwnerStatus(\'{_html.escape(ow)}\',\'{k}\')" '
             f'title="{k}:{counts[k]} 个Lot(点击查看)" '
             f'style="width:{counts[k]/total*100:.1f}%;background:{_owner_status_colors.get(k,"#94a3b8")};">'
             f'</div>')
            for k in status_keys if counts[k] > 0) if total else ""
        rows_html += (
            f'<div style="display:flex;align-items:center;gap:10px;padding:8px 6px;">'
            f'<div style="width:72px;flex:0 0 72px;font-size:13px;color:#334155;text-align:right;'
            f'font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_html.escape(ow)}</div>'
            f'<div class="wl-track" style="height:22px;">'
            f'<div style="width:{bar_pct:.1f}%;height:100%;display:flex;border-radius:5px;overflow:hidden;">{segs}</div>'
            f'</div>'
            f'<div style="width:54px;flex:0 0 54px;font-size:12px;color:#64748b;text-align:left;">{total} Lot</div>'
            f'</div>')
    legend = "".join(
        f'<i style="background:{_owner_status_colors[k]}"></i>{k} '
        for k in status_keys)
    if not rows_html:
        return (f'<div class="empty" style="padding:28px;"><div class="ico" style="font-size:32px;">👤</div>'
                f'<div class="tt">{_html.escape(empty_msg)}</div>'
                f'<div class="sb">{_html.escape(empty_sub)}</div></div>')
    return f'<div style="margin-top:8px;">{rows_html}</div><div class="legend" style="margin-top:10px;">{legend}</div>'


def render_stats(data, is_authed=False):
    wip_authed_js = 'true' if is_authed else 'false'   # 仅管理员可忽略 WIP LOT
    prod_bar = charts.bar(data["prod_dist"], width=520, height=240, color="#3b82f6", onclick="showModelOrders")
    owner_block = owner_load_html(data["owners"], data["owner_load"])

    rate = data["on_time_rate"]
    rate_color = "#22c55e" if rate >= 80 else ("#f59e0b" if rate >= 50 else "#ef4444")
    done_total = data.get("done_total", 0)
    on_time_total = data.get("on_time_total", 0)

    # 产品在制 WIP 数据(供「产品在制WIP」图):全量在制 lot + 各型号 CT 工序顺序 + 型号下拉
    import json as _json
    wip = data.get("wip") or {"wip_lots": [], "wip_ct": {}, "wip_models": []}
    wip_lots_json = _json.dumps(wip.get("wip_lots", []), ensure_ascii=False)
    wip_ct_json = _json.dumps(wip.get("wip_ct", {}), ensure_ascii=False)
    wip_models = wip.get("wip_models", [])
    wip_model_opts = "".join(
        f'<option value="{_html.escape(m["model"])}">{_html.escape(m["model"])} ({m["count"]} lot)</option>'
        for m in wip_models)
    # 加急Lot 模式数据(同结构,base=批次名);独立开关,与 NTO 区分
    urg = data.get("urgent_wip") or {"wip_lots": [], "wip_ct": {}, "wip_models": []}
    urg_lots_json = _json.dumps(urg.get("wip_lots", []), ensure_ascii=False)
    urg_ct_json = _json.dumps(urg.get("wip_ct", {}), ensure_ascii=False)
    urg_target_json = _json.dumps(urg.get("wip_target", {}), ensure_ascii=False)
    urg_models = urg.get("wip_models", [])
    urg_batch_opts = "".join(
        f'<option value="{_html.escape(m["model"])}">{_html.escape(m["model"])} ({m["count"]} lot)</option>'
        for m in urg_models) or '<option value="">暂无加急批次</option>'
    wip_legend = (
        '<i style="background:#22c55e"></i>RWIP(运行中) '
        '<i style="background:#ffdd00"></i>QWIP(等待) '
        '<i style="background:#ef4444"></i>HWIP(HOLD) '
        '<i style="background:#cbd5e1"></i>已忽略 '
        '<span style="border-left:1px solid #e2e8f0;margin-left:6px;padding-left:10px;">'
        '<i style="background:#2563eb;border-radius:0;height:2px;"></i>MOVE(产出·颗) '
        '<i style="background:#0f172a;border-radius:0;height:2px;"></i>CWIP累计(颗)</span>')

    body = f"""
    <div class="page-title">统计分析</div>
    <div class="page-sub">多维度数据可视化分析,辅助排产决策</div>
    <div class="panel" style="padding:10px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
      <span style="font-weight:600;color:#0f172a;font-size:13px;">准时完成率</span>
      <span style="font-size:22px;font-weight:700;color:{rate_color};line-height:1;">{rate}%</span>
      <div style="flex:0 1 220px;height:7px;background:#eef2f7;border-radius:4px;overflow:hidden;"><div style="width:{rate}%;height:100%;background:{rate_color};"></div></div>
      <span style="color:#64748b;font-size:12px;">{on_time_total} / {done_total} 个已完成工单在计划交付日内完成</span>
    </div>
    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px 16px;">
        <h3 style="margin:0;">产品在制 WIP 分布 <span id="wip_sub" style="font-size:12px;color:#94a3b8;font-weight:normal;"></span></h3>
        <div class="legend" style="margin:0;font-size:11px;">{wip_legend}</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <button id="wip_urg_btn" type="button" onclick="wip.toggleUrg()"
                  title="切换:NTO 在制型号 ⇄ 加急Lot 批次"
                  style="padding:6px 12px;border:1px solid #f59e0b;background:#fff;color:#b45309;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">⚡ 加急Lot</button>
          <label id="wip_lbl_nto" style="font-size:13px;color:#475569;">型号
            <select id="wip_model" onchange="wip.onModel()" style="margin-left:4px;padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;">{wip_model_opts}</select>
          </label>
          <label id="wip_lbl_urg" style="display:none;font-size:13px;color:#b45309;font-weight:600;">加急Lot
            <select id="wip_urg" onchange="wip.onModel()" style="margin-left:4px;padding:6px 10px;border:1px solid #f59e0b;border-radius:6px;font-size:13px;">{urg_batch_opts}</select>
          </label>
          <input id="wip_search" oninput="wip.onSearch()" placeholder="搜 产品 / 型号 / 工单号 / Lot号"
                 style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;width:200px;">
        </div>
      </div>
      <div id="wip_chart" style="margin-top:12px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;padding:22px 14px 12px;min-height:430px;box-sizing:border-box;"><div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在汇总在制 WIP…</div></div></div>
    </div>
    <div class="grid2" style="margin-top:30px;margin-bottom:30px;">
      <div class="panel"><h3>产品型号分布 <span style="font-size:12px;color:#94a3b8;font-weight:normal;">(点击柱状条查看该型号工单)</span></h3>{prod_bar}</div>
      <div class="panel"><h3>负责人工作量统计 <span style="font-size:12px;color:#94a3b8;font-weight:normal;">(点击状态色段查看对应 Lot · 跟随上方搜索联动)</span></h3>
        <div id="ownerPanel">{owner_block}</div>
      </div>
    </div>
    <!-- 负责人工单清单弹窗 -->
    <div id="ownerMask" onclick="hideOwner()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49;"></div>
    <div id="ownerBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:max-content;max-width:84vw;min-width:min(420px,90vw);max-height:85vh;background:#fff;border-radius:12px;
         z-index:50;box-shadow:0 20px 60px rgba(0,0,0,.3);overflow:hidden;flex-direction:column;">
      <div style="position:relative;padding:18px 24px 8px;flex:0 0 auto;">
        <h3 id="ownerTitle" style="margin:0;text-align:center;">负责人工单</h3>
        <div id="ownerHint" style="text-align:center;font-size:12px;color:#94a3b8;margin-top:6px;"></div>
        <button onclick="hideOwner()" style="position:absolute;top:16px;right:20px;border:none;background:none;cursor:pointer;font-size:14px;color:#64748b;">关闭</button>
      </div>
      <div id="ownerBody" style="font-size:13px;color:#64748b;text-align:center;overflow:auto;padding:0 24px 20px;flex:1 1 auto;">加载中…</div>
    </div>
    <script>
    var WIP_AUTHED = {wip_authed_js};        // 仅管理员可忽略 WIP LOT
    var _ownerIgnoreMode=false;              // 真=从 WIP 分布下钻(显示忽略列);假=负责人工作量
    var _ownerLots=[], _ownerSortKey='', _ownerSortAsc=true;
    var _ownerCols=[
      {{k:'model',  t:'型号'}},
      {{k:'wo_no',  t:'工单号'}},
      {{k:'lot_id', t:'Lot号'}},
      {{k:'product',t:'产品型号'}},
      {{k:'spec',   t:'工序'}},
      {{k:'desc',   t:'工序描述'}},
      {{k:'status', t:'状态'}},
      {{k:'eqpld',  t:'EQP ID'}},
      {{k:'run_time', t:'Run Time', num:true}},
      {{k:'wait_time',t:'Wait Time',num:true}},
      {{k:'hold_time',t:'Hold Time',num:true}},
      {{k:'qty',    t:'数量', num:true}},
      {{k:'hold_reason',t:'HOLD原因'}},
      {{k:'txn_comment',t:'MES备注'}},
      {{k:'eta_at',   t:'预计加工完成'}}
    ];
    function _numv(x){{ var v=parseFloat(String(x).replace('h','')); return isNaN(v)?-1:v; }}
    // MES 操作备注(TXNCOMMENTS)单元格:有则原文,无则灰显占位。
    function _cmtCell(v){{ var t=(v||'').trim(); return t?escapeHtml(t):'<span style="color:#cbd5e1;">—</span>'; }}
    function sortOwner(key){{
      if(_ownerSortKey===key){{ _ownerSortAsc=!_ownerSortAsc; }}
      else {{ _ownerSortKey=key; _ownerSortAsc=true; }}
      var col=_ownerCols.find(function(c){{return c.k===key;}});
      _ownerLots.sort(function(a,b){{
        var x=a[key], y=b[key];
        if(col&&col.num){{ x=_numv(x); y=_numv(y); return _ownerSortAsc?x-y:y-x; }}
        x=String(x||''); y=String(y||'');
        return _ownerSortAsc? x.localeCompare(y) : y.localeCompare(x);
      }});
      renderOwnerLots();
    }}
    // 复制到剪贴板(带降级)+ 在 hint 处给 1.5s 反馈
    function _fallbackCopy(text){{
      var ta=document.createElement('textarea'); ta.value=text;
      ta.style.position='fixed'; ta.style.top='-1000px'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      try{{ document.execCommand('copy'); }}catch(e){{}}
      document.body.removeChild(ta);
    }}
    function _toClip(text, okmsg){{
      var hint=document.getElementById('ownerHint');
      function done(){{ var old=_ownerHintBase; hint.textContent=okmsg;
        setTimeout(function(){{ hint.textContent=old; }},1500); }}
      if(navigator.clipboard && navigator.clipboard.writeText){{
        navigator.clipboard.writeText(text).then(done, function(){{ _fallbackCopy(text); done(); }});
      }} else {{ _fallbackCopy(text); done(); }}
    }}
    // 整列复制:把该列所有行的值按换行拼接复制,避免拖选串到别的列
    function copyOwnerCol(ev, key){{
      if(ev) ev.stopPropagation();
      var col=_ownerCols.find(function(c){{return c.k===key;}});
      var vals=_ownerLots.map(function(l){{ return String(l[key]==null?'':l[key]); }});
      _toClip(vals.join('\\n'), '已复制「'+(col?col.t:key)+'」共 '+vals.length+' 行');
    }}
    var _ownerHintBase='';
    function renderOwnerLots(){{
      var body=document.getElementById('ownerBody');
      var hint=document.getElementById('ownerHint');
      if(!_ownerLots.length){{ hint.textContent=''; body.innerHTML='<div style="padding:14px;color:#94a3b8;">该负责人该状态下暂无 Lot</div>'; return; }}
      var showIgn=_ownerIgnoreMode;   // WIP 下钻才显示「忽略」列
      var nIgn=showIgn?_ownerLots.filter(function(l){{return l.ignored;}}).length:0;
      _ownerHintBase='共 '+_ownerLots.length+' 个 Lot'+(showIgn&&nIgn?(' · 已忽略 '+nIgn+' 个'):'')
        +' · 点表头排序 · 点表头 ⧉ 复制整列 · 点工单号查看完整明细'
        +(showIgn?(WIP_AUTHED?' · 忽略=视同报废,不计入任何统计(含数量)':' · 仅管理员可忽略'):'');
      hint.textContent=_ownerHintBase;
      var th='text-align:center;padding:6px 12px;background:#eef2f7;position:sticky;top:0;z-index:1;user-select:none;white-space:nowrap;';
      var h='<table style="border-collapse:collapse;text-align:center;font-size:13px;margin:0 auto;white-space:nowrap;"><thead><tr style="color:#475569;">';
      _ownerCols.forEach(function(c){{
        var arrow = _ownerSortKey===c.k ? (_ownerSortAsc?' ▲':' ▼') : ' ⇅';
        h+='<td style="'+th+'"><span style="cursor:pointer;" onclick="sortOwner(\\''+c.k+'\\')">'+c.t
          +'<span style="color:#94a3b8;font-size:11px;">'+arrow+'</span></span>'
          +'<span onclick="copyOwnerCol(event,\\''+c.k+'\\')" title="复制整列" '
          +'style="margin-left:6px;cursor:pointer;color:#2563eb;font-size:12px;">⧉</span></td>';
      }});
      if(showIgn) h+='<td style="'+th+'">忽略</td>';
      h+='</tr></thead><tbody>';
      _ownerLots.forEach(function(l,i){{
        var rowbg=(showIgn&&l.ignored)?'background:#f1f5f9;color:#64748b;':'';
        h+='<tr style="border-top:1px solid #f1f5f9;'+rowbg+'" onmouseover="if(!'+((showIgn&&l.ignored)?'true':'false')+')this.style.background=\\'#f8fafc\\'" onmouseout="this.style.background=\\''+((showIgn&&l.ignored)?'#f1f5f9':'')+'\\'">'
          +'<td style="padding:6px 15px;text-align:center;font-weight:600;color:#0f172a;">'+(l.model||'')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;"><span onclick="openDetailFromOwner(\\''+(l.full_wo||l.wo_no)+'\\')" title="点击查看该工单完整明细" style="color:#2563eb;cursor:pointer;text-decoration:underline;">'+(l.full_wo||l.wo_no)+'</span></td>'
          +'<td style="padding:6px 15px;text-align:center;">'+escapeHtml(l.lot_id)+'</td>'
          +'<td style="padding:6px 15px;text-align:center;color:#64748b;">'+(l.product||'')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(l.spec||'')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;color:#64748b;white-space:normal;max-width:220px;">'+(l.desc||'')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+statusBadge(l.status||'')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escapeHtml(l.eqpld)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escapeHtml(l.run_time)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escapeHtml(l.wait_time)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;">'+(escapeHtml(l.hold_time)||'-')+'</td>'
          +'<td style="padding:6px 15px;text-align:center;font-weight:600;">'+(l.qty||0)+'</td>'
          +'<td style="padding:6px 12px;text-align:center;color:#475569;">'+(escapeHtml(l.hold_reason)||'<span style="color:#cbd5e1;">—</span>')+'</td>'
          +'<td style="padding:6px 12px;text-align:left;max-width:300px;white-space:normal;word-break:break-word;color:#334155;line-height:1.4;">'+_cmtCell(l.txn_comment)+'</td>'
          +'<td style="padding:6px 15px;text-align:center;" title="纯加工预计完成时刻=颗数÷设备UPH−已运行;同炉站点共享同一时刻">'+etaCell(l)+'</td>';
        if(showIgn){{
          h+='<td style="padding:6px 15px;text-align:center;white-space:nowrap;">'
            +(WIP_AUTHED
                ? '<input type="checkbox" '+(l.ignored?'checked':'')+' onchange="wipToggleIgnore('+i+',this.checked)" title="勾选=忽略(视同报废),取消=恢复" style="vertical-align:middle;cursor:pointer;width:16px;height:16px;">'
                  +(l.ignored
                      ? '<textarea class="ignnote" rows="1" oninput="_noteAutosize(this)" onchange="wipSaveNote('+i+',this.value)" placeholder="忽略备注(可换行)" style="vertical-align:middle;margin-left:6px;width:16ch;min-height:34px;font-size:12px;line-height:1.4;padding:3px 6px;border:1px solid #cbd5e1;border-radius:5px;resize:none;overflow:hidden;font-family:inherit;">'+escapeHtml(l.ignore_note)+'</textarea>'
                      : '')
                : (l.ignored?('已忽略'+(l.ignore_note?' · '+escapeHtml(l.ignore_note):'')):'—'))
          +'</td>';
        }}
        h+='</tr>';
      }});
      h+='</tbody></table>';
      if(showIgn&&WIP_AUTHED) h+='<div style="font-size:12px;color:#94a3b8;margin-top:10px;">忽略后该 LOT 视同报废,其全部数据(含数量)不计入统计,柱状图灰显;取消忽略后数据重新生效。</div>';
      body.innerHTML=h;
      body.querySelectorAll('.ignnote').forEach(_noteAutosize);   // 初始化备注框自适应尺寸
    }}
    // WIP 下钻 LOT 忽略(管理员):复选框勾选=忽略(视同报废),取消=恢复;勾选后内联备注框可直接编辑
    function wipToggleIgnore(i, checked){{
      var l=_ownerLots[i]; if(!l) return;
      _wipSubmitIgnore(l, checked?1:0, checked?(l.ignore_note||''):'');
    }}
    function wipSaveNote(i, val){{
      var l=_ownerLots[i]; if(!l || !l.ignored) return;
      _wipSubmitIgnore(l, 1, (val||'').trim());
    }}
    function _wipSubmitIgnore(l, ig, note){{
      var prevI=l.ignored, prevN=l.ignore_note||'';
      l.ignored=ig; l.ignore_note=ig?note:'';
      // 同步更新底层 WIP_LOTS(按 lot_id),使柱状图即时灰显/恢复
      if(typeof WIP_LOTS!=='undefined'){{
        for(var k=0;k<WIP_LOTS.length;k++){{ if(WIP_LOTS[k].lot_id===l.lot_id){{ WIP_LOTS[k].ignored=ig; WIP_LOTS[k].ignore_note=l.ignore_note; }} }}
      }}
      renderOwnerLots(); try{{ wip.render(); }}catch(e){{}}
      var body='lot_id='+encodeURIComponent(l.lot_id)
             +'&wo='+encodeURIComponent(l.wo_no||'')
             +'&spec='+encodeURIComponent(l.spec||'')
             +'&ignore='+(ig?'1':'0')
             +'&note='+encodeURIComponent(note||'');
      fetch('/ignore_lot', {{method:'POST',
            headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},
            body:body}})
        .then(function(r){{return r.json();}})
        .then(function(res){{
          if(res && res.ok){{
            var mt=''; try{{ mt=res.msg||''; }}catch(_e0){{}}
            try{{ wipToast('已保存:'+ (ig?'忽略(视同报废)':'已取消忽略') + (mt?(' '+mt):'')); }}catch(_e1){{}}
          }} else {{
            wipToast((res&&res.msg)||'操作失败','err');
            l.ignored=prevI; l.ignore_note=prevN;
            if(typeof WIP_LOTS!=='undefined'){{ for(var k=0;k<WIP_LOTS.length;k++){{ if(WIP_LOTS[k].lot_id===l.lot_id){{ WIP_LOTS[k].ignored=prevI; WIP_LOTS[k].ignore_note=prevN; }} }} }}
            renderOwnerLots(); try{{ wip.render(); }}catch(e){{}}
          }}
        }})
        .catch(function(){{
          wipToast('网络错误,未保存','err');
          l.ignored=prevI; l.ignore_note=prevN;
          if(typeof WIP_LOTS!=='undefined'){{ for(var k=0;k<WIP_LOTS.length;k++){{ if(WIP_LOTS[k].lot_id===l.lot_id){{ WIP_LOTS[k].ignored=prevI; WIP_LOTS[k].ignore_note=prevN; }} }} }}
          renderOwnerLots(); try{{ wip.render(); }}catch(e){{}}
        }});
    }}
    function showOwnerStatus(name, status){{
      _ownerIgnoreMode=false;   // 负责人工作量下钻:不显示忽略列
      document.getElementById('ownerTitle').textContent = name+' '+status+' 的 Lot 明细';
      document.getElementById('ownerMask').style.display='block';
      document.getElementById('ownerBox').style.display='flex';
      var body=document.getElementById('ownerBody'); body.innerHTML='加载中…';
      document.getElementById('ownerHint').textContent='';
      _ownerSortKey=''; _ownerSortAsc=true;
      fetch('/owner_lots?owner='+encodeURIComponent(name)+'&status='+encodeURIComponent(status))
        .then(function(r){{return r.json();}})
        .then(function(list){{ _ownerLots=list||[]; renderOwnerLots(); }})
        .catch(function(){{ body.innerHTML='<div style="padding:14px;color:#dc2626;">加载失败</div>'; }});
    }}
    function hideOwner(){{ document.getElementById('ownerMask').style.display='none'; document.getElementById('ownerBox').style.display='none'; }}
    // A3: 从 Lot 明细点进工单详情时,记住来源,详情关闭后返回 Lot 明细
    function openDetailFromOwner(wo){{
      document.getElementById('ownerBox').style.display='none';   // 暂时藏起(遮罩保留)
      window._detailReturn='owner';
      showDetail(wo);
    }}
    </script>
    <style>.barclick{{transition:opacity .12s;}} .barclick:hover rect{{opacity:.82;}}</style>
    <!-- 产品型号工单弹窗(点「产品型号分布」柱下钻,样式模仿工单明细) -->
    <div id="modelMask" onclick="hideModel()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49;"></div>
    <div id="modelBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:max-content;max-width:84vw;min-width:min(520px,90vw);max-height:85vh;background:#fff;border-radius:12px;
         z-index:50;box-shadow:0 20px 60px rgba(0,0,0,.3);overflow:hidden;flex-direction:column;">
      <div style="position:relative;padding:18px 24px 8px;flex:0 0 auto;">
        <h3 id="modelTitle" style="margin:0;text-align:center;">型号工单</h3>
        <div id="modelHint" style="text-align:center;font-size:12px;color:#94a3b8;margin-top:6px;"></div>
        <button onclick="hideModel()" style="position:absolute;top:16px;right:20px;border:none;background:none;cursor:pointer;font-size:14px;color:#64748b;">关闭</button>
      </div>
      <div id="modelBody" style="font-size:13px;color:#64748b;text-align:center;overflow:auto;padding:0 24px 20px;flex:1 1 auto;">加载中…</div>
    </div>
    <script>
    function prioBadge(p){{
      var map={{'火箭批':['#fee2e2','#b91c1c'],'1-0':['#ffedd5','#c2410c'],'1-1':['#fef9c3','#854d0e'],'Normal':['#f1f5f9','#475569']}};
      if(!p) return '-';
      var c=map[p]||['#f1f5f9','#475569'];
      return '<span style="padding:1px 6px;border-radius:3px;background:'+c[0]+';color:'+c[1]+';white-space:nowrap;">'+escapeHtml(p)+'</span>';
    }}
    function riskBadge(r){{
      var map={{'高风险':['#fecaca','#b91c1c'],'低风险':['#fef9c3','#854d0e'],'正常':['#dcfce7','#15803d'],'超前':['#dbeafe','#1d4ed8']}};
      if(!r) return '-';
      var c=map[r]||['#f1f5f9','#475569'];
      return '<span style="padding:1px 6px;border-radius:3px;background:'+c[0]+';color:'+c[1]+';white-space:nowrap;">'+escapeHtml(r)+'</span>';
    }}
    function showModelOrders(model){{
      document.getElementById('modelTitle').textContent = model+' · 工单明细';
      document.getElementById('modelMask').style.display='block';
      document.getElementById('modelBox').style.display='flex';
      document.getElementById('modelHint').textContent='';
      var body=document.getElementById('modelBody'); body.innerHTML='加载中…';
      fetch('/model_orders?model='+encodeURIComponent(model))
        .then(function(r){{return r.json();}})
        .then(function(list){{ renderModelOrders(model, list||[]); }})
        .catch(function(){{ body.innerHTML='<div style="padding:16px;color:#dc2626;">加载失败</div>'; }});
    }}
    function renderModelOrders(model, list){{
      var body=document.getElementById('modelBody');
      var hint=document.getElementById('modelHint');
      if(!list.length){{ hint.textContent=''; body.innerHTML='<div style="padding:16px;color:#94a3b8;">该型号暂无在制工单</div>'; return; }}
      hint.textContent='共 '+list.length+' 个在制工单 · 点工单号查看完整明细';
      var th='text-align:center;padding:7px 12px;background:#eef2f7;position:sticky;top:0;white-space:nowrap;';
      var h='<table style="border-collapse:collapse;text-align:center;font-size:13px;margin:0 auto;white-space:nowrap;"><thead><tr style="color:#475569;">'
        +'<td style="'+th+'">工单号</td><td style="'+th+'">产品型号</td><td style="'+th+'">状态</td><td style="'+th+'">优先级</td>'
        +'<td style="'+th+'">需求方</td><td style="'+th+'">计划交付</td><td style="'+th+'">当前工序</td><td style="'+th+'">负责人</td>'
        +'<td style="'+th+'">进度</td><td style="'+th+'">风险</td></tr></thead><tbody>';
      list.forEach(function(o){{
        var pg=o.progress||0;
        h+='<tr style="border-top:1px solid #f1f5f9;" onmouseover="this.style.background=\\'#f8fafc\\'" onmouseout="this.style.background=\\'\\'">'
          +'<td style="padding:6px 12px;"><span onclick="openDetailFromModel(\\''+o.wo_no+'\\')" title="查看完整明细" style="color:#2563eb;cursor:pointer;text-decoration:underline;font-weight:600;">'+escapeHtml(o.wo_no)+'</span></td>'
          +'<td style="padding:6px 12px;">'+escapeHtml(o.product)+'</td>'
          +'<td style="padding:6px 12px;">'+statusBadge(o.status||'')+'</td>'
          +'<td style="padding:6px 12px;">'+prioBadge(o.priority||'')+'</td>'
          +'<td style="padding:6px 12px;color:#64748b;">'+(escapeHtml(o.demander)||'-')+'</td>'
          +'<td style="padding:6px 12px;">'+(escapeHtml(o.plan_end)||'-')+'</td>'
          +'<td style="padding:6px 12px;color:#64748b;">'+(escapeHtml(o.cur_process)||'-')+'</td>'
          +'<td style="padding:6px 12px;">'+(escapeHtml(o.owner)||'-')+'</td>'
          +'<td style="padding:6px 12px;min-width:92px;"><div style="font-size:11px;color:#64748b;">'+pg+'%</div><div style="height:6px;background:#eef2f7;border-radius:3px;overflow:hidden;"><div style="width:'+pg+'%;height:100%;background:#3b82f6;"></div></div></td>'
          +'<td style="padding:6px 12px;" title="'+escapeHtml(o.risk_note||'')+'">'+riskBadge(o.risk_level||'')+'</td>'
          +'</tr>';
      }});
      h+='</tbody></table>';
      body.innerHTML=h;
    }}
    function hideModel(){{ document.getElementById('modelMask').style.display='none'; document.getElementById('modelBox').style.display='none'; }}
    function openDetailFromModel(wo){{ showDetail(wo); }}   // 详情弹窗 z-index 更高,盖在型号弹窗之上;关掉即回到列表
    </script>
    """ + _detail_modal() + """
    """ + _wip_chart_js(wip_lots_json, wip_ct_json, urg_lots_json, urg_ct_json, urg_target_json)
    return layout("stats", "统计分析", body)


# ---------- 数据源页 ----------
def render_data(orders, prefixes, mes_rows, synced, msg="", csv_ok=True,
                last_sync_at="", last_sync_status=""):
    """数据源页。
    prefixes: [(prefix,[录入型号...]),...] 来自后台白名单
    mes_rows: 已同步的 MES 工单(含 processes)
    synced: [(model, wo_count, match_mode, synced_at)...] 同步概况
    csv_ok: MES csv 是否存在
    last_sync_at/status: 最近自动/手动刷新时间与结果
    """
    msg_html = f'<div class="flash">{_html.escape(msg)}</div>' if msg else ""

    # 抓取白名单(来自后台录入型号,前两字符去重)
    if prefixes:
        chips = "".join(
            f'<span class="btag" style="background:#eef2ff;">{_html.escape(pre)} '
            f'<span style="color:#94a3b8;font-size:11px;">({"/".join(_html.escape(m) for m in models)})</span></span>'
            for pre, models in prefixes)
    else:
        chips = '<span style="color:#94a3b8;font-size:13px;">后台管理里还没录入产品型号。请先去“后台管理”添加,如 MC1、MB1。</span>'

    csv_warn = "" if csv_ok else (
        '<div class="flash" style="background:#fef2f2;color:#dc2626;">'
        '未找到 MES数据库.csv。请把它放到程序同目录,或设置环境变量 MES_CSV 指向其路径。</div>')

    # MES 抓取区
    sync_section = f"""
    <div class="panel"><h3>从 MES 抓取在制工单</h3>
      {csv_warn}
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:10px;">
        <span style="font-size:13px;color:#16a34a;">● 数据源每 3 分钟 · 界面随数据自动更新</span>
        <span style="font-size:12px;color:#94a3b8;">上次刷新:{_html.escape(last_sync_at or "尚未刷新")}
          {("· " + _html.escape(last_sync_status)) if last_sync_status else ""}</span>
      </div>
      <div style="font-size:13px;color:#475569;margin-bottom:10px;">
        按后台录入型号<b>从严到松</b>匹配 MES 的 PACKAGESIZE 抓取(先精确、无则前两字符兜底)。当前白名单前缀:
      </div>
      <div class="btags" style="margin-bottom:12px;">{chips}</div>
      <form method="post" action="/data/sync_mes">
        <button type="submit" class="btn-primary" style="width:auto;padding:8px 20px;"
          {"disabled" if not (prefixes and csv_ok) else ""}>⟳ 立即刷新</button>
        <span style="font-size:12px;color:#94a3b8;margin-left:10px;">
          手动触发一次抓取(与自动刷新逻辑相同)</span>
      </form>
    </div>"""

    # 同步概况
    if synced:
        mode_label = {"exact": "精确", "prefix": "前缀"}
        sync_info = "".join(
            f'<tr><td>{_html.escape(s["model"])}</td>'
            f'<td><span class="tag tag-normal">{mode_label.get(s.get("match_mode"),"")}</span></td>'
            f'<td>{s["wo_count"]}</td>'
            f'<td style="color:#94a3b8;">{_html.escape(s["synced_at"] or "")}</td></tr>'
            for s in synced)
        synced_panel = f"""
        <div class="panel"><h3>已抓取概况</h3>
          <table><thead><tr><th>录入型号</th><th>匹配</th><th>工单数</th><th>最近抓取</th></tr></thead>
          <tbody>{sync_info}</tbody></table>
        </div>"""
    else:
        synced_panel = ""

    # MES 工单列表(每行可展开看在制工序)
    mes_list = ""
    for o in mes_rows:
        procs = o.get("processes", [])
        proc_rows = "".join(
            f'<tr><td>{_html.escape(str(p.get("seq","")))}</td>'
            f'<td>{_html.escape(p.get("spec",""))}</td>'
            f'<td>{_html.escape(p.get("desc",""))}</td>'
            f'<td>{_html.escape(p.get("status",""))}</td>'
            f'<td>{p.get("lots",0)}</td><td>{p.get("qty",0)}</td></tr>'
            for p in procs)
        mes_list += f"""
        <details class="mes-wo">
          <summary>
            <b>{_html.escape(o["wo_no"])}</b>
            <span class="tag tag-normal">{_html.escape(o["product"])}</span>
            <span style="color:#64748b;font-size:12px;">优先级 {_html.escape(o["priority"] or "")}</span>
            <span style="float:right;color:#64748b;font-size:12px;">
              {o["lot_count"]} 批 / {o["qty"]} pcs · {len(procs)} 个在制工序</span>
          </summary>
          <div style="padding:8px 12px;overflow:auto;">
            <table><thead><tr><th>工序序</th><th>工序</th><th>描述</th><th>状态</th><th>批次</th><th>数量</th></tr></thead>
            <tbody>{proc_rows}</tbody></table>
          </div>
        </details>"""
    mes_panel = f"""
    <div class="panel"><h3>MES 在制工单 · 共 {len(mes_rows)} 个工单</h3>
      <div style="font-size:12px;color:#94a3b8;margin-bottom:8px;">点击工单展开查看其所有在制工序</div>
      {mes_list or '<div style="color:#94a3b8;font-size:13px;">尚未抓取。点上方“立即抓取”。</div>'}
    </div>"""

    # 手动工单(原有)
    manual_rows = "".join(f"""
      <tr><td>{_html.escape(o["wo_no"])}</td><td>{_html.escape(o["product"] or "")}</td>
      <td>{_html.escape(o["customer"] or "")}</td><td>{_html.escape(o["cur_process"] or "")}</td>
      <td>{_status_tag(o["status"])}</td><td>{_html.escape(o["owner"] or "")}</td>
      <td>{_prio_tag(o["priority"])}</td></tr>""" for o in orders)

    body = f"""
    <div class="page-title">数据源</div>
    <div class="page-sub">MES 在制品数据管理,支持从 MES 抓取与 Excel/CSV 导入</div>
    {msg_html}
    {sync_section}
    {synced_panel}
    {mes_panel}
    <div class="panel"><h3>导入数据(手动工单)</h3>
      <form method="post" action="/data/import" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx,.csv" required>
        <button type="submit" class="btn-primary" style="width:auto;padding:7px 18px;">导入</button>
      </form>
      <div style="font-size:12px;color:#94a3b8;margin-top:8px;">
        支持 .xlsx 和 .csv。列名按表头自动映射(工单号/产品型号/状态/负责人等)。</div>
    </div>
    <div class="panel"><h3>手动工单数据 · 共 {len(orders)} 条</h3>
      <div style="overflow:auto;"><table>
        <thead><tr><th>工单号</th><th>产品型号</th><th>客户</th><th>当前工序</th><th>状态</th><th>负责人</th><th>优先级</th></tr></thead>
        <tbody>{manual_rows}</tbody></table></div>
    </div>
    """
    return layout("data", "数据源", body)


# ---------- 后台管理页 ----------
def _order_form(o=None, action="/admin/create", products=None, ct_specs=None, eng_times=None):
    """工单新建/编辑表单。o 为 None 时是新建。
    去掉状态/风险/进度/当前工序(均由 MES 实时同步,手填无意义)。
    产品型号改下拉,选项来自基础数据;选 MB12 时弹出子项目(4in1/1in1)。
    完成工序:按所选型号联动(选项来自该型号 CT 表工序流),工单做到该工序即完工;
    风险/进度/ETA 据此折算(空=做完整条 CT)。
    """
    o = o or {}
    products = products or []
    ct_specs = ct_specs or {}
    # products 可能是 [{'id':..,'value':'MB1'},...](来自 base_list)或纯字符串列表
    prod_names = [p["value"] if isinstance(p, dict) else p for p in products]
    def val(k): return _html.escape(str(o.get(k, "")))

    # 产品下拉(基础数据型号)。MB12 选中时显示子项目框。
    cur_prod = o.get("product", "") or ""
    # 拆出主型号和子项目:'MB12 / 4in1' → ('MB12','4in1')
    main_prod, sub_prod = cur_prod, ""
    if "/" in cur_prod:
        parts = [p.strip() for p in cur_prod.split("/", 1)]
        main_prod, sub_prod = parts[0], (parts[1] if len(parts) > 1 else "")
    # 归一化型号选项:把 'MB12/4in1'、'MB12 / 1in1' 之类统一并入 'MB12',
    # 避免基础数据里残留变体导致选 MB12 时无选项。其余型号原样保留、去重。
    norm = []
    for p in prod_names:
        base = mes.base_model(p)      # 'MB12/4in1' → 'MB12'
        base = base if base else p
        if base not in norm:
            norm.append(base)
    # 不再强制塞 MB12:产品下拉只列「当前类别基础数据」里有的型号
    # (base_list 按塑封/灌封分库)。基础数据里有 MB12 或其变体(MB12/4in1 等)时,
    # 上面的归一化已得到 MB12;灌封类没录入 MB12 就不该出现在下拉里。
    # 编辑老工单若其型号(如历史的 MB12)不在当前基础数据里,这里补回,避免回显丢值。
    if main_prod and main_prod not in norm:
        norm.append(main_prod)
    prod_opts = '<option value="">请选择型号</option>' + "".join(
        f'<option value="{_html.escape(p)}"{" selected" if p==main_prod else ""}>{_html.escape(p)}</option>'
        for p in norm)
    sub_opts = ('<option value="4in1"' + (' selected' if sub_prod == '4in1' else '') + '>4in1</option>'
                '<option value="1in1"' + (' selected' if sub_prod == '1in1' else '') + '>1in1</option>')
    edit_flag = "true" if o.get("id") else "false"   # 编辑模式不做查重

    # 完成工序:按当前型号的 CT 工序流给初始选项(JS 会随型号切换重建)。
    # 下拉显示中文工序名,value 用 SPECNAME(后端按 SPECNAME 折算累计CT)。
    cur_final = o.get("final_process", "") or ""
    fp_specs = ct_specs.get(main_prod, [])
    fp_opts = '<option value="">做完整条工序流(默认)</option>' + "".join(
        f'<option value="{_html.escape(s["spec"])}"{" selected" if s["spec"]==cur_final else ""}>{_html.escape(s["spec"] + " " + s["name"])}</option>'
        for s in fp_specs)
    import json as _json
    ct_specs_json = _json.dumps(ct_specs, ensure_ascii=False)
    cur_final_json = _json.dumps(cur_final, ensure_ascii=False)
    # 初始 ENG 时间(编辑时回填)
    _init_eng = o.get("eng_times") or ""
    _init_eng_json = "{}"
    if _init_eng:
        try:
            _init_items = _json.loads(_init_eng)
            _init_map = {}
            for e in _init_items:
                if e.get("spec") and e.get("eng_h"):
                    _init_map[e["spec"]] = {"eng_h": e["eng_h"], "note": e.get("note", "")}
            _init_eng_json = _json.dumps(_init_map, ensure_ascii=False)
        except (ValueError, TypeError):
            pass

    return f"""
    <form method="post" action="{action}" class="wo-form">
      <input type="hidden" name="id" value="{val('id')}">
      <div class="frow"><label>工单号</label>
        <div style="flex:1;">
          <input name="wo_no" id="wo_no_input" value="{val('wo_no')}" required style="width:100%;"
                 placeholder="如 *50000010912(必须以 * 开头)"
                 oninput="checkWo()"
                 onblur="if(this.value.trim() && this.value.trim()[0]!=='*') this.value='*'+this.value.trim().replace(/^\\*+/,''); checkWo();">
          <div id="wo_hint" style="font-size:12px;margin-top:4px;min-height:16px;"></div>
        </div>
        <button type="button" class="btn-reset" style="flex:0 0 auto;width:auto;padding:6px 12px;margin-left:6px;align-self:flex-start;"
                onclick="lookupMes()">从 MES 查工序</button>
      </div>
      <div id="mes_result" style="display:none;font-size:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px;margin-bottom:9px;"></div>
      <div class="frow"><label>产品型号</label>
        <select name="product_main" id="product_main" onchange="onProdChange()">{prod_opts}</select></div>
      <div class="frow" id="sub_row" style="display:{'flex' if main_prod=='MB12' else 'none'};">
        <label>子项目</label>
        <select name="product_sub" id="product_sub">{sub_opts}</select></div>
      <div class="frow"><label>完成工序</label>
        <div style="flex:1;">
          <select name="final_process" id="final_process" style="width:100%;">{fp_opts}</select>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;">工单做到此工序即完工;风险/进度/预计完工按该工序累计 CT 折算(留空=做完整条工序流)</div>
        </div></div>
      <div class="frow"><label>交付数量</label>
        <input name="deliver_qty" type="number" min="0" step="1" value="{val('deliver_qty')}"
               placeholder="目标交付颗数(选填)"></div>
      <div class="frow"><label>客户</label><input name="customer" value="{val('customer')}"></div>
      <div class="frow"><label>需求方</label><input name="demander" value="{val('demander')}" placeholder="手动填写需求方"></div>
      <div class="frow"><label>优先级</label>
        <select name="priority">{_sel(['火箭批','1-0','1-1','Normal'], o.get('priority'))}</select></div>
      <div class="frow"><label>计划开始</label>
        <input id="plan_start" name="plan_start" type="date" value="{val('plan_start')}"
               oninput="document.getElementById('clear_plan_start').value=''">
        <button type="button" class="btn-reset" style="flex:0 0 auto;width:auto;padding:6px 10px;margin-left:6px;"
                onclick="clearPlanDate('plan_start')">清除</button>
        <input type="hidden" name="clear_plan_start" id="clear_plan_start" value=""></div>
      <div class="frow"><label>计划交付</label>
        <input id="plan_end" name="plan_end" type="date" value="{val('plan_end')}"
               oninput="document.getElementById('clear_plan_end').value=''">
        <button type="button" class="btn-reset" style="flex:0 0 auto;width:auto;padding:6px 10px;margin-left:6px;"
                onclick="clearPlanDate('plan_end')">清除</button>
        <input type="hidden" name="clear_plan_end" id="clear_plan_end" value=""></div>
      <div class="frow"><label>负责人</label>
        <input value="自动按 Owner 表抓取" disabled
               style="flex:1;background:#f1f5f9;color:#94a3b8;border:1px solid #e2e8f0;border-radius:6px;padding:6px 9px;"></div>
      <div class="frow"><label>备注</label>
        <textarea name="remark" rows="3" style="flex:1;resize:vertical;font-family:inherit;font-size:14px;padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;" placeholder="可填写补充说明…">{val('remark')}</textarea></div>
      <div class="frow" style="align-items:flex-start;">
        <label style="padding-top:6px;cursor:pointer;" onclick="var b=document.getElementById('eng_box');var a=document.getElementById('eng_arrow');b.style.display=b.style.display==='none'?'block':'none';a.textContent=b.style.display==='none'?'▸':'▾';">🕐 ENG验证时间 <span id="eng_arrow">▸</span></label>
        <div id="eng_box" style="flex:1;display:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;max-height:360px;overflow:auto;">
          <div style="font-size:12px;color:#64748b;margin-bottom:8px;">为需要特殊验证的工序填写 ENG 时间(小时)和备注，留空则使用标准 CT 时间</div>
          <table style="width:100%;font-size:12px;border-collapse:collapse;" id="eng_tbl">
            <thead><tr style="color:#64748b;"><th style="text-align:left;padding:4px 6px;width:40%;">工序</th><th style="text-align:left;padding:4px 6px;width:25%;">ENG时间(h)</th><th style="text-align:left;padding:4px 6px;">备注</th></tr></thead>
            <tbody id="eng_tbody"><tr><td colspan="3" style="color:#94a3b8;padding:8px 6px;">请先选择产品型号</td></tr></tbody>
          </table>
        </div>
      </div>
      <div class="fbtns"><button type="submit" class="btn-primary">保存</button>
        <button type="button" class="btn-reset" onclick="if(window.adminResetForm){{adminResetForm()}}else{{location.href='/admin'}}">取消</button></div>
    </form>
    <script>
    var WO_IS_EDIT = {edit_flag};
    var CT_SPECS = {ct_specs_json};
    var FINAL_PROC_CUR = {cur_final_json};
    var _initEngTimes = {_init_eng_json};
    var _woTimer = null;
    // 清除计划开始/交付日:置空日期框 + 标记 clear_xxx=1,后端据此把该日期清空(空值会被表单丢弃,故用标记)
    function clearPlanDate(field) {{
      var inp = document.getElementById(field);
      if (inp) inp.value = '';
      var flag = document.getElementById('clear_' + field);
      if (flag) flag.value = '1';
    }}
    // 按所选型号重建「完成工序」下拉选项(选项来自该型号 CT 工序流)。
    // 显示中文工序名(s.name),value 用 SPECNAME(s.spec)。
    function _escOpt(t){{ return String(t==null?'':t).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }}
    function rebuildFinalProc() {{
      var m = document.getElementById('product_main').value;
      var sel = document.getElementById('final_process');
      if (!sel) return;
      var keep = sel.value || FINAL_PROC_CUR || '';
      var specs = CT_SPECS[m] || [];
      var html = '<option value="">做完整条工序流(默认)</option>';
      for (var i=0;i<specs.length;i++) {{
        var s = specs[i];
        var on = (s.spec===keep) ? ' selected' : '';
        html += '<option value="'+_escOpt(s.spec)+'"'+on+'>'+_escOpt(s.spec+' '+s.name)+'</option>';
      }}
      sel.innerHTML = html;
    }}
    // 按所选型号重建 ENG 验证时间工序行
    var ENG_TIMES_CUR = {{}};  // {{spec: {{eng_h, note}}}} 当前已填的 ENG 时间
    function rebuildEngRows() {{
      var m = document.getElementById('product_main').value;
      var tbody = document.getElementById('eng_tbody');
      if (!tbody) return;
      var specs = CT_SPECS[m] || [];
      if (!specs.length) {{ tbody.innerHTML='<tr><td colspan="3" style="color:#94a3b8;padding:8px 6px;">该型号无工序数据(请先上传 CT 表)</td></tr>'; return; }}
      var html = '';
      for (var i=0;i<specs.length;i++) {{
        var s=specs[i], sp=s.spec, nm=s.name||sp;
        var cur=ENG_TIMES_CUR[sp]||{{}};
        var eh=cur.eng_h!=null ? cur.eng_h : '';
        var nt=cur.note||'';
        html += '<tr>'
          +'<td style="padding:3px 6px;color:#334155;">'+_escOpt(nm)+'</td>'
          +'<td style="padding:3px 6px;"><input name="eng_h_'+_escOpt(sp)+'" value="'+_escOpt(String(eh))+'" type="number" min="0" step="0.5" placeholder="留空=标准CT" style="width:90%;padding:3px 5px;border:1px solid #cbd5e1;border-radius:4px;font-size:12px;"></td>'
          +'<td style="padding:3px 6px;"><input name="eng_note_'+_escOpt(sp)+'" value="'+_escOpt(nt)+'" placeholder="备注" style="width:95%;padding:3px 5px;border:1px solid #cbd5e1;border-radius:4px;font-size:12px;"></td>'
          +'</tr>';
      }}
      tbody.innerHTML = html;
    }}
    function checkWo() {{
      if (WO_IS_EDIT) return;   // 编辑现有工单时不查重
      var inp = document.getElementById('wo_no_input');
      var hint = document.getElementById('wo_hint');
      var raw = (inp.value || '').trim();
      if (!raw) {{ hint.textContent=''; return; }}
      clearTimeout(_woTimer);
      hint.style.color = '#94a3b8'; hint.textContent = '校验中…';
      _woTimer = setTimeout(function() {{
        var wo = raw[0]==='*' ? raw : ('*'+raw.replace(/^\\*+/,''));
        fetch('/admin/check_wo?wo='+encodeURIComponent(wo))
          .then(function(r){{ return r.json(); }})
          .then(function(d){{
            if (d.exists) {{ hint.style.color='#dc2626'; hint.textContent='✗ 该工单已添加,请勿重复创建'; }}
            else {{ hint.style.color='#16a34a'; hint.textContent='✓ 可用'; }}
          }})
          .catch(function(){{ hint.textContent=''; }});
      }}, 350);
    }}
    function onProdChange() {{
      var m = document.getElementById('product_main').value;
      document.getElementById('sub_row').style.display = (m==='MB12') ? 'flex' : 'none';
      FINAL_PROC_CUR = '';   // 切换型号后旧工序不再适用,清空待选基准
      rebuildFinalProc();
      ENG_TIMES_CUR = {{}};   // 切换型号后清空旧的 ENG 时间
      rebuildEngRows();
    }}
    function lookupMes() {{
      var wo = document.getElementById('wo_no_input').value.trim();
      var box = document.getElementById('mes_result');
      if (!wo) {{ box.style.display='block'; box.innerHTML='请先填写工单号'; return; }}
      box.style.display='block'; box.innerHTML='查询中…';
      fetch('/admin/lookup_mes?wo=' + encodeURIComponent(wo))
        .then(function(r){{ return r.json(); }})
        .then(function(d){{
          if (!d.variants || d.variants.length===0) {{
            box.innerHTML='<span style="color:#dc2626;">MES 中未找到该工单(关键段无匹配)</span>';
            return;
          }}
          var f = document.forms[0];
          if (f.priority && d.priority) {{
            for (var i=0;i<f.priority.options.length;i++)
              if (f.priority.options[i].value===d.priority) f.priority.selectedIndex=i;
          }}
          // 自动识别型号:MES 返回 PACKAGESIZE → 基础型号(去 /变体后缀,如 MB12/4in1→MB12),
          // 匹配产品型号下拉并选中(选中后触发联动),识别后可再手动调整。
          var reco = String(d.product || '').split('/')[0].trim();
          var pm = document.getElementById('product_main');
          var recoHit = false;
          if (reco && pm) {{
            var up = reco.toUpperCase();
            for (var i = 0; i < pm.options.length; i++) {{
              if (String(pm.options[i].value).toUpperCase() === up) {{
                pm.selectedIndex = i; recoHit = true; break;
              }}
            }}
            if (recoHit && typeof onProdChange === 'function') onProdChange();
          }}
          var cur = d.cur_process || '';
          var html = (recoHit
              ? '<span style="color:#16a34a;">✓ 已识别型号 <b>'+reco+'</b> 并自动选中(可手动调整)</span><br>'
              : '') + '<b>匹配关键段 '+d.key+'</b> · 当前工序(最快):<b>'+cur+'</b> ['+(d.cur_status||'')+']<br>';
          html += '<table style="width:100%;margin-top:6px;font-size:11px;border-collapse:collapse;">';
          html += '<tr style="color:#64748b;"><td>工单号</td><td>首位</td><td>当前工序</td><td>状态</td><td>在制工序数</td></tr>';
          d.variants.forEach(function(v){{
            html += '<tr><td>'+v.full_wo+'</td><td>'+v.first+'</td><td>seq '+v.cur_seq+' '+v.cur_spec+'</td><td>'+v.cur_status+'</td><td>'+v.processes.length+'</td></tr>';
          }});
          html += '</table>';
          box.innerHTML = html;
        }})
        .catch(function(){{ box.innerHTML='<span style="color:#dc2626;">查询出错</span>'; }});
    }}
    // 编辑模式时回填 ENG 时间 + 构建工序行
    (function(){{
      var _init=null;
      try{{ _init = JSON.parse(_initEngTimes||'{{}}'); }}catch(_xe){{}}
      if(_init && typeof ENG_TIMES_CUR!=='undefined'){{
        ENG_TIMES_CUR = _init;
        if(typeof rebuildEngRows==='function') rebuildEngRows();
      }}
    }})();
    </script>"""


def _sel(options, current):
    return "".join(
        f'<option value="{_html.escape(o)}"{" selected" if o==current else ""}>{_html.escape(o)}</option>'
        for o in options)


def render_select(categories, names, current="", msg="", stats=None):
    """类别选择页(统一入口):登录前/后均可访问,选「塑封/灌封」进对应项目。
    匿名也能选要看的看板;选择后写 cookie,后续请求据此连对应业务库。
    背景沿用后台登录页的「神经网络」动态画布(由 MES 聚合数字驱动)。"""
    s = stats or {}
    g_total = int(s.get("total", 0) or 0)
    g_high = int(s.get("high", 0) or 0)
    g_running = int(s.get("running", 0) or 0)
    g_avg = float(s.get("avg_progress", 0) or 0)
    desc = {"molding": "Molding · 塑料封装产品线", "potting": "Potting · 灌封封装产品线"}
    # 线条 SVG 图标(青色描边,与神经网络背景同色系)。塑封=芯片封装,灌封=灌胶模块+胶滴
    svg = {
        "molding": ('<svg viewBox="0 0 64 64" width="56" height="56" fill="none" '
                    'stroke="#7dd3fc" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                    '<rect x="19" y="19" width="26" height="26" rx="3" fill="rgba(56,189,248,.10)"/>'
                    '<rect x="27" y="27" width="10" height="10" rx="2" fill="rgba(56,189,248,.28)"/>'
                    '<path d="M25 19v-7M32 19v-7M39 19v-7M25 45v7M32 45v7M39 45v7'
                    'M19 25h-7M19 32h-7M19 39h-7M45 25h7M45 32h7M45 39h7"/></svg>'),
        "potting": ('<svg viewBox="0 0 64 64" width="56" height="56" fill="none" '
                    'stroke="#7dd3fc" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                    '<path d="M32 9c4 6 7 9.5 7 13.5a7 7 0 0 1-14 0C25 18.5 28 15 32 9z" '
                    'fill="rgba(56,189,248,.22)"/>'
                    '<rect x="14" y="34" width="36" height="20" rx="3" fill="rgba(56,189,248,.08)"/>'
                    '<path d="M14 42c6 0 6 4 12 4s6-4 12-4 6 4 12 4"/></svg>'),
        "analysis": ('<svg viewBox="0 0 64 64" width="56" height="56" fill="none" '
                     'stroke="#fbbf24" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                     '<path d="M12 52h40" />'
                     '<rect x="16" y="34" width="7" height="14" rx="1.5" fill="rgba(251,191,36,.20)"/>'
                     '<rect x="28.5" y="24" width="7" height="24" rx="1.5" fill="rgba(251,191,36,.20)"/>'
                     '<rect x="41" y="14" width="7" height="34" rx="1.5" fill="rgba(251,191,36,.20)"/>'
                     '<circle cx="52" cy="10" r="5" fill="rgba(251,191,36,.12)"/>'
                     '<path d="M55.5 13.5 60 18"/></svg>'),
    }
    cards = ""
    for cat in categories:
        nm = names.get(cat, cat)
        on = " sel" if cat == current else ""
        cards += f"""
        <a class="catcard{on}" href="/select/set?cat={cat}">
          <div class="ico">{svg.get(cat,'')}</div>
          <div class="nm">{_html.escape(nm)}</div>
          <div class="ds">{_html.escape(desc.get(cat,''))}</div>
          {'<div class="cur">当前所在</div>' if cat==current else '<div class="go">进入 →</div>'}
        </a>"""
    cards += f"""
        <a class="catcard analysis" href="/wip_analysis">
          <div class="ico">{svg.get('analysis','')}</div>
          <div class="nm">WIP 超期分析</div>
          <div class="ds">Cross-series · 全部在制工单</div>
          <div class="go">进入 →</div>
        </a>"""
    msg_html = (f'<div class="flash">{_html.escape(msg)}</div>' if msg else "")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>选择系统 · NTO 生产进度</title>
<style>
* {{ box-sizing:border-box;margin:0;padding:0; }}
html,body {{ height:100%; }}
body {{ font-family:-apple-system,"Microsoft YaHei",sans-serif; background:#05070f;
  color:#e2e8f0; display:flex; flex-direction:column; align-items:center; justify-content:center;
  overflow:hidden; padding:24px; }}
#net {{ position:fixed; inset:0; z-index:0; display:block; }}
.veil {{ position:fixed; inset:0; z-index:1; pointer-events:none;
  background:radial-gradient(ellipse at center, rgba(5,7,15,.15) 0%, rgba(5,7,15,.55) 65%, rgba(5,7,15,.88) 100%); }}
h1 {{ position:relative; z-index:3; font-size:24px; font-weight:700; margin-bottom:6px;
  color:#f1f5f9; letter-spacing:1px; text-shadow:0 2px 18px rgba(0,0,0,.6); }}
.sub {{ position:relative; z-index:3; font-size:13px; color:#7dd3fc; margin-bottom:28px; opacity:.9; }}
.flash {{ position:relative; z-index:3; background:rgba(254,242,242,.92); color:#dc2626;
  padding:8px 14px; border-radius:8px; margin-bottom:16px; font-size:13px; }}
.cards {{ position:relative; z-index:3; display:flex; gap:22px; flex-wrap:wrap; justify-content:center; }}
.catcard {{ width:260px; padding:30px 24px; border-radius:16px; text-decoration:none; text-align:center;
  background:rgba(13,20,38,.55); border:1px solid rgba(56,189,248,.22); color:#e2e8f0;
  backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
  box-shadow:0 18px 50px rgba(0,0,0,.55), 0 0 0 1px rgba(56,189,248,.05) inset;
  transition:transform .15s, box-shadow .15s, border-color .15s; display:block; }}
.catcard:hover {{ transform:translateY(-6px); border-color:#38bdf8;
  box-shadow:0 22px 60px rgba(0,0,0,.6), 0 0 0 3px rgba(56,189,248,.15) inset; }}
.catcard.sel {{ border-color:#38bdf8; }}
.catcard.analysis {{ border-color:rgba(251,191,36,.30); }}
.catcard.analysis:hover {{ border-color:#fbbf24;
  box-shadow:0 22px 60px rgba(0,0,0,.6), 0 0 0 3px rgba(251,191,36,.14) inset; }}
.catcard .ico {{ height:60px; display:flex; align-items:center; justify-content:center; margin-bottom:14px; }}
.catcard .ico svg {{ filter:drop-shadow(0 0 9px rgba(56,189,248,.45)); transition:transform .15s; }}
.catcard:hover .ico svg {{ transform:scale(1.08); }}
.catcard .nm {{ font-size:19px; font-weight:700; color:#f1f5f9; }}
.catcard .ds {{ font-size:12px; color:#94a3b8; margin-top:6px; }}
.catcard .go {{ margin-top:16px; color:#7dd3fc; font-weight:600; font-size:14px; }}
.catcard .cur {{ margin-top:16px; color:#34d399; font-weight:600; font-size:13px; }}
.foot {{ position:relative; z-index:3; margin-top:30px; font-size:12px; color:#64748b; }}
.foot a {{ color:#7dd3fc; text-decoration:none; }}
</style></head><body>
<canvas id="net"></canvas>
<div class="veil"></div>
<h1>NTO 生产进度管理系统</h1>
<div class="sub">请选择要进入的产品线 / 分析模块</div>
{msg_html}
<div class="cards">{cards}</div>
<div class="foot">一套账号通用 · <a href="/login">后台登录</a></div>
<script>
/* ===== 神经网络动态背景(与后台登录页同款,由 MES 聚合数据驱动)===== */
(function(){{
  var cv=document.getElementById('net'), ctx=cv.getContext('2d');
  var DPR=Math.min(window.devicePixelRatio||1, 2);
  var MES={{ total:{g_total}, high:{g_high}, running:{g_running}, avg:{g_avg} }};
  var highRatio = MES.total>0 ? (MES.high/MES.total) : 0;
  var pulseRate = 0.0004 + highRatio*0.004;
  var linkR = Math.round(56 + highRatio*180);
  var linkG = Math.round(189 - highRatio*120);
  var linkB = Math.round(248 - highRatio*120);
  var speed = 0.30 + (MES.avg/100)*0.5;
  var W, H, nodes=[], pulses=[], LINK=150, mouse={{x:-9999,y:-9999}};
  function resize(){{
    W=window.innerWidth; H=window.innerHeight;
    cv.width=W*DPR; cv.height=H*DPR; cv.style.width=W+'px'; cv.style.height=H+'px';
    ctx.setTransform(DPR,0,0,DPR,0,0);
    var base=Math.round(W*H/16000);
    var density=base + MES.running*3 + Math.round(MES.total*0.6);
    density=Math.max(50, Math.min(200, density));
    nodes=[];
    for(var i=0;i<density;i++){{
      nodes.push({{ x:Math.random()*W, y:Math.random()*H,
        vx:(Math.random()-0.5)*speed, vy:(Math.random()-0.5)*speed, r:1.2+Math.random()*1.6 }});
    }}
  }}
  resize();
  window.addEventListener('resize', resize);
  window.addEventListener('mousemove', function(e){{ mouse.x=e.clientX; mouse.y=e.clientY; }});
  window.addEventListener('mouseout', function(){{ mouse.x=-9999; mouse.y=-9999; }});
  function spawnPulse(a,b){{ pulses.push({{a:a,b:b,t:0,sp:0.012+Math.random()*0.02}}); }}
  function step(){{
    ctx.clearRect(0,0,W,H);
    for(var i=0;i<nodes.length;i++){{
      var n=nodes[i]; n.x+=n.vx; n.y+=n.vy;
      if(n.x<0||n.x>W) n.vx*=-1; if(n.y<0||n.y>H) n.vy*=-1;
      var dx=n.x-mouse.x, dy=n.y-mouse.y, dm=Math.sqrt(dx*dx+dy*dy);
      if(dm<120 && dm>0){{ n.x+=dx/dm*1.2; n.y+=dy/dm*1.2; }}
    }}
    for(var i=0;i<nodes.length;i++){{
      for(var j=i+1;j<nodes.length;j++){{
        var a=nodes[i], b=nodes[j];
        var dx=a.x-b.x, dy=a.y-b.y, d=Math.sqrt(dx*dx+dy*dy);
        if(d<LINK){{
          var alpha=(1-d/LINK)*0.55;
          ctx.strokeStyle='rgba('+linkR+','+linkG+','+linkB+','+alpha+')';
          ctx.lineWidth=0.6;
          ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
          if(Math.random()<pulseRate && pulses.length<80) spawnPulse(a,b);
        }}
      }}
    }}
    ctx.globalCompositeOperation='lighter';
    for(var i=0;i<nodes.length;i++){{
      var n=nodes[i];
      var g=ctx.createRadialGradient(n.x,n.y,0,n.x,n.y,n.r*4);
      g.addColorStop(0,'rgba(125,211,252,.9)'); g.addColorStop(1,'rgba(125,211,252,0)');
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(n.x,n.y,n.r*4,0,Math.PI*2); ctx.fill();
      ctx.fillStyle='rgba(224,242,254,.95)';
      ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,Math.PI*2); ctx.fill();
    }}
    for(var k=pulses.length-1;k>=0;k--){{
      var p=pulses[k]; p.t+=p.sp;
      if(p.t>=1){{ pulses.splice(k,1); continue; }}
      var px=p.a.x+(p.b.x-p.a.x)*p.t, py=p.a.y+(p.b.y-p.a.y)*p.t;
      var pg=ctx.createRadialGradient(px,py,0,px,py,6);
      pg.addColorStop(0,'rgba(255,255,255,.95)'); pg.addColorStop(1,'rgba(56,189,248,0)');
      ctx.fillStyle=pg; ctx.beginPath(); ctx.arc(px,py,6,0,Math.PI*2); ctx.fill();
    }}
    ctx.globalCompositeOperation='source-over';
    requestAnimationFrame(step);
  }}
  requestAnimationFrame(step);
}})();
</script>
</body></html>"""


# ======================================================================
# WIP 超期分析页(顶层独立入口,跨塑封/灌封,公开浏览)
# ======================================================================

_WIP_ANALYSIS_HTML = r"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WIP 超期分析 · NTO 生产进度</title>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,"Microsoft YaHei",sans-serif; background:#f1f5f9; color:#1e293b; }
.top { background:#0f172a; color:#e2e8f0; padding:13px 26px; display:flex; align-items:center;
  gap:14px; flex-wrap:wrap; position:sticky; top:0; z-index:20; }
.top h1 { font-size:18px; color:#fff; font-weight:700; }
.top .sub { font-size:12px; color:#94a3b8; }
.top .sp { flex:1; }
.top a { color:#7dd3fc; text-decoration:none; font-size:13px; }
.top a:hover { text-decoration:underline; }
.wrap { padding:16px 18px 40px; width:100%; max-width:none; margin:0; }
.toolbar { background:#fff; border-radius:10px; padding:12px 16px; display:flex; gap:10px 14px;
  flex-wrap:wrap; align-items:center; box-shadow:0 1px 2px rgba(15,23,42,.04),0 4px 12px rgba(15,23,42,.05); }
.fld { display:flex; align-items:center; gap:6px; font-size:13px; color:#475569; }
.fld input,.fld select { padding:6px 9px; border:1px solid #cbd5e1; border-radius:6px;
  font-size:13px; background:#fff; color:#1e293b; }
.fld input:focus,.fld select:focus { outline:2px solid #bfdbfe; border-color:#2563eb; }
.fld input[type=number] { width:76px; }
#f_edges { width:150px; }
#f_kw { width:190px; }
.btn { padding:6px 13px; border:1px solid #cbd5e1; background:#fff; color:#334155;
  border-radius:6px; font-size:13px; cursor:pointer; font-weight:600; white-space:nowrap; }
.btn:hover { background:#f8fafc; border-color:#94a3b8; }
.btn.primary { background:#2563eb; border-color:#2563eb; color:#fff; }
.btn.primary:hover { background:#1d4ed8; }
.btn.amber { background:#fff; border-color:#f59e0b; color:#b45309; }
.btn.amber:hover { background:#fffbeb; }
.btn.guide { background:#e0f2fe; border-color:#7dd3fc; color:#0369a1;
  text-decoration:none; display:inline-flex; align-items:center; }
.btn.guide:hover { background:#bae6fd; border-color:#38bdf8; color:#075985; }
.btn:disabled { opacity:.45; cursor:not-allowed; }
.cards { display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin:16px 0; }
.card { background:#fff; border-radius:10px; padding:13px 17px;
  box-shadow:0 1px 2px rgba(15,23,42,.04),0 4px 12px rgba(15,23,42,.05); }
.card .k { font-size:12px; color:#64748b; }
.card .v { font-size:25px; font-weight:700; margin-top:3px; font-variant-numeric:tabular-nums; }
.card .s { font-size:11px; color:#94a3b8; margin-top:3px; }
.section-head { font-size:13px; font-weight:700; color:#334155; margin:6px 0 10px;
  padding-left:9px; border-left:3px solid #2563eb; letter-spacing:.5px; }
.chart-row { display:grid; gap:14px; margin-bottom:16px; }
.chart-row.row-3 { grid-template-columns:repeat(3,minmax(0,1fr)); }
.chart-row.row-2 { grid-template-columns:repeat(2,minmax(0,1fr)); }
.chart-row .panel { margin-bottom:0; display:flex; flex-direction:column; }
.chart-row .panel > div:not(.chart-foot) { flex:1; }
#series_panel { display:flex; flex-direction:column; justify-content:center; }
.chart-foot { font-size:11px; color:#94a3b8; margin-top:8px; text-align:center; }
.vchart { display:flex; align-items:flex-end; gap:10px; height:210px; padding:6px 2px 0; }
.vcol { flex:1; height:100%; display:flex; flex-direction:column; align-items:center;
  justify-content:flex-end; cursor:pointer; border-radius:8px; padding:4px 3px; }
.vcol:hover { background:#f8fafc; }
.vcol.selected { background:#eff6ff; box-shadow:inset 0 0 0 2px #bfdbfe; }
.vbar { width:72%; min-height:2px; border-radius:4px 4px 0 0; }
.vval { font-size:12px; font-weight:700; color:#334155; margin-bottom:4px; white-space:nowrap; }
.vlab { font-size:11px; color:#64748b; margin-top:6px; text-align:center; line-height:1.3; }
.panel { background:#fff; border-radius:10px; padding:15px 17px; margin-bottom:16px;
  box-shadow:0 1px 2px rgba(15,23,42,.04),0 4px 12px rgba(15,23,42,.05); }
.panel h3 { font-size:14px; margin-bottom:12px; color:#0f172a; display:flex;
  justify-content:space-between; align-items:baseline; gap:10px; }
.panel h3 .note { font-size:11px; color:#94a3b8; font-weight:400; }
.bar-row { display:grid; grid-template-columns:minmax(90px,190px) 1fr 78px; gap:9px;
  align-items:center; font-size:12px; margin:7px 0; }
.bar-lab { color:#334155; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar-track { background:#f1f5f9; border-radius:4px; height:16px; overflow:hidden; }
.bar-fill { height:100%; border-radius:4px; min-width:2px; transition:width .2s; }
.bar-val { text-align:right; color:#475569; font-variant-numeric:tabular-nums; }
.bar-row.click { cursor:pointer; }
.bar-row.click:hover .bar-lab { color:#2563eb; text-decoration:underline; }
.bar-row.selected { background:#eff6ff; border-radius:6px; }
.bar-row.selected .bar-lab { color:#1d4ed8; font-weight:700; }
.bar-row.selected .bar-lab::after { content:" ✓"; color:#2563eb; }
.bar-row.selected .bar-fill { box-shadow:inset 0 0 0 2px #1e3a8a; }
.seg { display:flex; height:22px; border-radius:6px; overflow:hidden; background:#f1f5f9; margin:4px 0 12px; }
.seg > div { height:100%; cursor:pointer; }
.seg > div.selected { box-shadow:inset 0 0 0 3px #fff, inset 0 0 0 5px #1e3a8a; }
.legend { display:flex; flex-direction:column; gap:4px; font-size:12.5px; color:#334155; }
.legend .row { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
  cursor:pointer; border-radius:6px; padding:4px 6px; margin:0 -6px; }
.legend .row:hover { background:#f8fafc; }
.legend .row.selected { background:#eff6ff; box-shadow:inset 0 0 0 1px #bfdbfe; }
.legend .row.selected .num { color:#1d4ed8; font-weight:700; }
.legend .row.selected .num::after { content:" ✓"; }
.dot { width:10px; height:10px; border-radius:50%; flex:0 0 auto; }
.legend .num { margin-left:auto; color:#64748b; font-variant-numeric:tabular-nums; }
.chips { display:none; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; }
.chips.on { display:flex; }
.chip { display:inline-flex; align-items:center; gap:6px; background:#eff6ff; color:#1d4ed8;
  border:1px solid #bfdbfe; border-radius:999px; padding:4px 11px; font-size:12.5px; cursor:pointer; }
.chip:hover { background:#dbeafe; }
.chip b { font-weight:700; }
.chip .x { color:#64748b; font-weight:700; }
.chip.clear { background:#fff; color:#64748b; border-color:#cbd5e1; }
.chip.clear:hover { background:#f8fafc; }
.export-toast { display:none; position:fixed; right:max(18px, env(safe-area-inset-right, 18px));
  bottom:max(18px, env(safe-area-inset-bottom, 18px)); z-index:90;
  width:min(340px, calc(100vw - 32px)); background:#0f172a; color:#e2e8f0;
  border:1px solid rgba(56,189,248,.35); border-radius:10px; padding:12px 14px;
  box-shadow:0 14px 40px rgba(2,6,23,.45);
  grid-template-columns:22px minmax(0,1fr) 18px; gap:10px; align-items:start; }
.export-toast.on { display:grid; }
.export-toast .et-spin { width:18px; height:18px; margin-top:2px; border-radius:50%;
  border:2px solid rgba(125,211,252,.25); border-top-color:#7dd3fc;
  animation:etspin .8s linear infinite; }
.export-toast.done .et-spin { border-color:#34d399; border-top-color:#34d399; animation:none; }
.export-toast.failed .et-spin { border-color:#f87171; border-top-color:#f87171; animation:none; }
.export-toast .et-title { font-size:13px; font-weight:700; color:#f1f5f9; }
.export-toast .et-msg { font-size:12px; color:#94a3b8; margin-top:2px; line-height:1.5; }
.export-toast .et-close { background:none; border:none; color:#94a3b8; cursor:pointer;
  font-size:16px; line-height:1; padding:0; }
.export-toast .et-close:hover { color:#e2e8f0; }
@keyframes etspin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .export-toast .et-spin { animation:none; } }
.empty { color:#94a3b8; font-size:13px; padding:18px 0; text-align:center; }
.tablebox { overflow-x:hidden; overflow-y:auto; max-height:600px; border:1px solid #e2e8f0; border-radius:8px; }
table { border-collapse:collapse; width:100%; table-layout:fixed; font-size:12px; }
thead th { position:sticky; top:0; background:#eef2f7; z-index:2; text-align:center;
  padding:7px 6px; white-space:normal; word-break:break-word; color:#475569;
  font-weight:600; border-bottom:1px solid #e2e8f0; }
tbody td { padding:6px 6px; border-bottom:1px solid #f1f5f9; white-space:normal;
  word-break:break-word; overflow-wrap:anywhere; vertical-align:middle; text-align:center; }
tbody tr:nth-child(even) td { background:#fafbfd; }
tbody tr:hover td { background:#f0f6ff; }
.mono { font-family:Consolas,"Courier New",monospace; word-break:break-all; }
.tag { display:inline-block; padding:1px 7px; border-radius:4px; font-size:11px; }
.days { font-weight:700; color:#dc2626; font-variant-numeric:tabular-nums; }
.pager { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:12px; font-size:12.5px; color:#475569; }
.pager .sp { flex:1; }
.pager input { width:64px; padding:4px 6px; border:1px solid #cbd5e1; border-radius:5px; font-size:12px; }
.warn { display:none; background:#fff7ed; color:#c2410c; border:1px solid #fed7aa;
  border-radius:8px; padding:9px 13px; font-size:12.5px; margin:12px 0 0; }
#msg { display:none; background:#fef2f2; color:#dc2626; border:1px solid #fecaca;
  border-radius:8px; padding:9px 13px; font-size:12.5px; margin:12px 0 0; }
.modal-mask { display:none; position:fixed; inset:0; background:rgba(15,23,42,.45); z-index:60; }
.modal { display:none; position:fixed; top:50%; left:50%; transform:translate(-50%,-50%);
  width:min(980px,94vw); max-height:88vh; background:#fff; border-radius:12px; z-index:61;
  box-shadow:0 24px 70px rgba(0,0,0,.35); flex-direction:column; overflow:hidden; }
.modal .mh { padding:15px 20px 11px; border-bottom:1px solid #e2e8f0; display:flex;
  align-items:center; gap:12px; flex-wrap:wrap; }
.modal .mh h3 { font-size:15px; margin:0; }
.modal .mb { padding:12px 20px; overflow:auto; flex:1; }
.modal .mf { padding:11px 20px; border-top:1px solid #e2e8f0; display:flex;
  gap:10px; align-items:center; flex-wrap:wrap; }
.modal table { font-size:12.5px; }
.modal select { padding:4px 8px; border:1px solid #cbd5e1; border-radius:5px; font-size:12.5px; }
.owner-box { background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:11px 13px; }
.hint { font-size:12px; color:#64748b; line-height:1.65; }
.owner-box input[type=file] { font-size:12px; max-width:260px; }
#ownerMsg.ok { color:#15803d; }
#ownerMsg.err { color:#dc2626; }
@media (max-width:1300px) {
  .chart-row.row-3 { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media (max-width:1100px) {
  .cards { grid-template-columns:repeat(2,1fr); }
}
@media (max-width:760px) {
  .chart-row.row-3, .chart-row.row-2 { grid-template-columns:1fr; }
}
@media (max-width:640px) {
  .wrap { padding:12px 12px 36px; }
  .top { padding:11px 13px; }
  .cards { grid-template-columns:1fr 1fr; gap:10px; }
  .card .v { font-size:21px; }
  .bar-row { grid-template-columns:minmax(70px,120px) 1fr 62px; }
  #f_kw, #f_edges { width:130px; }
}
</style></head><body>
<div class="top">
  <h1>WIP 超期分析</h1>
  <span class="sub">全部在制工单 · LOT 粒度 · 跨塑封/灌封 · 点击图表可联动筛选</span>
  <span class="sp"></span>
  <span id="snap" class="sub"></span>
  <a href="/select">← 返回选择</a>
  <a href="/login?next=/wip_analysis" id="admin_link">后台登录</a>
</div>
<div class="wrap">
  <div class="toolbar">
    <label class="fld">超期阈值 <input id="f_days" type="number" min="1" max="3650" value="60"> 天</label>
    <label class="fld" title="相对阈值的档位边界，逗号分隔。默认 15,30,60,120 → 61-75 / 76-90 / 91-120 / >120 天">分档边界
      <input id="f_edges" value="15,30,60,120" title="相对阈值的天数边界，逗号分隔"></label>
    <label class="fld">系列
      <select id="f_series">
        <option value="">全部</option>
        <option value="molding">塑封</option>
        <option value="potting">灌封</option>
        <option value="unknown">未分类</option>
      </select></label>
    <label class="fld">顶层型号 <select id="f_family"><option value="">全部</option></select></label>
    <label class="fld">细分项 <select id="f_product"><option value="">全部</option></select></label>
    <label class="fld">搜索 <input id="f_kw" placeholder="工单号 / LOT号"></label>
    <button class="btn primary" id="btn_query" onclick="applyInputs()">查询</button>
    <button class="btn" onclick="resetAll()">重置</button>
    <span style="flex:1"></span>
    <a class="btn guide" href="/wip_guide" target="_blank" rel="noopener">使用指南</a>
    <button class="btn" onclick="exportFile('csv')">导出 CSV</button>
    <button class="btn" onclick="exportFile('xlsx')">导出 Excel</button>
    <button class="btn amber" id="btn_map" onclick="openMap()" style="display:none;">归属与 Owner 维护</button>
  </div>
  <div id="msg"></div>
  <div class="warn" id="warn"></div>
  <div class="chips" id="chips"></div>

  <div class="cards">
    <div class="card"><div class="k">超期 LOT 数</div><div class="v" id="c_lots">—</div><div class="s">超过阈值的在制批次</div></div>
    <div class="card"><div class="k">涉及工单数</div><div class="v" id="c_orders">—</div><div class="s">按 MFGORDERNAME 去重</div></div>
    <div class="card"><div class="k">超期数量</div><div class="v" id="c_qty">—</div><div class="s">Ea（QTY 合计）</div></div>
    <div class="card"><div class="k">平均超期</div><div class="v" id="c_avg">—</div><div class="s">天</div></div>
    <div class="card"><div class="k">最长超期</div><div class="v" id="c_max">—</div><div class="s">天</div></div>
  </div>

  <div class="section-head">时间与工序</div>
  <div class="chart-row row-3">
    <div class="panel"><h3>按系列分布 <span class="note">点击筛选 · 再点取消</span></h3>
      <div id="series_panel"><div class="empty">加载中…</div></div></div>
    <div class="panel"><h3>按超期天数分档 <span class="note" id="bucket_note"></span></h3>
      <div id="bucket_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="bucket_foot"></div></div>
    <div class="panel"><h3>按当前工序 TOP <span class="note">显示前 8 · 点击筛选</span></h3>
      <div id="process_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="process_foot"></div></div>
  </div>

  <div class="section-head">产品型号</div>
  <div class="chart-row row-2">
    <div class="panel"><h3>按顶层型号 TOP <span class="note">MPC TECHNOLOGY · 显示前 10 · 点击筛选</span></h3>
      <div id="family_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="family_foot"></div></div>
    <div class="panel"><h3>按细分项 TOP <span class="note">PACKAGESIZE · 显示前 10 · 点击筛选</span></h3>
      <div id="product_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="product_foot"></div></div>
  </div>

  <div class="section-head">负责人</div>
  <div class="chart-row row-3">
    <div class="panel"><h3>Assy PC Owner TOP <span class="note">显示前 8 · 点击筛选</span></h3>
      <div id="assy_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="assy_foot"></div></div>
    <div class="panel"><h3>MPC Owner TOP <span class="note">显示前 8 · 点击筛选</span></h3>
      <div id="mpc_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="mpc_foot"></div></div>
    <div class="panel"><h3>Inline/ENG Owner TOP <span class="note">HOLD 取 ENG · 显示前 8 · 点击筛选</span></h3>
      <div id="inline_panel"><div class="empty">加载中…</div></div>
      <div class="chart-foot" id="inline_foot"></div></div>
  </div>

  <div class="panel">
    <h3>LOT 明细 <span class="note" id="tbl_note"></span></h3>
    <div class="tablebox">
      <table>
        <colgroup>
          <col style="width:6.5%"><col style="width:7.5%"><col style="width:6%"><col style="width:5.5%">
          <col style="width:7%"><col style="width:6%"><col style="width:4%"><col style="width:6.5%">
          <col style="width:12%"><col style="width:5%"><col style="width:4%"><col style="width:4%">
          <col style="width:4%"><col style="width:5%"><col style="width:5.5%"><col style="width:5%">
          <col style="width:6.5%">
        </colgroup>
        <thead><tr>
          <th>工单号</th><th>LOT号</th><th>顶层型号</th><th>细分项</th><th>产品代码</th><th>建单日</th>
          <th>超期天数</th><th>当前工序</th><th>工序描述</th><th>状态</th><th>数量</th>
          <th>系列</th><th>优先级</th><th>MES Owner</th>
          <th>Assy PC Owner</th><th>MPC Owner</th><th>Inline/ENG Owner</th>
        </tr></thead>
        <tbody id="tbody"><tr><td colspan="17" class="empty">加载中…</td></tr></tbody>
      </table>
    </div>
    <div class="pager" id="pager"></div>
    <div style="font-size:12px;color:#64748b;margin-top:8px;">
      Inline/ENG Owner：MES 状态含 HOLD 时取 ENG Owner，其余取 Inline Owner；负责人来自「归属与 Owner 维护」中导入的 Owner 表。
    </div>
  </div>
</div>

<!-- 系列归属维护(管理员) -->
<div class="modal-mask" id="mapMask" onclick="closeMap()"></div>
<div class="modal" id="mapBox">
  <div class="mh">
    <h3>归属与 Owner 维护</h3>
    <span class="note" style="font-size:12px;color:#94a3b8;" id="mapInfo"></span>
    <span style="flex:1"></span>
    <input id="mapSearch" placeholder="搜索型号" oninput="renderMap()" style="padding:5px 9px;border:1px solid #cbd5e1;border-radius:6px;font-size:12.5px;width:180px;">
    <button class="btn" onclick="closeMap()">关闭</button>
  </div>
  <div class="mb">
    <div class="owner-box">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <h4 style="margin:0;font-size:13px;">Owner 表导入</h4>
        <span id="ownerInfo" class="hint">尚未导入</span>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:9px;">
        <input type="file" id="ownerFile" accept=".xlsx,.csv">
        <button class="btn primary" id="ownerBtn" onclick="importOwners()">导入 Owner 表</button>
        <span id="ownerMsg" class="hint"></span>
      </div>
      <div class="hint" style="margin-top:7px;">
        支持一个 .xlsx 内含 Assy PC Owner / MPC Owner / Inline Owner 三个 sheet，也可分三次导入 .csv 或单 sheet Excel；
        同一类型再次导入会覆盖该类型旧数据。所有 Owner 都以细分项 PACKAGESIZE 为锚点：
        Assy 按 PACKAGESIZE（Product 只作参考），MPC 按 PACKAGESIZE 且其封装类型自动补系列归属，
        Inline 通过 MPC 的 TECHNOLOGY 关联到细分项；HOLD 的 Lot 取 ENG Owner。
      </div>
    </div>
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:13px 0;">
    <div style="font-size:12px;color:#64748b;margin-bottom:10px;">
      系列归属用于「按系列」统计。初始值取两个类别库后台的产品白名单与 Owner 表封装类型；白名单冲突或不在任何来源的型号显示「未分类」，可在此裁决。手工选择塑封/灌封优先；选择「未分类」不会覆盖 Owner 表封装类型。保存后以本页配置为准。<br>
      <b>显示</b>：默认全部勾选。取消勾选后，该型号不出现在 WIP 超期分析的卡片、图表、明细和导出中；维护列表仍保留，可随时重新勾选恢复。
    </div>
    <div class="tablebox" style="max-height:none;">
      <table>
        <colgroup>
          <col style="width:9%"><col style="width:39%"><col style="width:14%">
          <col style="width:18%"><col style="width:20%">
        </colgroup>
        <thead><tr>
          <th><input type="checkbox" id="mapCheckAll" onchange="toggleAllHidden(this.checked)" title="全选 / 全不选"> 显示</th>
          <th>产品型号（PACKAGESIZE）</th><th>在制 LOT 数</th><th>系列归属</th><th>状态</th>
        </tr></thead>
        <tbody id="mapBody"></tbody></table>
    </div>
  </div>
  <div class="mf">
    <span style="font-size:12px;color:#64748b;">仅管理员登录后可保存</span>
    <span style="flex:1"></span>
    <button class="btn" onclick="closeMap()">取消</button>
    <button class="btn primary" id="mapSave" onclick="saveMap()">保存归属与显示</button>
  </div>
</div>

<div class="export-toast" id="exportToast" role="status" aria-live="polite">
  <div class="et-spin"></div>
  <div><div class="et-title" id="etTitle">导出排队中</div><div class="et-msg" id="etMsg"></div></div>
  <button type="button" class="et-close" onclick="hideExportToast()" title="收起提示">×</button>
</div>

<script>
var CAN_EDIT = __CAN_EDIT__;
var S = { days:60, edges:'15,30,60,120', series:'', bucket:null,
          family:'', product:'', spec:'', aowner:'', mowner:'', iowner:'',
          kw:'', page:1 };
var DATA = null, MAP_ROWS = [], MAP_CUR = {}, HIDDEN_CUR = {}, loadTimer = null;
var exportPollTimer = null, exportTicket = null, exportPollStart = 0;
var MAP_REV = 0, OWNER_REV = 0;
var SERIES_LABEL = { molding:'塑封', potting:'灌封', unknown:'未分类' };
var SERIES_COLOR = { molding:'#2563eb', potting:'#0d9488', unknown:'#94a3b8' };
var OWNER_LABEL = { assy:'Assy PC Owner', mpc:'MPC Owner', inline:'Inline Owner' };

function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function fmt(n){ return (Number(n)||0).toLocaleString('zh-CN'); }
function $(id){ return document.getElementById(id); }
function qstr(extra){
  var p = new URLSearchParams();
  p.set('days', S.days);
  if(S.edges) p.set('edges', S.edges);
  if(S.series) p.set('series', S.series);
  if(S.bucket !== null && S.bucket !== undefined) p.set('bucket', S.bucket);
  if(S.family) p.set('family', S.family);
  if(S.product) p.set('product', S.product);
  if(S.spec) p.set('spec', S.spec);
  if(S.aowner) p.set('aowner', S.aowner);
  if(S.mowner) p.set('mowner', S.mowner);
  if(S.iowner) p.set('iowner', S.iowner);
  if(S.kw) p.set('kw', S.kw);
  if(extra) Object.keys(extra).forEach(function(k){ p.set(k, extra[k]); });
  return p.toString();
}
function syncURL(){
  var p = new URLSearchParams(qstr({}));
  history.replaceState(null, '', '/wip_analysis?' + p.toString());
}
function showMsg(t, isErr){
  var el = $('msg');
  if(!t){ el.style.display='none'; el.textContent=''; return; }
  el.textContent = t;
  el.style.display = 'block';
  el.style.background = isErr===false ? '#f0fdf4' : '#fef2f2';
  el.style.color = isErr===false ? '#15803d' : '#dc2626';
  el.style.borderColor = isErr===false ? '#bbf7d0' : '#fecaca';
}
function readInputs(){
  var d = parseInt($('f_days').value, 10);
  if(!(d >= 1)) d = 60;
  if(d > 3650) d = 3650;
  $('f_days').value = d;
  var es = ($('f_edges').value || '').split(/[,，\s]+/).map(function(x){
    return parseInt(x, 10);
  }).filter(function(x){ return x > 0 && x <= 3650; });
  es = Array.from(new Set(es)).sort(function(a,b){ return a-b; }).slice(0, 8);
  $('f_edges').value = es.join(',');
  S.days = d;
  S.edges = es.join(',');
  S.series = $('f_series').value;
  S.family = $('f_family').value;
  S.product = $('f_product').value;
  S.kw = ($('f_kw').value || '').trim();
}
function applyInputs(){
  var oldDays = S.days, oldEdges = S.edges;
  var oldSeries = S.series, oldFamily = S.family;
  readInputs();
  // 阈值/分档边界变了,原来选中的档位编号已没有意义 → 清掉档位筛选
  if(S.days !== oldDays || S.edges !== oldEdges) S.bucket = null;
  // 系列是硬隔离:切到某个具体系列时清掉不兼容的顶层型号/细分项/工序/Owner;
  // 切回「全部」时保留这些条件(它们仍然有效)。
  if(S.series && S.series !== oldSeries){
    S.family = ''; S.product = ''; S.spec = '';
    S.aowner = ''; S.mowner = ''; S.iowner = '';
    $('f_family').value = ''; $('f_product').value = '';
  } else if(S.family !== oldFamily){
    // 顶层型号变了,细分项必须重选
    S.product = ''; $('f_product').value = '';
  }
  S.page = 1;
  syncURL();
  load();
}
function resetAll(){
  S = { days:60, edges:'15,30,60,120', series:'', bucket:null,
        family:'', product:'', spec:'', aowner:'', mowner:'', iowner:'',
        kw:'', page:1 };
  $('f_days').value = 60;
  $('f_edges').value = '15,30,60,120';
  $('f_series').value = '';
  $('f_family').value = '';
  $('f_product').value = '';
  $('f_kw').value = '';
  syncURL();
  load();
}

function load(){
  showMsg('');
  var url = '/wip_analysis/data?' + qstr({ page:S.page });
  fetch(url).then(function(r){ return r.json(); }).then(function(d){
    if(!d || !d.ok){ throw new Error((d && d.msg) || '加载失败'); }
    DATA = d;
    render(d);
  }).catch(function(e){ showMsg('加载失败：' + e.message); });
}

function render(d){
  $('c_lots').textContent = fmt(d.summary.lots);
  $('c_orders').textContent = fmt(d.summary.orders);
  $('c_qty').textContent = fmt(d.summary.qty);
  $('c_avg').textContent = d.summary.avg_days;
  $('c_max').textContent = fmt(d.summary.max_days);
  $('snap').textContent = d.csv_mtime ? ('快照 ' + d.csv_mtime) : '';
  if(d.csv_exists){
    $('warn').style.display = 'none';
  }else{
    $('warn').style.display = 'block';
    $('warn').textContent = '未找到 MES数据库.csv 或文件为空，当前无法统计。请检查数据源刷新任务。';
  }
  $('bucket_note').textContent = '阈值 ' + d.threshold + ' 天以上 · 点击筛选 · 再点取消';
  renderChips(d);
  renderSeries(d);
  renderBuckets(d);
  renderFamilies(d);
  renderProducts(d);
  renderProcesses(d);
  renderAssyOwners(d);
  renderMpcOwners(d);
  renderInlineOwners(d);
  renderFamilySelect(d);
  renderProductsSelect(d);
  renderTable(d);
}

/* ===== 图表点击联动:多条件叠加,再点一次取消 ===== */
function toggleSeries(k){
  var old = S.series;
  S.series = (S.series === k) ? '' : k;
  $('f_series').value = S.series;
  // 切到具体系列时清掉不兼容的顶层/细分/工序/Owner;取消系列时保留
  if(S.series && S.series !== old){
    S.family = ''; S.product = ''; S.spec = '';
    S.aowner = ''; S.mowner = ''; S.iowner = '';
    $('f_family').value = ''; $('f_product').value = '';
  }
  S.page = 1; syncURL(); load();
}
function toggleBucket(i){
  S.bucket = (S.bucket === i) ? null : i;
  S.page = 1; syncURL(); load();
}
function toggleFamily(k){
  S.family = (S.family === k) ? '' : k;
  $('f_family').value = S.family;
  // 顶层型号变了,细分项必须重选
  S.product = ''; $('f_product').value = '';
  S.page = 1; syncURL(); load();
}
function toggleProduct(p){
  var v = p || '';
  var same = S.product && v && S.product.toUpperCase() === v.toUpperCase();
  S.product = same ? '' : v;
  $('f_product').value = S.product;
  S.page = 1; syncURL(); load();
}
function toggleSpec(sp){
  S.spec = (S.spec === sp) ? '' : sp;
  S.page = 1; syncURL(); load();
}
function toggleAowner(v){ S.aowner = (S.aowner === v) ? '' : v; S.page=1; syncURL(); load(); }
function toggleMowner(v){ S.mowner = (S.mowner === v) ? '' : v; S.page=1; syncURL(); load(); }
function toggleIowner(v){ S.iowner = (S.iowner === v) ? '' : v; S.page=1; syncURL(); load(); }
function clearSeries(){ S.series=''; $('f_series').value=''; S.page=1; syncURL(); load(); }
function clearBucket(){ S.bucket=null; S.page=1; syncURL(); load(); }
function clearFamily(){ S.family=''; $('f_family').value=''; S.page=1; syncURL(); load(); }
function clearProduct(){ S.product=''; $('f_product').value=''; S.page=1; syncURL(); load(); }
function clearSpec(){ S.spec=''; S.page=1; syncURL(); load(); }
function clearAowner(){ S.aowner=''; S.page=1; syncURL(); load(); }
function clearMowner(){ S.mowner=''; S.page=1; syncURL(); load(); }
function clearIowner(){ S.iowner=''; S.page=1; syncURL(); load(); }
function clearAllFilters(){
  S.series=''; S.bucket=null; S.family=''; S.product=''; S.spec='';
  S.aowner=''; S.mowner=''; S.iowner=''; S.kw='';
  $('f_series').value=''; $('f_family').value=''; $('f_product').value='';
  $('f_kw').value='';
  S.page=1; syncURL(); load();
}
function renderChips(d){
  var f = d.filters || {};
  var items = [];
  if(f.series) items.push(['系列', SERIES_LABEL[f.series] || f.series, 'clearSeries()']);
  if(f.bucket !== null && f.bucket !== undefined)
    items.push(['超期档', f.bucket_label || ('第 ' + (f.bucket + 1) + ' 档'), 'clearBucket()']);
  if(f.family) items.push(['顶层型号', f.family_label || f.family, 'clearFamily()']);
  if(f.product) items.push(['细分项', f.product, 'clearProduct()']);
  if(f.spec) items.push(['当前工序', f.spec_desc ? (f.spec + ' · ' + f.spec_desc) : f.spec, 'clearSpec()']);
  if(f.aowner) items.push(['Assy PC Owner', f.aowner_label || f.aowner, 'clearAowner()']);
  if(f.mowner) items.push(['MPC Owner', f.mowner_label || f.mowner, 'clearMowner()']);
  if(f.iowner) items.push(['Inline/ENG Owner', f.iowner_label || f.iowner, 'clearIowner()']);
  var el = $('chips');
  if(!items.length){ el.className = 'chips'; el.innerHTML = ''; return; }
  var html = '<span style="font-size:12px;color:#64748b;">已选条件:</span>';
  items.forEach(function(c){
    html += '<button type="button" class="chip" onclick="' + c[2] + '" title="点击取消该条件">' +
            '<b>' + esc(c[0]) + '</b> ' + esc(c[1]) + ' <span class="x">×</span></button>';
  });
  html += '<button type="button" class="chip clear" onclick="clearAllFilters()">清除全部</button>';
  el.className = 'chips on';
  el.innerHTML = html;
}

function renderSeries(d){
  var ser = (d.charts && d.charts.series) || d.summary.series || {};
  // 系列硬隔离:选了塑封/灌封就只展示该系列,不再显示另一系列的图例
  var order = S.series ? [S.series] : ['molding','potting','unknown'];
  var total = 0;
  order.forEach(function(k){ total += (ser[k] && ser[k].lots) || 0; });
  var el = $('series_panel');
  if(!total){ el.innerHTML = '<div class="empty">当前筛选条件下无超期 LOT</div>'; return; }
  var seg = '<div class="seg">';
  order.forEach(function(k){
    var v = (ser[k] && ser[k].lots) || 0;
    if(!v) return;
    var cls = S.series === k ? 'selected' : '';
    seg += '<div class="' + cls + '" style="width:' + (v/total*100).toFixed(2) + '%;background:' + SERIES_COLOR[k] +
           ';" onclick="toggleSeries(\'' + k + '\')" title="' + SERIES_LABEL[k] + ' ' + fmt(v) +
           ' 批，点击' + (S.series === k ? '取消' : '筛选') + '"></div>';
  });
  seg += '</div>';
  var legend = '<div class="legend">';
  order.forEach(function(k){
    var x = ser[k] || {lots:0,orders:0,qty:0};
    var cls = S.series === k ? ' selected' : '';
    legend += '<div class="row' + cls + '" onclick="toggleSeries(\'' + k + '\')" title="点击' +
      (S.series === k ? '取消' : '筛选') + '">' +
      '<span class="dot" style="background:' + SERIES_COLOR[k] + '"></span>' +
      '<span>' + SERIES_LABEL[k] + '</span>' +
      '<span class="num">' + fmt(x.lots) + ' 批 · ' + fmt(x.orders) + ' 单 · ' + fmt(x.qty) + ' Ea</span></div>';
  });
  legend += '</div>';
  el.innerHTML = seg + legend;
}

function barList(elId, items, opts){
  var el = $(elId);
  if(!items || !items.length){ el.innerHTML = '<div class="empty">无数据</div>'; return; }
  var max = 1;
  items.forEach(function(it){ max = Math.max(max, opts.value(it)); });
  el.innerHTML = '';
  items.forEach(function(it){
    var row = document.createElement('div');
    var selected = opts.isSelected ? !!opts.isSelected(it) : false;
    row.className = 'bar-row' + (opts.onClick ? ' click' : '') + (selected ? ' selected' : '');
    row.setAttribute('aria-pressed', selected ? 'true' : 'false');
    var lab = document.createElement('div');
    lab.className = 'bar-lab';
    lab.textContent = opts.label(it);
    lab.title = opts.title ? opts.title(it) : opts.label(it);
    var track = document.createElement('div');
    track.className = 'bar-track';
    var fill = document.createElement('div');
    fill.className = 'bar-fill';
    fill.style.width = Math.max(1, Math.round(opts.value(it)/max*100)) + '%';
    fill.style.background = opts.color ? opts.color(it) : '#2563eb';
    track.appendChild(fill);
    var val = document.createElement('div');
    val.className = 'bar-val';
    val.textContent = opts.valText ? opts.valText(it) : fmt(opts.value(it));
    row.appendChild(lab); row.appendChild(track); row.appendChild(val);
    if(opts.onClick){
      row.onclick = function(){ opts.onClick(it); };
    }
    el.appendChild(row);
  });
}

function renderBuckets(d){
  var items = (d.charts && d.charts.buckets) || d.buckets || [];
  var el = $('bucket_panel');
  if(!items.length){ el.innerHTML = '<div class="empty">无数据</div>'; return; }
  var max = 1;
  items.forEach(function(x){ if((x.lots || 0) > max) max = x.lots; });
  var colors = ['#f59e0b','#f97316','#ef4444','#b91c1c','#7f1d1d'];
  el.innerHTML = '';
  var wrap = document.createElement('div');
  wrap.className = 'vchart';
  items.forEach(function(x, i){
    var col = document.createElement('div');
    col.className = 'vcol' + (S.bucket === i ? ' selected' : '');
    col.title = x.label + ' · ' + fmt(x.lots) + ' 批 / ' + fmt(x.orders) + ' 单 / ' +
                fmt(x.qty) + ' Ea' + (S.bucket === i ? '（已选，再点取消）' : '（点击筛选）');
    var val = document.createElement('div');
    val.className = 'vval';
    val.textContent = fmt(x.lots) + ' 批';
    var bar = document.createElement('div');
    bar.className = 'vbar';
    bar.style.height = Math.max(2, Math.round((x.lots || 0) / max * 150)) + 'px';
    bar.style.background = colors[Math.min(i, colors.length - 1)];
    var lab = document.createElement('div');
    lab.className = 'vlab';
    lab.textContent = x.label;
    col.appendChild(val); col.appendChild(bar); col.appendChild(lab);
    col.onclick = function(){ toggleBucket(i); };
    wrap.appendChild(col);
  });
  el.appendChild(wrap);
  var foot = $('bucket_foot');
  if(foot) foot.textContent = '共 ' + items.length + ' 档 · 点击柱体筛选，再点取消';
}

function takeTop(items, n, isSel){
  items = items || [];
  var out = items.slice(0, n);
  if(isSel){
    var sel = null;
    for(var i = 0; i < items.length; i++){
      if(isSel(items[i])){ sel = items[i]; break; }
    }
    if(sel && out.indexOf(sel) < 0) out.push(sel);
  }
  return out;
}
function chartFoot(id, shown, total, noun){
  var el = $(id);
  if(!el) return;
  total = total || 0;
  if(!total){ el.textContent = ''; return; }
  if(total <= shown) el.textContent = '共 ' + fmt(total) + ' 个' + (noun || '');
  else el.textContent = '共 ' + fmt(total) + ' 个' + (noun || '') + ' · 显示前 ' + shown + ' 个';
}

function renderFamilies(d){
  var all = (d.charts && d.charts.families) || d.by_family || [];
  var items = takeTop(all, 10, function(x){ return !!S.family && S.family === x.key; });
  barList('family_panel', items, {
    label: function(x){ return x.family || '(未分类)'; },
    value: function(x){ return x.lots; },
    valText: function(x){ return fmt(x.lots) + ' 批'; },
    color: function(){ return '#7c3aed'; },
    isSelected: function(x){ return !!S.family && S.family === x.key; },
    title: function(x){
      return (x.family || '') + ' · ' + fmt(x.lots) + ' 批 / ' + fmt(x.orders) + ' 单 / ' +
             fmt(x.qty) + ' Ea · 最长 ' + x.max_days + ' 天 · 点击筛选/取消';
    },
    onClick: function(x){ toggleFamily(x.key || ''); }
  });
  var total = (d.chart_totals && d.chart_totals.families) || all.length;
  chartFoot('family_foot', 10, total, '顶层型号');
}

function renderProducts(d){
  var all = (d.charts && d.charts.by_product) || d.by_product || [];
  var items = takeTop(all, 10, function(x){
    return !!(S.product && x.product && S.product.toUpperCase() === x.product.toUpperCase());
  });
  barList('product_panel', items, {
    label: function(x){ return x.product || '(空型号)'; },
    value: function(x){ return x.lots; },
    valText: function(x){ return fmt(x.lots) + ' 批'; },
    color: function(){ return '#2563eb'; },
    isSelected: function(x){
      return !!(S.product && x.product && S.product.toUpperCase() === x.product.toUpperCase());
    },
    title: function(x){
      return x.product + ' · ' + fmt(x.lots) + ' 批 / ' + fmt(x.orders) + ' 单 / ' + fmt(x.qty) +
             ' Ea · 最长 ' + x.max_days + ' 天 · 点击筛选/取消';
    },
    onClick: function(x){ toggleProduct(x.product || ''); }
  });
  var total = (d.chart_totals && d.chart_totals.products) || all.length;
  chartFoot('product_foot', 10, total, '细分项');
}

function renderProcesses(d){
  var all = (d.charts && d.charts.by_process) || d.by_process || [];
  var items = takeTop(all, 8, function(x){ return !!S.spec && S.spec === x.spec; });
  barList('process_panel', items, {
    label: function(x){ return x.desc || x.spec || '(空工序)'; },
    value: function(x){ return x.lots; },
    valText: function(x){ return fmt(x.lots) + ' 批'; },
    color: function(){ return '#0d9488'; },
    isSelected: function(x){ return !!S.spec && S.spec === x.spec; },
    title: function(x){
      return (x.spec || '') + ' · ' + (x.desc || '') + ' · ' + fmt(x.lots) + ' 批 / ' +
             fmt(x.orders) + ' 单 / ' + fmt(x.qty) + ' Ea · 点击筛选/取消';
    },
    onClick: function(x){ toggleSpec(x.spec || ''); }
  });
  var total = (d.chart_totals && d.chart_totals.processes) || all.length;
  chartFoot('process_foot', 8, total, '工序');
}

function renderOwnerChart(d, elId, footId, totalKey, all, selFn, toggleFn, color){
  var items = takeTop(all, 8, function(x){ return !!selFn() && selFn() === x.key; });
  barList(elId, items, {
    label: function(x){ return x.owner || '未分配'; },
    value: function(x){ return x.lots; },
    valText: function(x){ return fmt(x.lots) + ' 批'; },
    color: function(){ return color; },
    isSelected: function(x){ return !!selFn() && selFn() === x.key; },
    title: function(x){
      return (x.owner || '未分配') + ' · ' + fmt(x.lots) + ' 批 / ' + fmt(x.orders) + ' 单 / ' +
             fmt(x.qty) + ' Ea · 最长 ' + x.max_days + ' 天 · 点击筛选/取消';
    },
    onClick: function(x){ toggleFn(x.key || '__none__'); }
  });
  var total = (d.chart_totals && d.chart_totals[totalKey]) || all.length;
  chartFoot(footId, 8, total, '负责人');
}
function renderAssyOwners(d){
  renderOwnerChart(d, 'assy_panel', 'assy_foot', 'assy',
                   (d.charts && d.charts.assy) || [],
                   function(){ return S.aowner; }, toggleAowner, '#2563eb');
}
function renderMpcOwners(d){
  renderOwnerChart(d, 'mpc_panel', 'mpc_foot', 'mpc',
                   (d.charts && d.charts.mpc) || [],
                   function(){ return S.mowner; }, toggleMowner, '#0d9488');
}
function renderInlineOwners(d){
  renderOwnerChart(d, 'inline_panel', 'inline_foot', 'inline',
                   (d.charts && d.charts.inline) || [],
                   function(){ return S.iowner; }, toggleIowner, '#b45309');
}

function renderFamilySelect(d){
  var sel = $('f_family');
  var cur = S.family;
  var html = '<option value="">全部</option>';
  (d.families || []).forEach(function(x){
    html += '<option value="' + esc(x.key) + '">' + esc(x.family) + '（' + fmt(x.count) + '）</option>';
  });
  sel.innerHTML = html;
  sel.value = cur;
  if(sel.value !== cur){ sel.value = ''; S.family = ''; }
}

function renderProductsSelect(d){
  var sel = $('f_product');
  var cur = S.product;
  var html = '<option value="">全部</option>';
  (d.products || []).forEach(function(x){
    html += '<option value="' + esc(x.product) + '">' + esc(x.product) + '（' + fmt(x.count) + '）</option>';
  });
  sel.innerHTML = html;
  sel.value = cur;
  if(sel.value !== cur){ sel.value = ''; S.product = ''; }
}

function statusTag(s){
  var c = '#64748b';
  if(s === '运行中') c = '#15803d';
  else if(s === '等待进站') c = '#b45309';
  else if(s === '等待出站') c = '#2563eb';
  else if(s === 'HOLD') c = '#dc2626';
  return '<span class="tag" style="background:' + c + '18;color:' + c + '">' + esc(s || '-') + '</span>';
}
function seriesTag(s){
  var c = SERIES_COLOR[s] || '#94a3b8';
  return '<span class="tag" style="background:' + c + '18;color:' + c + '">' +
         esc(SERIES_LABEL[s] || s || '未分类') + '</span>';
}
function ownerCell(r){
  if(!r.owner) return '-';
  var kind = r.owner_kind || '';
  var c = kind === 'ENG' ? '#dc2626' : '#475569';
  var bg = kind === 'ENG' ? '#fee2e2' : '#f1f5f9';
  var tag = kind ? ' <span class="tag" style="background:' + bg +
                   ';color:' + c + '">' + esc(kind) + '</span>' : '';
  return esc(r.owner) + tag;
}

function renderTable(d){
  var tb = $('tbody');
  var rows = d.rows || [];
  if(!rows.length){
    tb.innerHTML = '<tr><td colspan="17" class="empty">当前筛选条件下无超期 LOT</td></tr>';
  }else{
    var html = '';
    rows.forEach(function(r){
      var procTitle = (r.spec || '') + (r.spec_desc ? (' · ' + r.spec_desc) : '');
      html += '<tr>' +
        '<td class="mono">' + esc(r.wo) + '</td>' +
        '<td class="mono">' + esc(r.lot) + '</td>' +
        '<td>' + esc(r.family || '-') + '</td>' +
        '<td>' + esc(r.sub_model || r.product || '-') + '</td>' +
        '<td>' + esc(r.product_name || '-') + '</td>' +
        '<td>' + esc(r.start) + '</td>' +
        '<td class="days">' + r.days + '</td>' +
        '<td class="mono" title="' + esc(procTitle) + '">' + esc(r.spec || '-') + '</td>' +
        '<td>' + esc(r.spec_desc || '-') + '</td>' +
        '<td>' + statusTag(r.status) + '</td>' +
        '<td style="text-align:center;font-variant-numeric:tabular-nums;">' + fmt(r.qty) + '</td>' +
        '<td>' + seriesTag(r.series) + '</td>' +
        '<td>' + esc(r.priority || '-') + '</td>' +
        '<td>' + esc(r.mes_owner || '-') + '</td>' +
        '<td>' + esc(r.assy_owner || '-') + '</td>' +
        '<td>' + esc(r.mpc_owner || '-') + '</td>' +
        '<td>' + ownerCell(r) + '</td>' +
        '</tr>';
    });
    tb.innerHTML = html;
  }
  $('tbl_note').textContent = '共 ' + fmt(d.total) + ' 条 · 第 ' + d.page + ' / ' + d.pages + ' 页';
  renderPager(d);
}

function renderPager(d){
  var p = $('pager');
  var html = '';
  html += '<button class="btn" onclick="gotoPage(1)"' + (d.page<=1?' disabled':'') + '>« 首页</button>';
  html += '<button class="btn" onclick="gotoPage(' + (d.page-1) + ')"' + (d.page<=1?' disabled':'') + '>‹ 上一页</button>';
  html += '<span>第 ' + d.page + ' / ' + d.pages + ' 页 · 共 ' + fmt(d.total) + ' 条</span>';
  html += '<button class="btn" onclick="gotoPage(' + (d.page+1) + ')"' + (d.page>=d.pages?' disabled':'') + '>下一页 ›</button>';
  html += '<button class="btn" onclick="gotoPage(' + d.pages + ')"' + (d.page>=d.pages?' disabled':'') + '>末页 »</button>';
  html += '<span class="sp"></span><span>跳至</span><input id="jump" type="number" min="1" max="' + d.pages + '" value="' + d.page + '">';
  html += '<button class="btn" onclick="jumpPage()">GO</button>';
  p.innerHTML = html;
}
function gotoPage(n){
  if(!DATA) return;
  n = Math.max(1, Math.min(parseInt(n,10) || 1, DATA.pages));
  S.page = n; syncURL(); load();
}
function jumpPage(){
  var el = $('jump');
  if(el) gotoPage(el.value);
}

function showExportToast(title, msg, state){
  var el = $('exportToast');
  if(!el) return;
  el.className = 'export-toast on' + (state ? ' ' + state : '');
  $('etTitle').textContent = title || '';
  $('etMsg').textContent = msg || '';
}
function stopExportPoll(){
  if(exportPollTimer){ clearInterval(exportPollTimer); exportPollTimer = null; }
}
function hideExportToast(){
  stopExportPoll();
  var el = $('exportToast');
  if(el) el.className = 'export-toast';
}
function pollExportStatus(ticket){
  function tick(){
    fetch('/wip_analysis/export_status?ticket=' + encodeURIComponent(ticket))
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(!d || !d.ok || ticket !== exportTicket) return;
        var state = d.state;
        if(state === 'waiting'){
          var pos = d.position || 1, ahead = d.ahead || 0, eta = d.eta_seconds || 0;
          showExportToast('导出排队中',
            '第 ' + pos + ' 位 · 前面还有 ' + ahead + ' 个 · 预计约 ' + eta + ' 秒');
        }else if(state === 'running'){
          showExportToast('正在生成文件',
            '马上开始下载，最多约 ' + (d.eta_seconds || 8) + ' 秒');
        }else if(state === 'done'){
          showExportToast('文件已生成', '浏览器开始下载', 'done');
          stopExportPoll();
          setTimeout(function(){ if(ticket === exportTicket) hideExportToast(); }, 3500);
        }else if(state === 'failed'){
          showExportToast('导出失败', '请重试，或先把筛选范围缩小', 'failed');
          stopExportPoll();
          setTimeout(function(){ if(ticket === exportTicket) hideExportToast(); }, 5000);
        }else if(Date.now() - exportPollStart > 15000){
          showExportToast('导出状态未知', '如果浏览器没开始下载，请重试', 'failed');
          stopExportPoll();
          setTimeout(function(){ if(ticket === exportTicket) hideExportToast(); }, 5000);
        }
      })
      .catch(function(){ /* 网络抖动,下一轮再试 */ });
  }
  tick();
  exportPollTimer = setInterval(tick, 1500);
}
function exportFile(kind){
  readInputs();
  syncURL();
  stopExportPoll();
  exportTicket = 'e' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  exportPollStart = Date.now();
  showExportToast('正在提交导出…', '排队信息马上出来');
  var p = new URLSearchParams(qstr({}));
  p.set('ticket', exportTicket);
  location.href = '/wip_analysis/export_' + kind + '?' + p.toString();
  pollExportStatus(exportTicket);
}

/* ===== 系列归属维护(管理员) ===== */
function openMap(){
  if(!CAN_EDIT){ location.href = '/login'; return; }
  $('mapMask').style.display = 'block';
  $('mapBox').style.display = 'flex';
  $('mapBody').innerHTML = '<tr><td colspan="5" class="empty">加载中…</td></tr>';
  fetch('/wip_analysis/map').then(function(r){ return r.json(); }).then(function(d){
    if(!d || !d.ok){ throw new Error((d && d.msg) || '加载失败'); }
    MAP_ROWS = d.rows || [];
    MAP_CUR = {}; HIDDEN_CUR = {};
    MAP_REV = d.rev || 0;
    OWNER_REV = (d.owners && d.owners.rev) || 0;
    MAP_ROWS.forEach(function(x){
      MAP_CUR[x.product] = x.series || '';
      if(x.hidden) HIDDEN_CUR[x.product] = true;
    });
    var hn = Object.keys(HIDDEN_CUR).length;
    var info = '共 ' + MAP_ROWS.length + ' 个型号';
    if(hn) info += ' · 隐藏 ' + hn + ' 个';
    if(d.updated_at) info += ' · 上次保存 ' + d.updated_at + (d.updated_by ? (' · ' + d.updated_by) : '');
    if(d.conflicts && d.conflicts.length) info += ' · 白名单冲突 ' + d.conflicts.length + ' 个';
    $('mapInfo').textContent = info;
    renderOwnerInfo(d.owners);
    renderMap();
    updateCheckAll();
  }).catch(function(e){
    $('mapBody').innerHTML = '<tr><td colspan="5" class="empty">加载失败：' + esc(e.message) + '</td></tr>';
  });
}
function renderOwnerInfo(info){
  var el = $('ownerInfo');
  if(!info){ el.textContent = '尚未导入'; return; }
  var c = info.counts || {};
  var parts = [OWNER_LABEL.assy + ' ' + fmt(c.assy || 0),
               OWNER_LABEL.mpc + ' ' + fmt(c.mpc || 0),
               OWNER_LABEL.inline + ' ' + fmt(c.inline || 0)];
  if(info.updated_at) parts.push('更新 ' + info.updated_at + (info.updated_by ? (' · ' + info.updated_by) : ''));
  var cf = info.conflicts || {};
  var cs = [];
  ['assy','mpc','inline'].forEach(function(k){ if(cf[k]) cs.push(OWNER_LABEL[k] + ' ' + cf[k]); });
  if(cs.length) parts.push('冲突 ' + cs.join(' / '));
  el.textContent = '当前：' + parts.join(' · ');
}
function showOwnMsg(t, ok){
  var el = $('ownerMsg');
  el.textContent = t || '';
  el.className = 'hint' + (t ? (ok ? ' ok' : ' err') : '');
  el.title = '';
}
function importOwners(){
  if(!CAN_EDIT){ location.href = '/login'; return; }
  var input = $('ownerFile');
  var f = input && input.files && input.files[0];
  if(!f){ showOwnMsg('请先选择 .xlsx 或 .csv 文件', false); return; }
  var btn = $('ownerBtn');
  btn.disabled = true; btn.textContent = '导入中…';
  showOwnMsg('正在解析并导入…', true);
  var fd = new FormData(); fd.append('file', f);
  fetch('/wip_analysis/owners_import?rev=' + encodeURIComponent(OWNER_REV),
        {method:'POST', body:fd})
    .then(function(r){ return r.json(); })
    .then(function(d){
      btn.disabled = false; btn.textContent = '导入 Owner 表';
      if(!d || !d.ok) throw new Error((d && d.msg) || '导入失败');
      var s = d.imported || {}, parts = [];
      ['assy','mpc','inline'].forEach(function(k){
        if(s[k] !== undefined) parts.push(OWNER_LABEL[k] + ' ' + fmt(s[k]));
      });
      var msg = '导入成功：' + (parts.join(' · ') || '0 条');
      if(d.warnings && d.warnings.length) msg += '（' + d.warnings.length + ' 条提示，悬停查看）';
      showOwnMsg(msg, true);
      if(d.warnings && d.warnings.length) $('ownerMsg').title = d.warnings.join('\n');
      if(d.owner_info && d.owner_info.rev !== undefined) OWNER_REV = d.owner_info.rev;
      renderOwnerInfo(d.owner_info || {});
      input.value = '';
      load();
    })
    .catch(function(e){
      btn.disabled = false; btn.textContent = '导入 Owner 表';
      showOwnMsg('导入失败：' + e.message, false);
    });
}
function closeMap(){
  $('mapMask').style.display = 'none';
  $('mapBox').style.display = 'none';
}
function renderMap(){
  var kw = ($('mapSearch').value || '').trim().toLowerCase();
  var tb = $('mapBody');
  var rows = MAP_ROWS.filter(function(x){
    return !kw || (x.product || '').toLowerCase().indexOf(kw) >= 0;
  });
  if(!rows.length){
    tb.innerHTML = '<tr><td colspan="5" class="empty">无匹配型号</td></tr>';
    updateCheckAll();
    return;
  }
  tb.innerHTML = '';
  rows.forEach(function(x){
    var tr = document.createElement('tr');
    var td0 = document.createElement('td');
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !HIDDEN_CUR[x.product];
    cb.title = cb.checked ? '当前显示；取消勾选后从 WIP 分析中排除'
                          : '已隐藏；勾选后恢复显示';
    cb.onchange = function(){
      if(cb.checked) delete HIDDEN_CUR[x.product];
      else HIDDEN_CUR[x.product] = true;
      updateMapStatus(td4, x.product, x.conflict);
      updateCheckAll();
    };
    td0.appendChild(cb);
    var td1 = document.createElement('td'); td1.textContent = x.product; td1.className = 'mono';
    var td2 = document.createElement('td'); td2.textContent = fmt(x.count);
    td2.style.textAlign = 'center';
    var td3 = document.createElement('td');
    var sel = document.createElement('select');
    [['','未分类'],['molding','塑封'],['potting','灌封']].forEach(function(o){
      var opt = document.createElement('option');
      opt.value = o[0]; opt.textContent = o[1];
      if((MAP_CUR[x.product] || '') === o[0]) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = function(){ MAP_CUR[x.product] = sel.value; updateMapStatus(td4, x.product); };
    td3.appendChild(sel);
    var td4 = document.createElement('td');
    updateMapStatus(td4, x.product, x.conflict);
    tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2);
    tr.appendChild(td3); tr.appendChild(td4);
    tb.appendChild(tr);
  });
  updateCheckAll();
}
function updateMapStatus(td, product, conflict){
  if(HIDDEN_CUR[product]){
    td.innerHTML = '<span class="tag" style="background:#f1f5f9;color:#64748b">已隐藏</span>';
    return;
  }
  var v = MAP_CUR[product] || '';
  if(conflict && !v){
    td.innerHTML = '<span class="tag" style="background:#fef2f2;color:#dc2626">白名单冲突</span>';
  }else if(v){
    td.innerHTML = '<span class="tag" style="background:#f0fdf4;color:#15803d">已归属 · ' + SERIES_LABEL[v] + '</span>';
  }else{
    td.innerHTML = '<span class="tag" style="background:#f8fafc;color:#64748b">未分类</span>';
  }
}
function toggleAllHidden(checked){
  if(!checked && MAP_ROWS.length > 1){
    if(!confirm('将隐藏全部 ' + MAP_ROWS.length + ' 个型号，WIP 超期分析会没有数据。确定吗？')){
      var all = $('mapCheckAll');
      if(all) all.checked = true;
      return;
    }
  }
  HIDDEN_CUR = {};
  if(!checked){
    MAP_ROWS.forEach(function(x){ HIDDEN_CUR[x.product] = true; });
  }
  renderMap();
  updateCheckAll();
}
function updateCheckAll(){
  var cb = $('mapCheckAll');
  if(!cb) return;
  var total = MAP_ROWS.length, hidden = 0;
  MAP_ROWS.forEach(function(x){ if(HIDDEN_CUR[x.product]) hidden++; });
  cb.checked = total > 0 && hidden === 0;
  cb.indeterminate = hidden > 0 && hidden < total;
}
function saveMap(){
  var btn = $('mapSave');
  btn.disabled = true; btn.textContent = '保存中…';
  fetch('/wip_analysis/map', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({map:MAP_CUR, hidden:Object.keys(HIDDEN_CUR), rev:MAP_REV})
  }).then(function(r){ return r.json(); }).then(function(d){
    btn.disabled = false; btn.textContent = '保存归属与显示';
    if(!d || !d.ok){ throw new Error((d && d.msg) || '保存失败'); }
    if(d.rev !== undefined) MAP_REV = d.rev;
    closeMap();
    showMsg('已保存：归属 ' + d.saved + ' 个 · 隐藏 ' + (d.hidden_count || 0) + ' 个', false);
    load();
  }).catch(function(e){
    btn.disabled = false; btn.textContent = '保存归属与显示';
    showMsg('保存失败：' + e.message);
  });
}

/* ===== 初始化 ===== */
(function init(){
  var q = new URLSearchParams(location.search);
  var d = parseInt(q.get('days') || '', 10);
  if(d >= 1) S.days = d;
  if(q.get('edges')) S.edges = q.get('edges');
  S.series = q.get('series') || '';
  S.bucket = null;
  if(q.get('bucket') !== null && q.get('bucket') !== ''){
    var _b = parseInt(q.get('bucket'), 10);
    if(_b >= 0) S.bucket = _b;
  }
  S.family = q.get('family') || '';
  S.product = q.get('product') || '';
  S.spec = q.get('spec') || '';
  S.aowner = q.get('aowner') || '';
  S.mowner = q.get('mowner') || '';
  S.iowner = q.get('iowner') || '';
  S.kw = q.get('kw') || '';
  S.page = parseInt(q.get('page') || '1', 10) || 1;
  $('f_days').value = S.days;
  $('f_edges').value = S.edges;
  $('f_series').value = S.series;
  $('f_family').value = S.family;
  $('f_kw').value = S.kw;
  if(CAN_EDIT){
    $('btn_map').style.display = 'inline-block';
    $('admin_link').textContent = '后台管理';
    $('admin_link').href = '/admin';
  }
  $('f_series').onchange = applyInputs;
  $('f_family').onchange = applyInputs;
  $('f_product').onchange = applyInputs;
  $('f_days').onchange = applyInputs;
  $('f_edges').onchange = applyInputs;
  $('f_kw').addEventListener('input', function(){
    clearTimeout(loadTimer);
    loadTimer = setTimeout(applyInputs, 350);
  });
  $('f_kw').addEventListener('keydown', function(e){
    if(e.key === 'Enter'){ e.preventDefault(); applyInputs(); }
  });
  $('f_days').addEventListener('keydown', function(e){
    if(e.key === 'Enter'){ e.preventDefault(); applyInputs(); }
  });
  $('f_edges').addEventListener('keydown', function(e){
    if(e.key === 'Enter'){ e.preventDefault(); applyInputs(); }
  });
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape') closeMap();
  });
  load();
})();
</script>
</body></html>"""


def render_wip_analysis(is_authed=False):
    """WIP 超期分析页(公开;管理员登录后显示系列归属维护)。"""
    return _WIP_ANALYSIS_HTML.replace(
        "__CAN_EDIT__", "true" if is_authed else "false")


def render_login(msg="", stats=None, next_path=""):
    s = stats or {}
    next_html = (f'<input type="hidden" name="next" value="'
                 f'{_html.escape(next_path, quote=True)}">' if next_path else "")
    # MES 聚合数据驱动背景(仅非敏感数字)
    g_total = int(s.get("total", 0) or 0)
    g_high = int(s.get("high", 0) or 0)
    g_running = int(s.get("running", 0) or 0)
    g_avg = float(s.get("avg_progress", 0) or 0)
    msg_html = (f'<div class="flash" style="background:rgba(254,242,242,.92);color:#dc2626;">'
                f'{_html.escape(msg)}</div>' if msg else "")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · NTO 生产进度系统</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
html,body {{ height:100%; }}
body {{ font-family:-apple-system,"Microsoft YaHei",sans-serif; background:#05070f;
        display:flex; align-items:center; justify-content:center; overflow:hidden; }}
#net {{ position:fixed; inset:0; z-index:0; display:block; }}
.veil {{ position:fixed; inset:0; z-index:1; pointer-events:none;
         background:radial-gradient(ellipse at center, rgba(5,7,15,.15) 0%, rgba(5,7,15,.55) 65%, rgba(5,7,15,.88) 100%); }}
.box {{ position:relative; z-index:3; width:340px; padding:34px 32px;
        background:rgba(13,20,38,.55); border:1px solid rgba(56,189,248,.22);
        border-radius:14px; backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
        box-shadow:0 18px 50px rgba(0,0,0,.55), 0 0 0 1px rgba(56,189,248,.05) inset; }}
.box h2 {{ font-size:19px; color:#f1f5f9; margin-bottom:4px; letter-spacing:1px; }}
.box p {{ font-size:12px; color:#7dd3fc; margin-bottom:22px; opacity:.85; }}
.box label {{ font-size:13px; color:#cbd5e1; display:block; margin-bottom:6px; }}
.box input {{ width:100%; padding:10px 12px; border:1px solid rgba(148,163,184,.35);
        border-radius:8px; font-size:14px; margin-bottom:16px; background:rgba(2,6,23,.5);
        color:#e2e8f0; outline:none; transition:border-color .15s, box-shadow .15s; }}
.box input::placeholder {{ color:#64748b; }}
.box input:focus {{ border-color:#38bdf8; box-shadow:0 0 0 3px rgba(56,189,248,.18); }}
.box button {{ width:100%; background:linear-gradient(90deg,#2563eb,#0ea5e9); color:#fff;
        border:none; padding:11px; border-radius:8px; font-size:15px; cursor:pointer;
        letter-spacing:2px; transition:filter .15s, transform .15s; }}
.box button:hover {{ filter:brightness(1.1); transform:translateY(-1px); }}
.flash {{ padding:9px 12px; border-radius:8px; margin-bottom:16px; font-size:13px;
        background:rgba(220,252,231,.92); color:#16a34a; }}
/* —— 极光光晕层(在神经网络之上叠柔和色彩流动,增加景深) —— */
.aurora {{ position:fixed; inset:0; z-index:1; pointer-events:none; mix-blend-mode:screen;
  background:radial-gradient(55vw 50vh at 18% 22%, rgba(56,189,248,.12), transparent 60%),
             radial-gradient(50vw 55vh at 85% 78%, rgba(139,92,246,.12), transparent 62%);
  animation:auroraShift 16s ease-in-out infinite alternate; }}
@keyframes auroraShift {{ from {{ transform:translate3d(-2%,-1%,0) scale(1); opacity:.8; }}
                          to {{ transform:translate3d(3%,2%,0) scale(1.1); opacity:1; }} }}
/* —— 登录框:入场动画 + 内部裁切(供扫描线) —— */
.box {{ overflow:hidden; animation:boxIn .7s cubic-bezier(.2,.7,.2,1) both; }}
@keyframes boxIn {{ from {{ opacity:0; transform:translateY(18px) scale(.96); }}
                    to {{ opacity:1; transform:none; }} }}
/* —— 四角 HUD 角标 —— */
.cn {{ position:absolute; width:15px; height:15px; z-index:4;
       border:2px solid rgba(56,189,248,.85); filter:drop-shadow(0 0 4px rgba(56,189,248,.5)); }}
.cn.tl {{ top:9px; left:9px; border-right:0; border-bottom:0; }}
.cn.tr {{ top:9px; right:9px; border-left:0; border-bottom:0; }}
.cn.bl {{ bottom:9px; left:9px; border-right:0; border-top:0; }}
.cn.br {{ bottom:9px; right:9px; border-left:0; border-top:0; }}
/* —— 横向扫描线(周期性扫过登录框) —— */
.scan {{ position:absolute; left:0; right:0; top:0; height:2px; z-index:4; pointer-events:none;
         background:linear-gradient(90deg,transparent,rgba(56,189,248,.9),transparent);
         box-shadow:0 0 14px rgba(56,189,248,.75); animation:scanMove 3.8s ease-in-out infinite; }}
@keyframes scanMove {{ 0% {{ top:0; opacity:0; }} 12% {{ opacity:1; }} 88% {{ opacity:1; }} 100% {{ top:100%; opacity:0; }} }}
/* —— 系统状态行(等宽字体 + 闪烁绿点) —— */
.sysline {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:10px; letter-spacing:2px;
            color:#7dd3fc; opacity:.85; margin-bottom:14px; display:flex; align-items:center; gap:7px; }}
.sysline .dot {{ width:7px; height:7px; border-radius:50%; background:#22c55e;
                 box-shadow:0 0 9px #22c55e; animation:blink 1.5s ease-in-out infinite; }}
@keyframes blink {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.25; }} }}
.box h2 {{ text-shadow:0 0 20px rgba(56,189,248,.5); }}
.box p {{ font-family:ui-monospace,Menlo,Consolas,monospace; letter-spacing:1px; }}
/* —— 按钮扫光 —— */
.box button {{ position:relative; overflow:hidden; }}
.box button::after {{ content:""; position:absolute; top:0; left:-130%; width:55%; height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.4),transparent); transform:skewX(-20deg); }}
.box button:hover::after {{ animation:sweep .85s ease; }}
@keyframes sweep {{ to {{ left:150%; }} }}
.box input:focus {{ box-shadow:0 0 0 3px rgba(56,189,248,.22), 0 0 18px rgba(56,189,248,.25); }}
</style></head><body>
<canvas id="net"></canvas>
<div class="aurora"></div>
<div class="veil"></div>
<div class="box">
  <span class="cn tl"></span><span class="cn tr"></span><span class="cn bl"></span><span class="cn br"></span>
  <div class="scan"></div>
  <div class="sysline"><span class="dot"></span>SYSTEM ONLINE · NTO CONTROL</div>
  <h2>后台管理登录</h2>
  <p>NTO PRODUCTION CONTROL · 工单排产看板</p>
  {msg_html}
  <form method="post" action="/login">
    {next_html}
    <label>账号</label><input name="user" autofocus required placeholder="请输入账号">
    <label>密码</label><input name="pw" type="password" required placeholder="请输入密码">
    <button type="submit">登 录</button>
  </form>
</div>
<script>
/* ===== 神经网络动态背景(由 MES 聚合数据驱动,仅用非敏感数字)===== */
(function(){{
  var cv=document.getElementById('net'), ctx=cv.getContext('2d');
  var DPR=Math.min(window.devicePixelRatio||1, 2);
  // 来自 MES 的聚合指标(后端注入)
  var MES={{ total:{g_total}, high:{g_high}, running:{g_running}, avg:{g_avg} }};
  var highRatio = MES.total>0 ? (MES.high/MES.total) : 0;     // 高风险占比 → 脉冲频率/连线偏红
  var pulseRate = 0.0004 + highRatio*0.004;                   // 高风险越多,信号脉冲越密
  var linkR = Math.round(56 + highRatio*180);                 // 高风险越多,连线越偏红
  var linkG = Math.round(189 - highRatio*120);
  var linkB = Math.round(248 - highRatio*120);
  var speed = 0.30 + (MES.avg/100)*0.5;                       // 平均进度越高,节点游动越快(更"活")
  var W, H, nodes=[], pulses=[], LINK=150, mouse={{x:-9999,y:-9999}};

  function resize(){{
    W=window.innerWidth; H=window.innerHeight;
    cv.width=W*DPR; cv.height=H*DPR; cv.style.width=W+'px'; cv.style.height=H+'px';
    ctx.setTransform(DPR,0,0,DPR,0,0);
    // 节点数:屏幕大小为基,叠加在制工单数(每个在制工单多 ~3 个节点)
    var base=Math.round(W*H/16000);
    var density=base + MES.running*3 + Math.round(MES.total*0.6);
    density=Math.max(50, Math.min(200, density));
    nodes=[];
    for(var i=0;i<density;i++){{
      nodes.push({{
        x:Math.random()*W, y:Math.random()*H,
        vx:(Math.random()-0.5)*speed, vy:(Math.random()-0.5)*speed,
        r:1.2+Math.random()*1.6
      }});
    }}
  }}
  resize();
  window.addEventListener('resize', resize);
  window.addEventListener('mousemove', function(e){{ mouse.x=e.clientX; mouse.y=e.clientY; }});
  window.addEventListener('mouseout', function(){{ mouse.x=-9999; mouse.y=-9999; }});

  // 沿连线流动的脉冲(随机在相邻节点间触发)
  function spawnPulse(a,b){{
    pulses.push({{a:a,b:b,t:0,sp:0.012+Math.random()*0.02}});
  }}

  function step(){{
    ctx.clearRect(0,0,W,H);

    // 更新节点位置
    for(var i=0;i<nodes.length;i++){{
      var n=nodes[i];
      n.x+=n.vx; n.y+=n.vy;
      if(n.x<0||n.x>W) n.vx*=-1;
      if(n.y<0||n.y>H) n.vy*=-1;
      // 鼠标轻微排斥,增加交互感
      var dx=n.x-mouse.x, dy=n.y-mouse.y, dm=Math.sqrt(dx*dx+dy*dy);
      if(dm<120 && dm>0){{ n.x+=dx/dm*1.2; n.y+=dy/dm*1.2; }}
    }}

    // 画连线(邻近节点)
    for(var i=0;i<nodes.length;i++){{
      for(var j=i+1;j<nodes.length;j++){{
        var a=nodes[i], b=nodes[j];
        var dx=a.x-b.x, dy=a.y-b.y, d=Math.sqrt(dx*dx+dy*dy);
        if(d<LINK){{
          var alpha=(1-d/LINK)*0.55;
          ctx.strokeStyle='rgba('+linkR+','+linkG+','+linkB+','+alpha+')';   // 连线(高风险越多越偏红)
          ctx.lineWidth=0.6;
          ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
          // 偶发触发脉冲(频率随高风险占比上升)
          if(Math.random()<pulseRate && pulses.length<80) spawnPulse(a,b);
        }}
      }}
    }}

    // 画节点(带辉光)
    ctx.globalCompositeOperation='lighter';
    for(var i=0;i<nodes.length;i++){{
      var n=nodes[i];
      var g=ctx.createRadialGradient(n.x,n.y,0,n.x,n.y,n.r*4);
      g.addColorStop(0,'rgba(125,211,252,.9)');
      g.addColorStop(1,'rgba(125,211,252,0)');
      ctx.fillStyle=g;
      ctx.beginPath(); ctx.arc(n.x,n.y,n.r*4,0,Math.PI*2); ctx.fill();
      ctx.fillStyle='rgba(224,242,254,.95)';
      ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,Math.PI*2); ctx.fill();
    }}

    // 画脉冲(沿连线移动的亮点)
    for(var k=pulses.length-1;k>=0;k--){{
      var p=pulses[k]; p.t+=p.sp;
      if(p.t>=1){{ pulses.splice(k,1); continue; }}
      var px=p.a.x+(p.b.x-p.a.x)*p.t, py=p.a.y+(p.b.y-p.a.y)*p.t;
      var pg=ctx.createRadialGradient(px,py,0,px,py,6);
      pg.addColorStop(0,'rgba(255,255,255,.95)');
      pg.addColorStop(1,'rgba(56,189,248,0)');
      ctx.fillStyle=pg;
      ctx.beginPath(); ctx.arc(px,py,6,0,Math.PI*2); ctx.fill();
    }}
    ctx.globalCompositeOperation='source-over';

    requestAnimationFrame(step);
  }}
  requestAnimationFrame(step);
}})();
</script>
</body></html>"""


def render_ct_ai(models_summary, msg=""):
    """CT数据校准页:展示各型号实测CT(中位数),提供导出。
    models_summary: [{model, rows:[{seq,spec,samples,median_h,pending,reliable}], total_samples}]
    """
    msg_html = f'<div class="flash">{_html.escape(msg)}</div>' if msg else ""
    if not models_summary:
        inner = ('<div class="panel"><h3>CT 实测分析</h3>'
                 '<p style="color:#64748b;font-size:13px;line-height:1.7;">'
                 '暂无可用样本。本页通过 <b>observe 跨快照定稿</b>(批次走完工序后记录完整加工+等待,最准)'
                 '累积各型号 × 工序的实测周期CT,样本不足时用 <b>scan 单快照口径修正</b>兜底,随每次 MES 刷新自动累积。<br>'
                 '请确认 MES 数据已就绪;observe 主路需<b>持续运行一段时间</b>才会逐步积累样本。'
                 '若要让「生产进度看板」的进度分段也采用学习后 CT,请在「后台管理」上传对应型号的 CT 表作为工序骨架。</p></div>')
        return layout("ct_ai", "CT数据校准", f'<div class="page-title">CT 数据校准</div>'
                      f'<div class="page-sub">observe 跨快照定稿为主 · scan 单快照口径修正兜底 · 随 MES 刷新自动累积校准</div>'
                      f'{msg_html}{inner}')

    blocks = []
    for mi, m in enumerate(models_summary):
        rows = m["rows"]
        def _conf_cell(r):
            if not r.get("samples"):
                return '<td style="text-align:center;color:#cbd5e1;">—</td>'
            c = r.get("confidence", 0) or 0
            col = "#16a34a" if c >= 75 else ("#ca8a04" if c >= 40 else "#dc2626")
            lvl = "高" if c >= 75 else ("中" if c >= 40 else "低")
            tag = ('<span class="tag tag-done">可用</span>' if r["reliable"]
                   else '<span class="tag tag-low">样本不足</span>')
            # 默认只显示「高/中/低+可用」摘要;集中度/稳健变异系数折进详情(点击展开)
            # 整格内容居中(摘要、详情、展开行一致)
            return (f'<td style="min-width:116px;text-align:center;">'
                    f'<div style="display:flex;align-items:center;justify-content:center;gap:8px;">'
                    f'<span style="font-size:14px;font-weight:700;color:{col};">{lvl}</span>{tag}</div>'
                    f'<details style="font-size:10px;color:#94a3b8;"><summary style="cursor:pointer;list-style:none;margin-top:3px;text-align:center;">'
                    f'可信度 {c:.0f}% ▸</summary>'
                    f'<div style="margin-top:2px;text-align:center;">集中度 {r.get("concentration",0):.0f}% · '
                    f'稳健变异系数 {r.get("cv", "—")}</div></details></td>')

        def _src_cell(r):
            if not r.get("samples"):
                return ('<td style="text-align:center;"><span class="tag" '
                        'style="background:#f1f5f9;color:#94a3b8;" '
                        'title="该工序在上传的 CT 表中,但 MES 尚未采集到实测样本">'
                        '尚未采集</span></td>')
            if r.get("src") == "observe":
                return ('<td style="text-align:center;"><span class="tag tag-done" '
                        'title="跨快照定稿:已走完工序批次的完整周期,无删失/无长度偏倚,最准">'
                        '✓ 已校准</span></td>')
            return ('<td style="text-align:center;"><span class="tag tag-low" '
                    'title="单快照口径修正估算;observe 样本积累够(≥5条)后自动切换为已校准">'
                    '快照估算</span></td>')

        def _seqno_cell(r):
            # SEQNO 一律取「上传 CT 表」的步骤号;实测多出、CT 表没有的工序显示 —
            sq = r.get("ct_seqno")
            if sq is None or sq == "":
                return ('<td style="text-align:center;color:#cbd5e1;" '
                        'title="该工序不在上传的 CT 表中">—</td>')
            return f'<td style="text-align:center;">{_html.escape(str(sq))}</td>'

        def _num(v, n):
            # 未采集到样本的工序数值显示 — ,避免 0.00 被误读为"CT=0"
            return "—" if not n else f"{v:.2f}"

        body_rows = "".join(
            ('<tr style="background:#fafbfc;">' if not r["samples"] else '<tr>')
            + _seqno_cell(r)
            + f'<td>{_html.escape(r["spec"])}</td>'
            f'<td style="color:#475569;">{_html.escape(r.get("ct_desc") or "")}</td>'
            f'<td style="text-align:center;font-weight:600;">{_num(r["median_h"], r["samples"])}</td>'
            f'<td style="text-align:center;color:#94a3b8;">{_num(r.get("run_median_h",0), r["samples"])}</td>'
            f'<td style="text-align:center;color:#94a3b8;">{_num(r["wait_median_h"], r["samples"])}</td>'
            f'<td style="text-align:center;" title="有效样本(剔除&lt;30%×粗中位的近0截断碎片后)。上方为原始样本数,差异即被净化掉的碎片">'
            f'{r["samples"]}'
            f'{("<div style=\'font-size:10px;color:#94a3b8;\'>原始 " + str(r.get("raw_n") if r.get("raw_n") is not None else r["samples"]) + "</div>") if (r.get("raw_n") is not None and r["raw_n"] != r["samples"]) else ""}'
            f'</td>'
            + _src_cell(r)
            + _conf_cell(r)
            + '</tr>'
            for r in rows) or '<tr><td colspan="9" style="text-align:center;color:#94a3b8;padding:20px;">该型号暂无样本</td></tr>'
        # observe 主路校准进度:已切到 observe(无偏)的工序数 / 总工序数
        _obs = sum(1 for r in rows if r.get("src") == "observe")
        _tot = len(rows)
        _pct = (_obs / _tot * 100) if _tot else 0
        # C3: 表头 sticky;C4: 用 data-model 控制显隐,默认只显示第一个
        display = "block" if mi == 0 else "none"
        sticky_th = "position:sticky;top:0;background:#eef2f7;z-index:1;text-align:center;"
        blocks.append(f"""
        <div class="ct-block" data-model="{_html.escape(m["model"])}" style="display:{display};">
        <div class="panel">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:12px;">
            <h3 style="margin:0;">型号 {_html.escape(m["model"])} · 实测 CT(共 {m["total_samples"]} 条样本)</h3>
            <a href="/ct_ai/export?model={_html.escape(m["model"])}" class="btn-export" title="导出该型号实测CT(CSV)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>导出 CSV</a>
          </div>
          <div style="margin:-2px 0 12px;display:flex;align-items:center;gap:10px;font-size:12px;color:#475569;flex-wrap:wrap;">
            <span>observe 主路校准进度</span>
            <div style="flex:1;min-width:120px;max-width:260px;background:#e2e8f0;border-radius:4px;height:8px;overflow:hidden;">
              <div style="width:{_pct:.0f}%;height:100%;background:#16a34a;"></div></div>
            <span style="font-weight:600;color:#16a34a;">{_obs}/{_tot} 道工序已校准 ({_pct:.0f}%)</span>
            <span style="color:#94a3b8;">· 其余为快照估算 / 尚未采集,样本积累后自动校准</span>
          </div>
          <div style="max-height:calc(100vh - 280px);overflow:auto;"><table>
            <thead><tr>
              <th style="{sticky_th}" title="按你上传的 CT 表的步骤号(SEQNO)显示并排序">SEQNO(步骤)</th>
              <th style="{sticky_th}text-align:left;">工序(SPECNAME)</th>
              <th style="{sticky_th}text-align:left;">描述</th>
              <th style="{sticky_th}">实测周期CT(h)</th>
              <th style="{sticky_th}">其中加工(h)</th><th style="{sticky_th}" title="单条记录等待>WAIT_CAP_H(3h)视为失效剔除,其余取中位">其中等待(h)</th>
              <th style="{sticky_th}">样本数</th>
              <th style="{sticky_th}">来源</th>
              <th style="{sticky_th}">可信度</th>
            </tr></thead>
            <tbody>{body_rows}</tbody>
          </table></div>
        </div></div>""")

    # C4: 左侧型号筛选
    model_btns = "".join(
        f'<a href="javascript:void(0)" class="ctm-item{" active" if i==0 else ""}" '
        f'data-model="{_html.escape(m["model"])}" onclick="ctPick(this)">型号 {_html.escape(m["model"])}'
        f'<span style="float:right;color:#94a3b8;font-size:12px;">{m["total_samples"]}</span></a>'
        for i, m in enumerate(models_summary))

    # C2: 说明收进「计算逻辑说明」按钮,默认收起,点击展开(避免占用版面)
    top_note = ('<div style="margin-bottom:14px;">'
                '<button type="button" onclick="toggleCtNote(this)" '
                'style="display:inline-flex;align-items:center;gap:6px;font-size:13px;color:#475569;'
                'background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:7px 14px;cursor:pointer;">'
                '<span style="font-size:15px;">📖</span>计算逻辑说明<span id="ctNoteArrow" style="color:#94a3b8;">▾</span></button>'
                '<div id="ctNoteBox" style="display:none;font-size:12px;color:#64748b;background:#f8fafc;'
                'border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;margin-top:8px;line-height:1.8;">'
                '<b>实测周期CT</b> = 工序 <b>加工(RUNTIME) + 等待(WAITTIME)</b> 的<b>中位数</b>'
                '(从进入工序到流转去下一工序的总停留),与上传 CT 表 OTDO_CT 同口径;'
                '<b>有效性口径(run/wait 各自独立)</b>:加工取 RUNTIME>0 的记录;等待取 WAITTIME≤3h 的记录——'
                '<b>单条记录 WAITTIME 超过 WAIT_CAP_H(3h) 只让「该条等待」失效</b>(异常排队/搁置/暂存不计入该工序等待校准),'
                '它的 <b>run 仍然有效</b>、其他工序与其他样本照常计算;两者不绑定、不互相作废。'
                '另:样本数 &lt;5 的工序不取其中位(防单个卡死批次把加工CT 拉到几百小时),退回上传 CT;含 HTTVS 的虚拟报废工序不计入。<br>'
                '<b>「来源」列(准确性分级)</b>:<br>'
                '· <b style="color:#16a34a;">✓ 已校准(observe 主路)</b> —— 跨多张 MES 快照<b>追踪每个批次</b>,'
                '待批次<b>走完该工序</b>后才记录它的完整加工+等待,<b>无删失、无长度偏倚,最准</b>。'
                '随系统持续运行自动累积,顶部「校准进度」即该型号已切到 observe 的工序占比。<br>'
                '· <b style="color:#ca8a04;">快照估算(scan 兜底)</b> —— observe 样本不足(&lt;5 条)时,'
                '用<b>单张快照口径修正</b>过渡:RUNTIME 只取「加工中/等待出站」状态(避免「等待进站」'
                '阶段 RUNTIME≈0 把加工CT 拉低),WAITTIME 只取「等待进站」状态(真实排队),'
                '<b>HOLD 异常停留整行剔除</b>。可信度已自动打 7 折,样本够后会自动升级为「已校准」。<br>'
                '<b>可信度%</b> = 集中度 × 样本量权重:集中度 = max(0, 1 − 1.4826 × MAD ÷ 中位数)'
                '(MAD 为中位绝对偏差,比标准差更不受长尾/偶发异常影响),样本量权重 = min(1, 样本数 ÷ 20);'
                '集中度基于<b>加工节拍(RUNTIME)</b>的集中程度(等待时长大量为 0 是真实快速流转,不属污染,不用它算)。'
                '计算前先做<b>碎片净化</b>:剔除 &lt;max(0.05h, 30%×粗中位) 的近 0 截断样本(MES 快照间隔内刚上机即转走的长时记录,'
                '会拖低中位、拉大 MAD、虚高样本数);「样本数」列的原始值即净化前,差值为被剔除的碎片。'
                '样本满 20 条且为 observe 来源才标<b>「可用」</b>。'
                '<b>「生产进度看板」的进度分段、进度%、ETA 已自动采用学习后 CT</b>'
                '(observe 优先,样本 ≥ 5 的工序用实测周期,其余退回 scan 兜底/上传 CT 表),无需再人工导出上传。</div></div>')

    body = (f'<div class="page-title">CT 数据校准</div>'
            f'<div class="page-sub">实测周期CT = 工序加工 + 等待的中位数(run/wait 各自独立剔无效样本) · '
            f'<b style="color:#16a34a;">observe 跨快照定稿为主</b>(最准),样本不足时 scan 单快照口径修正兜底 · '
            f'随 MES 刷新自动累积校准(单条等待&gt;3h 失效、HOLD 整行剔除、稀疏工序退回上传CT)</div>'
            f'{msg_html}{top_note}'
            f'<div class="ct-layout">'
            f'<div class="ct-side">{model_btns}</div>'
            f'<div class="ct-main">{"".join(blocks)}</div>'
            f'</div>'
            f'<script>'
            f'function ctPick(el){{'
            f'  var m=el.getAttribute("data-model");'
            f'  document.querySelectorAll(".ctm-item").forEach(function(a){{a.classList.toggle("active",a===el);}});'
            f'  document.querySelectorAll(".ct-block").forEach(function(b){{b.style.display=(b.getAttribute("data-model")===m)?"block":"none";}});'
            f'}}'
            f'function toggleCtNote(btn){{'
            f'  var box=document.getElementById("ctNoteBox"),ar=document.getElementById("ctNoteArrow");'
            f'  var open=box.style.display==="none";'
            f'  box.style.display=open?"block":"none"; ar.textContent=open?"▴":"▾";'
            f'}}'
            f'</script>')
    return layout("ct_ai", "CT数据校准", body)


_MONITOR_JS = r"""
<script>
var mon = (function(){
  var ALL_OWNERS = __ALL_OWNERS__;
  var ALL_PRODUCTS = __ALL_PRODUCTS__;
  var sel = {owners: ALL_OWNERS.slice(), products: ALL_PRODUCTS.slice(), models: "MB12", kw:"", seqFrom:0, seqTo:0};
  var _hlLot=null, _hlSpec=null;   // 搜索完整 Lot 命中后的高亮目标
  var _lastData=null;              // 最近一次数据缓存(窗口缩放时按新高度重绘)
  var _dirty=false;                // 面板勾选未提交标记:关闭面板时统一刷新(支持连续多选)
  var _seq = 0;   // 请求序号:只接受最后一次的结果,防快速切换乱序
  function esc(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
  function fmt(n){ n=n||0; try{ return n.toLocaleString("en-US"); }catch(_){ return ""+n; } }
  function fmtEta(t){ if(!t) return '—';
    var d=new Date(t*1000),mo=String(d.getMonth()+1).padStart(2,'0'),
        da=String(d.getDate()).padStart(2,'0'),hh=String(d.getHours()).padStart(2,'0'),
        mi=String(d.getMinutes()).padStart(2,'0');
    return mo+'-'+da+' '+hh+':'+mi; }
  function qs(){
    var p=["models="+encodeURIComponent(sel.models)];
    // owners 总是显式传:空串=清空(一个都不匹配),全选=全列表(与不选区分开)
    p.push("owners="+encodeURIComponent(sel.owners.join(",")));
    // products 全选时不传(=全部,URL 更短);清空时传空串(一个都不匹配)
    if(sel.products.length<ALL_PRODUCTS.length) p.push("products="+encodeURIComponent(sel.products.join(",")));
    if(sel.kw) p.push("kw="+encodeURIComponent(sel.kw));
    // 搜索时显示完整流程(无在制数据的工序也占位显示)
    if(sel.kw) p.push("full=1");
    return p.join("&");
  }
  function labelOf(all, cur){
    if(!cur.length) return "无";
    if(cur.length>=all.length) return "全部";
    if(cur.length<=2) return cur.join("、");
    return cur.slice(0,2).join("、")+" 等"+cur.length+"项";
  }
  function allOf(kind){ return kind==="owners"?ALL_OWNERS:ALL_PRODUCTS; }
  function selOf(kind){ return kind==="owners"?sel.owners:sel.products; }
  function labelOfKind(kind){
    var all=allOf(kind), cur=selOf(kind);
    if(!cur.length) return "无";
    if(cur.length>=all.length) return "全部";
    if(cur.length<=2) return cur.join("、");
    return cur.slice(0,2).join("、")+" 等"+cur.length+"项";
  }
  function syncSelLabel(kind){
    var btn=document.getElementById("mon_btn_"+kind);
    if(btn) btn.textContent=(kind==="owners"?"OWNERNAME":"产品型号")+": "+labelOfKind(kind);
  }
  function closePanels(){
    var flush=_dirty;
    ["owners","products"].forEach(function(k){
      var p=document.getElementById("mon_panel_"+k); if(p) p.style.display="none";
    });
    // 勾选不立即刷新:关闭面板时统一提交(连续多选不打断)
    if(flush){ _dirty=false; load(render); }
  }
  function refreshPanel(kind){
    // 打开面板时按当前选择刷新候选(联动):owners 候选受已选产品型号约束,
    // 产品型号候选受已选 OWNER 约束;已选但不在候选里的自动移除
    fetch("/monitor/options?models="+encodeURIComponent(sel.models)
          +"&owners="+encodeURIComponent(sel.owners.join(","))
          +"&products="+encodeURIComponent(sel.products.join(",")))
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(!d) return;
        var vals=kind==="owners"?d.owners:d.products;
        if(!vals) return;
        var cur=selOf(kind);
        var keep=cur.filter(function(v){ return vals.indexOf(v)>=0; });
        var list=document.getElementById("mon_panel_"+kind).querySelector(".mon-owners-list");
        if(list) list.innerHTML=vals.map(function(o){
          return '<label style="display:block;padding:4px 8px;cursor:pointer;border-radius:5px;font-size:13px;color:#334155;">'
            + '<input type="checkbox" data-v="'+esc(o)+'"'+(keep.indexOf(o)>=0?' checked':'')+' style="margin-right:6px;">'+esc(o)+'</label>';
        }).join('') || '<div style="padding:10px;font-size:12px;color:#94a3b8;">无匹配选项(调整其他筛选后重开)</div>';
        cur.length=0; keep.forEach(function(v){ cur.push(v); });
        if(kind==="owners") ALL_OWNERS=vals; else ALL_PRODUCTS=vals;
        syncSelLabel(kind);
        // 重建后重新应用面板搜索框过滤
        var sf=document.getElementById("mon_search_"+kind);
        if(sf && sf.value) filterPanel(kind, sf.value);
      });
  }
  function togglePanel(kind){
    var btn=document.getElementById("mon_btn_"+kind), p=document.getElementById("mon_panel_"+kind);
    if(!p) return;
    var open=p.style.display==="block";
    closePanels();
    if(!open){
      // 移到 body 下 + fixed 定位:.main 带 transform 动画会创建 stacking context,
      // 面板留在 .main 里会被 body 级的下钻弹窗(遮罩49/弹窗50)盖住;body 下 z-index 60 可盖一切内容
      if(p.parentNode!==document.body){
        var r=btn.getBoundingClientRect();
        p.style.position="fixed";
        p.style.top=(r.bottom+4)+'px';
        p.style.left=r.left+'px';
        p.style.zIndex="60";
        document.body.appendChild(p);
      }
      p.style.display="block";
      // 再打开时恢复全量显示:清空搜索框并取消过滤(已选保留勾选)
      var sf=document.getElementById("mon_search_"+kind);
      if(sf){ sf.value=''; filterPanel(kind, ''); }
      refreshPanel(kind);   // 打开时联动刷新候选
    }
  }
  function onCheck(kind){
    var cur=selOf(kind);
    cur.length=0;
    document.querySelectorAll("#mon_panel_"+kind+" input[data-v]").forEach(function(cb){
      if(cb.checked) cur.push(cb.getAttribute("data-v"));
    });
    syncSelLabel(kind);
    _dirty=true;   // 不立即刷新:关闭面板时统一提交(连续多选不打断)
  }
  function setAll(kind,on){
    document.querySelectorAll("#mon_panel_"+kind+" input[data-v]").forEach(function(cb){ cb.checked=on; });
    onCheck(kind);
  }
  function load(cb){
    var seq=++_seq, box=document.getElementById("mon_chart");
    if(box) box.innerHTML='<div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在汇总在制 Lot…</div></div>';
    fetch("/monitor/data?"+qs()).then(function(r){ return r.json(); }).then(function(d){
      if(seq!==_seq) return;
      cb(d);
    }).catch(function(){
      if(seq!==_seq) return;
      if(box) box.innerHTML='<div class="empty"><div class="ico">⚠️</div><div class="tt">数据加载失败</div><div class="sb">请稍后重试,或检查 MES数据库.csv 是否存在</div></div>';
    });
  }
  // 悬停提示:色块颜色与柱段一致;data-tip 用双引号包裹,内联样式一律单引号
  function _sw(c){ return '<i style=\'display:inline-block;width:11px;height:11px;border-radius:2px;background:'+c+';\'></i>'; }
  function _row(mk,v,sub){ return '<div style=\'display:flex;align-items:center;gap:8px;padding:1px 0;\'>'+mk+'<span style=\'font-weight:600;\'>'+v+'</span>'+(sub?'<span style=\'color:#94a3b8;\'>'+sub+'</span>':'')+'</div>'; }
  function chartH(){
    // 图表高度:视口剩余空间与上限取小(整页一屏放下,不用滚动;上限 560,
    // 大屏幕下尽量利用空间,不再留大量底部空白)
    var box=document.getElementById("mon_chart"); if(!box) return 400;
    var t=box.getBoundingClientRect().top;
    var h=window.innerHeight - t - 96;
    if(h > 560) h = 560;
    return Math.max(240, Math.floor(h / 10) * 10);
  }
  function render(d){
    _lastData=d;   // 缓存:窗口缩放时按新高度重绘
    var box=document.getElementById("mon_chart"); if(!box) return;
    var s=d.summary;
    var specsAll=d.specs;
    var specs=specsAll;
    // 序号范围截断:支持 "N"(第1~N道) 和 "A-B"(第A~B道);序号=流程原始顺序
    if(sel.seqFrom>0 || sel.seqTo>0){
      var from=sel.seqFrom>0?Math.max(0,sel.seqFrom-1):0;
      var to=sel.seqTo>0?Math.min(sel.seqTo,specsAll.length):specsAll.length;
      specs=specsAll.slice(from,to);
    }
    var sum=document.getElementById("mon_sum");
    if(sum) sum.innerHTML='在制 <b style="color:#0f172a;">'+fmt(s.lots)+'</b> lot · <b style="color:#0f172a;">'+fmt(s.qty)+'</b> 颗 · <b style="color:#0f172a;">'+s.wo+'</b> 工单 · 覆盖 <b style="color:#0f172a;">'+s.specs+'</b> 个工序'
      +(s.hold?(' · <span style="color:#ef4444;font-weight:700;">🔴 HOLD '+s.hold+' lot</span>'):'')
      +(_hlLot?(' · <span style="color:#b45309;background:#fef3c7;padding:2px 9px;border-radius:5px;font-weight:600;">📍 Lot '+esc(_hlLot.lot_id)+' → '+( _hlLot.spec_desc||_hlLot.spec)+' · '+_hlLot.status+' <a href="#" onclick="mon.clearHl();return false;" style="color:#b45309;text-decoration:none;">✕</a></span>'):'');
    if(!specs.length){
      box.innerHTML='<div class="empty"><div class="ico">🔍</div><div class="tt">没有匹配的在制 Lot</div><div class="sb">调整 OWNER / 型号 / 工单前缀 / 搜索条件试试</div></div>';
      return;
    }
    // 柱高 = 该工序在制【颗数(QTY)】(含各状态);maxBar=单序最大颗数 → 缩放基准(与统计页同口径)
    var H=chartH(), maxBar=1;
    specs.forEach(function(x){ var t=x.qty; if(t>maxBar) maxBar=t; });
    // CWIP:该工序及其之后所有在制【颗数(QTY)】之和(从右往左累加),左高右低递减
    var cwip=[], run=0;
    for(var i=specs.length-1;i>=0;i--){ run+=specs[i].qty; cwip[i]=run; }
    var maxC=cwip.length?cwip[0]:1; if(maxC<1) maxC=1;
    var bars='';
    specs.forEach(function(x, idx){
      var sp=String(x.spec).replace(/'/g,'');
      var has=x.lots>0;   // 空工序(完整流程占位):柱高 0、灰显、不可下钻
      function seg(n,c){ if(n<=0) return ''; var hh=Math.max(2,n/maxBar*H); return '<div style="width:100%;height:'+hh+'px;background:'+c+';"></div>'; }
      // 分段高度按各状态【颗数】堆叠(柱高=在制颗数)
      var stack=seg(x.run_q,'#22c55e')+seg(x.wi_q,'#ffdd00')+seg(x.wo_q,'#60a5fa')+seg(x.hold_q,'#ef4444');
      // 悬停提示:色块颜色与柱段完全一致;data-tip 用双引号包裹,内联样式一律单引号
      function _sw(c){ return '<i style=\'display:inline-block;width:11px;height:11px;border-radius:2px;background:'+c+';\'></i>'; }
      function _ln(c){ return '<i style=\'display:inline-block;width:15px;border-top:2px solid '+c+';position:relative;top:-3px;\'></i>'; }
      function _row(mk,v){ return '<div style=\'display:flex;align-items:center;gap:8px;padding:1px 0;\'>'+mk+'<span style=\'font-weight:600;\'>'+v+'</span></div>'; }
      // MOVE(本工序产出):= 本班次流入「下一道工序」的量(按颗数)
      var mo=(idx+1<specs.length)?(specs[idx+1].move||0):0;
      var qwip_q=x.wi_q+x.wo_q;   // QWIP 颗数 = 等待进站 + 等待出站
      var tip=has
        ? '<div style=\'font-weight:700;margin-bottom:4px;border-bottom:1px solid #e2e8f0;padding-bottom:3px;\'>'+esc(x.desc||x.spec)+'</div>'
          +_row(_sw('#22c55e'),fmt(x.run_q)+' 运行中')
          +_row(_sw('#ffdd00'),fmt(qwip_q)+' QWIP(等待)')
          +_row(_sw('#ef4444'),fmt(x.hold_q)+' HOLD')
          +_row(_ln('#2563eb'),fmt(mo)+' MOVE(产出)')
          +_row(_ln('#0f172a'),fmt(cwip[idx])+' CWIP累计')
          +'<div style=\'margin-top:3px;font-size:10px;color:#94a3b8;\'>共 '+x.lots+' 个lot · 数值=颗数</div>'
          +'<div style=\'margin-top:2px;font-size:10px;color:#94a3b8;text-align:center;\'>点击看明细</div>'
        : '<div style=\'font-weight:700;margin-bottom:4px;border-bottom:1px solid #e2e8f0;padding-bottom:3px;\'>'+esc(x.desc||x.spec)+'</div>'
          +'<div style=\'color:#94a3b8;padding:1px 0;\'>无在制 lot</div>';
      var hl=(_hlSpec && x.spec===_hlSpec)?1:0;
      bars += '<div class="wseg" data-tip="'+tip+'" '+(has?'onclick="mon.drill(\''+sp+'\')"':'')+' style="display:flex;flex-direction:column;align-items:center;flex:1 1 0;min-width:0;'+(has?'cursor:pointer;':'cursor:default;')+(hl?'outline:2px solid #f59e0b;outline-offset:-2px;border-radius:5px;background:#fef9ec;':'')+'">'
        + '<div class="wbar" style="display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:'+H+'px;width:80%;max-width:22px;border-bottom:1px solid #cbd5e1;overflow:visible;">'
          + (hl?'<div style="font-size:13px;line-height:1.1;color:#b45309;">📍</div>':'')
          + (has
            ? '<div style="line-height:1.05;text-align:center;white-space:nowrap;margin-bottom:1px;">'
              + '<div style="font-size:12px;color:#0f172a;font-weight:600;">'+fmt(x.qty)+'</div>'
              + (x.hold?'<div style="font-size:10px;color:#ef4444;font-weight:700;">🔴 HOLD '+x.hold+'</div>':'')
              + '</div>'
            : '')
          + '<div style="width:100%;display:flex;flex-direction:column;">'+stack+'</div>'
        + '</div>'
        + '<div style="font-size:'+(has?'12px':'10px')+';writing-mode:vertical-rl;text-orientation:mixed;height:'+(has?'110px':'80px')+';overflow:hidden;margin-top:3px;white-space:nowrap;font-weight:'+(has?'600':'400')+';color:'+(has?'#0f172a':'#94a3b8')+';">'+esc(x.desc||x.spec)+'</div>'
        + '<div style="font-size:11px;color:#64748b;margin-top:2px;font-weight:600;">'+(specsAll.indexOf(x)+1)+'</div>'
        + '</div>';
    });
    var N=specs.length;
    // MOVE 折线(蓝):取「下一道工序的本班次流入 lot 数」归到本工序(=本工序做下来多少);末道无下游 → 0
    var mpts=specs.map(function(x,i){ var mv=(i+1<specs.length)?(specs[i+1].move_lots||0):0; var y=H-(maxC?mv/maxC*H:H); return (i+0.5)+','+y.toFixed(1); }).join(' ');
    // CWIP 折线(黑):该工序及其之后在制 lot 数累计
    var pts=cwip.map(function(c,i){ var y=H-(c/maxC*H); return (i+0.5)+','+y.toFixed(1); }).join(' ');
    var svg='<svg viewBox="0 0 '+N+' '+H+'" preserveAspectRatio="none" style="position:absolute;left:0;top:0px;width:100%;height:'+H+'px;pointer-events:none;overflow:visible;">'
          + '<polyline points="'+mpts+'" fill="none" stroke="#2563eb" stroke-width="1.6" vector-effect="non-scaling-stroke" opacity="0.95"/>'
          + '<polyline points="'+pts+'" fill="none" stroke="#0f172a" stroke-width="1.4" vector-effect="non-scaling-stroke" opacity="0.7"/></svg>';
    // 左Y轴=在制 Lot 数、右Y轴=CWIP 累计 Lot 数(双轴) + 灰色网格
    var ticks=5, yaxis='', raxis='', grid='';
    var hsub=10;
    for(var hi=0;hi<=hsub;hi++){ var hy=H*hi/hsub; grid += '<div style="position:absolute;left:0;right:0;top:'+hy+'px;border-top:1px dashed rgba(120,120,120,.30);"></div>'; }
    for(var vi=0;vi<=N;vi++){ var vx=vi/N*100; grid += '<div style="position:absolute;top:0;left:'+vx+'%;height:'+H+'px;border-left:1px dashed rgba(120,120,120,.30);"></div>'; }
    for(var k=0;k<ticks;k++){
      var frac=k/(ticks-1), gy=H*frac;
      var valL=Math.round(maxBar*(1-frac));
      var valR=Math.round(maxC*(1-frac));
      yaxis += '<div style="position:absolute;right:4px;top:'+(gy-6)+'px;font-size:9px;color:#334155;line-height:1;">'+valL+'</div>';
      raxis += '<div style="position:absolute;left:4px;top:'+(gy-6)+'px;font-size:9px;color:#0f172a;line-height:1;">'+valR+'</div>';
      grid  += '<div style="position:absolute;left:0;right:0;top:'+gy+'px;border-top:1px '+(k===ticks-1?'solid rgba(100,100,100,.55)':'dashed rgba(120,120,120,.45)')+';"></div>';
    }
    box.innerHTML =
      '<div style="display:flex;align-items:stretch;">'
      + '<div style="position:relative;flex:0 0 32px;width:32px;">'+yaxis+'</div>'
      + '<div style="position:relative;flex:1 1 auto;border-left:1px solid #64748b;border-right:1px solid #64748b;background:#f0ffff;">'
        + '<div style="position:absolute;left:0;right:0;top:0;height:'+H+'px;background:linear-gradient(180deg,#fdffff 0%,#b0eeee 100%);"></div>'
        + grid
        + '<div style="position:relative;z-index:1;display:flex;align-items:flex-start;gap:1px;">'+bars+'</div>'
        + svg
      + '</div>'
      + '<div style="position:relative;flex:0 0 42px;width:42px;">'+raxis+'</div>'
      + '</div>'
      + '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;font-size:10px;color:#94a3b8;margin-top:3px;">'
      +   '<span>左轴=在制颗数(柱高) · 右轴=CWIP 累计颗数</span>'
      +   '<span>柱高=在制颗数(QTY) · 柱顶=颗数 · 绿运行/黄等待进站/蓝等待出站/红HOLD · <span style="color:#2563eb;">蓝线=MOVE(产出·颗)</span> · 黑线=CWIP累计 · 搜索时显示完整流程 · 点柱看明细</span>'
      + '</div>';
  }
  function drill(spec){
    var mask=document.getElementById("monMask"), box=document.getElementById("monBox"), body=document.getElementById("monBody");
    document.getElementById("monTitle").textContent='工序: '+spec;
    mask.style.display='block'; box.style.display='flex';
    body.innerHTML='<div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在加载明细…</div></div>';
    fetch("/monitor/lots?spec="+encodeURIComponent(spec)+"&"+qs()).then(function(r){ return r.json(); }).then(function(lots){
      if(!lots.length){ body.innerHTML='<div class="empty"><div class="tt">无匹配 Lot</div></div>'; return; }
      // 与统计分析页下钻弹窗同款排布:表格居中、表头吸顶灰底、单元格居中、行 hover
      function stBadge(s){
        var map={'等待进站':['#fde047','#1f2937'],'等待出站':['#60a5fa','#1f2937'],
                 '运行中':['#bbf7d0','#15803d'],'HOLD':['#fecaca','#1f2937'],'已入库':['#bfdbfe','#1f2937']};
        var c=map[s]||['#f1f5f9','#475569'];
        return '<span style="padding:1px 6px;border-radius:3px;background:'+c[0]+';color:'+c[1]+';white-space:nowrap;">'+esc(s)+'</span>';
      }
      var rows=lots.map(function(l){
        var isHold=l.status==='HOLD';
        var extra=isHold && (l.hold_reason||l.comment)
          ? '<div style="color:#ef4444;font-size:11px;max-width:280px;white-space:normal;word-break:break-word;">'+(l.hold_reason||'')+(l.comment?' · '+esc(l.comment):'')+'</div>'
          : '<span style="color:#cbd5e1;">—</span>';
        var bg=isHold?'#fef2f2':'';
        return '<tr style="border-top:1px solid #f1f5f9;'+(bg?'background:'+bg+';':'')+'" '
          + 'onmouseover="this.style.background=\''+(isHold?'#fef2f2':'#f8fafc')+'\'" '
          + 'onmouseout="this.style.background=\''+bg+'\'">'
          + '<td style="padding:6px 15px;text-align:center;font-weight:600;color:#0f172a;">'+esc(l.full_wo)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+esc(l.lot_id)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+esc(l.psize)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;color:#64748b;">'+esc(l.product)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+esc(l.owner)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+(esc(l.eqp)||'-')+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+stBadge(l.status)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;font-weight:600;">'+fmt(l.qty)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+esc(l.run_time)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+esc(l.wait_time)+'</td>'
          + '<td style="padding:6px 15px;text-align:center;">'+esc(l.hold_time)+'</td>'
          + '<td style="padding:6px 12px;text-align:center;">'+extra+'</td>'
          + '<td style="padding:6px 15px;text-align:center;"><span title="纯加工时间(不含等待/停机),算法=当前批颗数÷该设备实测UPH−已运行">'
          + (l.status==='HOLD'
              ? '<span style="color:#ef4444;">已暂停</span>'
              : (l.eta_at
                  ? '<span style="color:#2563eb;font-weight:600;">'+fmtEta(l.eta_at)+'</span>'
                     +'<div style="font-size:10px;color:#94a3b8;">'+esc(l.eta_basis)+'实测</div>'
                  : '<span style="color:#cbd5e1;">—</span>'))
          + '</span></td>'
          + '</tr>';
      }).join('');
      var th='text-align:center;padding:6px 12px;background:#eef2f7;position:sticky;top:0;z-index:1;user-select:none;white-space:nowrap;';
      body.innerHTML='<div style="overflow:auto;max-height:62vh;">'
        + '<table style="border-collapse:collapse;text-align:center;font-size:13px;margin:0 auto;white-space:nowrap;">'
        + '<thead><tr style="color:#475569;">'
        + '<td style="'+th+'">工单号</td>'
        + '<td style="'+th+'">Lot号</td>'
        + '<td style="'+th+'">型号</td>'
        + '<td style="'+th+'">料号</td>'
        + '<td style="'+th+'">OWNER</td>'
        + '<td style="'+th+'">设备</td>'
        + '<td style="'+th+'">状态</td>'
        + '<td style="'+th+'">颗数</td>'
        + '<td style="'+th+'">运行</td>'
        + '<td style="'+th+'">等待</td>'
        + '<td style="'+th+'">HOLD时长</td>'
        + '<td style="'+th+'">HOLD原因/备注</td>'
        + '<td style="'+th+'">预计加工完成</td>'
        + '</tr></thead><tbody>'+rows+'</tbody></table>'
        + '</div>'
        + '<div style="margin-top:8px;font-size:12px;color:#64748b;text-align:center;">共 '+lots.length+' 个 Lot</div>';
    }).catch(function(){ body.innerHTML='<div class="empty"><div class="tt">明细加载失败</div></div>'; });
  }
  function rebuildPanel(kind, vals){
    // 型号切换后:面板重建为该型号下出现的选项(默认全选)
    var all=kind==="owners"?(ALL_OWNERS=vals):(ALL_PRODUCTS=vals);
    var cur=selOf(kind);
    cur.length=0; vals.forEach(function(v){ cur.push(v); });
    var list=document.getElementById("mon_panel_"+kind).querySelector(".mon-owners-list");
    if(list) list.innerHTML=vals.map(function(o){
      return '<label style="display:block;padding:4px 8px;cursor:pointer;border-radius:5px;font-size:13px;color:#334155;">'
        + '<input type="checkbox" data-v="'+esc(o)+'" checked style="margin-right:6px;">'+esc(o)+'</label>';
    }).join('') || '<div style="padding:10px;font-size:12px;color:#94a3b8;">该型号暂无数据</div>';
    syncSelLabel(kind);
  }
  function rebuildOwners(owners){ rebuildPanel("owners", owners); }
  function rebuildProducts(products){ rebuildPanel("products", products); }
  function onModel(){
    sel.models=document.getElementById("mon_model").value;
    fetch("/monitor/options?models="+encodeURIComponent(sel.models)).then(function(r){ return r.json(); }).then(function(d){
      if(d && d.owners) rebuildOwners(d.owners);
      if(d && d.products) rebuildProducts(d.products);
      load(render);
    }).catch(function(){ load(render); });
  }
  function switchModel(model){
    // 切换型号下拉 + 联动刷新 OWNER/产品型号面板(保留当前 kw 过滤)
    sel.models=model;
    var msel=document.getElementById("mon_model");
    if(msel) msel.value=model;
    fetch("/monitor/options?models="+encodeURIComponent(model)).then(function(r){ return r.json(); }).then(function(d){
      if(d && d.owners) rebuildOwners(d.owners);
      if(d && d.products) rebuildProducts(d.products);
      load(render);
    }).catch(function(){ load(render); });
  }
  function jumpLot(info){
    // 完整 Lot 命中:自动切到对应型号,确保该 lot 的 OWNER 已选,清空 kw,图上高亮该工序
    _hlLot=info; _hlSpec=info.spec||null;
    sel.kw='';
    document.getElementById("mon_search").value='';
    var msel=document.getElementById("mon_model");
    if(msel && msel.value!==info.model){
      switchModel(info.model);
    } else {
      // 型号相同:若该 lot 的 owner 未选中则补选(否则高亮会被筛选掉)
      if(sel.owners.indexOf(info.owner)<0){
        sel.owners.push(info.owner);
        document.querySelectorAll("#mon_panel_owners input[data-v]").forEach(function(cb){
          if(cb.getAttribute("data-v")===info.owner) cb.checked=true;
        });
        syncSelLabel();
      }
      load(render);
    }
  }
  function clearHl(){
    _hlLot=null; _hlSpec=null;
    var e=document.getElementById("mon_search"); if(e) e.value='';
    sel.kw='';
    load(render);
  }
  function onSearch(){
    var e=document.getElementById("mon_search");
    var v=(e.value||"").trim();
    clearTimeout(_t1);
    _t1=setTimeout(function(){
      if(!v){ sel.kw=''; load(render); return; }
      // 先试完整 Lot 号精确匹配 → 命中则自动跳转到对应型号并高亮
      fetch("/monitor/lot?lot="+encodeURIComponent(v)).then(function(r){ return r.json(); }).then(function(d){
        if(d && d.ok && d.lot && d.lot.spec){ jumpLot(d.lot); return; }
        _hlLot=null; _hlSpec=null;
        sel.kw=v;
        // 自动匹配型号:输入可识别为 型号/产品型号/工单号 → 自动切换型号(图直接显示)
        fetch("/monitor/match?kw="+encodeURIComponent(v)).then(function(r){ return r.json(); }).then(function(m){
          if(m && m.model && m.model!==sel.models){
            switchModel(m.model);   // 切型号(保留 kw 过滤)
          } else {
            load(render);
          }
        }).catch(function(){ load(render); });
      }).catch(function(){ _hlLot=null; _hlSpec=null; sel.kw=v; load(render); });
    },300);
  }
  var _t1=null;
  function onMaxSeq(){
    // 序号范围截断:支持 "N"(第1~N道) 和 "A-B"(第A~B道);前端截断,不重新请求
    var e=document.getElementById("mon_maxseq");
    var v=(e.value||"").trim();
    var from=0, to=0;
    var m=v.match(/^(\d+)\s*-\s*(\d+)$/);
    if(m){ from=parseInt(m[1],10); to=parseInt(m[2],10); }
    else {
      var n=parseInt(v,10);
      if(!isNaN(n)&&n>0){ from=1; to=n; }
    }
    sel.seqFrom=from>0?from:0;
    sel.seqTo=(to>0&&to>=from)?to:0;
    if(_lastData) try{ render(_lastData); }catch(err){}
  }
  function filterPanel(kind, kw){
    // 面板内搜索框:按关键字过滤 checkbox 显示(纯前端,不触发刷新)
    kw=(kw||"").toLowerCase().trim();
    document.querySelectorAll("#mon_panel_"+kind+" input[data-v]").forEach(function(cb){
      var v=cb.getAttribute("data-v")||"";
      var lbl=cb.closest("label");
      if(lbl) lbl.style.display=(!kw || v.toLowerCase().indexOf(kw)>=0) ? "" : "none";
    });
  }
  function uploadFlow(inp){
    // 右上角 Flow 上传:文件名去扩展名=型号,列 SEQNO/SPECNAME/STEPNAME(同后台 CT 表管理)
    if(!inp || !inp.files || !inp.files[0]) return;
    var fd=new FormData();
    fd.append("file", inp.files[0]);
    fetch("/monitor/upload_flow", {method:"POST", body:fd})
      .then(function(r){
        if(r.redirected) return {ok:false, msg:"未登录:请先到「后台管理」登录后再上传 Flow"};
        return r.json().catch(function(){ return {ok:false,msg:"上传失败(检查文件格式/列名 SEQNO SPECNAME)"}; });
      })
      .then(function(d){
        wipToast(d.msg || (d.ok? '上传成功':'上传失败'), d.ok?'ok':'err');
        if(d.ok) load(render);   // 重新取骨架(Flow 优先)
        if(inp) inp.value="";
      }).catch(function(){ wipToast("上传失败:网络错误"); if(inp) inp.value=""; });
  }
  function reset(){
    _hlLot=null; _hlSpec=null;
    document.getElementById("mon_model").value="MB12";
    var e=document.getElementById("mon_search"); e.value=''; sel.kw='';
    var me=document.getElementById("mon_maxseq"); if(me){ me.value=''; sel.seqFrom=0; sel.seqTo=0; }
    onModel();   // 型号回 MB12 + owner 联动重载
  }
  // 点下拉面板外部 → 关闭面板;滚动/窗口缩放时也关闭(fixed 面板会错位)。
  // 注意:面板已移到 body 下,不在 .mon-sel 容器内,点击面板自身(含搜索框)不得关闭
  document.addEventListener("click", function(e){
    var t=e.target;
    if(t && t.closest
       && (t.closest(".mon-sel") || t.closest(".mon-panel"))) return;
    closePanels();
  });
  var _main=document.querySelector(".main");
  if(_main) _main.addEventListener("scroll", closePanels);
  window.addEventListener("resize", closePanels);
  // 面板 checkbox 勾选/取消 → 实时联动(事件委托:重建面板后依然生效)
  ["owners","products"].forEach(function(k){
    var panel=document.getElementById("mon_panel_"+k);
    if(!panel) return;
    panel.addEventListener("change", function(e){
      if(e.target && e.target.getAttribute && e.target.getAttribute("data-v")!=null) onCheck(k);
    });
  });
  return {render:render, drill:drill, togglePanel:togglePanel, onCheck:onCheck, setAll:setAll,
          onModel:onModel, onSearch:onSearch, reset:reset, clearHl:clearHl, switchModel:switchModel, load:load,
          togglePanel:togglePanel, onCheck:onCheck, setAll:setAll, filterPanel:filterPanel,
          onMaxSeq:onMaxSeq, uploadFlow:uploadFlow,
          lastData:function(){ return _lastData; }};
})();
try { mon.load(mon.render); } catch(e) {}
window.addEventListener('load', function(){ try{ mon.load(mon.render); }catch(e){} });
// 窗口缩放:图表高度重新计算并按缓存数据重绘
window.addEventListener('resize', function(){
  var d=mon.lastData ? mon.lastData() : null;
  if(d) try{ mon.render(d); }catch(e){}
});
// 悬停提示(tooltip)动态注入样式(同 WIP 图机制)
(function(){
  var box=document.getElementById('mon_chart'); if(!box) return;
  var tip=document.getElementById('mon_tip');
  if(!tip){ tip=document.createElement('div'); tip.id='mon_tip'; document.body.appendChild(tip); }
  if(!document.getElementById('mon_tip_css')){
    var st=document.createElement('style'); st.id='mon_tip_css';
    st.textContent='.mon-sel{position:relative;display:inline-block;}'
      +'.mon-panel{position:absolute;top:calc(100% + 5px);left:0;z-index:40;background:#fff;border:1px solid #cbd5e1;border-radius:8px;box-shadow:0 10px 30px rgba(0,0,0,.15);width:200px;}'
      +'.mon-panel label:hover{background:#f1f5f9;}'
      +'.wseg{transition:filter .15s ease;}'
      +'.wseg:hover .wbar{filter:brightness(1.06) saturate(1.05);box-shadow:0 4px 14px rgba(15,23,42,.18);border-radius:3px;}'
      +'#mon_tip{display:none;position:fixed;z-index:60;pointer-events:none;background:#fff;color:#0f172a;font-size:12px;line-height:1.5;padding:8px 12px;border:1px solid #cbd5e1;border-radius:7px;box-shadow:0 6px 18px rgba(0,0,0,.18);white-space:nowrap;transform:translate(-50%,-100%);opacity:0;transition:opacity .1s ease;}'
      +'#mon_tip::after{content:"";position:absolute;left:50%;bottom:-6px;transform:translateX(-50%);border:6px solid transparent;border-top-color:#fff;border-bottom:0;}';
    document.head.appendChild(st);
  }
  function place(e){ tip.style.left=e.clientX+'px'; tip.style.top=(e.clientY-12)+'px'; }
  box.addEventListener('mouseover', function(e){
    var t=e.target.closest && e.target.closest('.wseg'); if(!t) return;
    var tx=t.getAttribute('data-tip')||''; if(!tx) return;
    tip.innerHTML=tx; tip.style.display='block'; place(e);
    requestAnimationFrame(function(){ tip.style.opacity='1'; });
  });
  box.addEventListener('mousemove', function(e){
    if(tip.style.display==='none') return;
    if(e.target.closest && e.target.closest('.wseg')) place(e);
  });
  box.addEventListener('mouseout', function(e){
    var t=e.target.closest && e.target.closest('.wseg'); if(!t) return;
    var to=e.relatedTarget;
    if(to && to.closest && to.closest('.wseg')) return;
    tip.style.opacity='0'; tip.style.display='none';
  });
})();
</script>
"""


def _mon_model_select(opts, default="MB12"):
    """工序监控页的型号单选下拉选项(默认选中 MB12,不存在则选第一个)。"""
    default = default if default in opts else (opts[0] if opts else "")
    return "".join(
        f'<option value="{_html.escape(m)}"{" selected" if m == default else ""}>{_html.escape(m)}</option>'
        for m in opts)


def _mon_sel_panel(kind, title, opts, search=False):
    """工序监控页的多选下拉面板 HTML(checkbox 列表,默认全选)。
    search=True 时面板头部加搜索框(过滤选项,如产品型号输 2070)。"""
    labels = "".join(
        f'<label style="display:block;padding:4px 8px;cursor:pointer;border-radius:5px;font-size:13px;color:#334155;">'
        f'<input type="checkbox" data-v="{_html.escape(o)}" checked style="margin-right:6px;">{_html.escape(o)}</label>'
        for o in opts)
    search_html = ""
    if search:
        search_html = (f'<div style="padding:6px 8px;border-bottom:1px solid #e2e8f0;">'
                       f'<input id="mon_search_{kind}" oninput="mon.filterPanel(\'{kind}\',this.value)" '
                       f'placeholder="搜{title},如 2070…" '
                       f'style="width:100%;box-sizing:border-box;padding:4px 8px;border:1px solid #cbd5e1;'
                       f'border-radius:5px;font-size:12px;"></div>')
    return (f'<div id="mon_panel_{kind}" class="mon-panel" style="display:none;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;'
            f'border-bottom:1px solid #e2e8f0;background:#f8fafc;border-radius:8px 8px 0 0;">'
            f'<span style="font-size:12px;color:#475569;font-weight:600;">{title}</span>'
            f'<span><button type="button" onclick="mon.setAll(\'{kind}\',true)" '
            f'style="padding:2px 8px;border:1px solid #cbd5e1;background:#fff;color:#475569;border-radius:5px;'
            f'font-size:12px;cursor:pointer;">全选</button> '
            f'<button type="button" onclick="mon.setAll(\'{kind}\',false)" '
            f'style="padding:2px 8px;border:1px solid #cbd5e1;background:#fff;color:#475569;border-radius:5px;'
            f'font-size:12px;cursor:pointer;">清空</button></span></div>'
            + search_html
            + f'<div class="mon-owners-list" style="max-height:230px;overflow:auto;padding:6px 4px;">{labels}</div></div>')


def render_monitor():
    """工序监控页:按 型号(PACKAGESIZE)×OWNERNAME×工单前缀 过滤,
    展示在制 Lot 分布在各工序(堆叠柱+下钻),数据直接读 MES数据库.csv 快照(monitor.py),
    不依赖系统录入工单,每次 MES 同步刷新 CSV 后自动更新。"""
    import monitor
    # 初始筛选面板按默认型号(MB12)联动:OWNER/产品型号只显示该型号包含的,不全表
    _all_models = monitor.options()["models"]
    _def = "MB12" if "MB12" in _all_models else (_all_models[0] if _all_models else "")
    opts = monitor.options(models=[_def]) if _def else monitor.options()
    # 型号下拉始终显示全部型号(过滤条件只影响 OWNER/产品型号候选)
    model_opts = _mon_model_select(_all_models, default=_def or "MB12")
    owner_opts = _mon_sel_panel("owners", "OWNERNAME", opts["owners"])
    product_opts = _mon_sel_panel("products", "产品型号", opts["products"], search=True)
    js = (_MONITOR_JS
          .replace("__ALL_OWNERS__",
                   '[' + ",".join(f'"{_html.escape(o)}"' for o in opts["owners"]) + ']')
          .replace("__ALL_PRODUCTS__",
                   '[' + ",".join(f'"{_html.escape(p)}"' for p in opts["products"]) + ']'))
    return layout("monitor", "工序监控", f"""
    <div class="page-title">🔍 实时WIP</div>
    <div class="page-sub">直接读取 MES 快照:按 OWNERNAME × 产品型号 × 工单前缀实时跟踪在制 Lot 分布在各工序。柱高=在制颗数,点柱看该工序 Lot 明细。</div>
    <div class="panel" style="padding:14px 18px;">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <label style="font-size:13px;color:#475569;">型号
          <select id="mon_model" onchange="mon.onModel()" title="单选型号,一次只看一个"
                  style="margin-left:4px;padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;max-width:170px;">
            {model_opts}
          </select>
        </label>
        <span class="mon-sel">
          <button type="button" id="mon_btn_products" onclick="mon.togglePanel('products')" title="点开多选产品型号(默认全选,显示当前型号包含的)"
                  style="padding:6px 12px;border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">产品型号: 全部</button>
          {product_opts}
        </span>
        <span class="mon-sel">
          <button type="button" id="mon_btn_owners" onclick="mon.togglePanel('owners')" title="点开多选 OWNERNAME"
                  style="padding:6px 12px;border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">OWNERNAME: 全部</button>
          {owner_opts}
        </span>
        <input id="mon_search" oninput="mon.onSearch()" placeholder="搜 工单号 / Lot号 / 型号(输入 04 看 04 开头工单)"
               style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;width:280px;">
        <input id="mon_maxseq" oninput="mon.onMaxSeq()" placeholder="工序序号,如 2-30"
               title="显示指定区间的工序:输入 N=第1~N道;输入 A-B=第A~B道;清空=全部"
               style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;width:150px;">
        <button type="button" onclick="mon.reset()" title="恢复默认:全选 + 清空前缀和搜索" style="padding:6px 10px;border:1px solid #e2e8f0;background:#f8fafc;color:#475569;border-radius:6px;font-size:13px;cursor:pointer;">↻ 重置</button>
        <button type="button" onclick="mon.load(mon.render)" title="手动刷新(同步后点这里)" style="padding:6px 12px;border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">刷新</button>
        <button type="button" onclick="document.getElementById('mon_flow_file').click()" title="上传流程 Flow 表(文件名=型号,列 SEQNO/SPECNAME/STEPNAME)"
                style="padding:6px 12px;border:1px solid #f59e0b;background:#fff;color:#b45309;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">📤 Flow上传</button>
        <input id="mon_flow_file" type="file" accept=".xlsx,.csv" style="display:none;" onchange="mon.uploadFlow(this)">
      </div>
      <div id="mon_sum" style="margin-top:10px;font-size:13px;color:#334155;"></div>
    </div>
    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px 16px;">
        <h3 style="margin:0;">在制工序分布 <span id="mon_sub" style="font-size:12px;color:#94a3b8;font-weight:normal;"></span></h3>
      </div>
      <div id="mon_chart" style="margin-top:12px;"><div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在汇总在制 Lot…</div></div></div>
    </div>
    <!-- 工序 Lot 明细弹窗 -->
    <div id="monMask" onclick="document.getElementById('monBox').style.display='none';this.style.display='none';" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49;"></div>
    <div id="monBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:max-content;max-width:92vw;min-width:min(640px,92vw);max-height:86vh;background:#fff;border-radius:12px;
         z-index:50;box-shadow:0 20px 60px rgba(0,0,0,.3);overflow:hidden;flex-direction:column;">
      <div style="position:relative;padding:16px 24px 6px;flex:0 0 auto;">
        <h3 id="monTitle" style="margin:0;text-align:center;font-size:15px;">工序明细</h3>
        <div id="monHint" style="text-align:center;font-size:12px;color:#94a3b8;margin-top:4px;"></div>
        <button onclick="document.getElementById('monBox').style.display='none';document.getElementById('monMask').style.display='none';" style="position:absolute;top:14px;right:20px;border:none;background:none;cursor:pointer;font-size:14px;color:#64748b;">关闭</button>
      </div>
      <div id="monBody" style="font-size:13px;color:#64748b;text-align:center;overflow:auto;padding:0 24px 20px;flex:1 1 auto;">加载中…</div>
    </div>
    {js}
    """)
    """工序监控页:按 型号(PACKAGESIZE)×OWNERNAME×工单前缀 过滤,
    展示在制 Lot 分布在各工序(堆叠柱+下钻),数据直接读 MES数据库.csv 快照(monitor.py),
    不依赖系统录入工单,每次 MES 同步刷新 CSV 后自动更新。"""
    import monitor
    opts = monitor.options()
    model_opts = "".join(
        f'<option value="{_html.escape(m)}">{_html.escape(m)}</option>'
        for m in opts["models"])
    owner_btns = "".join(
        f'<button type="button" class="mon-owner{" active" if o in ("R", "R-C") else ""}" '
        f'data-owner="{_html.escape(o)}" onclick="mon.toggleOwner(this)">{_html.escape(o)}</button>'
        for o in opts["owners"])
    js = _MONITOR_JS
    return layout("monitor", "工序监控", f"""
    <div class="page-title">🔍 工序监控</div>
    <div class="page-sub">直接读取 MES 快照:按 OWNERNAME × 型号 × 工单前缀跟踪在制 Lot 分布在各工序。柱高=在制颗数,点柱看该工序 Lot 明细。</div>
    <div class="panel" style="padding:14px 18px;">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <span style="font-weight:600;font-size:13px;color:#0f172a;">OWNER</span>
        {owner_btns}
        <span style="width:1px;height:20px;background:#e2e8f0;"></span>
        <label style="font-size:13px;color:#475569;">型号
          <select id="mon_model" onchange="mon.onModel()" style="margin-left:4px;padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;">
            <option value="">全部</option>
            {model_opts}
          </select>
        </label>
        <input id="mon_prefix" oninput="mon.onPrefix()" placeholder="工单前缀,如 04" value="04"
               style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;width:140px;">
        <input id="mon_search" oninput="mon.onSearch()" placeholder="搜 工单号 / Lot号 / 型号"
               style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;width:210px;">
        <button type="button" onclick="mon.reset()" title="前缀恢复 04、清空搜索" style="padding:6px 10px;border:1px solid #e2e8f0;background:#f8fafc;color:#475569;border-radius:6px;font-size:13px;cursor:pointer;">↻ 重置</button>
        <button type="button" onclick="mon.load(mon.render)" title="手动刷新(同步后点这里)" style="padding:6px 12px;border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600;">刷新</button>
      </div>
      <div id="mon_sum" style="margin-top:10px;font-size:13px;color:#334155;"></div>
    </div>
    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px 16px;">
        <h3 style="margin:0;">在制工序分布 <span id="mon_sub" style="font-size:12px;color:#94a3b8;font-weight:normal;"></span></h3>
      </div>
      <div id="mon_chart" style="margin-top:12px;"><div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在汇总在制 Lot…</div></div></div>
    </div>
    <!-- 工序 Lot 明细弹窗 -->
    <div id="monMask" onclick="document.getElementById('monBox').style.display='none';this.style.display='none';" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:49;"></div>
    <div id="monBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:max-content;max-width:92vw;min-width:min(640px,92vw);max-height:86vh;background:#fff;border-radius:12px;
         z-index:50;box-shadow:0 20px 60px rgba(0,0,0,.3);overflow:hidden;flex-direction:column;">
      <div style="position:relative;padding:16px 24px 6px;flex:0 0 auto;">
        <h3 id="monTitle" style="margin:0;text-align:center;font-size:15px;">工序明细</h3>
        <div id="monHint" style="text-align:center;font-size:12px;color:#94a3b8;margin-top:4px;"></div>
        <button onclick="document.getElementById('monBox').style.display='none';document.getElementById('monMask').style.display='none';" style="position:absolute;top:14px;right:20px;border:none;background:none;cursor:pointer;font-size:14px;color:#64748b;">关闭</button>
      </div>
      <div id="monBody" style="font-size:13px;color:#64748b;text-align:center;overflow:auto;padding:0 24px 20px;flex:1 1 auto;">加载中…</div>
    </div>
    {js}
    """)


def render_equip():
    """设备能力统计页:左侧型号导航,右侧工序清单(行内展开设备/样本明细)。
    数据由 equip.observe_snapshot 跨快照定稿累积:批次离开工序时用最终 RUNTIME
    计算 UPH = QTY÷运行时长,按型号×工序×设备分组取中位数。60 天滚动保留
    (与 CT 实测学习 ct_learn 样本窗口同口径)。"""
    return layout("equip", "设备能力", """
    <div class="page-title">🏭 设备能力统计</div>
    <div class="page-sub">UPH = 批次数量 ÷ 累计运行时长(小时) 的中位数 —— 纯加工产出峰值,
      不含等待/换线/停机;HOLD 冻结与不足 6 分钟的截断碎片不计。点工序行展开设备,再点设备行看样本明细。</div>

    <div class="eq-main">
      <div class="eq-side">
        <div class="eq-search">
          <input id="eqpKw" placeholder="设备号反查" onkeydown="if(event.key==='Enter')searchEqp()">
          <button class="op-edit" onclick="searchEqp()">🔍</button>
        </div>
        <div class="eq-side-title">型号分类 <span id="modelCnt" class="eq-badge"></span></div>
        <div id="modelList" class="eq-side-list"></div>
      </div>
      <div class="eq-content">
        <div id="crumbs" class="eq-crumbs" style="display:none;"></div>
        <div id="detail"></div>
      </div>
    </div>

    <style>
    .eq-main{display:flex;gap:18px;align-items:flex-start;
      height:calc(100vh - 145px);}          /* 顶部标题固定,仅右侧内容滚动 */
    .eq-side{width:245px;flex-shrink:0;height:100%;overflow-y:auto;}
    .eq-content{flex:1;min-width:0;height:100%;display:flex;flex-direction:column;padding-right:8px;}
    .eq-search{display:flex;gap:6px;margin-bottom:12px;}
    .eq-search input{flex:1;height:36px;padding:8px 11px;border:1px solid #cbd5e1;border-radius:8px;
      font-size:13px;min-width:0;}
    .eq-search input:focus{outline:none;border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12);}
    .eq-search button{width:36px;height:36px;flex-shrink:0;border-radius:8px;font-size:14px;cursor:pointer;
      border:1px solid #2563eb;background:#2563eb;color:#fff;line-height:1;}
    .eq-search button:hover{background:#1d4ed8;}
    .eq-side-title{font-size:12px;color:#64748b;font-weight:600;margin:2px 2px 8px;display:flex;align-items:center;gap:6px;}
    .eq-side-list{background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;
      box-shadow:0 1px 3px rgba(15,23,42,.06);}
    .eq-m{border-bottom:1px solid #f1f5f9;cursor:pointer;transition:background .12s;}
    .eq-m:last-child{border-bottom:none;}
    .eq-m:hover{background:#f8fafc;}
    .eq-m.on{background:#eff6ff;}
    .eq-m.on .eq-m-row{border-left:3px solid #2563eb;}
    .eq-m-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px 5px;border-left:3px solid transparent;}
    .eq-m-name{font-size:14px;font-weight:700;color:#0f172a;}
    .eq-m-uph{font-size:13px;font-weight:700;color:#2563eb;font-variant-numeric:tabular-nums;}
    .eq-m-sub{font-size:11px;color:#94a3b8;padding:0 14px 9px 17px;}
    .eq-crumbs{font-size:13px;color:#475569;margin-bottom:12px;min-height:22px;}
    .eq-crumbs a{color:#2563eb;cursor:pointer;text-decoration:none;font-weight:600;}
    .eq-crumbs a:hover{text-decoration:underline;}
    .eq-table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;
      box-shadow:0 1px 3px rgba(15,23,42,.06);}
    .eq-table th{background:#f8fafc;color:#64748b;font-size:12px;font-weight:600;text-align:left;
      padding:10px 16px;border-bottom:1px solid #e2e8f0;letter-spacing:.02em;white-space:nowrap;}
    .eq-table td{padding:10px 16px;font-size:13px;line-height:1.5;border-bottom:1px solid #f1f5f9;
      color:#1e293b;font-variant-numeric:tabular-nums;}
    .eq-table tbody tr:last-child td{border-bottom:none;}
    .eq-table tbody tr{cursor:pointer;transition:background .12s;}
    .eq-table tbody tr:hover td{background:#f0f6ff;}
    .eq-uph{font-weight:700;color:#2563eb;text-align:center;font-variant-numeric:tabular-nums;}
    .eq-pill{display:inline-block;background:#eff6ff;color:#1d4ed8;border-radius:20px;padding:3px 13px;
      font-size:12px;font-weight:600;}
    .eq-badge{display:inline-block;background:#f1f5f9;color:#64748b;border-radius:10px;padding:2px 9px;font-size:11px;}
    .eq-empty{color:#94a3b8;font-size:13px;padding:30px;text-align:center;background:#fff;
      border-radius:12px;border:1px dashed #e2e8f0;}
    .eq-hd{display:flex;justify-content:space-between;align-items:center;margin:2px 0 12px;flex-wrap:wrap;gap:8px;}
    .eq-hd h3{font-size:15px;margin:0;}
    .eq-tip{font-size:12px;color:#94a3b8;margin:-4px 0 12px;}
    #detail{flex:1;min-height:180px;overflow-y:auto;}   /* 工序清单窗口:占满右侧剩余高度(视口实时配比),
                                                         屏幕越高窗口越大;内部滚动,表头(列名)吸顶 */
    #detail > table > thead th{position:sticky;top:0;z-index:2;box-shadow:0 1px 0 #e2e8f0;}
    .spec-open{background:#eff6ff !important;}
    .spec-open td{background:#eff6ff;}
    .spec-open:hover td{background:#dbeafe !important;}
    .eqp-det{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums;}
    .eqp-det th{text-align:left;padding:5px 10px;border-bottom:1px solid #e2e8f0;color:#64748b;font-weight:600;white-space:nowrap;}
    .eqp-det td{padding:5px 10px;border-bottom:1px solid #f1f5f9;cursor:default;line-height:1.5;}
    .eq-caret{display:inline-block;width:16px;color:#64748b;transition:transform .15s;}
    .eq-caret.open{transform:rotate(90deg);}
    .eq-spec-cell{font-weight:600;}
    </style>

    <script>
    var _model=null,_product=null,_specList=[],_eqpData={},_matchList=[];
    function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
    function unh(s){return String(s==null?'':s).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');}
    function fmtUPH(v){return v==null?'—':(v>=100?Math.round(v):v.toFixed(1));}
    function fmtTime(ts){if(!ts)return '—';var d=new Date(ts*1000);
      return (d.getFullYear())+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')
        +' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
    function eqErr(e){return '<div class="eq-empty" style="color:#dc2626;">数据加载失败: '
      +esc(String(e&&e.message||e))+' (请检查控制台/网络)</div>';}

    function goHome(){_model=null;_product=null;renderCrumbs();loadModels();}
    function openModel(m){
      m=unh(m);
      _model=m;_product=null;_specList=[];_eqpData={};
      document.querySelectorAll('.eq-m.on').forEach(function(x){x.classList.remove('on');});
      var mEl=document.getElementById('m-'+m);
      if(mEl)mEl.classList.add('on');
      renderCrumbs();loadSpecs();
    }
    function setProduct(p){_product=p?unh(p):null;_specList=[];_eqpData={};renderCrumbs();loadSpecs();}

    function renderCrumbs(){
      var c=document.getElementById('crumbs');
      var h='<a onclick="goHome()">设备能力</a>';
      if(_model)h+=' › '+esc(_model);
      c.innerHTML=h;c.style.display='block';
      loadProducts();
    }
    function loadModels(){
      fetch('/equip/data?models=1').then(r=>r.json()).then(d=>{
        var body=d.models||[];
        document.getElementById('modelList').innerHTML=body.length?body.map(m=>
          '<div class="eq-m'+(m.model===_model?' on':'')+'" id="m-'+esc(m.model)+'" onclick="openModel(\\''+esc(m.model)+'\\')">'
            +'<div class="eq-m-row"><span class="eq-m-name">'+esc(m.model)+'</span>'
            +'<span class="eq-m-uph">'+fmtUPH(m.uph)+'</span></div>'
            +'<div class="eq-m-sub">'+m.eqps+' 台 · '+m.samples+' 样本</div>'
          +'</div>').join(''):'<div class="eq-s-empty">暂无型号样本</div>';
        document.getElementById('modelCnt').textContent=body.length;
        if(!_model)document.getElementById('detail').innerHTML=
          '<div class="eq-empty">从左侧选择型号,查看工序清单与设备 UPH 明细</div>';
      }).catch(function(e){document.getElementById('modelList').innerHTML=eqErr(e);});
    }
    function loadProducts(){
      fetch('/equip/data?model='+encodeURIComponent(_model||'')+'&products=1').then(r=>r.json()).then(d=>{
        var ps=d.products||[];
        var host=document.getElementById('crumbProducts');
        if(!host){host=document.createElement('span');host.id='crumbProducts';
          host.style.marginLeft='10px';document.getElementById('crumbs').appendChild(host);}
        host.innerHTML='<span style="color:#94a3b8;">料号:</span> '+(ps.length?ps.map(p=>
          '<a style="color:'+(p===_product?'#dc2626':'#2563eb')+';font-weight:'+(p===_product?'600':'400')
          +';cursor:pointer;margin-right:8px;text-decoration:none;" onclick="setProduct(\\''+esc(p)+'\\')">'
          +esc(p)+'</a>').join(''):'—');
      }).catch(function(e){});
    }
    function loadSpecs(){
      var url='/equip/data?model='+encodeURIComponent(_model||'');
      if(_product)url+='&product='+encodeURIComponent(_product);
      fetch(url).then(r=>r.json()).then(d=>{
        var specs=d.specs||[];
        _specList=specs.map(function(s){return s.spec;});
        document.getElementById('detail').innerHTML=
          '<div class="eq-hd"><h3>工序清单 <span class="eq-badge">'+specs.length+' 个</span></h3>'
          +(_product?'<span style="font-size:12px;color:#64748b;">料号: '+esc(_product)+' '
            +'<a class="eq-back" style="margin:0" onclick="setProduct(null)">(全型号)</a></span>':'')
          +'</div>'
          +(specs.length
            ?'<table class="eq-table"><thead><tr><th style="width:44px;"></th><th style="width:56px;">序号</th>'
             +'<th>工序</th><th>工序名称</th>'
             +'<th style="text-align:center;">UPH 中位</th><th style="text-align:center;">设备数</th>'
             +'<th style="text-align:center;">样本数</th></tr></thead><tbody>'
             +specs.map(function(s,i){
               return '<tr id="row-'+i+'" onclick="toggleSpec('+i+')">'
               +'<td style="text-align:center;"><span class="eq-caret" id="caret-'+i+'">▸</span></td>'
               +'<td style="text-align:center;color:#64748b;">'+esc(s.seq||'—')+'</td>'
               +'<td class="eq-spec-cell">'+esc(s.spec)+'</td>'
               +'<td style="color:#64748b;font-size:12px;line-height:1.5;">'+esc(s.desc||'—')+'</td>'
               +'<td class="eq-uph">'+fmtUPH(s.uph)+'</td>'
               +'<td style="text-align:center;">'+s.eqp_cnt+'</td>'
               +'<td style="text-align:center;">'+s.samples+'</td></tr>';}).join('')
             +'</tbody></table>'
            :'<div class="eq-empty">该'+( _product?'料号':'型号' )+'暂无已定稿工序样本。</div>');
      }).catch(function(e){document.getElementById('detail').innerHTML=eqErr(e);});
    }
    function setSpecRow(idx,on){
      var row=document.getElementById('row-'+idx);
      var caret=document.getElementById('caret-'+idx);
      if(row)row.classList.toggle('spec-open',on);
      if(caret)caret.classList.toggle('open',on);
    }
    function toggleSpec(idx){
      var det=document.getElementById('det-'+idx);
      var caret=document.getElementById('caret-'+idx);
      if(det){var show=det.style.display==='none';
        det.style.display=show?'':'none';
        if(caret)caret.classList.toggle('open',show);
        setSpecRow(idx,show);
        return;}
      var url='/equip/data?model='+encodeURIComponent(_model)+'&spec='+encodeURIComponent(_specList[idx]);
      if(_product)url+='&product='+encodeURIComponent(_product);
      fetch(url).then(r=>r.json()).then(d=>{
        var eqps=d.eqps||[];
        eqps.forEach(function(e){_eqpData[e.eqp]=e.detail||[];});
        var row=document.getElementById('row-'+idx);
        if(!row)return;
        setSpecRow(idx,true);
        row.insertAdjacentHTML('afterend',
          '<tr id="det-'+idx+'"><td colspan="7" style="background:#f8fafc;padding:8px 16px 16px 54px;">'
          +(eqps.length
            ?'<table class="eq-table" style="box-shadow:none;border:1px solid #e2e8f0;">'
             +'<thead><tr><th>设备号</th><th style="text-align:center;">UPH 中位数</th>'
             +'<th style="text-align:center;">样本数</th><th style="text-align:center;">最近定稿</th>'
             +'<th style="text-align:center;width:110px;">样本明细</th>'
             +'</tr></thead><tbody>'
             +eqps.map(function(e){
               return '<tr id="eqpr-'+idx+'-'+esc(e.eqp)+'" onclick="toggleEqp('+idx+',\\''+esc(e.eqp)+'\\')">'
               +'<td style="font-weight:600;">'+esc(e.eqp)+'</td>'
               +'<td class="eq-uph">'+fmtUPH(e.uph)+'</td>'
               +'<td style="text-align:center;">'+e.samples+'</td>'
               +'<td style="text-align:center;color:#94a3b8;">'+fmtTime(e.last_at)+'</td>'
               +'<td style="text-align:center;color:#2563eb;font-size:12px;">展开 ▾</td></tr>';}).join('')
             +'</tbody></table>'
            :'<div class="eq-empty">该工序暂无已定稿设备样本。</div>')
          +'</td></tr>');
      }).catch(function(e){});
    }
    function toggleEqp(idx,eqp){
      var det=document.getElementById('eqpd-'+idx+'-'+eqp);
      if(det){det.style.display=det.style.display==='none'?'':'none';return;}
      var data=_eqpData[eqp]||[];
      if(!data.length)return;
      var row=document.getElementById('eqpr-'+idx+'-'+eqp);
      if(!row)return;
      var html='<tr id="eqpd-'+idx+'-'+eqp+'"><td colspan="5" style="background:#fff;padding:4px 14px 14px;">'
        +'<table class="eqp-det"><thead><tr>'
        +'<th>料号 PRODUCTNAME</th><th style="text-align:center;">样本数</th>'
        +'<th style="text-align:center;">平均 UPH</th><th style="text-align:center;">最近定稿</th>'
        +'</tr></thead><tbody>'
        +data.map(function(x){
          return '<tr><td style="color:#475569;font-weight:600;">'+esc(x.product||'—')+'</td>'
            +'<td style="text-align:center;">'+x.samples+'</td>'
            +'<td style="color:#2563eb;font-weight:600;text-align:center;">'+fmtUPH(x.uph)+'</td>'
            +'<td style="text-align:center;color:#94a3b8;">'+fmtTime(x.at)+'</td></tr>';}).join('')
        +'</tbody></table></td></tr>';
      row.insertAdjacentHTML('afterend',html);
    }
    function searchEqp(){
      var kw=document.getElementById('eqpKw').value.trim();
      if(!kw)return;
      _model=null;_product=null;_specList=[];_matchList=[];
      renderCrumbs();
      document.getElementById('detail').innerHTML='<div class="eq-empty">查询中…</div>';
      fetch('/equip/data?eqp='+encodeURIComponent(kw)).then(r=>r.json()).then(d=>{
        var ms=d.matches||[];
        _matchList=ms;
        document.getElementById('detail').innerHTML=
          '<div class="eq-hd"><h3>设备「'+esc(kw)+'」做过的型号 × 工序 <span class="eq-badge">'+ms.length+' 个</span>'
          +'<span class="eq-badge" style="background:#eff6ff;color:#1d4ed8;">点行展开样本明细</span></h3></div>'
          +(ms.length
            ?'<table class="eq-table"><thead><tr><th style="width:56px;">序号</th><th>型号</th><th>工序</th><th>工序名称</th>'
             +'<th style="text-align:center;">UPH 中位</th><th style="text-align:center;">样本数</th>'
             +'<th style="text-align:center;">最近定稿</th></tr></thead><tbody>'
             +ms.map(function(m,i){
               return '<tr id="mrow-'+i+'" onclick="toggleMatch('+i+')">'
               +'<td style="text-align:center;color:#64748b;">'+esc(m.seq||'—')+'</td>'
               +'<td style="font-weight:600;">'+esc(m.model)+'</td><td>'+esc(m.spec)+'</td>'
               +'<td style="color:#64748b;font-size:12px;line-height:1.5;">'+esc(m.desc||'—')+'</td>'
               +'<td class="eq-uph">'+fmtUPH(m.uph)+'</td>'
               +'<td style="text-align:center;">'+m.samples+'</td>'
               +'<td style="text-align:center;color:#94a3b8;">'+fmtTime(m.last_at)+'</td></tr>';}).join('')
             +'</tbody></table>'
            :'<div class="eq-empty">未找到该设备的已定稿样本。</div>');
      }).catch(function(e){document.getElementById('detail').innerHTML=eqErr(e);});
    }
    // 反查结果行:就地展开该型号×工序下匹配设备的样本明细(与工序清单同款交互)
    function toggleMatch(i){
      var det=document.getElementById('mdet-'+i);
      var row=document.getElementById('mrow-'+i);
      if(det){var show=det.style.display==='none';
        det.style.display=show?'':'none';
        if(row)row.classList.toggle('spec-open',show);
        return;}
      var m=_matchList[i];
      if(!m||!m.detail||!m.detail.length){ if(row)row.classList.add('spec-open'); return;}
      row.classList.add('spec-open');
      row.insertAdjacentHTML('afterend',
        '<tr id="mdet-'+i+'"><td colspan="7" style="background:#f8fafc;padding:8px 16px 16px 54px;">'
        +'<table class="eqp-det"><thead><tr>'
        +'<th>料号 PRODUCTNAME</th><th style="text-align:center;">样本数</th>'
        +'<th style="text-align:center;">平均 UPH</th><th style="text-align:center;">最近定稿</th>'
        +'</tr></thead><tbody>'
        +m.detail.map(function(x){
          return '<tr><td style="color:#475569;font-weight:600;">'+esc(x.product||'—')+'</td>'
            +'<td style="text-align:center;">'+x.samples+'</td>'
            +'<td style="color:#2563eb;font-weight:600;text-align:center;">'+fmtUPH(x.uph)+'</td>'
            +'<td style="text-align:center;color:#94a3b8;">'+fmtTime(x.at)+'</td></tr>';}).join('')
        +'</tbody></table></td></tr>');
    }
    loadModels();
    </script>
    """)
def render_audit(logs, current_user=""):
    """操作审计日志页(超管可见)。"""
    logs = logs or []
    rows = "".join(
        f'<tr><td style="color:#94a3b8;white-space:nowrap;">{_html.escape(l["ts"] or "")}</td>'
        f'<td style="text-align:center;">{_html.escape(l["username"] or "")}</td>'
        f'<td style="text-align:center;">{_html.escape(l["action"] or "")}</td>'
        f'<td style="color:#475569;">{_html.escape(l["detail"] or "")}</td></tr>'
        for l in logs) or '<tr><td colspan="4" style="text-align:center;color:#94a3b8;padding:20px;">暂无操作记录</td></tr>'
    body = f"""
    <div class="page-title">操作审计日志</div>
    <div class="page-sub">最近 {len(logs)} 条后台关键操作(删工单 / 切换CT计算方式 / 账号增删 / 角色变更 / 改密)
      · <a href="/admin">← 返回后台</a></div>
    <div class="panel" style="max-height:calc(100vh - 220px);overflow:auto;"><table>
      <thead><tr><th>时间</th><th style="text-align:center;">操作人</th><th style="text-align:center;">操作</th><th>详情</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>"""
    return layout("admin", "审计日志", body)


def _eng_dot(o):
    """工单若有 ENG 验证时间,返 teal 圆点标记。"""
    import json as _json
    raw = (o.get("eng_times") or "").strip()
    if not raw:
        return ""
    try:
        items = _json.loads(raw)
        if any(e.get("eng_h") for e in items):
            return '<span title="含ENG验证时间" style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#0d9488;margin-right:4px;vertical-align:middle;"></span>'
    except (ValueError, TypeError):
        pass
    return ""

def admin_order_row(o):
    """后台「工单列表」单行 HTML。列表初次渲染与 AJAX 编辑后就地替换共用此模板,
    保证两处一致。行带 data-id(供编辑后定位替换)、data-search(供前端筛选)。"""
    oid = o["id"]
    del_form = (f'<form method="post" action="/admin/delete" style="display:inline;" '
                f"onsubmit=\"return confirm('确认删除此工单?');\">"
                f'<input type="hidden" name="id" value="{oid}">'
                f'<button type="submit" class="op-del">删除</button></form>')
    search = _html.escape(((o.get("wo_no") or "") + " " + (o.get("product") or "") + " "
                           + (o.get("customer") or "") + " " + (o.get("demander") or "")).lower())
    return f"""<tr data-id="{oid}" data-search="{search}">
        <td style="text-align:center;">{_eng_dot(o)}{_html.escape(o.get("wo_no") or "")}</td>
        <td style="text-align:center;">{_html.escape(o.get("product") or "")}</td>
        <td style="text-align:center;">{_html.escape(o.get("customer") or "")}</td>
        <td style="text-align:center;">{_prio_tag(o.get("priority"))}</td>
        <td style="text-align:center;">{_html.escape(o.get("demander") or "")}</td>
        <td style="text-align:center;">
          <button type="button" class="op-edit" onclick="adminEditRow({oid})">编辑</button>
          {del_form}
        </td>
      </tr>"""


def admin_order_js(o):
    """供前端编辑回填的工单可编辑字段(JSON 安全子集)。"""
    return {
        "id": o.get("id"), "wo_no": o.get("wo_no") or "", "product": o.get("product") or "",
        "customer": o.get("customer") or "", "demander": o.get("demander") or "",
        "priority": o.get("priority") or "", "plan_start": o.get("plan_start") or "",
        "plan_end": o.get("plan_end") or "", "deliver_qty": o.get("deliver_qty") or "",
        "final_process": o.get("final_process") or "", "remark": o.get("remark") or "",
        "eng_times": o.get("eng_times") or "",
    }


def render_admin(orders, products, edit_order=None, msg="", ct_tables=None,
                 prefixes=None, mes_rows=None, synced=None, csv_ok=True,
                 last_sync_at="", last_sync_status="", owner_info=None,
                 users=None, current_user="", current_role="normal",
                 exc_wait=24, exc_hold=24, exc_models=None, exc_rows=None,
                 urgent_batches=None, exc_wo_rows=None, mes_products=None,
                 ct_specs=None):
    is_super = (current_role == "super")
    ct_specs = ct_specs or {}
    mes_products = mes_products or []
    exc_models = exc_models or []
    exc_rows = exc_rows or []
    exc_wo_rows = exc_wo_rows or []
    urgent_batches = urgent_batches or []
    msg_html = f'<div class="flash">{_html.escape(msg)}</div>' if msg else ""
    ct_tables = ct_tables or []
    prefixes = prefixes or []
    mes_rows = mes_rows or []
    synced = synced or []

    rows = "".join(admin_order_row(o) for o in orders)
    import json as _json_admin
    admin_orders_js = _json_admin.dumps(
        {o["id"]: admin_order_js(o) for o in orders}, ensure_ascii=False).replace("<", "\\u003c")

    prod_tags = "".join(f"""
      <span class="btag">{_html.escape(p["value"])}
        <form method="post" action="/admin/base_delete" style="display:inline;">
          <input type="hidden" name="id" value="{p["id"]}">
          <button class="btag-x" type="submit">×</button>
        </form></span>""" for p in products)

    form_section = _order_form(edit_order, "/admin/update" if edit_order else "/admin/create", products, ct_specs)
    form_title = "编辑工单" if edit_order else "新建工单"

    def _ct_method_cell(t):
        # 两个按钮:按上传CT / 按校准后CT,当前选中高亮;点击 POST 切换(登录管理员均可)
        use_cal = bool(t.get("use_calibrated"))
        m = _html.escape(t["model"])
        def _btn(method, label, active):
            on = "background:#16a34a;color:#fff;border-color:#16a34a;" if active else \
                 "background:#f1f5f9;color:#475569;"
            return (f'<form method="post" action="/admin/ct_method" style="display:inline;margin:0 3px 0 0;">'
                    f'<input type="hidden" name="model" value="{m}">'
                    f'<input type="hidden" name="method" value="{method}">'
                    f'<button type="submit" title="切换为此计算方式" style="{on}'
                    f'border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;'
                    f'cursor:pointer;font-size:12px;">{label}</button></form>')
        return _btn("0", "按上传CT", not use_cal) + _btn("1", "按校准后", use_cal)

    def _days(h):
        return f'{h/24:.1f}' if h else '—'

    def _ct_del_cell(t):
        m = _html.escape(t["model"])
        return (f'<form method="post" action="/admin/ct_delete" style="display:inline;margin:0;" '
                f'onsubmit="return confirm(\'确定删除型号 {m} 的 CT 表?该型号工单将失去进度/风险依据。\');">'
                f'<input type="hidden" name="model" value="{m}">'
                f'<button type="submit" title="删除此 CT 表" '
                f'style="background:#fee2e2;color:#dc2626;border:1px solid #fecaca;border-radius:6px;'
                f'padding:4px 10px;cursor:pointer;font-size:12px;">删除</button></form>')

    ct_rows = "".join(
        f'<tr><td>{_html.escape(t["model"])}</td>'
        f'<td>{_days(t["total_ct"])} 天</td>'
        f'<td style="color:#16a34a;">{_days(t.get("calibrated_total"))} 天</td>'
        f'<td>{_ct_method_cell(t)}</td>'
        f'<td style="color:#94a3b8;">{_html.escape(t["uploaded_at"] or "")}</td>'
        f'<td>{_ct_del_cell(t)}</td></tr>'
        for t in ct_tables) or '<tr><td colspan="6" style="color:#94a3b8;">尚未上传任何 CT 表</td></tr>'

    # ----- 数据源(MES 抓取)区,已集成进后台管理 -----
    if prefixes:
        chips = "".join(
            f'<span class="btag" style="background:#eef2ff;">{_html.escape(pre)} '
            f'<span style="color:#94a3b8;font-size:11px;">({"/".join(_html.escape(m) for m in models)})</span></span>'
            for pre, models in prefixes)
    else:
        chips = '<span style="color:#94a3b8;font-size:13px;">下方“产品型号”里还没录入。请先添加,如 MC1、MB1。</span>'
    csv_warn = "" if csv_ok else (
        '<div class="flash" style="background:#fef2f2;color:#dc2626;">'
        '未找到 MES数据库.csv。请把它放到程序同目录,或设置环境变量 MES_CSV 指向其路径。</div>')
    datasource_panel = f"""
    <div class="panel"><h3>数据源 · 从 MES 抓取在制工单</h3>
      {csv_warn}
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:10px;">
        <span style="font-size:13px;color:#16a34a;">● 数据源每 3 分钟 · 界面随数据自动更新</span>
        <span style="font-size:12px;color:#94a3b8;">上次刷新:{_html.escape(last_sync_at or "尚未刷新")}
          {("· " + _html.escape(last_sync_status)) if last_sync_status else ""}</span>
      </div>
      <div style="font-size:13px;color:#475569;margin-bottom:10px;">
        按录入型号<b>从严到松</b>匹配 MES 的 PACKAGESIZE 抓取(先精确、无则前两字符兜底)。当前白名单前缀:
      </div>
      <div class="btags" style="margin-bottom:12px;">{chips}</div>
      <form method="post" action="/data/sync_mes">
        <button type="submit" class="btn-primary" style="width:auto;padding:8px 20px;"
          {"disabled" if not (prefixes and csv_ok) else ""}>⟳ 立即刷新</button>
        <span style="font-size:12px;color:#94a3b8;margin-left:10px;">手动触发一次抓取(与自动刷新逻辑相同)</span>
      </form>
      <div style="margin-top:10px;font-size:12px;color:#94a3b8;">
        MES 已抓取工单:{len(mes_rows)} 个。完整明细见
        <a href="/data" style="color:#2563eb;">数据源页</a>。
      </div>
    </div>"""

    # ----- Owner 表上传区 -----
    if owner_info:
        owner_status = (f'已上传 {owner_info.get("row_count",0)} 条 · '
                        f'{_html.escape(owner_info.get("uploaded_at") or "")}')
    else:
        owner_status = "尚未上传"
    owner_panel = f"""
    <div class="panel"><h3>Inline Owner 表(按工序+状态决定负责人)</h3>
      <div style="font-size:13px;color:#475569;margin-bottom:10px;">
        上传 Owner 表(.xlsx/.csv),需含列:<code>TECHNOLOGY</code>、<code>SPECNAME</code>、
        <code>ENG Owner</code>、<code>Inline Owner</code>。同步时按工单<b>当前工序</b>结合
        <b>MES 状态</b>决定负责人:<b>HOLD</b> 取 ENG Owner;
        <b>WaitingMoveOut / WaitingTrackIn / RUNNING</b> 取 Inline Owner。
      </div>
      <form method="post" action="/admin/upload_owner" enctype="multipart/form-data" style="margin-bottom:8px;">
        <input type="file" name="file" accept=".xlsx,.csv" required>
        <button type="submit" class="btn-primary" style="width:auto;padding:7px 16px;">上传 Owner 表</button>
      </form>
      <div style="font-size:12px;color:#94a3b8;">当前状态:{owner_status}</div>
    </div>"""

    # ----- 账号管理区(超管专属:角色分级) -----
    users = users or []

    def _role_badge(role):
        if role == "super":
            return '<span style="color:#16a34a;font-weight:600;">超级管理员</span>'
        return '<span style="color:#64748b;">管理员</span>'

    def _role_toggle(u):
        # 角色切换(不对自己显示,避免自降导致无超管)
        if u["username"] == current_user:
            return ''
        if u.get("role") == "super":
            return (f'<form method="post" action="/admin/set_role" style="display:inline;" '
                    f'''onsubmit="return confirm('将 {_html.escape(u["username"])} 降为管理员?');">'''
                    f'<input type="hidden" name="username" value="{_html.escape(u["username"])}">'
                    f'<input type="hidden" name="role" value="normal">'
                    f'<button type="submit" class="op-edit">设为普通</button></form>')
        return (f'<form method="post" action="/admin/set_role" style="display:inline;" '
                f'''onsubmit="return confirm('将 {_html.escape(u["username"])} 升为超级管理员?');">'''
                f'<input type="hidden" name="username" value="{_html.escape(u["username"])}">'
                f'<input type="hidden" name="role" value="super">'
                f'<button type="submit" class="op-edit">设为超管</button></form>')

    user_rows = "".join(f"""
      <tr>
        <td style="text-align:center;">{_html.escape(u["username"])}{' <span style="color:#0ea5e9;font-size:12px;">(当前)</span>' if u["username"] == current_user else ''}</td>
        <td style="text-align:center;">{_role_badge(u.get("role"))}</td>
        <td style="text-align:center;color:#94a3b8;">{_html.escape(u.get("created_at") or "")}</td>
        <td style="text-align:center;">
          {_role_toggle(u)}
          <form method="post" action="/admin/user_reset_pw" style="display:inline;"
                onsubmit="var p=prompt('为账号 {_html.escape(u["username"])} 设置新密码(至少8位,含字母和数字):');if(!p){{return false;}}if(p.length<8){{wipToast('密码至少8位','warn');return false;}}if(!/[a-zA-Z]/.test(p)||!/[0-9]/.test(p)){{wipToast('密码需同时包含字母和数字','warn');return false;}}this.new_pw.value=p;return true;">
            <input type="hidden" name="username" value="{_html.escape(u["username"])}">
            <input type="hidden" name="new_pw" value="">
            <button type="submit" class="op-edit">重置密码</button>
          </form>
          {'' if u["username"] == current_user else f'''
          <form method="post" action="/admin/user_delete" style="display:inline;" onsubmit="return confirm('确认删除账号 {_html.escape(u["username"])}?');">
            <input type="hidden" name="username" value="{_html.escape(u["username"])}">
            <button type="submit" class="op-del">删除</button>
          </form>'''}
        </td>
      </tr>""" for u in users)

    users_panel = f"""
    <div class="panel"><h3>账号管理 · 共 {len(users)} 个
      <a href="/admin/audit" style="font-size:13px;font-weight:normal;margin-left:12px;">查看操作审计日志 →</a></h3>
      <div style="font-size:13px;color:#475569;margin-bottom:10px;">
        <b>超级管理员</b>可做全部操作(账号管理、CT 计算方式、删工单);<b>管理员</b>仅日常操作。
        当前登录:<b>{_html.escape(current_user or "-")}</b>(超级管理员)。
      </div>
      <table style="text-align:center;margin-bottom:14px;">
        <thead><tr><th style="text-align:center;">账号</th><th style="text-align:center;">角色</th><th style="text-align:center;">创建时间</th><th style="text-align:center;">操作</th></tr></thead>
        <tbody>{user_rows or '<tr><td colspan="4" style="color:#94a3b8;">暂无账号</td></tr>'}</tbody>
      </table>
      <form method="post" action="/admin/user_add" class="wo-form" style="max-width:420px;"
            onsubmit="var pw=this.pw.value; if(pw.length<8){{wipToast('密码至少8位','warn');return false;}} if(!/[a-zA-Z]/.test(pw)||!/[0-9]/.test(pw)){{wipToast('密码需同时包含字母和数字','warn');return false;}} return true;">
        <div class="frow"><label>新账号</label><input name="username" required placeholder="字母/数字/_-.@,≤32位"></div>
        <div class="frow"><label>初始密码</label><input name="pw" type="password" required placeholder="至少8位,含字母和数字"></div>
        <div class="frow"><label>角色</label><select name="role" style="padding:7px 10px;border:1px solid #e2e8f0;border-radius:6px;"><option value="normal">管理员</option><option value="super">超级管理员</option></select></div>
        <div class="fbtns"><button type="submit" class="btn-primary">+ 新增账号</button></div>
      </form>
    </div>"""
    if not is_super:
        users_panel = ""   # 管理员不显示账号管理面板

    # ----- 账号安全:改账号名 + 改密码 合一表单。原密码做统一身份验证,填了哪项改哪项。 -----
    _ai = "flex:none;width:260px;padding:6px 9px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px;"
    rename_field = (
        f'<div class="frow"><label>新账号名</label>'
        f'<input name="new_name" autocomplete="off" placeholder="选填,留空=不改;字母/数字/_-.@,≤32位" style="{_ai}"></div>'
    ) if is_super else ""
    _rename_hint = "(改账号名后用新名登录,密码不变,无需重新登录)" if is_super else ""
    acct_security_panel = f"""
    <div class="panel" style="max-width:520px;"><h3>账号安全 · 当前账号({_html.escape(current_user or "-")})</h3>
      <div style="font-size:13px;color:#475569;margin-bottom:14px;">
        填了哪项就改哪项,留空表示不改。<b>原密码必填</b>用于验证身份{_rename_hint}。
      </div>
      <form method="post" action="/admin/account_update" class="wo-form" onsubmit="return acctSubmit();">
        <div class="frow"><label>当前账号名</label><input value="{_html.escape(current_user or "-")}" disabled style="{_ai}background:#f8fafc;color:#94a3b8;"></div>
        {rename_field}
        <div class="frow"><label>原密码</label><input name="old_pw" type="password" required autocomplete="off" placeholder="必填,验证身份" style="{_ai}"></div>
        <div class="frow"><label>新密码</label><input name="new_pw" type="password" autocomplete="off" placeholder="选填,留空=不改;至少8位含字母和数字" style="{_ai}"></div>
        <div class="fbtns" style="justify-content:flex-start;margin-top:10px;">
          <button type="submit" class="btn-primary" style="flex:0 0 auto;width:auto;padding:8px 24px;">保存修改</button>
          <a href="/logout" class="btn-reset" style="flex:0 0 auto;width:auto;padding:8px 18px;">退出登录</a>
        </div>
      </form>
    </div>
    <script>
    function acctSubmit() {{
      var nn=document.querySelector('input[name=new_name]');
      var np=document.querySelector('input[name=new_pw]');
      var hasName = nn && nn.value.trim();
      var hasPw = np && np.value.trim();
      if(!hasName && !hasPw) {{ wipToast('请至少填写「新账号名」或「新密码」其中一项'); return false; }}
      if(hasName) return confirm('确认将账号名改为「'+nn.value.trim()+'」?之后请用新名登录。');
      return true;
    }}
    </script>"""

    # 归一到基础封装型号并去重(MB12/4in1、MB12/1in1 → MB12),并入已上传 CT 的型号
    _raw_models = [(m["value"] if isinstance(m, dict) else m) for m in exc_models]
    _base = []
    for m in _raw_models:
        b = mes.base_model(m)
        if b and b not in _base:
            _base.append(b)
    _exc_model_names = _base
    exc_model_opts = "".join(
        f'<option value="{_html.escape(m)}">{_html.escape(m)}</option>' for m in _exc_model_names)
    _inp = "padding:6px;border:1px solid #cbd5e1;border-radius:6px;"
    thr_rows = ""
    for t in exc_rows:
        scope = t["spec"] or "（整个型号·全局）"
        thr_rows += f"""<tr>
          <td style="padding:6px 12px;text-align:center;font-weight:600;">{_html.escape(t['model'])}</td>
          <td style="padding:6px 12px;text-align:center;">{_html.escape(scope)}</td>
          <td style="padding:6px 12px;text-align:center;">{t['wait_h']:g}h</td>
          <td style="padding:6px 12px;text-align:center;">{t['hold_h']:g}h</td>
          <td style="padding:6px 12px;text-align:center;white-space:nowrap;">
            <button type="button" onclick="editExcRow('{_html.escape(t['model'])}','{_html.escape(t['spec'])}',{t['wait_h']:g},{t['hold_h']:g})"
              style="border:1px solid #2563eb;background:#fff;color:#2563eb;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px;">编辑</button>
            <form method="post" action="/admin/exc_thr_del" style="display:inline;margin-left:6px;" onsubmit="return confirm('删除该阈值?之后该范围恢复按上级阈值。');">
              <input type="hidden" name="model" value="{_html.escape(t['model'])}">
              <input type="hidden" name="spec" value="{_html.escape(t['spec'])}">
              <button type="submit" style="border:1px solid #dc2626;background:#fff;color:#dc2626;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px;">删除</button>
            </form>
          </td></tr>"""
    if not thr_rows:
        thr_rows = ('<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:24px;">'
                    '暂无型号/工序级阈值,全部按系统默认</td></tr>')
    # 工单级阈值行 + 工单号下拉数据
    wo_thr_rows = ""
    for t in exc_wo_rows:
        scope = t["spec"] or "（整单·全部工序）"
        wo_thr_rows += f"""<tr>
          <td style="padding:6px 12px;text-align:center;font-weight:600;">{_html.escape(t['wo_no'])}</td>
          <td style="padding:6px 12px;text-align:center;">{_html.escape(scope)}</td>
          <td style="padding:6px 12px;text-align:center;">{t['wait_h']:g}h</td>
          <td style="padding:6px 12px;text-align:center;">{t['hold_h']:g}h</td>
          <td style="padding:6px 12px;text-align:center;white-space:nowrap;">
            <button type="button" onclick="editExcWoRow('{_html.escape(t['wo_no'])}','{_html.escape(t['spec'])}',{t['wait_h']:g},{t['hold_h']:g})"
              style="border:1px solid #2563eb;background:#fff;color:#2563eb;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px;">编辑</button>
            <form method="post" action="/admin/exc_wo_del" style="display:inline;margin-left:6px;" onsubmit="return confirm('删除该工单阈值?');">
              <input type="hidden" name="wo_no" value="{_html.escape(t['wo_no'])}">
              <input type="hidden" name="spec" value="{_html.escape(t['spec'])}">
              <button type="submit" style="border:1px solid #dc2626;background:#fff;color:#dc2626;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px;">删除</button>
            </form></td></tr>"""
    if not wo_thr_rows:
        wo_thr_rows = '<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:24px;">暂无工单级阈值</td></tr>'
    wo_opts = "".join(f'<option value="{_html.escape(o["wo_no"])}">' for o in orders)
    exc_panel = f"""
    <div class="panel"><h3>异常记录阈值设置</h3>
      <div style="font-size:13px;color:#475569;margin-bottom:10px;">
        Lot 在工序中 Wait/HOLD 超过阈值时,每次 MES 同步自动在「异常记录」生成记录(设 0=不监控)。
        优先级:<b>工单</b> &gt; <b>型号+工序</b> &gt; <b>型号全局</b> &gt; <b>系统默认</b>。
        阈值是对<b>单个 Lot 在单道工序</b>的 Wait/HOLD 计时,不是累计总时长。
      </div>
      <div style="font-weight:600;font-size:13px;margin:6px 0;">① 系统默认阈值(所有型号兜底)</div>
      <form method="post" action="/admin/exc_thresholds" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px;">
        <label style="font-size:14px;">Wait 超过
          <input type="number" name="wait_h" value="{exc_wait:g}" min="0" step="0.5" style="width:84px;{_inp}"> 小时</label>
        <label style="font-size:14px;">HOLD 超过
          <input type="number" name="hold_h" value="{exc_hold:g}" min="0" step="0.5" style="width:84px;{_inp}"> 小时</label>
        <button type="submit" style="border:none;background:#475569;color:#fff;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:14px;">保存默认</button>
      </form>
      <div style="font-weight:600;font-size:13px;margin:6px 0;">② 按型号 / 工序设定(精确阈值)</div>
      <form method="post" action="/admin/exc_thr_set" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <label style="font-size:14px;">型号
          <select id="exc_model" name="model" onchange="loadExcSpecs()" style="{_inp}min-width:120px;" required>
            <option value="">选择型号</option>{exc_model_opts}
          </select></label>
        <label style="font-size:14px;">工序
          <select id="exc_spec" name="spec" style="{_inp}min-width:220px;">
            <option value="">（整个型号·全局）</option>
          </select></label>
        <label style="font-size:14px;">Wait&gt;<input id="exc_w" type="number" name="wait_h" value="24" min="0" step="0.5" style="width:78px;{_inp}">h</label>
        <label style="font-size:14px;">HOLD&gt;<input id="exc_h" type="number" name="hold_h" value="24" min="0" step="0.5" style="width:78px;{_inp}">h</label>
        <button type="submit" style="border:none;background:#2563eb;color:#fff;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:14px;">保存</button>
        <button type="button" onclick="document.getElementById('excThrMask').style.display='block';document.getElementById('excThrBox').style.display='flex';"
          style="border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:14px;">已设置型号/工序({len(exc_rows)})</button>
      </form>
      <div style="font-weight:600;font-size:13px;margin:14px 0 6px;">③ 按工单设定(优先级最高)</div>
      <form method="post" action="/admin/exc_wo_set" onsubmit="return collectWoSpecs()" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <label style="font-size:14px;">工单号
          <input id="excwo_no" name="wo_no" list="excwo_list" required placeholder="工单号"
                 onchange="loadWoSpecs()" style="{_inp}min-width:160px;">
          <datalist id="excwo_list">{wo_opts}</datalist></label>
        <label style="font-size:14px;">工序
          <span style="position:relative;display:inline-block;">
            <button type="button" id="excwo_specbtn" onclick="toggleWoSpecDrop(event)"
                    style="{_inp}min-width:220px;text-align:left;cursor:pointer;background:#fff;">先选工单 ▾</button>
            <div id="excwo_specdrop" style="display:none;position:absolute;z-index:70;left:0;top:100%;background:#fff;
                 border:1px solid #cbd5e1;border-radius:6px;max-height:260px;overflow:auto;min-width:280px;
                 box-shadow:0 8px 24px rgba(0,0,0,.15);padding:6px;">
              <div style="font-size:12px;color:#94a3b8;padding:4px 6px;">不勾=整单(全部工序);可多选</div>
              <div id="excwo_specopts"></div>
            </div>
          </span>
          <input type="hidden" name="specs" id="excwo_specs"></label>
        <label style="font-size:14px;">Wait&gt;<input id="excwo_w" type="number" name="wait_h" value="24" min="0" step="0.5" style="width:78px;{_inp}">h</label>
        <label style="font-size:14px;">HOLD&gt;<input id="excwo_h" type="number" name="hold_h" value="24" min="0" step="0.5" style="width:78px;{_inp}">h</label>
        <button type="submit" style="border:none;background:#2563eb;color:#fff;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:14px;">保存</button>
        <button type="button" onclick="document.getElementById('excWoThrMask').style.display='block';document.getElementById('excWoThrBox').style.display='flex';"
          style="border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:14px;">已设置工单({len(exc_wo_rows)})</button>
      </form>
    </div>
    <div id="excThrMask" onclick="document.getElementById('excThrMask').style.display='none';document.getElementById('excThrBox').style.display='none';"
         style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:60;"></div>
    <div id="excThrBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:760px;max-width:94vw;max-height:80vh;background:#fff;border-radius:12px;z-index:61;
         box-shadow:0 20px 60px rgba(0,0,0,.3);flex-direction:column;overflow:hidden;">
      <div style="position:relative;padding:16px 22px 10px;">
        <h3 style="margin:0;">已设置阈值的型号/工序</h3>
        <div style="font-size:12px;color:#94a3b8;margin-top:4px;">点「编辑」回填到上方表单修改,或「删除」恢复按上级阈值</div>
        <button onclick="document.getElementById('excThrMask').style.display='none';document.getElementById('excThrBox').style.display='none';"
          style="position:absolute;top:16px;right:20px;border:none;background:#f1f5f9;border-radius:6px;padding:6px 12px;cursor:pointer;">关闭</button>
      </div>
      <div style="overflow:auto;padding:0 18px 18px;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="color:#475569;background:#eef2f7;">
            <td style="padding:6px 12px;text-align:center;">型号</td><td style="padding:6px 12px;text-align:center;">范围</td>
            <td style="padding:6px 12px;text-align:center;">Wait阈值</td><td style="padding:6px 12px;text-align:center;">HOLD阈值</td>
            <td style="padding:6px 12px;text-align:center;">操作</td></tr></thead>
          <tbody>{thr_rows}</tbody>
        </table>
      </div>
    </div>
    <div id="excWoThrMask" onclick="document.getElementById('excWoThrMask').style.display='none';document.getElementById('excWoThrBox').style.display='none';"
         style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:60;"></div>
    <div id="excWoThrBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:760px;max-width:94vw;max-height:80vh;background:#fff;border-radius:12px;z-index:61;
         box-shadow:0 20px 60px rgba(0,0,0,.3);flex-direction:column;overflow:hidden;">
      <div style="position:relative;padding:16px 22px 10px;">
        <h3 style="margin:0;">已设置的工单级阈值(优先级最高)</h3>
        <div style="font-size:12px;color:#94a3b8;margin-top:4px;">点「编辑」回填到上方表单修改,或「删除」恢复按上级阈值</div>
        <button onclick="document.getElementById('excWoThrMask').style.display='none';document.getElementById('excWoThrBox').style.display='none';"
          style="position:absolute;top:16px;right:20px;border:none;background:#f1f5f9;border-radius:6px;padding:6px 12px;cursor:pointer;">关闭</button>
      </div>
      <div style="overflow:auto;padding:0 18px 18px;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="color:#475569;background:#eef2f7;">
            <td style="padding:6px 12px;text-align:center;">工单号</td><td style="padding:6px 12px;text-align:center;">范围</td>
            <td style="padding:6px 12px;text-align:center;">Wait阈值</td><td style="padding:6px 12px;text-align:center;">HOLD阈值</td>
            <td style="padding:6px 12px;text-align:center;">操作</td></tr></thead>
          <tbody>{wo_thr_rows}</tbody>
        </table>
      </div>
    </div>
    <script>
    function _updWoSpecBtn() {{
      var n=document.querySelectorAll('#excwo_specopts input:checked').length;
      document.getElementById('excwo_specbtn').textContent = (n? ('已选 '+n+' 个工序'):'整单(全部工序) ▾');
    }}
    function loadWoSpecs(preSpecs) {{
      var wo=document.getElementById('excwo_no').value.trim();
      var box=document.getElementById('excwo_specopts');
      box.innerHTML='<div style="padding:4px 6px;color:#94a3b8;font-size:12px;">加载中…</div>';
      document.getElementById('excwo_specbtn').textContent='整单(全部工序) ▾';
      if(!wo) {{ box.innerHTML=''; return; }}
      var pre=preSpecs||[];
      fetch('/wo_specs?wo='+encodeURIComponent(wo)).then(function(r){{return r.json();}}).then(function(d){{
        var specs=(d&&d.specs)||[];
        if(!specs.length){{ box.innerHTML='<div style="padding:4px 6px;color:#dc2626;font-size:12px;">该工单产品无 CT 工序('+(d.model||'?')+')</div>'; return; }}
        var h='';
        specs.forEach(function(s){{
          var ck=(pre.indexOf(s.spec)>=0)?' checked':'';
          h+='<label style="display:block;padding:3px 6px;font-size:13px;cursor:pointer;white-space:nowrap;">'
            +'<input type="checkbox" value="'+s.spec+'" onchange="_updWoSpecBtn()"'+ck+'> '+s.name+'</label>';
        }});
        box.innerHTML=h; _updWoSpecBtn();
      }}).catch(function(){{ box.innerHTML='<div style="padding:4px 6px;color:#dc2626;">加载失败</div>'; }});
    }}
    function toggleWoSpecDrop(ev) {{
      if(ev) ev.stopPropagation();
      var d=document.getElementById('excwo_specdrop');
      d.style.display = (d.style.display==='none'||!d.style.display)?'block':'none';
    }}
    document.addEventListener('click', function(e){{
      var d=document.getElementById('excwo_specdrop'), b=document.getElementById('excwo_specbtn');
      if(d && d.style.display==='block' && !d.contains(e.target) && e.target!==b) d.style.display='none';
    }});
    function collectWoSpecs() {{
      var vals=[];
      document.querySelectorAll('#excwo_specopts input:checked').forEach(function(c){{ vals.push(c.value); }});
      document.getElementById('excwo_specs').value=vals.join('\\n');   // 空=整单
      return true;
    }}
    function editExcWoRow(wo, spec, w, h) {{
      document.getElementById('excwo_no').value=wo;
      document.getElementById('excwo_w').value=w; document.getElementById('excwo_h').value=h;
      loadWoSpecs(spec? [spec] : []);
      document.getElementById('excThrMask').style.display='none'; document.getElementById('excThrBox').style.display='none';
      document.getElementById('excwo_no').scrollIntoView({{behavior:'smooth',block:'center'}});
    }}
    function loadExcSpecs(selSpec) {{
      var m=document.getElementById('exc_model').value, sel=document.getElementById('exc_spec');
      sel.innerHTML='<option value="">（整个型号·全局）</option>';
      if(!m) return;
      fetch('/ct_specs?model='+encodeURIComponent(m)).then(function(r){{return r.json();}})
        .then(function(list){{
          list.forEach(function(s){{ var o=document.createElement('option'); o.value=s.spec; o.textContent=s.name+' ('+s.spec+')'; sel.appendChild(o); }});
          if(selSpec){{ if(!Array.prototype.some.call(sel.options,function(o){{return o.value===selSpec;}})){{ var o=document.createElement('option'); o.value=selSpec; o.textContent=selSpec; sel.appendChild(o); }} sel.value=selSpec; }}
        }});
    }}
    function editExcRow(model, spec, w, h) {{
      document.getElementById('exc_model').value=model;
      document.getElementById('exc_w').value=w; document.getElementById('exc_h').value=h;
      loadExcSpecs(spec);
      document.getElementById('excThrMask').style.display='none'; document.getElementById('excThrBox').style.display='none';
      document.getElementById('exc_model').scrollIntoView({{behavior:'smooth',block:'center'}});
    }}
    </script>"""

    # ----- 加急验证 Lot:新建 + 列表(独立,不与 NTO 工单混淆) -----
    urg_model_opts = "".join(
        f'<option value="{_html.escape(m)}">{_html.escape(m)}</option>' for m in _exc_model_names)
    urg_rows = ""
    for b in urgent_batches:
        _bn = _html.escape(b['batch_name'] or '')
        urg_rows += f"""<tr>
          <td style="text-align:center;"><span onclick="toggleUrgRow({b['id']})" title="点击展开 / 收起该批次 Lot 明细" style="color:#2563eb;cursor:pointer;font-weight:600;"><span id="urgcaret_{b['id']}" style="display:inline-block;width:14px;color:#94a3b8;">▶</span>{_bn}</span></td>
          <td style="text-align:center;">{_html.escape(b.get('product') or '')}</td>
          <td style="text-align:center;">{_html.escape(b.get('target_name') or b.get('target_spec') or '')}</td>
          <td style="text-align:center;">{_html.escape(b.get('plan_end') or '')}</td>
          <td style="text-align:center;">{b.get('lot_count', 0)}</td>
          <td style="text-align:center;white-space:nowrap;">
            <button type="button" onclick="editUrg({b['id']})" style="border:1px solid #2563eb;background:#fff;color:#2563eb;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px;">编辑</button>
            <form method="post" action="/admin/urgent_delete" style="display:inline;margin-left:6px;" onsubmit="return confirm('删除该加急批次?');">
              <input type="hidden" name="id" value="{b['id']}">
              <button type="submit" style="border:1px solid #dc2626;background:#fff;color:#dc2626;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:12px;">删除</button>
            </form></td></tr>
          <tr id="urgrow_{b['id']}" style="display:none;background:#fafbff;"><td colspan="6" style="padding:0 14px 12px;"><div id="urgrowbody_{b['id']}"></div></td></tr>"""
    if not urg_rows:
        urg_rows = '<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:24px;">暂无加急验证 Lot 批次</td></tr>'
    urgent_panel = f"""
    <div class="panel" style="border:1px solid #fca5a5;">
      <h3 style="color:#dc2626;">⚡ 加急验证 Lot（独立，不与 NTO 工单混淆）</h3>
      <div style="font-size:13px;color:#475569;margin-bottom:10px;">
        新建一个加急批次:自定义批次名称、选型号与<b>作业到的目标工序</b>(到该工序即视为完成)、填入一个或多个 Lot 号、计划交付日。
      </div>
      <form method="post" action="/admin/urgent_create" onsubmit="return collectUrgLots()"
            style="display:flex;flex-direction:column;gap:12px;max-width:560px;">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <label style="width:96px;">批次名称</label>
          <input name="batch_name" required placeholder="自定义,如 加急验证-0610A" style="flex:1;{_inp}">
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <label style="width:96px;">型号</label>
          <select id="urg_model" name="product" required onchange="loadUrgInfo()" style="flex:1;{_inp}">
            <option value="">请选择型号</option>
          </select>
          <span id="urg_ctmodel" style="font-size:12px;color:#94a3b8;white-space:nowrap;"></span>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <label style="width:96px;">作业到工序</label>
          <select id="urg_spec" name="target_spec" required style="flex:1;{_inp}">
            <option value="">请先选产品</option>
          </select>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <label style="width:96px;">计划交付日</label>
          <input type="date" name="plan_end" style="{_inp}">
        </div>
        <div style="display:flex;gap:10px;">
          <label style="width:96px;padding-top:6px;">Lot 号</label>
          <div style="flex:1;">
            <textarea id="urg_lots_ta" oninput="urgLotCount()" rows="6"
                      placeholder="每行一个 Lot 号 —— 直接从 Excel/MES 整列复制粘贴进来即可(也支持逗号分隔;重复的会自动去重)"
                      style="width:100%;box-sizing:border-box;resize:vertical;font-family:inherit;line-height:1.6;{_inp}"></textarea>
            <div id="urg_lotcnt" style="font-size:12px;color:#94a3b8;margin-top:3px;">共 0 个 Lot</div>
          </div>
        </div>
        <input type="hidden" name="lots" id="urg_lots_hidden">
        <input type="hidden" name="batch_id" id="urg_batch_id">
        <div>
          <button type="submit" id="urg_submit" style="border:none;background:#dc2626;color:#fff;border-radius:6px;padding:9px 22px;cursor:pointer;font-size:14px;">新建加急批次</button>
          <button type="button" id="urg_cancel" onclick="resetUrgForm()" style="display:none;margin-left:8px;border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;padding:9px 18px;cursor:pointer;font-size:14px;">取消编辑</button>
        </div>
      </form>
    </div>
    <div class="panel">
      <h3>加急验证 Lot 列表 · 共 {len(urgent_batches)} 批</h3>
      <div style="overflow:auto;"><table>
        <thead><tr>
          <th style="text-align:center;">批次名称</th><th style="text-align:center;">型号</th>
          <th style="text-align:center;">作业到工序</th><th style="text-align:center;">计划交付</th>
          <th style="text-align:center;">Lot数</th><th style="text-align:center;">操作</th>
        </tr></thead>
        <tbody>{urg_rows}</tbody>
      </table></div>
    </div>
    <div id="urgMask" onclick="hideUrg()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:50;"></div>
    <div id="urgBox" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
         width:1100px;max-width:94vw;max-height:84vh;background:#fff;border-radius:12px;z-index:51;
         box-shadow:0 20px 60px rgba(0,0,0,.3);flex-direction:column;overflow:hidden;">
      <div style="position:relative;padding:16px 22px 8px;">
        <h3 id="urgTitle" style="margin:0;">批次 Lot 明细</h3>
        <div id="urgHint" style="font-size:12px;color:#94a3b8;margin-top:4px;"></div>
        <button onclick="hideUrg()" style="position:absolute;top:16px;right:20px;border:none;background:#f1f5f9;border-radius:6px;padding:6px 12px;cursor:pointer;">关闭</button>
      </div>
      <div id="urgBody" style="overflow:auto;padding:0 20px 18px;flex:1 1 auto;font-size:13px;">加载中…</div>
    </div>
    <script>
    var _urgProdLoaded=false;
    loadProdList();   // 进入后台页即预填型号下拉(纯选择)
    function loadProdList() {{
      if(_urgProdLoaded) return; _urgProdLoaded=true;
      // 型号下拉数据来自已上传的 CT 文件(如 MB6/MB12),纯下拉选择不可手输
      fetch('/ct_models').then(function(r){{return r.json();}}).then(function(list){{
        var sel=document.getElementById('urg_model'), cur=sel.value;
        sel.innerHTML='<option value="">请选择型号</option>';
        (list||[]).forEach(function(p){{ var o=document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o); }});
        if(cur) sel.value=cur;
      }}).catch(function(){{ _urgProdLoaded=false; }});
    }}
    function loadUrgInfo(selSpec) {{
      // 「型号」即 CT 表型号:直接按它取该 CT 表的工序填目标工序下拉
      var p=document.getElementById('urg_model').value.trim();
      var sel=document.getElementById('urg_spec'), tag=document.getElementById('urg_ctmodel');
      sel.innerHTML='<option value="">选择目标工序</option>'; tag.textContent='';
      if(!p) return;
      tag.textContent='加载中…';
      fetch('/ct_specs?model='+encodeURIComponent(p)).then(function(r){{return r.json();}})
        .then(function(specs){{
          specs=specs||[];
          if(specs.length){{ tag.textContent='CT表:'+p; tag.style.color='#16a34a'; }}
          else {{ tag.textContent='⚠ 未找到该型号的CT表(工序/进度将不可用)'; tag.style.color='#dc2626'; }}
          specs.forEach(function(s){{ var o=document.createElement('option'); o.value=s.spec; o.textContent=s.name+' ('+s.spec+')'; sel.appendChild(o); }});
          if(selSpec) sel.value=selSpec;
        }}).catch(function(){{ tag.textContent=''; }});
    }}
    function resetUrgForm() {{
      document.getElementById('urg_batch_id').value='';
      document.querySelector('input[name=batch_name]').value='';
      document.getElementById('urg_model').value=''; document.getElementById('urg_ctmodel').textContent='';
      document.getElementById('urg_spec').innerHTML='<option value="">请先选产品</option>';
      document.querySelector('input[name=plan_end]').value='';
      document.getElementById('urg_lots_ta').value=''; urgLotCount();
      document.getElementById('urg_submit').textContent='新建加急批次';
      document.getElementById('urg_cancel').style.display='none';
    }}
    function editUrg(id) {{
      fetch('/urgent_detail?batch='+id).then(function(r){{return r.json();}}).then(function(d){{
        if(!d || !d.id){{ wipToast('加载失败'); return; }}
        document.getElementById('urg_batch_id').value=d.id;
        document.querySelector('input[name=batch_name]').value=d.batch_name||'';
        var ms=document.getElementById('urg_model'); loadProdList();
        if(d.product && !Array.prototype.some.call(ms.options,function(o){{return o.value===d.product;}})){{
          var mo=document.createElement('option'); mo.value=d.product; mo.textContent=d.product; ms.appendChild(mo);
        }}
        ms.value=d.product||'';
        document.querySelector('input[name=plan_end]').value=d.plan_end||'';
        loadUrgInfo(d.target_spec||'');
        var lots=(d.lots||[]).map(function(l){{return l.lot_id;}});
        document.getElementById('urg_lots_ta').value=lots.join('\\n'); urgLotCount();
        document.getElementById('urg_submit').textContent='保存修改';
        document.getElementById('urg_cancel').style.display='';
        document.getElementById('urg_model').scrollIntoView({{behavior:'smooth',block:'center'}});
      }}).catch(function(){{ wipToast('加载失败'); }});
    }}
    function _urgParseLots() {{
      // textarea 文本 → Lot 列表:换行/逗号分隔,逐个去空白,忽略大小写去重(保留首次原样)
      var raw=document.getElementById('urg_lots_ta').value||'';
      var seen={{}}, out=[];
      raw.replace(/,/g,'\\n').split('\\n').forEach(function(s){{
        s=s.trim(); if(!s) return;
        var k=s.toUpperCase(); if(seen[k]) return; seen[k]=1; out.push(s);
      }});
      return out;
    }}
    function urgLotCount() {{
      document.getElementById('urg_lotcnt').textContent='共 '+_urgParseLots().length+' 个 Lot';
    }}
    function collectUrgLots() {{
      var vals=_urgParseLots();
      if(!vals.length){{ wipToast('请至少粘贴一个 Lot 号(每行一个)'); return false; }}
      document.getElementById('urg_lots_hidden').value=vals.join('\\n');
      return true;
    }}
    function _ue(s){{return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
    function urgDetail(id, name) {{
      document.getElementById('urgTitle').textContent='加急批次 '+name+' · Lot 明细';
      document.getElementById('urgMask').style.display='block';
      document.getElementById('urgBox').style.display='flex';
      var b=document.getElementById('urgBody'); b.innerHTML='加载中…'; document.getElementById('urgHint').textContent='';
      fetch('/urgent_detail?batch='+id).then(function(r){{return r.json();}})
        .then(function(d){{ renderUrgLots(d); }})
        .catch(function(){{ b.innerHTML='<div style="padding:14px;color:#dc2626;">加载失败</div>'; }});
    }}
    function _urgStat(s) {{
      // 状态徽标着色,与「生产进度看板/统计分析」同款配色
      var map={{'等待进站':['#fde047','#1f2937'],'等待出站':['#60a5fa','#1f2937'],'运行中':['#bbf7d0','#15803d'],'HOLD':['#fecaca','#1f2937'],'出站':['#bfdbfe','#1f2937'],'已出站':['#bfdbfe','#1f2937'],'已入库':['#bfdbfe','#1f2937']}};
      var c=map[(s||'').trim()]||['#e2e8f0','#475569'];
      return '<span style="padding:2px 8px;border-radius:4px;font-size:12px;background:'+c[0]+';color:'+c[1]+';white-space:nowrap;">'+_ue(s)+'</span>';
    }}
    function _urgLotTableHTML(d) {{
      // 生成某加急批次的 Lot 明细表(弹窗 / 后台列表内联折叠 共用)
      var lots=(d&&d.lots)||[];
      if(!lots.length) return '<div style="padding:14px;color:#94a3b8;">该批次暂无 Lot</div>';
      var summary='<div style="font-size:12px;color:#64748b;margin:2px 0 8px;">型号 '+_ue(d.product||'')+' · 作业到「'+_ue(d.target_name||d.target_spec||'')+'」即完成 · 共 '+lots.length+' 个 Lot,已完成 '+(d.done_count||0)+'</div>';
      var th='text-align:center;padding:6px 10px;background:#eef2f7;';
      var h='<table style="border-collapse:collapse;font-size:13px;width:100%;white-space:nowrap;"><thead><tr style="color:#475569;">'
        +'<td style="'+th+'">Lot号</td><td style="'+th+'">产品型号</td><td style="'+th+'">数量</td>'
        +'<td style="'+th+'">HOLD原因</td><td style="'+th+'">MES备注</td>'
        +'<td style="'+th+'">当前工序</td><td style="'+th+'">状态</td><td style="'+th+'">EQP ID</td>'
        +'<td style="'+th+'">进度</td><td style="'+th+'">Run</td><td style="'+th+'">Wait</td><td style="'+th+'">Hold</td></tr></thead><tbody>';
      lots.forEach(function(l){{
        var td='padding:6px 10px;border-top:1px solid #f1f5f9;text-align:center;';
        var pcol=l.done?'#16a34a':'#2563eb';
        var donetag=l.done?' ✓完成':'';
        h+='<tr><td style="'+td+'font-weight:600;">'+_ue(l.lot_id)+'</td>'
          +'<td style="'+td+'color:#475569;">'+_ue(l.product||'-')+'</td>'
          +'<td style="'+td+'">'+(l.qty||0)+'</td>'
          +'<td style="'+td+'color:#475569;">'+(_ue(l.hold_reason)||'-')+'</td>'
          +'<td style="text-align:left;padding:6px 10px;border-top:1px solid #f1f5f9;color:#334155;white-space:normal;word-break:break-word;min-width:300px;">'+(_ue(l.txn_comment)||'-')+'</td>'
          +'<td style="'+td+'white-space:nowrap;">'+_ue(l.spec_name||l.spec||'-')+'</td>'
          +'<td style="'+td+'">'+_urgStat(l.status)+'</td>'
          +'<td style="'+td+'">'+(_ue(l.eqpld)||'-')+'</td>'
          +'<td style="'+td+'color:'+pcol+';font-weight:600;">'+(l.progress||0)+'%'+donetag+'</td>'
          +'<td style="'+td+'">'+_ue(l.run_time)+'</td><td style="'+td+'">'+_ue(l.wait_time)+'</td><td style="'+td+'">'+_ue(l.hold_time)+'</td></tr>';
      }});
      h+='</tbody></table>';
      return summary+h;
    }}
    function renderUrgLots(d) {{
      document.getElementById('urgHint').textContent='型号 '+(d.product||'')+' · 作业到「'+(d.target_name||d.target_spec||'')+'」即完成 · 共 '+((d&&d.lots)||[]).length+' 个 Lot,已完成 '+(d.done_count||0);
      document.getElementById('urgBody').innerHTML=_urgLotTableHTML(d);
    }}
    // 后台加急列表:点批次名 → 内联展开/收起该批次 Lot 明细(首次展开才拉取)
    function toggleUrgRow(id) {{
      var row=document.getElementById('urgrow_'+id), car=document.getElementById('urgcaret_'+id);
      if(!row) return;
      if(row.style.display==='none'){{
        row.style.display=''; if(car) car.textContent='▼';
        if(!row.getAttribute('data-loaded')){{
          var body=document.getElementById('urgrowbody_'+id); body.innerHTML='<div style="padding:10px;color:#94a3b8;">展开中…</div>';
          fetch('/urgent_detail?batch='+id).then(function(r){{return r.json();}}).then(function(d){{
            body.innerHTML=_urgLotTableHTML(d); row.setAttribute('data-loaded','1');
          }}).catch(function(){{ body.innerHTML='<div style="padding:10px;color:#dc2626;">加载失败</div>'; }});
        }}
      }} else {{ row.style.display='none'; if(car) car.textContent='▶'; }}
    }}
    function hideUrg(){{ document.getElementById('urgMask').style.display='none'; document.getElementById('urgBox').style.display='none'; }}
    </script>"""
    acct_panel = f"""{users_panel}"""
    orders_sec = f"""
      <div style="margin-bottom:14px;">
        <a href="/export/json" class="btn-reset" style="width:auto;display:inline-block;padding:7px 16px;">导出 JSON</a>
        <a href="/export/csv" class="btn-reset" style="width:auto;display:inline-block;padding:7px 16px;">导出 CSV</a>
      </div>
      <div class="admin-grid">
        <div class="panel admin-list-panel">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
            <h3 style="margin:0;">工单列表 · 共 {len(orders)} 条</h3>
            <input id="admin_wo_search" placeholder="搜工单号 / 型号 / 客户 / 需求方" oninput="adminSearch()"
                   style="padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px;width:230px;">
          </div>
          <div class="admin-list-scroll"><table style="text-align:center;">
            <thead><tr>
              <th style="text-align:center;">工单号</th><th style="text-align:center;">型号</th>
              <th style="text-align:center;">客户</th><th style="text-align:center;">优先级</th>
              <th style="text-align:center;">需求方</th><th style="text-align:center;">操作</th>
            </tr></thead>
            <tbody id="admin_tbody">{rows}</tbody>
          </table></div>
        </div>
        <div class="panel admin-form-panel"><h3>{form_title}</h3>
          <div class="admin-form-scroll">{form_section}</div>
        </div>
      </div>
      <script>
      var ADMIN_ORDERS = {admin_orders_js};
      function adminSearch(){{
        var kw=document.getElementById('admin_wo_search').value.trim().toLowerCase();
        var tb=document.getElementById('admin_tbody');
        tb.querySelectorAll('tr[data-search]').forEach(function(tr){{
          tr.style.display = (!kw || tr.dataset.search.split(/\\s+/).some(function(w){{return w.endsWith(kw);}})) ? '' : 'none';
        }});
      }}
      function _adminForm(){{ return document.querySelector('form.wo-form'); }}
      function _adminTitle(t){{ var h=document.querySelector('.admin-form-panel h3'); if(h) h.textContent=t; }}
      // 点「编辑」:用 JS 回填右侧表单,不跳转、不刷新列表(保住查询/滚动位置,方便批量维护)
      function adminEditRow(id){{
        var o=ADMIN_ORDERS[id]; var f=_adminForm(); if(!o||!f) return;
        if(typeof WO_IS_EDIT!=='undefined') WO_IS_EDIT=true;
        f.setAttribute('action','/admin/update');
        var idf=f.querySelector('[name=id]'); if(idf) idf.value=o.id;
        var wo=document.getElementById('wo_no_input'); if(wo) wo.value=o.wo_no||'';
        // 产品主型号 / 子项目
        var main=o.product||'', sub='';
        if(main.indexOf('/')>=0){{ var pp=main.split('/'); main=pp[0].trim(); sub=(pp[1]||'').trim(); }}
        var pm=document.getElementById('product_main');
        if(pm){{
          pm.value=main;
          if(pm.value!==main && main){{ var op=document.createElement('option'); op.value=main; op.textContent=main; pm.appendChild(op); pm.value=main; }}
        }}
        var sr=document.getElementById('sub_row'); if(sr) sr.style.display=(main==='MB12')?'flex':'none';
        if(main==='MB12'){{ var ps=document.getElementById('product_sub'); if(ps) ps.value=(sub||'4in1'); }}
        if(typeof FINAL_PROC_CUR!=='undefined') FINAL_PROC_CUR=o.final_process||'';
        if(typeof rebuildFinalProc==='function') rebuildFinalProc();
        // 回填 ENG 验证时间
        if(typeof ENG_TIMES_CUR!=='undefined') ENG_TIMES_CUR={{}};
        if(typeof rebuildEngRows==='function') rebuildEngRows();
        if(o.eng_times){{
          try{{
            var _et=JSON.parse(o.eng_times);
            _et.forEach(function(e){{ if(e.spec&&e.eng_h) ENG_TIMES_CUR[e.spec]={{eng_h:e.eng_h,note:e.note||''}}; }});
            rebuildEngRows();
          }}catch(_xe){{}}
        }}
        var setv=function(name,v){{ var el=f.querySelector('[name='+name+']'); if(el) el.value=v||''; }};
        setv('customer',o.customer); setv('demander',o.demander); setv('deliver_qty',o.deliver_qty); setv('remark',o.remark);
        if(f.priority){{ for(var i=0;i<f.priority.options.length;i++) if(f.priority.options[i].value===o.priority) f.priority.selectedIndex=i; }}
        var ps2=document.getElementById('plan_start'); if(ps2) ps2.value=o.plan_start||'';
        var pe2=document.getElementById('plan_end'); if(pe2) pe2.value=o.plan_end||'';
        var cs=document.getElementById('clear_plan_start'); if(cs) cs.value='';
        var ce=document.getElementById('clear_plan_end'); if(ce) ce.value='';
        var wh=document.getElementById('wo_hint'); if(wh) wh.textContent='';
        _adminTitle('编辑工单');
        var panel=document.querySelector('.admin-form-panel'); if(panel) panel.scrollIntoView({{behavior:'smooth',block:'start'}});
      }}
      // 取消编辑 / 保存成功后:把表单复位为「新建」态(不刷新页面)
      function adminResetForm(){{
        var f=_adminForm(); if(!f) return;
        if(typeof WO_IS_EDIT!=='undefined') WO_IS_EDIT=false;
        f.setAttribute('action','/admin/create'); f.reset();
        var idf=f.querySelector('[name=id]'); if(idf) idf.value='';
        var sr=document.getElementById('sub_row'); if(sr) sr.style.display='none';
        if(typeof FINAL_PROC_CUR!=='undefined') FINAL_PROC_CUR='';
        if(typeof rebuildFinalProc==='function') rebuildFinalProc();
        if(typeof ENG_TIMES_CUR!=='undefined') ENG_TIMES_CUR={{}};
        if(typeof rebuildEngRows==='function') rebuildEngRows();
        var wh=document.getElementById('wo_hint'); if(wh) wh.textContent='';
        _adminTitle('新建工单');
      }}
      function adminToast(msg){{
        var t=document.getElementById('admin_toast');
        if(!t){{ t=document.createElement('div'); t.id='admin_toast';
          t.style.cssText='position:fixed;top:18px;left:50%;transform:translateX(-50%);background:#16a34a;color:#fff;padding:8px 18px;border-radius:8px;font-size:14px;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,.18);transition:opacity .3s;';
          document.body.appendChild(t); }}
        t.textContent=msg; t.style.opacity='1';
        clearTimeout(t._tm); t._tm=setTimeout(function(){{ t.style.opacity='0'; }},1800);
      }}
      // 拦截工单表单提交:编辑态(有id)走 AJAX 就地更新该行,不刷新列表;新建态放行(整页提交)
      document.addEventListener('submit', function(ev){{
        var f=ev.target;
        if(!f.classList || !f.classList.contains('wo-form')) return;
        var idf=f.querySelector('[name=id]'); var idv=idf? (idf.value||'').trim() : '';
        if(!idv) return;   // 新建:不拦截
        ev.preventDefault();
        var body=new URLSearchParams();
        var engItems=[];
        new FormData(f).forEach(function(v,k){{
          if(k.startsWith('eng_h_')){{ var sp=k.slice(6); var eh=parseFloat(v)||0; if(eh>0) engItems.push({{spec:sp,eng_h:eh,note:''}}); }}
          else if(k.startsWith('eng_note_')){{ var sp2=k.slice(9);
            var ei=null; for(var ei2=0;ei2<engItems.length;ei2++){{ if(engItems[ei2].spec===sp2){{ ei=engItems[ei2]; break; }} }}
            if(ei) ei.note=v; }}
          else body.append(k,v);
        }});
        if(engItems.length>0) body.append('eng_times',JSON.stringify(engItems));
        body.append('ajax','1');
        fetch('/admin/update',{{method:'POST',headers:{{'X-Requested-With':'XMLHttpRequest'}},body:body}})
          .then(function(r){{ return r.json(); }})
          .then(function(d){{
            if(!d || !d.ok){{ wipToast((d&&d.msg)||'保存失败'); return; }}
            var tr=document.querySelector('#admin_tbody tr[data-id="'+d.id+'"]');
            if(tr && d.row){{ var tmp=document.createElement('tbody'); tmp.innerHTML=d.row.trim();
              var ntr=tmp.firstElementChild; if(ntr) tr.parentNode.replaceChild(ntr,tr); }}
            if(d.order) ADMIN_ORDERS[d.id]=d.order;
            adminSearch();        // 重新套用当前查询(行内容可能变,保持筛选一致)
            adminResetForm();
            adminToast(d.msg||'已更新');
          }})
          .catch(function(){{ wipToast('网络错误,保存失败'); }});
      }});
      </script>"""
    # 从 MES 自动读取的产品型号 → 复选框选择要抓取的(勾选即精确匹配,避免手输匹配不到)
    if mes_products:
        sel_n = sum(1 for p in mes_products if p["checked"])
        mes_checks = "".join(
            f"""<label class="prodchk{' on' if p['checked'] else ''}" data-name="{_html.escape(p['name'].lower())}">
              <input type="checkbox" name="prod" value="{_html.escape(p['name'])}" {'checked' if p['checked'] else ''}>
              <span class="pcn">{_html.escape(p['name'])}</span>
              <span class="pcc">{p['count']}</span>
            </label>""" for p in mes_products)
        mes_pick = f"""
        <div style="border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:14px;background:#f8fafc;">
          <div style="font-size:13px;color:#0f172a;font-weight:600;margin-bottom:4px;">从 MES 自动读取产品型号 · 勾选要抓取的</div>
          <div style="font-size:12px;color:#64748b;margin-bottom:10px;">
            下列型号直接来自 MES 数据库(PACKAGESIZE),括号内为批次数。勾选即<b>精确匹配抓取</b>,
            无需手输、不会匹配不到。已勾选 <b id="prodselN">{sel_n}</b> / 共 {len(mes_products)} 个。保存后立即按新名单重新抓取。
          </div>
          <input id="prodfilter" placeholder="搜型号…" oninput="filterProd(this.value)"
                 style="padding:6px 10px;border:1px solid #e2e8f0;border-radius:6px;width:200px;margin-bottom:10px;">
          <form method="post" action="/admin/sync_products">
            <div style="margin-bottom:8px;">
              <a href="javascript:void(0)" onclick="prodAll(true)" style="color:#2563eb;font-size:12px;margin-right:12px;">全选</a>
              <a href="javascript:void(0)" onclick="prodAll(false)" style="color:#64748b;font-size:12px;">清空</a>
            </div>
            <div id="prodgrid" style="display:flex;flex-wrap:wrap;gap:8px;max-height:280px;overflow:auto;margin-bottom:12px;">
              {mes_checks}
            </div>
            <button type="submit" class="btn-primary" style="width:auto;padding:8px 18px;"
              onclick="return confirm('保存白名单并立即重新抓取 MES?未勾选且原本在名单中的型号将停止抓取。');">
              💾 保存并立即抓取
            </button>
          </form>
        </div>
        <style>
          .prodchk {{ display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border:1px solid #e2e8f0;
            border-radius:20px;background:#fff;cursor:pointer;font-size:13px;user-select:none; }}
          .prodchk.on {{ background:#eef2ff;border-color:#a5b4fc; }}
          .prodchk .pcn {{ color:#1f2937;font-weight:600; }}
          .prodchk .pcc {{ color:#94a3b8;font-size:11px; }}
          .prodchk input {{ accent-color:#2563eb; }}
        </style>
        <script>
          function _syncOn(cb){{ cb.closest('.prodchk').classList.toggle('on', cb.checked); }}
          document.querySelectorAll('#prodgrid input[type=checkbox]').forEach(function(cb){{
            cb.addEventListener('change', function(){{ _syncOn(cb); _prodCount(); }});
          }});
          function prodAll(v){{
            document.querySelectorAll('#prodgrid .prodchk').forEach(function(l){{
              if(l.style.display==='none') return;            // 只对当前可见项操作
              var cb=l.querySelector('input'); cb.checked=v; _syncOn(cb);
            }});
            _prodCount();
          }}
          function _prodCount(){{
            var n=document.querySelectorAll('#prodgrid input:checked').length;
            var el=document.getElementById('prodselN'); if(el) el.textContent=n;
          }}
          function filterProd(kw){{
            kw=(kw||'').trim().toLowerCase();
            document.querySelectorAll('#prodgrid .prodchk').forEach(function(l){{
              l.style.display = (!kw || l.dataset.name.endsWith(kw)) ? '' : 'none';
            }});
          }}
        </script>"""
    else:
        mes_pick = ('<div style="font-size:13px;color:#94a3b8;margin-bottom:14px;">'
                    '未读取到 MES 产品型号(请确认 MES数据库.csv 存在)。可在下方手动输入型号。</div>')

    ct_sec = f"""
      <div class="panel"><h3>基础数据管理 · 产品型号(MES 抓取白名单)</h3>
        {mes_pick}
        <div style="font-size:12px;color:#64748b;margin-bottom:6px;">手动添加(用于 MES 里暂时查不到、或需前缀兜底的特殊型号):</div>
        <form method="post" action="/admin/base_add" style="margin-bottom:12px;">
          <input type="hidden" name="kind" value="product">
          <input name="value" placeholder="输入新产品型号" style="padding:7px 10px;border:1px solid #e2e8f0;border-radius:6px;">
          <button type="submit" class="btn-primary" style="width:auto;padding:7px 16px;">+ 添加</button>
        </form>
        <div style="font-size:12px;color:#64748b;margin-bottom:6px;">当前白名单(点 × 移除):</div>
        <div class="btags">{prod_tags}</div>
      </div>
      {owner_panel}
      <div class="panel"><h3>CT 表管理(用于进度/风险计算)</h3>
        <div style="font-size:13px;color:#475569;margin-bottom:10px;">
          上传各型号 CT 表(.xlsx/.csv)。<b>文件名即型号</b>,如 <code>MB12.xlsx</code> 对应 MB12 工单。
          需含列:SEQNO、SPECNAME、OTDO_CT。上传后立即按"当前工序累计CT/总CT"重算进度,并据交期判风险。
        </div>
        <form method="post" action="/admin/upload_ct" enctype="multipart/form-data" style="margin-bottom:12px;">
          <input type="file" name="file" accept=".xlsx,.csv" required>
          <button type="submit" class="btn-primary" style="width:auto;padding:7px 16px;">上传 CT 表</button>
        </form>
        <div style="font-size:12px;color:#64748b;margin-bottom:8px;">
          <b>当前 CT 计算方式</b>:每个型号可选「按上传CT」(你上传的原始虚高值)或
          「按校准后」(用量产实测 RUNTIME 修正后的值)。进度看板/ETA/风险按所选方式计算,可随时切换。</div>
        <table><thead><tr><th>型号</th><th>按上传CT</th><th>按校准后</th><th>当前计算方式</th><th>上传时间</th><th>操作</th></tr></thead>
        <tbody>{ct_rows}</tbody></table>
      </div>"""
    _tabs = [("orders", "📋 工单管理"), ("urgent", "⚡ 加急验证Lot"), ("exc", "⏱ 异常监控"),
             ("data", "🔄 数据源"), ("ct", "📊 CT与基础数据"), ("acct", "👤 账号设置")]
    _navbtns = "".join(
        f'<button type="button" class="asubn{" on" if i == 0 else ""}" '
        f'onclick="showASec(\'{k}\',this)">{t}</button>' for i, (k, t) in enumerate(_tabs))
    body = f"""
    <style>
    .asubn {{ display:block;width:100%;text-align:left;border:none;background:transparent;
      color:#cbd5e1;padding:10px 14px;cursor:pointer;font-size:14px;border-radius:8px;margin-bottom:4px; }}
    .asubn:hover {{ background:#1e293b;color:#fff; }}
    .asubn.on {{ background:#2563eb;color:#fff;font-weight:600; }}
    .asec {{ display:none; }} .asec.on {{ display:block; }}
    </style>
    <div class="page-title">后台数据管理</div>
    <div class="page-sub">管理系统工单数据,支持增删改查、数据源抓取与导入导出 ·
      <a href="/logout" style="color:#2563eb;">退出登录</a></div>
    {msg_html}
    <div style="display:flex;gap:18px;align-items:flex-start;">
      <div style="flex:0 0 150px;position:sticky;top:16px;background:#0f172a;border-radius:10px;padding:8px;">
        {_navbtns}
      </div>
      <div style="flex:1;min-width:0;">
        <div class="asec on" id="asec-orders">{orders_sec}</div>
        <div class="asec" id="asec-urgent">{urgent_panel}</div>
        <div class="asec" id="asec-exc">{exc_panel}</div>
        <div class="asec" id="asec-data">{datasource_panel}</div>
        <div class="asec" id="asec-ct">{ct_sec}</div>
        <div class="asec" id="asec-acct">{acct_panel}</div>
      </div>
    </div>
    <script>
    function showASec(id, btn) {{
      document.querySelectorAll('.asec').forEach(function(s){{ s.classList.remove('on'); }});
      var el=document.getElementById('asec-'+id); if(el) el.classList.add('on');
      document.querySelectorAll('.asubn').forEach(function(b){{ b.classList.remove('on'); }});
      if(btn) btn.classList.add('on');
      try {{ history.replaceState(null,'','#'+id); }} catch(e) {{}}
    }}
    (function(){{ var h=(location.hash||'').replace('#','');
      var tabs=['orders','urgent','exc','data','ct','acct'];
      if(tabs.indexOf(h)>=0){{
        var btn=document.querySelector('.asubn[onclick*="\\''+h+'\\'"]');
        showASec(h, btn);
      }} }})();
    </script>
    """
    return layout("admin", "后台管理", body)


# ---------- SWR 要求查询页 ----------
def render_swr():
    """SWR(特殊作业要求)查询:输入工单号/尾号,匹配公盘 PIE 投放的要求 PDF 并预览。
    配置:swr.py 顶部 SWR_ROOT(公盘路径)。"""
    body = """<div class="page-title">📄 SWR 要求查询</div>
    <div class="page-sub">输入工单号(如 *60000003599)或尾号(如 03599),匹配公盘上 PIE 投放的
    特殊作业要求 PDF · 多个命中全部列出(文件名以输入开头者优先)· 点击「查看」直接预览</div>
    <div class="eq-search" style="max-width:520px;">
      <input id="swrKw" placeholder="工单号 / 尾号,如 03599" onkeydown="if(event.key==='Enter')swrSearch();"
             style="flex:1;height:36px;padding:8px 11px;border:1px solid #cbd5e1;border-radius:8px;font-size:13px;min-width:0;">
      <button onclick="swrSearch()" style="width:36px;height:36px;flex-shrink:0;border-radius:8px;font-size:14px;cursor:pointer;border:1px solid #2563eb;background:#2563eb;color:#fff;line-height:1;">🔍</button>
    </div>
    <div id="swrCfg" class="eq-empty" style="display:none;margin-bottom:12px;color:#b45309;border-color:#f59e0b;">
      ⚙ 尚未配置公盘路径 —— 在 swr.py 顶部 <b>SWR_ROOT</b> 一行填入公盘地址(如 r"\\\\YOUR_FILE_SERVER\\\\swr"),重启服务后生效
    </div>
    <div id="swrBody" class="eq-empty">输入工单号或尾号开始查询</div>

    <!-- 预览弹窗 -->
    <div id="swrMask" onclick="swrClose()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:49;"></div>
    <div id="swrBox" style="display:none;position:fixed;top:4vh;left:50%;transform:translate(-50%,0);width:min(1100px,92vw);height:88vh;background:#fff;border-radius:12px;z-index:50;box-shadow:0 20px 60px rgba(0,0,0,.35);overflow:hidden;flex-direction:column;">
      <div style="display:flex;align-items:center;gap:10px;padding:10px 18px;border-bottom:1px solid #e2e8f0;">
        <b id="swrTitle" style="font-size:14px;color:#0f172a;">预览</b>
        <span style="flex:1;"></span>
        <a id="swrDl" href="#" download style="font-size:12px;color:#2563eb;text-decoration:none;">下载</a>
        <button onclick="swrClose()" style="border:none;background:none;cursor:pointer;font-size:14px;color:#64748b;">关闭</button>
      </div>
      <iframe id="swrFrame" src="" style="flex:1;width:100%;border:none;background:#fff;"></iframe>
    </div>

    <script>
    function fmtTime(ts){ if(!ts) return '—';
      var d=new Date(ts*1000),mo=String(d.getMonth()+1).padStart(2,'0'),
          da=String(d.getDate()).padStart(2,'0'),hh=String(d.getHours()).padStart(2,'0'),
          mi=String(d.getMinutes()).padStart(2,'0');
      return mo+'-'+da+' '+hh+':'+mi; }
    function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    var SWR_BASE='';
    function swrSearch(){
      var kw=document.getElementById('swrKw').value.trim();
      var body=document.getElementById('swrBody');
      if(!kw){ body.innerHTML='<div class="eq-empty">输入工单号或尾号开始查询</div>'; return; }
      body.innerHTML='<div class="loadbox"><span class="spin"></span><div style="margin-top:10px;">正在匹配公盘…</div></div>';
      fetch('/swr/data?kw='+encodeURIComponent(kw)).then(function(r){return r.json();}).then(function(d){
        if(!d.configured){
          document.getElementById('swrCfg').style.display='block';
          body.innerHTML='<div class="eq-empty">未配置公盘路径(SWR_ROOT)</div>';
          return;
        }
        document.getElementById('swrCfg').style.display='none';
        var files=d.files||[];
        if(!files.length){ body.innerHTML='<div class="eq-empty" style="text-shadow:none;">未找到匹配『 '+esc(kw)+' 』的 PDF<br><span style="font-size:12px;color:#94a3b8;">请确认工单号无误,或 PIE 尚未投放/文件名不含该号</span></div>'; return; }
        body.innerHTML='<div class="eq-hd"><h3>匹配 '+files.length+' 份要求文件</h3></div>'
          +'<table class="eq-table"><thead><tr>'
          +'<th style="width:46%;">文件名</th><th style="text-align:center;">修改时间</th>'
          +'<th style="text-align:center;">大小</th><th style="text-align:center;width:120px;">操作</th></tr></thead><tbody>'
          +files.map(function(f,i){
            return '<tr><td style="font-weight:600;">'+esc(f.name)+'</td>'
              +'<td style="text-align:center;color:#64748b;">'+fmtTime(f.mtime)+'</td>'
              +'<td style="text-align:center;color:#64748b;">'+(f.size>1048576?(f.size/1048576).toFixed(1)+' MB':Math.max(1,Math.round(f.size/1024))+' KB')+'</td>'
              +'<td style="text-align:center;"><button onclick="swrView('+i+')" style="padding:4px 12px;border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;font-size:12px;cursor:pointer;">查看</button></td></tr>';}).join('')
          +'</tbody></table>';
        SWR_FILES=files;
      }).catch(function(){ body.innerHTML='<div class="eq-empty" style="color:#dc2626;">查询失败(服务异常)</div>'; });
    }
    var SWR_FILES=[];
    function swrView(i){
      var f=SWR_FILES[i]; if(!f) return;
      document.getElementById('swrTitle').textContent=f.name;
      document.getElementById('swrDl').href='/swr/file?p='+encodeURIComponent(f.rel);
      document.getElementById('swrFrame').src='/swr/file?p='+encodeURIComponent(f.rel);
      document.getElementById('swrMask').style.display='block';
      document.getElementById('swrBox').style.display='flex';
    }
    function swrClose(){
      document.getElementById('swrMask').style.display='none';
      document.getElementById('swrBox').style.display='none';
      document.getElementById('swrFrame').src='';
    }
    </script>
    """
    return layout("swr", "SWR要求查询", body)


# ---------- 健康检查页 ----------
def render_health(data):
    """一键体检:只读汇总 数据泵/MES 快照/型号配置/存储,红黄绿+修复指引。"""
    csv = data.get("csv") or {}
    log = data.get("log") or {}
    models = data.get("models") or []
    stores = data.get("stores") or []

    def dot(cls):
        return ('<span style="display:inline-block;width:10px;height:10px;'
                'border-radius:50%;margin-right:6px;background:' + cls + ';"></span>')

    # 快照卡
    if not csv.get("ok"):
        snap_state = '<span style="color:#dc2626;">' + dot("#dc2626") + '缺失</span> —— MES数据库.csv 不存在,检查数据泵/文件路径'
    elif (csv.get("age_min") or 0) > 15:
        snap_state = '<span style="color:#dc2626;">' + dot("#dc2626") + '已停更 ' + str(csv.get("age_min")) + ' 分钟</span> —— 检查 refresh_mes_loop.vbs;侧边栏红标同因'
    elif (csv.get("age_min") or 0) > 5:
        snap_state = '<span style="color:#ca8a04;">' + dot("#ca8a04") + str(csv.get("age_min")) + ' 分钟未更新</span> —— 正常 60~120 秒一幕'
    else:
        snap_state = '<span style="color:#16a34a;">' + dot("#16a34a") + '新鲜(' + str(csv.get("age_min")) + ' 分钟)</span>'
    snap_txt = ('<div>MES 快照: <b>' + snap_state + '</b></div>'
                '<div style="color:#64748b;margin-top:4px;">行数: <b>'
                + str(csv.get("rows", 0)) + '</b> · 上次同步: <b>'
                + _html.escape(str(csv.get("last_at", ""))) + '</b> '
                + _html.escape(str(csv.get("status", ""))) + '</div>')
    # 数据泵卡
    if not log.get("exists"):
        pump_txt = '<div><span style="color:#dc2626;">' + dot("#dc2626") + '未发现 refresh_mes.log</span> —— 数据泵脚本可能未运行</div>'
    else:
        lage = log.get("age_min") or 0
        lcls = "#16a34a" if lage <= 5 else ("#ca8a04" if lage <= 15 else "#dc2626")
        log_tail = _html.escape("<br>".join(log.get("tail") or [])) or "—"
        pump_txt = ('<div>数据泵日志: <b><span style="color:' + lcls + '">' + dot(lcls)
                    + '最后写入 ' + str(lage) + ' 分钟前</span></b></div>'
                    '<div style="color:#64748b;margin-top:4px;font-family:monospace;font-size:12px;'
                    'max-height:96px;overflow:auto;">' + log_tail + '</div>')
    # 型号配置卡
    if not models:
        mtxt = '<div style="color:#94a3b8;">后台尚未录入产品型号(基础数据管理)</div>'
    else:
        rows = []
        bad = 0
        for m in models:
            if not m["ct"]:
                bad += 1
            ct_t = ('<span class="tag tag-done">CT 表'
                    + (' · 用 ' + _html.escape(str(m.get("ct_under") or "")) if m.get("ct_under") else '')
                    + '</span>' if m["ct"] else '<span class="tag tag-low">缺 CT 表</span>')
            smp_t = ('<span style="color:#16a34a;">' + str(m["samples"]) + ' 样本</span>'
                     if m["samples"] >= 20 else
                     ('<span style="color:#ca8a04;">' + str(m["samples"]) + ' 样本</span>'
                      if m["samples"] else '<span style="color:#94a3b8;">0 样本</span>'))
            uph_t = ('· UPH ' + _html.escape(str(m.get("uph") or "—")) if m.get("uph") is not None else '')
            rows.append('<tr><td style="text-align:center;font-weight:600;padding:6px 12px;">'
                        + _html.escape(m["model"]) + '</td>'
                        '<td style="text-align:center;padding:6px 12px;">' + ct_t + '</td>'
                        '<td style="text-align:center;padding:6px 12px;">' + smp_t + ' ' + uph_t + '</td></tr>')
        mtxt = ('<table style="border-collapse:collapse;font-size:13px;width:100%;"><tr style="color:#475569;">'
                '<th style="text-align:center;padding:6px 12px;">型号</th>'
                '<th style="text-align:center;padding:6px 12px;">CT 表</th>'
                '<th style="text-align:center;padding:6px 12px;">设备能力样本</th></tr>'
                + "".join(rows) + '</table>'
                + ('<div style="margin-top:6px;font-size:12px;color:#dc2626;">'
                   + dot("#dc2626") + str(bad) + ' 个型号未上传 CT 表 → 进度/工序顺序无法计算,'
                   '请在 后台管理 → CT 表管理 上传(文件名=型号,如 MB12.xlsx)</div>' if bad else ''))
    # 存储卡
    if stores:
        st_txt = '<div style="color:#64748b;">' + " · ".join(
            _html.escape(f"{n} {s}MB") for n, s in stores) + '</div>'
    else:
        st_txt = '<div style="color:#94a3b8;">未找到业务库文件</div>'
    return layout("health", "健康检查", """
    <div class="page-title">🩺 健康检查</div>
    <div class="page-sub">只读体检,不动任何数据 · 检查时间 _CHK_ · 数据泵/MES 快照/型号配置/存储一览</div>
    <div class="panel"><h3>MES 数据源</h3>_SNAP_</div>
    <div class="panel"><h3>数据泵脚本</h3>_PUMP_</div>
    <div class="panel"><h3>型号配置(白名单)</h3>_MODELLIST_</div>
    <div class="panel"><h3>存储</h3>_STORE_</div>
    """.replace("_CHK_", _html.escape(str(data.get("checked_at", "")))
               ).replace("_SNAP_", snap_txt
               ).replace("_PUMP_", pump_txt
               ).replace("_MODELLIST_", mtxt
               ).replace("_STORE_", st_txt))


# ---------- 使用说明页 ----------
def render_help():
    """系统使用说明:面向日常使用者,按模块讲清每个页面怎么用、数据从哪来。"""
    import version as _ver
    v = _ver.get_version()

    def card(title, inner):
        return ('<div class="panel">'
                '<h3 style="color:#1e293b;border-left:4px solid #2563eb;padding-left:10px;">'
                + title + '</h3>'
                '<div style="font-size:13px;color:#334155;line-height:1.9;">' + inner + '</div></div>')

    overview = (
        '本系统对接 <b>MES 数据库</b>,自动跟踪 塑封/灌封 系列 NTO 工单的生产进度、风险与异常。'
        '数据每 <b>60~120 秒</b>自动从 MES 刷新一次;页面左侧边栏实时显示<b>快照新鲜度</b>'
        '(超过 5 分钟未更新会黄标、15 分钟红标——正常约 1~2 分钟一幕,长时间不更新请检查'
        '数据泵 refresh_mes_loop.vbs 是否在运行)。<br>'
        '浏览类页面<b>无需登录</b>即可查看;数据导出、触发同步、'
        '录入工单、上传 CT、改阈值等 <b>操作需登录</b>(连续 5 次输错密码时账号/IP 将锁定 15 分钟)。')

    pages = (
        '<b>统计分析</b> — 总体概览:工单数量、风险分布、产品在制 WIP 分布(点击柱/下钻)、'
        '产品型号分布、负责人工作量等。<br>'
        '<b>实时WIP</b> — 按 型号×OWNER 过滤的在制 Lot 工序分布(堆叠柱+下钻);'
        '下钻明细含「预计加工完成」列 = 当前批颗数÷该设备实测UPH−已运行(纯加工口径,HOLD 显示"已暂停")。<br>'
        '<b>生产进度看板</b> — 核心页。每个工单一条时间进度条,按 CT 表折算进度%、'
        '标注风险(超前/正常/低风险/高风险)与预计完工。ETA 口径在备注中标注'
        '(标准CT / 实测(含等待) / +设备UPH)。右上角红色按钮进入<b>「⚡ 加急验证 Lot」</b>。<br>'
        '<b>工单明细</b> — 按工单号/型号/负责人筛选查看每个工单的工序级明细。<br>'
        '<b>异常记录</b> — Lot 在工序卡 Wait/HOLD 超时的记录,按工单汇总,可填责任人/备注。<br>'
        '<b>已完成工单</b> — 进度达 100% 的工单归集处。<br>'
        '<b>CT 数据校准</b> — 长期累积 MES 实测工序周期:样本先做<b>碎片净化</b>'
        '(剔除 <30%×粗中位 的近 0 截断记录)再取中位,样本数列显示"有效/原始";'
        '可信度列默认显示 高/中/低 摘要,点击展开 集中度/稳健变异系数。可对比/采用「校准后 CT」。<br>'
        '<b>设备能力</b> — 按 型号×工序×设备 的实测 UPH 中位(样本 60 天滚动,与 CT 校准同窗口);'
        '点设备行展开按料号合并的明细(平均 UPH)。<br>'
        '<b>后台管理</b> — 录入工单、上传 CT 表、设异常阈值、加急 Lot、账号管理等。')

    urgent = (
        '加急验证 Lot 独立于普通工单,以 <b>Lot 号</b> 为主键,作业到选定工序即视为完成。<br>'
        '<b>新建:</b>后台管理 → ⚡ 加急验证 Lot →<br>'
        '① 填批次名称;② 选<b>型号</b>(下拉,来自已上传的 CT 文件,如 MB6/MB12);'
        '③ 选<b>作业到工序</b>(到该工序即完成);④ 填一个或多个 Lot 号;⑤ 选计划交付日。<br>'
        '<b>查看:</b>生产进度看板右上「⚡ 加急验证 Lot」。每个 Lot 显示当前工序、'
        '状态(着色)、Run/Wait/Hold 时长与到目标工序的进度。')

    exc = (
        '<b>怎么产生:</b>Lot 在某工序的 Wait 或 HOLD 等待超过设定阈值时,每次 MES 同步自动生成记录。<br>'
        '<b>阈值在哪设:</b>后台管理 → 异常记录阈值设置。优先级 高→低:'
        '<b>工单级 ＞ 型号+工序级 ＞ 型号级 ＞ 系统默认</b>;某项设 0 = 不监控。<br>'
        '<b>始终以当前阈值为准:</b>调整阈值后,峰值已低于当前阈值的旧记录会自动清除'
        '(例如早期阈值设小产生的误报);<b>未设监控(阈值为 0)则不产生、也不残留异常记录</b>。<br>'
        '<b>备注永久保留:</b>凡填过「责任 Owner」或「备注」的记录,视为已人工确认,'
        '永久保留,不受阈值调整影响。<br>'
        '<b>自动忽略:</b>虚拟站、「送报废仓」等暂存站点的等待不计为异常。<br>'
        '<span style="color:#64748b;">(异常记录页标题旁的「ⓘ 工作原理」也有同样说明。)</span>')

    admin = (
        '<b>录入型号:</b>后台管理 → 基础数据管理 · 产品型号(如 MB1、MB6)。系统据此从 MES 抓取对应工单。<br>'
        '<b>上传 CT 表:</b>上传 xlsx/csv,<b>文件名(去扩展名)= 型号</b>,用于计算进度/工序/ETA。<br>'
        '<b>账号:</b>账号设置可改自己的账号名与密码;<b>超级管理员</b>还能增删账号、设角色、删工单、'
        '切换 CT 计算方式。<br>'
        '<b>数据刷新:</b>MES 每 60~120 秒自动同步;也可在「数据源」手动同步(需登录,'
        '触发全量同步,耗时约 10~30 秒)。')

    hold = (
        '生产遇到缺料、设备占用等<b>可协调异常</b>时,管理员可对工单按工序「挂起」,填恢复作业日 + 原因。<br>'
        '<b>多次 / 多工序:</b>一个工单可同时挂起多道做不了的工序,也可先后多次挂起,历史记录保留、不被覆盖。<br>'
        '<b>看板显示:</b>每一条挂起在其工序位置画一段<b>粉色斜纹间隙</b>(工序自然起点 → 恢复作业日),并标 ⏸ 与恢复日绿线;'
        '鼠标悬停看工序 / 恢复日 / 原因。进度条按全部挂起<b>累计顺延</b>,ETA 同步重算。<br>'
        '<b>操作:</b>点工单左侧「⏸挂起」徽标 → 面板里可对每条单独「解除」,或「➕新增一条挂起」;多处挂起徽标显示「⏸挂起·N处」。')

    bot = (
        '内网系统外网访问不了,配套<b>飞书机器人</b>让手机端也能查 WIP(机器人在内网运行,只「出站」连飞书,不开放任何外网入口)。<br>'
        '在飞书给机器人「WIP 助手」发消息即可:<br>'
        '<b>① 工单号</b> → 在制单返回 产品 / 客户 / 需求方 / 在制·交付数量 / 计划 / 风险提示 / 工单用途 + 各 Lot 明细(工序·状态·Wait/Run/HoldTime);'
        '已完成单返回 产品型号 / 需求方 / 计划开始 / 计划交付 / 完工日 / 工单用途。<br>'
        '<b>② 产品型号 / 关键词</b>(如 MC3、MB12)→ 列出该型号所有工单,<b>回复序号</b>(如 1)看对应工单;支持「型号+残缺号」如 MB12 1153 精准定位。<br>'
        '<b>③ 加急</b> → 发「加急」列出全部批次(回复序号看详情),发批次名看该批次各 Lot 当前状态。<br>'
        '<b>@ 规则:</b>群里需 <b>@机器人</b> 才回复(机器人刚发过列表后,直接回序号免 @);单聊无需 @。')

    faq = (
        '<b>页面打不开/数据为空?</b> 先确认后台已录入型号、MES 数据源 CSV 存在且已同步一次。<br>'
        '<b>进度/工序不准?</b> 确认该型号已上传 CT 表;CT 表是进度与工序的骨架。<br>'
        '<b>异常一大堆?</b> 多半是阈值设太小,调大后旧的误报会在下次同步自动清理(填过备注的会保留)。<br>'
        '<b>侧边栏"数据已停更"红标?</b> MES 快照超过 15 分钟未更新:查看 <安装目录>\refresh_mes.log 尾部,'
        '确认 refresh_mes_loop.vbs 进程在运行;重启该脚本后 1~2 分钟内应恢复。<br>'
        '<b>每周备份:</b>复制 <安装目录>\\common 下的 MES数据库.csv、wip_molding.db、wip_potting.db、'
        'auth.db 与 ct_samples_molding/ct_samples_potting 文件夹即可(只读源+sqlite,热拷贝安全)。<br>'
        '<b>账号:</b> 账号由<b>超级管理员</b>在后台管理 → 账号管理中统一创建分配,如需账号请联系超级管理员。<br>'
        '<b>连续 5 次输错密码?</b> 该账号/来源 IP 将锁定 15 分钟,稍后再试;未自动解锁则重启服务(内存计数)。')

    body = (
        '<div class="page-title">📖 使用说明</div>'
        '<div class="page-sub">塑封/灌封系列 NTO 生产进度管理系统 · 当前版本 ' + _html.escape(str(v)) + '</div>'
        + card('① 系统是做什么的', overview)
        + card('② 各页面怎么用', pages)
        + card('③ 加急验证 Lot', urgent)
        + card('④ 异常记录是怎么工作的', exc)
        + card('⑤ 后台管理关键操作', admin)
        + card('⑥ 工单挂起(多次 / 多工序)', hold)
        + card('⑦ 飞书查询机器人(手机端查 WIP)', bot)
        + card('⑧ 常见问题', faq))
    return layout("help", "使用说明", body)
