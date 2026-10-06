"""Compatibility import for the general Conclave command."""
import sys
from conclave import cli as _implementation
sys.modules[__name__] = _implementation
