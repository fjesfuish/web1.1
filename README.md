# NTO 生产进度系统（WIP 工单进度管理）

面向生产制造场景的 WIP（在制品）工单生产进度管理系统：读取 MES 系统导出的
数据库 CSV 快照，跟踪 NTO（新产品验证）工单的生产进度、交期风险与异常，
提供进度看板、工序甘特、CT 工时分析、WIP 超期分析、设备能力与实时 WIP、
飞书查询机器人等功能。

> 本仓库是经过脱敏处理的开源版本：不含任何真实生产数据、账号口令与公司
> 内部信息。首次使用前，请按下面说明自行准备 MES 数据与管理员账号。

## 技术栈

- Python 3.13，**仅标准库**（`http.server` + `sqlite3` + `csv`），无第三方依赖
- 前端为服务端模板生成的 HTML，JS/CSS 内联
- 数据层为 SQLite，数据目录默认为代码目录下的 `common/`

## 快速开始

```bash
python3 app.py
# 默认监听 8000 端口，浏览器打开 http://localhost:8000
```

## 首次使用：准备数据与账号

### 1. MES 数据

在代码目录下新建 `common/` 目录，放入 MES 系统导出的数据库 CSV 快照，
文件名为 `MES数据库.csv`，每 60–120 秒同步一次。CSV 每行对应一个 Lot 的
当前工序；列定义见 `mes.py`。没有 CSV 时系统可启动，但各页面无数据。

### 2. 创建超级管理员账号

源码中**不存放任何默认账号口令**。首次启动前，用环境变量创建超管：

```bash
# Windows PowerShell
$env:WIP_DEFAULT_ADMIN=1
$env:WIP_DEFAULT_ADMIN_USER="admin"
$env:WIP_DEFAULT_ADMIN_PASS="请设置一个至少8位、含字母和数字的强口令"
python3 app.py
```

启动成功后立即取消这三个环境变量并重启服务。首次登录时会强制修改口令。
此后账号统一由超级管理员在后台「账号管理」中创建分配。

### 3. CT 工时表（可选但建议）

在后台导入各型号的 CT（标准工时）表后，进度与工序甘特的计算才准确；
未导入时相关页面会提示缺失。

### 4. SWR 特殊作业要求（可选）

如需查询共享盘上的特殊作业要求 PDF，在 `swr.py` 顶部填写 `SWR_ROOT`
（UNC 共享路径或已映射盘符），留空则该功能显示未配置提示，不影响运行。

### 5. 飞书查询机器人（可选，不含在本仓库）

机器人代码不在本仓库内，需另行获取。凭据一律走环境变量
`FEISHU_APP_ID` / `FEISHU_APP_SECRET`，不要写进代码或提交到仓库。

## 自检

```bash
python3 test_smoke.py
```

冒烟测试覆盖语法编译、页面渲染、空库降级与导入解析。注意：与真实数据
相关的检查项在没有 MES 数据与数据库时不会通过，这是脱敏后的预期状态，
不代表代码损坏。

## 目录说明

```text
app.py            服务入口（http.server，端口 8000）
views.py          页面模板与渲染
db.py             SQLite 数据层
mes.py            MES CSV 快照解析与同步
importer.py       Excel/CSV 导入（CT 表、Owner 表等）
auth.py           登录、会话与密码哈希（PBKDF2-SHA256）
charts.py         图表数据聚合
version.py        版本号管理（version.json）
ct.py / ct_learn.py   CT 工时表与校准
equip.py          设备能力
monitor.py        实时 WIP 监控
swr.py            SWR 特殊作业要求查询
gen_manual_pdf.py 使用说明书 PDF 生成（独立工具，需 fpdf2）
wip_guide.html    WIP 功能使用指南
使用说明书.md     面向日常使用者的说明
test_smoke.py     冒烟/回归测试
```

## 安全提示

- 不要把 `common/` 下的数据库、MES CSV、`.env`、`connections.xml` 提交入库，
  `.gitignore` 已覆盖这些路径。
- 生产部署建议仅内网访问；不要把服务直接暴露到公网。
- 账号口令只存 PBKDF2 哈希；连续输错 5 次会锁定 15 分钟。
