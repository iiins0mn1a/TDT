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
