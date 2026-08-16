# Day 3: 原訂驗收在這個版本上表達不出來,以及改驗什麼

![wasmCloud 官方標誌](../assets/logos/wasmcloud-icon-color.svg){ align=right width="88" }

> [Day 2](sprint3-day2-wasmcloud.md) 證明了 wasmCloud 不碰節點,也量到它把代價換到哪裡。今天要走它的分散式模型——結果第一步就撞牆:**原本要驗的東西,在 2.6.1 上根本沒有對應的機制。** 這一章有一半在講怎麼判定「表達不出來」,以及改驗什麼才算數。

!!! abstract "你在課程的哪裡"
    - **Day 2**:wasmCloud 裝起來、跑了一個元件、節點五份 diff 零差異。
    - **今天**:加第二台節點,走完 wasmCloud 的分散式模型。**原訂驗收改寫過**,理由在步驟 1。
    - **Day 4**:換 WasmEdge,第二條路線。

原訂的驗收是「**同一個元件不換映像,換掉它的能力提供者,行為隨之改變**」。這是 wasmCloud v1 世代最著名的賣點:元件與能力解耦,link definition 把兩邊綁起來,想換 Redis 換成 NATS 就換一個 provider。

**在 2.6.1 的 Kubernetes 整合上,這件事沒有任何可設定的入口。** 判定過程本身比結論有用,所以放在第一步。

## 今天要走的路

| 步驟 | 做什麼 | 為什麼排在這 |
|---|---|---|
| 1 | 查原訂驗收表達不表達得出來 | **判定「這個版本做不到」需要證據,不是試兩下沒成功就下結論** |
| 2 | 換一個真的會用到能力的元件 | Day 2 的 `hello-world` 只用 HTTP,驗不出能力這一層 |
| 3 | 驗收 (A):同一映像、只改能力設定 | 改寫後的第一半 |
| 4 | 加一台 spot 節點 | Day 2 [地雷 2](sprint3-day2-wasmcloud.md#mine-2) 的實際代價 |
| 5 | 驗收 (B):誰決定 workload 去哪 | 改寫後的第二半 |

## 步驟 1: 原訂驗收表達不表達得出來

原訂驗收需要三個 v1 世代的概念:**capability provider**(一個獨立、可抽換的能力實作)、**link definition**(把元件與 provider 綁起來的物件)、以及**換掉 provider 而不動元件**。逐項查,三層都要看。

### CRD 這一側:一個都沒有

```console
$ kubectl api-resources --api-group=runtime.wasmcloud.dev
NAME                  SHORTNAMES   KIND
artifacts                          Artifact
hosts                              Host
workloaddeployments                WorkloadDeployment
workloadreplicasets                WorkloadReplicaSet
workloads             ww           Workload

$ kubectl get crd -o yaml | grep -icE "\blink\b|capabilityprovider|\bprovider\b"
0
```

**五個 CRD、零命中。** 能力在 CRD 上的落點只有一個地方,而它長這樣:

```console
$ kubectl explain workloaddeployment.spec.template.spec.hostInterfaces.config
FIELD: config <map[string]string>
DESCRIPTION:
    <empty>
```

**整個能力設定面是一個沒有 schema、沒有說明、沒有驗證的字串對字串 map。** 這件事後面會造成兩次失敗([地雷 2](#mine-2)、[地雷 3](#mine-3))。

### 二進位檔這一側:多後端存在,但沒編進去

原始碼樹裡確實有一整套可抽換的後端:

```text
crates/wash-runtime/src/plugin/wasi_blobstore/{in_memory,filesystem,nats}.rs
crates/wash-runtime/src/plugin/wasi_keyvalue/{in_memory,filesystem,nats,redis}.rs
crates/wash-runtime/src/plugin/multiplex.rs
```

`multiplex.rs` 連選擇的鑰匙都寫清楚了:`config.backend`,預設 `in-memory`。

但**跑在 Kubernetes 裡的 host 是 `wash host` 這個子指令**,它註冊了哪些:

```rust
// crates/wash/src/cli/host.rs(v2.6.1)
    .with_plugin(Arc::new(plugin::wasi_blobstore::NatsBlobstore::new(&data_nats_client)))?
    .with_plugin(Arc::new(plugin::wasmcloud_messaging::NatsMessaging::new(…)))?
    .with_plugin(Arc::new(plugin::wasi_keyvalue::NatsKeyValue::new(&data_nats_client)))?
    …
#[cfg(feature = "wasm_component_model_implements")]
{
    cluster_host_builder = cluster_host_builder.with_multiplexed_plugins()?;
}
```

**每個能力介面各一個實作,全部是 NATS 版。多後端那一整套鎖在一個 cargo feature 後面。**

chart 的 values 自己承認過同型的事——`hostPlugins` 欄位的註解逐字寫著:

```yaml
# Requires a wash image built with the `host-component-plugins` feature
# (not enabled in release images yet); a host handed a plugin it cannot
# build fails to start.
```

### 綁定粒度這一側:提供者是 host 的啟動參數

```console
$ kubectl -n wasmcloud get deploy hostgroup-default -o jsonpath='{…args}'
   host
   --host-group=default
   --scheduler-nats-url=nats://nats.wasmcloud.svc.cluster.local:4222
   --data-nats-url=nats://nats.wasmcloud.svc.cluster.local:4222   ← 提供者的端點在這裡
   --oci-cache-dir=/oci-cache
   --http-addr=0.0.0.0:80
```

**綁定的粒度是 host group,不是元件、也不是某個 link 物件。** 要換提供者,唯一的入口是 chart 的 `runtime.hostGroups[].dataNatsUrl`——也就是**開第二個 host group 指到另一座 NATS**,而不是「把元件的 link 指到另一個 provider」。

### 裁決

!!! failure "原訂驗收:表達不出來"
    **「同一個元件不換映像、換掉它的能力提供者」在 wasmCloud 2.6.1 的 Kubernetes 整合上沒有可設定的入口。** 不是操作方式不對:CRD 沒有對應物件、host 二進位檔裡每個介面只編進一個實作、多後端在 cargo feature 後面、提供者端點是 host 的啟動參數。

    這一顆記成[地雷 1](#mine-1)。

**改成驗兩件它實際支援的事**,兩半都能給出是/否:

- **(A)** 同一個元件映像不換,**只改 `hostInterfaces[].config`**,行為隨之改變。
- **(B)** 跨 host 排程**由 operator 決定而非 kube-scheduler**,且能力提供者**跨 host、跨節點共享**。

## 步驟 2: 換一個會用到能力的元件,以及第一個 500

Day 2 的 `hello-world` 只用 `wasi:http`。改用 **blobby**——官方 examples 裡的 `wasi:blobstore` CRUD 檔案伺服器,是 2.6.1 有發佈成 OCI artifact 的四個元件之一(另三個是 `hello-world`、`qrcode`、`oci-registry`)。

它的 artifact 一樣是單層 `application/wasm`,290 KiB。world 只 import 一個東西:

```wit
world blobby {
  import wasi:blobstore/blobstore@0.2.0-draft;
}
```

第一版 manifest 照原始碼裡 multiplex 的寫法給 `backend: in-memory`:

```bash
cat > blobby.yaml <<'EOF'
apiVersion: runtime.wasmcloud.dev/v1alpha1
kind: WorkloadDeployment
metadata: { name: blobby, namespace: wasmcloud }
spec:
  replicas: 1
  template:
    spec:
      hostSelector: { hostgroup: default }
      kubernetes: { service: { name: blobby } }
      components:
        - name: blobby
          image: ghcr.io/wasmcloud/components/blobby:0.6.0
      hostInterfaces:
        - namespace: wasi
          package: http
          interfaces: [incoming-handler]
          config: { host: blobby.wasmcloud.svc.cluster.local }   # Day 2 mine 4's fix
        - namespace: wasi
          package: blobstore
          interfaces: [blobstore]
          version: 0.2.0-draft
          config: { backend: in-memory }
EOF
```

物件全綠、EndpointSlice 有位址。呼叫下去:

```console
$ curl -sS -D- -X POST --data-binary 'day3' http://blobby.wasmcloud.svc.cluster.local/day3.txt
HTTP/1.1 500 Internal Server Error
content-length: 0
```

**又是一個零位元組的錯誤,而 `kubectl` 那一側全部正常**——形狀跟 Day 2 [地雷 4](sprint3-day2-wasmcloud.md#mine-4) 一樣,但這次是 500 不是 404。線索一樣只在 host log:

```console
ERROR handle_http_request{http.method=POST http.uri=/day3.txt}:
      failed to invoke component err=ErrorCode::InternalError(Some("unauthorized"))
```

追查改從原始碼下手,把那個字串拿去 grep:

```console
$ grep -rn '"unauthorized"' crates/
crates/wash-runtime/src/plugin/wasi_blobstore/nats.rs:163
crates/wash-runtime/src/plugin/wasi_blobstore/nats.rs:203
crates/wash-runtime/src/plugin/wasi_blobstore/nats.rs:233
crates/wash-runtime/src/plugin/wasi_blobstore/nats.rs:254
```

**四處全在 NATS 版 blobstore。** 這本身就是一個確認:**實際被載入的是 NATS 那一個,不是 manifest 裡設定的 in-memory。**

根因在同一個檔案:

```rust
let buckets = match interface.config.get("buckets") {
    Some(buckets) => buckets.split(',').map(|s| s.to_string()).collect(),
    None => vec![],                                   // 沒設就是空的
};
…
if data.buckets.contains(container_name) { return Some(…) }   // allow
if is_write && data.read_only { return None }
None                                                          // 其餘一律拒絕
```

**`config.buckets` 是一份 allow-list,預設空的,也就是預設全部拒絕。** blobby 開的 container 叫 `blobby`,所以要寫 `buckets: blobby`。

**這個鍵在 CRD 裡沒有、在 chart values 裡沒有、在 blobby 的 README 裡也沒有**(README 只說 `wash dev` 會自動接上)。**唯一的來源是 host 的原始碼。** 記成[地雷 3](#mine-3)。

## 步驟 3: 驗收 (A)——同一個映像,只改能力設定 {#gate-a}

四個狀態,`image` 全程不變:

```console
=== 狀態 1:buckets 未設
  image  = ghcr.io/wasmcloud/components/blobby:0.6.0
  config = {"backend":"in-memory"}
  PUT /probe.txt -> 500      GET /day3.txt -> 500  body=

=== 狀態 2:buckets: blobby
  image  = ghcr.io/wasmcloud/components/blobby:0.6.0
  config = {"buckets":"blobby"}
  PUT /probe.txt -> 201      GET /day3.txt -> 200  body=day3-nats-jetstream

=== 狀態 3:buckets: some-other-bucket
  image  = ghcr.io/wasmcloud/components/blobby:0.6.0
  config = {"buckets":"some-other-bucket"}
  PUT /probe.txt -> 500      GET /day3.txt -> 500  body=

=== 狀態 4:改回 buckets: blobby
  image  = ghcr.io/wasmcloud/components/blobby:0.6.0
  config = {"buckets":"blobby"}
  PUT /probe.txt -> 201      GET /day3.txt -> 200  body=day3-nats-jetstream
```

**四個狀態的映像完全相同,行為在 500 與 200 之間來回四次。過。**

狀態 4 額外證明一件事:**資料在能力被撤銷、又重新授權之後還在**——它不在元件裡、也不在 host 行程裡,在 NATS 的 JetStream object store 上。

### 跟 shim 路線的對照

| | RuntimeClass + shim(Day 1／6) | wasmCloud `hostInterfaces`(今天) |
|---|---|---|
| 「這個工作負載能做什麼」寫在哪 | Pod spec 的 `securityContext` / `capabilities` / PSA,由 kernel 執行 | `hostInterfaces[].config`,由 host 行程執行 |
| 預設值 | 容器預設**有**一組能力,要靠設定收斂 | **預設全部沒有**:沒列進 `hostInterfaces` 的介面連不到,列了但沒授權的資源一律拒絕 |
| 拒絕的形狀 | `Permission denied`,syscall 層 | 元件收到字串 `"unauthorized"`,通常變成 HTTP 500 |
| 誰能驗證設定寫得對 | API server 的 schema + admission | **沒有人**——`config` 是 `map[string]string`,寫錯不會被擋 |

**「預設無能力」跟 [Day 0](sprint3-day0-wasm-concepts.md) 講的 wasm 沙箱模型是同一件事的延伸**:瀏覽器不敢讓網頁碰檔案系統,wasmCloud 也不讓元件碰它沒被明確授權的 bucket。差別在於**這一層的授權表達完全沒有型別**。

## 步驟 4: 加一台 spot 節點,以及 Day 2 那顆地雷的實際代價

```bash
az aks nodepool add -g <resource-group> --cluster-name <cluster> -n spot1 \
  --node-count 1 --node-vm-size Standard_D2as_v5 \
  --priority Spot --eviction-policy Delete --spot-max-price -1 \
  --node-taints kubernetes.azure.com/scalesetpriority=spot:NoSchedule
                                            實測 1 分 39 秒
```

### 加了節點,host 一顆都沒過去

```console
$ kubectl -n wasmcloud get pods -o custom-columns='POD:.metadata.name,NODE:.spec.nodeName'
hostgroup-default-…   <system-node>
hostgroup-default-…   <system-node>
hostgroup-default-…   <system-node>
nats-…                <system-node>
runtime-operator-…    <system-node>
```

**三顆 host 一顆都沒動**,而且兩件事同時成立:既有 pod 不會因為多了一台機器就被重新排程(Kubernetes 從來不做這件事),而就算重建,taint 也擋著。

```console
<system-node>    cpu  1877m (98%)
<spot-node>      cpu   335m (17%)      ← 只有 kube-system 的 DaemonSet
```

**「加一台節點來鬆綁 98%」的直覺在這裡失效**:那 98% 是既有 pod 的 request,新節點對它一點影響都沒有。

把 host group 擴到 4,逼出可見的錯誤訊息:

```console
Warning  FailedScheduling  default-scheduler
0/2 nodes are available: 1 Insufficient cpu, 1 node(s) had untolerated taint(s).
```

**一行訊息裡同時看得到 Day 2 的兩顆地雷**:隨需節點 `Insufficient cpu`([地雷 1](sprint3-day2-wasmcloud.md#mine-1) 的 overlay),spot 節點 `untolerated taint`([地雷 2](sprint3-day2-wasmcloud.md#mine-2) 的缺欄位)。**訊息沒有說是哪一個 taint**,要自己去 `describe node`。

### 先確認一次 chart 到底有沒有那些欄位

Day 2 grep 的是 repo 裡的原始碼。今天改 grep **已發佈的 chart** 與**已安裝的 rendered manifest**:

```console
$ helm show values oci://ghcr.io/wasmcloud/charts/runtime-operator --version 2.6.1 | wc -l
412
$ helm show values … | grep -cE "toleration|affinity|nodeSelector|topologySpread|priorityClass"
0

$ helm get manifest wasmcloud -n wasmcloud | wc -l
924
$ helm get manifest wasmcloud -n wasmcloud | grep -cE "toleration|affinity|…"
0
```

**412 行 values、924 行 rendered manifest,五個關鍵字全部零命中。** 而且硬給也不會有人告訴你——`--set runtime.tolerations[0].key=…` helm 會收下,template 不用它,不警告。

!!! warning "`grep -c … == 0` 不能單獨當證據"
    這一組 grep 第一次跑時忘了給 helm `KUBECONFIG`,指令其實失敗了,而 **`grep -c` 對空輸入照樣回 `0`**——看起來像「乾淨」,實際上是「沒查到」。

    上面的數字是補上 `wc -l` 確認真的取到內容之後重跑的。**任何拿「零命中」當證據的地方,都要同時證明輸入非空。**

### 補上 toleration 之後,四顆 host 全搬到 spot 節點

```console
$ kubectl -n wasmcloud patch deploy/hostgroup-default --type=strategic \
    --patch-file toleration-patch.json
$ kubectl -n wasmcloud get pods -o custom-columns='POD:.metadata.name,NODE:.spec.nodeName'
hostgroup-default-…   <spot-node>
hostgroup-default-…   <spot-node>
hostgroup-default-…   <spot-node>
hostgroup-default-…   <spot-node>
```

**四顆全部搬過去。** 節點佔用翻轉:隨需 98% → 59%,spot 17% → 70%。

原因不難理解:滾動更新要先建新 pod,隨需節點只剩 23m 排不下,spot 節點空著,`LeastAllocated` 就全塞過去了。

**但結果是整個 wasm 資料平面落在一台隨時可能被回收的機器上,而這是「為了讓 host 能用到 spot 節點」這個動作的直接後果。** 記成[地雷 5](#mine-5)。

要一節點一顆,得再 patch 第二個 chart 表達不出來的欄位(`topologySpreadConstraints`,maxSkew=1 over `kubernetes.io/hostname`):

```console
hostgroup-default-…   10.244.1.93    <spot-node>
hostgroup-default-…   10.244.0.117   <system-node>
```

**兩顆 host、兩台節點、一台一顆。代價:兩個 `kubectl patch`,兩個 chart 沒有入口的欄位。**

## 步驟 5: 驗收 (B)——誰決定 workload 去哪 {#gate-b}

把 blobby 從 1 個 replica 擴到 4:

```console
=== 擴之前:命名空間 pod 總數
5

--- workload 落在哪些 host
blobby-66c4b8bd46-64d67f789f       host-A
blobby-66c4b8bd46-65b7d69b5c       host-A
blobby-66c4b8bd46-d675945bc        host-B
blobby-66c4b8bd46-ddd6545c9        host-B

--- host 物件
longing-trail-7203      10.244.1.93    WORKLOADS 2      ← 在 spot 節點
spiritual-death-0162    10.244.0.117   WORKLOADS 2      ← 在隨需節點

--- 擴完之後:命名空間 pod 總數
5
```

**四個 workload、兩顆 host、兩台節點,而命名空間的 pod 數量從頭到尾是 5。**

kube-scheduler 對這四個 workload 一無所知——它們沒有經過 API server 的 Pod 資源、沒有 `nodeName`、沒有 QoS class。**決定它們去哪的是 operator**,判斷依據寫在 `Host` 物件的 `status` 裡,全部經 NATS 回報。

直接打每一顆 host pod 的 IP,再做一次方向明確的寫讀分離:

```console
  GET /day3.txt @ 10.244.1.93  (spot)   -> 200  body=day3-nats-jetstream
  GET /day3.txt @ 10.244.0.117 (system) -> 200  body=day3-nats-jetstream

  PUT /gateb.txt @ spot host   -> 201
  GET /gateb.txt @ system host -> 200  body=written-via-spot-node-host
```

**在 spot 節點的 host 上寫,在隨需節點的 host 上讀得到。過。** 能力提供者(NATS JetStream object store)對兩顆 host 是同一個。

### 排程是一次性的,不會重新平衡

同一個實驗跑兩次分佈不一樣:一次 3:1,一次 2:2。而擴完之後再加第三顆 host:

```console
$ kubectl get hosts.runtime.wasmcloud.dev -A
incompetent-birth-5131   10.244.1.183   WL <none>     ← 新來的,空的
longing-trail-7203       10.244.1.93    WL 2
spiritual-death-0162     10.244.0.117   WL 3
```

**新加入的 host 不會分到既有的 workload。** wasmCloud 沒有重新平衡;跟 Kubernetes 一樣,排程是一次性的決定。

### 把承載一半 workload 的節點整個刪掉

刪除 spot 節點池正好是驗證的機會——那台機器上有 2 顆 host:

```console
$ az aks nodepool delete …                          實測 36 秒

$ kubectl -n wasmcloud get workloaddeployment
blobby        4   True
hello-world   1   True

$ curl http://blobby.wasmcloud.svc.cluster.local/gateb.txt
200  body=written-via-spot-node-host      ← 從已經不存在的節點上寫進去的那一筆
```

**兩層復原各自成立**:host pod 是普通 Deployment,節點消失就重建(Kubernetes 做的);workload 跟著新的 host 重新排程,資料因為在 NATS 上而沒有跟著消失(wasmCloud 做的)。

**但耐久性的邊界要講清楚**:chart 內建的那顆 NATS 用的是 **emptyDir**,而它一直跑在隨需節點上。**今天證明的是「資料撐得過 host 消失」,不是「撐得過 NATS pod 消失」。**

## 誠實的差距

- **「換掉能力提供者」完全沒有正面實測。** 判定它表達不出來,靠的是 CRD 零命中、原始碼的 `with_plugin` 清單、以及一次反證。**沒有做的是**:開第二個 host group 指到第二座 NATS。那是這個版本上唯一真能換提供者的路徑,今天只有讀 values 得到的推論。
- **多後端有沒有被編進 release 映像,沒有直接驗證。** 證據是原始碼的 `#[cfg(feature = …)]` 加上 `backend` 鍵無效果。沒做的是在 host pod 裡確認 feature flag 狀態。「設了沒反應」也可能是別的原因,兩者結論相同但推理強度不同。
- **NATS 掛掉資料會怎樣,沒有測。** 內建 NATS 用 emptyDir,理論上 pod 一重建資料就沒了,**但這個推論沒有實測**——而它決定了「wasmCloud 的 `wasi:blobstore` 算不算持久化儲存」這個判斷。
- **operator 的排程依據只有旁證。** 同一實驗兩次跑出 3:1 與 2:2,而 `Host.status` 有 `workloadCount` 與 `systemCPUUsage` 兩個看起來像依據的欄位。**沒有讀碼確認。**
- **`Host.status.systemMemoryFree` 的行為看不懂**:沒有 workload 的新 host 回報約 5 GB,兩顆有 workload 的都回報 `0`。沒有追。
- **啟動時間只有兩筆非控制條件下的數字**:首次拉取到 workload 啟動 962 ms(含 OCI 拉取),同一顆 host 上第二個 replica 是 586 ms(快取命中)。**各只跑一次、沒有對照組,不作任何比較宣稱。** Day 5 才是量這個的日子。
- **記憶體同樣只有一個快照**,而且元件跟 Day 2 不同(blobby 290 KiB vs `hello-world` 219 KiB),**不能跟 Day 2 的數字比**。
- **Day 2 留下的 `hello-world` 全程沒有再驗**,只確認 `READY=True`。今天所有 HTTP 驗證都打 blobby。

## 驗收 checkpoint

| | 判定 | 支撐證據 |
|---|---|---|
| **原訂驗收**:換掉能力提供者 | **已改寫** | 五個 CRD 對 `link`/`provider`/`capability` 零命中;`wash host` 每個介面只註冊一個 NATS 實作,多後端在 cargo feature 後面;提供者端點是 host 的 `--data-nats-url` 啟動參數 |
| **(A)** 同一映像、只改能力設定,行為改變 | **過** | 四個狀態映像完全相同,`PUT` 在 500／201／500／201 之間切換;狀態 4 讀回狀態 2 寫入的資料 |
| **(B)** 排程由 operator 決定、提供者跨節點共享 | **過** | workload 1→4,命名空間 pod 數恆為 5;四個 workload 落在兩顆 host(分屬兩台節點);spot 節點寫入、隨需節點讀得到 |
| 節點仍然乾淨(**兩台都比**) | **過** | 兩台節點的 `config.toml`、config dump、`/usr/bin` 清單各三份 diff 全部 `exit=0` |

!!! note "差異出現時,先確認是不是取樣指令本身變了"
    `/usr/bin` 清單第一次比出差異,原因是 grep 樣式跟 Day 1 不同(`runc` 會順便命中 `runcon`、`truncate`),欄位寬度也因為命中數不同而變。改成錨定完整路徑再正規化空白之後才是真正的零差異。

## 地雷記錄

### 地雷 1:2.6.1 的 Kubernetes 整合沒有可抽換的 capability provider {#mine-1}

**症狀**:照 v1 世代的教學找 link definition 或 capability provider 的 CRD,找不到;改設 `config.backend: in-memory`(原始碼裡確實有這個選擇鍵),設了完全沒有效果。

**根因**三層一起:**CRD 層**五個 CRD grep `link`/`provider`/`capability` 零命中;**二進位檔層**`wash host` 每個能力介面只註冊一個 NATS 實作,多後端(`with_multiplexed_plugins()`)鎖在 `#[cfg(feature = "wasm_component_model_implements")]` 後面;**綁定粒度層**提供者端點是 host 的 `--data-nats-url` 啟動參數,只能在 `runtime.hostGroups[].dataNatsUrl` 覆寫——**粒度是 host group,不是元件**。

**判斷準則**:**「這個版本做不到」需要三層證據,不是試兩下沒成功就下結論。** 查 CRD 表面、查實際載入的實作、查綁定粒度——三層都指向同一個答案才算數。

### 地雷 2:`config` 是沒有 schema 的 map,寫錯的鍵靜默忽略 {#mine-2}

**症狀**:`config: {backend: filesystem, root: /tmp/blobby}` 套用成功、`READY=True`、沒有任何 Event 或警告,而資料照樣進出 NATS。

**根因**:`kubectl explain …hostInterfaces.config` 的 `DESCRIPTION` 是 `<empty>`,型別是 `map[string]string`——**API server 對這個 map 的內容一無所知**,operator 不驗,host 端的 plugin 只 `config.get("自己認識的鍵")`。**能寫進去不代表有人會讀。**

**追查順序**:**唯一可靠的鍵清單來源是 host 原始碼**(`crates/wash-runtime/src/plugin/<介面>/`,找 `config.get(…)`)。實務上先用「改一個鍵、看行為變不變」二分:今天 `backend`/`root` 沒反應、`buckets` 有反應,就分得出哪些是真的。

### 地雷 3:blobstore 預設全部拒絕,而拒絕長成一個 HTTP 500 {#mine-3}

**症狀**:三層 Workload 物件全綠、EndpointSlice 有位址,而 curl 得到 `500` + `content-length: 0`。

**根因**:`config.buckets` 是 allow-list,**沒設就是空的,也就是預設全部拒絕**。blobby 開的 container 叫 `blobby`,所以要 `buckets: blobby`。**這個鍵在 CRD、chart values、blobby 的 README 裡都查不到**——README 只寫 `wash dev` 會自動接上,那是本機開發路徑。

**追查順序**:host pod log 找 `err=ErrorCode::InternalError(Some("unauthorized"))` → 把字串 grep 原始碼 → 找到出自哪一個 plugin(**順帶確認實際被載入的是哪一個實作**)→ 讀該 plugin 的 `on_workload_bind` 拿到鍵名。

**跟 Day 2 [地雷 4](sprint3-day2-wasmcloud.md#mine-4) 是同一種形狀:wasmCloud 的錯誤只出現在 host pod 的 log,`kubectl` 那一側永遠是綠的。** 差別是那一顆給 404(虛擬主機比對失敗),這一顆給 500(能力授權失敗)——**兩種代碼分得開,值得記成反射動作。**

### 地雷 4:`kubectl scale` 過的 Deployment 會讓下一次 `helm upgrade` 直接失敗 {#mine-4}

**症狀**:

```console
Error: UPGRADE FAILED: conflict occurred while applying object wasmcloud/hostgroup-default
  Apply failed with 1 conflict:
  conflict with "kubectl" with subresource "scale" using apps/v1: .spec.replicas
```

**根因**:chart 走 server-side apply,而 `kubectl scale` 把 `.spec.replicas` 的 field manager 搶成 `kubectl`(`--show-managed-fields` 看得到 `manager=kubectl operation=Update subresource=scale`)。SSA 遇到別人擁有的欄位就拒絕。

**兩個直覺解法都無效**:`--force` 回 `cannot use server-side apply and force replace together`;`--take-ownership` 只處理 helm 自己的 annotation,對 SSA 的欄位所有權沒有用。而**每一次失敗都在 release 歷史留下一個 `failed` 版本**。

**修法**:`helm upgrade --force-conflicts`。**預防**:`replicas` 一律走 `--set runtime.hostGroups[0].replicas=N`,不要用 `kubectl scale`。

**這一顆同時修正 Day 2 的預期。** Day 2 說「patch 會被下次 upgrade 蓋掉」,實測**只對一半**:

- chart **沒有** template 的欄位(`tolerations`、`topologySpreadConstraints`),patch 進去 upgrade **不會**沖掉——SSA 只管自己宣告過的欄位。
- chart **有** template 的欄位(`replicas`)才會被蓋回去,而且用 `kubectl scale` 動過還會先讓 upgrade 整個失敗。

**實務建議因此要改寫**:chart 表達不出來的排程欄位**可以** patch,但要接受它是一份沒有紀錄在任何 values 檔裡的帶外設定。

### 地雷 5:加節點不會讓 host 擴散過去;補上 toleration 之後,整組 host 反而全搬到 spot 節點 {#mine-5}

**症狀**:spot node pool 加完,三顆 host 一顆都沒過去(節點 request 佔用仍是 98% vs 17%)。`kubectl patch` 補 toleration 之後,滾動更新把**四顆 host 全部**排到 spot 節點,隨需節點掉到 0 顆。

**根因**兩件事疊加:chart 沒有 `tolerations`(已發佈 values 與 rendered manifest 各驗一次,零命中),也沒有 `topologySpreadConstraints` 或反親和性——所以補了 toleration 之後排程只剩容量在決定,隨需節點只剩 23m,spot 節點空著,`LeastAllocated` 全塞過去。

**修法**:要一節點一顆 host,得 patch **兩個** chart 表達不出來的欄位。

所以「加 spot 節點省錢」這句話要連著另一半一起說:你會需要兩個帶外 patch,而且中途會有一段時間**整個資料平面都在可回收機器上**。

### 地雷 6:`read_only: "true"` 是死碼,設了永遠不會生效 {#mine-6}

```console
--- read_only=true
  PUT=201            ← 寫入沒有被擋
  GET(ro.txt)=200 body=should-fail    ← 剛剛那次寫入確實落地了
```

**根因**:`workload_permit` 裡 allow-list 命中就 `return Some(…)`,**`read_only` 的檢查排在它後面**。所以只要 bucket 在 allow-list 裡,`read_only` 永遠輪不到;不在 allow-list 裡的話本來就全部拒絕。**這個鍵在任何情況下都不會改變行為。**

**這是[地雷 2](#mine-2) 的一個特例,但更難發現**——它是一個**會被讀取**的合法鍵,只是邏輯上到不了。靠「改一個鍵看行為變不變」二分也分不出來,因為它跟寫錯的鍵一樣沒反應。

### 地雷 7:每次拉元件都會噴一行看起來像認證失敗的 WARN,而拉取其實成功 {#mine-7}

```console
WARN …:resolve_credentials{registry=ghcr.io}:
     failed to retrieve docker credentials error=docker credential retrieval error: Unable to read config
INFO Starting workload                                    ← 下一行,拉取成功了
```

**根因**:host 容器裡沒有 `~/.docker/config.json`(`readOnlyRootFilesystem: true`,也沒掛 imagePullSecret),憑證解析當然失敗,於是退回匿名拉取。公開 registry 匿名拉得到,所以沒有後果。

**為什麼要記**:**這行是 `WARN` 而且字面上寫 `failed`**,在追查真正的拉取問題時(私有 registry、`imagePullSecret` 沒設對)會跟真正的錯誤混在一起,而官方文件沒有提到這是正常噪音。**判斷準則:看它下一行有沒有 `Starting workload`。**

## 帶得走的東西

- **判定「這個版本做不到」需要三層證據。** 查它的宣告面(CRD)、查它實際載入了什麼(二進位檔裡的註冊清單)、查綁定的粒度。三層都指向同一個答案才算數——**試兩下沒成功就宣稱「不支援」,跟照抄教學一樣不負責任。**
- **驗收表達不出來就改寫,不要硬湊。** 今天原訂的那一半換成了兩件這個版本實際支援的事,而且各自都能給出是/否。**寫清楚原本要驗什麼、為什麼表達不出來、改成驗什麼**,比勉強生出一個「通過」有用得多。
- **`grep -c … == 0` 不能單獨當證據,要同時證明輸入非空。** 指令失敗跟「沒有命中」對 `grep -c` 來說是同一個 `0`。這條在任何用「零命中」下結論的地方都成立。
- **沒有 schema 的設定面,只能靠原始碼與二分法。** `map[string]string` 意味著寫錯不會被擋、也不會有 Event。今天三顆地雷都長在這一個根上:寫錯的鍵靜默忽略、必要的鍵沒有文件、合法但到不了的鍵。
- **「加一台節點」不是一個中性的動作。** 它不會鬆綁既有 pod 的 request、不會讓既有 pod 搬家,而一旦你補上 toleration 讓它們能搬,它們可能**全部**搬過去。加節點之前要先想清楚你要的是容量還是分散,那是兩個不同的欄位。
- **server-side apply 決定了帶外 patch 的壽命。** 誰宣告過那個欄位,誰就管它;沒人宣告的欄位 patch 進去會活著。這比「patch 遲早被 helm 沖掉」這個直覺精確,而且方向相反。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Kubernetes:Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)** —— 地雷 4 的一手來源。「A conflict is a special status error that occurs when an Apply operation tries to change a field that another manager also claims to manage」,以及 `--force-conflicts` 的三種解法。讀懂 field manager 才知道為什麼 `--force` 跟 `--take-ownership` 在這裡都沒用。
- **[wasmCloud 的 blobby 範例](https://github.com/wasmCloud/wasmCloud/tree/main/examples/blobby)** —— 今天用的元件。值得看的是它**沒有**寫什麼:README 只說 `wash dev` 然後開 localhost:8000,`buckets`、`backend` 這些設定鍵一個字都沒提。地雷 3 的成因就在這裡。
- **[wasmCloud 的 runtime-operator chart](https://github.com/wasmCloud/wasmCloud/tree/main/charts/runtime-operator)** —— `values.yaml` 的 `hostPlugins` 註解自己承認多後端「not enabled in release images yet」,是地雷 1 最直接的一份佐證。

## 這條路線收尾:wasmCloud 適合誰

兩天實測走完,配上 [Day 0 那五條](sprint3-day0-wasm-concepts.md#health-method)的查證(2026-08-11),這條路線可以下判定了:

**體質**:三條路線裡最好——CNCF Incubating(另兩條是 Sandbox)、近 13 週 384 個 commit、機器人佔比僅 7%、標準跟得最緊(WASI 0.3 預設開啟)。隱憂是集中度:最近 100 筆 commit 有 74 筆出自同一人,而且在惡化(開課前查是 69%)。

**適合**:要用 wasm 元件組跨雲、跨邊緣的分散式應用,而且接受它自成一套執行模型。節點零侵入(五份 diff 為證),所以退出成本預期是三條最低的——不過「`helm uninstall` 之後節點真的一乾二淨」本課沒有實際拆過驗證,這一步是推論。

**不適合**:想把 wasm 塞進現有的 Kubernetes 工作負載模型。它不走 RuntimeClass、元件不是 Pod、kubectl 對它半盲——它解的不是那個問題。這種需求要等 Day 6 的 SpinKube。

選它的話,把單一貢獻者 74% 這件事寫進風險登記簿。

## 下一步

wasmCloud 這條路線到此告一段落。兩天實測下來,位置很清楚:**它不碰節點,而它換來的是自己的一整套抽象**——自己的排程器、自己的能力授權模型、自己的分散式狀態,以及一個沒有型別的設定面。

[Day 4](sprint3-day4-wasmedge.md) 換 **WasmEdge**,第二條路線。它比 wasmCloud 更靠近節點(走執行期整合),但還沒到 Day 6 改 containerd 設定那一步。**對節點的侵入程度由低到高**這個順序,到那一天會開始看得出差別。

而 Day 2、Day 3 留下的量測陷阱(記憶體不會歸還、元件不同不能比、啟動時間各只跑一次)全部指向同一天:**Day 5 才是量成本的日子**,那天要先做方法學試跑,確認雜訊小於效果。

---

!!! quote ""
    wasmCloud 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
