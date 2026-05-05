# Bug Patterns Quick Index

Match symptoms below, then Read the full pattern from `bug_patterns.md` header `## Pattern N`.

| # | Name | Class | Key Symptom | Read Header |
|---|------|-------|-------------|-------------|
| 1 | Latch-Then-Load Race | Cross-Module | First data block wrong; submodule reads stale latched value | `## Pattern 1` |
| 2 | Shift Register Window Drain | Datapath | Outputs zero after N rounds; next_elem gated by step counter | `## Pattern 2` |
| 3 | Algorithm Initial State Incomplete | Init | Output off by constant XOR; first-block failure | `## Pattern 3` |
| 4 | Off-By-One Pipeline Delay | Timing | Correct value, wrong cycle (1 cycle late/early) | `## Pattern 4` |
| 5 | Reset Not Clearing Output | Init | Output not zero after reset | `## Pattern 5` |
| 6 | FSM Stuck / Missing Transition | Control | FSM never reaches expected state | `## Pattern 6` |
| 7 | Handshake Violation | Protocol | Valid deasserted before ready; protocol timing broken | `## Pattern 7` |
| 8 | Counter Range Off-By-One | Datapath | Counter overflows or stops one short | `## Pattern 8` |
| 9 | Premature Timing Hypothesis | Debug | Debugging timing when root cause is logic error | `## Pattern 9` |
| 10 | Finalize-State Comb Leak | Logic | Output = N+1 rounds instead of N; `_new` used in DONE state | `## Pattern 10` |
| 11 | Chaining Register Not Reset | Init | 2nd message wrong after 1st passes | `## Pattern 11` |
| 12 | FSM Latch-on-Transition Race | Control | Latched value "one cycle late"; state_reg vs next_state | `## Pattern 12` |
| 13 | Bit-Slice Concat Width Truncation | Logic | ROL/ROR wrong; silent upper-bit truncation | `## Pattern 13` |
| 14 | Multi-Block Valid Not Gated | Protocol | Valid fires after every block, not just last | `## Pattern 14` |
| 15 | Cocotb-vs-Verilog Timing | Sim | False divergence; alignment artifact | `## Pattern 15` |
| 16 | Wire-Output Registered | Codegen | Submodule output 1 cycle late; `# wire` implemented as `output reg` | `## Pattern 16` |
| 17 | Variable Part-Select V2005 | Codegen | Compile error or wrong ROL with variable shift | `## Pattern 17` |
| 18 | Init-Value Consistency | Logic | 2nd+ operation wrong; finalize uses `accum_reg` not `init_val` | `## Pattern 18` |

## Classification Quick Guide

| Value | Class | Action |
|-------|-------|--------|
| RTL output = 0 but golden expects non-zero | **D (Init)** | Check: register init, reset path, enable gating |
| Correct value at wrong cycle | **B (Timing)** | Check: pipeline alignment, cross-module delay |
| Wrong value at any cycle | **A (Logic)** | Check: formula, condition, index, concatenation width |

## Most Likely Patterns by Failure Mode

- **First round/cycle wrong**: Pattern 1, 3, 16
- **Works for block 1, fails for block 2+**: Pattern 11, 18
- **Output off by one round of computation**: Pattern 10
- **Valid fires too early**: Pattern 14
- **ROL/ROR produces wrong result**: Pattern 13, 17
