# bl3 客机解释器崩溃：只读排查

日期：2026-11-13。范围：当前客机启动、Windows 既有事件和 Claude bl1 报告；未改变系统配置、未重启、未运行压力测试。

## 已确认
- bl3 第 402 个单元发送进程在 B1 前 CPython 3.12.3 垃圾回收阶段触发 type_traverse 非 heap type 断言及 Fatal Python error；后续 injection-receipt 缺失是尚未注入的连带现象。触发 G-K，本次启动的资格与正式批次按协议作废。
- bl1 第 57 个单元也发生相似 type_traverse 非 heap type 断言，发生在另一次客机运行；此前客机还记录过内核 Oops。两次关联的底层根因未知。
- 当前启动内核 G-K 关键字未命中；客机日志中 OOM/segfault/hardware error/machine check 查询无匹配。客机当前内存可用约 1.5 GiB、swap 未用、文件系统空余约 9.6 GiB。这些是检查时快照，不代表故障发生瞬间状态。
- 当前 guest /var/crash 有对应 bl3 发送进程崩溃报告，约 3.0 MB，属 ecr-sut/root，普通 guest-admin 无权读取；历史 bl1 也有相应崩溃报告。未读取或复制内容；报告可能包含进程敏感信息。
- 宿主 CPU Intel Core i9-13980HX，物理内存 16 GiB。Windows 最近 45 天有一条 2026-10-09 的 WHEA ID 19：processor core、corrected machine check、internal parity error；近三天无匹配 WHEA 事件。单条历史更正错误不能证明当前 CPU 故障。
- Windows 2026-10-26 的内存诊断结果为未检测到错误；该结果不能证明今天不存在间歇性错误。
- VM 分配 2 vCPU、2 GiB；前次曾在 VMware 25 与 26 的不同运行中出现不稳定。配置是现状，不单独证明原因。当前 vmware.log 由本次进程持有且从当前权限视角长度为 0，未据此判断 VMware 无错误。

## 不确定性和下一步
根因 UNKNOWN：可能涉及宿主 CPU/内存、虚拟化、客机或软件；不能因类似症状断定硬件损坏。可先做无需重启的供应商硬件诊断并检查 BIOS/固件和散热，再根据结果决定是否做内存测试。诊断过程可能高负载；任何重启、压力测试、下载安装、固件或电源设置变更先取得用户对具体动作的授权。保留本次 VM、/var/crash 与批次证据，禁止自动续跑。

## 补充：崩溃报告头与 Python 包核验
用户提供的报告头显示 Signal 6（SIGABRT），与 stderr 的 CPython 致命断言中止一致；此字段不能判定根因。guest 的 python3.12-minimal、libpython3.12-minimal 和 libpython3.12-stdlib 均为 3.12.3-1ubuntu0.17；dpkg --verify 对这三个包退出 0 且未报告差异。该检查仅覆盖 dpkg 可核验文件，不能证明运行时、硬件或内存稳定。首次查询包版本时远端 shell 展开格式变量导致空输出，已改用默认格式确认版本。

## Intel CPU 默认功能测试
2026-11-13 08:25–08:29，本机 Intel Processor Diagnostic Tool 4.5.0 完成默认功能测试，界面 Testing Completed、总体 PASS；测试结果汇总文件 <HOST-PATH> Files\Intel Corporation\Intel Processor Diagnostic Tool 64bit\TESTRESULTS.TXT 的 SHA256 为 CA2EFBC724DEE44DBF62D6832D6A3FA49A71B9A9454A7C4892C207C5CCBBF7A1。CPU load、cache、IMC、prime、floating point、math 等显示 Pass；频率模块写 NO COMPARE OPTION USED。此结果不能排除间歇性硬件、VM、软件故障，也不改变 bl3 失败启动的 G-K 状态。
程序最初从错误工作目录启动未显示窗口，指定安装目录为工作目录后正常运行。

