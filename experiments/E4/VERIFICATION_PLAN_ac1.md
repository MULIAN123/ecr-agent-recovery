# ac1 实验包：待执行的验证命令、验收条件与设计记录（2026-10-16）

> **本轮只写了代码和测试，没有执行任何测试，也没有做语法检查。**
> - 没有开 VM，没有冻结，没有运行实验，没有改论文、冻结对象和长期记忆。
> - 下面各步都要在你批准之后才执行。

**位置**：
- 实验包：`outputs/20261016/ECR_授权崩溃实验包_ac1/async_sim_ac1/`
- 冻结参考副本（只读）：`frozen_base_r2/`
- 差异清单：`async_sim_ac1/CHANGES_ac1.md`
- 首批清单：`FIRST_BATCH_40.md`

## 1. 验证步骤与验收条件

### V0 语法检查（主机，只读，不运行任何逻辑）

```bash
cd "<WORKSPACE>" && <HOST-PATH> -I -m py_compile src/pausepoint.py src/acreceipts.py src/scorer_diag.py src/authority.py runner/ackill.py runner/aclib.py runner/run_ac.py tools/build_ac_cells.py tools/direct_authorize.py tools/make_execution_manifest.py tests/fixtures_ac.py tests/test_ac_receipts.py tests/test_ac_snapshot.py tests/test_ac_diag_unknown.py tests/test_ac_crosskey.py tests/test_ac_aclib.py tests/test_ac_cells.py tests/test_ac_equivalence.py tests/test_ac_pause_kill.py
```

**验收**：退出码为 0，没有任何输出。

### V1 针对性测试（主机 Windows，不需要 root，只用合成夹具）

```bash
cd "<WORKSPACE>" && <HOST-PATH> -I -m unittest discover -s tests -t tests -p "test_ac_*.py" -v
```

| 测试 | 覆盖内容 | 验收 |
|---|---|---|
| T-R `test_ac_receipts` | 信号收据：CLD_KILLED/9 有效；`exit(137)`（CLD_EXITED/137）、CLD_DUMPED、其他信号、只有标准化 −9、pid 不符、启动时间变化、哈希被改、时间倒序、兼容字段不一致，都要被拒；B4 屏障收据由端点写入时，不比较 pid | 全部通过 |
| T-S `test_ac_snapshot` | 快照时机：必须在回收之后、恢复开始之前，且没有存活的 agent 进程。另有一项结构检查：恢复循环里的顺序必须是先注入，再快照，再启动恢复，最后校验时机 | 全部通过 |
| T-U `test_ac_diag_unknown` | UNKNOWN 的判定：账本缺失；`task.json` 缺失或截断；**缺少 final.json 时不能判成 UNKNOWN**；端点日志不可读；有发送但依据记录不可读；有发送但没有任何依据（判为 VIOLATED）；F3/P0 的 C3–C5（`first_dispatch` 从 grant 中读取，并通过 grant_id 关联）；L 的传递规则；异常退出与安全结果同时记录 | 全部通过 |
| T-K `test_ac_crosskey` | 跨键重复：诊断评分器按交付物统计，结果为 2 个效果，判 S1、S2 违反。对照冻结的 scorer_v2：它报 FOREIGN_EFFECT 故障，且不判重复 | 前两项通过；第三项在 Windows 上**跳过**（scorer_v2 调用 `os.geteuid`） |
| T-A `test_ac_aclib` | 判定这次尝试的状态：异常退出属于观察结果；执行无效则停批；基础设施问题记为 INFRA_FAILED | 全部通过 |
| T-C `test_ac_cells` | 单元表：共 102 个（M 74、B 2、S 18、C 8）；首批 40 个与方案的顺序完全一致；3 个对照都保留；B1b 不在首批；不可达的组合不存在；最后一次恢复不暂停；只有两个 F3/P0 单元用条件化规则；每个单元都登记了预测 | 全部通过。它会在临时目录里运行生成脚本，不写入实验包 |
| T-E `test_ac_equivalence` | `authority.py` 与冻结版在 AST 上相同（去掉暂停点相关部分之后）；另外 15 个源文件字节相同；`src/` 中没有 `os._exit` | 全部通过 |
| T-P `test_ac_pause_kill` | 真实子进程：暂停后被 SIGKILL，收据有效；`exit(137)` 被拒；释放暂停会报错；未设置环境变量时不会暂停 | 在 Windows 上**全部跳过**（4 项） |

**V1 总验收**：失败数为 0；跳过的只有 T-P 的 4 项和 T-K 的第 3 项，共 5 项。

### V2 Linux 上补跑（需要开 VM，用非特权用户）

在实验 VM 上，用非特权用户运行同一条 unittest 命令。

**验收**：失败数为 0，跳过数为 0。T-E 在 guest 上如果没有 `frozen_base_r2`，会被跳过；这种情况要写明，并以 V1 的结果为准。

### V3 生成单元表与执行清单（属于冻结的准备步骤，需要另行批准）

运行 `tools/build_ac_cells.py private/cells_p3.json private/cells_ac.json`，然后在 guest 上生成 `arms_js` 和 `execution-manifest-ac1.json`。

**验收**：
- 生成脚本输出的汇总等于 `{"AC_units": 102, "AC_first_batch": 40, "ACQ_units": 6}`；
- 执行清单包含 `src/pausepoint.py`、`src/acreceipts.py`、`src/scorer_diag.py`、`tools/direct_authorize.py`。

### V4 ACQ 资格运行（在 guest 上用 root 运行 run_ac.py，需要另行批准）

这一步首次端到端检验 `run_attempt`：暂停、杀进程、快照、连续恢复、注入、三个评分器。资格单元有 6 个：F2/P1、F3/P2、R1/P1、SB/P1、对照 P1、截断 consumed/P1。

**验收**：
- 每个单元的 A 判断都是 VALID；
- 收据全部满足 V1 中的接受条件；
- 快照时机有效；
- 只出现预先声明的评分器故障；
- 计划的负对照（植入错误的评分器夹具）能被检出。

**以上任何一步失败，都停下并报告**，不自动修改后重跑。

## 2. 设计矛盾与记录（只记录，没有自行改变被测机制或评分口径）

| # | 内容 | 现在的处理 | 需要你决定吗 |
|---|---|---|---|
| DC1 | 冻结的 `p3lib.evaluate_attempt` 把恢复进程的非零退出和缺少 final.json 视为失败并停批；方案 r2 把异常退出定义为观察结果 | `aclib` 按方案实现：只有执行无效或基础设施问题才停批。如果最后一次恢复的规划器调用失败，仍记为 INFRA_FAILED | 是（Q1） |
| DC2 | 对照单元没有被杀，而冻结的 scorer_v2 要求注入收据满足 `exit_status == -9`，所以对照单元**必然**出现 INJECTION_RECEIPT_MISMATCH | 对照单元写入 `injection_action: NONE` 的收据；这个故障在单元中预先声明为预期，记录但不停批。scorer_v2 没有改动 | 是（Q2） |
| DC3 | 截断类压力单元会让冻结的主评分器和次评分器在 `json.loads` 处失败 | 只对预先声明的单元容忍评分器失败，并记录在案；诊断评分器把受影响的指标判为 UNKNOWN。没有改评分规则 | 是（Q3） |
| DN1 | 冻结 scorer_v2 的 `fenced_ok` 只要求"存在一条 retry_generation 相同的 negative fence，而且之后没有发送"，**不要求这条 fence 是最新的**。所以 S6 中用旧 generation 发出的请求，可能被判为依据有效 | 只记录；S6 的预测按 scorer_v2 现有规则登记（S4 满足），并附上本条说明 | 是（Q5） |
| DC4 | F3/P0 条件 C1 的"代码身份"，用的是 ac1 版 `authority.py` 的哈希，不是冻结版的哈希（因为加了暂停点） | 写入顺序没有改，由 T-E 证明；诊断评分器记录部署的哈希 | 否 |
| DC5 | 暂停点会改变时序（进程阻塞在该点）；R5、R6 的请求可能在杀进程前后才被端点处理 | 与现有 B 屏障同类；在计划里写明，不当作机制的性质 | 否 |
| DC6 | S6 用工具绕过 agent，属于超出故障模型的演示 | 只放在压力层，不计入安全率 | 否 |
| DC7 | 冻结包的开发测试没有带入 | 见 Q4 | 是（Q4） |

## 3. A4 表述的收窄（只改方案文档的口径，本轮不改论文）

前几轮方案和附录中"A4 只有在……时才需要""实际上已由代码比对检查过"这类全局说法，现收窄为如下的**已核对路径上的代码分析**：

> **已核对的路径**：
> - EBRQ-v2 的首次发送由同一 store 中的 authority 签发 grant（`authority.py:134–146`），grant 记录的键等于当时的配置键；
> - 恢复读取同一 store 的 authority 记录；
> - EV7 逐个比较 grant 的键与恢复时的配置键（`evidence.py:194–196`）；
> - R2-A 下，DBC 存在时，还必须恰好对应一个已消费的首次发送 grant（`:214–219`）。
>
> **在这些路径上，代码分析表明**：授权记录完整时，"恢复配置键 ≠ 首次发送 grant 的键"会被判为 anomaly，然后冻结。这一点由 B1a 检验，目前**仍是预测**。
>
> **不能推出的**：
> - 不能推出 R2-A 在所有路径上都不依赖 A4；
> - 不能推出 B1b（记录篡改）的结果说明 A4 在正常故障模型下必不可少。
>
> **没有核对的情形**：
> - 首次发送时配置键本身就错了：grant 和端点收到的都是这个错误的键，此时"原始分派键"本身就不对，A4 的含义要另行定义；
> - authority 记录跨 store 迁移或被重建；
> - 并发恢复。

## 4. 仍需你决定的事项（停在针对性验证的审批点）

| # | 事项 | 我的建议 |
|---|---|---|
| **V0/V1** | 是否批准在主机上执行语法检查和针对性测试 | 批准；只读，只用合成夹具 |
| V2–V4 | 开 VM、在 Linux 上补跑、冻结准备、ACQ 资格运行 | V1 通过之后再逐项审批 |
| Q1 | DC1：异常退出不停批（按方案实现） | 同意 |
| Q2 | DC2：对照单元预期出现 INJECTION_RECEIPT_MISMATCH，记录但不停批 | 同意 |
| Q3 | DC3：截断类压力单元容忍评分器失败 | 同意；只限预先声明的单元 |
| Q4 | 是否移植冻结包中与钩子无关的开发测试（单入口、评分器、分类器不变性） | 建议移植 `test_single_entry`、`test_scorer_v2`、`test_classifier_unchanged`，需要先确认它们不依赖开发框架 |
| Q5 | DN1：scorer_v2 的 fenced_ok 不要求 fence 是最新的，是否需要一个补充诊断 | 本轮只记录；如果需要，放进诊断评分器作为附加项，不改主评分 |
