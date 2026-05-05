"""VeriFlow DSL — Python hardware description framework.

Provides formal timing semantics for VeriFlow's Python-to-Verilog pipeline.
Inspired by Amaranth's domain model: timing is a property of assignments,
not signals.

Usage:
    from veriflow.dsl import Module, Signal, Const, Cat, Mux
    from veriflow.dsl import CycleSimulator, VerilogEmitter
"""

from ._types import Signal, Const, Cat, Mux, Value
from ._module import Module, Domain, DomainCollection
from ._simulator import CycleSimulator
from ._emitter import VerilogEmitter
from ._trace import diff_traces, TraceDiff

__all__ = [
    "Signal", "Const", "Cat", "Mux", "Value",
    "Module", "Domain", "DomainCollection",
    "CycleSimulator",
    "VerilogEmitter",
    "diff_traces", "TraceDiff",
]
