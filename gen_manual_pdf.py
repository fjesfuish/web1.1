# -*- coding: utf-8 -*-
"""生成《NTO 生产进度系统 使用说明书》PDF(中文,fpdf2 + 系统 NotoSansCJK 字体)。
用法(用带 fpdf2 的解释器):  python gen_manual_pdf.py
输出: 使用说明书.pdf
"""
import os
from fpdf import FPDF

# 字体查找顺序:Windows 微软雅黑 → Linux 思源黑体(跨平台可用;找不到则报错提示)
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",                     # Windows 微软雅黑
    "/system/fonts/NotoSansCJK-Regular.ttc",          # Android/Linux 思源黑体
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _find_font():
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    import sys
    print("未找到中文字体,请将字体路径加入 _FONT_CANDIDATES 或安装 Noto Sans CJK。")
    sys.exit(1)


FONT = _find_font()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)) or ".", "使用说明书.pdf")

# ── 内容(轻量 markdown:# 大标题 / ## 章 / ### 节 / - 列表 / > 计算框 / --- 线 / [PB]换页) ──
DOC = r"""
# NTO 生产进度系统 · 使用说明书
副标题 工单排产看板 · 塑封/灌封双系列 NTO · v2.9.0 · 2026-06

本说明书帮助你看懂系统每个功能在表达什么、怎么用,并附上每项指标背后的计算逻辑。

## 一、系统是做什么的
系统从 MES 导出的在制数据(CSV)自动抓取工单与 Lot 明细,把"每个工单走到哪、卡在哪、能否按期交付"可视化出来,辅助排产决策。
- 双系列隔离:塑封(molding)与灌封(potting)两套独立数据库,共享一套账号。
- 自动刷新:每 4 分钟从 MES CSV 同步一次;也可在后台手动"立即刷新"。
- 纯内网部署,无第三方依赖。

## 二、登录与进入
- 打开网址进入登录页(神经网络背景由 MES 实时聚合数据驱动:高风险越多连线越红、脉冲越密)。
- 输入账号密码(由管理员在"后台管理"分配)。
- 选择"塑封系列"或"灌封系列"进入对应看板;任意页面左上角"切换类别"可随时切换。

## 三、先看懂这几个核心概念
- 工单:一张生产订单,单号前缀有含义(05 开头=厂内验证工单,作业到 FVI 即视为完成)。
- Lot(批次):工单下实际流转的批,一个工单可能有多个 Lot/分身(0/E/F 首位)。
- 工序 / 工艺路线:产品从投料到出站要经过的一连串站点;顺序与每站标准耗时来自上传的"CT 表"。
- CT(Cycle Time,周期时间):一道工序从进站到出站的标准耗时(小时)。
- 首批 / 尾批(重要):同一工单跑得最快的批=首批,跑得最慢的批=尾批。尾批决定整单何时能全部完成,所以整单进度、交期风险都以尾批为准。
- MES 状态:运行中(加工中)、等待进站(排队等开工)、等待出站(完工待流出)、HOLD(暂停/异常扣留)。

[PB]
## 四、生产进度看板(甘特图)
这是核心页面。每一行是一个工单,横向时间轴上用进度条展示它的工艺进展与交期风险。

### 顶部风险卡片
五张卡:总工单数 / 超前 / 正常 / 低风险 / 高风险,每张带占比迷你条。点击任一张,按该风险等级筛选下方甘特图。

### 进度条的颜色(按 CT 工序分段)
- 灰色 = 已完成工序(首尾批都已通过)
- 蓝色 = 在制·有产品(该工序当前有 Lot 停留)
- 浅蓝 = 在制·无产品(在首尾批之间、但当前没料的空工序)
- 黄色 = 未作业工序(还没轮到)
- 浅粉 = 残留·已忽略(被管理员标记剔除、不计入尾批的残留 Lot)
- 斜纹 = 验证单 FVI 之后不作业的工序

### 工序小格交互
鼠标移到任一工序段:该段动态高亮浮起,上方弹出浮层显示"工序名 + CT 时间"。点击有产品(蓝色)或残留(粉色)的段,可下钻看该工序的 Lot 明细;管理员可在其中勾选"忽略"剔除残留 Lot。

### 左侧工单信息
优先级徽章(火箭批=红底黄字 / 1-0=红底黑字 / 1-1=黄底黑字 / Normal=灰底黑字)、工单号、交期、运行状态色块、风险等级、产品型号、进度百分比。

### 时间轴上的标记
- 今日:整列加深。
- 交付日:紫色竖线(计划交期)。
- 预计产出(ETA):今日到 ETA 的虚线框,蓝框=能在交期前产出,红框=预计滞后。
- 应到:橙色标记,表示"按日历该到的位置",和实际位置对比看快慢。

### 计算逻辑(点看板里"风险计算说明"亦可展开)
> ① 进度% = 当前工序累计 CT ÷ 总 CT(按 CT 表分段)。整单取最慢分身(尾批)为准。验证单(05 开头)到 FVI 即 100%,FVI 之后不计入。
> ② 预计产出日(ETA) = 今天 + 剩余天数。剩余天数 = 剩余各工序真实流转时间 ÷ 24;优先用 AI 学习的实测时间(加工 RUNTIME + 排队 WAITTIME 的中位数),某工序无样本时退回标准 CT。验证单只累计到 FVI 之前。
> ③ 富余 Buffer = 计划交付日 − 预计产出日(ETA)。大于 0 有富余(蓝色"Buffer X天"),小于 0 滞后(红色"滞后 X天")。
> ④ 风险等级:填了"计划开始日"时按相对富余度(富余 ÷ 计划总周期)分级——大于等于 15% 超前、0~15% 正常、−10%~0 低风险、低于 −10% 高风险;未填计划开始日时退回绝对阈值——富余大于等于 2 天超前、0~2 天正常、−1~0 天低风险、低于 −1 天高风险。
> ⑤ 应到 = 按日历已耗时间占计划周期的比例,对应到的工序位置(仅参考,与实测 CT 无关)。

[PB]
## 五、统计分析
全局数据概览,辅助排产。

### 准时完成率(顶部)
> 计算口径:在"已完成工单"(进度≥100)里,完成日期不晚于计划交付日的占比。即 准时率 = 准时完成数 ÷ 已完成总数 ×100%。完成日缺失时按当天兜底。

### 产品在制 WIP 分布(核心图)
横轴是完整工艺路线(按 CT 表工序顺序),纵轴是各工序当前在制 Lot 数。
- 堆叠柱按状态分色:运行中=绿、等待进站=黄、等待出站=蓝、HOLD=红。
- 黑色折线 = CWIP 累计在制曲线。
- 左上"型号下拉":选型号看其 WIP 分布,默认显示在制最多的型号。
- "搜索框":输产品/型号/工单号全局定位,下拉会自动跳到匹配工单的型号。
- 点击某根柱:下钻看该工序的 Lot 明细。
> WIP(柱高) = 该工序当前停留的在制 Lot 数(按四种状态堆叠)。
> CWIP(黑线) = 该工序及其之后所有工序的在制 Lot 之和(从末工序往前累加),因此左高右低、逐站递减,反映整体在制堆积与流动趋势。

### 产品型号分布
各产品型号的工单数量柱状图,看产能投向。

### 负责人工作量
每位负责人名下各状态的 Lot 数,用横向条形按总量比例显示;点色段可看对应 Lot 明细。
> 负责人归属:按每个 Lot 当前工序 + 状态判定责任人(HOLD 归 ENG Owner,运行/等待归 Inline Owner)。

## 六、工单明细 / 异常记录 / 已完成
- 工单明细:工单列表,可按型号/需求方/优先级/风险/负责人/关键字筛选;点工单号看 Lot 级明细。
- 异常记录:以工单汇总异常(总异常 / Wait / HOLD 时长峰值累计);点工单号看各 Lot 异常明细,管理员可填责任 Owner/备注,支持导出 CSV。
- 已完成工单:进度达 100% 的工单归集于此(已从看板与明细移出),支持按工单号/需求方/型号/负责人查询。

## 七、CT 数据校准
展示各型号×工序的实测周期 CT,供进度分段采用"学习后 CT"使预测更准。
> 计算逻辑:以 observe(跨快照定稿)为主——批次走完工序后记录完整的加工+等待时间,最准;样本不足时用 scan(单快照口径)兜底修正。每个型号×工序取实测样本的中位数,随每次 MES 刷新自动累积。需在后台上传该型号 CT 表作为工序骨架。

## 八、后台管理
账号管理(分配/角色/改密)、产品型号录入、MES 抓取白名单(按 PACKAGESIZE 从严到松匹配)、CT 表上传等。

[PB]
## 九、运维与自检
- 启动: 在程序目录执行  PORT=8000 python3 app.py  ,浏览器访问 http://主机:8000
- 停止/重启: Ctrl+C 或 pkill -f "python3 app.py";改了代码必须重启才生效。重启后需重新登录(会话存内存)。
- 自检/回归测试(纯标准库,只读不写): python3 test_smoke.py  (加 --http 再做 HTTP 冒烟)。退出码 0=全通过。
- 数据: 业务库 common/wip_molding.db、common/wip_potting.db;账号库 common/auth.db。备份直接复制 common/ 目录。

## 十、计算逻辑速查
> 进度% = 尾批已完成累计 CT ÷ 总 CT
> ETA = 今天 + 剩余工序(实测/标准 CT 之和)÷24
> Buffer = 计划交付日 − ETA
> 风险(填了计划开始日)= 富余 ÷ 计划总周期:≥15%超前 / 0~15%正常 / −10%~0低风险 / <−10%高风险
> 风险(未填)= 富余天数:≥2超前 / 0~2正常 / −1~0低风险 / <−1高风险
> 准时完成率 = 已完成且未超期的工单数 ÷ 已完成总数
> WIP = 各工序当前在制 Lot 数;CWIP = 该工序及之后所有工序在制之和
> CT(校准)= 各型号×工序实测(RUNTIME+WAITTIME)样本的中位数

常见问题: 切到某系列看板是空的 → 该系列还没工单数据(灌封未投产即为空);WIP 图工序顺序乱 → 该型号没上传 CT 表;数据不是最新 → 每 4 分钟自动刷新或后台手动刷新。
"""


class PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("cjk", size=8)
        self.set_text_color(150)
        self.cell(0, 6, "NTO 生产进度系统 · 使用说明书 v2.9.0", align="L")
        self.ln(8)

    def footer(self):
        self.set_y(-12)
        self.set_font("cjk", size=8)
        self.set_text_color(150)
        self.cell(0, 6, "第 %d 页" % self.page_no(), align="C")


def build():
    pdf = PDF(format="A4")
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_font("cjk", "", FONT)
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    for raw in DOC.strip("\n").split("\n"):
        line = raw.rstrip()
        if line == "[PB]":
            pdf.add_page(); continue
        if line == "":
            pdf.ln(2.5); continue
        if line == "---":
            y = pdf.get_y(); pdf.set_draw_color(210); pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y); pdf.ln(3); continue

        if line.startswith("# "):
            pdf.set_font("cjk", size=20); pdf.set_text_color(15, 23, 42)
            pdf.multi_cell(W, 10, line[2:]); pdf.ln(2)
        elif line.startswith("副标题 "):
            pdf.set_font("cjk", size=11); pdf.set_text_color(37, 99, 235)
            pdf.multi_cell(W, 6, line[4:]); pdf.ln(3)
        elif line.startswith("## "):
            pdf.ln(2); pdf.set_font("cjk", size=15); pdf.set_text_color(37, 99, 235)
            pdf.multi_cell(W, 8, line[3:]); pdf.ln(1)
        elif line.startswith("### "):
            pdf.set_font("cjk", size=12); pdf.set_text_color(30, 41, 59)
            pdf.multi_cell(W, 7, line[4:]); pdf.ln(0.5)
        elif line.startswith("> "):
            pdf.set_font("cjk", size=10); pdf.set_text_color(51, 65, 85)
            pdf.set_fill_color(241, 245, 249)
            pdf.multi_cell(W, 6, line[2:], fill=True, border=0); pdf.ln(0.5)
        elif line.startswith("- "):
            pdf.set_font("cjk", size=10.5); pdf.set_text_color(40, 50, 65)
            x = pdf.get_x()
            pdf.set_x(x + 4)
            pdf.multi_cell(W - 4, 6, "·  " + line[2:])
        else:
            pdf.set_font("cjk", size=10.5); pdf.set_text_color(40, 50, 65)
            pdf.multi_cell(W, 6, line)

    pdf.output(OUT)
    return OUT, os.path.getsize(OUT)


if __name__ == "__main__":
    path, size = build()
    print("✓ 已生成 %s (%d 字节)" % (path, size))
