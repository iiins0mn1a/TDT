# Up-to-Date 仓库更新进度

## 2026-06-01 01:20 启动记录

这份记录单独追踪 `up-to-date` 分支的仓库更新工作，和此前性能优化记录分开。它的用途是让人快速判断：当前是否还保护着稳定 `main`，新版 geth/Prysm 升级推进到哪里，Shadow upstream 的改动有没有被安全吸收。

当前主线已经切换：不再继续性能优化 goal，而是先保住稳定基线，再在隔离分支探索新版 real clients。TDT 已经从 `origin/main` 创建并推送 `up-to-date`，Shadow fork 也已经从稳定 commit `33237a152` 创建并推送 `up-to-date`。

稳定基线目前没有被替换。TDT `main` 仍然指向 Shadow `33237a152`，这是前一轮筛出的相对高性能稳定候选；当前升级工作不会直接合入 `main`。

已经新增两个版本锚定的实验 client submodule：`deps/go-ethereum-v1.17.3` 指向 geth `v1.17.3`，`deps/prysm-v7.1.4` 指向 Prysm `v7.1.4`。旧的 `deps/go-ethereum` 和 `deps/prysm` 保留，继续作为 baseline。

Shadow upstream 已经拉取。初步判断是不能整体 merge 官方 `main`，因为它和我们的 checkpoint/restore、run-control、legacy TCP restore 改动分歧很大；后续只考虑 cherry-pick 小块、低风险、对新版 client 兼容有帮助的改动，例如 resolver 或 scheduler syscall 支持。

下一步先解决构建边界。当前系统 Go 是 `1.23.0`，而新版 geth 需要 `go 1.24.0`，新版 Prysm 需要 `go 1.25.1`；因此需要先确认本机是否已有可用的新 Go/toolchain，或者通过项目局部方式安装，再开始构建新版 binaries。

## 2026-06-01 01:15 Client binaries 准备

新版 Prysm 先采用官方 release binaries，而不是本地 Bazel 构建。原因是本机只有 `bazelisk` 没有 `bazel`，且 Prysm `v7.1.4` 要求 `go 1.25.1`；为了尽快得到可运行实验对象，先用 `prysm.sh` 在源码 submodule 对应 tag 下下载并校验官方二进制。

Prysm 三个二进制已经下载到 `deps/prysm-v7.1.4/dist/`，版本为 `v7.1.4`，签名校验通过。`beacon-chain` 和 `validator` 的 `--version` 可正常输出；`prysmctl` 不支持 `--version`，这只是探测方式不兼容，不代表下载失败。

geth `v1.17.3` 已经从源码构建到 `deps/go-ethereum-v1.17.3/build/bin/geth`。构建时 Go 自动下载并使用了 `go1.24.0` toolchain，满足 geth 的版本要求。

当前发现一个版本元数据异常：`geth version` 显示的 Git commit 像是父 TDT 仓库的 commit，而不是 geth tag commit。源码 submodule 本身确认在 `117e067f0f0bae1a17082321f224dedb6765b10f`，所以先把它记录为构建元数据问题，不把它当作功能阻塞。

`tdt_config.up_to_date.toml` 已改为指向这些新版二进制。下一步是让 TDT 用该配置完成 prepare/smoke，观察新版 client 是否在现有 genesis 与启动参数下兼容。

## 2026-06-01 01:15 进度记录规则确认

接下来这份文件作为新版仓库更新的独立观察窗口，只记录 `up-to-date` 分支相关的计划、执行、失败和修正。它不混入旧性能优化记录，也不替代最终报告；它的作用是让你快速知道我现在为什么做某个动作，以及这个动作有没有推进升级目标。

记录会保持 append-only。每条记录先写判断，再写证据和下一步；如果某个试错失败，会明确写失败点和保留价值，避免把终端输出里的噪声当成进度。

当前执行边界也一并固定：稳定 `main` 不被直接改动，新版 geth/Prysm 只在 `up-to-date` 分支和新增 submodule 中推进；Shadow upstream 不做大合并，只在新版 client 运行需要时考虑小范围 cherry-pick。

## 2026-06-01 01:17 Genesis 配置冲突处理

第一次新版 smoke 没有进入 Shadow，而是在 `prysmctl testnet generate-genesis` 阶段失败。错误指向 Prysm v7 的 configset 冲突：自定义 interop 配置没有显式给出 Fulu/Gloas fork version，Prysm 从 mainnet 默认配置继承了 `0x06000000`，于是和内置 mainnet schedule 撞车。

这不是 checkpoint/restore 或 Shadow 网络层问题，而是新版 Prysm 对链配置字段的要求更完整。源码确认 `UnmarshalConfig` 在 `PRESET_BASE` 不是 minimal 时会先用 `MainnetConfig()` 作为底，再用 YAML 覆盖；因此缺失的新 fork 字段会保留 mainnet 默认值。

修正方式是隔离式适配：新增 `assets/config.up_to_date.yml`，只给新版 client 路线使用；同时给 TDT 配置增加可选的 `chain_config` / `genesis_json` 输入。默认 `assets/config.yml` 不被覆盖，旧 baseline 仍能按原路径运行。新版配置继续保持 Deneb 从 genesis 激活，Electra/Fulu/Gloas 放到 far-future，并显式使用不与 mainnet 冲突的 fork version。

下一步重新跑 `tdt_config.up_to_date.toml` 的 smoke，确认是否能越过 genesis 进入 Shadow 启动阶段。

## 2026-06-01 01:18 Genesis JSON 格式升级

第二次新版 smoke 已经越过 Fulu/Gloas 配置冲突，但仍停在 `prysmctl testnet generate-genesis`。新的错误是 `baseFeePerGas must be set in genesis.json for Post-Merge networks`，说明 Prysm v7 对 execution genesis 的 post-merge 字段校验比旧版本更严格。

这仍然不是 Shadow 运行失败，而是新版 real clients 对输入资产格式的兼容问题。当前 `assets/genesis.json` 里 `baseFeePerGas`、`excessBlobGas`、`blobGasUsed` 都是 `null`；旧版本能容忍，Prysm v7 不再接受至少 `baseFeePerGas` 为空。

下一步继续使用隔离适配：新增 `assets/genesis.up_to_date.json`，只给 `tdt_config.up_to_date.toml` 使用。计划把 `baseFeePerGas` 设为 London 初始基准值 `0x3b9aca00`，并把 blob gas 字段设为 `0x0`，避免新版 geth/Prysm 在 Cancun/Deneb 路径上遇到空字段。

## 2026-06-01 01:19 Geth v1.17 genesis 校验

第三次新版 smoke 已经成功生成 `genesis.ssz`，说明 Prysm v7 的链配置和 execution genesis 输入已经基本可读。新的阻塞点移动到了 geth `v1.17.3 init`，错误是 `missing entry for fork "cancun" in blobSchedule`。

这个结果有两个含义。第一，Prysm 侧的前两个兼容问题已经被消掉；第二，新版 geth 对 Cancun/Deneb genesis 的 JSON schema 更严格，需要在 execution chain config 中显式提供 blob schedule，而旧 geth 版本不要求。

下一步检查 Prysm 生成后的 `/tmp/tdt-up-to-date-runtime/network/genesis.json` 和 geth v1.17 的 genesis schema，确定 `blobSchedule` 应该放在 `.config` 下的具体格式。修正仍然只落在 `assets/genesis.up_to_date.json` 或生成后补丁逻辑上，不触碰默认 baseline。

## 2026-06-01 01:27 共识语义异常定位

外部执行的 smoke 能正常启动 Shadow，4 个 beacon peer 也都写入了 `beacon_peers.txt`，说明前一个 `EPERM` abort 是 sandbox 执行噪声，不是新版 TDT 配置本身的问题。geth、beacon、validator 进程都能被 Shadow 拉起。

但这轮 smoke 不能算通过，因为日志语义不正常。validator 端显示 validator 已经 `ACTIVE`，同时 beacon 端持续报 `no active validator indices`，validator duties 无法初始化。这里的主矛盾已经从“能否启动进程”转成“新版 Prysm 是否按我们期望的 genesis 时间和 fork schedule 推进共识”。

关键线索是 Prysm v7 的 `prysmctl` 会优先使用 input `genesis.json` 里的旧 `timestamp`，而旧版本曾默认使用 `now()`。这导致 beacon 启动时认为 genesis 在 2026-04-14，当前已经是几十天后的 slot；再叠加从 mainnet config 继承的部分 schedule，slot processing 走到不一致状态。

修正策略是显式恢复旧行为：`tdt_config.up_to_date.toml` 打开 `use_current_genesis_time = true`，TDT 生成 consensus genesis 时传入当前 Unix 时间。同时新版链配置补齐 `MIN_GENESIS_ACTIVE_VALIDATOR_COUNT`、`MIN_GENESIS_TIME`、`GENESIS_DELAY`，并显式给出本地 `BLOB_SCHEDULE`，避免继续继承 mainnet 的 BPO epochs。

下一步重新 prepare/smoke，重点检查日志里是否还出现 `no active validator indices`，以及 validator duties 是否能初始化。

## 2026-06-01 01:39 新版 smoke 中途语义恢复

重新运行外部 smoke 后，Prysm genesis 明确使用当前 Unix 时间 `1780248630`，不再继承旧 input genesis 的 2026-04-14 timestamp。prepare 阶段继续通过，geth init 也继续通过。

中途日志显示共识语义已经恢复到可推进状态：`beacon_peers.txt` 有 4 个 peer，beacon 日志持续出现 `Synced new block` 和 `Finished applying state transition`，validator 日志出现 `Submitted new block`、`Submitted new attestations` 和 `Submitted new aggregate attestations`。之前的 `no active validator indices` 和 `validator duties are not initialized` 没有再出现。

目前仍需等完整 smoke 自然退出并运行 logcheck。中途结论只能说明新版 real clients 已经从“能启动但共识错误”推进到“能出块和投票”，还不能替代完整测试结果。

## 2026-06-01 smoke 结束语义检查

结论：新版本 geth/Prysm 的真实客户端 smoke 已经能形成有效共识语义，但当前还不能算作完整通过，因为 Shadow 的进程结束状态检查返回了失败。

证据：logcheck 显示 4 个 beacon 都完成 151 次 state transition 和 151 次 block sync，geth 更新链头 154 次，4 个 validator 都提交了 block 或 sync message；这说明前面修复的 genesis 时间、fork 配置和 blobSchedule 已经让链能跑起来。

失败点：Shadow 退出码为 1，直接原因是仿真结束时 geth 和 4 个 validator 仍在运行，被 Shadow 判定为 unexpected final state。这个问题更像 TDT smoke 编排没有声明长期服务进程的期望终态，而不是客户端兼容性或共识失败。

下一步：检查生成的 shadow.yaml 和 TDT 编排器，优先在模拟编排层修正长期服务进程的 expected end state，不修改应用层参数。

## 2026-06-01 修正 smoke 的结束状态语义

结论：下一处改动限定在 TDT 的 shadow.yaml 生成逻辑，不碰 geth/Prysm 参数，也不碰 Shadow 内核语义。

原因：geth 和 validator 是长期运行服务，smoke run 的目标是观察网络在指定时长内是否形成共识，而不是要求这些服务在 stop_time 自行退出。现有配置只给 beacon wrapper 声明了 shutdown/expected_final_state，遗漏了 geth 和 validator。

计划：为 geth 和 validator 添加与 beacon 一致的仿真结束清理语义：在 stop_time 前由 Shadow 发 SIGKILL，并将 SIGKILL 作为期望终态。这样 smoke 的通过条件会回到真实目标：运行期间的链上进展和日志语义。

## 2026-06-01 smoke 复测通过

结论：修正长期服务进程的结束状态后，up-to-date 客户端 smoke 已完整通过。

证据：Shadow 退出码从 1 变为 0，tdt_logcheck 的 verify-smoke 返回 PASS。4 个 beacon 各完成 151 次 block sync 和 state transition；geth 链头更新 155 次；4 个 validator 均提交 block，并持续提交 sync message。

影响范围：这次修正只改变 TDT 生成 shadow.yaml 时对 geth/validator 的 shutdown/expected_final_state 声明，不改变 geth、Prysm、链配置、网络延迟或 Shadow 内核语义。

下一步：进入 up-to-date 客户端的 cp/restore 验证，检查 checkpoint/restore 流程是否仍能保持真实客户端工作流完整。

## 2026-06-01 cp/restore 验证通过

结论：up-to-date 客户端组合已经通过一次真实客户端 checkpoint/restore 功能验证。

证据：control_cp_restore.py 完成 warmup、checkpoint、post-checkpoint、restore、post-restore 推进后返回成功；验证器输出 PASS。恢复后日志仍显示 4 个 beacon 推进 state transition 和 block sync，geth 更新链头，4 个 validator 都提交 block 或 sync message。

保留限制：这仍然沿用当前 TDT 里已知的外部状态边界，只回滚 managed external paths，即 network/ 和 beacon_peers.txt；host-side shadow.data rollback 仍不是这个里程碑的一部分。

下一步：整理本次迁移改动，提交并推送 TDT 的 up-to-date 分支，使新客户端适配状态可以恢复。Shadow 的 up-to-date 分支目前没有为新客户端额外改内核代码。

## 2026-06-01 远程保存

结论：本次 up-to-date 迁移成果已经保存到 TDT 远程分支。

证据：TDT 分支 up-to-date 已提交 abbaa7c，提交内容包括新 geth/Prysm submodule、up-to-date 专用链配置、genesis、TDT 配置、编排器兼容改动，以及这个独立进度文件。随后已推送到 origin/up-to-date，并设置本地分支跟踪该远程分支。

状态：Shadow 的 up-to-date 分支没有为了新客户端额外修改内核代码；当前只需要 TDT 侧的编排和配置适配即可让 smoke 与 cp/restore 通过。

## 2026-06-01 Shadow 分支确认

结论：Shadow fork 的 up-to-date 分支也已确认可恢复。

证据：deps/shadow 当前在 up-to-date，提交为 33237a152；push -u origin up-to-date 返回 Everything up-to-date，并将本地分支设置为跟踪 origin/up-to-date。TDT 本次适配没有引入新的 Shadow 代码改动。

备注：deps/shadow 内仍有未跟踪的 src/test/signal/shadow.data/ 测试产物，未纳入 TDT 提交。

## 2026-06-01 官方版本与配置依据复核

结论：当前 up-to-date 分支使用的 geth v1.17.3 和 Prysm v7.1.4 仍然符合“最新稳定客户端”的目标。

证据：geth 官方下载页显示当前 stable release 为 v1.17.3，commit 117e067f；Prysm GitHub releases 显示 v7.1.4 为 Latest，tag commit 1756380。TDT submodule 当前正好固定到这两个 commit。

配置含义：Prysm v7.1.4 release 明确包含 Gloas/Fulu 相关推进和性能修复；这解释了升级时必须显式声明后续 fork version/epoch，避免 testnet config 继承 mainnet 默认值造成 fork schedule 冲突。geth v1.17.3 对 Cancun/blobSchedule 的校验也要求 genesis JSON 明确包含 blobSchedule.cancun。

下一步：开始审 Shadow upstream 最近改动，只考虑能改善 Go real-client 兼容性、确定性或性能，且能用 TDT smoke/cp-restore 证明不回退的小范围补丁。

## 2026-06-01 Shadow upstream 候选筛选

结论：本轮只准备吸收 resolver numeric address 兼容补丁，不直接合并 upstream/main，也暂不吸收 scheduler/PDEATHSIG 类补丁。

原因：upstream/main 与当前 fork 的差异很大，直接 merge 会触碰 checkpoint/restore、run-control、TCP restore 等核心改动，风险不可接受。resolver 补丁只影响 shim getaddrinfo 的数字地址解析，属于无持久运行态的兼容性改动；对真实 Go 客户端和未来更多 client 有价值。

暂缓项：scheduler syscall emulation 和 PR_SET_PDEATHSIG 都对更多真实程序有潜在价值，但它们会新增或依赖 thread/process 运行态。当前 fork 的 checkpoint/restore 要求所有恢复关键状态都显式进入 snapshot，所以这些补丁需要单独建模和测试，不能作为低风险顺手 merge。

下一步：cherry-pick upstream PR #3742 的 resolver 补丁，构建 Shadow，并重跑 TDT smoke/cp-restore 守卫。

## 2026-06-01 Shadow upstream 小补丁落地

结论：Shadow up-to-date 分支现在吸收了两个低风险 upstream 补丁：resolver numeric IPv6/AI_V4MAPPED 兼容，以及 fcntl command enum logging。

具体提交：62d5b9c04 对应 upstream PR #3742，改善 shim getaddrinfo 对 ::1、IPv6 literal 和 AI_V4MAPPED 的行为；66fcc1535 对应 upstream 的 fcntl enum logging，使 syscall 日志更容易读。

风险控制：没有吸收 scheduler syscall emulation、PR_SET_PDEATHSIG、linux-api 7.0、Rust toolchain bump 等会引入 thread/process 状态或大面积 generated-code churn 的补丁。这样可以避免给 checkpoint/restore 增加未建模恢复态。

验证进度：补丁后 Shadow 已重新构建成功。resolver-only 时已跑过一次 smoke/cp-restore；加入 fcntl logging 后还需要重新跑最终 TDT 守卫。

## 2026-06-01 upstream 小补丁后的最终 smoke/cp-restore 守卫

结论：加入 resolver 和 fcntl logging 两个 Shadow upstream 小补丁后，up-to-date 真实客户端 smoke 与 cp/restore 仍然通过。

证据：最终 smoke 中 Shadow exit code 为 0，verify-smoke PASS；4 个 beacon 均完成 150 次 block sync/state transition，geth 链头更新 152 次，4 个 validator 均有 block 与 sync message。最终 cprestore 也 PASS，恢复后 beacon/geth/validator 日志仍持续推进。

语义边界：这仍证明的是恢复后继续推进，不等价于 determinism oracle。Subagent B 的审计指出，up-to-date clients 的确定性 replay 和完整 suite/perf 还需要单独补测。

下一步：先提交并推送 Shadow/TDT submodule 指针，保存当前可恢复状态；随后运行 up-to-date real-client determinism / suite / perf gate。

## 2026-06-01 远程保存 Shadow upstream 小补丁

结论：Shadow fork 的 up-to-date 分支已推送到远程，TDT 接下来会固定到这个新 Shadow gitlink。

证据：deps/shadow 当前 HEAD 为 66fcc1535，包含 62d5b9c04 resolver 补丁和 66fcc1535 fcntl enum logging 补丁；git push origin up-to-date 已完成。

下一步：在 TDT up-to-date 提交 deps/shadow gitlink 更新和本进度记录，然后开始 determinism/suite/perf 验证。
