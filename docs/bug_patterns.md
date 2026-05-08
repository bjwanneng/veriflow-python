# VeriFlow Known Bug Patterns

Catalog of recurring RTL bug patterns discovered in real projects.
Referenced by SKILL.md Error Recovery Step 1.5 for rapid root-cause matching.

Each pattern includes: symptom, root cause, fix, and **prevention rule** for
earlier-stage detection.

---

## Pattern 1: Latch-Then-Load Race (Cross-Module)

**Discovered in**: Multi-module design (top-wrapper latches input, submodule loads same cycle)

### Symptom

First data block produces wrong output. Subsequent blocks may or may not be
correct depending on whether the latched register happens to hold valid data
from a previous operation.

### Root Cause

A top-level module latches an input into a register on `posedge N`. On the
**same** `posedge N`, a submodule reads that register as its load data. Due to
NBA semantics, the submodule sees the OLD (pre-latch) value — typically the
reset default (zero).

```
posedge N:  top.latch_reg  <= input_data     (NBA scheduled)
posedge N:  sub.data_en=1, sub.data_in=top.latch_reg  (sees OLD value!)
```

### Fix

Connect the submodule directly to the **external input** (combinational path),
not the latched register. The latch is unnecessary if the submodule consumes
the data on the same cycle.

```verilog
// WRONG — race condition
u_submodule (.data_port(latched_reg), ...);

// CORRECT — direct combinational path
u_submodule (.data_port(input_signal), ...);
```

### Prevention

**verify_fix stage**: In the cross-module timing analysis, if a signal is marked
"0 (combinational)" from producer to consumer, verify the RTL does NOT route
it through a registered latch.

**codegen stage**: Check for this pattern:
- Signal X is an input port of module M
- Module M contains a register `X_latched` updated on `posedge clk`
- A submodule of M connects to `X_latched` (not `X` directly)
- The submodule's load enable fires on the same cycle as the latch write
→ Flag as **potential race condition**

---

## Pattern 2: Shift Register Window Drain

**Discovered in**: Data expansion module (sliding-window shift register)

### Symptom

Shift register outputs are correct for the first N rounds (where N = register
depth), then all outputs become zero or stale for all subsequent rounds.

### Root Cause

A shift register shifts every cycle (consuming one element from position 0),
but the **replenishment** (new element appended at the end) is gated by a
conditional that suppresses injection during early rounds.

```verilog
// WRONG — window drains when round_cnt < THRESHOLD
wire [31:0] next_elem = (round_cnt < THRESHOLD) ? 32'd0 : expansion_func(...);
```

After THRESHOLD shifts with zero injection, all original data has been shifted
out, leaving the register full of zeros. The expansion formula then has no
valid inputs to work with.

### Fix

**Always compute and inject the next element**, regardless of whether it's
"needed" this round. For a sliding-window algorithm, the window must remain
full at all times.

```verilog
// CORRECT — always replenish
wire [31:0] next_elem = expansion_func(...);
```

### Prevention

**codegen stage**: When generating shift-register-based data expansion,
the coder MUST follow this rule:

> **Sliding Window Replenishment Rule**: If a shift register shifts every
> active cycle, the next-element computation MUST NOT be gated by a step
> counter or conditional. Always compute and inject. The step counter only
> determines whether the injected element is consumed externally, not whether
> it's computed.

**verify_fix stage**: Flag any shift register where:
- `reg[i] <= reg[i+1]` (shift) happens unconditionally during active cycles
- `reg[N-1] <= next_elem` where `next_elem` is gated by a condition
- The gate condition depends on `round_cnt < THRESHOLD` where THRESHOLD ≤ N

---

## Pattern 3: Algorithm Initial State Incomplete

**Discovered in**: Iterative datapath (accumulator registers not initialized)

### Symptom

Output is off by a constant XOR — specifically, the output equals the raw
computation result instead of `initial_value ^ computation_result` (or vice
versa). Multi-block messages may work for block 2+ but fail on block 1.

### Root Cause

The algorithm defines two sets of initial state:
1. Working registers — loaded from algorithm-defined initial values for first operation
2. Accumulator/feedback registers — also initialized to algorithm-defined values for first operation

The coder correctly initialized set 1 but missed set 2. The accumulator registers
remained at their reset value (0), so `output = accum_reg(=0) ^ result = result`
instead of `INIT_VALUE ^ result`.

### Fix

During the load phase for the first block, initialize ALL registers that
participate in the final output computation.

```verilog
if (init_en && is_first_operation) begin
    // Working registers
    work_reg_A_next = INIT_VAL_A;
    work_reg_B_next = INIT_VAL_B;
    // Accumulator registers — ALSO initialize
    accum_reg_A_next = INIT_VAL_A;
    accum_reg_B_next = INIT_VAL_B;
end
```

### Prevention

**codegen stage**: Add an "Initial State Completeness Check" during code generation.
For each output signal, trace backwards through the computation:

1. List all registers that contribute to the output expression
2. For each register, verify it has a defined initial value for the first
   operational cycle (not just "reset to 0")
3. If a register feeds into an XOR/ADD chain where 0 is NOT a safe default,
   flag it as "requires explicit initialization"

**verify_fix stage**: Check for registers where:
- The register is read in an expression that contributes to a module output
- The register's only initialization path is the reset block (value = 0)
- The output expression is XOR-based (where 0 is a meaningful but potentially
  wrong operand)
→ Flag as **potential initialization gap**

---

## Pattern 4: Off-By-One Pipeline Delay

*(From SKILL.md Pattern A)*

### Symptom

Output data is correct but arrives one cycle late (or early). Simulation shows
the expected value at cycle N+1 instead of cycle N.

### Prevention

**verify_fix stage**: For every output assertion in the testbench, specify
both the expected value AND the expected cycle. If the value appears one cycle
off, check for an extra register stage in the output path.

---

## Pattern 5: Reset Not Clearing Output Register

*(From SKILL.md Pattern B)*

### Prevention

**verify_fix stage**: Verify that every output port is driven by a register
(or combinational logic derived from a register) that is included in the
reset block.

---

## Pattern 6: FSM Stuck / Missing Transition

*(From SKILL.md Pattern C)*

### Prevention

**verify_fix stage**: For every FSM state, verify that all transition
conditions are reachable and that every state has a defined next-state for
all input combinations (explicit `default` branch).

---

## Pattern 7: Handshake Violation

*(From SKILL.md Pattern D)*

### Prevention

**verify_fix stage**: Verify that the testbench's handshake scenarios
include: (a) valid asserted before ready, (b) valid held until ready, (c)
valid deasserted after ready. For `hold_until_ack` protocols, verify valid
persistence.

---

## Pattern 8: Counter Range Off-By-One

*(From SKILL.md Pattern E)*

### Prevention

**codegen stage**: Use `integer` for loop counters in testbenches and
simulation-only code. For synthesis, use sufficiently wide register widths
and verify terminal condition uses the correct comparison (`<` vs `<=`).

**verify_fix stage**: For every counter with `reg [N:0]`, verify the terminal
value is reachable without overflow. If the terminal value equals `2^N - 1`,
the counter wraps correctly. If it equals `2^N`, the counter overflows.

---

## Pattern 9: Premature Timing Hypothesis

**Discovered in**: Multiple projects — debuggers assume timing/pipeline issues
when the real cause is a logic error (wrong formula, wrong condition, wrong index).

### Symptom

Debug session spends significant time modifying FSM timing, adding delay
registers, or adjusting pipeline stages, without resolving the failure.

### Root Cause

When computation output is wrong, the default assumption is often "pipeline
alignment" or "register timing." But most RTL bugs in synchronous
single-clock-domain designs are **logic errors** — incorrect formulas,
wrong conditional guards, missing initial values, or incorrect array indices.
Timing issues are rare in fully synchronous designs with a single clock domain.

### Fix

Before investigating timing:
1. Run golden model cycle-by-cycle comparison (algorithm designs) or
   protocol compliance check (interface designs)
2. Classify: is the wrong value **(A) data-wrong** or **(B) timing-wrong**?
3. Data-wrong → trace the signal's computation logic (formula, condition, index)
4. Timing-wrong → then investigate pipeline alignment

**Key discriminator**: If the divergent value is zero or a constant when the
golden model expects a computed value, it is almost certainly a logic error
(conditional gating to zero, missing initialization, wrong formula), not a
timing issue. Zero is never a timing symptom.

### Prevention

**verify_fix stage**: When a simulation fails, the mandatory data collection
step (Error Recovery Step 0) prevents this pattern:

1. Run golden model diff to find first divergence cycle
2. Examine the divergent value:
   - Zero or constant → logic error (Type A or D)
   - Correct value, wrong cycle → timing error (Type B)
3. Never skip to "timing fix" without completing data-driven classification

**Rule**: Never assume timing issues without data. If the golden model diff
shows a zero or constant at the divergence point, it's a LOGIC error, not
a timing error.

---

## Pattern 10: Finalize-State Combinational Leak

**Discovered in**: Iterative computation core (finalize state reading `_new` wires instead of `_reg`)

### Symptom

Output is completely wrong — values are off by one full round of computation.
The output appears to be the result of N+1 rounds instead of N rounds.
Multi-block messages may also fail because chaining values are computed from
the wrong state.

### Root Cause

In an iterative computation FSM, combinational `_new` wires represent the **next**
cycle's register values — i.e., what registers WILL become after the current round's
computation is applied. In the DONE/finalize state, the designer incorrectly used
these combinational wires instead of the actual registered values.

```verilog
// WRONG — reads next-state (combinational) values in finalize state
STATE_FINALIZE: begin
    accum_reg <= accum_reg ^ result_new;  // result_new = extra computation round!
    data_out  <= {accum_reg ^ result_new, ...};
end

// CORRECT — reads current registered values in finalize state
STATE_FINALIZE: begin
    accum_reg <= accum_reg ^ result_reg;  // result_reg holds the completed result
    data_out  <= {accum_reg ^ result_reg, ...};
end
```

The `_new` wires are fed by the combinational logic that computes the next round.
When the FSM transitions to the finalize state (after round N-1), these wires hold
what round N WOULD produce — but round N was never meant to execute. The registered
`_reg` values hold the correct result of N completed rounds.

### Fix

In finalize states, use ONLY `_reg` (registered) values. Never use `_new`
(combinational next-state) wires. The `_new` wires are valid ONLY inside the
processing state's sequential block for updating registers.

### Prevention

**codegen stage**: Add this rule to the coder's checklist:

> **Finalize-State Register Read Rule**: In finalize FSM states, ALL output
> computations and register updates MUST use `_reg` (registered) values only.
> Never use `_new` (combinational next-state) wires — they represent the NEXT
> computation round, not the current state.

**verify_fix stage**: Flag any finalize state where:
- An expression references a `_new` wire (combinational next-state signal)
- The `_new` wire is derived from the same combinational logic as the processing state
→ This applies an extra unintended computation round.

---

## Pattern 11: Iterated Construction Chaining Register Reset

**Discovered in**: Iterative hash/digest core (chaining registers not re-initialized for new messages)

### Symptom

First message processes correctly. Second message produces wrong output. Specifically,
test 2 fails after test 1 passes in the same simulation run.

### Root Cause

In iterated constructions (Merkle-Damgård, sponge, etc.), chaining/accumulator
registers accumulate intermediate results across blocks. When a new message starts,
these registers MUST be re-initialized to the algorithm-defined initial values —
but the code only initialized the working registers, leaving chaining registers
with stale values from the previous message.

```verilog
// WRONG — chaining registers retain stale values from previous message
if (is_first_block) begin
    // Only working registers initialized
    work_reg_A <= INIT_VAL_A;
    work_reg_B <= INIT_VAL_B;
    // chaining_reg_A, chaining_reg_B NOT re-initialized!
end

// CORRECT — re-initialize BOTH working and chaining registers
if (is_first_block) begin
    work_reg_A     <= INIT_VAL_A;
    work_reg_B     <= INIT_VAL_B;
    chaining_reg_A <= INIT_VAL_A;  // ALSO re-init chaining values
    chaining_reg_B <= INIT_VAL_B;
end
```

### Fix

For iterated constructions (Merkle-Damgård, sponge, etc.), when starting a
new message, re-initialize ALL state registers that persist across blocks — both
working registers AND chaining/accumulator registers.

### Prevention

**codegen stage**: For designs with dual register sets (working + chaining),
verify that the first-block initialization path covers BOTH sets.

**verify_fix stage**: Flag designs where:
- Two sets of registers exist (working set + chaining/accumulator set)
- The first-block init path only covers one set
→ The other set retains stale state from previous messages.

---

## Pattern 12: FSM Latch-on-Transition Race

**Discovered in**: Generic FSM designs where pre-NBA state values are used to
make decisions during the same cycle the state is transitioning.

### Symptom

An FSM control signal appears to be "one cycle late" or fails to assert on the
expected cycle. Specifically, a signal that should be latched when the FSM
transitions from STATE_A to STATE_B is never captured, because the latch logic
checks `state_reg == STATE_A` but `state_reg` has already been scheduled to
update via NBA.

### Root Cause

In a single always block, the FSM updates `state_reg <= next_state` at the
bottom. All `case (state_reg)` branches above it run with the PRE-NBA value.
This is normally correct. However, if the designer uses `case (state_reg)` to
detect a transition (e.g., "when in IDLE and transitioning to CALC, latch the
input"), the `STATE_IDLE` branch fires with the OLD state value — which IS
correct for the current cycle. But if the state was already transitioned by a
combinational `next_state` assignment that ran before the case statement, the
branch may not match.

The more subtle variant: the designer writes `if (state_reg == STATE_IDLE)`
inside the sequential block, expecting it to fire on the IDLE→CALC transition
cycle. It does fire — but `state_reg` still holds STATE_IDLE because the NBA
hasn't applied yet. The problem arises when the designer ALSO writes the same
condition in a SECOND sequential block (or expects it NOT to fire in a
subsequent cycle when state_reg has already advanced).

### Fix

Detect state transitions explicitly using both `state_reg` and `next_state`
before the state register update:

```verilog
always @(posedge clk) begin
    if (rst) begin
        state_reg <= STATE_IDLE;
        latched_input <= 'd0;
    end else begin
        // Detect transition BEFORE state_reg updates
        if (state_reg == STATE_IDLE && next_state == STATE_CALC) begin
            latched_input <= data_input;  // captures input on transition cycle
        end

        case (state_reg)
            STATE_IDLE:  counter_reg <= 'd0;
            STATE_CALC:  counter_reg <= counter_reg + 1'b1;
            default:     ;
        endcase

        state_reg <= next_state;
    end
end
```

**Alternative approach** (Mealy-style combinational latch):
```verilog
// Combinational block — no registration delay
wire transition_to_calc = (state_reg == STATE_IDLE) && start_valid;
```

### Prevention

**codegen stage**: When building the cycle timing table, for each FSM state
transition A→B where an input must be latched:
1. Mark the latch as happening "at the A→B boundary"
2. Use explicit transition detection: `(state_reg == STATE_A && next_state == STATE_B)`
3. Do NOT rely on `case (state_reg) STATE_A:` alone for transition-time actions

**verify_fix stage**: For any FSM that latches inputs on state transitions:
- Verify the latch condition uses both `state_reg` and `next_state`
- Flag conditions that only check `state_reg` for one-shot capture actions
→ These will either fire at the wrong time or never fire depending on NBA ordering

---

## Pattern 13: Bit-Slice Concatenation Width Truncation

**Class**: A (Computation)

### Symptom

ROL/ROR or other bit-manipulation operations produce wrong results starting from
a specific round or step. The error manifests as unexpected constant bits (often
upper bits always zero or always one) in the rotated value.

### Root Cause

Verilog concatenation `{a, b}` produces a value whose width is the SUM of the
widths of `a` and `b`. When this concatenation is assigned to a narrower
variable, the upper bits are **silently truncated** with NO warning from any
simulator or synthesis tool.

The most common manifestation is an incorrect ROL (rotate left) implementation:

```verilog
// WRONG: ROL(x, 7) for 32-bit value
// x[24:0] = 25 bits, x[31:7] = 25 bits → concatenation = 50 bits!
// Assigned to 32-bit target → upper 18 bits silently truncated
assign rol_wrong = {x[24:0], x[31:7]};

// CORRECT: ROL(x, 7) for 32-bit value
// x[24:0] = 25 bits, x[31:25] = 7 bits → concatenation = 32 bits ✓
assign rol_correct = {x[24:0], x[31:25]};
```

General ROL(x, N) for WIDTH-bit value:
```verilog
// Correct template: two slices MUST sum to exactly WIDTH bits
assign rol_result = {x[WIDTH-1-N:0], x[WIDTH-1:WIDTH-N]};
//   slice widths:    (WIDTH-N)     +     N        = WIDTH ✓
```

### Verification Rule

After writing ANY `{a, b}` concatenation:
1. Count the bit width of each slice: `$bits(a)` and `$bits(b)`
2. Verify `$bits(a) + $bits(b)` equals the target width
3. If the sum exceeds the target width, bits are silently truncated — wrong result

### Prevention

**codegen stage (vf-coder)**: Internal verification checklist item 15 checks every
concatenation for width correctness.

**verify_fix stage**: When a computation error is detected at a specific round:
1. Check ALL concatenation expressions in the datapath
2. Manually count bit widths of each slice
3. Look for the pattern `{x[WIDTH-1-N:0], x[WIDTH-1:N]}` where the second slice
   should be `x[WIDTH-1:WIDTH-N]` (N bits, not WIDTH-N bits)

**Sized literal trap**: `5'd32` silently wraps to 0 in a 5-bit field. Use unsized
integer literals (just `32`) in width-critical expressions like `32 - n`.

---

## Pattern 14: Multi-Block Valid Signal Not Gated

**Class**: C (Protocol)

### Symptom

In a multi-block message processor (hash core, cipher), the `valid` or `done`
output fires after EVERY block — including intermediate blocks — instead of only
after the final block. This causes downstream modules to read partial/incorrect results.

### Root Cause

The valid/done signal is computed from FSM state and round counter only:

```verilog
// WRONG: fires after every block's last round
assign done_pending = (state_reg == STATE_CALC) && (round_cnt_reg == MAX_ROUND);

// CORRECT: only fires after the LAST block
assign done_pending = (state_reg == STATE_CALC) && (round_cnt_reg == MAX_ROUND)
                      && is_last_reg;
```

The `is_last` flag (indicating the current block is the final one) was available
but not included in the gating condition.

### When This Bug Appears

- Multi-block hash algorithms (SHA-256, SHA-512, MD5, etc.)
- Block cipher modes that process multiple blocks (CBC, CTR chains)
- Any design where valid output should only assert after ALL input blocks are processed

### Prevention

**codegen stage**: For any design that processes multiple input blocks:
1. Identify the "final result valid" signal
2. Verify it includes `is_last` (or equivalent) in its gating condition
3. Add a comment: `// gated by is_last: only valid after final block`

**verify_fix stage**: Run multi-block test vectors (at least 2 blocks) and verify:
1. `valid` does NOT assert after intermediate blocks
2. `valid` DOES assert after the final block
3. The testbench MUST include multi-block test vectors to catch this bug

---

## Pattern 15: Cocotb-vs-Verilog Timing Divergence

**Class**: B (Timing) — but manifests as false Type A

### Symptom

Cocotb per-cycle comparison reports a FIRST DIVERGENCE, but the RTL is actually
correct. The divergence is a timing alignment artifact, not a real bug.

Alternatively: a Verilog `$display` at posedge shows value X, but cocotb
`RisingEdge` + `.value` shows value Y at the "same" cycle. Developer assumes
one of them is wrong.

### Root Cause

**Cocotb + iverilog VPI reads values from the PREVIOUS posedge's NBA.** After
`await RisingEdge(dut.clk)` at posedge N, cocotb reads the register values
produced by posedge N-1's NBA (i.e., POST-NBA of N-1 = PRE-NBA of N):

| Tool | Read point | Value seen |
|------|-----------|------------|
| Verilog `$display` at posedge N | Active region (before NBA) | Pre-NBA of N (= Post-NBA of N-1) |
| Cocotb `await RisingEdge` at posedge N | After Active+NBA regions | **Post-NBA of N-1** (previous posedge) |
| Verilog `$display` at negedge N | After NBA applied | Post-NBA of N |

Example at posedge T where `reg_x <= new_value`:
- Verilog `$display` at posedge T → sees OLD reg_x (pre-NBA of T)
- Cocotb `RisingEdge` at posedge T → sees reg_x from posedge T-1's NBA
- At posedge T+1's RisingEdge → sees NEW reg_x (posedge T's NBA applied)

**Three common timing misalignments:**

1. **Golden model offset**: Golden trace cycle K is compared against DUT state
   that is actually cycle K+1 or K-1. Fix: adjust DRIVE_PHASE_CYCLES or block_start.

2. **Registered output delay**: Signals like `ready_reg` and `hash_valid_reg`
   are registered outputs. They reflect the combinational `_next` signal from
   the PREVIOUS posedge. For example:
   - IDLE state always sets `ready_next=1`, even when transitioning to CALC
   - So at the IDLE→CALC posedge: `ready_reg <= 1` (from IDLE's ready_next=1)
   - At the next CALC posedge: `ready_reg <= 0` (from CALC's ready_next=0)
   - Golden model must track this 1-cycle delay, not set ready=0 at the transition

3. **Held registers in DONE state**: Registers like `round_cnt` that are not
   explicitly updated in the DONE state hold their last value (default: hold).
   Golden model must not reset these to 0 unless the RTL actually does so.

### Prevention

**design_spec_template.py**: Trace convention documents the 1-cycle read delay
and the registered output behavior. Design spec must track `_next` vs `_reg`
for registered outputs (ready, valid, etc.).

**cocotb_template.py**:
- Clock is restarted in each test (cocotb v2.0 kills background tasks between tests)
- DRIVE_PHASE_CYCLES=0 when block_trace already skips cycle 0 (reset)
- test_layered uses GOLDEN_TO_PORT mapping for register-to-port name translation

**verify_fix stage**: If FIRST DIVERGENCE is reported:
1. Check if it's at cycle 0 or 1 → likely alignment issue, not RTL bug
2. Check if the divergent value is off by exactly 1 cycle → timing convention mismatch
3. Check if it's a registered output (ready, valid) at a state transition → golden model must account for 1-cycle register delay
4. Check if a register is 0 in golden model but non-zero in DUT → golden model may incorrectly reset a held register

---

## Pattern 16: Wire-Output Registered in Error

**Discovered in**: SM3 message expansion module (w_gen_shift)

### Symptom

A submodule's output signal arrives 1 cycle late. The first round of
compression reads stale/reset values (typically 0) instead of the current
computed values. The design may pass single-block tests but fail multi-block
tests, or produce wrong results from round 0.

### Root Cause

In design_spec.py, a function return value is annotated `# wire` (same-cycle
combinational output), but the codegen implements it as `output reg` +
`always @(posedge clk)`. This adds a 1-cycle register delay that was not
present in the Python model.

```python
# Python: cur_elem and cur_flag are same-cycle outputs (wire)
def data_expand(shift_reg, step_cnt):
    cur_elem = shift_reg[0]                           # wire
    cur_flag = (shift_reg[0] ^ shift_reg[4]) & MASK32  # wire
    return cur_elem, cur_flag, next_shift_reg
```

```verilog
// WRONG: registered output adds 1-cycle delay
output reg [31:0] cur_elem_o,
always @(posedge clk) begin
    if (calc_en_i)
        cur_elem_o <= shift_reg[0];  // visible NEXT cycle, not this cycle!
end

// CORRECT: combinational output matches Python semantics
output wire [31:0] cur_elem_o,
assign cur_elem_o = shift_reg[0];  // same-cycle combinational output
```

### Fix

Change `output reg` to `output wire` + `assign` for any signal annotated `# wire`
in design_spec.py.

### Prevention

**codegen stage (Rule R1)**: For every function return value:
1. Check its timing annotation in design_spec.py (`# wire` or `# reg_next`)
2. `# wire` → `output wire` + `assign` (MUST NOT use `output reg`)
3. `# reg_next` → `output reg` + `always @(posedge clk)` NBA

**verify_fix stage**: If the first cycle of operation produces wrong results and
the submodule inputs are all zero/stale, check whether the producing module's
outputs are registered when they should be combinational.

---

## Pattern 17: Variable Part-Select in Verilog-2005

**Discovered in**: SM3 compression module (T_j rotation)

### Symptom

Compilation error (`variable index not supported`) or silently wrong rotation
results. The error occurs specifically when `ROL(x, n)` is translated with a
variable `n` using Verilog-2005 bit-slice concatenation.

### Root Cause

Verilog-2005 does not support variable part-select: `{x[31-n:0], x[31:32-n]}`
where `n` is a variable (e.g., `round_cnt`). This is a SystemVerilog extension.
iverilog may either reject it or silently produce wrong results.

```verilog
// WRONG: variable part-select — illegal in Verilog-2005
wire [31:0] rot_result = {data_val[31-shift_amt:0], data_val[31:32-shift_amt]};

// CORRECT: barrel shifter — Verilog-2005 compatible
reg [31:0] rot_s0, rot_s1, rot_s2, rot_s3, rot_out;
always @(*) begin
    rot_s0  = shift_amt[0] ? {data_val[30:0], data_val[31]}           : data_val;
    rot_s1  = shift_amt[1] ? {rot_s0[29:0], rot_s0[31:30]}            : rot_s0;
    rot_s2  = shift_amt[2] ? {rot_s1[27:0], rot_s1[31:28]}            : rot_s1;
    rot_s3  = shift_amt[3] ? {rot_s2[23:0], rot_s2[31:24]}            : rot_s2;
    rot_out = shift_amt[4] ? {rot_s3[15:0], rot_s3[31:16]}            : rot_s3;
end
```

### Fix

Replace variable part-select with a log2(WIDTH)-stage barrel shifter. Each
stage conditionally rotates by 2^k bits based on the k-th bit of the shift amount.

### Prevention

**codegen stage (Rule R5)**: When translating `ROL(x, n)` where `n` is a
VARIABLE (not a compile-time constant):
1. MUST use barrel shifter (log2(WIDTH) stages)
2. MUST NOT use variable part-select `{x[W-1-N:0], x[W-1:W-N]}`
3. All intermediate signals declared as `reg` (assigned in `always @*`)

**verify_fix stage**: Search for patterns like `{x[<expr>-<var>:0]}` or
`{x[<expr>:<expr>-<var>]}` where the slice boundaries depend on a variable.
Flag as Verilog-2005 violation.

---

## Pattern 18: Init-Value Consistency Violation

**Discovered in**: SM3 core top module (V accumulator XOR)

### Symptom

First operation produces correct output. Subsequent operations produce wrong
output. The result after operation 2+ is off by a constant XOR/ADD —
specifically, it equals `accum_reg ^ result` instead of `init_val ^ result`.
When `is_first=1` for a new sequence, `accum_reg` holds stale data from a
previous sequence.

### Root Cause

A design has an init-value selector: `init_val = is_first ? CONST : accum_reg`.
Working registers are loaded from `init_val`. After the operation completes,
the finalize/DONE state computes `result = <operand> ^ work_output`. The bug
is using `accum_reg` as the operand instead of `init_val`.

When `is_first=1`: `init_val = CONST`, but `accum_reg` may hold stale data
from a previous sequence. Using `accum_reg` produces `stale ^ result` instead
of `CONST ^ result`.

**Applicable designs**:
- Iterated hash (Merkle-Damgard, sponge): `init_val = is_first_op ? IV : accum_reg`
- Cipher chaining (CBC, CFB): `init_val = is_first_op ? IV : prev_cipher_reg`
- CRC with selectable init: `init_val = is_first_op ? CRC_INIT : running_crc`
- Any design with `init = mux(CONST, stored_register)`

```python
# Python model: init is correctly set for this operation
init = CONST if is_first else accum  # accum = correct init value
# ... operation runs with working_regs = init ...
accum = (init ^ result) & MASK32     # init = correct init value
```

```verilog
// Pattern: init-value selector at operation start
wire [W-1:0] init_val = is_first ? CONST : accum_reg;

// WRONG: finalize uses accum_reg (stale from previous operation)
wire [W-1:0] result = accum_reg ^ work_out;

// CORRECT: finalize uses init_val (matches what working regs were loaded with)
wire [W-1:0] result = init_val ^ work_out;
```

### Fix

In the finalize/DONE state, use `init_val` (the init-value selector signal)
instead of `accum_reg` (the global storage register). The `init_val` signal
is typically already computed in the module for loading working registers —
reuse it for the finalize computation.

### Prevention

**codegen stage (Rule R3)**: When a design has `init_val = is_first ? CONST :
accum_reg` and a finalize computation that combines the init value with the
operation result:
1. Identify which value was used to initialize the working registers
2. Use that SAME value for the finalize computation (not the stored register)
3. Verify: when `is_first=1`, `init_val=CONST` but `accum_reg` may differ

**verify_fix stage**: For multi-operation test failures:
1. Compare the result after operation 2 with the golden model
2. If the difference is a constant XOR/ADD with the previous operation's value,
   the finalize state is using `accum_reg` instead of `init_val`
3. Check: finalize uses `accum_reg` vs `init_val`

---

## Pattern 19: FSM Control Signal Timing Mismatch

**Discovered in**: SM3 FSM module (registered control signals vs combinational golden model)

**Class**: B (Timing) — but causes downstream Type A symptoms

### Symptom

Round counter appears off-by-one. Control signals (calc_en, load_en, update_v_en,
hash_valid) assert one cycle too early or too late compared to the golden model.
The design produces correct logic in isolation but wrong results due to cycle
misalignment. Common manifestations:

- `round_cnt` reaches MAX_ROUND one cycle early/late
- `calc_en` leaks into DONE state, applying an extra computation round
- `hash_valid` asserts one cycle off, breaking handshake timing
- First-block result correct but multi-block results wrong

### Root Cause

The golden model (design_spec.py `compute()`) treats FSM control signals as
**combinational** (immediate on state entry), but the Verilog implementation
**registers** them (adding a 1-cycle delay). This shifts all downstream timing
by one cycle.

```python
# Python golden model: calc_en is combinational (immediate)
def compute(state, ...):
    if state == CALC:
        calc_en = 1   # active immediately in CALC state
        # ... computation ...
```

```verilog
// WRONG: registered control — 1 cycle delay
always @(posedge clk) begin
    if (state_reg == S_CALC)
        calc_en_reg <= 1;  // not visible until NEXT posedge!
end
assign calc_en = calc_en_reg;

// CORRECT: combinational control — matches golden model
assign calc_en = (state_reg == S_CALC) && !load_en_reg;
```

The 1-cycle delay causes:
- `round_cnt` increments one cycle late → off-by-one in round count
- `calc_en` leaks into DONE state → extra computation round (Pattern 10)
- `hash_valid` asserts one cycle late → wrong handshake timing

### Two Valid Patterns

**Pattern A — Combinational control** (default for `# wire` annotated signals):
```verilog
assign calc_en     = (state_reg == S_CALC) && !load_en_reg;
assign update_v_en = (state_reg == S_DONE);
assign hash_valid  = (state_reg == S_DONE) && is_last;
```

**Pattern B — Registered control** (ONLY for `# reg_next` annotated signals):
```verilog
always @(posedge clk) begin
    calc_en_reg <= (state_reg == S_CALC) && !load_en_reg;
end
assign calc_en = calc_en_reg;  // 1-cycle delay from state transition
```

**MIXING IS FORBIDDEN**: If the golden model uses Pattern A timing, the Verilog
MUST NOT use Pattern B for any related control signal. This is the #1 cause of
round-counter offset bugs.

### Fix

Match the golden model's control signal timing pattern:
1. Read `design_spec.py` `compute()` function
2. Determine if control signals are combinational or registered
3. Use the SAME pattern in Verilog for ALL related control signals
4. Document the timing choice: `// combinational control — matches golden model`

### Prevention

**codegen stage (Rule R7)**: When translating FSM control signals:
1. Read the golden model's `compute()` function carefully
2. Determine if control signals are combinational (set in same cycle as state)
   or registered (delayed by 1 cycle)
3. Use the SAME pattern in Verilog — combinational `assign` or registered `<=`
4. **MIXING IS FORBIDDEN** — all related signals must use the same pattern

**verify_fix stage**: When round counter or control timing is wrong:
1. Check if the golden model uses combinational or registered control
2. Compare Verilog implementation — if mismatched, this is Pattern 19
3. Fix: change ALL related control signals to match the golden model's timing

---

## Pattern 20: Port Name Fabrication

**Discovered in**: AES-128 design (aes_128_core.v instantiating aes_key_expansion)

### Symptom

Compilation error or silent functional failure. Module instantiation uses port
names that do not exist in the target module's declaration. Often the fabricated
names are "plausible" (e.g., `.key_word_0()` instead of `.key_words()`).

### Root Cause

Agent generates instantiation code without first reading the target module's
actual port declaration. Common in multi-agent parallel generation where top-level
and submodule agents don't share interface information.

### Example

```verilog
// Module declaration (actual):
module aes_key_expansion (
    input  wire [127:0] key_words,
    input  wire [3:0]   round_num,
    output wire [127:0] round_key
);

// Instance (WRONG — fabricated port names):
aes_key_expansion u_key (
    .key_word_0(key_reg[127:96]),  // does not exist!
    .new_word_0(key_word_0),       // does not exist!
);

// Instance (CORRECT — matching actual ports):
aes_key_expansion u_key (
    .key_words   (key_reg),
    .round_num   (round_counter_reg),
    .round_key   (round_key_expanded)
);
```

### Fix

1. Read the target module's Verilog file to extract its exact port list.
2. Use only named port connections (`.port(signal)`).
3. Verify every port name matches case-sensitively.

### Prevention

**codegen stage**: ALWAYS read the target module file before writing an
instantiation. Use `rtl_checker.py --rtl-dir <dir>` to auto-detect mismatches.

**coding_style_core.md C15**: Instance port matching checklist is MANDATORY.

---

## Pattern 21: Testbench Blocking Assignment Race

**Discovered in**: AES-128 testbench (tb_aes_128_core.v)

### Symptom

First test vector always times out or fails. Subsequent vectors may pass
intermittently. The DUT appears to never see `start`, `rst_n`, or `data_in`
changes.

### Root Cause

Blocking assignment (`=`) in testbench clock-synchronized blocks creates a
race condition with the DUT's `always @(posedge clk)`. In Verilog's event
queue, the execution order between blocking assignments in the testbench and
the DUT's always block is not guaranteed.

```verilog
// WRONG — race condition:
always @(posedge clk) begin
    rst_n = 1'b0;   // blocking: DUT may sample stale value
    start = 1'b1;   // blocking: DUT may miss this entirely
end

// CORRECT — non-blocking:
always @(posedge clk) begin
    rst_n  <= 1'b0;  // non-blocking: DUT sees new value next edge
    start  <= 1'b1;
    data_in <= test_data;
end
```

### Fix

Replace ALL blocking assignments (`=`) with non-blocking (`<=`) for any signal
consumed by the DUT's `always @(posedge clk)`. Only `clk` generation may use
blocking assignment: `always #5 clk = ~clk;`

### Prevention

**coding_style_core.md C16**: All testbench sync-driven signals must use `<=`.

**Automated**: `rtl_checker.py --tb-dir <dir>` detects blocking assignments in
`@(posedge clk)` blocks.

---

## Pattern 22: Reset Strategy Syntax Mismatch

**Discovered in**: AES-128 design (requirement: async low-active, code: sync)

### Symptom

Reset behavior does not match specification. During clock failure, device
cannot be reset. Reset release timing may cause metastability.

### Root Cause

Agent confuses **polarity** (active-high/active-low) with **timing**
(synchronous/asynchronous). Writing `if (!rst_n)` inside `always @(posedge clk)`
is a **synchronous** reset, regardless of the signal name or polarity.

```verilog
// Requirement: "asynchronous reset, active-low"
// WRONG — this is SYNCHRONOUS:
always @(posedge clk) begin
    if (!rst_n) begin ... end  // only checks at clock edge
end

// CORRECT — true asynchronous:
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin ... end  // checks immediately on rst_n falling edge
end
```

### Fix

1. Identify the reset strategy from design_spec.py Section 1 declaration.
2. For synchronous: `always @(posedge clk)` + `if (rst)` or `if (!rst_n)`.
3. For asynchronous: `always @(posedge clk or negedge rst_n)` + `if (!rst_n)`
   or `always @(posedge clk or posedge rst)` + `if (rst)`.

### Prevention

**design_rules.md**: design_spec.py Section 1 MUST declare reset strategy,
polarity, and signal name.

**Automated**: `rtl_checker.py --reset-strategy <synchronous|asynchronous>`
verifies the sensitive list matches the declared strategy.
