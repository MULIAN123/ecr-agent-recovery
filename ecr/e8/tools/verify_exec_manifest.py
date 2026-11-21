"""Verify a package (or a deployed public directory) against an E8 manifest, read-only.
  verify : every listed file exists with the listed SHA-256; no unlisted file (except excluded names)
  compare: two execution manifests are equivalent (same files/hashes, same guard fields)
Usage: python3 -I verify_exec_manifest.py verify --root DIR --manifest M.json [--public-only]
       python3 -I verify_exec_manifest.py compare A.json B.json
Exit 0 = OK; prints a JSON report.
"""
import hashlib
import json
import sys
from pathlib import Path

EXCLUDE = {"execution-manifest-e8.json", "qualification-authorization-e8.json"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify(root: Path, manifest: dict, public_only: bool) -> dict:
    wanted = {r: manifest["files"][r] for r in (manifest["public_files"] if public_only else manifest["files"])}
    problems = []
    for rel, d in wanted.items():
        p = root / rel
        if p.is_symlink() or not p.is_file():
            problems.append(f"missing {rel}")
        elif sha(p) != d:
            problems.append(f"hash differs {rel}")
    present = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    extra = sorted(r for r in present - set(wanted) if r not in EXCLUDE and "__pycache__" not in r and not r.endswith(".pyc"))
    if extra:
        problems += [f"unlisted file {r}" for r in extra]
    return {"status": "OK" if not problems else "FAIL", "checked": len(wanted), "problems": problems}


def compare(a: dict, b: dict) -> dict:
    keys = ("vm_uuids", "python_version", "kernel", "node_binary_sha256", "planner_route", "freeze_record_sha256", "public_files")
    diffs = [k for k in keys if a.get(k) != b.get(k)]
    fa, fb = a["files"], b["files"]
    diffs += [f"file {r}" for r in sorted(set(fa) | set(fb)) if fa.get(r) != fb.get(r)]
    return {"status": "EQUIVALENT" if not diffs else "DIFFERENT", "differences": diffs}


if __name__ == "__main__":
    if sys.argv[1] == "verify":
        args = sys.argv[2:]
        root = Path(args[args.index("--root") + 1]); m = json.loads(Path(args[args.index("--manifest") + 1]).read_text())
        rep = verify(root, m, "--public-only" in args)
    else:
        rep = compare(json.loads(Path(sys.argv[2]).read_text()), json.loads(Path(sys.argv[3]).read_text()))
    print(json.dumps(rep, indent=1))
    sys.exit(0 if rep["status"] in ("OK", "EQUIVALENT") else 1)
