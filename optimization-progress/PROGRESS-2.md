# TDT 优化进度记录 2

说明：从 2026-05-29 起继续记录性能优化与稳定性验证。后续每个进度文件接近 500 行时，新建下一个 `PROGRESS-N.md`，避免单个文件过长。

## 2026-05-29 进度记录轮转

- 用户要求：新建 `PROGRESS-2.md`，之后每记录到差不多 500 行就新建一个文件继续记录。
- 执行：已创建本文件；后续新增进度优先 append 到本文件。
- 当前状态：带 performance 的完整 suite 正在运行中，目标是验证 `run_local_suite.py` 默认 correctness 后顺带跑 1/4/8 性能 probe。

## 2026-05-29 latch 稳定性修复

- 决策：先暂停性能优化，优先修复带 performance suite 中 setup8 的 run-control pause timeout。
- 依据：失败不像应用层确定性 diff，而像 Shadow scheduler worker 在 continue 后停止推进；最可疑点是 `simple_latch` bounded spin 结束后直接 futex wait，缺少 futex wait 前的 generation 复查。
- 修改：在 `deps/shadow/src/lib/scheduler/src/sync/simple_latch.rs` 中，spin/yield 预算耗尽后重新读取并判断 latch generation；若 latch 已打开则直接返回，不进入 futex wait。
- 测试补强：新增 `spin_yield=true` waiter 的阻塞/唤醒测试，确保该路径不会在 paused simulation 场景中继续纯 busy spin。
- 下一步：运行 scheduler 单元测试、重建 Shadow，然后跑带 performance 的完整 TDT suite。
- 验证：`cargo test -p scheduler simple_latch` 通过，6 个测试全绿。
- 验证：`cmake --build build -j 8 --target shadow` 通过，已重建本地 Shadow binary；构建中仅出现既有 warning。
- 旁路分析：日志 subagent 判断 setup8 失败发生在 `continue_for -> wait_until_paused`，未进入 determinism mismatch 阶段，支持模拟层停滞假设。
- 旁路分析：测试 subagent 建议增加 `spin_yield=true` open/wait 竞争 stress，用于防止 bounded spin 到 futex wait 的边界回归。
- 修改：追加 `test_spin_yield_waiter_does_not_lose_racing_wake`，1000 轮覆盖 waiter 与 opener 的短竞争。
- 验证：重新运行 `cargo test -p scheduler simple_latch`，7 个测试全绿。
- 验证注意：第一次 suite 在受限 sandbox 内运行，control socket bind 被拒绝，属于无效结果；已停止并改为 sandbox 外重跑。
- 有效验证：sandbox 外完整 suite 输出 `YES`；真实客户端 setup 1/4/8、6 个合成 verifier、默认 1/4/8 性能参考全部 PASS。
- 有效性能参考：setup1 steady 43.37x，checkpoint 154.86ms，restore 96.58ms；setup4 steady 37.60x，checkpoint 144.91ms，restore 193.46ms；setup8 steady 32.68x，checkpoint 175.69ms，restore 340.24ms。

## 2026-05-29 syscall wake 热路径继续优化

- 决策：继续沿模拟层 syscall wake / Shadow-shim IPC 热路径推进，不碰应用层配置、不改网络延迟或 runahead 语义。
- 依据：最新性能报告显示 setup8 local wall 约 21060.93ms，其中 syscall wake 约 20262.60ms；packet wall 仅 16.21ms，queue lock 约 39.3ns，不是当前主瓶颈。
- 候选：`vasi-sync` 的 `receive_assuming_single_consumer` 已是 Shadow/shim IPC 快路径，但 Ready 路径仍在读取消息前额外 acquire-load 一次 state。
- 修改计划：将 SPSC fast receive 的主循环 state load 改为 acquire，并让 CAS/futex-wakeup 后重新进入 Ready 的路径也通过 acquire 观察；删除 Ready 后的额外 acquire-load，只保留 debug assertion。
- 修改：已更新 `deps/shadow/src/lib/vasi-sync/src/scchannel.rs` 的 SPSC fast receive acquire 路径，减少 Ready 热路径的一次 atomic load。
- 验证：`cargo test -p vasi-sync --test scchannel-tests` 通过，8 个测试全绿。
- 验证：`cmake --build build -j 8 --target shadow` 通过，已重建本地 Shadow；仅出现既有 warning。
- 结果：完整 suite 返回 `NO`，唯一失败为真实客户端 setup1 determinism；setup4/setup8、6 个合成 verifier、性能参考均 PASS。
- 失败语义：setup1 reference 在窗口末尾多出 `UpdateHead` 和 validator sync contribution 日志，replay 少尾部日志；不是崩溃，也不是 control socket 问题。
- 复核：单独重跑 setup1 determinism 通过，说明该失败不是稳定复现，但属于用户提醒过的窗口级 determinism 偶发现象。
- 性能判断：该 IPC acquire 微优化收益不明确，setup4 变快但 setup1/setup8 没有稳定收益；在出现一次完整 suite `NO` 的情况下不值得保留。
- 决策：回退本轮 `receive_assuming_single_consumer` acquire-load 微优化，保留之前已通过完整 suite 的 latch 稳定性修复。
- 回退验证：`cargo test -p vasi-sync --test scchannel-tests` 通过，8 个测试全绿。
- 回退验证：`cmake --build build -j 8 --target shadow` 通过，已重建回退后的本地 Shadow。
- 回退后复测：完整 suite 仍返回 `NO`，这次唯一失败变为 setup4 determinism；setup1/setup8、6 个合成 verifier、性能参考均 PASS。
- 失败共性：setup1/setup4 偶发失败均表现为 reference 比 replay 多出比较窗口末尾的 `UpdateHead ...` 和 validator `timeSinceSlotStart=12s` 尾部日志，说明问题不属于刚才回退的 IPC 微优化。
- 新主线：TDT determinism 采样边界稳定性。`run_study.py` 在窗口结束后 `wait_until_paused()` 立即 `capture_window()`，没有等待应用 stdout/stderr 文件稳定。
- 修改：在 reference 和 replay 的比较窗口结束后、捕获日志切片前都调用 `wait_for_log_quiescence(work_dir)`，只修复采样边界，不改变模拟和应用配置。
- Targeted 验证：重跑刚才失败的 setup4 determinism，通过；结果目录 `/tmp/tdt-rerun-setup4-after-log-quiescence1`。
- 完整验证第 1 轮：`/tmp/tdt-suite-with-perf-after-log-quiescence1` 输出 `YES`；真实客户端 setup1/4/8、6 个合成 verifier、性能参考全部 PASS。
- 性能参考第 1 轮：setup1 steady 43.04x，checkpoint 154.26ms，restore 93.39ms；setup4 steady 37.77x，checkpoint 98.44ms，restore 195.99ms；setup8 steady 31.29x，checkpoint 187.13ms，restore 364.74ms。
- 完整验证第 2 轮：`/tmp/tdt-suite-with-perf-after-log-quiescence2` 输出 `YES`；真实客户端 setup1/4/8、6 个合成 verifier、性能参考全部 PASS。
- 性能参考第 2 轮：setup1 steady 44.89x，checkpoint 148.41ms，restore 98.49ms；setup4 steady 38.97x，checkpoint 129.54ms，restore 208.17ms；setup8 steady 32.67x，checkpoint 170.37ms，restore 343.41ms。
- 完整验证第 3 轮：`/tmp/tdt-suite-with-perf-after-log-quiescence3` 输出 `YES`；真实客户端 setup1/4/8、6 个合成 verifier、性能参考全部 PASS。
- 性能参考第 3 轮：setup1 steady 44.64x，checkpoint 149.51ms，restore 96.72ms；setup4 steady 38.76x，checkpoint 101.21ms，restore 207.63ms；setup8 steady 31.87x，checkpoint 146.60ms，restore 458.48ms。
- 旁路结论：subagent 复核 setup1 偶发 diff，分类为 TDT 比较窗口边界/日志截断偶发，不是应用共识语义漂移。
- 下一步判断：不要继续做只改善 instrumentation 的优化；性能模型仍显示 syscall wake / managed-thread continue 是真实主线，packet wall 和 queue lock 不是主瓶颈。
## 2026-05-29 18:02:22 +0800

- 阶段状态：连续 3 轮完整 suite+perf 已经通过，稳定性从“偶发尾窗口 diff”恢复到可作为性能优化的前置条件。
- 当前主矛盾：setup 扩大后 steady speed 下降，已知最强解释不是 packet queue，也不是 cp/restore 本身，而是 syscall wake / managed-thread continue 相关本地调度成本。
- 本轮执行原则：不改应用层配置，不改网络延迟/runahead 语义，不做纯调参；只接受模拟层内部、接口不变、能被 suite 兜住的优化。
- subagent 分工：主会话保留集成判断；side lane 只读分析，不让上下文被宽泛日志和候选枝杈污染。

### 本轮路线细化

- side lane A 结论：`syscall_condition_wake_wall_ns` 不是 C 层 wake 本身，而是 wake task 触发后一路进入 `host_continue -> managed_thread::continue_plugin`，包含真实客户端运行到下一个 shim 事件的墙钟时间。
- 这解释了为什么 setup 扩大后 wake wall 和 `continue_plugin_receive/send` 同时上涨，但也意味着不能把 `receive_wall_ns` 当成纯 Shadow 内部开销直接“优化掉”。
- 决策：先修正性能测量边界，让 `SHADOW_TDT_PERF_COUNTERS=0` 能真正关闭 Rust 侧计数，并让 perf_model 支持 counters on/off。后续性能对比必须区分“真实运行改善”和“计数器开销减少”。
- 试跑 `/tmp/tdt-perf-counters-off1` 发现 managed-thread/syscall-condition 计数已经关闭，但 scheduler 计数仍然输出；原因是 `scheduler_perf_counters_enabled()` 仍用 `var_os().is_some()`。
- 决策：继续统一 Shadow 内部 TDT perf counter 开关语义，避免 `0` 被部分模块当成开启。
- 修复后重跑 `/tmp/tdt-perf-counters-off2`：通过，且不再输出 scheduler / managed-thread / syscall-condition detailed counters。
- counters-off throughput：setup1 steady `44.77x`，setup4 steady `40.07x`，setup8 steady `31.92x`；与 counters-on 第 3 轮 `44.64x / 38.76x / 31.87x` 相比，计数器污染存在但不是 setup8 主瓶颈，setup4 有约 3% 量级差异。
- side lane C 结论：当前命题应表述为“有效 syscall wake 导致的 managed-thread continue/native-return 长尾”，不是“futex/IPC 本身可直接优化掉”。下一步真实 runtime improvement 应使用 counters-off A/B/A，再用 counters-on perf_model 解释结构。

### 兜底验证

- 运行完整 suite：`/tmp/tdt-suite-after-counter-switch-fix1`，结果 `YES`。
- 真实客户端 determinism：setup1/setup4/setup8 全部 PASS。
- 合成 CP/restore：6 个 synthetic case 全部 PASS。
- 默认 counters-on performance：setup1 steady `41.65x`，setup4 steady `40.06x`，setup8 steady `31.32x`；checkpoint `150.18/103.62/169.78 ms`，restore `115.51/200.27/366.41 ms`。
- 本次没有把 counters-off 当成优化收益，只把它固化为后续 A/B/A runtime probe 的测量工具。
## 2026-05-29 18:15:38 +0800

- 继续 active goal：保持 TDT 功能可用，同时继续寻找模拟层内部性能优化点。
- 当前主矛盾：setup 扩大后吞吐下降仍由“有效 syscall wake -> managed-thread continue/native-return 长尾”解释；不能回到 runahead/网络延迟调参，也不能把 packet path 当主线。
- 本轮假设：每次 shim/syscall 往返存在固定模拟器成本，其中“返回用户态前总是处理 signal 的共享内存锁/扫描”可能是高频、低语义风险的优化候选，但必须先审计 signal 语义。
- 成功标准：任何代码改动后至少要 build 过，并用 targeted test 或 suite/perf 证明功能不退；若只是测量工具改动，不能宣称真实性能提升。

### signal fast-path 设计

- 当前 `shim::signals::process_signals` 每次 syscall completion 都会获取 host shmem lock，再检查 thread/process pending signals。
- 保守优化：给 `ProcessShmem` 和 `ThreadShmem` 增加 unprotected atomic hint。只有两个 hint 都是 false 时直接返回；任何 pending signal 被加入时先/同步置 true；消费后如果 pending set 为空再置 false。
- 语义边界：hint 允许假阳性，不能假阴性。blocked pending signal 会保持 hint=true，最多多走慢路径，不漏交付。
- 实现后第一次 signal 测试失败，根因不是语义失败，而是 `libshadow_shim.so` 没有随 shared-memory layout 改动重建，Shadow 和 shim ABI 不一致，panic 在 `shmalloc_impl.rs` 的 block size 校验。
- 处理：重建 `rust-shadow-shim-project`，再跑 `signals-extra` 和 `signals-multiprocess`，均通过。
- side lane signal-fastpath 结论：当前 per-process + per-thread `AtomicBool` hint 是可接受的保守方案；不建议缓存“unblocked pending”，因为 `sigprocmask` 会改变 blocked pending 的可递送性。

### signal fast-path 验证与性能判断

- 修改：在 `ProcessShmem` 和 `ThreadShmem` 中加入 `pending_signals_hint: AtomicBool`，在无 pending signal 的常见 syscall 返回路径跳过 host shmem lock 和 pending set 扫描。
- 修改：process-directed signal、thread-directed signal、hardware-error signal 写入 pending 后都会置 hint；消费 pending 后根据剩余 pending set 更新 hint。
- 语义判断：blocked pending signal 不会把 hint 清掉，因此最多产生慢路径假阳性，不会漏掉之后因 `sigprocmask` 改变而变成可递送的 signal。
- 验证：`cmake --build build -j 8 --target shadow` 通过。
- 验证：`cargo test -p shadow-shim-helper-rs --features std,nix` 通过，38 个 unit tests 和 doctests 全绿。
- 验证：`signals-extra` 通过；第一次失败是 shared-memory layout 改动后 shim dynamic lib 未重建导致 ABI 不一致，不是 signal 语义失败。
- 验证：`signals-multiprocess` 通过。
- 完整 suite：`/tmp/tdt-suite-after-signal-hint1` 输出 `YES`。
- 完整 suite 结果：真实客户端 determinism setup1/setup4/setup8 全部 PASS，6 个 synthetic CP/restore verifier 全部 PASS，reference performance PASS。
- counters-off 单轮性能：`/tmp/tdt-perf-signal-hint-off1` setup1 steady `44.86x`，setup4 steady `41.53x`，setup8 steady `32.74x`。
- counters-off 单轮 cp/restore：setup1 checkpoint `147.32ms` restore `85.58ms`；setup4 checkpoint `104.55ms` restore `204.69ms`；setup8 checkpoint `148.74ms` restore `386.81ms`。
- 与 counters-off 基线 `/tmp/tdt-perf-counters-off2` 对比：setup1 `44.77x -> 44.86x`，setup4 `40.07x -> 41.53x`，setup8 `31.92x -> 32.74x`，单轮看有小幅正收益。
- 但 setup8 counters-off 三轮复测 `/tmp/tdt-perf-signal-hint-setup8-trials3` 的 steady 中位为 `30.83x`，checkpoint 中位 `158.51ms`，restore 中位 `373.21ms`。
- 性能结论：signal hint 正确性目前成立，收益方向合理但不够稳定，不能作为阶段性“大优化”宣称；更适合保留为低风险固定成本削减，同时继续找更强主瓶颈。
- 当前主矛盾保持不变：setup 扩大后吞吐下降仍主要指向有效 syscall wake / managed-thread continue/native-return 长尾，而不是 packet path 或 event queue lock。
- 格式化边界：`cargo fmt --check` 当前不干净，但输出覆盖大量既有 dirty worktree 文件；本轮不执行全仓 `cargo fmt`，避免把历史改动和本轮优化混在一起。

## 2026-05-29 18:55:00 +0800

- 本轮主线：继续压缩模拟层内部固定成本，但稳定性优先；一旦真实客户端 suite 出现重复失败，立即暂停性能线。
- 保留优化：shim-side `process_signals` pending-signal hint 仍保留。它通过 targeted signal tests 和完整 suite，性能收益小且不稳定，但语义保守。
- 试错 1：尝试把 `SelfContainedChannel::send` 的 Ready 发布从 `fetch_update` 改为保留 flag 的 atomic OR，以降低 Shadow->shim IPC send 固定成本。
- 验证 1：`cargo test -p vasi-sync --test scchannel-tests` 通过，8 个测试全绿；`cmake --build build -j 8 --target shadow` 通过。
- 完整 suite 1：`/tmp/tdt-suite-after-channel-send-or1` 输出 `YES`，真实客户端 setup1/setup4/setup8、6 个 synthetic、performance 均通过。
- 性能 1：counters-off `/tmp/tdt-perf-channel-send-or-off1` 为 setup1 `44.92x`，setup4 `41.68x`，setup8 `32.03x`；setup8 相比 signal-hint 单轮 `32.74x` 没有稳定提升。
- 试错 2：尝试把 pending-signal hint 下推到 Shadow 侧 `Thread::unblocked_signal_pending`，希望减少 blocked syscall 路径上的 shmem borrow。
- 验证 2：build、`signals-extra`、`signals-multiprocess`、`scchannel-tests` 通过；`/tmp/tdt-suite-after-signal-shadowhint1` 输出 `YES`。
- 性能 2：counters-off `/tmp/tdt-perf-signal-shadowhint-off1` 为 setup1 `44.81x`，setup4 `37.68x`，setup8 `31.95x`；setup4 明显回退，判定不值得保留。
- 回退 2：移除 Shadow-side blocked-signal hint，只保留 shim-side signal hint。
- 观察：回退后完整 suite `/tmp/tdt-suite-after-shadowhint-revert1` 和 `/tmp/tdt-suite-after-shadowhint-revert2` 都出现 setup1/setup8 real-client determinism 失败，失败均为 reference 在窗口尾部多 `UpdateHead` 或 `timeSinceSlotStart=12s` 末尾日志。
- 旁路 agent 结论：该失败更像 window extraction boundary，不像共识语义漂移；证据是共同前缀一致，geth 一致，差异集中在 EOF，并且边界时间等于比较窗口 end。
- 试错 3：将 suite/checkpoint-study 的 comparison window 从 120s 改为 119s，试图避开 12s slot 边界。
- 验证 3：`/tmp/tdt-suite-window119-1` 仍输出 `NO`，setup1/setup4 在尾部多 `UpdateHead` 和 validator sync contribution；说明“改窗口长度”是调参陷阱，不解决根因。
- 决策：回退 IPC send OR、回退 `thread.rs` 附带 borrow 改动、恢复 comparison window 120s，回到更接近 known-good 的稳定状态。
- 稳定性复核 1：`/tmp/tdt-suite-reverted-ipc-window1` 输出 `YES`，真实客户端 setup1/setup4/setup8、6 个 synthetic、performance 均通过。
- 稳定性复核 2：`/tmp/tdt-suite-reverted-ipc-window2` 输出 `YES`，真实客户端 setup1/setup4/setup8、6 个 synthetic、performance 均通过。
- 当前性能参考：`/tmp/tdt-suite-reverted-ipc-window1` setup1 `43.30x`，setup4 `40.23x`，setup8 `32.03x`；checkpoint `155.75/102.56/149.67ms`，restore `88.08/211.55/386.76ms`。
- 第二轮性能参考：`/tmp/tdt-suite-reverted-ipc-window2` setup1 `43.31x`，setup4 `38.83x`，setup8 `31.89x`；restore setup4/setup8 有噪声尖峰，不作为优化结论。
- 当前结论：微优化不能以牺牲 suite 稳定性为代价；IPC send OR 和 Shadow-side blocked-signal hint 均不保留。下一条有意义的路线是架构级 async managed-thread continue 原型，但它需要单独设计，不应和当前稳定性收尾混做。

## 2026-05-29 19:04:43 +0800

- 继续执行性能优化 goal，使用 main-contradiction-first：主会话只保留主矛盾、证据门槛和集成决策。
- 当前主矛盾：setup8 吞吐下降由 scheduler scope 中最慢 worker body 的同步 managed-thread continue/native-return 长尾解释；packet/event queue 仍不是主瓶颈。
- Turing side lane 已确认：两轮稳定 suite 中 setup8 syscall wake wall 约 20s、continue wall 约 17.8s、receive wall 约 12.8s，而 packet wall 只有约 16ms；scope/max-body 约 98.5%。
- 决策：先加诊断而不是直接异步化。目标指标是 top worker body 内 continue_plugin receive 等待占比，用来判断 async continue 是否有真实可重叠空间。
- 已请求 Feynman 继续只读审计 async continue 的语义边界；已请求 Turing 只读审计 perf_model 解析点。

### continue receive worker-body instrumentation

- 修改：在 Shadow managed-thread TDT counters 中加入 per-worker-body thread-local 累加器，用于记录当前 scheduler worker body 内的 continue_plugin receive 等待时间。
- 修改：scheduler  现在输出 、，并在  中输出  和 。
- 修改：TDT  已适配新字段，同时保持旧日志兼容。
- 验证： 通过； 通过。
- setup8 单独性能诊断  通过：steady ，top 8 slowest worker bodies 的 continue receive 占比约  到 。
- 完整 suite  输出 ：真实客户端 determinism setup1/setup4/setup8 全过，6 个 synthetic CP/restore 全过，performance 参考全过。
- suite 性能参考：setup1 ，setup4 ，setup8 ；checkpoint ，restore 。
- 结论：这一步不是性能优化本身，而是把 async managed-thread continue 的可行性判据固化成可重复观测指标；当前数据支持继续研究重叠 native return 等待，但不能直接跳到架构改写。

### correction for previous append

- 上一段 append 因 shell quoting 中的反引号被解释，导致若干命令名和路径在进度文件里显示为空；下面补充同一批事实的无反引号版本。
- 修改：Shadow managed-thread TDT counters 加入 per-worker-body thread-local 累加器，记录当前 scheduler worker body 内的 continue_plugin receive 等待时间。
- 修改：scheduler TDT counters 输出字段 worker_body_continue_receive_wall_ns 和 max_worker_body_continue_receive_wall_ns；top_worker_bodies 输出 continue_receive_ms 和 continue_receive_pct。
- 修改：TDT experiments/perf_model/run_perf_model.py 已适配新字段，同时保持旧日志兼容。
- 验证：python3 -m py_compile experiments/perf_model/run_perf_model.py 通过；cmake --build build -j 8 --target shadow 通过。
- setup8 单独性能诊断 /tmp/tdt-perf-continue-recv-probe1 通过：steady 29.96x，top 8 slowest worker bodies 的 continue receive 占比约 73.3% 到 83.4%。
- 完整 suite /tmp/tdt-suite-after-continue-recv-instrument1 输出 YES：真实客户端 determinism setup1/setup4/setup8 全过，6 个 synthetic CP/restore 全过，performance 参考全过。
- suite 性能参考：setup1 43.50x，setup4 40.25x，setup8 32.67x；checkpoint 143.85/101.54/143.00ms，restore 87.76/206.84/394.24ms。
- 结论：这一步不是性能优化本身，而是把 async managed-thread continue 的可行性判据固化成可重复观测指标；当前数据支持继续研究重叠 native return 等待，但不能直接跳到架构改写。
- 补充：perf_model summary/report 现在还计算 worker body continue receive 总占比和 max-body 占比，方便直接从 Task Wall Time 表判断 async continue 是否值得继续。
- 验证：补充字段后再次运行 python3 -m py_compile experiments/perf_model/run_perf_model.py，通过。
- subagent 集成：Feynman 确认 async prototype 必须保留 host-level pending 串行、sim time 更新、shmem lock、Worker TLS、current_event exactly-once、syscall condition 同步返回等不变量；因此本阶段只保留 instrumentation，不直接实现 async。
- subagent 集成：Turing 指出的 perf_model 必改点已完成：scheduler regex、top_worker_bodies regex、dict 字段和 REPORT 表格均已适配。
- 报告 smoke：/tmp/tdt-perf-report-recv-pct-smoke1 通过，REPORT 已显示 Worker body receive ms、Worker body receive %、Max body receive %。
- smoke 数据：setup1 steady 44.87x；worker body receive 4089.69ms，占 worker body wall 81.5%；max-body receive 占比 80.6%。

## 2026-05-29 19:20:16 +0800

- 继续主线：不改应用层配置、不调网络延迟、不扩大 runahead；只建模模拟层内部的 managed-thread continue/native-return 同步等待。
- 文献/资料校准：保守 PDES 的性能瓶颈通常来自 lookahead、同步开销、负载不均和处理器 idle time；当前 TDT 对应的是最慢 worker body 阻塞 scheduler scope，不是 packet path。
- 修改：新增 window 级 async-overlap 上界估算。每个 scheduler window 记录 critical worker body 的 continue receive 时间，并用 top1/top2 worker body 估算如果 critical receive 可重叠，最多能节省多少 scope wall time。
- 修改：perf_model 解析并报告 estimated async overlap ms / percent。该数字是理论上界，不是实际收益承诺。
- 验证：python3 -m py_compile experiments/perf_model/run_perf_model.py 通过；cmake --build build -j 8 --target shadow 通过。
- setup8 诊断 /tmp/tdt-perf-async-overlap-estimate-setup8-1 通过：steady 32.21x，worker body receive 12639.38ms，占 61.1%；max-body receive 占 72.0%；估算 async overlap 上界 3704.33ms，占 scheduler scope 43.6%。
- 决策：该结果支持继续做 env-gated async continue 状态机设计，但仍不能跳过 host-level pending、sim time、shmem lock、Worker TLS、current_event exactly-once 等语义边界。

## 2026-05-29 19:26:18 +0800 收尾

- 收束当前方向：今天不继续展开 async managed-thread 状态机，只固化必要观测和一个后续原型需要的 SPSC 非阻塞 receive 原语。
- 修改：vasi-sync SelfContainedChannel 增加 try_receive_assuming_single_consumer，用于单消费者场景下非阻塞检查 pending message。
- 语义：空队列返回 Ok(None)，pending message 优先返回 Ok(Some)，writer closed 且无 pending message 返回 WriterIsClosed。
- 验证：cargo test -p vasi-sync --test scchannel-tests 通过，10 个测试全绿。
- 验证：python3 -m py_compile experiments/perf_model/run_perf_model.py experiments/run_local_suite.py 通过。
- 验证：cmake --build build -j 8 --target shadow 通过，仅有既有 warning。

## 2026-05-29 19:44:00 +0800 当日收尾

- 当前方向收束：不再继续展开 async managed-thread continue 状态机；本阶段只保留可重复观测指标和 SPSC 非阻塞 receive 原语，避免在收尾阶段引入大规模调度语义风险。
- 已完成验证：cargo test -p vasi-sync --test scchannel-tests 通过，10 个测试全绿；python3 -m py_compile experiments/perf_model/run_perf_model.py experiments/run_local_suite.py 通过；cmake --build build -j 8 --target shadow 通过。
- 完整 suite：/tmp/tdt-suite-final-20260529 输出 YES。真实客户端 determinism setup1、setup4、setup8 全过；synthetic multihost full、multihost tcp、eth poc、eth multiproc、eth shadowyaml stable、eth shadowyaml bootstrap 全过；reference performance 也完成。
- 真实客户端 determinism：setup1 comparisons=3 mismatches=0，checkpoint=117.59ms，restore=112.79ms；setup4 comparisons=9 mismatches=0，checkpoint=100.52ms，restore=246.37ms；setup8 comparisons=17 mismatches=0，checkpoint=132.36ms，restore=388.13ms。
- 共识语义检查：setup1/4/8 均有 geth chain head update 和 imported chain segment，beacon 均有 synced new block 和 finished applying state transition，validators 均有 sync message，多个 validator 有 submitted new block。setup8 中 8 个 beacon 都同步 5 个新区块并完成 5 次 state transition，geth head/import 各 5 次，payload 6 次，peer recorder 8 条。
- 日志异常解释：setup1 reference/replay 的 DeadlineExceeded/Could not 计数均为 18，setup4 reference/replay 均为 18，setup8 reference/replay 均为 3；这些错误在 reference 和 replay 中一致出现，且 determinism mismatch=0，不构成共识语义失败或恢复漂移。
- 性能数据：/tmp/tdt-suite-final-20260529/performance/REPORT.md 中 setup1 steady=43.14x，checkpoint=154.82ms，restore=107.64ms；setup4 steady=38.72x，checkpoint=133.82ms，restore=199.95ms；setup8 steady=29.93x，checkpoint=202.94ms，restore=1714.00ms。
- 与稳定初始节点 /tmp/tdt-suite-reverted-ipc-window1 对比：setup1 steady 43.30x -> 43.14x，约 -0.4%；setup4 40.23x -> 38.72x，约 -3.8%；setup8 32.03x -> 29.93x，约 -6.6%。本轮 counters-on 收尾性能没有形成优化收益，setup8 performance restore=1714.00ms 是明显噪声尖峰；同一 suite 的真实客户端 determinism setup8 restore=388.13ms，更接近此前稳定区间。
- 与最早报告中的 CP/restore 数字对比：setup1 checkpoint 332.14ms -> 154.82ms，restore 170.44ms -> 107.64ms；setup4 checkpoint 3489.38ms -> 133.82ms，restore 403.19ms -> 199.95ms；setup8 checkpoint 7799.97ms -> 202.94ms，但本次 performance restore 551.80ms -> 1714.00ms 受噪声尖峰影响，不作为退化结论。
- 当前主矛盾仍然成立：packet/event queue 不是瓶颈；setup8 吞吐下降主要由 managed-thread continue/native-return 同步等待 pin 住 scheduler window critical path 解释。新指标显示 setup8 worker body receive=13767.36ms，占 worker body wall 60.7%，估算 async overlap 上界=4112.38ms，占 scheduler scope 43.7%。
- 暂停点：下一次 resume 应从 env-gated async managed-thread continue 原型设计开始，但必须先明确 host-level pending 串行、sim time 更新、shmem lock、Worker TLS、current_event exactly-once 和 syscall condition 同步返回这些不变量。当前状态不是技术阻塞，而是用户要求暂停；不应把它误记为失败。

## 2026-05-29 20:18:00 +0800 resume 后第一轮

- 主矛盾复位：继续保持真实客户端语义和 suite 稳定，优化只允许发生在模拟层内部，不改应用层配置、不扩大 runahead、不调网络延迟。
- subagent A 只读审计结论：async continue 原型不能由后台线程直接改 Shadow 状态；必须是 host 级 pending/yield，再通过同一条 Host::resume -> Process::resume -> Thread::resume -> ManagedThread::resume 链路回收结果。
- subagent A 给出的不变量：同一 host 不允许并发继续多个事件；Worker TLS/current_time/active host/process/thread 只能在 scheduler worker 内有效；current_event 只能在同步 resume 状态机推进；scchannel 仍必须 SPSC；shmem lock 交接顺序不变；syscall condition 的 Block 必须同步返回到 Thread::resume 安装；原型阶段 checkpoint 前必须 drain pending continue。
- subagent B 汇总结论：没有证据推翻 managed-thread continue receive/synchronous return wait 是主瓶颈，但 native-return 这个窄标签证据不足；应把主瓶颈表述收窄为 managed-thread continue receive dominates。
- 修改：managed_thread TDT counters 增加 continue_exchange_top，按 Shadow->shim sent event 和 shim->Shadow received event 聚合 calls、wall_ms、receive_ms。
- 修改：experiments/perf_model/run_perf_model.py 解析 continue_exchange_top，并在 REPORT 中新增 Managed Continue Exchanges 表。
- 验证：rustfmt src/main/host/managed_thread.rs 通过；python3 -m py_compile experiments/perf_model/run_perf_model.py experiments/run_local_suite.py 通过；cargo test -p vasi-sync --test scchannel-tests 通过，10 个测试全绿；cmake --build build -j 8 --target shadow 通过。
- setup8 探针：/tmp/tdt-pfm-exchange-setup8-1 通过，steady=29.50x，checkpoint=191.78ms，restore=324.36ms。
- 关键发现：setup8 managed continue receive 总计 13292.76ms，其中 SyscallComplete->Syscall 为 542200 次、receive=13036.07ms、wall=18093.98ms，占绝对主导；SyscallDoNative->Syscall 只有 receive=203.22ms。
- 决策更新：下一步 async 原型不应只盯 native syscall return；真正的大头是 Shadow 把 emulated syscall result 交回 shim 后，应用继续运行直到下一次 syscall 的用户态时间片。若做 env-gated async，优先覆盖 SyscallComplete->Syscall 这类 exchange，同时保留 host 级 pending/yield 串行边界。

## 2026-05-29 20:06:42 +0800 当日收尾前的异步原型结论

- 当前局部探索完成：按主矛盾继续验证 managed-thread continue receive 是否可以通过 host-level async pending/yield 压缩 critical path。
- 原型尝试：实现了默认关闭的 SHADOW_ASYNC_MANAGED_CONTINUE=1，只覆盖 SyscallReturn::Done -> SyscallComplete，并在 host 边界 yield，窗口末 drain 后重新调度 ResumeProcess。
- 第一次失败：setup1 探针在 restore 后 panic，futex handler 报 `syscall was blocked, but there wasn't an existing futex`，说明异步路径破坏了 checkpoint/restore 后的 syscall/futex 状态一致性。
- 修复尝试：在 async start 时预分配 local event id，drain 时用 Event::new_local_with_id 入队，试图保持同步路径中的同时间事件顺序。
- 第二次失败：沙箱外重跑 /tmp/tdt-pfm-async-proto-setup1-3 仍在 restore 后触发同一个 futex 状态不一致 panic；说明问题不只是 event id 分配时机，async/yield 本身仍改变了同步 resume 状态机语义。
- 决策：不把该原型留在稳定路径。已移除 SHADOW_ASYNC_MANAGED_CONTINUE 相关代码，只保留观测指标和 SPSC try_receive 原语；今天不继续深挖该失败原型。
- 解释：该方向仍证明瓶颈真实存在，但当前最小异步化方案没有维护 `SyscallComplete -> 下一次 Syscall` 的同步语义边界，不能进入默认 suite。
- 验证：移除失败原型后，rustfmt 通过；cargo test -p vasi-sync --test scchannel-tests 通过，10 个测试全绿；cmake --build build -j 8 --target shadow 通过。

## 2026-05-29 20:11:02 +0800 收尾 suite 与性能

- 修复：完整 suite 的 reference-performance 子任务在较长 results/work-root 下生成的 control.sock 路径超过 Unix domain socket 限制。已在 experiments/run_local_suite.py 中加入短路径 fallback：当 performance work-root 过长时自动改用 `/tmp/tdtpf-<hash>`。
- 验证：python3 -m py_compile experiments/run_local_suite.py experiments/perf_model/run_perf_model.py experiments/checkpoint-study/run_study.py 通过。
- 第一轮完整 suite `/tmp/tdt-suite-closeout-20260529` 输出 NO：setup1/setup4 真实客户端 determinism 通过，6 个 synthetic CP/restore 通过；setup8 determinism 失败，performance 因 control socket path 太长直接失败。
- setup8 失败分析：mismatch 只发生在 8 个 beacon stdout，geth、validators、所有 beacon stderr 完全一致；每个 beacon 都是 reference 在 EOF 多一条 `[CORE_CALL] UpdateHead 1780056802000000000`，共同前缀完全一致。
- subagent Halley 复核：这不是共识语义失败，也不是 10 秒级漂移，而是窗口右边界日志采集边界问题；reference/replay 都同步到 slot 24，geth/validator/beacon stderr 一致。
- 第二轮完整 suite `/tmp/tdt-suite-closeout2-20260529` 使用短 work-root `/tmp/tdtco2` 输出 YES。
- 第二轮真实客户端 determinism：setup1 passed=true comparisons=3 mismatches=0 checkpoint=145.36ms restore=83.26ms；setup4 passed=true comparisons=9 mismatches=0 checkpoint=128.13ms restore=193.98ms；setup8 passed=true comparisons=17 mismatches=0 checkpoint=170.31ms restore=330.32ms。
- 第二轮 synthetic CP/restore：multihost-full、multihost-tcp、eth-poc、eth-multiproc、eth-shadowyaml-stable、eth-shadowyaml-bootstrap 全部通过。
- 共识语义检查：setup8 runtime 中 geth Chain head updated/imported segment 各 31 次，payload 32 次；8 个 beacon 都 Connected peers=6、Synced new block=31、Finished applying state transition=31；8 个 validator 都持续提交 sync message，且多个 validator 提交 block。判断：测试网语义正常，能够推进共识。
- 第二轮性能：setup1 steady=43.23x checkpoint=148.86ms restore=88.42ms；setup4 steady=38.89x checkpoint=126.17ms restore=189.37ms；setup8 steady=31.17x checkpoint=185.26ms restore=346.23ms。
- 与稳定初始节点 `/tmp/tdt-suite-reverted-ipc-window1` 对比：setup1 steady 43.30x -> 43.23x，约 -0.2%；setup4 40.23x -> 38.89x，约 -3.3%；setup8 32.03x -> 31.17x，约 -2.7%。当前观测性 instrumentation 没有带来性能收益，但也没有形成明显吞吐退化。
- CP/restore 对比稳定初始节点：setup1 checkpoint 155.75ms -> 148.86ms，restore 88.08ms -> 88.42ms；setup4 checkpoint 102.56ms -> 126.17ms，restore 211.55ms -> 189.37ms；setup8 checkpoint 149.67ms -> 185.26ms，restore 386.76ms -> 346.23ms。setup4/8 checkpoint 较高，但 restore 较低，属于单次测量波动范围。
- 与最早报告数字对比：setup1 checkpoint 332.14ms -> 148.86ms，restore 170.44ms -> 88.42ms；setup4 checkpoint 3489.38ms -> 126.17ms，restore 403.19ms -> 189.37ms；setup8 checkpoint 7799.97ms -> 185.26ms，restore 551.80ms -> 346.23ms。
- 当前性能主矛盾仍未变：setup8 managed continue receive=13006.41ms，占 worker body wall 60.5%；Managed Continue Exchanges 中 SyscallComplete->Syscall 551329 次、receive=12741.62ms，是主导项。packet/event queue 不是主瓶颈。
- 收尾状态：默认路径 suite 通过、共识语义正常、失败异步原型已移除。下一次 resume 应优先完善窗口边界诊断和继续研究能保持同步 resume 语义的 managed-thread continue 压缩方案。

## 2026-05-29 async continue safepoint 分支启动
- 已固化稳定 baseline：Shadow `33237a152 Preserve TDT checkpoint restore baseline` 已推送到 `spike-network-restore-protocol-rewrite`；TDT `bd00382 Add TDT local suite and performance guard` 已推送到 `main`。
- 已进入探索分支：Shadow 与 TDT 均为 `explore/async-continue-safepoint`。
- 本阶段只探索 managed-thread `SyscallComplete -> next Syscall` 异步化与 safepoint/drain，不展开 runahead、网络延迟、应用层配置或 packet 路线。
- 当前主矛盾：能否增加 native 执行与 Shadow scheduler 的重叠，同时保持同步 resume 链的事件顺序与 checkpoint/restore 静止点。
- 设计约束：不能把 next syscall 简单改成普通 local event，否则同 host 其它事件可能插队；checkpoint/pause 前必须 drain 到无 native in-flight。

## 2026-05-29 async continue 原型补丁
- 实现了默认关闭的 `SHADOW_TDT_ASYNC_CONTINUE` 实验开关。默认路径仍使用同步 `continue_plugin`，不改变 baseline。
- async 路线只覆盖 syscall 完成后的 `SyscallComplete` / `SyscallDoNative`，不覆盖 start、clone child、post-restore refresh 等更复杂路径。
- 添加了 host 级 pending continuation 队列：native 线程运行期间 host shmem lock 被释放，scheduler 不能继续执行该 host 的普通事件。
- scheduler 在本 worker 扫描其它 host 后，必须 drain 所有 pending continuation；drain 时先接收 shim 事件、重新 lock shmem、更新 `Worker::current_time`，再直接重入 `Host::resume`。
- checkpoint 防线：`runtime_snapshot()` 断言不能在 managed thread async in-flight 时执行。
- 这一步的判定标准：默认关闭必须通过构建和 guard；开启后 setup1 若出现确定性/CP restore 语义问题，则记录为路线风险而不是硬调参。

## 2026-05-29 async continue 试错：drain scope
- 第一次 async-on setup1 失败：只有 `tdt-async-continue begin`，没有 complete；应用日志全空，Shadow 很快到 sim end。
- 定位原因：我把 drain 放进同一个消费式 `HostIter` 的第二次遍历，第二次遍历实际为空，所以 pending native 从未 drain。
- 修正：将 drain 移到第一个 scheduler scope 之后的独立 scheduler drain scope，并只在 `SHADOW_TDT_ASYNC_CONTINUE=1` 时启用，避免 default-off 增加额外扫描成本。
- 第二次 async-on setup1 进入 begin/complete/re-enter，但 panic 于 `record_async_continuation` 的 pending 队列 `RefCell already borrowed`。
- 定位原因：`while let Some(...) = self.async_continuations.borrow_mut().pop_front()` 的临时 borrow 覆盖了循环体，直接重入 `Host::resume` 时再次 append pending 触发借用冲突。
- 修正：将 pop_front 的 borrow 限定在单独语句中，进入循环体前释放。

## 2026-05-29 async continue 试错：收窄 SyscallDoNative
- 修复 borrow 后，async-on setup1 不再早期崩溃，geth 与 validator 有日志，但没有达到 ready：beacon 启动脚本日志全空，validator 持续连接 `11.0.0.0:4000` 超时。
- 语义判断：`SyscallDoNative` 涉及真实内核副作用和进程状态变化，比纯 `SyscallComplete` 更容易破坏 shell/exec 启动链；这属于主路线内部的复杂边，不是调参问题。
- 新试验：暂时只异步化 `SyscallComplete`，不异步化 `SyscallDoNative`。目标是先证明纯 Shadow-handled syscall completion 的 async/drain 是否能保持真实客户端 liveness。

## 2026-05-29 async continue 试错：事件循环类 syscall 白名单
- `SyscallComplete` 全量 async 仍未达到 ready。关键证据：start_beacon 脚本执行到 `write(1, ..., 29)`，但 stdout 文件为空，说明启动链/stdio/进程控制路径对异步重入敏感。
- 为避免把风险归因成调参，本次改为语义白名单：只允许 epoll/poll/futex/select/nanosleep 等事件循环等待类 syscall completion 异步化。
- 这条白名单的理由：目标是重叠等待后应用继续执行到下一次 syscall 的时间，而不是改写 fork/exec/write/open 这类启动与文件副作用路径。

## 2026-05-29 async continue default-off 复核与阶段结论
- 复核刚才完整 suite 中 setup1 偶发失败：用同一份 `/tmp/tdt-async-defaultoff-suite/local-suite-experiment.toml` 单独重跑 setup1，结果 `/tmp/tdt-async-defaultoff-setup1-suiteconfig-rerun/determinism-setup-1.json` 为 `passed=true`，3 个真实客户端比较全部一致，checkpoint=146.33ms，restore=87.87ms。
- 再跑完整 default-off suite `/tmp/tdt-async-defaultoff-suite-rerun2`，最终输出 `YES`。真实客户端 determinism setup1/setup4/setup8 全通过，6 个 synthetic CP/restore 全通过，reference-performance 通过。
- 本轮 suite determinism 数据：setup1 checkpoint=147.12ms restore=79.56ms comparisons=3 mismatches=0；setup4 checkpoint=130.08ms restore=196.30ms comparisons=9 mismatches=0；setup8 checkpoint=166.00ms restore=342.83ms comparisons=17 mismatches=0。
- 本轮 ready 语义：setup8 中 8 个 beacon 都完成 state transition/synced block=7；geth head/import=7、payload=9；8 个 validator 都持续提交 sync message，且多个 validator 提交 block。判断 default-off 测试网仍能推进共识语义。
- 本轮 performance 数据：setup1 steady=39.50x checkpoint=139.93ms restore=107.44ms；setup4 steady=35.51x checkpoint=135.78ms restore=194.91ms；setup8 steady=31.10x checkpoint=159.84ms restore=338.77ms。
- 性能主矛盾证据仍一致：setup8 worker body receive=13230.93ms，占 worker body wall 60.3%；Managed Continue Exchanges 中 `SyscallComplete -> Syscall` 为 543825 次，receive=12967.81ms，是最大项。
- async-on 事件循环白名单试验 `/tmp/tdt-async-on-setup1-eventlooponly` 达到 liveness，但 determinism 失败：geth payload `delivery` 变 `timeout`，beacon block build slot 10 出现约 0.8s 差异，validator 从提交 block 变成 EL payload timeout。
- 决策：当前 async continue 路线不能进入默认路径或声称优化成功。主原因不是 liveness，而是 drain/重入顺序仍会改变真实客户端 replay timeline。下一步若继续该方向，必须先设计 deterministic ordered drain barrier，再谈性能收益。
- 保护线：当前探索补丁默认关闭，default-off 完整 suite 已通过；稳定 baseline 已在远程 `spike-network-restore-protocol-rewrite` 和 TDT `main` 保存。

## 2026-05-29 async continue 第二轮：immediate drain 与 readiness-only 边界
- 本轮主矛盾：继续只探索 `SyscallComplete -> next Syscall` async overlap，目标是找出能保持 determinism 的 safepoint/drain 语义；不改应用层配置、不改 runahead、不动网络延迟。
- subagent 尝试：线程池已满，无法新开 read-only explorer。本轮由主 session 本地完成关键路径判断，没有展开额外路线。
- 实现：新增 `SHADOW_TDT_ASYNC_SCOPE_DRAIN`。默认 `SHADOW_TDT_ASYNC_CONTINUE=1` 时改为 host execute 返回后 immediate drain；只有额外设置 scope drain 开关才保留旧的 scheduler-scope 延迟 drain。
- 结果：`/tmp/tdt-async-immediate-on-setup1` 仍 `passed=false`，首个 mismatch 与上一轮同类：geth payload `delivery` -> `timeout`，beacon slot 10 build time 约 1.3s -> 2.1s，validator 提交 block -> EL payload timeout。
- 结论：确定性失败不只是 scope 级 drain 太晚；async begin/complete 机制本身或可异步 syscall 边界仍改变 replay timeline。
- 语义收窄：将 async 白名单从 epoll/poll/select/futex/nanosleep 缩到 readiness wait 类：epoll/poll/select，排除 futex 和 sleep 类 syscall，因为它们携带同步或时间状态。
- readiness-only 结果 1：`/tmp/tdt-async-readiness-on-setup1` 在 restore 时 Shadow abort，panic 位于 `Host::schedule_task_with_delay()` 对 `Worker::current_time()` 的 unwrap。说明这个边界暴露了 checkpoint replay 期间无 worker current_time 但仍按 delay 调度任务的恢复期脆弱点。
- 修复尝试：`schedule_task_with_delay()` 在 `Worker::current_time()` 缺失时，用 event queue 的 `last_popped_event_time()` 作为基准。这只影响没有 current worker time 的恢复/重建路径。
- readiness-only 结果 2：`/tmp/tdt-async-readiness-fallback-on-setup1` 不再是同一个 unwrap panic，但 restore 仍失败，Shadow code=-11，说明恢复期状态仍不满足 async readiness-only 路线的安全条件。
- default-off 保护线：`/tmp/tdt-async-fallback-defaultoff-suite` 完整 suite 输出 `YES`。真实客户端 determinism setup1/setup4/setup8 全通过；6 个 synthetic CP/restore 全通过；reference-performance 通过。
- default-off determinism 数据：setup1 checkpoint=146.39ms restore=82.17ms mismatches=0；setup4 checkpoint=131.42ms restore=194.14ms mismatches=0；setup8 checkpoint=171.56ms restore=366.51ms mismatches=0。
- default-off performance：setup1 steady=42.95x checkpoint=144.29ms restore=88.01ms；setup4 steady=40.36x checkpoint=143.87ms restore=196.71ms；setup8 steady=32.75x checkpoint=212.60ms restore=614.75ms。
- 当前判断：async continue 方向仍是主性能矛盾的合理路线，但简单 host-level async 不满足 CP/restore determinism。下一步不能继续收窄白名单式调参；应转向显式定义 async safepoint 状态机，或者寻找不释放同步 resume 语义的更小 overlap 点。

## 2026-05-29 async continue 第三轮：socket I/O 与 inline drain
- 本轮主矛盾：上一轮 readiness-only async 失败后，继续只探索 managed-thread continue overlap，不展开 runahead、网络延迟、packet queue 或应用层配置。核心问题变成：能否找到一个不破坏 CP/restore 的 async 边界，同时不把 scheduler window 切碎。
- 外部资料复盘：PDES/并行离散事件仿真的主线仍是 conservative lookahead 与 optimistic rollback；TDT 真实客户端和 CP/restore 目标不适合引入应用级 rollback，所以本轮继续选择保守、可静止点证明的 async continuation，而不是改变模拟语义。
- 失败试验：实现 `SHADOW_TDT_ASYNC_ORDERED_DRAIN`，尝试按 host id 稳定顺序 drain pending continuation。setup1 restore 仍 Shadow code=-11。判断：全局 host 顺序不是主因；pending continuation 与 restore 静止点状态边界仍需要更明确建模。该代码随后已移除，避免固化失败路线。
- 新边界试验：实现 `SHADOW_TDT_ASYNC_SOCKET_IO`，只异步化 socket/legacy-tcp 上的 `read/write/readv/writev` completion，不再碰 futex、sleep、epoll 等等待类 syscall。原因是性能报告中 read/write continue wall time 很大，同时这类 syscall 的完成语义比 sleep/futex 更接近数据面 I/O。
- socket-I/O async determinism：setup1 `/tmp/tdt-async-socketio-on-setup1`、setup4 `/tmp/tdt-async-socketio-on-setup4`、setup8 `/tmp/tdt-async-socketio-on-setup8` 全部 passed=true。这是 async continue 路线第一次通过真实客户端 determinism setup1/4/8。
- socket-I/O async 性能负面：`/tmp/tdt-async-socketio-nolog-performance` 虽然把 setup8 local wall 从约 20.86s 降到约 9.33s，`SyscallComplete->Syscall` wall 从约 17.52s 降到约 8.52s，但 scheduler windows 从 2956 增到 5600，worker busy 从 40.33% 降到 17.52%，steady 从同构默认 31.96x 降到 31.27x。
- subagent Socrates 结论：socket syscall 级收益真实存在，但新增开销是 scheduler fragmentation；setup4/8 windows 接近翻倍，吞掉 read/write async 的收益。
- subagent Lagrange 结论：碎片化的直接原因是 `Host::execute` 发现 pending async 后立即 break，completion 被 manager post-scope drain，后续同 host 工作只能进入下一轮 scheduler window。更好的静止点位置在 host-local drain，而不是 manager 外层。
- 最小正向试验：实现 `SHADOW_TDT_ASYNC_INLINE_DRAIN`。当 host 内出现 pending async continuation 时，在同一个 `Host::execute(until)` 内 `drain_async_continuations()` 并继续本 host 事件循环，而不是返回 manager 制造新 window。
- inline drain determinism：setup1 `/tmp/tdt-async-inline-drain-on-setup1`、setup4 `/tmp/tdt-async-inline-drain-on-setup4`、setup8 `/tmp/tdt-async-inline-drain-on-setup8` 全部 passed=true。
- inline drain 性能：`/tmp/tdt-async-inline-drain-performance` 中 setup1 steady=43.26x，setup4 steady=40.17x，setup8 steady=32.72x；scheduler windows 恢复为 2356/2356/2956，说明碎片化被消除。
- 同构默认对比：`/tmp/tdt-current-default-performance` 为 setup1 43.36x、setup4 41.69x、setup8 31.96x；inline drain 为 setup1 -0.2%、setup4 -3.6%、setup8 +2.4%。结论：这不是稳定明确的整体优化，但已经把“可确定性 + 不碎窗口”的实现位置找出来了。
- 默认路径保护：清理 ordered-drain 失败代码后重新 build 通过。随后默认环境完整 suite `/tmp/tdt-inline-default-suite` 输出 YES：真实客户端 setup1/4/8、6 个 synthetic CP/restore、reference-performance 全部通过。
- 默认 suite 性能参考：`/tmp/tdt-inline-default-suite/performance/REPORT.md` 中 setup1 steady=43.21x、setup4 steady=38.85x、setup8 steady=32.64x；这是默认关闭 async 的基线，不代表 inline drain 开关收益。
- 决策：保留默认关闭的 socket-I/O + inline-drain 实验代码进入分支，作为下一轮显式 safepoint/host-local quiescence 状态机的基础；不把它作为默认优化开启，也不声称性能目标完成。
- 下一步：在最终清理后的代码上再跑一轮完整默认 suite；若仍 YES，则提交并推送 Shadow 分支，再更新 TDT submodule/progress，确保当前稳定状态保存到远程。
- 最终清理版验证：`/tmp/tdt-inline-final-default-suite` 输出 YES。真实客户端 setup1/4/8、6 个 synthetic CP/restore、reference-performance 全部通过。最终默认性能参考：setup1 steady=44.84x checkpoint=146.40ms restore=84.64ms；setup4 steady=38.71x checkpoint=104.46ms restore=188.76ms；setup8 steady=32.77x checkpoint=141.80ms restore=363.56ms。

## 2026-05-29 async continue 第四轮：same-window re-enter 负结果
- 本轮主矛盾复核：`continue_plugin_receive_wall_ns` 不是纯 channel receive 成本，而是 native thread 从收到 Shadow 回复到下一次 syscall/yield 的运行时间。Erdos 只读审计确认底层 `SelfContainedChannel` 已经是共享内存 SPSC，single-consumer receive 已避开普通 receive 的 Reading 状态；优先改 channel 状态机收益低、语义风险高。
- 新试验：实现临时 `SHADOW_TDT_ASYNC_SCOPE_REENTER`，只在 `ASYNC_CONTINUE + SOCKET_IO + SCOPE_DRAIN` 时启用。设计目标是保留 scope-drain 的跨 host overlap，同时 drain 后在同一个 outer manager window 内继续执行该 host，避免 socket-only scope drain 把后续 host 工作推到下一轮 window。
- 语义结果：`/tmp/tdt-async-reenter-on-setup1`、`/tmp/tdt-async-reenter-on-setup4`、`/tmp/tdt-async-reenter-on-setup8` 全部 passed=true。说明 same-window re-enter 没有立刻破坏真实客户端 CP/restore determinism。
- 性能结果：`/tmp/tdt-async-reenter-performance` passed=true，但端到端性能明显退化：setup1 steady=43.31x，setup4 steady=36.61x，setup8 steady=29.27x。对比 inline drain setup8 32.72x 和默认最终 32.77x，re-enter 不是优化。
- 解释：re-enter 虽然减少了部分 local/syscall wake 统计项，但 worker busy 下降到 setup8 12.50%，Scope/max-body 也降到 39.97%，整体并行结构变差；并且当前 re-enter 的二次 `host.execute` 未接入完整细粒度 counters，报告细项不能作为收益依据，只能看端到端 steady。
- 决策：移除 `SHADOW_TDT_ASYNC_SCOPE_REENTER` 代码，不保留该负路线。下一步不再尝试把 socket-I/O scope-drain 和 same-window re-enter 拼接；改为按 syscall 风险排序，单独验证低风险 `getrandom` 是否能作为更稀疏的 async safepoint 候选。
- getrandom-only 试验：临时加入 `SHADOW_TDT_ASYNC_GETRANDOM`，只允许 `getrandom` completion 进入 async scope-drain，不叠加 socket-I/O。setup1 `/tmp/tdt-async-getrandom-on-setup1` 在 restore 阶段 Shadow `code=-11`，control socket 关闭。
- 判断：`getrandom` handler 本身是低风险 deterministic RNG + 用户内存写回，但把它作为 async continuation 切点仍会暴露 restore/continuation 中间态。这个结果说明问题不只是 syscall 类型风险，而是 scope-drain async 状态机本身与 CP/restore 静止点仍不够显式。
- 决策：移除 `SHADOW_TDT_ASYNC_GETRANDOM` 代码，不保留该负路线。下一步应停止继续扩 syscall 白名单，转向两类更根本的工作：一是细分 prepare/clock/runahead 纯同步成本，二是显式状态机化 async continuation safepoint，而不是继续靠白名单试错。

## 2026-05-29 阶段性总结：停止白名单式 async 试错
- 本阶段回答的问题：真实客户端 TDT 的主要性能瓶颈是否能通过简单 managed-thread async continuation 绕开。结论是否定的：socket-I/O async 证明了 receive 等待可以被隐藏，但端到端收益不稳定；其它白名单候选要么破坏 determinism，要么 restore 崩溃，要么降低整体并行效率。
- 当前稳定成果：默认路径仍可用，`/tmp/tdt-inline-final-default-suite` 输出 YES。真实客户端 setup1/4/8、6 个 synthetic CP/restore、reference-performance 全部通过。Shadow 已保存的正向实验是默认关闭的 socket-I/O + inline-drain 开关，作为后续显式 safepoint 设计的材料，不作为默认优化。
- 性能基线：最终默认 suite 参考数据为 setup1 steady=44.84x、setup4 steady=38.71x、setup8 steady=32.77x。这个数字是当前阶段后续判断的基线；低于它的实验不应继续包装成优化。
- 关键洞察 1：`continue_plugin_receive_wall_ns` 不能被理解成 channel receive 开销。它主要包含 native thread 从收到 Shadow 回复到下一次 syscall/yield 的真实运行时间，因此改 `SelfContainedChannel` 状态机不是当前主路线。
- 关键洞察 2：async continuation 的语义边界比 syscall 类型更重要。即使 `getrandom` 本身低风险，一旦作为 async 切点进入 scope-drain，也能在 restore 阶段崩溃，说明 pending continuation 与 CP/restore 静止点需要显式状态机，而不是靠白名单规避。
- 关键洞察 3：调度结构会吞掉局部收益。socket-I/O scope-drain 能降低 read/write continue wall time，但会把 scheduler windows 从 setup8 约 2956 推到 5600；inline-drain 消除了碎片化，但减少 overlap；same-window re-enter 语义通过但 setup8 steady 退到 29.27x。
- 负结果清单：事件循环白名单导致真实客户端 determinism drift；readiness-only 在 restore 失败；ordered-drain restore code=-11；same-window re-enter 性能退化；getrandom-only restore code=-11。这些路线暂不继续。
- 下一阶段不继续做什么：不再扩 syscall 白名单，不再把 runahead/网络延迟当作调参手段，不默认开启任何 async 实验，不修改应用层配置。
- 下一阶段应该做什么：先把 `continue_plugin` 的同步 prepare 成本拆细观测，包括 `Worker::max_event_runahead_time(host)`、`host.set_shim_clock_state(...)`、`host.unlock_shmem()`、send/receive/lock/time update 的真实比例。只有当同步成本里出现可证明的模拟层内部热点时，再做无语义变化的压缩。
- 若继续 async 方向，必须先设计显式 safepoint 状态机：定义 pending native、drained-yielded、checkpoint-safe 三类状态，并保证 checkpoint/pause 只发生在明确静止点。没有这个模型之前，继续白名单试错价值很低。

## 2026-05-29 局部收尾：continue_plugin prepare 细分观测
- 本次只完成上一阶段留下的局部任务：把 `continue_plugin_prepare_wall_ns` 拆成 `runahead`、`clock_state`、`unlock` 三个子计数，并让 TDT perf model 报告这些列。目标是判断 prepare 是否存在模拟层内部可压缩热点，不继续扩展 async syscall 白名单。
- Shadow 改动点：`src/main/host/managed_thread.rs` 新增 `continue_plugin_runahead_wall_ns`、`continue_plugin_clock_state_wall_ns`、`continue_plugin_unlock_wall_ns` 三个 perf counter。计数只在 `SHADOW_TDT_PERF_COUNTERS` 开启时生效，默认路径不改变行为。
- TDT 改动点：`experiments/perf_model/run_perf_model.py` 兼容解析新旧 managed-thread counter 日志，并在 `Managed Thread Wall Time` 表格中展示 `runahead ms`、`clock state ms`、`unlock ms`。
- 验证：Shadow `cargo fmt --manifest-path src/main/Cargo.toml` 通过；Shadow `./setup build` 通过；TDT `python3 -m py_compile experiments/perf_model/run_perf_model.py` 通过。
- 最小真实客户端性能样本：`/tmp/tdt-continue-prepare-split-performance` passed=true。setup1 steady=44.89x，checkpoint=150.07ms，restore=88.58ms。
- 新观测值：setup1 `continue_plugin` 总计 4669.04ms，其中 receive=4119.65ms、send=523.25ms、prepare=10.08ms；prepare 内部 runahead=2.42ms、clock state=1.59ms、unlock=1.69ms。
- 结论：prepare 子项不是当前性能主矛盾，至少在 setup1 上只占 `continue_plugin` 的约 0.2%。本次观测没有发现值得立即压缩的同步 prepare 热点。
- 阶段停止点：不继续展开新路线。若之后恢复 goal，优先回到两个主判断：一是显式 safepoint 状态机是否能让 async continuation 与 CP/restore 共存；二是 setup4/8 上是否存在与 setup1 不同的同步模拟层热点。

## 2026-05-29 恢复后第一步：prepare split 扩展到 setup4/8
- 主矛盾复述：目标仍是在不改变应用层配置、不破坏真实客户端 CP/restore determinism 的前提下压缩模拟层关键路径。当前需要先证明热点在哪里，而不是继续白名单式 async 试错。
- 本轮只读 subagent 分工：Bacon 分析 Shadow managed-thread/worker 调度路径；Hooke 分析 TDT perf report 的 setup scaling 指标。主 session 保留最终路线判断。
- 主线实验：运行 `/tmp/tdt-prepare-split-scale-performance`，`--setups 4,8 --trials 1 --perf-counters on`，结果 passed=true。
- setup4 新观测：steady=37.64x，checkpoint=122.16ms，restore=207.47ms；`continue_plugin`=9000.76ms，receive=6888.47ms，send=2012.54ms，prepare=39.06ms，其中 runahead=8.95ms、clock_state=6.06ms、unlock=6.09ms。
- setup8 新观测：steady=31.26x，checkpoint=161.01ms，restore=401.13ms；`continue_plugin`=18055.60ms，receive=12973.10ms，send=4834.63ms，prepare=98.47ms，其中 runahead=22.60ms、clock_state=14.69ms、unlock=14.55ms。
- 结论更新：prepare 在 setup4/8 仍不是主热点。setup8 中 prepare 约占 `continue_plugin` 的 0.55%，远小于 receive 和 send；因此不应继续围绕 runahead/clock_state/unlock 做实现优化。
- Hooke 的指标判断：setup 扩大后的吞吐下降最贴近 managed continue/syscall wake 路径放大；`CP+restore wall %` 只有约 4%-6%，event queue 平均 lock ns 没随规模升高，暂时不是主因。
- Bacon 的低风险候选：既有 `SHADOW_PACKET_ROUTE_CACHE` 是只读派生路由缓存，理论上不改变事件时间和顺序；全量 host scan 和 async scope drain 二次扫描也可疑，但实现风险高于先做 route-cache A/B。
- 下一步候选：route-cache A/B，最小实验是 setup4/8、trials=1，对比 `/tmp/tdt-prepare-split-scale-performance` 的 packet/local/syscall/steady 指标，并保持 determinism 通过。
- 执行边界：尝试运行 route-cache A/B 时被执行审批拒绝，理由是超过上一阶段“收尾暂停”的授权边界。为避免绕过审批，本轮没有用其它方式继续跑该实验。后续需要明确授权后再执行。

## 2026-05-29 route-cache A/B：安全但不是新主优化点
- 本轮主实验：验证 `SHADOW_PACKET_ROUTE_CACHE=1` 是否能解释 setup8 的 packet/relay 放大。该开关只缓存 `route_endpoints + routing_info.path()` 派生的 `PacketRoute`，理论上不改变 packet delivery 时间、事件顺序或 RNG 抽样。
- 先跑无效 A/B：`/tmp/tdt-route-cache-scale-performance` 和 `/tmp/tdt-route-cache-ab-on-setup8-t2` 都 passed=true，但随后发现 `tdt_config.py` 中 `packet_route_cache` 默认就是 true，`tdt_config.local.toml` 没覆盖该字段，所以普通 base 已经启用了 route cache。
- 日志确认：默认 base 和显式 env-on 的 performance.log 都有 `Built packet route cache with 289 entries`，说明它们不是 off/on 对比。
- 为做有效对比，临时创建 `tdt_config.route_cache_off.local.toml`，只把 `packet_route_cache=false`，其它真实客户端配置保持一致；实验后已删除该临时文件，未提交。
- 有效 off 结果：`/tmp/tdt-route-cache-ab-off-setup8-t2` passed=true。setup8 steady=30.07x，elapsed=24.68s，managed_continue=18159.81ms，receive=13014.81ms。
- 默认 on 结果：`/tmp/tdt-route-cache-ab-base-setup8-t2` passed=true。setup8 steady=30.28x，elapsed=24.53s，managed_continue=17930.65ms，receive=12681.54ms。
- 有效 A/B 差异：route-cache on 相对 off，setup8 steady +0.68%，elapsed -0.61%，managed_continue -1.26%，receive -2.56%。这些变化低于先前估计的约 5% setup8 噪声阈值。
- 语义结论：route cache 是安全的派生缓存，CP/restore 后由新 Manager 重建，不需要 checkpoint 新状态；但它已经是当前 TDT 默认基线的一部分，且收益很小，不能作为新优化成果。
- 路线决策：停止 route-cache 路线。下一候选应回到更能解释 scaling 的 scheduler/host 扫描和 managed continue safepoint 结构；但任何实现前需要先加更直接的 host scan / pending-host 分布计数，避免再靠大粒度 wall time 猜测。

## 2026-05-29 host scan 降级与 syscall wake residual 方向
- 复核 host scan：现有 perf JSON 已有 `scheduler_host_scans`、`scheduler_host_executes` 和 `scheduler_host_scans_per_execute`，不需要先加新计数。
- 结果：`/tmp/tdt-prepare-split-scale-performance` 中 setup4 scans/execute=1.151，setup8 scans/execute=1.146；`/tmp/tdt-inline-final-default-suite/performance` 中 setup1=1.374、setup4=1.151、setup8=1.147。
- 判断：setup8 的 host scan per execute 并没有比 setup1/setup4 更差，不能解释 setup 扩大后吞吐下降。全量 host scan 暂时降级，不进入实现。
- 当前剩余主热点：`scheduler_syscall_condition_wake_wall_ms` 与 `managed_continue_plugin_wall_ms` 仍随 setup 放大。setup8 典型值约为 syscall wake 20s、managed continue 18s，说明大部分 wake wall 是继续 native thread 到下一次 syscall/yield 的时间，但仍有 residual 需要拆分。
- 本地代码定位：`SyscallConditionWake` task 由 `src/main/host/syscall/syscall_condition.c` 的 `_syscallcondition_scheduleWakeupTask()` 创建，触发后 `_syscallcondition_trigger()` 检查 process/thread/condition satisfied，然后调用 `host_continue(host, pid, tid)`；Rust 侧 task descriptor 在 `src/main/core/work/task.rs`，host 统计归类在 `src/main/host/host.rs`。
- 下一步不应直接优化：`host_continue`/`continue_plugin` 是语义核心，之前 async 白名单已经证明容易破坏 determinism 或 CP/restore。继续动它前需要更明确的 safepoint 状态机。
- 下一步应做的最小观测：把 syscall-condition wake wall 拆成 condition trigger/check 部分与 host_continue 部分，或者在 report 中先估算 syscall wake residual，确认 residual 是否足够大。只有 residual 稳定显著，才考虑 C/Rust 边界的小优化；否则继续回到 safepoint 设计。
