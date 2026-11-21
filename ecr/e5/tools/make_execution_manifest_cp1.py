"""Write execution-manifest-cp1.json for runner/run_cp1.py (r9.6 Batch A, CONV+; derived from the 2b tool:
only the manifest name, the required cell table and the public src_cp/ directory differ). Run on the guest after the JS
artifacts were generated there (route J), as the unprivileged owner of the package.

The manifest fixes what run_r2.guard() checks before any batch: VM UUIDs, Python version, kernel,
Node binary hash, planner route, the SHA-256 of every package file, and the subset of files that
is deployed to the public (read-only, root-owned) directory for the service identities.
Excluded from "files": the manifest itself, qualification-authorization.json, __pycache__ and .pyc.
Candidate binding (v4): --candidate-manifest names the frozen-candidate code manifest. Every file it
lists must exist in the package with that hash, and every package file except the guest-generated
arms_js/ must be listed in it; its SHA-256 is recorded as candidate_manifest_sha256. The execution
manifest's own SHA-256 is the code identity the planner-call recorder checks on every call.
Route J requires arms_js/strip-manifest.json with status COMPLETED, produced by the same Node
binary (hash) as given here; otherwise the tool refuses. It never overwrites an existing manifest.

Usage: python3 -I make_execution_manifest.py --pkg DIR --vm-uuid UUID [--vm-uuid UUID ...]
           --python-version X.Y.Z --kernel RELEASE --node-sha256 HEX --route J|T
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

NAME = "execution-manifest-cp1.json"
EXCLUDE = {NAME, "qualification-authorization.json"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_candidate(files: dict, candidate: dict) -> list[str]:
    problems = [f"candidate file {rel} missing or differs" for rel, digest in candidate["files"].items() if files.get(rel) != digest]
    problems += [f"package file {rel} not in candidate manifest" for rel in files
                 if rel not in candidate["files"] and not rel.startswith("arms_js/")]
    return problems


def build(pkg: Path, vm_uuids, python_version, kernel, node_sha, route) -> dict:
    files = {}
    for p in sorted(pkg.rglob("*")):
        rel = p.relative_to(pkg).as_posix()
        if p.is_symlink():
            raise SystemExit(f"symlink in package: {rel}")
        if p.is_dir() or rel in EXCLUDE or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        files[rel] = sha(p)
    if "private/cells_cp1.json" not in files:
        raise SystemExit("private/cells_cp1.json missing")
    if route == "J":
        strip = pkg / "arms_js/strip-manifest.json"
        if not strip.is_file():
            raise SystemExit("route J requires arms_js/strip-manifest.json (generate the JS on this guest first)")
        m = json.loads(strip.read_text())
        if m.get("status") != "COMPLETED" or m.get("node_binary_sha256") != node_sha or m.get("options") != {"mode": "strip"}:
            raise SystemExit("strip manifest not COMPLETED, or produced by another Node binary or options")
    elif route != "T":
        raise SystemExit("route must be J or T")
    public = sorted(rel for rel in files
                    if rel.startswith(("src/", "src_cp/", "arms/")) or (route == "J" and rel.startswith("arms_js/"))
                    or rel in ("tools/planner_recorder.py", "tools/replay.py"))
    return {"status": "EXECUTION_MANIFEST", "vm_uuids": [u.strip().lower() for u in vm_uuids],
            "python_version": python_version, "kernel": kernel, "node_binary_sha256": node_sha,
            "planner_route": route, "files": files, "public_files": public}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--vm-uuid", action="append", required=True)
    ap.add_argument("--python-version", required=True)
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--node-sha256", required=True)
    ap.add_argument("--route", required=True)
    ap.add_argument("--candidate-manifest", required=True)
    args = ap.parse_args()
    pkg = Path(args.pkg).resolve()
    out = pkg / NAME
    if out.exists():
        sys.exit(f"refusing to overwrite {out}")
    manifest = build(pkg, args.vm_uuid, args.python_version, args.kernel, args.node_sha256, args.route)
    candidate_raw = Path(args.candidate_manifest).read_bytes()
    problems = check_candidate(manifest["files"], json.loads(candidate_raw))
    if problems:
        sys.exit("package does not match the candidate manifest:\n" + "\n".join(problems))
    manifest["candidate_manifest_sha256"] = hashlib.sha256(candidate_raw).hexdigest()
    manifest["candidate_manifest_file_count"] = len(json.loads(candidate_raw)["files"])
    with open(out, "x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"files": len(manifest["files"]), "public_files": len(manifest["public_files"]),
                      "route": args.route, "sha256": sha(out)}))


if __name__ == "__main__":
    main()
