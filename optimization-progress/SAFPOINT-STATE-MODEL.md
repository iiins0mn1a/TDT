# Checkpoint-safe Safepoint State Model

这份文档回答一个问题：如果要继续从 Shadow 的 managed continuation 边界拿性能，什么状态才允许被 checkpoint/restore，并且什么优化形态已经被证据排除。

当前性能主矛盾不是某个 syscall handler 慢，而是真实 native thread 从 Shadow 回复后运行到下一次 syscall/yield 的等待时间。这个边界有 overlap 机会，但它也是 checkpoint/restore 的语义边界；没有明确静止点定义时，简单 async 化会破坏 determinism。

## Current Evidence

已经降级的方向：

- `route-cache`：setup8 两轮 A/B 只有约 0.68% 差异，低于噪声。
- host 全量扫描：setup8 `scans/execute` 没有随规模恶化。
- syscall-condition trigger/check：setup8 lookup+satisfied 约 29.8ms，而 wake wall 约 20.7s。
- `host_continue` 包装层 residual：setup8 residual 约 1.77%，大头在 managed/native continuation。
- async syscall 白名单：socket-I/O、same-window tail-drain、getrandom 都暴露了语义风险。

关键正信号：

- scope-drain 观测中，setup8 有 17196 个 pending host drain 后仍存在 17196 个 `< window_end` 的 re-enter opportunity，说明 window fragmentation 是真实性能损失来源。

关键反信号：

- naive same-window tail-drain 让 setup8 determinism 失败，beacon replay 少了一批 `UpdateHead`。
- getrandom async 在 setup8 performance restore 后让 Shadow `code=-11` 退出，并出现超大内存分配失败。

## Code Boundary

当前 async continuation 状态跨三层：

- `ManagedThread::begin_async_continue()` 设置 `async_continue_pending=true`，写 shim clock/runahead，释放 host shmem lock，然后把 syscall completion 发给 shim。见 `deps/shadow/src/main/host/managed_thread.rs:1273`。
- `Host::record_async_continuation()` 把 `(pid, tid, time)` 加入 host 队列。见 `deps/shadow/src/main/host/host.rs:940`。
- `Host::drain_async_continuations()` 取出 pending continuation，设置当前模拟时间，调用 `complete_async_continuation()`，再 `resume()` 同一线程。见 `deps/shadow/src/main/host/host.rs:950`。
- `ManagedThread::complete_async_continue()` 从 shim 收下一事件，重新 `host.lock_shmem()`，读取 shim time，并更新 `current_event`。见 `deps/shadow/src/main/host/managed_thread.rs:1294`。
- `ManagedThread::runtime_snapshot()` 明确 assert checkpoint 时不能有 in-flight async continuation。见 `deps/shadow/src/main/host/managed_thread.rs:542`。
- manager 主 scheduler scope 在 scope-drain 模式下遇到 pending 会先返回 `None`，随后第二个 scope drain pending host。见 `deps/shadow/src/main/core/manager.rs:833` 和 `deps/shadow/src/main/core/manager.rs:966`。
- run-control 的 checkpoint/restore 命令要求 simulation paused；pause 本身也只在 window boundary 生效。见 `deps/shadow/src/main/core/run_control/socket_driver.rs:227` 和 `deps/shadow/src/main/core/run_control/socket_driver.rs:325`。

## Safe State Definition

一个 checkpoint-safe safepoint 必须同时满足：

1. 所有 `ManagedThread.async_continue_pending == false`。
2. 所有 host 的 async continuation queue 为空。
3. 所有 host shmem lock 处于 manager 预期状态，不能停在 `begin_async_continue()` 已 unlock、`complete_async_continue()` 尚未 relock 的中间态。
4. shim clock state 与 Shadow worker current time 一致，且 `max_runahead_time` 没有跨过 host 的下一事件或当前 window end。
5. 每个 managed thread 的 `current_event` 已经是 Shadow 可 checkpoint 的稳定事件，而不是“syscall completion 已发给 shim、下一事件还没收回”的半状态。
6. manager 已经完成当前 window 内所有允许的 event queue 更新，`min_next_event_time` 反映 drain 后的真实下一事件。
7. 外部 run-control 只能在上述条件成立后进入 checkpoint/restore。

这些条件比“没有 pending 队列”更强。getrandom async 失败说明即使 syscall handler 本身低副作用，只要跨过 continuation/restore 边界，进程内存视图或 shim IPC 状态仍可能不一致。

## Rejected Shapes

不要继续尝试这些形态：

- 继续扩 `SHADOW_TDT_ASYNC_*` syscall 白名单。
- 在 manager 第二个 scope 中简单 `drain -> if next_event < window_end then execute`。
- 在不了解 `current_event` 和 shim memory 状态的情况下，把 handler 看起来“纯”的 syscall 标为 async。
- 用 setup8 单轮 throughput 微小上升作为保留依据；必须先过 setup8 determinism。

## Viable Next Experiment

下一步如果继续 safepoint/overlap，应先做观测，不直接改行为：

1. 在 checkpoint 前和每个 window boundary 前记录 async quiescence audit：
   - pending host 数；
   - pending managed thread 数；
   - pending continuation 的 syscall/event kind；
   - drain 后是否仍有 `< window_end` 的 host event；
   - drain 后 `current_event` 是否稳定在 `Syscall`、`StartReq`、`ProcessDeath` 等可 snapshot 状态。
2. 把 audit 接入 TDT perf report，先跑 setup8 default、socket-I/O scope-drain、inline-drain 三组。
3. 只有当某类 continuation 在 reference/replay 中满足同一 quiescent state，并且不改变 setup8 determinism，才允许做行为原型。

这个实验的目标不是立刻变快，而是缩小可安全 overlap 的集合。没有这个集合，继续做行为优化会重复 tail-drain/getrandom 的失败。
