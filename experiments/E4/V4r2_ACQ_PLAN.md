# V4r2（ACQ 资格批次 第 2 次运行 `ecr-acq-20261017-r2`）执行命令与取证方案（2026-10-17）

- **私有冻结包位置（VM）**：`<HOME>/ecr-ac1-freeze-r2-20261017/async_sim_ac1`（权限 `0700`，64 个文件，tree `04622c2a…`）
- **执行清单 `execution-manifest-ac1.json` SHA-256**：`2e958a52b00e1d1fd0165abda61d6173bb352a843b3ca0402966f75b5e664aae`
- **批次 ID**：`ecr-acq-20261017-r2`
- **公开部署目录**：`/opt/ecr-followup-r1-async-ac1/ecr-acq-20261017-r2`
- **新存储目录（保留第 1 次运行证据不动）**：新建 `/var/lib/ecr-followup-r1/async-sim-ac1-r2`（`root:ecr-scorer` `0750`）并通过 `mount --bind` 挂载至 `/var/lib/ecr-followup-r1/async-sim-ac1`，使第 1 次运行的底层目录与文件完全保持原样不改动，同时无需改动 `run_ac.py` 的常量定义即可全量运行 6 个 ACQ 单元。

## 请在 VM 的 SSH 会话（提示符 `guest-admin@ecrfollowupr`）中粘贴执行的 `sudo` 命令

```bash
# 1. 创建新 var-root 目录 /var/lib/ecr-followup-r1/async-sim-ac1-r2 并 bind-mount（保留第一次运行原目录不动）
mkdir -p /tmp/ecr-v4r2-logs
sudo install -d -o root -g ecr-scorer -m 0750 /var/lib/ecr-followup-r1/async-sim-ac1-r2
sudo mount --bind /var/lib/ecr-followup-r1/async-sim-ac1-r2 /var/lib/ecr-followup-r1/async-sim-ac1

# 2. 运行全部 6 个 ACQ 资格单元（批次 ecr-acq-20261017-r2）
cd <HOME>/ecr-ac1-freeze-r2-20261017/async_sim_ac1 && \
sudo /usr/bin/python3 -I -B runner/run_ac.py run \
  --set ACQ \
  --batch-id ecr-acq-20261017-r2 \
  2>&1 | tee /tmp/ecr-v4r2-logs/v4r2_acq_run.log ; \
echo "run_exit=${PIPESTATUS[0]}" | tee -a /tmp/ecr-v4r2-logs/v4r2_acq_run.log

# 3. 运行同运行时重放（same-runtime replay）
cd <HOME>/ecr-ac1-freeze-r2-20261017/async_sim_ac1 && \
sudo /usr/bin/python3 -I -B runner/run_ac.py replay \
  --batch-id ecr-acq-20261017-r2 \
  2>&1 | tee /tmp/ecr-v4r2-logs/v4r2_acq_replay.log ; \
echo "replay_exit=${PIPESTATUS[0]}" | tee -a /tmp/ecr-v4r2-logs/v4r2_acq_replay.log

# 4. 打包导出批次证据、检查公开部署目录无字节码副作用，并解除 bind-mount 恢复原目录可见性
sudo tar -cf /tmp/ecr-v4r2-logs/acq-ecr-acq-20261017-r2.tar \
  -C /var/lib/ecr-followup-r1/async-sim-ac1-r2 ecr-acq-20261017-r2 attempt-index && \
sudo chown guest-admin:guest-admin /tmp/ecr-v4r2-logs/acq-ecr-acq-20261017-r2.tar && \
sudo find /opt/ecr-followup-r1-async-ac1/ecr-acq-20261017-r2 -type f -printf '%P\n' | LC_ALL=C sort | \
  sudo xargs -I{} sha256sum "/opt/ecr-followup-r1-async-ac1/ecr-acq-20261017-r2/{}" > /tmp/ecr-v4r2-logs/v4r2_public_deployed.sha256 && \
sudo find /opt/ecr-followup-r1-async-ac1/ecr-acq-20261017-r2 -name __pycache__ -o -name '*.pyc' > /tmp/ecr-v4r2-logs/v4r2_public_pycache.txt && \
sudo umount /var/lib/ecr-followup-r1/async-sim-ac1 && \
sha256sum /tmp/ecr-v4r2-logs/* | tee /tmp/ecr-v4r2-logs/SHA256SUMS.txt
```
