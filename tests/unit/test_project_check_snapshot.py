import gzip
import hashlib
import io
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest

from aegisops.project_checks.snapshot import scan_snapshot
from aegisops.project_runner.models import SnapshotInfo
from aegisops.project_runner.snapshot import Snapshot, snapshot_project
from aegisops.projects.models import Project, ProjectError


def _make_snap(members: list[tuple[str, bytes | tarfile.TarInfo]]) -> Snapshot:
    buf = io.BytesIO()
    py_c = js_c = tot = 0
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        for item in members:
            if isinstance(item[1], tarfile.TarInfo):
                ti = item[1]
                tar.addfile(ti, io.BytesIO(b"") if ti.isfile() else None)
            else:
                name, data = item[0], item[1]
                ti = tarfile.TarInfo(name=name)
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
                tot += len(data)
                py_c += Path(name).suffix.lower() == ".py"
                js_c += Path(name).suffix.lower() in {".js", ".ts"}
    raw = buf.getvalue()
    cnt = max(1, len(members))
    return Snapshot(
        data=raw,
        info=SnapshotInfo(
            sha256=hashlib.sha256(raw).hexdigest(),
            files=cnt,
            python_files=max(1, py_c),
            javascript_files=js_c,
            bytes=tot,
            skipped=0,
        ),
    )


@pytest.mark.unit
@pytest.mark.safety
def test_scan_snapshot_real_and_post_mutation_isolation(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('safe canary')\nraise RuntimeError('canary')\n")
    (tmp_path / "data.json").write_text('{"ok": true}')
    stat = tmp_path.stat()
    proj = Project(
        id=uuid4(),
        name="p",
        path=str(tmp_path),
        device=stat.st_dev,
        inode=stat.st_ino,
        added_at="2026-01-01T00:00:00Z",
    )
    snap = snapshot_project(proj)
    (tmp_path / "main.py").write_text("def broken_syntax(:")
    (tmp_path / "data.json").write_text("INVALID JSON {")
    report = scan_snapshot(proj.id, snap)
    assert report.status == "passed"
    assert report.findings == ()
    assert report.execution_performed is False


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "bad_path",
    [
        "../main.py",
        "/abs/main.py",
        "sub\\main.py",
        "ctrl\x01.py",
        "del\x7f.py",
        "bad:colon.py",
        "trailing.",
        "nul.py",
        "com¹.py",
        ".env",
        "node_modules/mod.py",
    ],
)
def test_scan_snapshot_rejects_malformed_paths(bad_path: str) -> None:
    snap = _make_snap([(bad_path, b"# bad\n"), ("main.py", b"# ok\n")])
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), snap)


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "kind",
    ["symlink", "hardlink", "dir", "fifo"],
)
def test_scan_snapshot_rejects_non_regular_entries(kind: str) -> None:
    ti = tarfile.TarInfo(name="bad_entry")
    if kind == "symlink":
        ti.type = tarfile.SYMTYPE
        ti.linkname = "main.py"
    elif kind == "hardlink":
        ti.type = tarfile.LNKTYPE
        ti.linkname = "main.py"
    elif kind == "dir":
        ti.type = tarfile.DIRTYPE
    else:
        ti.type = tarfile.FIFOTYPE
    snap = _make_snap([("bad_entry", ti), ("main.py", b"# ok\n")])
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), snap)


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    "members",
    [
        [("main.py", b"# 1\n"), ("MAIN.PY", b"# 2\n")],
        [("foo/main.py", b"# 1\n"), ("FOO/main.py", b"# 2\n")],
        [("sub", b"# file\n"), ("SUB/child.py", b"# child\n")],
        [("SUB/child.py", b"# child\n"), ("sub", b"# file\n")],
    ],
)
def test_scan_snapshot_rejects_casefold_collisions(members: list[tuple[str, bytes]]) -> None:
    snap = _make_snap(members)
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), snap)


@pytest.mark.unit
@pytest.mark.safety
def test_scan_snapshot_rejects_bad_metadata_and_payloads() -> None:
    base = _make_snap([("main.py", b"# ok\n")])
    bad_hash = Snapshot(
        data=base.data,
        info=base.info.model_copy(update={"sha256": "0" * 64}),
    )
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), bad_hash)

    bad_count = Snapshot(
        data=base.data,
        info=base.info.model_copy(update={"files": 2}),
    )
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), bad_count)

    gz_data = gzip.compress(base.data)
    gz_snap = Snapshot(
        data=gz_data,
        info=SnapshotInfo(
            sha256=hashlib.sha256(gz_data).hexdigest(),
            files=1,
            python_files=1,
            javascript_files=0,
            bytes=len(gz_data),
            skipped=0,
        ),
    )
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), gz_snap)

    with pytest.raises(ProjectError):
        scan_snapshot(
            uuid4(),
            Snapshot(
                data=b"not a tar",
                info=SnapshotInfo(
                    sha256=hashlib.sha256(b"not a tar").hexdigest(),
                    files=1,
                    python_files=1,
                    javascript_files=0,
                    bytes=9,
                    skipped=0,
                ),
            ),
        )

    big_file = _make_snap([("main.py", b"x" * (256 * 1024 + 1))])
    with pytest.raises(ProjectError):
        scan_snapshot(uuid4(), big_file)
