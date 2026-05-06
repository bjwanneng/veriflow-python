# Verilog-2005 Core Coding Rules (Stage 2 essential)

Extracted from coding_style.md. For formatting, naming, FSM, pipeline,
and other reference rules, see coding_style_reference.md.

---

## C1. Two-Block Pattern [MUST]

Separate combinational (next-state) and sequential (register update) into distinct blocks.

```verilog
// Block 1: combinational — compute all _next signals
always @* begin
    state_next = state_reg;     // default: hold
    data_next  = data_reg;      // default: hold
    // ... conditional logic ...
end

// Block 2: sequential — register sampling
always @(posedge clk) begin
    if (rst) begin
        state_reg <= STATE_IDLE;
        data_reg  <= {DATA_WIDTH{1'b0}};
    end else begin
        state_reg <= state_next;
        data_reg  <= data_next;
    end
end
```

- Combinational blocks: blocking `=` only
- Sequential blocks: non-blocking `<=` only
- Use `always @*` (no parentheses) for combinational
- **MUST NOT** mix `=` and `<=` for same signal in same block

---

## C2. Output Port Driving [MUST]

All `output` ports declared as `output wire`, driven via `assign` from internal `_reg`.

```verilog
output wire s_axi_awready,
// ...
reg  s_axi_awready_reg = 1'b0, s_axi_awready_next;
assign s_axi_awready = s_axi_awready_reg;
```

**MUST NOT** use `output reg` or assign outputs directly in `always` blocks.

---

## C3. Signal Declaration [MUST]

| Driven by           | Declare as |
|---------------------|------------|
| `always` block      | `reg`      |
| `assign` / wire out | `wire`     |

MUST assign initial values to all `reg` at declaration:

```verilog
reg [1:0] state_reg = 2'd0, state_next;
```

---

## C4. Latch Prevention — Default Values [MUST]

Assign defaults at the **top** of every `always @*` block:

```verilog
always @* begin
    state_next   = state_reg;     // default: hold
    data_next    = data_reg;      // default: hold
    mem_wr_en    = 1'b0;          // default: inactive
    // ... conditional overrides ...
end
```

---

## C5. Reset Strategy [MUST]

Synchronous active-high reset named `rst`. Use `if/else` structure:

```verilog
always @(posedge clk) begin
    if (rst) begin
        state_reg <= STATE_IDLE;
        data_reg  <= {DATA_WIDTH{1'b0}};
    end else begin
        state_reg <= state_next;
        data_reg  <= data_next;
    end
end
```

---

## C6. _reg / _next Naming [MUST]

| Suffix  | Meaning                                    |
|---------|--------------------------------------------|
| `_reg`  | Register current state (clocked)           |
| `_next` | Combinational next-state (always @* output) |

Declare pairs on same line: `reg [7:0] cnt_reg = 8'd0, cnt_next;`

---

## C7. Module Declaration [MUST]

Verilog-2001 ANSI style. Ports: clocks first, reset, then others.

```verilog
module my_mod
(
    input  wire                   clk,
    input  wire                   rst,
    input  wire [DATA_WIDTH-1:0]  data_in,
    output wire [DATA_WIDTH-1:0]  data_out
);
```

- Explicitly declare `wire` on all ports
- Vertically align direction, type, width, name

---

## C8. Number Literals [MUST]

Always explicit widths: `4'd4`, `8'h2a`, `1'b0`.
Parameterized zero: `{WIDTH{1'b0}}` (not `'0`).
Wide (>64-bit) constants: decompose into 32-bit segments via `{}`.

---

## C9. Wire vs reg_next Timing (Python annotation → Verilog)

| Python annotation | Verilog implementation           |
|-------------------|----------------------------------|
| `# wire`          | `output wire` + `assign` (same cycle) |
| `# reg_next`      | `output wire` + `assign _reg` (next cycle via NBA) |

**Rule R1**: `# wire` outputs MUST NOT be `output reg` + `always @(posedge clk)`.

---

## C10. Variable Rotation → Barrel Shifter [MUST]

When `ROL(x, n)` has variable `n`, MUST use log2(W)-stage barrel shifter.
Variable part-select `{x[31-n:0], ...}` is ILLEGAL in Verilog-2005.

```verilog
// 5-stage barrel shifter for 32-bit
always @(*) begin
    rot_s0  = n[0] ? {x[30:0], x[31]}           : x;
    rot_s1  = n[1] ? {rot_s0[29:0], rot_s0[31:30]} : rot_s0;
    rot_s2  = n[2] ? {rot_s1[27:0], rot_s1[31:28]} : rot_s1;
    rot_s3  = n[3] ? {rot_s2[23:0], rot_s2[31:24]} : rot_s2;
    rot_out = n[4] ? {rot_s3[15:0], rot_s3[31:16]} : rot_s3;
end
```

---

## C11. Prohibited Constructs

- SystemVerilog (`logic`, `always_ff`, `always_comb`, `assert property`)
- `casex`, `full_case`, `parallel_case`
- `defparam`, `output reg`
- `#delay` in synthesizable code
- Latches, implicit nets
- `always @(*)` (use `always @*`), explicit sensitivity lists
- Asynchronous/active-low reset (`rst_n`)

---

## C12. Case Statements

- Always include `default` branch
- `case` for exact match; `casez` with `?` for wildcards

## C13. Module Instantiation

- Named port connections only (no positional)
- All ports must appear; unused: `.port()` or `.port(8'd0)`

---

## C14. FSM Control Signal Timing [MUST]

Control signals (calc_en, load_en, done, valid, etc.) MUST match the golden
model's timing model. Two valid patterns — **mixing is forbidden**.

| Golden model pattern | Verilog implementation |
|---------------------|----------------------|
| Signal set in same cycle as state transition | `assign signal = (state_reg == STATE);` (combinational) |
| Signal delayed by 1 cycle from state transition | `always @(posedge clk) signal <= condition;` (registered) |

```verilog
// Pattern A — Combinational control (default for # wire annotated signals)
assign calc_en    = (state_reg == S_CALC) && !load_en_reg;
assign hash_valid = (state_reg == S_DONE) && is_last;

// Pattern B — Registered control (ONLY for # reg_next annotated signals)
always @(posedge clk) begin
    calc_en_reg <= (state_reg == S_CALC) && !load_en_reg;
end
assign calc_en = calc_en_reg;
```

**Rule**: If golden model sets signals immediately on state entry → all related
signals use `assign`. If delayed → all use registered. Mixing causes Pattern 19.
