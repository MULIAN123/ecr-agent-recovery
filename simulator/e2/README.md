# ECR experiment 2: asynchronous fault simulation (package r1)

52 scenarios (8 crash points x 4 endpoint profiles + 5 local-evidence overlays x 4 profiles) x 8 arms = 416 cells.
`sudo python3 run.py --mode qualify`, host audit, then `--mode formal` with qualification-authorization.json.
Public code is deployed to /opt/ecr-followup-r1-async/<batch>; private/cells.json (truth) stays in the 0700 package.
