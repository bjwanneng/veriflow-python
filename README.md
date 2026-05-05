# VeriFlow-python

AI-driven RTL design pipeline: Natural Language → Python Design Spec → Verilog.

VeriFlow 将 RTL 设计流程分为 4 个阶段，每个阶段由 AI 代理执行，人类在关键节点审核：

```
Stage 1: 需求 → AI → design_spec.py     (Python 设计规格 + 参考模型 + 测试向量)
Stage 2: design_spec.py → AI → Verilog   (Python 函数逐个翻译为 Verilog 模块)
Stage 3: iverilog/cocotb 仿真 → 修复 RTL Bug
Stage 4: lint + synthesis
```

## 核心理念：design_spec.py 三合一

`design_spec.py` 同时承担三个角色：

1. **设计规格** — 接口定义、模块层次、协议、时序
2. **参考模型** — 可运行的算法实现，用标准测试向量验证
3. **翻译蓝图** — 每个 Python 函数映射到一个 Verilog 模块

### Python-to-Verilog NBA 时序约定

Python 的赋值语义天然匹配 Verilog 非阻塞赋值（NBA）：

| Python 构造 | Verilog 映射 |
|---|---|
| 函数参数（旧值） | `always @(posedge clk)` 中读取的 `reg` |
| 函数返回值（新值） | `<=` 非阻塞赋值 |
| 局部变量 | `wire` 或组合逻辑 `always @*` |
| 赋值点 | 时钟沿（周期边界） |

示例：
```python
# Python: A, B, C = TT1, A, ROL(B, 9)
# 所有右侧读旧值，所有左侧同时更新 — 等同于 Verilog NBA
# Verilog:
#   A <= TT1;
#   B <= A;       // 读旧 A
#   C <= ROL(B, 9); // 读旧 B
```

## 目录结构

```
Veriflow-python/
├── SKILL.md                          # 流程编排入口（Claude Code skill 定义）
├── README.md                         # 本文件
│
├── skill/                            # 流程编排层
│   ├── init.py                       # 项目初始化：EDA 工具发现、目录创建、状态初始化
│   └── state.py                      # 流水线状态管理：阶段顺序、前置条件、重试策略、CLI
│
├── agent/                            # 代理工具层（Stage 3/4 调用）
│   ├── cocotb_runner.py              # Cocotb 仿真运行器
│   ├── iverilog_runner.py            # Iverilog 纯 Verilog 仿真运行器
│   └── vcd2table.py                  # VCD 波形解析 + 周期精确对比 + 黄金模型差异分析
│
├── docs/                             # 规则与文档
│   ├── coding_style.md               # Verilog-2005 编码风格指南（27 节）
│   ├── design_rules.md               # 设计规则：复位策略、接口锁定、finalize 不变量
│   ├── error_recovery.md             # Stage 3 错误恢复流程：数据收集、Bug 分类、根因分析
│   └── bug_patterns.md               # 15 种已知 RTL Bug 模式库
│
└── templates/                        # 代码模板
    ├── design_spec_template.py       # design_spec.py 模板（8 节结构）
    ├── tb_integration_template.v     # Verilog testbench 模板
    └── cocotb_template.py            # Cocotb testbench 模板
```

## 文件说明

### skill/ — 流程编排层

| 文件 | 功能 |
|------|------|
| `init.py` | 项目初始化：自动发现 Python/iverilog/yosys/cocotb，创建 `workspace/` 目录结构，生成 `.veriflow/eda_env.sh`，初始化流水线状态 |
| `state.py` | 流水线状态管理：`PipelineState` 数据类，阶段顺序与前置条件检查，重试预算（每阶段 3 次），阶段计时，CLI 入口点 |

### agent/ — 代理工具层

| 文件 | 功能 |
|------|------|
| `cocotb_runner.py` | 运行 cocotb 测试：自动编译 RTL、驱动输入、逐周期对比内部信号，报告首次分歧点 |
| `iverilog_runner.py` | 运行 iverilog+vvp 仿真：解析 `[PASS]`/`[FAIL]` 输出，分类 Bug 类型（A/B/D），黄金模型自检 |
| `vcd2table.py` | VCD 解析器：生成周期精确信号表，与黄金模型逐周期对比，时序断言检查，LLM 可读差异报告 |

### docs/ — 规则与文档

| 文件 | 功能 |
|------|------|
| `coding_style.md` | Verilog-2005 编码风格完整指南：格式化、命名、两块模式、FSM 规则、流水线纪律（27 节） |
| `design_rules.md` | 核心设计规则：同步高有效复位、接口锁定（Stage 1 后冻结）、finalize-state 不变量 |
| `error_recovery.md` | Stage 3 错误恢复标准流程：5 步数据收集 → Bug 分类 → 5 点根因分析 → 修复 → 重试 |
| `bug_patterns.md` | 15 种已知 Bug 模式：症状、根因、修复方案、预防规则，按阶段标注 |

### templates/ — 代码模板

| 文件 | 功能 |
|------|------|
| `design_spec_template.py` | design_spec.py 的 8 节模板：接口定义、模块层次、算法常量、辅助函数、模块伪代码、顶层集成、测试向量、标准接口 |
| `tb_integration_template.v` | Verilog testbench 模板：NBA 时序、复位序列、多块消息、输出采样 |
| `cocotb_template.py` | Cocotb testbench 模板：复位、协议驱动、分层黄金模型对比、内部信号对比 |

## 流水线阶段详解

### Stage 1: design_spec

输入：`requirement.md` + 可选的 `constraints.md`、`design_intent.md`、`context/*.md`

输出：`workspace/docs/design_spec.py`

AI 代理生成单个 Python 文件，包含完整的设计规格、可运行的参考模型和标准测试向量。人类审核此文件，确认算法正确后再进入 Stage 2。

验证：运行 `python design_spec.py`，所有测试向量必须 PASS。

### Stage 2: codegen

输入：`design_spec.py`

输出：`workspace/rtl/*.v` + `workspace/tb/`

每个 Python 函数翻译为一个 Verilog 模块。并行启动多个代理，每个模块一个。

### Stage 3: verify_fix

输入：RTL + testbench

运行 iverilog 仿真（或 cocotb 逐周期对比）。如果失败，执行 5 步错误恢复流程，最多 3 次重试。

### Stage 4: lint_synth

并行运行 lint（iverilog 语法检查）和 synthesis（yosys 综合报告）。

## 安装为 Claude Code Skill

将本项目目录安装为 Claude Code skill：

```bash
# 方法 1：使用安装脚本
python install.py

# 方法 2：手动创建链接
# Linux/macOS:
ln -s /path/to/Veriflow-python ~/.claude/skills/vf-pyverilog

# Windows (管理员权限):
mklink /D "%USERPROFILE%\.claude\skills\vf-pyverilog" "C:\path\to\Veriflow-python"
```

安装后，在 Claude Code 中使用：

```
/vf-pyverilog /path/to/project
```

## 项目输入文件

在项目目录中准备以下文件：

| 文件 | 必需 | 说明 |
|------|------|------|
| `requirement.md` | 是 | 功能需求描述 |
| `constraints.md` | 否 | 时序、面积、功耗约束 |
| `design_intent.md` | 否 | 架构风格、模块划分偏好 |
| `context/*.md` | 否 | 算法参考、协议规范等补充文档 |

## 设计约束

- **Verilog-2005 only** — 不使用 SystemVerilog
- 同步高有效复位，端口名 `rst`
- 端口命名：`_n` 后缀表示低有效，`_i`/`_o` 表示方向
- 接口锁定：端口名、握手协议、模块层次在 Stage 1 后冻结

## 许可证

内部使用
