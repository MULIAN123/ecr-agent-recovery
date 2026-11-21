# async_sim_ac1：相对冻结包的差异清单（2026-10-16）

**基准**：实验 2b 的冻结运行包，执行清单 `5034c58c…`，共 55 个文件。复制时逐个核对了哈希（`../BASE_COPY_RECORD.json`），只读参考副本在 `../frozen_base_r2/`。

**本包的状态**：已编写，**未执行、未冻结**。没有运行任何测试，也没有做语法检查。

逐文件哈希对比见 `../work_splice/filediff.txt`。

## 1. 被测机制：没有改变决策逻辑

| 文件 | 状态 | 改动 |
|---|---|---|
| `src/authority.py` | **修改** | 只有暂停点相关的改动：<br>① 模块说明；<br>② `import pausepoint`；<br>③ `_crash_point` 的函数体由 `os._exit(137)` 改为 `pausepoint.pause(name, scope)`；<br>④ 新增 4 处 `_crash_point` 调用：`before_grant`（F1）、`after_fence_post`（R1）、`after_fence_record`（R2）、`after_result_log`（R7）。<br>授权条件、记录内容、端点调用、写入顺序都没有改。未设置 `ECR_PAUSE_AT` 时，行为与冻结版相同。由测试 T-E 用 AST 比对来验证 |
| `src/agent_v1.py`、`agent_v2.py`、`evidence.py`、`endpoint.py`、`protocol.py`、`plan.mjs`、`plan_js.mjs` | 未改 | 字节相同（T-E 验证） |
| `arms/*`（冻结的分类器和规划器 TS） | 未改 | 字节相同。`arms_js/` 要在 guest 上重新生成，没有复制 |

## 2. 新增的实验设施

| 文件 | 作用 |
|---|---|
| `src/pausepoint.py` | 暂停点。到达指定的点和范围时，先写持久收据和就绪标记，然后阻塞，等待 SIGKILL。不会自行退出；如果出现释放文件，就抛出异常。 |
| `src/acreceipts.py` | 纯函数：校验 SIGKILL 收据（原始 `si_code`/`si_status`、`wtermsig`、标准化返回码、pid 与启动时间、收据哈希、时间顺序），以及快照时机 |
| `runner/ackill.py` | 以 root 身份发出 SIGKILL：先读 `/proc` 启动时间，再写意图，然后 `kill`，用 `waitid(WNOWAIT)` 取原始状态，再读一次启动时间，回收进程，最后写收据。收据保留 scorer_v2 兼容字段 `exit_status`（等于标准化返回码）和 `barrier_id`（等于崩溃点名） |
| `runner/aclib.py` | 判定这次尝试是否有效、是否停批。异常退出、安全违规和 L 损失都只是**观察结果**，不停批（DC1） |
| `runner/run_ac.py` | 由冻结的 `run_r2.py` 拼接而成（拼接脚本见 `../work_splice/splice_runner.py`，每处替换都必须唯一匹配）。新的 `run_attempt` 负责：<br>· 发送阶段：暂停点或 B 屏障，用 SIGKILL 杀进程并取收据；<br>· 每次恢复之前：先注入 overlay，再做恢复前快照（记录哈希和时机）；<br>· 连续恢复：除最后一次外都暂停并被杀；<br>· 对照：正常完成。<br>其余部分：授权文件**必须写明批次 ID 和集合**（冻结版不检查这两项）；拒绝 `ECR_TEST_CRASH_*` 环境变量；`start()` 启动子进程时显式加 `-B`（因 `-E` 会忽略 `PYTHONDONTWRITEBYTECODE`，F8）；运行第三个评分器（诊断）；只有事先声明的单元且退出码为 1 的预期 JSON 解析错误，评分器失败才被容忍（DC3 / F2 / F9）；通过 `filter_expected_faults` 对称校验主/次评分器预期故障豁免，限定于对应构造（`CTRL` / `TRUNCATE_*` / `B1B`）与指定故障（F1 / F10） |
| `src/scorer_diag.py` | 独立的诊断评分器，分三个判断：A 执行有效性；B 中 S1–S4 各自判定；C 恢复完成。按**交付物**统计效果，即单元账本中的全部效果，不区分键。任务状态取自 `task.json`。按方案 r2 第 3 节判定 UNKNOWN；按 5.1 节的 F3/P0 条件化规则判定机会。另外记录两个标签（异常退出、证据不完整），并与预测比对；预测一致不等于通过 |
| `tools/build_ac_cells.py` | 生成单元表：AC 102 个，首批 40 个按方案顺序；另有 ACQ 资格族 6 个。基础场景和真值行取自冻结的 `cells_p3.json`；为 `CTRL`、`TRUNCATE_*`、`B1B` 同步声明主/次评分器同名预期故障豁免（F10） |
| `tools/direct_authorize.py` | 仅用于压力测试 S6：绕过 agent，直接向 authority 申请 FENCED_NEGATIVE |
| `tests/*` | 针对性测试和合成夹具，见 `../VERIFICATION_PLAN_ac1.md` |

## 3. 改名或适配

| 文件 | 改动 |
|---|---|
| `tools/make_execution_manifest.py` | 输出文件名改为 `execution-manifest-ac1.json`；排除文件改为 `ac1-authorization.json`；要求存在 `private/cells_ac.json`；部署列表加入 `tools/direct_authorize.py` |
| `runner/run_r2.py` | 删除，由 `run_ac.py` 取代 |
| `tools/build_p3_cells.py`、`CHANGES_vs_r1.md` | 删除（属于冻结包的历史文件） |
| `tests/` 下冻结包的开发测试（`dev_harness`、`run_dev_suite`、`test_authority` 等） | **没有带入**。它们依赖已经删除的 `os._exit` 钩子和开发框架。是否移植其中与钩子无关的测试，见待决事项 Q4 |

## 4. 保持不变

`runner/p3lib.py`、`runner/controls.py`，以及 `tools/` 下的 `planner_recorder.py`、`planner_records.py`、`replay.py`、`finalize_states.py`、`qualify_planner.py`、`planner_inputs.py`、`strip_ts.mjs`、`compare_qualification_r1.py`，与冻结版字节相同。
- `compare_qualification_r1.py` 是针对实验 2 资格验证的工具，本包不使用。
