svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 920" width="1200" height="920">
  <defs>
    <marker id="arrowFill" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
      <polygon points="0 0, 8 3, 0 6" fill="#1a3a5c"/>
    </marker>
    <marker id="arrowLine" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
      <polygon points="0 0, 8 3, 0 6" fill="#5a7a9a"/>
    </marker>
  </defs>

  <rect width="1200" height="920" fill="#fff"/>

  <!-- TOP BANNER -->
  <rect x="0" y="0" width="1200" height="50" fill="#1a3a5c"/>
  <text x="600" y="31" text-anchor="middle" font-family="SimHei,sans-serif" font-size="18" font-weight="bold" fill="#fff">系统全流程架构图</text>

  <rect x="0" y="50" width="1200" height="26" fill="#f0f2f5"/>
  <text x="600" y="68" text-anchor="middle" font-family="SimSun,sans-serif" font-size="11" fill="#5a7a9a">多源数据接入  →  前置过滤  →  11模型并行扫描  →  三条关联链穿透  →  六模块聚合  →  报告与看板输出</text>

  <!-- ====== 一、数据层 ====== -->
  <rect x="20" y="90" width="1160" height="110" rx="2" fill="#fafbfc" stroke="#d5dce6" stroke-width="1"/>
  <rect x="20" y="90" width="1160" height="30" rx="2" fill="#1a3a5c"/>
  <rect x="20" y="108" width="1160" height="12" fill="#1a3a5c"/>
  <text x="40" y="110" font-family="SimHei,sans-serif" font-size="13" font-weight="bold" fill="#fff">一、数据层 — 多源数据接入</text>

  <rect x="35" y="130" width="340" height="58" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <rect x="35" y="130" width="340" height="20" rx="2" fill="#e8ecf2"/>
  <rect x="35" y="146" width="340" height="4" fill="#e8ecf2"/>
  <text x="205" y="144" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">DMP营销系统</text>
  <text x="205" y="168" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">签约报量：项目地址、签约额、客户信息</text>
  <text x="205" y="182" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">付款条件、合同条款、投标方式等</text>

  <rect x="390" y="130" width="380" height="58" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <rect x="390" y="130" width="380" height="20" rx="2" fill="#e8ecf2"/>
  <rect x="390" y="146" width="380" height="4" fill="#e8ecf2"/>
  <text x="580" y="144" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">审计附表（SAP财务字段融合）</text>
  <text x="580" y="168" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">实际产值、利润、累计收付款、资金余额</text>
  <text x="580" y="182" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">保证金、预收款、逾期、项目状态、停工退场</text>

  <rect x="785" y="130" width="380" height="58" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <rect x="785" y="130" width="380" height="20" rx="2" fill="#e8ecf2"/>
  <rect x="785" y="146" width="380" height="4" fill="#e8ecf2"/>
  <text x="975" y="144" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#2a4a6a">中标报量数据</text>
  <text x="975" y="168" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">中标额、中标时间、招标方式</text>
  <text x="975" y="182" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">用于中标转化率、客户流失监测、新客户识别</text>

  <!-- Arrows -->
  <line x1="205" y1="188" x2="205" y2="225" stroke="#1a3a5c" stroke-width="2" marker-end="url(#arrowFill)"/>
  <line x1="580" y1="188" x2="580" y2="225" stroke="#1a3a5c" stroke-width="2" marker-end="url(#arrowFill)"/>
  <line x1="975" y1="188" x2="975" y2="225" stroke="#1a3a5c" stroke-width="2" marker-end="url(#arrowFill)"/>

  <!-- ====== 二、处理层 ====== -->
  <rect x="20" y="228" width="1160" height="310" rx="2" fill="#fafbfc" stroke="#d5dce6" stroke-width="1"/>
  <rect x="20" y="228" width="1160" height="30" rx="2" fill="#1a3a5c"/>
  <rect x="20" y="246" width="1160" height="12" fill="#1a3a5c"/>
  <text x="40" y="249" font-family="SimHei,sans-serif" font-size="13" font-weight="bold" fill="#fff">二、处理层</text>

  <!-- Pre-filter -->
  <rect x="35" y="268" width="140" height="40" rx="2" fill="#fff" stroke="#1a3a5c" stroke-width="1.5"/>
  <text x="105" y="286" text-anchor="middle" font-family="SimHei,sans-serif" font-size="12" font-weight="bold" fill="#1a3a5c">前置过滤层</text>
  <text x="105" y="301" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">清洗·映射·剔除</text>

  <line x1="175" y1="288" x2="200" y2="288" stroke="#5a7a9a" stroke-width="1.5" marker-end="url(#arrowLine)"/>

  <!-- 11 Models row 1 -->
  <rect x="205" y="262" width="960" height="52" rx="2" fill="#e8ecf2" stroke="#ccd5e0" stroke-width="1"/>
  <text x="220" y="280" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#1a3a5c">点 — 11模型并行扫描（逐项目、逐规则检测，输出问题清单+严重等级+触发规则）</text>

  <!-- Dim 1 row -->
  <rect x="220" y="296" width="300" height="18" rx="2" fill="#fff" stroke="#d5dce6" stroke-width="0.5"/>
  <text x="225" y="309" font-family="SimSun,sans-serif" font-size="10" fill="#4a5a6a">维度一：战略与布局（1.1/1.2/1.3/1.4）</text>
  <!-- Dim 2 row -->
  <rect x="528" y="296" width="350" height="18" rx="2" fill="#fff" stroke="#d5dce6" stroke-width="0.5"/>
  <text x="533" y="309" font-family="SimSun,sans-serif" font-size="10" fill="#4a5a6a">维度二：合同质量与风险穿透（2.1~2.5）</text>
  <!-- Dim 3 row -->
  <rect x="886" y="296" width="270" height="18" rx="2" fill="#fff" stroke="#d5dce6" stroke-width="0.5"/>
  <text x="891" y="309" font-family="SimSun,sans-serif" font-size="10" fill="#4a5a6a">维度三：客户健康度（3.1/3.2）</text>

  <line x1="600" y1="314" x2="600" y2="335" stroke="#1a3a5c" stroke-width="1.5" marker-end="url(#arrowFill)"/>

  <!-- 3 Chains -->
  <rect x="35" y="340" width="1130" height="80" rx="2" fill="#f5f6f8" stroke="#d0d5dd" stroke-width="1"/>
  <text x="50" y="356" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#1a3a5c">线 — 三条关联链穿透（跨模型风险传导追踪，仅提示相关性，不做因果认定）</text>

  <rect x="40" y="366" width="350" height="44" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="215" y="384" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">关联链1：战略 → 风险传导</text>
  <text x="215" y="400" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">1.1区域窜区 → 2.1风险红线 → 2.2盈利底线</text>

  <text x="398" y="395" font-family="SimSun,sans-serif" font-size="18" fill="#ccd0d8">▶</text>

  <rect x="415" y="366" width="350" height="44" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="590" y="384" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">关联链2：客户 → 合同 → 资金传导</text>
  <text x="590" y="400" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">1.3/3.1客户健康 → 2.2盈利偏差 → 2.3资金安全</text>

  <text x="773" y="395" font-family="SimSun,sans-serif" font-size="18" fill="#ccd0d8">▶</text>

  <rect x="790" y="366" width="355" height="44" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="967" y="384" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">关联链3：数据质量 → 决策可靠性</text>
  <text x="967" y="400" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">1.4数据验真 → 全部11模型（数据守门员）</text>

  <!-- 数据治理前提 bar -->
  <rect x="35" y="428" width="1130" height="100" rx="2" fill="#f8f9fb" stroke="#d5dce6" stroke-width="0.5"/>
  <text x="50" y="446" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#1a3a5c">数据治理三前提</text>

  <rect x="50" y="456" width="290" height="28" rx="2" fill="#fff" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="195" y="474" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#3a4a5a">前提一：授权清单动态维护</text>

  <rect x="350" y="456" width="360" height="28" rx="2" fill="#fff" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="530" y="474" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#3a4a5a">前提二：三套系统项目编码统一映射</text>

  <rect x="720" y="456" width="430" height="28" rx="2" fill="#fff" stroke="#d8dde4" stroke-width="0.5"/>
  <text x="935" y="474" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#3a4a5a">前提三：模型级前置数据校验（缺关键字段不执行/缺次要字段降级运行）</text>

  <text x="50" y="504" font-family="SimSun,sans-serif" font-size="10" fill="#7a8a9a">授权清单由市场部按年度发布、每季度核验、变动即时更新，保证窜区识别准确率</text>
  <text x="50" y="520" font-family="SimSun,sans-serif" font-size="10" fill="#7a8a9a">以DMP编码为基准建对照表，将三套编码逐个关联，确保跨系统数据对齐不失效</text>

  <line x1="600" y1="528" x2="600" y2="563" stroke="#1a3a5c" stroke-width="2" marker-end="url(#arrowFill)"/>

  <!-- ====== 三、输出层 ====== -->
  <rect x="20" y="567" width="1160" height="130" rx="2" fill="#fafbfc" stroke="#d5dce6" stroke-width="1"/>
  <rect x="20" y="567" width="1160" height="30" rx="2" fill="#1a3a5c"/>
  <rect x="20" y="585" width="1160" height="12" fill="#1a3a5c"/>
  <text x="40" y="588" font-family="SimHei,sans-serif" font-size="13" font-weight="bold" fill="#fff">三、输出层 — 面 · 六模块经营态势聚合 + 移动端导出</text>

  <rect x="35" y="608" width="172" height="40" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="121" y="626" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">风险仪表板</text>
  <text x="121" y="641" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">全量统计·态势雷达</text>

  <rect x="217" y="608" width="172" height="40" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="303" y="626" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">模型预警明细</text>
  <text x="303" y="641" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">逐项目·逐规则穿透</text>

  <rect x="399" y="608" width="172" height="40" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="485" y="626" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">穿透关联图谱</text>
  <text x="485" y="641" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">三条链·关联追踪</text>

  <rect x="581" y="608" width="190" height="40" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="676" y="626" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">风险-收益评估</text>
  <text x="676" y="641" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">九宫格·散点图·省域图谱</text>

  <rect x="781" y="608" width="190" height="40" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="876" y="626" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">经营健康监测台</text>
  <text x="876" y="641" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">五维雷达·三级对标</text>

  <rect x="981" y="608" width="180" height="40" rx="2" fill="#fff" stroke="#ccd5e0" stroke-width="1"/>
  <text x="1071" y="626" text-anchor="middle" font-family="SimHei,sans-serif" font-size="11" font-weight="bold" fill="#2a4a6a">综合报告+移动端</text>
  <text x="1071" y="641" text-anchor="middle" font-family="SimSun,sans-serif" font-size="10" fill="#6a7a8a">八章节·Excel·二维码</text>

  <!-- Feature badges -->
  <rect x="35" y="658" width="175" height="20" rx="3" fill="#1a3a5c"/>
  <text x="122" y="672" text-anchor="middle" font-family="SimSun,sans-serif" font-size="9" fill="#fff">本地化部署 · 数据不出本机</text>

  <rect x="222" y="658" width="195" height="20" rx="3" fill="#1a3a5c"/>
  <text x="319" y="672" text-anchor="middle" font-family="SimSun,sans-serif" font-size="9" fill="#fff">零门槛 · 浏览器操作 · 无需编程</text>

  <rect x="429" y="658" width="215" height="20" rx="3" fill="#1a3a5c"/>
  <text x="536" y="672" text-anchor="middle" font-family="SimSun,sans-serif" font-size="9" fill="#fff">移动端离线导出 · 二维码扫码即看</text>

  <!-- 左侧标签 -->
  <rect x="6" y="262" width="12" height="52" rx="2" fill="#2a5a8a"/>
  <text x="12" y="294" text-anchor="middle" font-family="SimHei,sans-serif" font-size="8" fill="#fff" transform="rotate(-90,12,294)">点</text>

  <rect x="6" y="340" width="12" height="80" rx="2" fill="#3a6a9a"/>
  <text x="12" y="387" text-anchor="middle" font-family="SimHei,sans-serif" font-size="8" fill="#fff" transform="rotate(-90,12,387)">线</text>

  <rect x="6" y="608" width="12" height="70" rx="2" fill="#4a8aba"/>
  <text x="12" y="650" text-anchor="middle" font-family="SimHei,sans-serif" font-size="8" fill="#fff" transform="rotate(-90,12,650)">面</text>

  <!-- Bottom strip -->
  <rect x="0" y="710" width="1200" height="26" fill="#f0f2f5"/>
  <text x="600" y="728" text-anchor="middle" font-family="SimSun,sans-serif" font-size="9" fill="#8a9aaa">所有数据处理均在本地完成，不依赖云端服务或外部API，杜绝数据泄露风险  |  Flask仅绑定127.0.0.1本地回环地址</text>

  <rect x="0" y="736" width="1200" height="22" fill="#1a3a5c"/>
  <text x="600" y="751" text-anchor="middle" font-family="SimHei,sans-serif" font-size="10" font-weight="bold" fill="#fff">建筑行业跨区经营分析大模型  —  从审计问题发现到经营态势感知</text>
</svg>'''

path = r"c:\Users\sasa\Desktop\模型建设\模型2：市场营销\图1_系统全流程架构图.svg"
with open(path, "w", encoding="utf-8") as f:
    f.write(svg)
print(f"Done: {path}")
