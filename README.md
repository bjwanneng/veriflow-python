# VeriFlow-python

AI-driven RTL design pipeline: Natural Language → Python Design Spec → Verilog.

VeriFlow 将 RTL 设计流程分为 5 个阶段，每个阶段由 AI 代理执行：

```
Stage 0: 初始化 & 需求澄清
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
├── install.py                        # Skill 安装脚本
│
├── dsl/                              # VeriFlow DSL 核心引擎
│   ├── _types.py                     # 类型系统：Signal, Const, Cat, Mux, BinOp, ROL
│   ├── _module.py                    # Module/Domain: m.d.comb, m.d.sync 时序域
│   ├── _emitter.py                   # VerilogEmitter: DSL → Verilog-2005 代码生成
│   ├── _simulator.py                 # CycleSimulator: 周期精确仿真
│   ├── _trace.py                     # 波形追踪 & Bug 分类（Type A/B/D）
│   ├── ext/                          # 扩展 API（FV formal 兼容接口预留）
│   └── tests/                        # 92 个单元测试
│
├── skill/                            # 流程编排层
│   ├── init.py                       # 项目初始化：EDA 工具发现、目录创建、状态初始化
│   ├── state.py                      # 流水线状态管理：阶段顺序、前置条件、重试策略
│   └── stages/                       # 各阶段执行指令（按需读取）
│       ├── stage0_init.md            # 初始化 & 需求澄清
│       ├── stage1_design_spec.md     # 设计规格生成
│       ├── stage2_codegen.md         # Python→Verilog 代码生成
│       ├── stage3_verify_fix.md      # 仿真验证 & Bug 修复
│       └── stage4_lint_synth.md      # Lint + 综合
│
├── agent/                            # 代理工具层（Stage 3/4 调用）
│   ├── cocotb_runner.py              # Cocotb 仿真运行器
│   ├── iverilog_runner.py            # Iverilog 纯 Verilog 仿真运行器
│   ├── golden_loader.py              # 黄金模型加载器
│   └── vcd2table.py                  # VCD 波形解析 + 周期精确对比
│
├── docs/                             # 规则与文档
│   ├── design_rules.md               # 设计规则：复位策略、接口锁定
│   ├── coding_style_core.md          # 编码风格核心规则（Stage 2 使用，~200 行）
│   ├── coding_style.md               # 编码风格完整指南（27 节，参考用）
│   ├── coding_style_reference.md     # 编码风格参考副本
│   ├── error_recovery.md             # Stage 3 错误恢复流程
│   ├── bug_patterns.md               # 已知 RTL Bug 模式库
│   ├── bug_patterns_index.md         # Bug 模式快速索引
│   └── template_guide.md             # 模板使用说明
│
└── templates/                        # 代码模板
    ├── design_spec_template.py       # design_spec.py 模板（8 节结构）
    ├── tb_integration_template.v     # Verilog testbench 模板
    └── cocotb_template.py            # Cocotb testbench 模板
```

## 流水线阶段详解

### Stage 0: 初始化 & 需求澄清

运行 `init.py` 创建项目骨架，自动发现 EDA 工具（iverilog/yosys/cocotb），读取输入文件并按 A-G 七类进行需求澄清。

### Stage 1: design_spec

输入：`requirement.md` + 可选的 `constraints.md`、`design_intent.md`、`context/*.md`

输出：`workspace/docs/design_spec.py`

AI 代理生成单个 Python 文件，包含 8 节结构：接口定义、模块层次、算法常量、辅助函数、模块伪代码、顶层集成、测试向量、标准接口。人类审核此文件，确认算法正确后再进入 Stage 2。

可选：使用 VeriFlow DSL 编写 `build_*()` 函数，通过 `VerilogEmitter` 确定性生成 Verilog，跳过 AI 翻译。

### Stage 2: codegen

输入：`design_spec.py`

输出：`workspace/rtl/*.v` + `workspace/tb/`

每个 Python 函数翻译为一个 Verilog 模块。17 条翻译规则 + 5 条关键规则（R1-R5）确保正确性。支持 DSL 路径（如果 `build_*()` 存在）和 AI 翻译路径。生成后进行 iverilog 语法验证。

### Stage 3: verify_fix

输入：RTL + testbench

运行设计规格自检 → DSL 周期追踪 → cocotb 逐周期对比 → iverilog 仿真。如果失败，执行 5 点根因分析 + Bug 模式匹配，最多 3 次重试。

### Stage 4: lint_synth

并行运行 lint（iverilog 语法检查）和 synthesis（yosys 综合报告）。

## 安装为 Claude Code Skill

```bash
# 方法 1：使用安装脚本
python install.py

# 方法 2：手动创建链接
# Linux/macOS:
ln -s /path/to/Veriflow-python ~/.claude/skills/vf-pyverilog

# Windows (管理员权限):
mklink /D "%USERPROFILE%\.claude\skills\vf-pyverilog" "C:\path\to\Veriflow-python"
```

安装后在 Claude Code 中使用：

```
/vf-pyverilog /path/to/project
```

## 项目输入文件

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
- Python `==`/`!=` 运算符返回 DSL `BinOp`（不返回 `bool`），与 `NotImplemented` 回退兼容

## 运行测试

```bash
python -m pytest dsl/tests/ -v
```

## 最近更新

- **install.py**: 修复 `remove_link()` 中符号链接跟随导致的误删目标目录风险（先判断 `is_symlink()` 再 `unlink`）
- **dsl/_emitter.py**: 清理死代码（移除不可能触发的 `has_reg` 分支）；将内联 `import re` 移至模块顶部；修复纯 sync 信号仍声明未驱动 `_next` wire 的问题
- **dsl/_simulator.py**: 收敛后增加 comb 目标未成功求值的显式报错；sync 域求值失败由静默忽略改为抛出 `RuntimeError`
- **dsl/_types.py**: 限制 `__lshift__` 结果位宽上限，避免 shift_amount 位宽较大时位宽爆炸
- **agent/cocotb_runner.py**: 移除过时的注释
- **skill/validate_interface.py**: 将 `MASK32` 从必需变量改为可选变量，避免对非密码学设计的过度约束
- **agent/golden_loader.py**: 收窄 `TypeError` 捕获范围，仅对签名不匹配 fallback，其余异常继续抛出
- **agent/vcd2table.py**: 扩展信号名正则，支持大写字母或下划线开头的标识符
- **skill/state.py**: `validate_design_spec()` 委托给 `validate_interface` 模块，消除重复验证逻辑

## 许可证

内部使用
