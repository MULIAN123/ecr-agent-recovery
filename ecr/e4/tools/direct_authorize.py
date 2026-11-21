"""Stress S6 only (outside the fault model): bypass the agent and apply directly to the authority for a
FENCED_NEGATIVE send with a given generation, then dispatch it if granted. Used after the runner
injected a stale copy of a negative fence record (overlay STALE_FENCE_COPY). Runs as ecr-sut.
Prints one JSON line: {"granted": bool, "dispatch": {...} | null, "denial_reasons": [...]}.
Usage: python direct_authorize.py <recover-configuration.json> '{"generation": "stale"|int}'
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # script dir is tools/, not src/
from authority import Authority  # noqa: E402


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text())
    args = json.loads(sys.argv[2])
    contract = {k: config["endpoint_contract"][k] for k in ("query", "idempotent")}
    auth = Authority(Path(config["store"]), config["r2_mode"], config["logical_operation_id"], contract, config["port"])
    report = auth.report()
    fence = report.unconsumed_fence or {}
    generation = (fence.get("response") or {}).get("retry_generation") if args.get("generation") == "stale" else args.get("generation")
    n = len(list((Path(config["store"]) / "authority" / "grants").glob("*.json"))) + 1
    request = {"logical_operation_id": config["logical_operation_id"], "attempt_id": f"{config['attempt_id']}-tool{n}",
               "transport_request_id": f"{config['transport_request_id']}-tool{n}", "generation": generation}
    grant = auth.authorize("FENCED_NEGATIVE", request)
    if grant is None:
        denials = sorted((Path(config["store"]) / "authority" / "log").glob("*-denial.json"))
        reasons = json.loads(denials[-1].read_text()).get("reasons") if denials else []
        print(json.dumps({"granted": False, "dispatch": None, "denial_reasons": reasons}))
        return
    result = auth.dispatch(grant)
    print(json.dumps({"granted": True, "dispatch": result, "denial_reasons": []}))


if __name__ == "__main__":
    main()
