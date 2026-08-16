# Day 2: wasmCloud——一條不碰節點的路線,以及它把代價換到了哪裡

![wasmCloud 官方標誌](../assets/logos/wasmcloud-icon-color.svg){ align=right width="88" }

> [Day 1](sprint3-day1-three-generations.md) 把 RuntimeClass 從 Pod spec 追到節點上的 shim 行程,七層每一層都指出來了。今天這條路線**一層都不用**——wasm 元件不經過 scheduler、不經過 kubelet、不經過 CRI。驗收有兩半,第二半是拿 Day 1 存下來的節點基準逐字比對。

!!! abstract "你在課程的哪裡"
    - **Day 1**:RuntimeClass 七層實測,節點基準存檔。
    - **今天**:三條路線裡**對節點侵入程度最低**的那一條。裝 wasmCloud、跑第一個元件,然後證明節點一個位元組都沒變。
    - **Day 3**:同一套 wasmCloud,往它的分散式模型走——host group、link、跨節點排程。

「不改節點」聽起來只有好處,所以今天要問的第二個問題是:**那代價去哪了。** 今天量到三筆。

## 今天要走的路

| 步驟 | 做什麼 | 為什麼排在這 |
|---|---|---|
| 1 | 驗證 Day 1 的基準沒有漂移 | **`stop`/`start` 會換一台新 VM**,基準若漂了,後面的 diff 就不指向安裝行為 |
| 2 | 確認要裝的到底是什麼 | 「wasmCloud 在 K8s 上怎麼裝」2026 年整個換過,網路上的教學大多還停在舊路徑 |
| 3 | 安裝,並清點它在叢集裡放了什麼 | 三筆代價有兩筆在這一步就看得到 |
| 4 | 模型:為什麼它不需要 shim | 跟 Day 1 並排,兩條路線的取捨才講得清楚 |
| 5 | 跑第一個元件 | 驗收第一半 |
| 6 | 五份 diff | 驗收第二半 |

## 步驟 1: 換了一台 VM 之後,基準還是同一份嗎

AKS 的節點會被汰換——`stop`/`start`、節點映像升級、spot 回收,拿回來的都是**全新的 VM**(本例:兩章之間叢集停機過,VMSS 執行個體換了編號)。所以在裝任何東西之前,得先確認 Day 1 存下來的四份基準在新 VM 上仍然成立:

```console
$ diff <Day1 基準>/baseline-containerd-config.toml pre-containerd-config.toml
  → exit=0

$ diff <Day1 基準>/baseline-containerd-config-dump.toml pre-containerd-config-dump.toml
  → exit=0

$ diff <Day1 基準>/baseline-node-runtimehandlers.json pre-node-runtimehandlers.json
  → exit=0

$ diff <Day1 基準>/baseline-shim-binaries.txt pre-shim-binaries.txt
  → exit=0
```

四份全同,連 `ls -la` 的 mtime 都是同一個日期。

**這一步不只是保險,它證明了一件事**:那份基準是**節點映像**的性質,不是那台 VM 的性質。有了這個前提,步驟 6 的 diff 才指向「安裝 wasmCloud 這個行為」,而不是「兩台機器本來就不一樣」。

## 步驟 2: 要裝的到底是什麼——這一步查出兩件事

版本採 v2.6.1,查證仍是最新:

```console
$ gh api repos/wasmCloud/wasmCloud/releases --jq '.[] | "\(.tag_name)\t\(.published_at)"' | head -4
v2.6.1   2026-07-29T21:06:31Z
v2.6.0   2026-07-28T22:02:31Z
v2.5.2   2026-07-10T19:25:23Z
v2.5.1   2026-07-02T13:27:14Z
```

但**「wasmCloud 在 Kubernetes 上怎麼裝」這件事在 2026 年整個換過**,而網路上大量教學還停在舊路徑。

**舊的 `wasmCloud/wasmcloud-operator` repo 已經改名成 `wadm-operator`:**

```console
$ gh api repos/wasmCloud/wasmcloud-operator --jq '.full_name'
wasmCloud/wadm-operator          ← GitHub 自動轉址,舊 URL 還通

$ gh api repos/wasmCloud/wadm-operator --jq '.pushed_at'
2025-12-15T09:11:53Z             最後 release v0.5.1(2025-12-04)
```

**任何叫你 `helm install wasmcloud-operator …` 的教學,裝的是 v1 世代的東西。** 而它的舊 URL 還會正常轉址過去,不會給你任何警告。

v2 的 Kubernetes 入口在主 repo 裡,叫 `runtime-operator`,chart 版本與 appVersion 綁在一起:

```console
$ helm show chart oci://ghcr.io/wasmcloud/charts/runtime-operator
name: runtime-operator
version: 2.6.1
appVersion: 2.6.1        ← release train 每次一起推
```

而這個轉換期還有一個更難察覺的形狀,見[地雷 5](#mine-5)。

安裝指令必須帶一份 overlay:

```bash
helm install wasmcloud oci://ghcr.io/wasmcloud/charts/runtime-operator \
  --namespace wasmcloud --create-namespace \
  -f https://raw.githubusercontent.com/wasmCloud/wasmCloud/refs/heads/main/charts/runtime-operator/values.local.yaml
```

那份 overlay 做兩件事:把已棄用的 `runtime-gateway` 關掉(2.0.3 起 HTTP 路由改由 operator 經 EndpointSlice 處理),以及把 host group 開成 **3 個 replica**。第二件事等一下會出問題。

!!! warning "這行指令自己埋了一個不可重現性"
    `-f` 指向的是 **`refs/heads/main`**,不是某個 tag。**用一個釘死的 chart 版本(2.6.1),配一份會隨 main 漂移的 values。**

    穩妥的做法是先把那份 overlay 下載存成本機檔案再 `-f` 它。照抄那行指令沒有錯,但要知道你今天裝到的和下個月裝到的可能不是同一組設定。

## 步驟 3: 它在叢集裡放了什麼

```console
$ helm install wasmcloud oci://ghcr.io/wasmcloud/charts/runtime-operator \
    --namespace wasmcloud --create-namespace -f values.local.yaml
STATUS: deployed
                              實測 9 秒
```

清點結果:**5 個 CRD、3 個 Deployment**(`runtime-operator` / `nats` / `hostgroup-default`×3)、3 個 ClusterIP Service、3 個 NetworkPolicy、RBAC。

**0 個 DaemonSet、0 個 webhook、0 個 PVC、0 個 LoadBalancer。**

`resources` 三個 Deployment 全部有設,而且 values.yaml 的註解直接寫出量測依據——這是本 sprint 到目前為止最負責任的一份 chart:

| Deployment | requests | limits | values.yaml 註解裡的實測值 |
|---|---|---|---|
| `runtime-operator` | cpu 100m / mem 64Mi | mem 192Mi | `Idle ~19Mi (profiled)`,limit 留給 informer cache |
| `nats` | cpu 100m / mem 64Mi | mem 128Mi | `Idle ~6Mi (profiled)` |
| `hostgroup-default` | cpu 250m / mem 64Mi | cpu 500m / mem 512Mi | overlay 覆寫 |

但另外五個欄位一個都沒有:

```console
$ grep -iE "toleration|affinity|nodeSelector|topologySpread|priorityClass" \
    charts/runtime-operator/values.yaml charts/runtime-operator/templates/runtime/deployment.yaml
                                            ← 五個關鍵字,兩個檔案,零命中
```

這兩件事各自變成一顆地雷:[地雷 1](#mine-1)(資源帳)與[地雷 2](#mine-2)(生態帳)。叢集重啟行為則是[地雷 3](#mine-3)。

## 步驟 4: 為什麼它不需要 shim

Day 1 建立的心智模型是這樣的:

```text
Pod spec: runtimeClassName ──▶ RuntimeClass 物件的 handler
                                      │
                                kubelet 把 handler 傳給 CRI
                                      │
                     containerd 查 config.toml 的 runtimes.<handler>
                                      │
                            起對應的 shim 二進位檔
```

**每一個箭頭都在節點上,而最後一格是磁碟上的一個檔案。** 要跑 wasm 就得在節點上多放一支 shim,並在 `config.toml` 加一段指向它——那是 Day 6 的工作。

wasmCloud 在**第一個箭頭之前就分岔了**:

```text
WorkloadDeployment(CRD) ──▶ runtime-operator ──NATS──▶ host pod(普通 Deployment)
                                                            │
                                                   host 行程裡的 Wasmtime
                                                            │
                                              把 .wasm bytecode 載進來執行
```

**wasm 元件從來沒有變成一顆 Pod。** containerd 在整條路徑上只參與一次:把 `ghcr.io/wasmcloud/wash:2.6.1` 這個**普通容器映像**拉下來跑成 host pod——而那件事用預設的 `runc` handler 就做完了。

三段證據:

**每一顆 wasmCloud pod 都走預設 handler**,跟 Day 1 那顆「什麼都沒指定」的 pod 同一條路:

```console
$ nsenter -t 1 -m -- crictl pods --namespace wasmcloud -o json | …
hostgroup-default-…       runtimeHandler=''
hostgroup-default-…       runtimeHandler=''
hostgroup-default-…       runtimeHandler=''
runtime-operator-…        runtimeHandler=''
nats-…                    runtimeHandler=''
```

對照 Day 1 同一條指令的三種狀態:`'runc'`、`'untrusted'`、`''`。**wasmCloud 全體落在第三種。**

**host pod 的 spec 沒有任何節點特權:**

```console
$ kubectl -n wasmcloud get deploy hostgroup-default -o json | …
hostPID / hostNetwork / hostIPC : None None None
privileged                      : None
securityContext : {"allowPrivilegeEscalation": false, "capabilities": {"drop": ["ALL"]},
                   "readOnlyRootFilesystem": true, "runAsNonRoot": true}
volumes         : [oci-cache(emptyDir), tmp(emptyDir),
                   runtime-cert(secret), data-cert(secret)]   ← 零個 hostPath
```

**跟 Day 1 那顆 `node-shell` 剛好相反**——那顆要 `hostPID: true` + `privileged: true` + `hostPath: /` 才看得到節點。**能不能看到節點寫在 pod spec 裡,而 wasmCloud 的答案是不能。**

**節點上沒有多出任何標籤、註記、taint:**

```console
$ kubectl get node -o json | …
labels containing 'wasm' or 'spin' : []
annotations containing 'wasm'      : []
taints                             : None
```

**這一點記住,Day 6 會相反。** SpinKube 的 `runtime-class-manager` 會替裝好 shim 的節點打標籤(所以 Day 1 [地雷 3](sprint3-day1-three-generations.md#mine-3) 已預告 Day 6 的 RuntimeClass 要帶 `scheduling.nodeSelector`)。

**「需不需要替節點打標籤」是分辨這兩種整合模型最快的一個問號**:需要標籤,代表節點之間有差異;節點之間有差異,代表有東西落在節點上。

### 兩條路線,一張表

| | **Day 1 / Day 6:RuntimeClass + containerd shim** | **Day 2:wasmCloud host** |
|---|---|---|
| wasm 元件對 K8s 來說是什麼 | **一顆 Pod**(有 IP、QoS、probe、`kubectl logs`) | **一個 CRD 物件**,K8s 不知道它在跑什麼 |
| 誰決定它跑在哪 | kube-scheduler | wasmCloud operator 經 NATS,**scheduler 沒有參與** |
| 誰真的執行它 | containerd → shim → Wasmtime | host pod 行程裡的 Wasmtime |
| 節點要不要改 | **要**:shim 二進位檔 + `config.toml` + 通常還要標籤 | **不要**(五份 diff 為證) |
| 隔離邊界 | 每個元件一個 sandbox,含 cgroup / namespace | **同一顆 host pod 內多個元件共用一個 cgroup**,隔離靠 wasm 的能力模型而不是 Linux |
| 資源限制粒度 | 每個元件 | **每顆 host**,元件之間沒有 K8s 層的上限 |
| 生態相容 | HPA、PDB、`kubectl logs`、Service selector 原生可用 | **都不適用**,要用 wasmCloud 自己的機制 |
| 誰承擔升級風險 | 節點映像 / containerd 版本(Day 1 [地雷 1](sprint3-day1-three-generations.md#mine-1) 的形狀) | wasmCloud 自己的控制平面,多一套 NATS 要顧 |

**這不是同一件事的兩種寫法。取捨可以寫成一句:**

> **shim 路線把 wasm 塞進 Kubernetes 既有的抽象裡,代價是要改節點;wasmCloud 在 Kubernetes 上面另外蓋一層排程,代價是不改節點,但 Kubernetes 的工具對它半盲。**

「半盲」不是形容詞,今天有三個具體量測:`kubectl top` 看不到單一元件的用量、`kubectl get pod` 的數量不隨元件增減、Service 的 EndpointSlice 不是由 selector 產生的。

## 步驟 5: 跑第一個元件

```bash
cat > hello-world.yaml <<'EOF'
apiVersion: v1
kind: Service
metadata: { name: hello-world, namespace: wasmcloud }
spec:
  type: ClusterIP        # the upstream sample uses NodePort; AKS NSGs block 30000-32767
  ports: [ { name: http, port: 80, protocol: TCP } ]
                         # no selector — see below
---
apiVersion: runtime.wasmcloud.dev/v1alpha1
kind: WorkloadDeployment
metadata: { name: hello-world, namespace: wasmcloud }
spec:
  replicas: 1
  template:
    spec:
      hostSelector: { hostgroup: default }
      kubernetes: { service: { name: hello-world } }
      components:
        - name: hello-world
          image: ghcr.io/wasmcloud/components/hello-world:0.1.0
      hostInterfaces:
        - namespace: wasi
          package: http
          interfaces: [ incoming-handler ]
          config: { host: hello-world.wasmcloud.svc.cluster.local }
EOF
kubectl apply -f hello-world.yaml
```

那個 `config.host` 值跟官方範例不一樣,理由見[地雷 4](#mine-4)——照抄範例的話這裡會拿到一個看不出原因的 404。

**`Service` 沒有 `selector`。** 這不是漏寫。EndpointSlice 由 operator 維護,指向真正承載該元件的 host pod:

```console
$ kubectl -n wasmcloud get endpointslice
NAME                    PORTS   ENDPOINTS
hello-world-…           80      <host-a>                      ← 只有一個,元件在那顆 host 上
hostgroup-default-…     80      <host-a>,<host-b>,<host-c>    ← 三顆 host 都在
```

**這是「半盲」最具體的一格**:Service 還是那個 Service、kube-proxy 照常做事,但**「哪些後端算數」的判斷從 label selector 換成了 operator 的內部狀態**。(沒有 selector 的 Service 搭配外部管理的 EndpointSlice 是 Kubernetes 正式支援的用法,不是繞路。)

### 部署之後,pod 數量一顆都沒變

```console
$ kubectl -n wasmcloud get pods --no-headers | wc -l
5                                    ← 部署前也是 5

$ kubectl -n wasmcloud get workloaddeployment,workloadreplicaset,workload
NAME                            REPLICAS   READY
workloaddeployment/hello-world  1          True
NAME                                       REPLICAS   READY
workloadreplicaset/hello-world-748fb64f5c  1          True
NAME                                        READY
workload/hello-world-748fb64f5c-7bb6b5cb69  True
```

**`WorkloadDeployment` → `WorkloadReplicaSet` → `Workload` 跟 `Deployment` → `ReplicaSet` → `Pod` 是刻意的鏡像**,連 hash 後綴的命名慣例都照抄。

設計意圖很清楚:讓 K8s 使用者不用重學。**代價是這三層完全不是 K8s 的原生物件,HPA / PDB / `kubectl rollout` 一個都接不上。**

### 元件有多大、起多快

OCI artifact 的 manifest 直接說話:

```console
$ curl …/v2/wasmcloud/components/hello-world/manifests/0.1.0
"config": { "mediaType": "application/vnd.wasm.config.v0+json", "size": 469 },
"layers": [
  { "mediaType": "application/wasm", "size": 224241 }      ← 219 KiB,單一 layer
]
```

**`mediaType: application/wasm`,一層,219 KiB。** 不是 `tar+gzip`,裡面**沒有 rootfs、沒有 libc、沒有 base image**。這是 Day 5 量映像大小的第一個數字。

冷啟動分兩個尺度量:

```console
# host 內部:從拉取元件到 workload 起來
05:47:14.466  WARN  pull_component{reference=…hello-world:0.1.0}
05:47:14.997  INFO  Starting workload
                                            →  531 ms

# 端到端:kubectl apply 到第一個 HTTP 200(叢集內每 0.2s 輪詢)
attempts=7  elapsed_sec=3.63
```

**531 ms 對 3.63 秒。** 差的 3 秒不在 wasm,在 operator 對帳、NATS 來回、EndpointSlice 建立、kube-proxy 寫規則。

!!! warning "這兩個數字不能當成「wasm 冷啟動時間」"
    (a) 含 OCI 拉取(本次是快取命中)與 Wasmtime 編譯;(b) 含整條 K8s 控制平面路徑。**各只跑一次,沒有重複取樣、沒有分佈、沒有容器組對照。**

    Day 5 才是量這個的日子。今天的數字只作為量級參考,**不拿來做任何比較宣稱**。

記憶體有一個給 Day 5 的陷阱:

```console
$ kubectl top pods -n wasmcloud
hostgroup-default-…   1m   25Mi     ← 目前承載 hello-world
hostgroup-default-…   6m   25Mi     ← 曾經承載過,現在沒有
hostgroup-default-…   1m    4Mi     ← 從頭到尾沒載過
```

沒載過元件的 host 是 4Mi,載過的是 25Mi。但**中間那顆的元件已經停掉了,記憶體仍然是 25Mi**——卸載元件並沒有把記憶體還給 host 行程(多半是配置器沒有歸還給 OS,不代表洩漏)。

**Day 5 量記憶體之前必須換一顆乾淨的 host 或重啟 host pod,否則讀數被前一個元件污染。**

## 驗收 checkpoint

### 第一半:一個 wasm 元件在叢集上回應請求

```console
$ kubectl -n wasmcloud exec curler -- curl -sS -D- http://hello-world.wasmcloud.svc.cluster.local/
HTTP/1.1 200 OK
transfer-encoding: chunked

Hello from wasmCloud!
```

**過。** 而且部署前後 `wasmcloud` 命名空間的 pod 數量都是 5。

### 第二半:節點未被觸碰 {#gate-b}

```console
$ diff <Day1 基準>/baseline-containerd-config.toml       post-containerd-config.toml       → exit=0
$ diff <Day1 基準>/baseline-containerd-config-dump.toml  post-containerd-config-dump.toml  → exit=0
$ diff <Day1 基準>/baseline-node-runtimehandlers.json    post-node-runtimehandlers.json    → exit=0
$ diff <Day1 基準>/baseline-shim-binaries.txt            post-shim-binaries.txt            → exit=0
$ diff pre-runtimeclasses.txt                            post-runtimeclasses.txt           → exit=0
```

**五份全部 `exit=0`、零行輸出。** 補三項 diff 表達不了的:

```console
$ ls /usr/bin/ | grep -iE "shim|wasm|spin|wash|crun|wasmtime|wasmedge|youki"
containerd-shim-runc-v2                  ← 只有這一支,與 Day 1 相同

$ kubectl get ds -A | grep -v kube-system
(只剩自己佈的節點觀測窗口;wasmCloud 沒有 DaemonSet)

$ kubectl get node -o json | (labels/annotations 含 wasm|spin) → []   taints → None
```

| 驗證 | 判準 | 結果 |
|---|---|---|
| 基準沒有隨新 VM 漂移 | 安裝前先跑一次四項對照 | 四份全同 |
| **驗收第一半** | wasm 元件回應 HTTP | `200 OK` + `Hello from wasmCloud!`,pod 數量不變 |
| **驗收第二半** | 五份對照 Day 1 基準 | **五份 `exit=0`、零行輸出** |
| 沒有東西想動節點 | CRI 側的 handler、pod 特權、節點標籤 | 全體 `runtimeHandler=''`;無 hostPath / hostPID / privileged;無標籤註記 taint |

## 誠實的差距

- **啟動時間與記憶體都只跑一次,沒有對照組。** 531 ms 與 3.63 秒不能拿來跟容器比,今天也沒有比。Day 5 才做這件事。
- **那 21Mi 的差不等於「一個 wasm 元件的記憶體足跡」。** 它含 OCI 快取、Wasmtime 的編譯結果、host 第一次進入「有工作負載」路徑時配置的執行期結構。要拆開需要 Day 5 的方法學。
- **單節點,所以「host 不保證分散」只是讀 chart 讀出來的,沒有實測。** 三顆 host 今天全落在同一顆節點上,但那是因為只有一顆節點。Day 3 加第二顆節點才驗得到。
- **隔離邊界只讀了設定,沒有做穿透測試。** 「同一顆 host pod 內多個元件共用一個 cgroup」是從 pod spec 與模型推出來的,沒有實際跑兩個元件去驗證它們之間的隔離強度。
- **沒有測 host pod 被刪掉時元件會怎樣。** 地雷 2 提到的「host 一被驅逐元件跟著消失」是從架構推論,今天沒有實際驗證。

## 地雷記錄

### 地雷 1:官方推薦的 overlay 是給本機叢集寫的,在 2 vCPU 節點上吃掉 98% CPU request {#mine-1}

`values.local.yaml`(README 用 `-f` 直接指向它)預設 `replicas: 3`,每顆 host `requests.cpu: 250m`,加 operator 100m 與 NATS 100m 共 **950m**。

```console
# 安裝前
cpu   927m (48%)     memory  994Mi (17%)

# 安裝後
cpu   1877m (98%)    memory  1314Mi (22%)
                     ↑ 節點 allocatable 是 1900m,剩 23m
```

**只差 23m 就排不下去了。** 而這 950m 換到的實際用量是:

```console
$ kubectl top pods -n wasmcloud          # 元件還沒部署,純 idle
                             20m   29Mi     ← request 是 950m / 320Mi
```

**request 是實測的 47 倍。**

**根因不是 chart 亂寫。** `runtime.resources` 的 250m 是給「host 上真的跑滿元件」的密度準備的,values.yaml 註解也明說要按預期密度調整。**問題出在那份 overlay:`replicas: 3` 是為了在本機 kind 叢集上展示跨 host 排程**,而 README 把它當成通用的 recommended overlay 推薦給所有人。

**失敗形狀比 Day 1 友善**(`Pending` + `Insufficient cpu`,訊息直指原因),但**它會在你下一次多裝一個東西時才炸,不是在裝 wasmCloud 的時候**。這次沒有觸發,只是因為還剩 23m。

**修法是明確覆寫,不是把 request 拿掉**:`--set runtime.hostGroups[0].replicas=1`(單節點)或加節點。照抄 README 那行指令就必須同時交代節點要幾顆 vCPU。

### 地雷 2:chart 沒有 tolerations / affinity / priorityClassName 任何一個 {#mine-2}

步驟 3 那個零命中的 grep 不是小事,三個具體後果:

**host replica 不保證分散。** host group 存在的理由之一就是跨 host 容錯,而 chart 沒有任何反親和性或 `topologySpreadConstraints`,**連 values 的覆寫入口都沒有**。

**帶 taint 的節點池排不上去。** AKS 的 spot node pool 自帶 `kubernetes.azure.com/scalesetpriority=spot:NoSchedule`,host pod 沒有 toleration。chart 既然沒有這個欄位,就只剩直接 `kubectl patch` Deployment 的 pod template 一條路。

!!! info "這種帶外 patch 的壽命,跟直覺不一樣"
    直覺會說「patch 進去的東西下次 `helm upgrade` 就被沖掉了」。**[Day 3 實測的結果是只對一半](sprint3-day3-wasmcloud-distributed.md#mine-4)**:chart 沒有 template 的欄位(`tolerations`、`topologySpreadConstraints`)patch 進去**不會**被沖掉,因為 server-side apply 只管自己宣告過的欄位。

    真正會出事的是 chart **有** template 的欄位——用 `kubectl scale` 動過 `replicas`,下一次 `helm upgrade` 會**直接失敗**。

**`priorityClassName` 缺席意味著 operator 與 host 跟一般業務 pod 同為 `priority: 0`**,節點吃緊時會被 preempt。而 host 一被驅逐,上面所有 wasm 元件跟著消失。

**「元件不是 Pod」的另一面是「元件沒有 Pod 層級的重啟保護」**:它的可用性完全繼承自承載它的 host pod,而那顆 pod 沒有任何優先級保護。

### 地雷 3:operator 與 host 每次叢集重啟一律先 CrashLoop 兩次等 NATS {#mine-3}

```console
$ kubectl -n wasmcloud get pods
runtime-operator-…    1/1  Running  2 (41s ago)
hostgroup-default-…   1/1  Running  2 (41s ago)     ← 三顆都是 2
```

逐字原因:

```console
$ kubectl -n wasmcloud logs runtime-operator-… --previous | tail -3
ERROR  setup  unable to create runtime operator
       {"error": "transport error: nats: no servers available for connection"}

$ kubectl -n wasmcloud logs hostgroup-default-… --previous | tail -5
failed to connect to NATS Scheduler URL
Caused by:
    0: failed to connect to NATS
    1: IO error: Connection refused (os error 111)
```

**chart 沒有任何啟動順序處理**——沒有 initContainer 等 NATS、沒有連線重試退避,程序直接 `exit 1` 交給 kubelet 重啟。

分寸要講清楚:**這是自癒的,不是壞掉**,約 55 秒內全部 `Running`。但兩個實務後果:

**`RESTARTS: 2` 是每次叢集重啟的常態,不是異常訊號。** 監控「重啟次數 > 0」的規則會在每次叢集重啟、每次 NATS 滾動更新時噴警報。先知道這件事,就不會去追一個不存在的問題。

**外接 NATS(正式環境該做的事)時這個形狀會放大**:NATS 在別的叢集或命名空間、NetworkPolicy 還沒生效、DNS 還沒收斂——就會變成全體 CrashLoopBackOff 退避到五分鐘一次,而 `Connection refused (os error 111)` **一個字都沒提到 `schedulerUrl` 指到哪裡**。

### 地雷 4:範例的 `config.host: localhost` 是虛擬主機比對,照抄拿到沒有內容的 404 {#mine-4}

照官方範例填 `config.host: localhost`,再用 Service 的 FQDN 呼叫:

```console
$ curl -sS -D- http://hello-world.wasmcloud.svc.cluster.local/
HTTP/1.1 404 Not Found
content-length: 0
```

**404、零位元組、沒有任何說明。** 而三層 Workload 物件全部 `READY=True`、EndpointSlice 也有位址——**每一個 `kubectl` 指令都告訴你一切正常。**

**唯一的線索在 host pod 的 log,而且是 `WARN` 不是 `ERROR`:**

```console
WARN handle_http_request{http.host=hello-world.wasmcloud.svc.cluster.local}:
     failed to route incoming request
     err=no workload bound to host "hello-world.wasmcloud.svc.cluster.local"
```

**`hostInterfaces[].config.host` 是 HTTP `Host` 標頭的比對值**,也就是虛擬主機路由。範例寫 `localhost` 是因為它給本機 kind 叢集用。

兩個方向都驗過是一對一比對:加 `-H "Host: localhost"` → 200;把 `config.host` 改成 Service FQDN → 200,而**原本會通的 `Host: localhost` 反過來 404**。

**判斷準則:wasmCloud 的 404 先看 host pod log 的 `no workload bound to host`,不要從 Service / EndpointSlice 查起。**

有一個對照組幫忙分辨兩種症狀——**元件被刪掉時,同一個 Service 給的不是 404 而是連不上**,因為 operator 把 EndpointSlice 清空了:

```console
$ kubectl -n wasmcloud delete workloaddeployment hello-world
$ curl --max-time 10 http://hello-world.wasmcloud.svc.cluster.local/
curl: (7) Failed to connect … port 80 after 2 ms
```

**「元件不存在」是連線被拒;「元件在但 Host 對不上」是 404。**

### 地雷 5:官方文件站給的安裝指令,裝的是上一代的 chart {#mine-5}

`wasmcloud.com/docs/deployment/k8s/` 今天教的是:

```bash
helm upgrade --install wasmcloud-platform \
  --values https://raw.githubusercontent.com/wasmCloud/wasmcloud/main/charts/wasmcloud-platform/values.yaml \
  oci://ghcr.io/wasmcloud/charts/wasmcloud-platform --dependency-update
```

那個 chart 今天實測**仍然拉得下來**:

```console
$ helm show chart oci://ghcr.io/wasmcloud/charts/wasmcloud-platform
Pulled: ghcr.io/wasmcloud/charts/wasmcloud-platform:0.1.2
appVersion: 1.2.1          ← v1 世代
```

但主 repo 的 `charts/` 目錄底下**現在只剩一個**:

```console
$ gh api repos/wasmCloud/wasmCloud/contents/charts --jq '.[].name'
runtime-operator           ← 只有這一筆
```

**文件站推薦的 chart 已經不在原始碼樹裡了,而 registry 上的舊 artifact 還在。**

**失敗形狀是最糟的那一種:不會失敗。** `helm install` 會成功、pod 會起來、你會得到一套能用的 wasmCloud——只是它是 v1 世代,CRD 不一樣,而後面幾天要用的 `WorkloadDeployment` 在上面不存在。

**判定方法**:裝完先 `kubectl get crd | grep wasmcloud`。看到 `runtime.wasmcloud.dev/v1alpha1` 的 `WorkloadDeployment` 才是 v2;看到 `wadm` 相關的就是 v1。

## 帶得走的東西

- **「不改節點」不等於「沒有代價」。** 今天量到三筆換過去的帳:資源帳(overlay 預設吃掉 98% CPU request)、生態帳(chart 缺五個排程欄位、HPA/PDB/`kubectl logs` 對元件不適用)、控制平面帳(多一套 NATS 要顧,每次重啟必先 CrashLoop 兩次)。**推薦一條「乾淨」的路線時,這三筆要跟「乾淨」一起講。**
- **驗「什麼都沒發生」要有事前基準,而基準本身要先驗過。** 今天的第二半驗收之所以有力,是因為安裝前先確認了 Day 1 的基準在**新 VM** 上仍然逐字成立——證明那是節點映像的性質而不是那台機器的性質。**沒有這一步,五份零行輸出的 diff 證明力會弱很多。**
- **「需不需要替節點打標籤」能一句話分辨整合模型。** 需要標籤,代表節點之間有差異;有差異,代表有東西落在節點上。
- **上游改版時,最危險的不是壞掉,是還能用。** 舊 repo 改名後 URL 自動轉址、舊 chart 還留在 registry、文件站還沒更新——三件事湊在一起,照著搜尋結果做會裝出一套「能跑但是上一代」的東西,而且沒有任何一步會報錯。
- **物件全綠不代表請求會通。** 那個 `content-length: 0` 的 404,在 `kubectl` 那一側完全看不出來。**當控制平面的健康狀態由第三方 operator 定義時,「READY=True」的含意也由它定義。**

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[wasmCloud 的 runtime-operator chart 原始碼](https://github.com/wasmCloud/wasmCloud/tree/main/charts/runtime-operator)** —— v2 在 Kubernetes 上的唯一入口。`values.yaml` 的註解直接寫出各元件的 profiled idle 用量,值得對照地雷 1 一起讀;這個目錄底下**沒有 README**,安裝指令散在別處。
- **[改名後的 wadm-operator repo](https://github.com/wasmCloud/wadm-operator)** —— 舊的 `wasmcloud-operator`。舊 URL 今天仍會自動轉址過來,不會有任何提示。最後推送 2025-12-15。
- **[Kubernetes:沒有 selector 的 Service](https://kubernetes.io/docs/concepts/services-networking/service/)** —— 「Services without selectors」一節。wasmCloud 讓 operator 維護 EndpointSlice 是這個機制的正規用法,不是繞路;讀懂這一節才知道為什麼 `kubectl get endpointslice` 是追查 wasmCloud 路由的正確起點。

## 下一步

今天證明了 wasmCloud 不碰節點,也量到它把代價換到哪裡。但**三顆 host 全落在同一顆節點上**,所以「host group 跨主機容錯」這件事今天只是讀 chart 讀出來的。

[Day 3](sprint3-day3-wasmcloud-distributed.md) 加第二顆節點,而**第一件事就是撞地雷 2**:新節點帶 taint,host pod 沒有 toleration,排不上去。那一天要把 wasmCloud 的分散式模型走完——host group、link、以及元件與能力解耦這件事在 Kubernetes 上實際長什麼樣。

---

!!! quote ""
    wasmCloud 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
