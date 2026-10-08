from cupy.cuda import compiler as compiler
from cupy.cuda import nvrtc as nvrtc
from cupy.cuda import runtime as runtime

class Device:
    @property
    def compute_capability(self) -> str: ...
