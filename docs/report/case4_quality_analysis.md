# Case Study 4 Quality Analysis: OMC Auto-Research vs Single-Agent Baseline

**Topic**: World Models for Embodied AI and Robotics (2021-2026)

**Setup**:
- **OMC产出**: 3个specialized agents (2x Claude Sonnet 4.6 + 1 self-hosted), COO协调, 两phase, 17个文件, $16.26, <2小时
- **Baseline (MD文件)**: 单个AI agent一次性生成的综述, 约400行markdown

---

## 1. Topic贴合度

MD文件有大量内容偏离core topic：

| 内容 | 和"WM for Embodied AI"的关系 | MD | OMC |
|---|---|---|---|
| Dreamer/RSSM/TD-MPC | 核心 | 2页 | 详细覆盖+benchmark数据 |
| Sim-to-real for WM | 核心 | 散见 | 专门failure mode分析 |
| Compounding error | 核心 | 1段narrative | 定量证据(DreamerV3 15步, RoboDreamer 15%) |
| Contact/manipulation dynamics | 核心 | 提及 | deployment readiness matrix覆盖28系统 |
| Sora/VideoPoet是不是WM | 外围（video generation） | 2页专题 | 未覆盖 |
| 3DGS/NeRF | 外围（3D视觉） | 1.5页专题 | 未覆盖 |
| Neurosymbolic | 外围 | 作为Bet 1 | 未覆盖 |
| Industry implications | 外围（商业分析） | 1页 | 未覆盖 |
| Multi-robot WM | 外围 | 作为Bet 4 | 未覆盖 |

MD文件约40%的篇幅花在外围topic上。OMC产出更focused，基本没有跑题内容。

## 2. 分析深度

| 维度 | OMC | MD |
|---|---|---|
| Failure mode taxonomy | ✅ 11个primary FM + 16 sub-types，每个有severity/frequency/mitigation | ❌ 无系统分类，问题散落在文中 |
| Open problem formalization | ✅ 8个OP，有schema(category, severity, status, empirical evidence, directions) | 5个research directions，但无severity/evidence grading |
| 定量证据 | ✅ 引用具体数字（DreamerV3 15步退化，RoboDreamer 15% success） | 少数字，多narrative描述 |
| Paper annotation | ✅ 17篇逐篇annotation（PAPER-001到017），15字段per paper | ❌ 无逐篇annotation |
| Deployment评估 | ✅ 28个系统的latency/memory/edge-compatibility matrix | 有一个粗略table（4行） |
| 文献检索方法论 | ✅ inclusion protocol + keyword taxonomy + search strategy | ❌ 无方法论描述 |

OMC的分析工件更适合后续研究——failure mode taxonomy和annotation template可以直接复用。MD更适合快速阅读理解全貌。

## 3. 准确性

| | OMC | MD |
|---|---|---|
| 引用真实性 | ✅ 35+篇全部验证通过 | ⚠️ 大部分真实，但DayDreamer标2023(实际CoRL 2022)，Li et al. 2026和RoboScape 2025无法验证 |
| 技术描述 | ✅ 精确（引用具体数字和实验设置） | ⚠️ 偶有不精确（如"DreamerV3 fixed hyperparameters"的简化描述） |
| 幻觉风险 | 低（TBD标注诚实） | 中等（部分2025-2026引用可能不存在） |

## 4. Research Ideas质量

| 维度 | OMC (3个ideas) | MD (5个directions) |
|---|---|---|
| 问题锚定 | ✅ 每个idea锚定到具体FM编号和定量证据 | 从narrative观察出发，无formal grounding |
| Technical formulation | ✅ 有数学公式、架构设计、参数量估算 | 概念描述为主，无formulation |
| 可执行性 | ✅ 有target venue + timeline + expected baselines | 方向性建议，无具体执行方案 |
| 新颖性 | 中等（Idea 3 MAWM较好，Idea 1/2和已有工作有overlap） | 中等（方向都是known open problems） |
| 互补性 | ✅ 明确设计为互补（addressing different layers of WM stack） | 5个directions之间关系不清 |

## 5. 总结

| 维度 | OMC | MD | 谁更好 |
|---|---|---|---|
| Topic贴合度 | 紧扣核心 | ~40%外围内容 | **OMC** |
| 分析深度 | 系统化taxonomy + 定量证据 | Narrative为主 | **OMC** |
| 准确性 | 高，TBD诚实 | 较高，少量待验证引用 | **OMC略胜** |
| 可读性 | 需人工整合17个文件 | 开箱即读 | **MD** |
| 前沿覆盖 | 经典MBRL为主(2021-2023) | 覆盖到generative turn(2024-2026) | **MD** |
| Research ideas | 有formal grounding + formulation | 方向性建议 | **OMC** |
| 作为研究起点 | 可直接复用taxonomy和template | 适合快速了解全貌 | 各有用途 |

**一句话**: MD是一篇好的科普综述，OMC产出是一套可以直接带进实验室的研究工件。

---

*Note: OMC产出为single zero-shot iteration结果。通过human review和后续迭代可进一步提高覆盖面和前沿性。*
