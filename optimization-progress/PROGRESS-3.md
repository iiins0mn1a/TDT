# TDT/Shadow 性能优化进度记录（三）

## 2026-05-31 继续 phase3：native reply / host lock 边界

- `PROGRESS-2.md` 已达到 483 行，接近约 500 行的轮换阈值；后续进度从本文件继续 append-only 记录。
- 已从当前 TDT 分支与 shadow 子模块分支分别新建：
  - TDT: `explore/native-reply-host-lock-20260531`
  - shadow: `explore/native-reply-host-lock-20260531`
- 当前主矛盾保持不变：真实客户端 setup 扩大后，Shadow worker 大量时间花在等待 native thread 的 continue-plugin reply；优化应围绕这个等待边界能否被拆成 pending/ready 状态，而不是改变应用层配置或扩大模拟网络延迟窗口。
- 本轮局部目标：先做行为保持的接口铺垫，让 manager 能知道 `host.execute()` 返回时 host shmem 是否已经由下层释放。同步路径默认仍为“未释放，由 manager 释放”，不改变现有行为。

## 2026-05-31 host shmem return 状态铺垫

- 修改 `HostExecutionStats`，新增 `host_shmem_unlocked_on_return: bool`。
- 修改 manager 的 host execute 调度点：只有当 `execution_stats.host_shmem_unlocked_on_return == false` 时才执行 `host.unlock_shmem()`。
- 当前 `HostExecutionStats::default()` 仍让该字段为 `false`，因此同步路径行为应完全保持不变。
- 这个铺垫的意义：后续如果 `host.execute()` 可以返回 `NativeReplyPending`，下层可能已经释放 host shmem 以等待 native reply；manager 需要有显式状态避免二次 unlock。

### 验证结果

- `cargo test --manifest-path src/Cargo.toml -p shadow-rs --lib host::managed_thread`：编译通过；该过滤条件下 0 个测试实际执行，166 个 filtered out；只有既有 warning。
- `./setup build`：通过；生成 release `build/src/main/shadow`。
- setup 8 counters-off 探针：`/tmp/tdt-host-lock-return-off-s8`，`passed=true`。
- 本次性能点：elapsed `11.64s`，sim/wall `30.92x`，steady `33.48x`，checkpoint `174.81 ms`，restore `341.48 ms`。
- 语义检查点：post-restore 后 8 个 beacon 均达到 `Synced new block=26` / `Finished applying state transition=26`，geth `Chain head was updated=26`，说明测试网继续推进。
- 结论：这是一个行为保持的结构铺垫，不预期带来性能收益；当前结果没有显示功能或确定性风险。

## 2026-05-31 CP/restore 静止点审计

- 让 subagent 只读审计了 `ManagedThread` native reply pending 与 checkpoint/restore 的冲突边界。
- 结论：该方向没有被直接否定，但 checkpoint 格式当前只表达 Shadow 已重新拥有协议控制权的状态，也就是 `native_run_phase == Parked`、IPC channel 为空、`current_event` 已经更新。
- 因此未来如允许 `NativeReplyPending` 返回 manager，checkpoint 前必须 drain 所有 pending native reply 回到现有静止点；第一阶段不序列化半交换状态。
- 关键风险：如果 Shadow 已发送 `ShimEventToShim` 但还没收到 reply 就 snapshot，restore 后 `current_event_bytes` 会仍然表示旧事件，协议两端错位。
- 本轮继续只打通返回通道，不让 pending 实际发生；这样仍是行为保持改造。

## 2026-05-31 Task/Resume pending 返回通道

- 增加 `TaskExecutionResult::{Complete, NativeReplyPending}`，让 `TaskRef::execute()` 可以向 `Host::execute()` 回传执行结果。
- 增加 `TaskRef::new_with_result_and_descriptor()`；原有 `TaskRef::new()` 和 `new_with_descriptor()` 仍包装成 `Complete`，保持既有接口可用。
- 在 `ManagedThread::ResumeResult`、`Thread::ResumeResult`、`Process::ResumeResult` 中加入 `NativeReplyPending`，并把返回链打到 `Host::resume()`。
- `Host::execute()` 在 local task 返回 `NativeReplyPending` 时设置 `host_shmem_unlocked_on_return=true` 并停止执行该 host。本轮还没有实际构造 pending，因此同步行为不应变化。
- 只让明确的 `ResumeProcess` task 具备向上回传 pending 的能力；其它 restore/helper task 暂时仍通过旧 API 返回 `Complete`，避免把半成品语义扩散到 checkpoint reconstruction helper。

### 验证结果

- `cargo test --manifest-path src/Cargo.toml -p shadow-rs --lib host::managed_thread`：编译通过；该过滤条件下 0 个测试实际执行；只有既有 warning。
- `./setup build`：通过。
- setup 8 counters-off 探针：`/tmp/tdt-task-result-route-off-s8`，`passed=true`。
- 本次性能点：elapsed `12.20s`，sim/wall `29.52x`，steady `31.91x`，checkpoint `173.13 ms`，restore `359.24 ms`。
- 语义检查点：post-restore 后 8 个 beacon 均达到 `Synced new block=28` / `Finished applying state transition=28`，geth `Chain head was updated=28`，说明测试网继续推进。
- 结论：返回通道 plumbing 已通过真实客户端 setup 8 guard；本步不是性能收益点，而是为下一步实现 pending completion/drain 做结构准备。

## 2026-05-31 pending native run checkpoint guard

- 在 `ManagedThread` 中增加 `pending_native_run: RefCell<Option<NativeRunToken>>`，当前初始化为 `None`，暂不实际填充。
- 扩展 `assert_shadow_owned_safepoint()`：checkpoint snapshot 前除了要求 `native_run_phase == Parked` 和 IPC channel 为空，也要求 `pending_native_run` 为空。
- 这个改动把 subagent 审计提出的 CP/restore 硬边界写进代码：未来真正让 native reply pending 返回 manager 时，如果没有先 drain，checkpoint 会在语义边界上失败，而不是悄悄序列化半交换状态。

### 验证结果

- `cargo test --manifest-path src/Cargo.toml -p shadow-rs --lib host::managed_thread`：编译通过；该过滤条件下 0 个测试实际执行；只有既有 warning。
- `./setup build`：通过。
- setup 8 counters-off 探针：`/tmp/tdt-pending-guard-off-s8`，`passed=true`。
- 本次性能点：elapsed `11.89s`，sim/wall `30.27x`，steady `32.76x`，checkpoint `169.32 ms`，restore `358.69 ms`。
- 语义检查点：post-restore 后 8 个 beacon 均达到 `Synced new block=27` / `Finished applying state transition=27`，geth `Chain head was updated=27`，说明测试网继续推进。
- 结论：guard 本身不带来性能收益，但明确了 async pending 路线不能绕过的 CP/restore 静止点。

## 2026-05-31 opt-in async native reply 试错

- 尝试实现 `SHADOW_TDT_ASYNC_NATIVE_REPLY=1` 开关：`ManagedThread::continue_plugin()` 先发送 shim 命令并保存 `NativeRunToken`，让 resume 返回 `NativeReplyPending`；下一次 resume 时再阻塞完成 receive/finish/current_event update。
- 第一次 setup 8 opt-in 失败：`/tmp/tdt-async-native-on-s8`，Shadow 在 `host.unlock_shmem()` 断言失败。原因是仍有旧 `TaskRef::new_with_descriptor` 路径吞掉了 `host.resume()` 的 pending 返回值，manager 以为 host shmem 仍持锁。
- 修复返回通道覆盖后第二次 opt-in 试验：`/tmp/tdt-async-native-on2-s8`。问题变成大量重复 geth 进程。原因是把 pending retry 放在 `Host::execute()` 层会重放整个 local task；对 `StartApplication` 来说这会重复 spawn 应用。
- 将 retry 责任下移到 `Host::resume(pid, tid)` 后第三次 opt-in：`/tmp/tdt-async-native-on3-s8`。重复 spawn 消失，但 Shadow 长时间停留在初始 simtime，CPU 接近满载，吞吐严重退化；该实验在 60 秒仍未完成 setup8 的第一个 60 秒 continue，手动停止。
- 结论：没有外部 readiness 通知时，“pending 后同一 simulated time 重试”会退化为忙轮询/极高频 scheduler window，抵消并超过 native wait overlap 的收益。这条具体实现路线判负，不提交。
- 保留的 insight：要继续这条主矛盾，必须引入低开销 readiness 通知或批量 drain 机制；否则不能只靠 event queue 的同时间重试。
- 已回退未提交的 opt-in 实验代码，保留此前已验证并推送的结构铺垫提交。
- 回退后默认 setup8 guard：`/tmp/tdt-post-async-revert-off-s8`，`passed=true`；steady `31.23x`，checkpoint `199.01 ms`，restore `343.15 ms`。
- 语义检查点：post-restore 后 8 个 beacon 均达到 `Synced new block=28` / `Finished applying state transition=28`，geth `Chain head was updated=28`。

## 2026-05-31 当前稳定分支 counters-on 复测

- 当前稳定分支 setup8 counters-on：`/tmp/tdt-current-counters-on-s8`，`passed=true`。
- 性能点：elapsed `12.40s`，sim/wall `29.04x`，steady `31.30x`，checkpoint `171.56 ms`，restore `348.77 ms`。
- 调度统计：parallelism `6`，windows `2956`，worker busy `39.68%`，scheduler scope wall `9019.49 ms`。
- native reply 仍是主瓶颈：worker body continue receive `12752.98 ms`，占 worker body `59.27%`；最慢 worker body continue receive pct 可到 `79.32%`。
- 模型估算：如果能在窗口内重叠 native receive，理论 async overlap savings `4003.62 ms`，约 `44.39%` scheduler scope；说明主矛盾仍然成立。
- 但上一轮试错说明：这个收益不能靠同一时间重试拿到，必须要 readiness 通知或窗口级批量 drain。

## 2026-05-31 readiness bridge 只读审计

- subagent 审计结论：`SelfContainedChannel::wait_ready_assuming_single_consumer()` 可以不消费地等待 `Ready`，但它仍占用 single-consumer 角色，不能作为旁路 observer 和 scheduler 线程上的 `try_receive/receive` 并行使用。
- 当前 scheduler/manager 没有现成“外部线程把 host/thread 标为 ready 并唤醒 scheduler”的 API；`Host` 内部大量 `RefCell/RootedRc` 假定由 scheduler worker 持有，helper thread 不能直接操作 `Host` 或 `EventQueue`。
- run-control 的 `Condvar` 和 scheduler pool 的 `ThreadUnparker` 只负责 window boundary 或线程池任务启动，不是 simulated-time readiness 层。
- helper thread 方案如果继续，需要新增一个小的 readiness bridge：helper 只能写 `{host_id,pid,tid,seq}` 到独立队列；manager/scheduler 按确定性顺序 drain，真正操作 host 的仍必须是 scheduler worker。
- CP/restore 边界：checkpoint 前必须保证没有 helper 未 join、没有 pending token、没有 ready-but-unconsumed reply、没有丢失 wake event；否则仍会违反 `Parked + IPC empty + current_event updated` 的静止点。
- 因此下一条候选不是“直接 helper 操作 host”，而是两种更小的方向：
  1. 窗口级批量 drain：pending 后先让 worker 处理其它 host，window/scope 末尾集中完成 pending reply。
  2. 新增确定性 readiness bridge：helper 只等待 futex ready 并提交 token，manager 统一排序后投递 continuation。

## 2026-05-31 新分支：native reply batch drain/readiness bridge

- 从当前稳定分支切出 `explore/native-reply-batch-drain-20260531`，TDT 与 `deps/shadow` 同名。
- 本阶段只探索 native reply 等待解耦，不展开 runahead、网络延迟、应用层配置或其它调参路线。
- 主矛盾：setup8 中 `continue_receive` 占 worker body 约 `59%`，最慢 worker 可达 `79%`；但同一 simulated time retry 已被证明会忙轮询。
- 当前可接受方案必须满足三条：
  1. 不改变应用层配置和模拟语义。
  2. checkpoint 前能回到 `Parked + IPC empty + current_event updated` 静止点。
  3. 不重放 local task，尤其不能重复 `StartApplication`。
- 下一步先读代码确定最小插入点：是否能在一个 scheduler scope/window 末尾按确定性顺序 drain pending native reply；如果插入点不成立，再考虑小型 readiness bridge。

## 2026-05-31 batch drain 最小原型

- subagent 与本地审计结论一致：可试插入点在当前 scheduler scope/window 内 host 扫描之后、`min_next_event_time` 与 run-control/checkpoint 边界之前。
- 该插入点比 same-time retry event 更安全，因为不会重放 `TaskRef`，特别是不会重放 `StartApplication` closure。
- 原型增加 `SHADOW_TDT_BATCH_DRAIN_NATIVE_REPLY=1` 开关；默认关闭时继续同步 `continue_plugin`，不改变默认语义。
- 原型思路：`ManagedThread` 发送 shim event 后先 `try_receive`，如果 reply 未就绪则保存 `NativeRunToken` 并返回 `NativeReplyPending`；`Host` 记录 `(pid, tid)`；worker 扫完 hosts 后 drain pending reply，再调用 `Host::resume(pid, tid)` 继续原 thread state machine。
- 这个原型仍不引入 helper thread，因此不涉及外部 readiness 排序；它只测试“把阻塞点推迟到本 worker host 扫描尾部”是否能减少关键路径等待。
- 编译验证：
  - `cargo test --manifest-path src/Cargo.toml -p shadow-rs --lib host::managed_thread` 通过；0 个测试实际执行，166 filtered out。
  - `cargo fmt` 在 `src/main` 成功。
  - `./setup build` 通过，生成 release shadow。

### batch drain opt-in 失败记录

- 第一次 setup8 opt-in：`/tmp/tdt-batch-drain-on-s8`，快速失败，`Host::unlock_shmem()` 断言。原因是 `StartApplication` 仍使用旧 `TaskRef::new_with_descriptor`，吞掉 `host.resume()` 的 `NativeReplyPending` 返回。
- 补齐 `StartApplication` pending 返回后，setup1：`/tmp/tdt-batch-drain-on-s1-fix1` 不再 panic，但 Shadow 在约 4 秒模拟时间早停，应用仍 running。诊断显示 pending 被记录，但没有被 drain。
- 进一步定位：同一个 `HostIter` 第一轮遍历会把 host 从当前队列转移到下一队列，第二轮 `for_each_host` 访问不到 host；这是 scheduler API 使用错位。
- 改成第一个 scheduler scope 完成后启动第二个 scope drain，setup1：`/tmp/tdt-batch-drain-on-s1-scope2`。这次 drain 发生了，但 geth 在同一个 simulated timestamp 反复 `pending recorded -> pending finish`，本质上又变成同一 simulated time busy loop，最终触发线程状态 unwrap。
- 结论：没有外部 readiness 通知时，窗口尾部 batch drain 也不能成立；它只是把 busy polling 从 event retry 移到了 drain loop。该原型判负，不提交 shadow 代码。
- 保留 insight：真正可继续的路线必须是 readiness bridge，也就是让等待发生在不占用 scheduler critical path 的 helper/通知层，scheduler 只在 reply ready 后按确定性顺序恢复 host。

### 回退后稳定性验证

- 已反向应用本轮失败原型和 `cargo fmt` 造成的所有 shadow tracked diff；shadow tracked clean，仅保留既有 untracked `src/test/signal/shadow.data/`。
- 重新 `./setup build`，确保 binary 回到稳定源码。
- 默认 setup8 guard：`/tmp/tdt-after-batch-revert-s8`，`passed=true`。
- 性能点：elapsed `12.40s`，sim/wall `29.04x`，steady `31.33x`，checkpoint `194.36 ms`，restore `332.82 ms`。
- 语义检查点：post-restore 后 8 个 beacon 均达到 `Synced new block=26` / `Finished applying state transition=26`，geth `Chain head was updated=26`，说明测试网继续推进。
- 方向调整：本分支继续只考虑 readiness bridge；不再尝试没有 readiness 的 same-time retry 或 batch drain。

## 2026-05-31 readiness bridge 进入设计审计

- 已启动只读 subagent 审计 readiness bridge 的最小实现边界。
- 本地当前判断：helper thread 不能消费 `from_plugin` message，只能调用 `wait_ready_assuming_single_consumer()` 等待 ready；真正 `receive` 和 `current_event` 更新仍必须由 scheduler worker 在 host 上下文中完成。
- ready token 至少需要包含 `{host_id, pid, tid, deterministic_seq}`；跨 host 恢复顺序必须由 manager/scheduler 排序，而不能按 OS helper wake 顺序直接执行。
- checkpoint 前必须能证明：
  1. 没有 helper 正在 wait；
  2. 没有 ready-but-unconsumed token；
  3. 没有 `pending_native_run`；
  4. IPC channel 为空且 `current_event` 已更新。
- 因此 readiness bridge 不能只是“开线程 wait”；还需要一个明确的 manager boundary drain/barrier。

### readiness bridge subagent 结论

- 可以捕获 `Arc<IpcShmem>` 到 helper thread；不要捕获 `&from_plugin()` 引用。`IpcShmem` 和 `SelfContainedChannel<T>` 满足跨线程使用条件。
- helper safety 条件：helper 调 `wait_ready_assuming_single_consumer()` 期间，scheduler/worker 不能同时对同一 `from_plugin` 调 `receive/try_receive/wait_ready`。
- ready token 应放在 manager 级 receiver；`WorkerShared` 只提供 sender 和 pending counter；`ManagedThread.pending_native_run` 保留权威 pending 状态。
- token 只携带身份和排序字段：`{host_id, pid, tid, generation, event_time}`，不能携带 Host/Thread/ManagedThread 对象。
- ready continuation 不能作为 same-time local event 入队；应在 scheduler pass 返回后、`min_next_event_time` 和 run-control/checkpoint 前，用专用 scheduler pass 找 host 并直接完成 continuation。
- checkpoint 前必须执行同一套 barrier：等待所有 helper ready，完成 receive/finish/current_event update，直到 pending counter 为 0。
- 因此下一步原型只接受单一默认关闭开关 `SHADOW_NATIVE_REPLY_READINESS_BRIDGE=1`；默认路径完全保持 blocking `continue_plugin`。

## 2026-05-31 22:35 readiness bridge 原型阶段结论

- 主矛盾：native thread continue 的阻塞等待限制并发度。当前只探索 readiness bridge，不展开 runahead/网络延迟调参。
- 本轮改动：在 shadow 内部增加 opt-in `SHADOW_NATIVE_REPLY_READINESS_BRIDGE=1`；将 native reply 等待拆成 pending/ready/finish 三段；补齐 StartApplication、ExecContinuation、C task `host_continue()` 对 `NativeReplyPending` 的返回传播。
- 关键失败修复：第一次 setup1 因 `TaskRef::new`/C wakeup 吞掉 pending，导致 host shmem lock 状态崩坏并在 `Process::resume` unwrap panic；新增 Host 内部 ffi task result bridge 后 setup1 通过。
- 验证：`cargo test --manifest-path src/Cargo.toml -p shadow-rs --lib host::managed_thread` 通过；`./setup build` 通过。
- 功能结果：opt-in setup1 `/tmp/tdt-readiness-bridge-s1-fix2` 通过；opt-in setup8 `/tmp/tdt-readiness-bridge-s8-fix2` 通过，post-restore 共识语义正常推进到 block 27。
- 性能结果：setup8 readiness bridge elapsed=34.65s, sim/wall=10.39x, steady=10.66x；最近稳定默认对照 `/tmp/tdt-after-batch-revert-s8` elapsed=12.40s, sim/wall=29.04x, steady=31.33x。
- 结论：这个具体 per-native-run helper thread 实现功能可行但性能显著退化，不能进入主线。当前信号指向 helper thread 创建/唤醒/调度成本远大于释放的等待重叠收益。下一步只考虑 bounded waiter pool/更轻量 readiness 通知；如果仍无法解释正向收益，应回退该路线。

### 默认关闭 guard

- 补丁默认关闭时重跑 setup8：`/tmp/tdt-readiness-bridge-defaultoff-s8`，`passed=true`。
- 性能：elapsed=12.35s, sim/wall=29.16x, steady=31.37x，和稳定对照 `/tmp/tdt-after-batch-revert-s8` 的 12.40s / 29.04x / 31.33x 等价。
- 结论：当前未提交补丁在 flag 关闭时没有明显污染主功能或主性能；性能退化集中在 opt-in readiness bridge 的 per-run helper thread / barrier 实现。

### fixed waiter pool 试错结论

- 固定 waiter pool 原型：`cargo test --manifest-path src/Cargo.toml -p shadow-rs --lib host::managed_thread` 通过，`./setup build` 通过。
- opt-in setup1 `/tmp/tdt-readiness-pool-s1` 通过。
- opt-in setup8 `/tmp/tdt-readiness-pool-s8` 通过，但性能进一步退化：elapsed=37.21s, sim/wall=9.67x, steady=9.92x，checkpoint=178.10ms，restore=357.84ms。
- 对比 per-run helper：`/tmp/tdt-readiness-bridge-s8-fix2` elapsed=34.65s, steady=10.66x；对比默认关闭稳定点 `/tmp/tdt-readiness-bridge-defaultoff-s8` elapsed=12.35s, steady=31.37x。
- 结论：退化不是单纯线程创建/销毁成本；固定 waiter pool 仍被 head-of-line blocking、全局 barrier drain、以及 host.execute 批处理被切碎主导。helper/waiter bridge 路线不能作为可采纳优化，下一步应回退该原型，转向 eventfd/IPC readiness notification 或更根本的边界压缩设计。

### counters-off 历史候选复测

- 已先回退被否定的 readiness/waiter 未提交原型，patch 备份在 `/tmp/shadow-readiness-waiter-rejected-20260531.patch`。shadow tracked clean，仅保留既有 untracked `src/test/signal/shadow.data/`。
- 当前分支 `explore/native-reply-batch-drain-20260531` 回退后重建，setup8 三轮 counters-off：`/tmp/tdt-current-reverted-s8-t3`，passed=true，steady=31.06x，checkpoint median=173.49ms，restore median=361.14ms。
- 历史最高 suite 绑定的 shadow `33237a152` 复测三轮 counters-off：`/tmp/tdt-shadow33237-s8-t3`，passed=true，steady=30.15x，checkpoint median=184.93ms，restore median=350.82ms。没有超过当前，也没有复现历史 full-suite 单次 34.34x。
- phase2 token/guard 稳定点 shadow `f912a8a58` 复测三轮 counters-off：`/tmp/tdt-shadowf912-s8-t3`，passed=true，steady=30.05x，checkpoint median=176.60ms，restore median=368.82ms。也没有超过当前。
- 结论：历史 full-suite `/tmp/tdt-suite-default-off-full-r2-20260531` 的 setup8 steady=34.34x 仍是现存最好完整 suite 记录，但当前同机复测两个相关 shadow 提交均只有约30x；更像单次宿主噪声/短窗口优势，不足以作为回滚目标。当前应保持回退后的当前分支，并继续寻找真正结构性优化；不要为追 34.34x 盲目切回旧提交。

### dd41cdd 完整环境复测

- 按用户要求恢复到 PROGRESS-2 里 34.34x 对应的完整环境：TDT checkout 到 `dd41cdd`，submodule `deps/shadow` checkout 到 `33237a152`，并重新 `./setup build`。
- 复测1：`/tmp/tdt-dd41cdd-exact-s8-r1`，passed=true，setup8 counters-off steady=31.97x，elapsed=12.00s，checkpoint=172.06ms，restore=381.60ms。
- 复测2：`/tmp/tdt-dd41cdd-exact-s8-r2`，passed=true，setup8 counters-off steady=31.21x，elapsed=12.30s，checkpoint=181.95ms，restore=362.55ms。
- 对照历史 artifact `/tmp/tdt-suite-default-off-full-r2-20260531/performance/perf-model.json`：setup8 steady=34.34x，elapsed=11.14s，checkpoint=154.53ms，restore=327.57ms。
- 结论：即使恢复到同一 TDT commit 和同一 Shadow commit，两次复测仍没有复现 34.34x。当前证据支持把 34.34x 视为一次历史高点/宿主状态波动，而不是稳定可恢复的代码版本性能。复测后已切回当前分支 `explore/native-reply-batch-drain-20260531`，submodule 回到 `6622755f5`，并重新 build 当前 binary。

## 2026-05-31 历史高性能稳定版本探索

- 决策：本轮目标从“继续优化”切换为“在历史 Shadow commit 中寻找相对高性能且稳定的版本”。
- 主矛盾：历史 34.34x 是否对应稳定代码版本，还是宿主状态/短窗口波动。
- 方法：先固定当前 TDT harness，不改应用层配置，只切 `deps/shadow` 历史 commit；setup8 counters-off 单轮筛选，候选再做多轮复测和 determinism gate。

- 单轮筛选结果：所有候选 setup8 counters-off 均 passed=true。最高两名为 `f912a8a58` steady=33.40x、`44204a009` steady=33.39x；当前点 `6622755f5` steady=33.09x；`a0f3d7f41` 明显较低为31.82x。
- 决策：不追逐单轮 33.x，进入多轮复测；只对 `f912a8a58` 和 `44204a009` 跑 setup8 counters-off 三轮。

- 三轮同口径复测：`33237a152` setup8 steady=31.86x；`44204a009` steady=31.45x；`f912a8a58` steady=31.36x；当前 `6622755f5` steady=31.08x。
- 决策：目前最好的稳定候选是 `33237a152`，不是单轮最高的 `f912a8a58/44204a009`。下一步给 `33237a152` 跑真实客户端 determinism setup1/4/8 gate。

- 稳定性 gate：候选 `33237a152` 真实客户端 determinism setup1/setup4/setup8 全 PASS，结果目录 `/tmp/tdt-hist-33237-real-gate`，suite 输出 YES。
- 收尾：探索后将 `deps/shadow` 切回当前分支对应 commit `6622755f5` 并重建 binary，避免历史探索污染当前默认运行环境。

## 2026-06-01 候选版本与 main 对比

- 核查结果：TDT `main` 的 `deps/shadow` submodule 已经指向 `33237a152`，这正是本轮筛出的相对高性能稳定候选。
- 当前探索分支 `explore/native-reply-batch-drain-20260531` 的 `deps/shadow` 指向 `6622755f5`，三轮 setup8 steady=31.08x，低于候选 `33237a152` 的31.86x。
- 决策：不 merge 当前探索分支到 `main`，因为这会让 main 离开更好的候选版本。当前无需合并；从 TDT main 角度，候选版本已经在 main 上。
