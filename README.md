# 节点设备插件声明与独占分配庭

本项目是离线云原生业务控制面模拟。使用 Python 标准库和 SQLite；数据、身份与时间均为合成 fixture，不操作真实集群。

## 三个业务子系统

### 设备发现庭

仅可信插件代际报告的健康物理设备进入供给池。持久化：plugins,devices,health_samples 分别保存身份、代际、状态与相互引用的决定凭据。

入口：`python -m devicecourt inventory --db state.sqlite --fixture fixtures/inventory_normal.json`。

### 资源请求庭

申请的拓扑、型号、租户额度共同决定能否保留设备。持久化：requests,allocations,quota_ledgers 分别保存身份、代际、状态与相互引用的决定凭据。

入口：`python -m devicecourt allocate --db state.sqlite --fixture fixtures/allocate_normal.json`。

### 启动验收庭

启动回执绑定实际设备 UID 和分配代际才算已交付。持久化：start_receipts,detach_receipts,recovery_cases 分别保存身份、代际、状态与相互引用的决定凭据。

入口：`python -m devicecourt runtime --db state.sqlite --fixture fixtures/runtime_normal.json`。

## 首版业务能力

1. **插件代际登记**：插件代际登记 的独立裁定：插件 UID 与代际递增后接纳设备清单。失败边界：插件代际登记 的异常输入或迟到事实：旧进程心跳不可复活被换掉的设备
2. **设备物理身份核验**：设备物理身份核验 的独立裁定：PCI 地址、序列身份与拓扑位置共同确定设备。失败边界：设备物理身份核验 的异常输入或迟到事实：同槽新卡须新设备 UID
3. **健康降级隔离**：健康降级隔离 的独立裁定：连续来源样本与致命故障区分暂缓与隔离。失败边界：健康降级隔离 的异常输入或迟到事实：单次可恢复噪声不能永封设备
4. **租户请求判定**：租户请求判定 的独立裁定：请求型号与设备拓扑、租户优先权共同决定候选池。失败边界：租户请求判定 的异常输入或迟到事实：跨拓扑组合不得假装可满足
5. **设备独占保留**：设备独占保留 的独立裁定：保留事务排除并发重复占用并记录代际。失败边界：设备独占保留 的异常输入或迟到事实：同一物理 UID 不得双重分配
6. **配额返还裁定**：配额返还裁定 的独立裁定：只有解绑证据与旧分配关闭后返还额度。失败边界：配额返还裁定 的异常输入或迟到事实：虚报释放不能扩大可用配额
7. **容器启动确认**：容器启动确认 的独立裁定：运行时回执需指向保留的设备 UID 与插件代际。失败边界：容器启动确认 的异常输入或迟到事实：插件重启后的旧回执不能完成交付
8. **故障设备迁离**：故障设备迁离 的独立裁定：故障时冻结绑定并以归还回执决定能否重新分配。失败边界：故障设备迁离 的异常输入或迟到事实：未清退的旧容器不得与新容器共享设备

## 本地运行与验证

在仓库根目录运行：

```sh
python -m devicecourt demo --db demo.sqlite
python -m devicecourt replay --db demo.sqlite
python -m unittest discover -s tests -v
```

每个子系统提供一份独立正常 fixture 与一份实质失败 fixture；`fixtures/path.json` 串联跨子系统场景。每条命令保留原始输入、决定与不可变事件，`replay` 在空数据库重建状态并逐表核验。

## 业务边界

- **设备发现庭 → 资源请求庭**：插件 plugin-a 代际 3 认领 GPU-7 且健康，资源请求 req-1 可把该物理 UID 放进候选；插件重启为代际 4 后旧候选失效。失败分支：同槽位的新卡 GPU-8 不得沿用 GPU-7 的健康历史与分配资格
- **资源请求庭 → 启动验收庭**：req-1 为租户 t1 独占 GPU-7 后启动庭只接受同代设备回执；设备故障隔离会冻结后续请求和当前交付。失败分支：旧插件代际的启动回执不得让隔离中的 GPU-7 看起来交付成功
- **设备发现庭 → 启动验收庭**：GPU-7 的解绑回执与分配关闭共同使配额返还，另一租户 req-2 才能保留同一设备。失败分支：缺解绑证据时虚报释放不得同时满足 req-1 和 req-2

该仓仅实现本地业务状态与回执协议；不连接真实 Kubernetes、设备插件、时间服务或告警平台。

## 板内算力切片

用 `python -B -m devicecourt allocate --fixture fixtures/compute_slices.json --db slices.sqlite` 演示板卡持有人细分算力。
发现层公布同代设备的合法计算单元形状。分配端以实际位置排除重叠，并在首次提交时选择最靠前的可用形状。
