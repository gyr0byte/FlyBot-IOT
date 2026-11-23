"""Spiking network: circuit file format (circuit.py) and engine (engine.py).

Circuits are data, not code: the engine runs any circuit folder that follows format version 1
(brain/README.md, "Circuit file format"), the designed one or the FlyWire-derived one.
"""
from .circuit import Circuit, CircuitError, load_circuit, write_circuit
from .engine import Network

__all__ = ["Circuit", "CircuitError", "load_circuit", "write_circuit", "Network"]
