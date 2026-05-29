# TDT/Shadow 性能优化进度窗口

记录规则：append only。每一步只追加中文结论、证据路径、是否进入默认方案。

## 2026-05-28 阶段性补记

- 主目标：不修改 geth/prysm 等被模拟应用层配置，只在 Shadow/TDT 模拟层内部压缩关键路径，同时保持 TDT 真实客户端 determinism setup 1/4/8 和 6 个合成 CP/restore verifier 可过。
- 当前主矛盾：fast-fsync 之后，setup8 的主要瓶颈不是 fdatasync syscall handler，也不是 packet queue lock，而是 `SyscallConditionWake -> continue_plugin` 中等待 prysm-beacon 原生执行到下一个 syscall 的 native wait；这解释了 setup 扩大后吞吐下降。
- 已采纳优化：`SHADOW_FAST_FILE_SYNC=1`。效果：setup8 steady throughput 从约 22.42 sim-s/wall-s 提升到约 31.31 sim-s/wall-s；`fdatasync(75)` 从 syscall hotspot 消失；完整功能门禁通过。证据：`/tmp/tdt-pfm-fast-final-all/REPORT.md`、`/tmp/tdt-suite-fastfsync1`。
- 已采纳优化：`SHADOW_PACKET_ROUTE_CACHE=1` 和 `shadow_parallelism=6` 作为当前默认模拟层配置。效果：packet route/queue 不是当前主瓶颈，parallelism sweep 显示 setup8 在 6 附近最好，p4=29.24、p6=31.28、p8=30.60、p12=30.56 steady sim-s/wall-s。
- 已证伪候选：native preemption 默认 100 ms/10 ms。结果：setup8 可通过但变慢，steady throughput 约 28.78，低于 fast-fsync 基线约 31.31；top worker body 仍是单 prysm-beacon native run，最大约 304 ms，未压缩主瓶颈。结论：保留为默认关闭的实验配置项，不进入默认方案。证据：`/tmp/tdt-pfm-preempt100b/REPORT.md`。
- 环境噪声记录：第一次 native preemption probe 失败不是语义失败，而是落到了系统 `/usr/sbin/criu`，其不支持 `--unprivileged`。修正方法是在 temp config 显式指定本地 CRIU：`/home/ins0/workspace-for-agent/user_data/task/criu_demo/criu-src/criu/criu`。
- 已证伪候选：关闭 Shadow heartbeat（`heartbeat_interval = null`）。结果：setup8 可通过，但 steady throughput 约 30.62，低于 fast-fsync 基线约 31.31；关闭宿主侧 resource heartbeat 采样没有带来稳定收益。结论：先不进入默认方案。证据：`/tmp/tdt-pfm-noheartbeat1/REPORT.md`。

## 2026-05-28 当前追加

- 候选评估：Shadow `use_preload_openssl_crypto`。代码含义是拦截并跳过部分 OpenSSL AES/EVP 加密，属于 Tor 场景下的近似加速，不是以太坊真实客户端的保守语义优化。`ldd` 显示当前 geth/prysm beacon/validator 不链接 `libssl`/`libcrypto`，因此它既难以加速当前 Go 客户端关键路径，也可能在换客户端时破坏应用语义。结论：不进入测试优先级，不进入默认方案。
- 工程状态：已新增 TDT 配置字段 `native_preemption_*` 和 `shadow_heartbeat_interval`，二者默认保持当前行为或默认关闭；Python 语法检查通过。`python3 -m py_compile` 因只读 pycache 失败，改用内存 `compile()` 验证，没有语法错误。
- 已证伪候选：关闭 Shadow `use_worker_spinning`。结果：setup8 可通过，但 steady throughput 约 30.62，低于 fast-fsync 基线约 31.31；`SyscallConditionWake` 和 `continue_plugin` 主耗时没有下降。结论：worker spinning 不解释当前主瓶颈，默认保持开启。证据：`/tmp/tdt-pfm-nospin1/REPORT.md`。
- 已证伪候选：关闭 Shadow `use_cpu_pinning`。结果：setup8 可通过但显著变慢，steady throughput 约 13.96；`continue_plugin` wall 从 fast-fsync 基线约 18.20s 放大到约 55.96s，说明宿主调度抖动大幅增加。结论：CPU pinning 对当前真实客户端实验很重要，默认必须保持开启。证据：`/tmp/tdt-pfm-nopin1/REPORT.md`。
- subagent lane C 汇总：setup1/4/8 steady throughput 约 `43.53 -> 37.76 -> 31.31`，setup8 local task wall 约 21.34s，其中 `SyscallConditionWake` 约 20.57s；`continue_plugin` 约 18.19s，`receive` 约 12.89s，syscall handler 本身约 1.81s。分类：确认主矛盾。下一步方向应是减少 wake/continue 往返或其等待频率，而不是继续调 packet queue 或 CP/restore。
- 已证伪候选：把 TDT work-root 放到 `/dev/shm` tmpfs。第一次失败是系统 CRIU 环境噪声，显式 `CRIU_BIN` 后 setup8 可通过；steady throughput 约 31.15，基本等同或略低于 fast-fsync 基线约 31.31。结论：宿主 ext4 runtime 写入不是当前 steady 主瓶颈，不进入默认方案。证据：`/tmp/tdt-pfm-shm-work2/REPORT.md`。
- parallelism 细化确认：setup8 p5 steady 约 30.63，p7 steady 约 30.59，均低于 p6 基线约 31.31；结合之前 p4=29.24、p8=30.60、p12=30.56，结论是默认 `shadow_parallelism=6` 仍是当前较优点。证据：`/tmp/tdt-pfm-p5-final/REPORT.md`、`/tmp/tdt-pfm-p7-final/REPORT.md`。
- 正确性门禁复核：完整 suite 第一次运行时 setup1/setup8 和 6 个合成 verifier 通过，但 setup4 determinism 失败，first mismatch 是 beacon `UpdateHead` 时间漂移。随后用同一份 local-suite experiment config 单独重跑 setup4，通过。结论：暂判为一次真实客户端 determinism 偶发漂移，不是当前配置接入的稳定回归；仍需再跑一次完整 suite 拿最终 YES。失败证据：`/tmp/tdt-suite-current-stage1/real-client/setup-4/determinism-setup-4.json`；复跑通过证据：`/tmp/tdt-guard-setup4-localcfg-rerun/determinism-setup-4.json`。
- 当前阶段完整门禁：第二轮完整 suite 通过，最终输出 YES。setup1/4/8 真实客户端 determinism 全过，6 个合成 CP/restore verifier 全过。证据：`/tmp/tdt-suite-current-stage2/suite-result.json` 和各 case log。
- 下一步决策：停止继续做参数 sweep。主线回到代码路径建模：在 `SyscallConditionWake` 内部区分有效 wake（最终 `host_continue`）和无效/spurious wake（重新阻塞、进程/线程不存在、重复 schedule 被跳过）。只有如果无效 wake 占比高，才考虑减少 wake fanout；如果绝大多数 wake 都有效，则优化方向应转向降低真实 `continue_plugin` 往返/receive 等待成本。
- 用户策略补充：如果后续真实客户端 determinism 再出现 10 秒级 UpdateHead/slot/time drift，不在主 session 里临时糊弄过去；必须开一个 subagent 专门深挖最早 divergence、checkpoint/restore 前后时间线、beacon/validator 日志差异和是否与 Shadow 时间接口/CPU/poll wakeup 有关。主 session 只保留全局判断和最终取舍。
- 新增代码路径建模：给 `SysCallCondition` 加了仅在 `SHADOW_TDT_PERF_COUNTERS` 下启用的 wakeup 分支计数，并让 perf_model 报告 `schedule_attempts/scheduled/skipped/trigger_continues/reblocks`。setup8 probe 通过，steady 约 31.27。关键结果：`scheduled_wakeups=228644`，`trigger_enters=228644`，`trigger_continues=228644`，`trigger_reblocks=0`，`skipped_already_scheduled=32151`。结论：几乎所有已调度 wake 都是真正有效地继续进程，不存在大量无效/spurious wake 可删除；下一步应优化真实 `continue_plugin` 往返/receive 等待，而不是减少 wake fanout。证据：`/tmp/tdt-pfm-syscond1/REPORT.md`。
- 新增 `continue_plugin` 细分建模：setup8 probe 显示 `continue_plugin` 总计约 18.58s，其中 `receive` 约 12.56s，`send` 约 5.52s，prepare/lock/time-update 合计只有百毫秒级。结论：Shadow 时间更新不是主成本，IPC channel 发布/等待才是可疑点。已开始实际代码优化：把 `SelfContainedChannel::send` 从 `Empty -> Writing -> Ready` 两次原子状态转换改成在单生产者契约下“写 message 后一次 `Empty -> Ready` release 发布”，减少每次 send 的原子操作。证据：`/tmp/tdt-pfm-contsplit1/REPORT.md`。
- channel fast-send 初步结果：setup8 probe 通过，send wall 从约 5.52s 降到约 5.27s，但 `receive`/native wait 波动更大，steady 只从同等细分计数基线约 29.89 到约 30.02，属于小幅微优化，不是主突破。`cargo test -p vasi-sync --test scchannel-tests` 通过 7/7；`cargo test -p vasi-sync scchannel` 会被无关 `atomic-tls-map-tests` 的 nix `process` feature 配置问题挡住。证据：`/tmp/tdt-pfm-channel-fastsend1/REPORT.md`。
- channel fast-send 后完整门禁：真实客户端 determinism setup1/setup4/setup8 全通过，6 个合成 CP/restore verifier 全通过，最终输出 YES。结论：该微优化没有破坏确定性和 restore 语义，可以保留；但它只压缩 send 侧一小段成本，当前主瓶颈仍是有效 `SyscallConditionWake -> continue_plugin/receive` 的真实 native 边界等待。证据：`/tmp/tdt-suite-channel-fastsend1/suite-result.json`。
- 战略规则固化：后续只要再次遇到真实客户端 10 秒级 determinism 漂移，不把它当作普通偶发噪声直接覆盖；主 session 立即冻结当次失败证据并开 subagent 深挖 earliest divergence，包括 checkpoint 前后 Shadow 时间线、beacon `UpdateHead`/slot 日志、run-control 步进边界、syscall wake/continue 计数和是否存在跨节点先后顺序漂移。只有能解释最早分歧的结果才进入主线决策。

## 2026-05-29 当前追加

- subagent lane A 只读结论：`continue_plugin_receive_wall_ns` 不是纯 channel/futex 成本，而是 Shadow 放行 shim 后，到被模拟进程原生执行并再次进入被拦截 syscall 的整段等待；其中混有 shim 处理、应用 native gap、下一次 syscall handoff 和 IPC wait。分类：确认主矛盾。结论：继续只抠 `SelfContainedChannel` 原子状态机收益有限，下一轮更应拆 shim 侧 native execution gap。
- 已实现微优化：新增 `SelfContainedChannel::receive_assuming_single_consumer()`，只在 Shadow/shim IPC 这种明确单 consumer 通道上使用，保留原 `receive()` 的并发 reader 检测语义不变。该 fast receive 少一次 `Ready -> Reading` 原子状态转换，属于模拟层内部优化，不修改应用层配置。`cargo test -p vasi-sync --test scchannel-tests` 通过 8/8，Shadow `cmake --build build -j 8 --target shadow` 通过。
- fast receive 性能结果：setup8 probe 通过，steady throughput 约 31.29 sim-s/wall-s，和 fast-fsync/p6 可靠基线约 31.31 基本持平；`continue_plugin` 约 18.67s，`receive` 约 12.81s，`send` 约 5.36s。结论：可以保留为低风险微优化，但不是主突破。证据：`/tmp/tdt-pfm-fastrecv1/REPORT.md`。
- fast receive 后完整门禁：真实客户端 determinism setup1/setup4/setup8 全通过，6 个合成 CP/restore verifier 全通过，最终输出 YES。结论：当前功能可用性仍成立。证据：`/tmp/tdt-suite-fastrecv1/suite-result.json`。
- 建模工具增强：`experiments/perf_model/run_perf_model.py` 现在会结构化解析 `syscall_top` 并在后续报告中输出 top syscall 表。用当前 fast receive 日志验证可解析 8 个 top syscall，第一项为 `read`，continue 约 7259.658 ms。结论：下一轮可以直接从报告定位 read/write/epoll/getrandom/futex 等 syscall 的 handler 与 continue 边界贡献，不再手翻长日志。
- 网络检索方向记录：PDES/网络仿真加速文献反复强调 conservative simulation 的关键是 lookahead、同步开销、事件批处理和减少跨 LP 等待；这和当前证据一致，即 TDT 当前瓶颈更像“有效 wake 过多导致频繁 native/Shadow 边界切换”，不是 packet queue lock 或 checkpoint 本身。下一步候选应围绕 syscall handoff 次数、shim-local fast path、native gap 拆分，而不是继续做 parallelism/heartbeat 细调。
- 失败试错：尝试在 shim 热路径用 `clock_gettime(CLOCK_MONOTONIC)` 逐 syscall 记录“返回应用到下一次进入 shim”的 native gap。结果 setup8 probe 在第一次 `continue_for` 后运行超过 3 分钟仍无正常进展，Shadow CPU 约 500%，geth 变成 defunct；该仪表严重污染目标系统或触发异常运行状态。决策：立即停止 probe，并回滚这条 shim-gap 仪表，不进入默认代码。失败证据：`/tmp/tdt-pfm-shimgap1`、`/tmp/tdt-pfm-shimgap1w`。
- shimgap 回滚后门禁：Shadow 重新编译通过；完整本地 suite 再次通过，真实客户端 determinism setup1/setup4/setup8 全过，6 个合成 CP/restore verifier 全过，最终输出 YES。结论：当前工作树功能可用；后续若要拆 native gap，必须使用采样、按 top syscall/host 局部启用、或已有边界时间间接估算，不能全量逐 syscall wall-clock 计时。证据：`/tmp/tdt-suite-post-shimgap-revert1/suite-result.json`。
- syscall handler 小剪枝：`writev_helper` 原来在判断是否 socket 前无条件借用 host RNG 和 network namespace；现在只在 socket 分支里借用。`futex_wake_helper` 在 `num_wakeups == 0` 时先返回，避免无意义地址转换和 futex table borrow；`readv_helper` 延后 memory borrow，避免 socket 零长度 read 也拿进程内存借用。语义：只缩短不必要借用范围，不改变应用层配置和 syscall 返回语义。
- syscall trim 性能结果：setup8 probe 通过，write handler wall 从 fastrecv 约 469.142ms 到约 456.281ms，futex handler 基本持平，steady throughput 约 29.89，整体仍被有效 `continue_plugin`/native 边界主瓶颈和测量噪声主导。结论：保留为低风险局部优化，但不是主突破。证据：`/tmp/tdt-pfm-writeborrow1/REPORT.md`。
- 10 秒级漂移处理：第一次完整 suite `/tmp/tdt-suite-syscall-trim1` 出现 setup4 determinism 失败，earliest mismatch 是多个 beacon 的 `[CORE_CALL] UpdateHead`，reference 比 replay 晚约 10s/1.3s；这类现象按规则交给 subagent 深挖 earliest divergence。主会话单独重跑 setup4 通过，证据：`/tmp/tdt-guard-setup4-syscall-trim-rerun/determinism-setup-4.json`。
- syscall trim 后完整门禁复核：第二轮完整 suite 通过，真实客户端 determinism setup1/setup4/setup8 全过，6 个合成 CP/restore verifier 全过，最终输出 YES。结论：当前工作树功能可用；setup4 漂移保留为高信号偶发问题继续调查，但不构成本轮小剪枝的稳定回归证据。证据：`/tmp/tdt-suite-syscall-trim2/suite-result.json`。
- subagent earliest-divergence 结论：失败不是持续 10s 时钟漂移，而是 replay window 开头多捕获了一条 restore 后第一批事件附近的 `UpdateHead 1779986531000000000`，后续基本重新对齐，窗口尾部相应少一条事件。差异集中在 beacon stdout 和 validator 1/2 stderr，geth 完全一致，beacon stderr 一致，synthetic 全过。结论：与本轮 `uio/futex` 小剪枝的直接因果证据弱；若后续稳定复现，应优先查 checkpoint/event reconstruction 或 window extraction 边界，而不是先回滚小剪枝。

## 2026-05-29 - 漂移触发规则固化

- 决策：后续真实客户端 determinism 如果再次出现 10 秒级偏移，不在主 session 里展开长 trace，而是立即开 subagent 深挖 earliest divergence。主 session 只保留主矛盾判断：该漂移是否威胁真实客户端 checkpoint/restore 确定性。
- 分工：subagent 输出第一条不一致日志、checkpoint/restore 窗口边界、是否持续漂移或只是窗口采样偏移；主 session 决定是否进入实现候选。
- 原因：10 秒级偏移直接触及时间线一致性，是 skeleton 级信号；但长日志对比会污染主上下文，适合委派。

## 2026-05-29 - perf counter lookup 缓存后的完整套件验证

- 操作：运行 `python3 experiments/run_local_suite.py --results-dir /tmp/tdt-suite-perfstat-cache1 --work-root /tmp/tdt-suite-perfstat-cache1w --case-timeout 2400`。
- 结果：YES。真实客户端 determinism setup 1/4/8 全通过；本地合成 CP/restore verifier 6 个场景全通过。
- 证据：`/tmp/tdt-suite-perfstat-cache1/suite-result.json`，日志目录 `/tmp/tdt-suite-perfstat-cache1/logs/`。
- 判断：最近的 managed_thread perf stats lookup 缓存没有破坏功能语义；它最多降低部分测量/统计路径开销，不改变当前主瓶颈判断。

## 2026-05-29 - 今日收尾性能测试与论文 baseline 对比

- 停止继续开新优化线，按用户要求进入收尾：完整 correctness suite、当前性能测试、和论文最早 baseline 做对比。
- 本轮保守优化：在 `managed_thread.rs` 中把 `SHADOW_TDT_PERF_COUNTERS` 是否启用缓存为 `OnceLock<bool>`，避免默认关闭计数时每个 syscall/continue 边界反复查询环境变量。语义：只影响 Shadow 内部统计开关读取，不改变应用层配置、syscall 返回、网络、时间线或 checkpoint 状态。
- 构建：`cmake --build build -j 8 --target shadow` 通过。
- 默认 performance 探针第一次失败原因：手工命令未显式设置 `CRIU_BIN`，Shadow 落到 `/usr/sbin/criu`，该版本不支持 `--unprivileged`；不是本轮代码回归。显式使用本地 CRIU 后 setup8 performance 通过，证据 `/tmp/tdt-performance-envcache2/performance-setup-8.json`。
- 完整门禁：`python3 experiments/run_local_suite.py --results-dir /tmp/tdt-suite-envcache1 --work-root /tmp/tdt-suite-envcache1w --case-timeout 2400` 输出 YES。真实客户端 determinism setup 1/4/8 全过，6 个合成 CP/restore verifier 全过。
- 当前性能测试：`python3 experiments/perf_model/run_perf_model.py --setups 1,4,8 --trials 1 --results-dir /tmp/tdt-pfm-final-20260529 --work-root /tmp/tdt-pfm-final-20260529w --timeout 2400` 通过。steady sim-s/wall-s：setup1=42.09，setup4=38.84，setup8=30.59；checkpoint/restore ms：setup1=147.57/136.77，setup4=105.06/202.60，setup8=184.33/390.48。
- 论文 baseline 来源：`deps/shadow/Writing/new/3-Evaluation.tex` 的 efficiency 表，3600s steady workload 下 setup1/4/8 wall time 分别为 88.57s、104.23s、136.64s，对应 speedup 40.7x、34.5x、26.3x。
- 归一化对比：用当前 steady speedup 折算 3600s workload，setup1 约 85.53s（比 baseline 快 3.04s / 3.4%），setup4 约 92.69s（快 11.54s / 11.1%），setup8 约 117.69s（快 18.95s / 13.9%）。当前 setup8 speedup 30.59x，相比论文 baseline 26.3x 提升约 16.3%。
- 注意：当前 perf_model 是短窗口并开启 TDT perf counters 的建模口径，论文 baseline 是 3600s steady workload；因此该对比是同一 speedup 含义下的归一化近似，偏保守，不应写成严格 apples-to-apples 复现实验。
- subagent 收尾结论：lane A 认为安全候选主要剩 `getrandom` 确定性 shim fast path（但应用可见随机流和 checkpoint 状态风险中等）、futex blocked re-entry 小优化、stdio/log write 窄路径缓冲、epoll ready path 小优化；拒绝 native randomness、全局异步普通文件 write、把 read/epoll/futex 放到 shim/native。lane B 认为 event batching/runahead 低风险空间很小，不能提前投递 packet、不能任意增大 runahead、不能并行同 host 事件；packet queue/event lock/继续加 worker 不再是主线。

## 2026-05-29 - 用户要求暂停 goal

- 决策：按用户要求停止继续推进性能优化 goal，不再开新优化线、不再跑新实验。
- 收尾状态：上一轮完整 suite 通过，证据 `/tmp/tdt-suite-envcache1/suite-result.json`；上一轮性能模型通过，证据 `/tmp/tdt-pfm-final-20260529/REPORT.md`。
- 中断点：本轮刚开始检查 host-level 长尾模型，发现 Shadow 日志已输出 `top_hosts` 和 `top_worker_bodies`，但 TDT `experiments/perf_model/run_perf_model.py` 还没有解析/展示；尚未修改文件。若之后恢复，这是低风险的下一步证据层工作。
- subagent：已关闭 lane A 和 lane B；它们的结论已经写入上一条收尾记录。

## 2026-05-29 - 恢复 goal 后 host-level 长尾证据层

- 决策：恢复执行后不继续调参，先补齐能解释 setup8 吞吐下降的 host-level 证据层。
- 修改：`experiments/perf_model/run_perf_model.py` 现在解析 Shadow 已经输出的 `top_hosts` 和 `top_worker_bodies`，并在 `REPORT.md` 中新增 `Scheduler Top Hosts` 与 `Scheduler Slowest Worker Bodies` 两个表。该改动只影响 TDT 报告解析，不改变 Shadow 执行语义和应用层配置。
- 离线验证：用 `/tmp/tdt-pfm-final-20260529w/checkpoint-study/performance-setup-8-trial-1/performance.log` 验证解析成功，能提取 top host 和 slowest worker body。
- 性能探针：运行 `python3 experiments/perf_model/run_perf_model.py --setups 8 --trials 1 --results-dir /tmp/tdt-pfm-tophosts1 --work-root /tmp/tdt-pfm-tophosts1w --timeout 2400`，结果 passed=true。报告证据：`/tmp/tdt-pfm-tophosts1/REPORT.md`。
- 关键观察：本次 setup8 受宿主噪声影响较慢，steady 约 11.43 sim-s/wall-s；但结构信号很强。top hosts 全是 `prysm-beacon-*`，前五名 host wall 约 5.08-5.52s，其中 syscall wake 占 96.5%-97.0%。最慢 worker body 约 1.09-1.24s，也由单个 beacon host 的 syscall wake 主导。
- 判断：这确认当前主瓶颈不是 event queue lock、packet route、checkpoint/restore、继续加 worker 或普通 scheduler batching；setup8 的 wall-clock 长尾来自 beacon 进程在有效 syscall wake/continue 边界上的原生执行与再次陷入 syscall。下一步若继续优化，应优先考虑语义保守的 beacon syscall 边界减法：例如 futex blocked re-entry 小优化、epoll ready path 小优化、或严格可恢复的 deterministic getrandom shim fast path；但 getrandom 涉及应用可见随机流，必须先设计 checkpoint/restore 状态语义，不能直接改用 native randomness。

## 2026-05-29 - futex blocked re-entry 候选开始

- 决策：恢复 goal 后的下一条实现候选选择 `futex_wait_helper` blocked re-entry 小剪枝，而不是继续 parallelism/heartbeat/worker 参数调节。原因：当前证据显示主成本来自 beacon 的有效 syscall wake/continue 边界；futex blocked re-entry 位于该边界内，且可以在不改变应用层配置的前提下减少重复用户内存读取。
- 修改：在 Shadow `src/main/host/syscall/handler/futex.rs` 中，首次 FUTEX_WAIT 仍读取 timeout、读取 futex word 并执行 compare-and-block；但 syscall 已经 blocked 后再次进入 handler 时，不再重新读取 timeout pointer 和 futex word，只根据已有 SyscallCondition 的 timeout 状态、signal 状态和 futex listener cleanup 返回 `ETIMEDOUT`、`EINTR` 或 `0`。
- 语义判断：Linux futex wait 的用户值比较发生在实际阻塞前；已经阻塞后被唤醒/超时/信号打断，不应把用户内存的后续变化重新解释成新的 `EAGAIN`。因此该改动更接近 compare-and-block 语义，同时减少 repeated re-entry 的 handler work。旁路 subagent 正在只读审查 checkpoint/restore 风险。
- 构建结果：`cmake --build build -j 8 --target shadow` 通过。当前只看到既有 tcp const qualifier、unreachable pattern、FFI-safe warning；没有由 futex 小剪枝引入的新构建错误。
- subagent 只读审查结论：分类为 `safe`。理由是 `FUTEX_WAIT` 的用户 futex word compare 属于阻塞前的一次性 compare-and-block；阻塞后被 wake/timeout/signal 重新调度回来时，不应重新读取用户 futex word，也不应重新读取 timeout pointer。checkpoint/restore 侧 blocked futex condition 依赖 blocked futex address、absolute timeout 和 listener sequence 重建，不依赖 blocked re-entry 重读这些用户参数。
- setup8 初次性能探针：`/tmp/tdt-pfm-futex-reentry1/REPORT.md` 结果 passed=true，但机器噪声明显，steady 只有约 16.52，checkpoint/restore 也异常放大到约 748.97/1416.41ms。该结果只能说明本轮改动没有立刻破坏功能；不能据此判断性能收益。结构上 futex handler wall 从之前 setup8 约 415.618ms 降到约 302.916ms，但整体被宿主噪声和 continue 边界等待淹没，需要再验证。

## 2026-05-29 - runahead / 网络延迟窗口方向

- 用户提示：可以关注 runahead 并行度，模拟层网络延迟越大，调度窗口越大，可能改善并行度。主会话判断：这是高价值建模方向，但“直接调大网络延迟”会改变网络语义，不能作为默认优化；可以作为敏感性实验，用来判断当前瓶颈是否受 runahead/window 限制。
- 现状阅读：TDT 生成的 Shadow graph 是单网络节点自环，默认 `latency "100 ms"`、`host_bandwidth 1000 Gbit`；Shadow runahead 默认取 `max(min_possible_latency, experimental.runahead)`，而默认 `experimental.runahead` lower bound 是 1ms，因此 TDT 当前实际窗口主要由 100ms latency 决定。setup8 性能报告约 2956 个 scheduling windows，和 100ms 级窗口一致。
- 初步判断：当前 `scheduler_scope_over_window_max_percent` 约 99%，说明每个窗口 wall time 基本由最慢 worker body 决定；`worker_busy_percent` 约 39%，说明不是单纯窗口太小，而是窗口内 beacon host 长尾 syscall wake/continue 不均衡。增大 latency 可能减少窗口数并提升表观吞吐，但也会延后 packet delivery，属于语义 tradeoff，不是保守优化。
- 修改：给 TDT harness 增加默认不变的 `simulation.network_latency = "100 ms"`，由 `TDT_NETWORK_LATENCY` 传给 `generate_shadow_yaml.py`。默认值保持 100ms，不改变现有实验；该入口只用于敏感性实验和报告复现，不作为性能优化默认方案。
- 200ms 敏感性实验：`TDT_NETWORK_LATENCY="200 ms"` setup8 通过，证据 `/tmp/tdt-pfm-lat200-1/REPORT.md`，生成的 shadow.yaml 确认为 `latency "200 ms"`。结果：windows 从 100ms 基线约 2956 降到 1481，但 steady 只有约 19.37（低于当前 noisy futex run 和可靠 100ms 基线），worker busy 只到约 41.03%，`Scope/max-body %` 仍约 99.65%，top hosts 仍几乎全是 beacon syscall wake。
- 结论：增大网络延迟确实增大调度窗口、减少窗口数，但没有解决窗口内最慢 beacon host 的 native/syscall wake 长尾；在当前 TDT 真实客户端 workload 下，runahead 不是主优化矛盾。该方向保留为论文 tradeoff/sensitivity 证据，不进入默认优化。

## 2026-05-29 - futex 语义回归测试

- 决策：给 futex blocked re-entry 小剪枝补一个窄语义测试，避免未来把“wake 后重新读取 futex word 并返回 EAGAIN”的旧行为当成正确行为。该测试属于 Shadow 自身 syscall 语义，不修改 TDT 应用层配置。
- 修改：在 `deps/shadow/src/test/futex/test_futex.c` 新增 `/futex/wait_wake_after_value_change`。测试让 waiter 以 `UNAVAILABLE` 入队等待，parent 在确认 waiter 可能已等待后把 futex word 改成 `AVAILABLE` 并 `FUTEX_WAKE`；只有当 wake 返回 1，说明确实唤醒了已入队 waiter，此时 waiter 必须返回 0，而不能因为 futex word 已改变而返回 `EAGAIN`。若竞态导致 waiter 未入队并返回初始 `EAGAIN`，测试 bounded retry。
- 验证：当前 release build tree 是 `SHADOW_TEST=OFF`，没有 `test-futex`/ctest target；未重配主 build tree。改用临时 `/tmp/test-futex` 编译该 C 测试，`/tmp/test-futex -p /futex/wait_wake_after_value_change` 通过。该 native 测试验证 Linux 语义和测试自身稳定性；Shadow 路径由后续 TDT setup8 和完整 suite 覆盖。
- 默认 setup8 复测：`/tmp/tdt-pfm-futex-reentry2/REPORT.md` passed=true，生成的 shadow.yaml 确认为默认 `latency "100 ms"`。但宿主仍然很噪，steady 约 17.11；因此不能用本轮 wall-clock 做强性能结论。结构信号仍一致：futex handler wall 约 284.990ms，低于可靠 100ms 基线中的约 415.618ms；不过总耗时仍由 `continue_plugin`/receive 和 beacon syscall wake 长尾主导。

## 2026-05-29 - futex/latency 改动后的完整 suite 首轮失败

- 结果：`python3 experiments/run_local_suite.py --results-dir /tmp/tdt-suite-futex-reentry1 --work-root /tmp/tdt-suite-futex-reentry1w --case-timeout 2400` 输出 NO。通过：real-client setup4、synthetic multihost full/tcp、eth-poc、eth-multiproc、eth-shadowyaml bootstrap。失败：real-client setup1、real-client setup8、synthetic eth-shadowyaml stable。
- setup1 失败类型：不是 determinism mismatch，而是在等待 Shadow pause 时 timeout；last status 显示 `sim_waiting=false`，`auto_run_until=Some(...)`，说明控制侧没有在预期时间内等到 pause。这和本轮宿主高负载/噪声相关性强，但仍不能忽略。
- setup8 失败类型：明确 10 秒级漂移，`determinism-setup-8.json` 第一条 mismatch 是所有 beacon stdout 的 `[CORE_CALL] UpdateHead`，replay 第一行约 `1780034746000000000`，reference 多数约 `1780034756000000000`；geth 与 validators 完全一致，beacon stderr 也一致。按规则已开 subagent 专门深挖 earliest divergence，主会话不在长 diff 里展开。
- synthetic eth-shadowyaml stable 失败类型：Shadow logger panic，`main/core/logger/shadow_logger.rs:341` 对 `logger_elapsed_micros()` 做 `u64::try_from(...).unwrap()` 时遇到负值，随后 orchestrator socket closed。该失败和 real-client UpdateHead drift 是不同失败类，需要单独定位；在解释清楚前不能把完整 suite 视为通过。
- 修复 synthetic logger panic：`shadow_logger.rs` 现在对 `logger_elapsed_micros()` 的负值做 saturating-to-zero，再构造 `Duration::from_micros`。这只影响 Shadow 日志记录的 wall_time 字段，避免日志计时异常杀死模拟，不改变应用可见网络、时间、syscall 或 checkpoint/restore 状态。
- 构建结果：logger panic 修复后 `cmake --build build -j 8 --target shadow` 通过；仍只有既有 warning。
- synthetic stable 复核：第一次手工复跑失败是命令没有设置 `CRIU_BIN`，Shadow 按设计拒绝 checkpoint，不是代码回归。带 `CRIU_BIN=/home/ins0/workspace-for-agent/user_data/task/criu_demo/criu-src/criu/criu` 后，`synthetic checkpoint-network-eth-shadowyaml --scenario stable` 通过。证据：`/tmp/tdt-synth-stable-loggerfix2`。
- setup8 漂移 subagent 结论：分类为 `likely window extraction boundary`，不是持续 10s 时间线漂移。replay 窗口开头多捕获一条 `UpdateHead 1780034746000000000`，reference 窗口尾部多一条 `1780034866000000000`，中间序列对齐；geth、validators、beacon stderr 都一致。该结果和之前 setup4 偶发漂移同类，更像窗口采样边界问题，不支持主 checkpoint/restore 语义损坏。
- 本地配置修正：`tdt_config.local.toml` 中 `geth` 路径原来写成不存在的 `deps/go-ethereum/build/bin/gethq`，导致手工 `run_study.py` 直接失败；已改回存在的 `deps/go-ethereum/build/bin/geth`。这是测试入口修复，不改变模拟应用参数。
- setup1 单独复跑：修正本地 geth 路径后，`/tmp/tdt-guard-setup1-futex-rerun3/determinism-setup-1.json` passed=true。判断：完整 suite 中 setup1 的 pause timeout 不是稳定语义回归，更像宿主负载/控制等待异常。
- setup8 单独复跑：`/tmp/tdt-guard-setup8-futex-rerun1/determinism-setup-8.json` passed=true。结合 subagent 对失败样本的 diff 判断，完整 suite 中 setup8 的 10 秒级 mismatch 暂归类为偶发 window extraction boundary，而不是稳定 restore 语义回归。

## 2026-05-29 - futex blocked re-entry 候选回滚

- 决策：第二轮完整 suite 仍出现 setup1 post-restore `continue_for(120s)` 等待 pause timeout，虽然 setup4/setup8 和 6 个 synthetic verifier 通过，但该候选还没有证明自己比当前主目标更重要。按主矛盾优先，真实客户端可靠性优先于小幅 syscall handler 剪枝。
- 操作：回滚 `futex_wait_helper` blocked re-entry 的语义变更，恢复已阻塞路径上重新读取 timeout/futex word 的旧逻辑；同时删除为该候选新增的 `/futex/wait_wake_after_value_change` 测试。保留独立的 `FUTEX_WAKE num_wakeups == 0` 早返回，因为它不触碰 wait/replay 语义。
- 判断：这条优化线暂时归档为“语义上可能正确但验证收益不足、suite 风险过高”。后续除非有更强证据证明 setup1 timeout 与该候选无关，否则不继续在主线推进它。
- 验证：回滚后重建 Shadow 通过；运行 `python3 experiments/run_local_suite.py --results-dir /tmp/tdt-suite-post-futex-revert1 --work-root /tmp/tdt-suite-post-futex-revert1w --case-timeout 2400` 输出 YES。真实客户端 determinism setup 1/4/8 全过，6 个 synthetic CP/restore verifier 全过。
- 结论：当前 correctness baseline 已恢复。接下来性能主线不再围绕 futex re-entry，而是继续定位 setup8 窗口内 `prysm-beacon-*` syscall wake/native execution 长尾。

## 2026-05-29 - epoll ready trace-only 元数据剪枝

- 决策：下一步选择 `epoll` ready-path 的 trace-only 元数据延迟构造，而不是 getrandom shim fast path。原因：setup8 中 `epoll_pwait` 高频，handler 固定成本可局部减少；而 getrandom 会触碰应用可见随机流，除非先设计可 checkpoint/restore 的 RNG 状态，否则不进入实现。
- 修改：`deps/shadow/src/main/host/descriptor/epoll/mod.rs` 中 `collect_ready_events_with_trace(None)` 不再构造 `watched_fd`、`canonical_handle`、`interest_bits`、`data`、`file_kind/local/peer addr` 等 restore-order trace 字段；只有 trace 真的启用时才构造这些字段。
- 语义判断：默认路径不启用 restore-order trace，该改动不改变 ready heap、公平性、返回事件顺序、应用可见 epoll events 或 checkpoint 状态；只减少默认性能路径中的不必要元数据读取和 socket addr 描述。
- 初步验证：重建 Shadow 通过；setup8 probe `/tmp/tdt-pfm-epoll-tracecut1/REPORT.md` passed=true。对比回滚基线 `/tmp/tdt-pfm-post-futex-revert1/REPORT.md`，`epoll_pwait` handler 从约 178.220ms 降到约 154.221ms，handler calls 基本相同，说明剪枝命中固定成本。整体 steady 从约 17.30 到 17.90，只作为弱信号，不能单独作为收益结论。
- 完整 suite 首轮：`/tmp/tdt-suite-epoll-tracecut1/suite-result.json` 输出 NO，但只有 setup1 在 post-restore `continue_for(120s)` 等待 pause timeout；setup4/setup8 和 6 个 synthetic verifier 全过。失败 last status 显示 `auto_run_until=Some(...)` 且 sim_time 停在 restore epoch，和之前出现过的偶发控制等待类一致。
- setup1 单独复跑：使用同一 suite config `/tmp/tdt-suite-epoll-tracecut1/local-suite-experiment.toml` 运行 `/tmp/tdt-guard-setup1-epoll-rerun2`，结果 passed=true。判断：目前没有证据表明 epoll 剪枝造成稳定 determinism 回归；需要再跑一次完整 suite 固化 YES。
- 完整 suite 第二轮：`python3 experiments/run_local_suite.py --results-dir /tmp/tdt-suite-epoll-tracecut2 --work-root /tmp/tdt-suite-epoll-tracecut2w --case-timeout 2400` 输出 YES。真实客户端 determinism setup 1/4/8 全过，6 个 synthetic CP/restore verifier 全过。
- 结论：保留 epoll trace-only 剪枝。收益是小而结构清楚的 handler 固定成本下降；它不会解决 setup8 主长尾，但属于正确性门禁已通过的模拟层内部优化。

## 2026-05-29 - epoll descriptor table 短借用剪枝

- 决策：继续做 epoll wait 高频路径的小剪枝，但只缩短 descriptor table borrow，不合并 ready check 与 collect 的 mutable borrow。原因：`CallbackQueue::queue_and_run` 会在闭包返回后运行回调，延长 epoll mutable borrow 可能引入重入风险；缩短 descriptor table borrow 更保守。
- 修改：`epoll_wait_helper` 现在从 descriptor table 中取到 `File::Epoll(Arc<...>)` 后 clone `Arc` 并显式 `drop(desc_table)`，后续 ready/timeout/signal 分支不再持有 descriptor table borrow。
- 语义判断：不改变 fd 查找、epoll ready 状态、返回事件顺序或错误顺序；只是缩短高频 syscall handler 内部借用寿命。
- 结果：setup8 probe `/tmp/tdt-pfm-epoll-borrowcut1/REPORT.md` passed=true，但 `epoll_pwait` handler 基本不变：154.221ms -> 154.256ms。该改动还会给每次 epoll wait 增加 `Arc` clone，收益不足。
- 决策：回滚 descriptor table 短借用剪枝，只保留已通过完整 suite 且有效的 trace-only 元数据剪枝。该分支记录为一次负结果，不继续推进。

## 2026-05-29 - runahead/PDES 文献检索后的主线修正

- 检索：查看 Shadow 设计文档、NASA NTRS 的 conservative PDES 同步成本分析、IBM Research 的 null-message PDES 性能模型和 PDES 同步综述。
- 结论：文献和当前数据一致。Lookahead/window 能影响并行度，但性能上界还受 event processing delay、通信拓扑中的瓶颈 cycle、同步/事件队列开销和处理器 idle time 共同限制。TDT setup8 当前 `Scope/max-body` 约 99%，top worker body 基本由单个 beacon host 的 syscall wake/native execution 占满，说明主矛盾不是窗口太小，而是窗口内最慢 LP/host 的处理时间。
- 决策：不继续把 `network_latency` 或 `experimental.runahead` 作为默认优化旋钮；它们会改变 packet delivery/时间线语义。后续继续做模拟层内部建模：先把 `read/write` 热 syscall 的 fd/file-kind 分布建出来，判断 `write(1)` handler 高成本到底来自 stdout regular-file 日志、socket 路径还是 legacy fallback，再选择实现候选。

## 2026-05-29 - read/write fd 分布建模

- 决策：在优化 `write(1)` 之前，先补 fd 维度证据。原因：当前 `Managed Thread Top Syscalls` 只告诉我们 Linux syscall number 是 `write(1)`，不告诉我们应用写的是 stdout/stderr、socket 还是其它 fd；直接做 stdout buffering 或 socket 快路都会有语义风险。
- 修改：`deps/shadow/src/main/host/managed_thread.rs` 在 `SHADOW_TDT_PERF_COUNTERS=1` 时，对首参是 fd 的 syscall 记录 `(syscall_nr, fd)` top counts；`experiments/perf_model/run_perf_model.py` 解析 `fd_top` 并在 `Managed Thread Top Syscalls` 表中展示 `Top fds`。默认 TDT 和 Shadow 正常运行不启用该统计，不改变模拟语义或应用配置。
- setup8 probe：`/tmp/tdt-pfm-fdtop1/REPORT.md` passed=true。结果显示 `write(1)` 热点不是 stdout/stderr fd=1/2，而是 fd 14=21867、15=16659、16=15535、13=4845；`read(0)` 也是 fd 16/15/14/8 等高编号 fd；`epoll_pwait` 集中在 fd 3/4。
- 判断：全局 stdout buffering/regular-file write 不是当前主线。下一步要把这些 fd 映射到 descriptor kind，确认是 socket、epoll 还是 legacy file，再选择 socket send/recv 或 descriptor 层候选。
- descriptor kind probe：`/tmp/tdt-pfm-fdkind1/REPORT.md` passed=true。`write(1)` kind top 为 socket=74303、legacy-file=6930、pipe=122；`read(0)` kind top 为 socket=47828、legacy-file=1027、pipe=336、eventfd=1；`epoll_pwait` 全是 epoll。
- 结论：读写热点主要是 socket，而不是 stdout/stderr 日志。后续实现候选应看 socket send/recv handler 固定成本或 socket syscall 边界；stdout buffering、regular-file async write、host-side log 策略不是当前主矛盾。

## 2026-05-29 - runahead 并行度复核

- 用户提示：再次关注 runahead 并行度，模拟层网络延迟越大，调度窗口越大，可能改善并行度。主会话复核后继续把它作为“建模证据”，但不升级为默认优化旋钮。
- 当前配置：TDT 默认 `simulation.network_latency = "100 ms"`；`generate_shadow_yaml.py` 将它写入 Shadow graph 的 `latency "100 ms"`。Shadow 默认 `experimental.runahead = 1 ms`，但 `Runahead::get()` 实际取 `max(min_possible_latency, min_runahead_config)`，因此当前 TDT 的有效固定窗口主要由 100ms 网络延迟决定。
- 200ms 敏感性结果：`/tmp/tdt-pfm-lat200-1/REPORT.md` passed=true，windows 从 100ms 基线约 2956 降到 1481，说明延迟窗口确实能放大调度窗口；但 `Scope/max-body` 仍约 99.65%，slowest worker body 仍由单个 `prysm-beacon-*` 的 syscall wake/native execution 主导，worker busy 只约 41.03%。
- 判断：继续调大 `network_latency` 会改变应用可见 packet delivery 时间线，`experimental.runahead` 强行高于真实最小延迟也可能让包跨窗口延迟到超过 intended latency；这两者都不能作为保守默认优化。当前主矛盾仍是窗口内最慢 beacon host 的 socket/read/write/epoll/syscall 边界成本，而不是窗口尺寸不足。
- 后续：已开一个只读 subagent 复核是否存在“不改变 packet latency 但提高有效并行度”的 runahead/scheduler 入口。除非它给出这样的入口，否则实现主线继续沿 socket send/recv handler 固定成本建模推进。
- subagent 复核：结论归类为 `confirms main contradiction`。它独立确认当前有效 runahead 为 `max(100ms, 1ms)=100ms`；`network_latency` 会直接改变 packet delivery 语义；`experimental.runahead > 100ms` 会把包延后到超过 intended latency；最近报告中 100ms/200ms 都显示 `Scope/max-body` 接近 99%，top host 仍是 beacon syscall wake 长尾。
- 集成决策：不在默认 TDT 配置中增加 latency/runahead，也不继续做延迟调参。保留 `TDT_NETWORK_LATENCY` 入口只用于 sensitivity 和论文讨论；主线进入 socket/read/write 路径建模。

## 2026-05-29 - fd/kind instrumentation 后的 suite 固化

- 首轮完整 suite：`/tmp/tdt-suite-fdkind1/suite-result.json` 输出 NO。setup4/setup8 真实客户端 determinism 通过，6 个 synthetic CP/restore verifier 通过；唯一失败是 setup1。
- setup1 失败类型：不是 determinism mismatch，而是 `continue_for(120s)` 等待 Shadow pause timeout。last status 显示 `sim_waiting=false`、`auto_run_until=Some(...)`，属于此前多次见过的控制等待/宿主噪声类失败。
- setup1 单独复跑：使用同一 suite config 运行 `/tmp/tdt-guard-setup1-fdkind-rerun1`，结果 passed=true。
- 判断：当前没有证据显示 fd/kind perf-only instrumentation 改变 CP/restore 语义；但为了得到一个干净 YES，继续跑第二轮完整 suite 固化结果。如果第二轮仍只出现 setup1 pause timeout，就转向 runner wait/control 侧，而不是继续做性能调参。
- 第二轮完整 suite：`/tmp/tdt-suite-fdkind2/suite-result.json` 输出 YES。真实客户端 determinism setup1/setup4/setup8 全部 passed=true；synthetic multihost full/tcp、eth-poc、eth-multiproc、eth-shadowyaml stable/bootstrap 全部通过。
- 真实客户端 deterministic replay：setup1 比较 3 个 host、setup4 比较 9 个 host、setup8 比较 17 个 host，全部 mismatches=0。setup8 checkpoint/restore wall time 约 207.52ms / 427.97ms。
- setup8 共识语义抽查：8 个 beacon 都持续同步新区块并推进到 slot 24；geth 持续 import/head update，execution block/head 到 number=19；validators 提交 sync messages=688、sync contributions=438、attestations=20、aggregate attestations=20、blocks=19。说明不是“只活着”，而是在产生和传播共识相关消息。
- 日志噪声分类：setup8 中存在 EL follow-distance、重复 payload、`Nil finalized block cannot evict old blobs`、sync committee gRPC deadline 等 error/warn，但这些没有阻止 head progression、block import 或 replay determinism。`finalizedEpoch=0` 是因为该 determinism window 只推进到 slot 24，还没跨过一个完整 epoch，不能用本轮短测试判断 finality。
- 结论：当前一键 suite 和真实客户端共识语义都可接受。下一步性能优化继续只从模拟层内部的 socket/read/write/epoll/syscall 边界找候选，不再探索 runahead/latency 调参。

## 2026-05-29 - socket/read/write 局部探索收束并转入稳定性验证

- 局部探索：`write(1)`/`read(0)` 热点主要是 socket，路径会经过 `uio.rs` 的 `writev_helper/readv_helper`，再进入 `Socket::sendmsg/recvmsg`、`InetSocket::{LegacyTcp,Tcp,Udp}` 分派，最后到 legacy TCP C path 或 Rust TCP/UDP path。
- 当前判断：已有 fd/kind 统计还不足以安全区分 TCP/UDP/LegacyTcp、payload size、block/retry 与 native continue 成本，因此不直接改 socket 语义或 buffer 逻辑。盲目优化 TCP send/recv、减少 copy、改 active_file/block 行为都有 CP/restore 或应用可见语义风险。
- 决策：先不做 socket 代码改动。用户要求优先确认 TDT 框架稳定可用，因此下一阶段连续多轮运行完整 suite，专门捕获“单个测试偶发不过”的类型和原因。
- 目标：如果失败是 pause timeout/control socket 等待类，定位 runner/control 稳定性；如果是 10 秒级 determinism mismatch，按既定规则开 subagent 深挖 earliest divergence；如果是 synthetic 回归，单独分类，不和真实客户端主线混淆。

## 2026-05-29 - socket/read/write subagent 结果归类

- Hubble 只读分析确认：socket read/write 的 handler 固定成本存在，但 setup8 报告中更大的墙钟成本来自 `continue_plugin` 往返；该路径不能通过批量合并或绕过来优化，否则会改变 syscall、信号、时间推进和 checkpoint/restore 语义。
- 当前可接受候选收敛到观测层拆分、trace disabled 时跳过 LegacyTcp restore-drain 元数据查询、以及后续更细粒度建模。
- 决策：性能优化线暂缓，主矛盾切换为 TDT suite 多轮稳定性；只有修复或解释偶发单测失败后，才继续评估低风险性能候选。

## 2026-05-29 - flaky audit setup8 初步归类

- 第一轮多跑 suite `/tmp/tdt-flaky-audit-1` 中 setup8 determinism 失败，但 8 个 mismatch 全部只发生在 beacon stdout；geth、validator、beacon stderr 均一致。
- Subagent Aristotle 只读检查确认：8 个 beacon 的中间序列完全对齐，表现为 replay 窗口头部多一条 `[CORE_CALL] UpdateHead 1780039285000000000`，尾部少一条 `1780039405000000000`，即 `replay[1:] == reference[:-1]`。
- 决策：setup8 暂归类为 comparison/window slicing boundary artifact，而不是已证明的 restore 语义发散；继续保留为次级关注。当前更硬的问题是 setup1 restore 时 Shadow 控制 socket 断开/退出。

## 2026-05-29 - flaky audit 第二轮 setup1 主失败归类

- 第二轮完整 suite `/tmp/tdt-flaky-audit-2`：setup4/setup8 和 6 个 synthetic 全通过，唯一失败为 setup1 real-client determinism，耗时 311.99s 后退出。
- setup1 失败不是 mismatch，而是 post-restore `continue_for(120s)` 后 `wait_until_paused` 超时；最后 status 为 `sim_time_ns=833354774000000000`、`auto_run_until=Some(833354894000000000)`、`sim_waiting=false`，说明 run-control 目标已设置但仿真不推进。
- 关键线索：restore replay 后 host checkpoint 日志中的 `next_event` 为 `EmulatedTime(1780039573...)`，和 status/auto_run_until 的 `833354...` sim-time 坐标不在同一量级。主假设切换为：setup1 post-restore 事件时间坐标或事件队列重建存在偶发/条件性不一致。
- 决策：暂不继续性能优化，先用 setup1 单 case 和通过 case 对比验证该时间坐标假设。

## 2026-05-29 - setup1 失败假设收敛

- Subagent Pasteur 确认：restore replay 日志里的 `EmulatedTime(1780...)` 在通过的 setup4/setup8 中也存在，因此它不是 setup1 独有的错误坐标证据。
- 更强的主假设：setup1 在 restore 后接受 `continue_for`，但第一个 post-restore 执行窗口没有正常返回到 run-control 边界；`continue_for` 本身只是设置 `auto_run_until`，不会证明仿真已经推进。
- 决策：先做 falsifying experiment：setup1 restore 后执行 `info` 和 `step_one_window`。若 `step_one_window` 也卡住，则主矛盾转为第一 post-restore window 的 host/event 执行；若能返回，则再回到 run-control deadline/boundary 处理。

## 2026-05-29 - setup1 step_one_window falsifying experiment

- 单 case 探针 `/tmp/tdt-step-probe-1w/determinism-setup-1/step-probe.log`：setup1 restore 后 `info` 正常返回，显示三个 host 的下一窗口都在 checkpoint 时刻附近。
- 随后 `step_one_window` 在约 57.5ms 内返回，sim_time 从 `833355446000000000` 推进到 `833355446100000000`。
- 结论：第一 post-restore execution window 本身没有卡死；第二轮 suite 的 setup1 失败更可能是 `continue_for(120s)` 的自动 deadline 没有形成可观察窗口边界。
- 决策：实现模拟层 run-control 修复：将 auto deadline 暴露给 manager，并在调度窗口跨过 deadline 时截断 window_end，使无事件 deadline 也能触发 pause。

## 2026-05-29 continue 审计方向校正

- 决策：确认 step_one_window 只保留为一次诊断证据，不作为 continue_for 替代方案。
- 处理：撤回此前未命中的 run-control deadline/window 截断补丁残留，避免污染后续审计。
- 下一步：集中审计 socket run-control 的 continue_for、wait_until_paused、restore/restart 后 SharedState 生命周期，定位为什么 setup1 restore 后 continue_for 接收成功但模拟不再到达暂停边界。

## 2026-05-29 setup1 continue_for 卡住审计进展

- 决策修正：不再考虑用 step_one_window 替代 continue_for；step_one_window 只作为一次诊断探针，不进入方案。
- 已排除：control socket 的 continue_for 状态机不是直接根因。trace 显示 restore 后 continue_for 被接收，paused boundary 被唤醒，auto_run_until 正确设置，主线程进入下一轮 scheduler window。
- 当前主矛盾：setup1 在 restore 后第一段 replay 的某个 scheduler/Host::execute 路径不返回，导致主线程等不到 scheduler returned，也就无法进入下一次 run-control boundary。
- 证据：/tmp/tdt-continue-audit-setup1w、/tmp/tdt-continue-audit2-setup1w、/tmp/tdt-continue-audit3-setup1w 的 determinism.log 中，post-restore continue_for 后可见 manager window start，但没有后续 scheduler returned；宿主 ps -L 显示主线程 futex 等待，两个 shadow-worker 持续占 CPU。
- 进一步定位：Host::execute 事件级 trace 显示 RestoreBlockedSyscallCondition 初始重放可以完成；post-restore 第一轮 replay 后，忙 worker 出现在 prysm-validator/geth/beacon 的 TimerExpire、SyscallConditionWake、LegacyTcpDeferredAction、RelayForward 等事件序列中。上一轮采样只看前 32 个事件，可能漏掉真正不返回的 event，因此已提高到前 512 个事件采样。
- 下一步：等 /tmp/tdt-continue-audit4-setup1w 的高密度 trace 输出，定位最后一个没有 complete 的 event/task，再只针对该语义路径修复；不做 runahead/延迟调参，不改应用层配置。

## 2026-05-29 进度记录位置修正

- 收到反馈：后续进度不能只打印在对话/终端输出中，必须写入这个 append-only 进度窗口。
- 当前主问题：不使用 gstep_one_window 替代 continue；继续审计 restore 后 continue_for 进入 worker 窗口后为何没有 scheduler returned。
- 当前正在等待高密度 trace 运行输出：/tmp/tdt-continue-audit4-setup1w，用于定位最后一个未 complete 的事件。

## 2026-05-29 setup1 continue_for 高密度 trace 结果

- 高密度 trace 已完成 begin/complete 配对分析：56902 个 host event begin，56901 个 complete，只剩 1 个未完成。
- 未完成事件：restore 后第一个正常窗口内，host=prysm-beacon-1，event_count=3，task=SyscallConditionWake { process_id: 1000, thread_id: 1005 }。
- 结论：当前主矛盾不在 control socket 的 continue_for 接受逻辑；continue_for 已经让 manager 进入窗口，卡点在 worker 执行 beacon 的 syscall wake 时没有返回。
- 下一步：沿 SyscallConditionWake -> managed_thread / plugin resume 路径继续加窄定位，确认是 shim IPC、线程恢复状态、还是 restore 后阻塞条件重建问题。

## 2026-05-29 悬挂诊断进程收尾

- /tmp/tdt-continue-audit4-setup1w 这轮 Shadow 在证据收集后仍悬挂，已终止 Shadow 进程；runner 因 control socket 断开退出，这是预期诊断收尾。
- 下一步开更窄的 trace：只针对 prysm-beacon-1 的 post-restore thread resume / managed_thread syscall 路径，目标确认 thread_id=1005 卡在 continue_plugin 的哪一段。

## 2026-05-29 prysm-beacon-1 线程恢复窄 trace 结果

- 新 trace：SHADOW_RESTORE_THREAD_TRACE=host=prysm-beacon-1。
- restore 后第一个正常窗口中，未完成事件仍是 prysm-beacon-1 的 SyscallConditionWake(thread_id=1005)。
- 线程路径细节：host_resume -> process_resume -> thread_resume_enter 后，managed_thread_syscall_result 返回 syscall_nr=202，结果为 Done(retval=-110/ETIMEDOUT, restartable=false)。
- 随后 thread_resume_result=exited_process；结合 ps 中对应 Shadow worker wchan=do_wait，当前判断是：Shadow 误进入 ProcessDeath/进程退出清理路径并调用 waitpid，但 CRIU 恢复后的 native beacon 进程实际没有退出，导致 worker 阻塞。
- 主结论更新：continue_for 不是替代/绕过对象；主要矛盾转为 restore 后某个 mthread/IPC 状态把正常 syscall completion 之后的状态错误推进到 ExitedProcess/reap 路径。
- 下一步：审计 ManagedThread::resume 中 ProcessDeath 分支和 IPC receive close 条件，确认为什么 syscall completion 后 current_event 变成 ProcessDeath，以及 cleanup/reap 是否应避免阻塞等待仍存活的 restored process。

## 2026-05-29 IPC writer_closed 修复尝试

- 已做最小代码修改：
  - src/lib/vasi-sync/src/scchannel.rs 增加 reopen_writer_after_restore()，只清除 writer_closed 标志。
  - src/main/host/managed_thread.rs 在 ManagedThread::from_checkpoint 重接 IPC 后，如果 restored_event 不是 ProcessDeath，则清除 from_plugin channel 的 writer_closed。
- 语义意图：旧 native 进程清理可以关闭旧 writer，但 restored native process 使用同一 IPC shared memory 继续运行；恢复后不能把旧 writer_closed 当作新进程死亡。
- 约束：不改应用层配置，不改 run-control 接口，不用 step_one_window 替代 continue_for。
- 下一步：编译 Shadow，然后先跑 setup1 determinism 验证这个悬挂是否消失。

## 2026-05-29 IPC 修复后 setup1 验证

- 编译结果：cmake --build build --target shadow -j 16 通过。
- 验证命令：run_study.py --mode determinism --setup 1 --results-dir /tmp/tdt-ipc-fix-setup1 --work-root /tmp/tdt-ipc-fix-setup1w。
- 结果：determinism setup=1 passed=True。
- 关键意义：修复后真实 continue_for 路径自然返回，未使用 step_one_window 绕过；之前的 restore 后 worker do_wait 悬挂没有复现。
- 下一步：跑更完整的本地 suite，确认 setup 4/8 和 6 个 synthetic CP/restore verifier 不被该 IPC 修复破坏。

## 2026-05-29 完整 suite 验证启动

- 已启动一键本地 correctness suite：/home/ins0/Repos/TDT/experiments/run_local_suite.py。
- 结果目录：/tmp/tdt-ipc-fix-suite；工作目录：/tmp/tdt-ipc-fix-suite-work。
- 目标：确认 IPC restore 修复后 setup1/4/8 真实客户端 determinism 和 6 个 synthetic CP/restore verifier 全部仍可通过。

## 2026-05-29 完整 suite 第一轮结果

- suite 结果：NO。
- 通过：real-client setup1、real-client setup4、6 个 synthetic verifier 全部通过。
- 未通过：real-client setup8，runner 正常退出、非超时；determinism JSON 显示 comparisons=17，mismatches=2。
- setup8 首个 mismatch：geth-node.stderr 第 113 行，同一时间戳 08:18:44.200 下 reference 为 Ignoring already known beacon payload，replay 为 Nil finalized block cannot evict old blobs。
- 第二个 mismatch：prysm-validator-2.stderr 第 41 行，同一时间戳 08:18:14 下 sync contribution 的 aggregatorIndex/subcommitteeIndex 不同。
- 这不是刚才的 continue_for 悬挂，也不是明显 10 秒级时间偏移；更像 setup8 下恢复后共识/日志顺序或聚合者选择路径仍有偶发非确定性。
- 下一步：单独重跑 setup8，判断是偶发还是稳定；同时把 setup8 mismatch 作为侧线交给 subagent 做只读日志语义审计。

## 2026-05-29 setup8 单独重跑结果

- 单独重跑 setup8 determinism：/tmp/tdt-ipc-fix-setup8-rerun1，结果 passed=True。
- 解释：完整 suite 第一轮的 setup8 mismatch 暂时判定为偶发非确定性/日志语义漂移，而不是 IPC writer_closed 修复导致的稳定回归。
- 重要边界：该失败不是 continue_for 悬挂，也不是 10 秒级时间偏移；它表现为同一时间戳下 geth/prysm 日志内容或 aggregator 选择不同。
- 下一步：再跑一轮完整 suite，判断一键测试在修复后是否稳定；若 setup8 或其它单例再次失败，再按最早分歧日志进入专项定位。

## 2026-05-29 完整 suite 第二轮启动

- 已启动第二轮完整 suite：/tmp/tdt-ipc-fix-suite-rerun2。
- 目的：验证 IPC 修复后的 TDT 一键测试稳定性，尤其关注 setup8 是否再次出现同时间戳日志/aggregator mismatch。

## 2026-05-29 setup8 失败侧线审计结论

- subagent 只读审计确认：setup8 第一轮失败不是 10 秒级时间偏移，也不是 setup1 的 post-restore do_wait/IPC writer_closed 问题。
- 最早语义分歧：prysm-validator-2 在 slot 20 同 blockRoot/bitsCount 下 sync contribution proof 的 aggregatorIndex/subcommitteeIndex 顺序换位；geth-node 是同一 block/hash 的 duplicate payload WARN 与 Nil finalized 日志 0.1s 级重排。
- 分类：更像高并发 setup8 下应用/网络事件顺序微漂移，未看到共识状态分叉证据；不是稳定的 continue_for 或 IPC restore 回归。
- 建议：继续重复 setup8 / 完整 suite；如果只出现同 slot 同 blockRoot 可交换日志重排，再考虑比较器归一化实验；若出现更早 packet/event drift，再加 Shadow event-id 级 instrumentation。

## 2026-05-29 完整 suite 第二轮结果：setup8 10 秒级漂移

- 第二轮完整 suite 结果仍为 NO。
- 通过项仍稳定：setup1、setup4、6 个 synthetic verifier 全部 PASS。
- setup8 失败形态变化：不是第一轮的同 slot 日志重排，而是 8 个 beacon stdout 第一行全部出现 10 秒级 UpdateHead 时间差。
- 例子：prysm-beacon-1 reference=[CORE_CALL] UpdateHead 1780042901000000000，replay=[CORE_CALL] UpdateHead 1780042891000000000。
- 这是用户之前明确指出的 10 秒级偏移类问题，必须专项分析；已准备交给 subagent 深究。
- 主 session 当前判断：IPC writer_closed 修复解决了 setup1 continue_for 悬挂且不破坏 setup4/synthetic，但 setup8 suite 稳定性仍未达标，主矛盾转为 setup8 恢复后 10 秒级共识/UpdateHead 时间漂移。

## 2026-05-29 setup8 10 秒漂移修复尝试：harness log quiescence

- 诊断结论：第二轮 setup8 的 10 秒 UpdateHead 差异来自 replay log 切片边界。restore 后 restored.1000.stdout 在 post_offsets 之后才出现/刷入，导致 checkpoint 前已有的一条 UpdateHead 被误计入 replay 窗口。
- 已修改 experiments/checkpoint-study/run_study.py：新增 wait_for_log_quiescence()，在 pre_offsets 和 post_offsets 前等待主应用 stdout/stderr 文件集合和大小短暂稳定。
- 语义边界：只等待墙钟文件系统稳定，不推进模拟时间，不修改应用层配置，不改变 Shadow run-control。
- 下一步：单独跑 setup8 验证 10 秒漂移是否消失；若通过，再跑完整 suite。

## 2026-05-29 log quiescence 后 setup8 单项验证

- 单独重跑 setup8 determinism：/tmp/tdt-logq-setup8，结果 passed=True。
- 这支持 10 秒 UpdateHead 差异是 restore 后日志文件创建/刷盘与 offset 采样的 harness 边界问题，而非共识时间语义回归。
- 下一步：跑完整 suite，确认一键测试从 NO 收敛到 YES。

## 2026-05-29 log quiescence 后完整 suite 启动

- 已启动完整 suite：/tmp/tdt-logq-suite。
- 验证目标：setup1/4/8 真实客户端 determinism + 6 个 synthetic verifier 全部通过，确认一键测试恢复稳定。

## 2026-05-29 restored log 前缀对齐修复尝试

- setup4 失败进一步证明：有些 restored.* 日志文件会在 replay 开始后才出现，单纯等待文件稳定不够。
- 已修改 run_study.py：
  - 在 checkpoint 边界记录各 host/stdout/stderr 到 offset 为止的 checkpoint_prefixes。
  - replay capture 时，如果遇到 old_size=0 且 path.name 以 restored. 开头的新日志文件，就剥掉其内容开头与 checkpoint_prefixes 后缀完全相同的字节前缀。
- 语义意图：只移除 checkpoint 前已经存在、但因 restored log 文件新建而被误计入 replay 窗口的日志；不按时间戳猜测，不改模拟和应用配置。
- 下一步：先重跑 setup4，再跑完整 suite。

## 2026-05-29 restored log 前缀对齐后 setup4 验证

- 单独重跑 setup4 determinism：/tmp/tdt-prefixtrim-setup4，结果 passed=True。
- 这说明上一轮完整 suite 中 setup4 的 replay 前缀错位可以由 restored.* 日志前缀剥离修复。
- 下一步：再次运行完整 suite，验证 setup1/4/8 + synthetic 全部收敛。

## 2026-05-29 restored log 前缀对齐后完整 suite 启动

- 已启动完整 suite：/tmp/tdt-prefixtrim-suite。
- 目标：确认 setup1/4/8 determinism 与 6 个 synthetic verifier 全部通过。

## 2026-05-29 restored log 前缀对齐后完整 suite 结果

- 完整 suite：/tmp/tdt-prefixtrim-suite，结果 YES。
- PASS：real-client determinism setup1、setup4、setup8。
- PASS：synthetic-multihost-full、synthetic-multihost-tcp、synthetic-eth-poc、synthetic-eth-multiproc、synthetic-eth-shadowyaml-stable、synthetic-eth-shadowyaml-bootstrap。
- 结论：IPC writer_closed 修复解决 setup1 restore 后 continue_for 悬挂；restored log 前缀对齐修复解决 suite 中 setup4/setup8 的 restored.* 日志窗口错位。
- 当前剩余风险：第一轮 setup8 曾出现同 slot aggregator/log 顺序微漂移，但在最终 prefixtrim suite 中未复现；如后续多轮再出现，应按 packet/event order 继续专项定位。

## 2026-05-29 10 秒漂移 subagent 复核结果

- subagent 复核确认：1780042891000000000 这条 UpdateHead 在原始 reference 全量 stdout 中本来就存在，但 reference slice 从后一条 1780042901000000000 开始；replay slice 从 restored.1000.stdout 第 1 行 1780042891000000000 开始。
- 因此 10 秒差异是 comparison window 起点对齐/日志切片 artifact，不是 restore 后共识时间语义漂移。
- 该结论与主 session 的 restored log 前缀剥离修复一致；最终 /tmp/tdt-prefixtrim-suite 已验证 YES。

## 2026-05-29 - 稳定性优先三连 suite 复核

- 决策：按用户要求暂停性能优化主线，连续运行 3 次完整 TDT suite。若任一轮出现偶发失败，立即转入稳定性定位，不继续做性能试错。
- 主矛盾：TDT 框架必须稳定可用；性能优化只有在真实客户端 determinism setup1/4/8 与 6 个合成 CP/restore verifier 多轮稳定通过后才恢复。

- 三连复核第 1 轮结果：NO。通过：real-client setup1/setup4 与 6 个合成 verifier；失败：real-client setup8 determinism。证据：/tmp/tdt-stability-triplet-1/suite-result.json，case log：/tmp/tdt-stability-triplet-1/logs/real-client-setup-8.log。决策：暂停后续盲跑，先定位 setup8 偶发失败原因，稳定性优先。

- setup8 失败初判：不是持续漂移，geth/validator/beacon stderr 一致，8 个 beacon stdout 都只在窗口尾部少最后一条 UpdateHead 1780043965000000000。当前怀疑点从应用语义漂移转为 run-control/capture 边界或 restore 后边界事件执行差异。下一步单独启用 SHADOW_RUN_CONTROL_TRACE 重跑 setup8，审计 continue_for 的 reference/replay 暂停边界；不使用 step_one_window 替代 continue。

- 稳定性回退：已回退最近两个低收益 syscall 小剪枝：uio read/write 借用顺序调整、futex wake num_wakeups==0 早返回移动。保留 CP/restore、TCP congestion、IPC restore、log prefix trim 等主修复不动。原因：当前偶发失败发生在真实客户端 replay 尾部进度差异上，先排除最近低收益性能候选对 restore 后 syscall 路径的影响。

- 回退后 setup8 单跑：PASS。证据：/tmp/tdt-stability-setup8-after-syscall-revert1/determinism-setup-8.json。下一步从零开始重新做完整 suite 三连，只有三连全过才恢复性能优化主线。

- 回退后完整 suite 三连第 1 轮：YES。真实客户端 setup1/setup4/setup8 全过；6 个合成 verifier 全过。证据：/tmp/tdt-stability-after-revert-triplet-1/suite-result.json。

- 回退后完整 suite 三连第 2 轮：YES。真实客户端 setup1/setup4/setup8 全过；6 个合成 verifier 全过。证据：/tmp/tdt-stability-after-revert-triplet-2/suite-result.json。

- 回退后完整 suite 三连第 3 轮：YES。真实客户端 setup1/setup4/setup8 全过；6 个合成 verifier 全过。证据：/tmp/tdt-stability-after-revert-triplet-3/suite-result.json。
- 稳定性门禁结论：回退低收益 syscall 小剪枝后，完整 TDT suite 连续 3 轮通过。三轮证据：/tmp/tdt-stability-after-revert-triplet-1/suite-result.json、/tmp/tdt-stability-after-revert-triplet-2/suite-result.json、/tmp/tdt-stability-after-revert-triplet-3/suite-result.json。下一步按用户要求记录当前性能数据，并与之前节点性能数据对比。

## 2026-05-29 性能记录：三连 suite 通过后的采样

主目标：先确保 TDT 框架稳定可用，再记录当前性能，并和之前节点对比。这里不改变被模拟应用层配置，只记录模拟层表现。

完整 suite 稳定性结论：三连通过。

- 第 1 轮：`/tmp/tdt-stability-after-revert-triplet-1/suite-result.json`，YES。
- 第 2 轮：`/tmp/tdt-stability-after-revert-triplet-2/suite-result.json`，YES。
- 第 3 轮：`/tmp/tdt-stability-after-revert-triplet-3/suite-result.json`，YES。
- 覆盖项：真实客户端 determinism setup 1/4/8，以及 6 个本地合成 CP/restore verifier。

当前性能采样：`/tmp/tdt-pfm-after-stability-revert1/REPORT.md`，1/4/8 均通过。

| setup | steady sim/wall | elapsed | checkpoint | restore | local events | syscall wake wall | continue_plugin | receive |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 21.90x | 14.00s | 143.28ms | 121.66ms | 55,152 | 10.65s | 10.35s | 10.07s |
| 4 | 22.61x | 13.80s | 130.54ms | 267.75ms | 214,711 | 19.85s | 18.91s | 17.99s |
| 8 | 17.28x | 21.67s | 188.81ms | 449.00ms | 532,971 | 41.88s | 39.55s | 37.12s |

与之前较快节点 `/tmp/tdt-pfm-final-20260529/REPORT.md` 对比：

| setup | 当前 steady | 之前 steady | 变化 | 当前 checkpoint | 之前 checkpoint | 当前 restore | 之前 restore |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 21.90x | 42.09x | -48.0% | 143.28ms | 147.57ms | 121.66ms | 136.77ms |
| 4 | 22.61x | 38.84x | -41.8% | 130.54ms | 105.06ms | 267.75ms | 202.60ms |
| 8 | 17.28x | 30.59x | -43.5% | 188.81ms | 184.33ms | 449.00ms | 390.48ms |

与之前 `fast-final-all` 节点 `/tmp/tdt-pfm-fast-final-all/REPORT.md` 对比：

| setup | 当前 steady | fast-final-all steady | 变化 |
| ---: | ---: | ---: | ---: |
| 1 | 21.90x | 43.53x | -49.7% |
| 4 | 22.61x | 37.76x | -40.1% |
| 8 | 17.28x | 31.31x | -44.8% |

setup8 结构性对比：当前 workload 规模基本一致，但 wall time 明显膨胀。

- 当前 setup8 local events 为 532,971；之前 `/tmp/tdt-pfm-final-20260529` 为 531,656，事件规模接近。
- 当前 scheduler windows 为 2956；之前也是 2956，窗口数接近。
- 当前 setup8 syscall wake wall 为 41.88s；之前为 20.47s，约 +104.5%。
- 当前 setup8 continue_plugin 为 39.55s；之前为 18.26s，约 +116.6%。
- 当前 setup8 receive 为 37.12s；之前为 13.03s，约 +184.9%。

解释：本轮性能采样在语义上通过，但吞吐明显慢于之前节点。由于事件数、窗口数、packet/local 规模基本一致，差异更像是同一 workload 下 wall-time 等待扩大，集中在 managed thread 的 `continue_plugin receive/syscall wake` 路径，而不是应用层配置变化或模拟工作量变化。

额外试错：我尝试额外单独重跑 setup8 性能采样，结果不能作为吞吐数据使用。

- 结果文件：`/tmp/tdt-pfm-after-stability-revert1-setup8-rerun/perf-model.json`，失败。
- 失败原因：Shadow 启动早期 control socket 绑定失败，路径过长，报 `path must be shorter than SUN_LEN`。
- 日志：`/tmp/tdt-pfm-after-stability-revert1-setup8-rerun-w/checkpoint-study/performance-setup-8-trial-1/performance.log`。
- 结论：这是 runner/work-root 路径长度问题，不是 CP/restore determinism 失败，也不是吞吐退化数据点。后续性能 runner 应优先使用更短 work-root，例如 `/tmp/tpfm8w`。

与论文 baseline 对比：如果沿用之前记录的 baseline speedup setup 1/4/8 为 40.7x / 34.5x / 26.3x，则当前 21.90x / 22.61x / 17.28x 分别低约 46.2% / 34.5% / 34.3%。这一结论只能说明“当前这次采样偏慢”，不能直接说明功能回退；稳定性 suite 已连续通过，且这次慢点主要暴露在 wall-time 等待路径。

下一步如果继续性能线：不要调应用层配置；先用短 work-root 重复性能采样确认是否可复现，再集中审计 managed thread `continue_plugin receive/syscall wake` 的 wall-time 放大原因。当前阶段不继续追 runahead 或调参。

## 2026-05-29 性能慢样本复核：外部 Shadow 负载

决策：当前 17x 左右的慢样本不能直接当作代码回退。主矛盾先从“Shadow 热路径退化”调整为“性能采样是否被宿主机外部负载污染”。

证据：

- 短路径 setup8 复采样 `/tmp/tdt-pfm-short-setup8-20260529a/REPORT.md` 通过，但 steady 仍为 17.23x，和 `/tmp/tdt-pfm-after-stability-revert1/REPORT.md` 的 17.28x 一致。
- 短路径排除了 control socket 路径长度作为慢样本主因；但它只排除启动路径问题，不排除宿主负载。
- `ps` 发现当前仍有另一个 Shadow 进程在跑：PID 599851，命令 `/home/ins0/Repos/TDT/deps/shadow/build/src/main/shadow /tmp/tdt-local-runtime/shadow.yaml`，CPU 约 587%。
- 这足以解释当前性能采样中 `continue_plugin receive/syscall wake` wall time 翻倍以上，因为 Shadow manager 等 native managed threads 竞争同一宿主 CPU。

Sidecar B 只读复核结论：

- setup8 的 windows、packet/local/syscall-wake 事件量与之前快样本接近，说明不是应用配置或 workload 变化。
- 慢在 wall time：`continue_plugin`、`receive`、`syscall wake` 均显著放大。
- 当前样本更像宿主调度/负载噪声；后续正式性能口径必须记录 loadavg、重负载进程、Shadow 日志中的 ru_nivcsw，并尽量在无额外 Shadow 运行时做 A/B 交错多次采样。

下一步：

- 不杀用户可能正在使用的 `/tmp/tdt-local-runtime` Shadow 进程。
- 在性能 runner 中固化更短的默认 work-root/路径长度检查，并记录宿主负载证据，避免再次把 SUN_LEN 或外部 Shadow 干扰混入性能结论。
- 继续只在模拟层内部寻找优化点；不改应用层配置，不追 runahead 调参。

## 2026-05-29 perf runner 测量可靠性修复

修改：`experiments/perf_model/run_perf_model.py`。

- 默认 `--work-root` 从 `/tmp/tdt-perf-model` 缩短为 `/tmp/tpfm`，降低 control socket 路径超过 `sockaddr_un.sun_path` 的概率。
- 新增 `validate_control_socket_path()`，在启动 Shadow 前检查即将生成的 `control.sock` 路径长度；若过长，runner 直接报错并提示使用更短 `--work-root`，避免 60 秒后才在 Shadow 日志里看到 `path must be shorter than SUN_LEN`。
- 新增 `host_load_snapshot()`，在性能实验前后记录 `loadavg` 与 top CPU 进程，并写入 `perf-model.json` 和 `REPORT.md` 的 `Host Load Snapshot` 小节。

验证：

- Python AST parse 通过：`ast.parse(run_perf_model.py)`。
- 长路径检查已触发预期错误：`/tmp/tdt-pfm-after-stability-revert1-setup8-rerun-w/.../control.sock (109 bytes)` 被 runner 直接拒绝。

作用：这不是模拟热路径优化，而是防止性能实验误判。当前主性能瓶颈候选仍是 managed-thread `continue_plugin receive/syscall wake`，但在宿主机有另一个 Shadow 占用约 587% CPU 时，不能继续用 wall-time 数据做代码归因。

## 2026-05-29 关闭外部 Shadow 后的性能复测与 idle-spin 优化

用户关闭另一个 `/tmp/tdt-local-runtime` Shadow 后，重新跑当前 Shadow binary 的性能模型。

代码修改：

- `deps/shadow/src/lib/scheduler/src/sync/simple_latch.rs`
  - 原逻辑：`use_worker_spinning=true` 时，worker 等待下一轮 scheduler task 会无限 `spin_loop + yield_now`。
  - 问题：run-control 暂停态没有下一轮 task，但 worker 仍持续自旋；之前观察到一个 `sim_waiting=true` 的 Shadow 仍占用约 600% CPU。
  - 新逻辑：先短暂 spin/yield，再进入 futex wait。运行期仍保留短等待低延迟路径，长暂停/空闲时释放 CPU。

构建与单元验证：

- `cmake --build build -j 8 --target shadow`：通过。
- `cargo test -p scheduler simple_latch`：5 个 simple_latch 单元测试全通过。

性能复测：`/tmp/tdt-pfm-after-idle-spin-fix1/REPORT.md`，work-root `/tmp/tpfm-fix1w`，1/4/8 全通过。

| setup | steady sim/wall | elapsed | checkpoint | restore | local events | syscall wake wall | continue_plugin receive |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 43.26x | 7.23s | 154.37ms | 81.36ms | 55,173 | 4.94s | 4.17s |
| 4 | 36.60x | 8.79s | 148.40ms | 213.33ms | 215,226 | 9.87s | 6.84s |
| 8 | 31.26x | 12.50s | 197.68ms | 414.56ms | 532,770 | 20.60s | 13.28s |

与被外部 Shadow 干扰的慢样本 `/tmp/tdt-pfm-after-stability-revert1/REPORT.md` 对比：

| setup | 慢样本 steady | 当前 steady | 变化 |
| ---: | ---: | ---: | ---: |
| 1 | 21.90x | 43.26x | +97.5% |
| 4 | 22.61x | 36.60x | +61.9% |
| 8 | 17.28x | 31.26x | +80.9% |

与之前较快节点 `/tmp/tdt-pfm-final-20260529/REPORT.md` 对比：

| setup | 之前 steady | 当前 steady | 变化 |
| ---: | ---: | ---: | ---: |
| 1 | 42.09x | 43.26x | +2.8% |
| 4 | 38.84x | 36.60x | -5.8% |
| 8 | 30.59x | 31.26x | +2.2% |

与之前 `fast-final-all` 节点 `/tmp/tdt-pfm-fast-final-all/REPORT.md` 对比：

| setup | fast-final-all steady | 当前 steady | 变化 |
| ---: | ---: | ---: | ---: |
| 1 | 43.53x | 43.26x | -0.6% |
| 4 | 37.76x | 36.60x | -3.1% |
| 8 | 31.31x | 31.26x | -0.2% |

结论：

- 关闭外部 Shadow 后，当前性能恢复到之前最快节点附近；上一轮 17x 慢样本确认为外部 Shadow 自旋/宿主负载污染，不是代码退化。
- idle-spin 修改的主要收益不是提升 running-state steady throughput，而是释放 run-control 暂停/空闲态 CPU，避免后续交互 Shadow 污染其它实验。
- 运行期主热路径仍是 setup 扩大后 prysm beacon 的有效 syscall wake/continue 边界，setup8 仍约 20.60s syscall wake wall、13.28s receive wall。

下一步：

- 用功能完整 suite 验证 idle-spin 修改不影响 determinism 与本地 CP/restore verifier。
- 若 suite 仍稳定，再继续沿 `read/write/epoll/getrandom` 的 syscall boundary 建模，而不是调 runahead 或应用配置。

## 2026-05-29 idle-spin 修改后的功能完整 suite

目的：确认 scheduler idle-spin 修改不影响 TDT 功能语义。

命令：

`python3 /home/ins0/Repos/TDT/experiments/run_local_suite.py --results-dir /tmp/tdt-suite-after-idle-spin-fix1 --work-root /tmp/tsuite-idle-fix1 --case-timeout 1800`

结果：YES。

通过项：

- real-client-determinism-setup-1：PASS，8.89s。
- real-client-determinism-setup-4：PASS，9.69s。
- real-client-determinism-setup-8：PASS，13.65s。
- synthetic-multihost-full：PASS。
- synthetic-multihost-tcp：PASS。
- synthetic-eth-poc：PASS。
- synthetic-eth-multiproc：PASS。
- synthetic-eth-shadowyaml-stable：PASS。
- synthetic-eth-shadowyaml-bootstrap：PASS。

证据：`/tmp/tdt-suite-after-idle-spin-fix1/suite-result.json`。

结论：当前 scheduler idle-spin 修改没有破坏真实客户端 determinism，也没有破坏 6 个本地合成 CP/restore verifier。功能门禁恢复为绿。
