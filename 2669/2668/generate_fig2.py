svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 860" width="1200" height="860">
  <defs>
    <marker id="arrowUp" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
      <polygon points="0 6, 8 3, 0 0" fill="#1a3a5c"/>
    </marker>
    <marker id="arrowRight" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
      <polygon points="0 0, 8 3, 0 6" fill="#5a7a9a"/>
    </marker>
  </defs>

  <rect width="1200" height="860" fill="#fff"/>

  <!-- TOP BANNER -->
  <rect x="0" y="0" width="1200" height="50" fill="#1a3a5c"/>
  <text x="600" y="31" text-anchor="middle" font-family="SimHei,sans-serif" font-size="18" font-weight="bold" fill="#fff">点线面三层架构示意图</text>
  <rect x="0" y="50" width="1200" height="24" fill="#f0f2f5"/>
  <text x="600" y="67" text-anchor="middle" font-family="SimSun,sans-serif" font-size="11" fill="#5a7a9a">单模型检测（点）→ 三条关联链串联（线）→ 六模块经营态势聚合（面）· 逐层递进，从微观到宏观</text>

  <!-- ====== 面 (TOP LAYER - smallest) ====== -->
  <rect x="20" y="90" width="1160" height="145" rx="2" fill="#fafbfc" stroke="#d5dce6" stroke-width="1"/>

  <!-- 面 label badge -->
  <rect x="520" y="78" width="160" height="24" rx="12" fill="#1a3a5c"/>
  <text x="600" y="95" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#fff">面 — 经营态势聚合</text>

  <!-- Six modules -->
  <rect x="40" y="108" width="170" height="48" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="125" y="130" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">风险仪表板</text>
  <text x="125" y="148" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">全量统计 · 态势雷达</text>

  <rect x="222" y="108" width="170" height="48" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="307" y="130" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">模型预警明细</text>
  <text x="307" y="148" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">逐项目 · 逐规则穿透</text>

  <rect x="404" y="108" width="170" height="48" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="489" y="130" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">穿透关联图谱</text>
  <text x="489" y="148" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">三条链 · 关联追踪</text>

  <rect x="586" y="108" width="195" height="48" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="683" y="130" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">风险-收益评估</text>
  <text x="683" y="148" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">九宫格 · 散点图 · 省域图谱</text>

  <rect x="793" y="108" width="195" height="48" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="890" y="130" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">经营健康监测台</text>
  <text x="890" y="148" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">五维雷达 · 三级对标</text>

  <rect x="1000" y="108" width="165" height="48" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="1082" y="130" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">综合报告</text>
  <text x="1082" y="148" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">八章节 · 双格式</text>

  <!-- Output description -->
  <rect x="40" y="164" width="1125" height="24" rx="2" fill="#e8ecf2"/>
  <text x="600" y="181" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#4a5a6a">输出升级：从"问题清单"变为"经营看板+决策建议"  |  支持Web端交互查看与移动端离线导出（二维码扫码即看）</text>

  <!-- Arrows from 面 down to 线 -->
  <line x1="300" y1="235" x2="460" y2="280" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="600" y1="235" x2="600" y2="280" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="900" y1="235" x2="740" y2="280" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>

  <!-- ====== 线 (MIDDLE LAYER) ====== -->
  <rect x="20" y="285" width="1160" height="150" rx="2" fill="#fafbfc" stroke="#d5dce6" stroke-width="1"/>

  <rect x="480" y="274" width="240" height="22" rx="11" fill="#1a3a5c"/>
  <text x="600" y="290" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#fff">线 — 三条关联链串联</text>

  <!-- Chain 1 -->
  <rect x="40" y="310" width="340" height="52" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="210" y="330" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">关联链1：战略 → 风险传导</text>
  <text x="210" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">1.1 区域窜区</text>
  <text x="310" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="12" fill="#ccd5e0">→</text>
  <text x="345" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">2.1 风险红线</text>
  <text x="445" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="12" fill="#ccd5e0">→</text>
  <text x="480" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">2.2 盈利底线</text>

  <!-- Chain 2 -->
  <rect x="400" y="310" width="390" height="52" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="595" y="330" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">关联链2：客户 → 合同 → 资金传导</text>
  <text x="595" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">1.3/3.1 客户健康 → 2.2 盈利偏差 → 2.3 资金安全</text>

  <!-- Chain 3 -->
  <rect x="810" y="310" width="355" height="52" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="987" y="330" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">关联链3：数据质量 → 决策可靠性</text>
  <text x="987" y="348" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">1.4 数据验真 → 全部11模型（守门员）</text>

  <!-- Disclaimer -->
  <rect x="40" y="372" width="1125" height="24" rx="2" fill="#fff8f0"/>
  <text x="600" y="389" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#8a6a3a">关联分析仅用于提示风险相关性和核查方向，不直接替代审计结论中的因果认定</text>

  <!-- Arrows from 线 down to 点 -->
  <line x1="250" y1="435" x2="350" y2="480" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="460" y1="435" x2="490" y2="480" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="600" y1="435" x2="600" y2="480" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="740" y1="435" x2="710" y2="480" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="950" y1="435" x2="850" y2="480" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>

  <!-- ====== 点 (BOTTOM LAYER - largest) ====== -->
  <rect x="20" y="485" width="1160" height="205" rx="2" fill="#fafbfc" stroke="#d5dce6" stroke-width="1"/>

  <rect x="440" y="474" width="320" height="22" rx="11" fill="#1a3a5c"/>
  <text x="600" y="490" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#fff">点 — 11模型并行扫描（逐项目、逐规则检测）</text>

  <!-- 维度一 -->
  <rect x="35" y="510" width="370" height="70" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <rect x="35" y="510" width="370" height="22" rx="2" fill="#e8ecf2"/>
  <rect x="35" y="528" width="370" height="4" fill="#e8ecf2"/>
  <text x="220" y="526" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">维度一：战略与布局</text>

  <rect x="45" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="85" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">1.1</text>
  <text x="85" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">区域窜区</text>

  <rect x="130" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="170" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">1.2</text>
  <text x="170" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">业务结构</text>

  <rect x="215" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="255" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">1.3</text>
  <text x="255" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">战略客户</text>

  <rect x="300" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="340" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">1.4</text>
  <text x="340" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">数据验真</text>

  <!-- 维度二 -->
  <rect x="415" y="510" width="445" height="70" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <rect x="415" y="510" width="445" height="22" rx="2" fill="#e8ecf2"/>
  <rect x="415" y="528" width="445" height="4" fill="#e8ecf2"/>
  <text x="637" y="526" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">维度二：合同质量与风险穿透</text>

  <rect x="425" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="465" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">2.1</text>
  <text x="465" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">风险红线</text>

  <rect x="510" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="550" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">2.2</text>
  <text x="550" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">盈利底线</text>

  <rect x="595" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="635" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">2.3</text>
  <text x="635" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">资金安全</text>

  <rect x="680" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="720" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">2.4</text>
  <text x="720" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">合同条款</text>

  <rect x="765" y="542" width="80" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="805" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">2.5</text>
  <text x="805" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">施工验证</text>

  <!-- 维度三 -->
  <rect x="870" y="510" width="295" height="70" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <rect x="870" y="510" width="295" height="22" rx="2" fill="#e8ecf2"/>
  <rect x="870" y="528" width="295" height="4" fill="#e8ecf2"/>
  <text x="1017" y="526" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">维度三：客户健康度</text>

  <rect x="880" y="542" width="130" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="945" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">3.1</text>
  <text x="945" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">客户全生命周期</text>

  <rect x="1018" y="542" width="130" height="28" rx="2" fill="#f5f7fa" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="1083" y="561" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" font-weight="bold" fill="#3a5a7a">3.2</text>
  <text x="1083" y="556" text-anchor="middle" font-family="SimSun,sans-serif" font-size="7" fill="#7a8a9a">新客户质量</text>

  <!-- Bottom description -->
  <rect x="35" y="592" width="1125" height="22" rx="2" fill="#e8ecf2"/>
  <text x="600" y="608" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#4a5a6a">三个维度 · 11个模型 · 覆盖区域合规、业务结构、合同风险、资金安全、客户健康、施工验证 · 全部项目100%扫描</text>

  <!-- 点线面 vertical label on left -->
  <text x="8" y="170" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#1a3a5c" transform="rotate(-90,8,170)">面</text>
  <text x="8" y="365" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#1a3a5c" transform="rotate(-90,8,365)">线</text>
  <text x="8" y="595" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#1a3a5c" transform="rotate(-90,8,595)">点</text>

  <!-- Arrow between layers on left -->
  <line x1="6" y1="210" x2="6" y2="295" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>
  <line x1="6" y1="400" x2="6" y2="495" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowUp)"/>

  <!-- Right side annotation -->
  <rect x="1130" y="130" width="60" height="24" rx="3" fill="#1a3a5c" opacity="0.8"/>
  <text x="1160" y="147" text-anchor="middle" font-family="SimSun,sans-serif" font-size="9" fill="#fff">宏观</text>
  <line x1="1160" y1="154" x2="1160" y2="610" stroke="#1a3a5c" stroke-width="1" stroke-dasharray="4,3"/>
  <rect x="1130" y="610" width="60" height="24" rx="3" fill="#1a3a5c" opacity="0.8"/>
  <text x="1160" y="627" text-anchor="middle" font-family="SimSun,sans-serif" font-size="9" fill="#fff">微观</text>

  <!-- Bottom banner -->
  <rect x="0" y="710" width="1200" height="24" fill="#f0f2f5"/>
  <text x="600" y="727" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#8a9aaa">从微观的单项目规则检测（点），到中观的跨模型风险传导追踪（线），到宏观的经营态势聚合（面），逐层递进</text>

  <rect x="0" y="734" width="1200" height="22" fill="#1a3a5c"/>
  <text x="600" y="749" text-anchor="middle" font-family="SimHei,sans-serif" font-size="10" font-weight="bold" fill="#fff">建筑行业跨区经营分析大模型  —  从审计问题发现到经营态势感知</text>
</svg>'''

path = r"c:\Users\sasa\Desktop\模型建设\模型2：市场营销\图2_点线面三层架构示意图.svg"
with open(path, "w", encoding="utf-8") as f:
    f.write(svg)
print(f"Done: {path}")
