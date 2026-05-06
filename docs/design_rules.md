# Design Rules (Apply to ALL stages)

- All modules use **synchronous active-high reset** named `rst`. Reset is checked inside `always @(posedge clk)` only — no async sensitivity list. See `docs/coding_style.md` Section 6 for full rules.
- Port naming: `_n` suffix for active-low, `_i`/`_o` for direction
- Parameterized design: use `parameter` for widths and depths
- Clock domains must be explicitly declared
- **Verilog-2005 only** — NO SystemVerilog (`logic`, `always_ff`, `assert property`, `|->`, `##`)

## Interface Lock

The following fields in design_spec.py are locked after Stage 1 completes. Stages 2-4 must NOT modify them:
- Port names, widths, and directions (Section 1: Interface Definition)
- Reset polarity: synchronous active-high → port named `rst`
- Handshake protocol (hold_until_ack / single_cycle / pulse)
- Module hierarchy (Section 2: Module Hierarchy)

If a later stage discovers a problem with the interface definition, it must roll back to Stage 1 to redefine.

## Port Semantic Fields (Interface Lock)

- Ports with `protocol: "reset"` MUST declare `reset_polarity`: `"active_high"` only
- Ports with `protocol: "valid"` MUST declare `handshake`: `"hold_until_ack"` | `"single_cycle"` | `"pulse"`
- If `handshake: "hold_until_ack"`, MUST also declare `ack_port` with the name of the corresponding ack input port
- All ports MUST declare `signal_lifetime`: `"pulse"` or `"hold_until_used"`:
  - `"pulse"` — signal is asserted for 1 cycle and consumed immediately by the receiver
  - `"hold_until_used"` — signal is sampled at most once, arrives as a short pulse but is consumed many cycles later. The receiver MUST latch this signal.
- These fields are locked after Stage 1 and MUST NOT be changed by subsequent stages

## Finalize-State Invariant `[CRITICAL]`

In iterative computation FSMs (IDLE → CALC → DONE), the DONE/finalize state
MUST compute outputs from **registered values only** (`_reg`). Never use
combinational next-state wires (`_new`) in finalize states.

**Applies to**: All FSM designs with a DONE/finalize state that produces outputs
or updates state based on the completed computation.

**Rationale**: Combinational `_new` wires represent the result of applying one more
round of computation. When the FSM reaches DONE after N rounds, reading `_new`
effectively applies round N+1, corrupting the output.

```verilog
// WRONG — DONE state uses combinational next-state wires
STATE_DONE: begin
    accum <= accum ^ data_new;  // data_new = extra computation round!
end

// CORRECT — DONE state uses registered values only
STATE_DONE: begin
    accum <= accum ^ data_reg;  // data_reg = result of completed N rounds
end
```

## Merkle-Damgård Init Completeness `[CRITICAL]`

For iterated hash constructions with dual register sets (working registers +
chaining registers), the initialization path for new messages MUST re-initialize
BOTH sets to IV. Chaining registers that retain stale values from previous
messages will corrupt subsequent message hashes.

## Init-Value Consistency `[CRITICAL]`

When a design has an init-value selector (e.g., `init_val = is_first ? CONST :
accum_reg`) that feeds into working registers at the start of an operation, the
finalize/DONE state MUST use that same `init_val` for any accumulation or output
computation — NOT the raw `accum_reg`. The `accum_reg` may contain stale values
from a previous operation, while `init_val` correctly reflects what the working
registers were initialized with for the current operation.

**Applies to**: iterated hash (Merkle-Damgard, sponge), cipher chaining (CBC,
CFB), CRC with selectable init, and any design with `init = mux(CONST, stored)`.

```verilog
// Pattern: init-value selector at operation start
wire [W-1:0] init_val = is_first ? CONST : accum_reg;
// ... operation runs using init_val as starting point ...

// CORRECT: finalize uses init_val — consistent with what was loaded
wire [W-1:0] result = init_val ^ work_out;

// WRONG: finalize uses accum_reg — stale when is_first=1
wire [W-1:0] result = accum_reg ^ work_out;
```

**Rule R3**: The finalize computation must use the same init value that was
selected at the start of the operation (`init_val = is_first ? CONST : accum_reg`),
not the raw storage register. This ensures correct behavior for both first-operation
(is_first=1 → CONST) and continuation (is_first=0 → accum_reg) cases.

## Data-Control Alignment `[CRITICAL]`

Data output signals and their associated valid/ready control signals MUST be
timing-aligned — they must be either BOTH combinational or BOTH registered
relative to the same FSM state.

**The problem**: When `valid` is combinational (`assign valid = (state == DONE)`)
but `data_out` is registered (`data_out <= result`), the valid signal fires
one cycle BEFORE the data is ready. The consumer samples stale/wrong data.

**Two valid patterns**:

```verilog
// Pattern A: BOTH combinational (valid and data active in same cycle)
assign hash_valid = (state_reg == S_DONE) && is_last_reg;
assign hash_out   = update_v_en ? {V_reg ^ A_reg, ...} : {V_reg, ...};

// Pattern B: BOTH registered (valid and data both delayed 1 cycle)
always @(posedge clk) begin
    hash_valid_reg <= (state_reg == S_DONE) && is_last_reg;
    hash_out_reg   <= {V_reg ^ A_reg, ...};
end
assign hash_valid = hash_valid_reg;
assign hash_out   = hash_out_reg;
```

**FORBIDDEN**: Mixing — combinational valid + registered data, or vice versa.

**How to verify**: Check that every `(valid/ready)_out` signal and its paired
`data_out` signal have the same delay relative to the FSM state that produces them.
If `valid` is `assign` from `state_reg`, then `data_out` MUST also be readable
from `state_reg` (not from a `_next` wire that updates next cycle).
