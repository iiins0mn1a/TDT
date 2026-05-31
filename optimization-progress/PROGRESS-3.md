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

