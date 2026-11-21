"""VQ-E8 persistence checks on the Linux guest (read-only except for its own scratch directory).
  semantics --scratch DIR : protocol.durable_json writes, refuses to overwrite (FileExistsError), replace=True replaces,
                            leaves no *.tmp-* file; os.fsync and directory fsync succeed on the guest filesystem
  record    --root DIR [--root DIR ...] --out LIST.json : record SHA-256 of every file under each DIR
                                          (a VQ batch and its SUT/ledger roots)
  verify    --list LIST.json            : after a guest reboot, every recorded file still exists with the same SHA-256
Usage: sudo python3 -I -B persist_check.py semantics --scratch /var/lib/ecr-e8-persist-scratch --out R.json
"""
import hashlib
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def semantics(scratch: Path) -> dict:
    sys.path.insert(0, str(PKG / "src"))
    from protocol import durable_json  # frozen; Linux only (os.O_DIRECTORY)
    scratch.mkdir(mode=0o700)
    out, p = {}, scratch / "a.json"
    durable_json(p, {"v": 1})
    out["write"] = p.is_file()
    try:
        durable_json(p, {"v": 2}); out["exclusive"] = False
    except FileExistsError:
        out["exclusive"] = True
    durable_json(p, {"v": 3}, replace=True)
    out["replace"] = json.loads(p.read_text())["v"] == 3
    out["no_tmp_left"] = not list(scratch.glob("*.tmp-*"))
    out["status"] = "OK" if all(v is True for v in out.values()) else "FAIL"
    return out


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "semantics":
        rep = semantics(Path(args[args.index("--scratch") + 1]))
    elif cmd == "record":
        roots = [Path(args[i + 1]) for i, a in enumerate(args) if a == "--root"]
        if not roots or not all(r.is_dir() for r in roots):
            sys.exit("record: every --root must be an existing directory")
        rep = {"roots": [str(r) for r in roots],
               "files": {str(p): sha(p) for r in roots for p in sorted(r.rglob("*")) if p.is_file()}}
        out = Path(args[args.index("--out") + 1])
        if out.exists():
            sys.exit(f"refusing to overwrite {out}")
        out.write_text(json.dumps(rep, indent=1))
        rep = {"status": "RECORDED", "roots": len(roots), "files": len(rep["files"])}
    else:
        rec = json.loads(Path(args[args.index("--list") + 1]).read_text())
        bad = [p for p, d in rec["files"].items() if not Path(p).is_file() or sha(p) != d]
        rep = {"status": "OK" if not bad else "FAIL", "checked": len(rec["files"]), "missing_or_changed": bad[:50]}
    if cmd in ("semantics", "verify") and "--out" in args:  # report file read by vq_e8_checks (VQ-12)
        out = Path(args[args.index("--out") + 1])
        if out.exists():
            sys.exit(f"refusing to overwrite {out}")
        out.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    sys.exit(0 if rep["status"] in ("OK", "RECORDED") else 1)
