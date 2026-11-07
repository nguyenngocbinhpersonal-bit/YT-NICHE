"""Gọi `pipectl` của YT_WF_001 bằng subprocess (không shell): `runs` (đọc) và `mark-published` (ghi, khó đảo ngược)."""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from yt_niche import config

RUNS_TIMEOUT_S = 120
MARK_TIMEOUT_S = 300  # đã có ca chờ catalog 2 phút 13 giây

# Kết quả của mark_published
OK = "ok"
CATALOG_PENDING = "catalog_pending"  # folder + project.json đã ghi, catalog chưa
CATALOG_NOT_READY = "catalog_not_ready"  # chưa đổi gì, thử lại sau
LOCKED = "locked"
ERROR = "error"


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


@dataclass
class MarkResult:
    status: str  # ok | catalog_pending | catalog_not_ready | locked | error
    message: str = ""  # chỉ dòng ERROR: cuối (hoặc lời giải thích tiếng Việt), không bao giờ cả stderr
    data: dict | None = None  # JSON của lệnh khi ok

    @property
    def ok(self) -> bool:
        return self.status == OK


_NOT_READY_HINT = "Catalog chưa sẵn sàng nên chưa đổi gì. Chờ khoảng 1 phút rồi thử lại."
_PENDING_HINT = "Folder và project.json đã ghi nhưng catalog chưa. Chờ khoảng 1 phút rồi bấm «Thử lại ghi catalog» trên thẻ."
_LOCKED_HINT = "Folder đang bị giữ khoá. Đóng File Explorer, CapCut và cửa sổ Studio đang upload rồi thử lại."


def classify_mark(res: CmdResult) -> MarkResult:
    """Phân loại kết quả `mark-published` từ mã thoát + stderr (không đoán từ chỗ nào khác)."""
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            return MarkResult(ERROR, "pipectl mark-published thoát 0 nhưng không trả JSON hợp lệ.")
        if not isinstance(data, dict):
            return MarkResult(ERROR, "pipectl mark-published trả JSON không đúng dạng.")
        return MarkResult(OK, "", data)
    line = error_line(res.stderr)
    err = res.stderr or ""
    if res.returncode == 2 and "catalog was not updated" in err:
        return MarkResult(CATALOG_PENDING, _PENDING_HINT)
    if res.returncode == 2 and "catalog not ready" in err:
        return MarkResult(CATALOG_NOT_READY, _NOT_READY_HINT)
    if "still locked" in err:
        return MarkResult(LOCKED, _LOCKED_HINT)
    return MarkResult(ERROR, line or f"pipectl mark-published thoát mã {res.returncode}")


def mark_published(run_dir: Path | str, url: str, published_at: str, timeout: float = MARK_TIMEOUT_S) -> MarkResult:
    """`pipectl mark-published --run-dir … --url … --published-at … --json`. Không tự sửa project.json hay catalog."""
    try:
        res = _pipectl(
            ["mark-published", "--run-dir", str(run_dir), "--url", url, "--published-at", published_at, "--json"], timeout
        )
    except PipectlError as e:
        return MarkResult(ERROR, str(e))
    return classify_mark(res)


def _num(v) -> int | None:
    try:
        return int(str(v))
    except ValueError:
        return None


def find_run_dir(root: Path | str, channel_slug: str, number: int) -> str | None:
    """Chạy `runs` MỚI (không cache) và lấy run_dir theo khoá (kênh, số): folder có thể đã đổi tên từ lần quét trước."""
    for r in runs(root).get("runs", []):
        if r.get("channel_slug") == channel_slug and _num(r.get("video_id")) == number:
            return r.get("run_dir")
    return None
