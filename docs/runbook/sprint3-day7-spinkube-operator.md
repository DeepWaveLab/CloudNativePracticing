# Day 7: SpinKube(下)——13 個月沒發版的 operator 裝得起來,而它其實不是執行機制

![SpinKube 官方標誌](../assets/logos/spinkube-icon-color.svg){ align=right width="88" }

> [Day 6](sprint3-day6-spinkube-shim.md) 把節點側裝好了。今天先用節點汰換後的前五分鐘做一個一次性的觀察,然後裝整組裡唯一停擺的元件 spin-operator——**最後用一份只有三個關鍵欄位的 Deployment 證明:就算它不能用,也沒關係。**

!!! abstract "你在課程的哪裡"
    - **Day 6**:rcm 佈建 shim,踩到它猜錯 plugin 路徑的雷,修一個字串跑通。
    - **今天**:spin-operator、`SpinApp` CRD,以及本 sprint 最重要的一個對照實驗。
    - **Day 8**:全部拆掉,拿 Day 1 的基準驗「拆得乾淨嗎」。

## 今天要走的路

| 步驟 | 做什麼 |
|---|---|
| 0 | 節點汰換觀察窗:rcm 面對新節點會做什麼 |
| 1 | s0 對帳(節點層改動第三次驗證消失) |
| 2 | 裝 spin-operator,判定 13 個月的空窗有沒有變成技術債 |
| 3 | 驗收 (A):SpinApp 回應請求 |
| 4 | 驗收 (B):**不經 operator 跑同一支程式** |

## 步驟 0: 節點汰換的觀察窗——「叢集記得、節點忘了」

[Day 5](sprint3-day5-cost-measurement.md#mine-1) 確立了節點層改動撐不過 VM 汰換。兩章之間節點又被換過一次,這是唯一能觀察「rcm 面對新節點會做什麼」的機會。

### 標籤全數消失,rcm 正確地什麼都不做

```console
$ kubectl get node -o jsonpath='{…metadata.labels}' | (篩 spin/rcm/kwasm)
{}                    ← 節點標籤總數 42 個,命中 0
```

Day 6 是用 `kubectl label node` 手動貼的標籤——**Node 物件隨 VM 重建,手動標籤跟著消失**。而 rcm 的 shim controller 今天全部的輸出是:

```text
{"shim":"spin-v2","message":"RuntimeClass found: wasmtime-spin-v2"}
{"shim":"spin-v2","message":"No nodes found"}
```

**`nodeSelector` 選不到節點,它就不做任何事——這是正確行為**,是 Day 6 那格「佈建範圍宣告式、預設空集合」的另一面。所以「rcm 會不會自動重佈建新節點」的答案是:**在手動貼標籤的用法下,不會。**

實務結論不是「rcm 做得不好」,而是**節點標籤要有一個能撐過 VM 重建的來源**:AKS 節點池的 `--labels` 寫在 VMSS 的佈建設定裡,每顆新 VM 都會帶;`kubectl label node` 只寫進 Node 物件,換一顆 VM 就沒了。**這兩者要分開講。**

### 但 job controller 在旁邊一直 panic

Day 6 那顆節點的 install Job 還在 etcd 裡(Job 是叢集層物件),controller 一啟動就對它 reconcile,然後:

```text
{"level":"error","error":"Node \"<old-node-name>\" not found","message":"Unable to fetch node"}
ERROR Observed a panic {"controller": "job", "panic": "assignment to entry in nil map", …}
```

退避重試 1s → 82s,18 分鐘內累計 **18 次**,永不停止。而外面看到的是:

```console
$ kubectl -n rcm get pod
runtime-class-manager-…   1/1   Running   0
```

**`RESTARTS 0`、`READY 1/1`。** 這是[地雷 1](#mine-1),今天最值得記的一顆。

### etcd 側全在,Pending 形狀對帳成立

七顆 RuntimeClass、Shim CR、Helm releases、cert-manager——**宣告全在,執行全無**。在這個狀態下丟一顆指名 `wasmtime-spin-v2` 的 pod:

```console
NAME                    READY   STATUS    AGE
spinapp-pending-shape   0/1     Pending   20s
Warning  FailedScheduling  0/1 nodes are available: 1 node(s) didn't match Pod's node affinity/selector.
```

**是 Pending,不是 ContainerCreating。** [Day 1 地雷 3](sprint3-day1-three-generations.md#mine-3) 預告的兩種失敗形狀,現在兩種都有實例了:

| | 沒寫 `scheduling` | 寫了 `scheduling.nodeSelector` |
|---|---|---|
| 誰擋下來 | kubelet(pod 已排上節點) | scheduler(pod 排不上) |
| 狀態 | `ContainerCreating` 無限重試 | `Pending` |
| 例子 | Day 1 手建的那顆 | rcm 建的這顆 |

!!! info "順帶更正 Day 1 的一句話"
    Day 1 地雷 3 說 RuntimeClass 的 selector「pod spec 裡看不到,要去查 RuntimeClass 才找得到」。**實測是錯的**:

    ```console
    $ kubectl get pod spinapp-pending-shape -o jsonpath='{.spec.nodeSelector}'
    {"spin":"true"}
    ```

    RuntimeClass 的 admission 外掛在 admission 階段就把 `scheduling.nodeSelector` **併進 pod 的活物件**。追查路徑因此更短:`kubectl get pod -o yaml` 看到一個自己沒寫過的 `nodeSelector`,那本身就是「有人在 admission 動過手腳」的線索。

### 恢復節點:12 秒的實作動作

重新貼標 → Job 5 秒內出現、17 秒 Complete——**而它寫進 `config.toml` 的那一段跟 Day 6 逐字相同,又是 1.x 的錯路徑**。[Day 6 地雷 1](sprint3-day6-spinkube-shim.md#mine-1) 在全新節點上完整重現,一個位元組都沒差:這個 bug 是確定性的。跑同一支修正腳本,handler 出現,繼續。

## 步驟 2: spin-operator——空窗 13 個月,零摩擦

版本現況(`gh api`,查證日當天):**spin-operator v0.6.1,距上次發版 13 個月又 2 天**。2025 上半年每三個月一版,之後停住。**但 repo 沒有封存,上個月還有 push**——「發版停了、開發沒停」,跟 [Day 1 的 Krustlet](sprint3-day1-three-generations.md)(最後 commit 是訃聞)是兩回事。

裝起來:

```console
$ kubectl apply -f spin-operator.crds.yaml        # 1 秒,無警告
$ helm upgrade --install spin-operator \
    oci://ghcr.io/spinframework/charts/spin-operator --version 0.6.1 …   # 23 秒
```

逐項相容性檢查:controller `restartCount=0`、cert-manager 的 Certificate READY True、validating + mutating webhook 都註冊了、**500 行 log 裡 API deprecation 警告 0 筆**。

!!! note "「最後發版日」單獨當健康度指標,在這裡給出錯誤排序"
    13 個月的空窗**沒有變成技術債**,理由不難理解:spin-operator 只用 `apps/v1` Deployment、`v1` Service、`admissionregistration.k8s.io/v1` webhook 與自己的 CRD——**全部是十年沒動過的穩定 API**。

    **真正被版本推著跑的是節點層**(containerd 的 plugin 路徑重排,Day 6 那顆雷),而那一層歸 rcm 管——rcm 反而是還在發版的那一個。

    評估工具健康度時,**「這個元件依賴哪些 API 的演進速度」跟「最後發版日」要一起看**。這正是 [Day 0 檢查清單](sprint3-day0-wasm-concepts.md#health-method)第 2、4 條說的事,在這裡撞到了實例。

順帶一格對照:`SpinAppExecutor` 是 Namespaced 資源,放錯 namespace 時 **validating webhook 在 admission 就擋下來,訊息指明欄位、值、原因**。跟 rcm 對同名 RuntimeClass 安靜跳過([Day 6 地雷 2](sprint3-day6-spinkube-shim.md#mine-2))正好一個大聲一個無聲——**「有 webhook」與「沒有 webhook」的差別,這兩天各給了一個實例。**

## 步驟 3: 驗收 (A)——SpinApp 回應請求

```yaml
apiVersion: core.spinkube.dev/v1alpha1
kind: SpinApp
metadata: {name: day7-operator-app, namespace: wasmlab}
spec:
  image: "ttl.sh/<tag>:2h"
  executor: containerd-shim-spin
  replicas: 1
```

`spec` 有 18 個頂層欄位,required 只有兩個。套用後 26 秒:

```console
spinapp/day7-operator-app     READY 1
$ curl -sS -i http://day7-operator-app.wasmlab.svc.cluster.local/static/index.html
HTTP/1.1 200 OK
etag: 8fa951d7…

Sprint3 Day6 SpinKube probe
```

**過。** 更有意思的是把 operator 產出的 Deployment 攤開——它到底替你做了什麼:

```text
runtimeClassName: wasmtime-spin-v2
command:          ["/"]                        ← 補上 Day 6 地雷 4 那一行
env:              SPIN_HTTP_LISTEN_ADDR=0.0.0.0:80
volumes:          runtime-config(Secret)、CA 憑證
labels:           core.spinkube.dev/app.…status: ready   ← Service selector 用它,自製就緒閘門
resources:        {}                           ← 沒有填,overhead 也沒有
```

**六樣東西,沒有一樣是魔法。** 這正是驗收 (B) 要證明的事。

## 步驟 4: 驗收 (B)——拿掉 operator,pod 照跑 {#gate-b}

刻意只保留三個欄位,界定最小必要集合:

```yaml
spec:
  runtimeClassName: wasmtime-spin-v2      # 1. the only field that routes to the shim
  containers:
    - name: app
      image: ttl.sh/<tag>:2h
      command: ["/"]                      # 2. Spin images have no Cmd/Entrypoint
      env:
        - name: SPIN_HTTP_LISTEN_ADDR     # 3. otherwise Spin listens on 127.0.0.1 only
          value: "0.0.0.0:80"
```

```console
$ kubectl -n wasmlab get deploy day7-native-app          # 21 秒後
day7-native-app   1/1

$ curl … http://day7-native-app.wasmlab.svc.cluster.local/static/index.html
HTTP/1.1 200 OK
etag: 8fa951d7…                ← 與 (A) 逐字相同
```

**同一顆映像、同一個 etag、同一條 shim——沒有 SpinApp、沒有 executor,operator 的 controller 從頭到尾沒收到任何事件。** 兩顆 pod 的關鍵欄位逐格相同(連 `nodeSelector` 都一樣,因為都是 admission 併進去的)。

### 對照組:刪掉那一行

同一份 YAML 只刪 `runtimeClassName`:

```console
day7-native-norc-…   0/1   RunContainerError
Warning  Failed  … runc create failed: … exec: "/": is a directory
```

**一句錯誤訊息,同時證明兩件事**:`command: ["/"]` 不是路徑,是只有 shim 讀得懂的記號(runc 照字面解讀就報「這是個目錄」);而 **`runtimeClassName` 是整條鏈唯一的開關**——映像、command、節點都不變,只有那一行決定 pod 落到哪個 handler。

!!! success "驗收兩半都過,而 (B) 比 (A) 值錢"
    (A) 證明的是「這個 operator 還能用」;**(B) 證明的是「就算它不能用,也沒關係」。**

    spin-operator 做的是把 `SpinApp` 翻譯成一份普通的 Deployment + Service,順手補上三個容易漏的欄位。**真正執行 wasm 的是 Day 1 建立的那條鏈——RuntimeClass → handler → containerd runtime 區塊 → shim 二進位檔——而那條鏈完全不知道 operator 存在。**

    這也是三條路線的分水嶺:wasmCloud 在自己的 host 行程裡跑,Kubernetes 半盲;**只有 SpinKube 走 Kubernetes 原生的執行路徑,而 (B) 就是「原生」二字的實證**。「spin-operator 13 個月沒發版」在風險評估上被高估了——**它停擺的後果是少一層便利,不是跑不動。**

### operator 不動節點

spin-operator 裝完(CRD + chart + executor),節點七份取樣**逐字全部 IDENTICAL,連目錄 mtime 都沒動**。

**分工是乾淨的:節點層歸 rcm,叢集層歸 spin-operator,交界就是 RuntimeClass 這一個物件。** 而今天最貴的一課是:**那個交界上的名字,兩邊的官方預設值對不上**——見[地雷 2](#mine-2)。

## 誠實的差距

- **「rcm 自動補新節點」只驗了手動標籤的用法。** 節點池層級標籤(`az aks nodepool --labels`)才是正式做法,也才可能讓 rcm 真的自動補——那需要多一次停開機,沒做。**它決定「rcm 有沒有解決節點層揮發」的最終答案。**
- **`spin kube` 子指令(spin-plugin-kube,整組最靜止的元件)今天沒用**,它跟 spin v4.0.2 的相容性沒驗。
- **shim 行程數只有兩次單點取樣**(一顆 pod 8 個、兩顆 10 個,不是乾淨的線性),沒有 Day 5 式的擬合,不能推算常駐與邊際。記憶體沒量。
- **今天的 app 仍不是 Day 5 那份 Rust 原始碼**,「同一支程式跨三條路線」在 SpinKube 側仍未接上。
- **多副本、HPA、rollout 行為都沒測**——operator 產出的 Deployment `resources` 是空的,`overhead` 也沒填,這對容量規劃的影響沒有評估。
- **只有單節點。**

## 地雷記錄

### 地雷 1:rcm 的 Job controller 遇到「Job 在、節點不在」會 panic,而 pod 顯示 Running、RESTARTS 0 {#mine-1}

**症狀**:叢集重開機(或縮容、spot 回收、節點升級)之後,controller pod `1/1 Running`、`restartCount 0`、`kubectl get shim` 無異常。**只有翻 log 才看得到它每隔一段時間 panic 一次**,退避到 82 秒以上,永不停止。

**根因**:install Job 是叢集層物件,節點消失它不會消失。`getNode()` 在節點不存在時走 `client.IgnoreNotFound(err)`——**把 NotFound 轉成 `nil` 並回傳一顆空 Node**,呼叫端看到「沒有錯誤」,拿著 `Labels == nil` 的空 Node 對 nil map 賦值就 panic。controller-runtime 攔下 panic 不讓行程死,改成退避重試——**於是它變成一個永遠不會自癒、也永遠不會被健康檢查抓到的迴圈。**

**繞法**:刪掉指向已不存在節點的 Job。它沒有 ownerReference 指向 Node,不會被 GC;rcm 也沒設 `ttlSecondsAfterFinished`。

**為什麼要記**:上游沒有這個 issue(逐關鍵字查過),而觸發條件在雲上是**常態不是例外**——任何會換掉 VM 的操作都會留下「Job 在、節點不在」。

**判斷準則**:**`restartCount == 0` 不等於 controller 健康。** controller-runtime 的 panic 攔截會把「每次 reconcile 都炸」偽裝成一顆正常的 pod。看 `controller_runtime_reconcile_errors_total`,或直接 grep log 的 `Observed a panic`,不要看 pod 狀態。

### 地雷 2:兩份官方資產對同一個 RuntimeClass 的 handler 各寫各的,而 `handler` 是 immutable {#mine-2}

**症狀**:照官方文件把四個元件依序裝完,SpinApp 的 pod 卡在 `no runtime for "spin" is configured`——**節點上明明有 handler,只是叫 `spin-v2`**。每個儀表板都是綠的。

**根因**:兩份官方 release 資產打架——

```yaml
# containerd-shim-spin v0.25.1 的 Shim CR
runtimeClass: {name: wasmtime-spin-v2, handler: spin-v2}

# spin-operator v0.6.1 的 spin-operator.runtime-class.yaml(官方安裝指引第 2 步)
metadata: {name: wasmtime-spin-v2}
handler: spin
```

兩件事讓撞名升級成卡死:**`handler` 是 immutable**(實測 `field is immutable`,先寫的贏、改不回來、只能刪重建);**rcm 又不對帳**([Day 6 地雷 2](sprint3-day6-spinkube-shim.md#mine-2)),兩種順序都實測過,都是「儀表板全綠、pod 起不來」。附帶一個更難看見的細節:**滾動更新時舊 pod 還 Running,Deployment 的 `AVAILABLE` 維持 1**,只有新 pod 卡著。

**修法**:走 rcm 這條路線就**不要套 `spin-operator.runtime-class.yaml`**——那是給另一條安裝路線(kwasm/k3d 那類 handler 叫 `spin` 的做法)用的,但官方文件把它列在通用步驟裡,沒有說「用 rcm 的話跳過」。已經撞上就刪掉重建再戳 Shim。

**判斷準則**:跨元件共用一個叢集層物件的名字時,**確認每個元件對內容的期望,不要只確認名字**。RuntimeClass 三個條件湊齊——叢集層、沒 namespace、handler immutable——就是「先寫的贏,後來的只能刪」。

### 地雷 3:`command: ["/"]` 是 shim 專屬記號;漏了 `runtimeClassName` 會得到 `exec: "/": is a directory` {#mine-3}

**症狀**:pod 反覆 `RunContainerError`,訊息 `exec: "/": is a directory`——**看起來像 command 寫錯,沒有一個字提到 wasm 或 RuntimeClass**。

**根因**:`command: ["/"]` 不是在指一個叫 `/` 的執行檔,是 containerd-shim-spin 表示「跑整個 Spin app」的記號,shim 不會去 exec 它。**pod 一旦少了 `runtimeClassName`(打錯、被 values 覆蓋、複製時漏掉),就落到 runc 上,runc 照字面把 `/` 當路徑**。

**為什麼要記**:`command: ["/"]` 在任何官方文件裡都查不到——它只存在於 spin-operator 產出的 Deployment 裡,是逆向看出來的。**而錯誤訊息會把追查方向帶偏到 command 上。**

**判斷準則**:非 runc 執行體的映像報出字面解讀的 exec 錯誤時,**第一件事查 `{.spec.runtimeClassName}`,不是查 command**。更一般地:**shim 會重新定義 pod spec 某些欄位的語意,那些欄位落回 runc 不會報「你用錯了」,只會報一個字面的錯。**

## 帶得走的東西

- **operator 的價值要用「拿掉它」來量。** 攤開它產出的物件、手寫一份最小等價物、跑通——這一輪做完,你才知道它是便利層還是執行機制,而這決定了它停止維護時你要多緊張。
- **`restartCount == 0` 不等於健康。** panic 被框架攔截、錯誤被退避重試吞掉——controller 的健康度在 log 與 reconcile 指標裡,不在 pod 狀態欄。
- **「發版停了」要拆開看停的是哪一層。** 只依賴穩定 API 的元件可以停很久不出事;貼著快速演進面(containerd 設定、節點映像)的元件停三個月就會出事。**看「依賴的演進速度」,不是只看日期。**
- **同一個生態系的兩個官方元件也會打架。** handler 叫 `spin` 還是 `spin-v2`,兩份 release 資產各執一詞,而 immutable 欄位讓先到者永遠佔位。照官方文件逐步裝,不代表每一步彼此相容。
- **手動標籤是節點的短期記憶。** 要讓宣告式系統在 VM 揮發後自癒,每一層的宣告都得有一個揮發不掉的來源——`kubectl label` 不是,節點池設定才是。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[spin-operator](https://github.com/spinframework/spin-operator)** —— 今天的主角。README 寫明它「watches SpinApp Custom Resources and realizes the desired state」;release 頁附的四個資產裡,`spin-operator.runtime-class.yaml` 就是地雷 2 的另一半。
- **[runtime-class-manager 的 job_controller.go](https://github.com/spinframework/runtime-class-manager)** —— 地雷 1 的出處。`getNode()` 與 `updateNodeLabels()` 兩個函式加起來不到三十行,值得當成「錯誤被靜默轉換」的案例讀。
- **[Kubernetes RuntimeClass 文件](https://kubernetes.io/docs/concepts/containers/runtime-class/)** —— 回頭再讀一次「scheduling」一節:admission 把 `nodeSelector` 併進 pod 的行為就寫在裡面,Day 1 當初漏看的正是這一段。

## 下一步

三條路線全部走完了。SpinKube 的位置現在很清楚:**節點層歸 rcm(裝 83.9 MiB 的 shim、改 6 行設定),叢集層歸 operator(純便利層),而交界是一顆名字有爭議的 RuntimeClass。**

[Day 8](sprint3-day8-reversibility.md) 要把這一切**拆乾淨**,拿 Day 1 存的基準逐字比對。規則已經被 Day 5 改過:**解除安裝與比對必須在同一個 VM 生命週期內完成,中途不能停機**——否則驗到的是「VM 被換掉了」,不是「拆得乾淨」。而 Day 6 讀 rcm 原始碼時已經預埋了一個問題:它的 `RemoveRuntime()` 是字串比對後整段刪除,**手動修過 plugin domain 的那一段,字串已經對不上了**——拆除時會發生什麼,那天見。

---

!!! quote ""
    SpinKube 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
