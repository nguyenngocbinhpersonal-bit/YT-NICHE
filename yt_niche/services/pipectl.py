"""Gọi `pipectl` của YT_WF_001 bằng subprocess (không shell). Ở đây chỉ có lệnh đọc; lệnh ghi do task sau thêm."""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from yt_niche import config

RUNS_TIMEOUT_S = 120


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


class PipectlError(RuntimeError):
    pass


def _decode(b: bytes | None) -> str:
    return (b or b"").decode("utf-8", errors="replace")


def _run(args: list[str], timeout: float, cwd: Path | None = None) -> CmdResult:
    """Chạy `args` (đã gồm cả file thực thi), đọc bytes rồi giải mã UTF-8 để tiếng Nhật/Hàn không vỡ."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        p = subprocess.run(
            [str(a) for a in args],
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired as e:
        raise PipectlError(f"Quá thời gian chờ ({timeout:g}s): {_decode(e.stderr)[-200:]}") from e
    except OSError as e:
        raise PipectlError(f"Không chạy được lệnh: {e}") from e
    return CmdResult(p.returncode, _decode(p.stdout), _decode(p.stderr))


def error_line(stderr: str) -> str:
    """Dòng `ERROR:` cuối của stderr (khi pgserver timeout nó in cả log 86 KB); không có thì dòng cuối."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("ERROR:"):
            return ln
    return lines[-1] if lines else ""


def _pipectl(args: list[str], timeout: float) -> CmdResult:
    return _run([config.YT_WF_PYTHON, config.PIPECTL_PATH, *args], timeout)


def runs(root: Path | str, timeout: float = RUNS_TIMEOUT_S) -> dict:
    """`pipectl runs --root <root> --json` đã parse. Lỗi thì ném PipectlError với dòng ERROR: cuối."""
    res = _pipectl(["runs", "--root", str(root), "--json"], timeout)
    if res.returncode != 0:
        raise PipectlError(error_line(res.stderr) or f"pipectl runs thoát mã {res.returncode}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise PipectlError(f"pipectl runs không trả JSON hợp lệ: {e}") from e
