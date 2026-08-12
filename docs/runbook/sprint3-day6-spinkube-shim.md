# Day 6: SpinKube(上)——operator 把節點改好了,只是改在一個 containerd 不看的地方

![SpinKube 官方標誌](../assets/logos/spinkube-icon-color.svg){ align=right width="88" }

> 三條路線的最後一條,也是唯一一條**由 operator 替你改節點**的。[Day 4](sprint3-day4-wasmedge.md) 手動做過同一件事——下載 shim、改 `config.toml`、重啟 containerd——今天把它交給 `runtime-class-manager`,看自動化到底做了什麼、又在哪一步出事。

!!! abstract "你在課程的哪裡"
    - **Day 4**:手動把 WasmEdge 的 shim 裝上節點,量出「第 3 步開始節點不一樣」。
    - **今天**:SpinKube 的節點側。cert-manager → runtime-class-manager → shim 佈建。**spin-operator 是 Day 7 的事,今天不碰。**
    - **Day 8**:把這一切拆乾淨,拿 Day 1 的基準驗證。

這一天要先回答整個 sprint 風險最高的問題:「SpinKube 在現行 AKS 節點映像上還裝得起來嗎?」**裁決是裝得起來——但 `runtime-class-manager` v0.2.0 開箱不生效,而且不生效的方式是完全靜默的。差一個字串。**

## 今天要走的路

| 步驟 | 做什麼 | 節點變了嗎 |
|---|---|---|
| 1 | 查版本現況,修正一筆 health check | — |
| 2 | 本機打包 Spin App,推公開 registry | — |
| 3 | cert-manager | **沒有** |
| 4 | runtime-class-manager | **沒有** |
| 5 | Shim CR + 節點標籤 | **從這裡開始不一樣** |
| 6 | 處理一個 RuntimeClass 撞名 | 沒有(只動 API server) |
| 7 | 跑起來——先撞牆,再修一個字串 | 兩行置換 |

!!! danger "先講一件事:不要照 AKS 官方文件裝"
    `learn.microsoft.com/…/deploy-spinkube`(`updated_at: 2026-07-03`,查證 2026-08-11)教的安裝路徑是從明文 `http://kwasm.sh/` 裝 kwasm-operator——**那個專案已於 2026-05-15 封存退休**,README 自己指向本章要用的 runtime-class-manager,網域只保證解析到 2027-01-20。詳見[地雷 6](#mine-6)。本章走的是上游現行的那條路。

## 步驟 1: 版本現況——先修正自己的 health check

`gh api` 查證(repo 已由 `spinkube/*` 遷至 `spinframework/*`):

| repo | 最新 release | 發佈日 | repo 最後 push |
|---|---|---|---|
| `spin` | v4.0.2 | 2026-06-24 | 2026-08-10 |
| `containerd-shim-spin` | v0.25.1 | 2026-06-12 | 2026-06-22 |
| `runtime-class-manager` | v0.2.0 | 2026-03-17 | 2026-08-03 |
| **`spin-operator`** | **v0.6.1** | **2025-07-09** | 2026-07-20 |

只看發版日期會下這樣的判斷:「spin-operator 13 個月沒發版,同層元件同步靜止」——**後半句不成立**。shim 今年連發三版,rcm 今年才第一次有正式 release、八月還有 push。**今天要動手的兩個元件都是活的;真正停住的是 Day 7 才會碰的 spin-operator。**

這一條直接改寫風險排序:**真正的風險集中在 Day 7,不在今天。**

宣告面還有一段要先讀——rcm 怎麼決定 containerd 的 plugin 路徑(`internal/containerd/configure.go`,v0.2.0 逐字):

```go
func generateConfig(shimPath string, runtimeName string, runtimeOptions map[string]string, configData []byte) string {
	// Config domain for containerd 1.0 (config version 2)
	domain := "io.containerd.grpc.v1.cri"
	if strings.Contains(string(configData), "version = 3") {
		// Config domain for containerd 2.0 (config version 3)
		domain = "io.containerd.cri.v1.runtime"
	}
	…
}
```

**它不問 containerd 的版本,它 grep 設定檔內文有沒有 `version = 3` 這個字串。** 而 [Day 1](sprint3-day1-three-generations.md) 就看過,AKS 的 `config.toml` 是一份混血檔:**第一行宣告 `version = 2`,內容卻用 2.x 的 plugin 路徑**。

把這兩件事放在一起,結局已經可以預測了。先往下裝,看它怎麼發生。

## 步驟 2: 本機打包,第四種 OCI 格式

`spin` CLI v4.0.2,`static-fileserver` 範本,推上 ttl.sh(匿名公開 registry,免開 ACR):

```console
$ spin registry push ttl.sh/<tag>:1h
Pushed with digest sha256:5e551527…
```

拉回 manifest 一看,**Spin 的打包跟前三種都不一樣——兩層**:

```json
"layers": [
  { "mediaType": "application/vnd.wasm.content.layer.v1+wasm",         "size": 3781679 },
  { "mediaType": "application/vnd.fermyon.spin.application.v1+config", "size": 609 }
]
```

第二層那 609 B 是 Spin 的 locked app(trigger、route、component 來源);28 B 的 `index.html` **被 base64 內嵌進去而不是另開一層**。而 image config 只有 209 B,**沒有 `Cmd` 也沒有 `Entrypoint`**——這件事在步驟 7 會造成失敗。

併進 [Day 4 那張打包表](sprint3-day4-wasmedge.md#gate),現在是四欄:

| | wasmCloud | runwasi tar | runwasi wasm-layer | **Spin App** |
|---|---|---|---|---|
| layer mediaType | `application/wasm` | `…layer.v1.tar` | `…wasm.component.layer.v0+wasm` | **`…wasm.content.layer.v1+wasm` + `…fermyon.spin.application.v1+config`** |
| 層數 | 1 | 1 | 1 | **2** |
| 進入點 | 列 WIT 介面 | `Entrypoint` | `Entrypoint` + label | **沒有,config label 指向 locked app** |
| `os` 欄位 | `wasip2` | `wasip1` | `wasip1` | **`wasip1`(但 payload 其實是 component)** |
| 誰讀得懂 | wasmCloud | WasmEdge | WasmEdge | containerd-shim-spin |

**四種打包、四種讀法。wasm 的 OCI 打包沒有共通約定,`mediaType` 是各家自己定的——「同一個 registry」不等於「同一種東西」。** 還有一處值得盯著:Spin 的 wasm 前 8 個位元組是 `0d 00 01 00`(component),而 `os` 欄位寫 `wasip1`——**`os` 欄位在 Spin 的打包裡不能拿來判斷 payload 形態。**

## 步驟 3–4: 兩個 Helm chart,節點七份取樣全部不變

```console
$ helm upgrade --install cert-manager jetstack/cert-manager … --version v1.21.1   # 38 秒
$ helm upgrade --install runtime-class-manager \
    oci://ghcr.io/spinframework/charts/runtime-class-manager --version 0.2.0      # 9 秒
```

兩步之後,節點的七份取樣(config.toml、config dump、runtimeHandlers、shim 清單、目錄、標籤、RuntimeClass)**全部 IDENTICAL**。

順帶查出一件官方安裝指引不會告訴你的事:**cert-manager 是 spin-operator 的相依,不是 rcm 的。** rcm 的 chart 只有六個 template,沒有 webhook、沒有 Certificate、沒有對 cert-manager 的任何引用。官方把它排在第一步,讀者很容易以為裝 shim 需要它——今天先裝純粹是為了 Day 7。

## 步驟 5: 節點從「貼標籤」那一刻開始不一樣

套用 `containerd-shim-spin` v0.25.1 官方附的 Shim CR(逐字未改):

```yaml
spec:
  nodeSelector: {spin: "true"}
  fetchStrategy:
    platforms:
      - {os: linux, arch: x86_64, location: "…/containerd-shim-spin-v2-linux-x86_64.tar.gz",
         sha256: "1755fbeb…"}
  runtimeClass: {name: wasmtime-spin-v2, handler: spin-v2}
```

**只 apply CR、不貼標籤:什麼都沒發生。** `kubectl get shim` 是 `READY 0 / NODES 0`,沒有 Job,節點取樣不變。**`nodeSelector` 是實打實的閘門,佈建範圍是宣告式的,而且預設是空集合**——這是 rcm 相對手動路線最直接的優點。

貼上標籤:

```console
$ kubectl label node --all spin=true
```

10 秒內出現一個 **Job**(不是 DaemonSet),16 秒 `Complete`。它的結構值得攤開:

```text
initContainer: downloader   → 下載 tarball,逐字比對 sha256,不符就失敗
container:     provisioner  → 掛 hostPath /,裝 shim、改 config.toml、經 D-Bus 重啟 containerd
兩者皆 privileged
```

裝完的節點差異:

```text
config.toml       +6 行(檔尾,# RCM runtime config for spin-v2 起)
/opt/rcm/bin/containerd-shim-spin-v2     83.9 MiB
/opt/rcm/rcm-lock.json                   記錄 shim 名、sha256、路徑
節點標籤          +spin=true、+spin-v2=provisioned
```

**注意落點:`/opt/rcm/bin/`,不在 `PATH` 裡。** Day 4 式的「掃 `/usr/bin` 檢查有沒有裝」在這裡會誤判成沒裝([地雷 5](#mine-5))。rcm 在 `runtime_type` 直接寫絕對路徑,containerd 2.x 接受。

而 `rcm-lock.json` 是手動路線完全沒有的東西:**手動裝完,節點上沒有任何地方記得「這支檔案是誰放的、對應哪個版本」。**

### 但是——那 6 行寫在哪個路徑下?

```toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.spin-v2]
```

**1.x 的路徑。** 步驟 1 讀到的那段 `strings.Contains` 走進了錯的分支。三層一起看:

```text
config.toml 命中 spin-v2 的行數 = 4      檔案總行數 = 27       ← 宣告面:有
containerd config dump 命中     = 0      dump 總行數 = 308      ← 實際載入面:沒有
node.status.runtimeHandlers = ['', 'runc', 'untrusted']         ← 綁定粒度:沒有
```

而控制面這一側,**每一個指標都是綠的**:

```console
$ kubectl get shim -o wide
NAME      RUNTIMECLASS       READY   NODES
spin-v2   wasmtime-spin-v2   1       1

節點標籤:  "spin-v2": "provisioned"
install Job: Complete,log 沒有任何 warning,最後一行是 restarting containerd
```

containerd 真的被 rcm 重啟過(時間戳可查)。**重啟做了,設定沒生效。** 丟一顆 pod 進去:

```console
Warning  FailedCreatePodSandBox  kubelet
  no runtime for "spin-v2" is configured
```

這就是[地雷 1](#mine-1),今天最大的一顆。

## 步驟 6: RuntimeClass 撞名——Day 1 埋的種子發芽了

叢集上本來就有一顆 `wasmtime-spin-v2`——**[Day 1 驗收第二半](sprint3-day1-three-generations.md#step-4)刻意建的「handler 不存在」對照組**。而 SpinKube 官方 Shim CR 要建的 RuntimeClass,**名字就叫 `wasmtime-spin-v2`**(官方 YAML 註解寫明:這是 spin-operator 的預設值)。

**不是巧合,是 Day 1 挑名字時就撞上了 SpinKube 的預設。** 先不刪,看 rcm 怎麼處理:

```console
{"level":"debug","shim":"spin-v2","message":"RuntimeClass found: wasmtime-spin-v2"}

$ kubectl get runtimeclass wasmtime-spin-v2 -o yaml
handler: wasmtime-spin-v2       ← 還是 Day 1 那個壞 handler
resourceVersion: "2402"         ← 完全沒被改過
```

**rcm 沒有覆蓋,也沒有報錯。** 原始碼:`runtimeClassExists()` 只用名字查存在,存在就整個跳過([地雷 2](#mine-2))。諷刺的是,它的部署函式用的是 server-side apply 加 `Force: true`——**只要走得到就會覆蓋,但它走不到**。

處理決策(三條理由都寫進日誌了):**刪掉 Day 1 那顆,讓 rcm 重建;對照組的語意用新名字 `wasm-day1-missing-handler` 保住**——它要驗的是「handler 不存在會怎樣」,跟名字無關。

刪掉之後還有一層:**rcm 不 watch RuntimeClass,刪了它不會自動重建**([地雷 3](#mine-3))。要戳一下 Shim CR(加個 annotation)才會觸發 reconcile。重建出來的那顆:

```yaml
handler: spin-v2
metadata:
  ownerReferences:
    - {kind: Shim, name: spin-v2, controller: true}
scheduling:
  nodeSelector: {spin: "true"}
```

**[Day 1 地雷 3](sprint3-day1-three-generations.md#mine-3) 的預告成立:rcm 建的 RuntimeClass 帶 `scheduling.nodeSelector`。** 對照 Day 4 手動建的 `wasmedge`(沒有 `scheduling`):多節點時,前者讓 scheduler 先過濾節點,後者讓 pod 排到任何節點再失敗。**這是行為差異,不是裝飾。** 而 `ownerReferences` 指回 Shim——**刪掉 Shim CR,RuntimeClass 會被 GC 連帶刪除**,Day 8 的拆除驗收要把這條算進去。

## 步驟 7: 跑起來——修一個字串就好

先撞第一面牆(步驟 2 預告過的):

```console
Warning  Failed  Error: … failed to generate spec: no command specified
```

Spin 映像沒有 `Cmd`/`Entrypoint`,直接手寫 Pod 要自己補 `command: ["/"]`([地雷 4](#mine-4))。補上之後撞第二面牆——就是地雷 1 的 `no runtime for "spin-v2" is configured`。

修法只有一個字串:把 rcm 寫的那兩行 plugin domain 換成 `io.containerd.cri.v1.runtime`,重啟 containerd。**不必動 shim、不必動 Shim CR、不必重裝任何東西:**

```console
--- 有效設定裡出現幾次 spin-v2(改前是 0)
3
$ kubectl get node -o jsonpath='{…runtimeHandlers}'
['', 'runc', 'spin-v2', 'untrusted']

$ kubectl logs spinapp-probe-cmd
Serving http://0.0.0.0:80

$ curl -sS -i http://<pod-ip>/static/index.html
HTTP/1.1 200 OK

Sprint3 Day6 SpinKube probe
```

**本機打包 → 公開 registry → 節點拉取 → containerd-shim-spin 執行 → HTTP 200,整條走完——而且沒有用到 spin-operator。**

## rcm 自動化了 Day 4 手動做的哪些事

今天最有價值的一張表:

| Day 4 手動 | rcm 自動化 | 誰做得比較好 |
|---|---|---|
| `curl` 下載,自己挑版本與架構 | `fetchStrategy.platforms`,依節點 `arch` 選 | **rcm**——宣告式、跨架構 |
| **沒有校驗雜湊** | downloader 逐字比對 sha256,不符就失敗 | **rcm**——供應鏈這格 Day 4 是空白 |
| 沒有任何安裝紀錄 | `rcm-lock.json` 記 shim 名、sha256、路徑 | **rcm** |
| 手寫 8 行設定,**先離線 parse 確認路徑才寫** | `strings.Contains(cfg, "version = 3")` 猜路徑 | **Day 4 手動做得對**——rcm 猜錯,而且錯了不報 |
| 重啟時機人決定 | provisioner 自己重啟 | rcm 方便,但時機不可控、沒有 drain |
| RuntimeClass 沒帶 `scheduling` | 帶 `scheduling.nodeSelector` + `ownerReferences` | **rcm** |
| 佈建範圍 = 人工連進哪顆節點 | `nodeSelector`,預設空集合 | **rcm**——可稽核 |
| 新節點加入:什麼都不會發生 | controller watch `Node`,理論上會補 | **待驗**(Day 7 的第一件事) |

**一句話:rcm 把「取得、校驗、放置、記錄、範圍、生命週期」六件事都做了,唯一做壞的是「決定寫到哪個 plugin 路徑」——而那正是 Day 4 靠離線 parse 先驗過才敢寫的那一步。**

## 驗收 checkpoint

| 要求 | 判定 | 支撐證據 |
|---|---|---|
| 節點被標記為已佈建 shim | **達成** | 節點標籤 +`spin-v2: provisioned`;`kubectl get shim` READY 1/1;`rcm-lock.json` |
| 指出 containerd 設定被改了哪一段 | **達成** | config.toml 檔尾 6 行,999 B → 1247 B,diff 逐字 |
| (加碼)那一段有沒有生效 | **原本沒有,改一個字串後有** | dump 命中 0 → 3;handler 出現 |
| (加碼)端到端跑通 | **達成** | HTTP/1.1 200,body `Sprint3 Day6 SpinKube probe` |

!!! note "「已佈建」與「能跑」是兩件事"
    **「節點被標記為已佈建」與「節點真的能跑」是兩件事**——今天這兩件事在同一顆節點上先後成立與不成立過。驗收只要求前者,前者確實達成;**但如果只驗到這裡就停手,交出去的是一份所有指標都綠、pod 起不來的環境。**

## 誠實的差距

- **rcm 面對新 VM 會不會自動重新佈建,今天沒驗。** 它有 watch `Node`(啟動 log 可查),但那是「理論上會」。驗它需要一次停開機——答案在 Day 7 叢集重啟後的前五分鐘。
- **Spin 映像為什麼不需要 Day 4 的 `use_local_image_pull` 就拉得動,沒有解釋。** 合理猜測是打包形態(沒有 tar 層要解開),**但沒做對照實驗**。
- **shim 行程數只有單次取樣**(一顆 pod 8 個行程,量級同 Day 5 的 wasmedge),沒有線性擬合,不能跟 Day 5 的常駐/邊際數字並列。記憶體完全沒量。
- **今天跑的不是 Day 5 那份 Rust 原始碼**(用官方預編的 static-fileserver 元件),「同一支程式跨三條路線」這條線在 SpinKube 側斷了,要補一次編譯才接得上。
- **plugin domain 的修正是手改,不是上游修法。** 沒測改 AKS 設定檔第一行的做法,也沒向上游開 issue(確認過沒有對應的 open issue)。
- **只有單節點**,`nodeSelector` 的過濾與 rollout 行為沒有多節點實測。

## 地雷記錄

### 地雷 1:rcm 用「設定檔內文有沒有 `version = 3`」猜 plugin 路徑,在 AKS 上猜錯,而且錯了不報 {#mine-1}

**症狀**:Shim `READY 1/1`、節點標 `provisioned`、shim 在磁碟上、containerd 被重啟過、install Job `Complete` 且 log 無 warning——**每一個指標都綠**。而指名該 RuntimeClass 的 pod 卡在 `no runtime for "spin-v2" is configured`。

**根因**:`generateConfig()` 不查 containerd 版本,而是 `strings.Contains(configData, "version = 3")`。AKS 的 `config.toml` **宣告 `version = 2`、內容用 2.x 語法**,於是 rcm 走進 1.x 分支,寫出的區塊被 containerd 2.3.3 **靜默丟棄**([Day 4 地雷 2](sprint3-day4-wasmedge.md#mine-2) 的機制,這次是 operator 自己踩)。

**三層診斷**:config.toml 命中 4 行/27 行;dump 命中 0/308 行;`runtimeHandlers` 沒有 `spin-v2`。

**修法**(實驗環境的做法,不是正式環境的正解):把那兩行 domain 換成 `io.containerd.cri.v1.runtime`,重啟 containerd。不必動 shim、不必重裝。

**為什麼要記**:官方 `supported_distros.md` 把 Azure Kubernetes 標為 ✅ officially supported,上游沒有對應的 open issue。**「猜設定檔語法版本」跟「查執行檔版本」不是同一件事**,而混血設定檔在雲廠商的節點映像上很常見。

**判斷準則:任何自動化改 containerd 設定的元件,裝完一律用 `containerd config dump` 驗,不看 `config.toml`。前者是 containerd 認的,後者只是你寫了什麼。**

### 地雷 2:rcm 對同名的既有 RuntimeClass 只檢查「存在」,不對帳欄位 {#mine-2}

**症狀**:叢集上先有一顆同名 RuntimeClass(handler 是別的值),套上 Shim CR 之後 `kubectl get shim` 照樣 READY 1,而那顆 RuntimeClass 的每一個欄位都維持原樣(`resourceVersion` 沒變)。

**根因**:`if !rcExists { handleDeployRuntimeClass(…) }`,而 `runtimeClassExists()` 只用名字 `Get` 一次。

**為什麼特別容易中**:`wasmtime-spin-v2` 這名字是 spin-operator 的預設值,相當通用;而 **RuntimeClass 是叢集層資源,沒有 namespace 可以隔離**——前一次安裝的殘留、其他 wasm 方案、手動實驗都可能佔住這個名字。

**判斷準則**:裝 rcm 之前先 `kubectl get runtimeclass <name> -o yaml`;有同名的**先刪再讓 controller 重建**,不要指望它對帳。刪完還要看下一顆。

### 地雷 3:rcm 不 watch RuntimeClass,刪掉之後不會自動重建 {#mine-3}

**症狀**:刪掉 RuntimeClass 之後等 20 秒,controller log 完全沒反應。`kubectl get shim` 的 `RUNTIMECLASS` 欄位還印著那個已經不存在的名字。

**根因**:controller 註冊的 EventSource 只有 `Shim`、`Node`、`Job` 三種(啟動 log 逐字列出),RuntimeClass 的刪除事件進不了 workqueue。

**修法**:對 Shim 做任何一次 update(`kubectl annotate shim <name> k=v --overwrite`)觸發 reconcile,RuntimeClass 隨即重建。

**跟地雷 2 是一對**:先刪同名物件、然後以為 controller 會補上——**結果一個都不會發生,而且沒有任何訊息告訴你在等一個不會來的事件。**

### 地雷 4:Spin 映像沒有 `Cmd`/`Entrypoint`,手寫 Pod 會停在 `no command specified` {#mine-4}

**症狀**:shim 已生效、映像拉下來了,pod 反覆 `failed to generate spec: no command specified`。**錯誤訊息裡沒有 wasm、沒有 spin、沒有 shim,完全不像 wasm 問題。**

**根因**:image config 只有 209 B,進入點資訊在 label 指向的 locked app 層裡,CRI 不認得那個 label。

**修法**:Pod spec 補 `command: ["/"]`(spin-operator 產出的 Deployment 就是這麼做的,Day 7 會看到)。

**為什麼要記**:只有在「不用 operator、自己寫 Pod」時會遇到——**而教學上那正是最該做一次的事,因為它把 operator 到底替你做了什麼攤開來。**

### 地雷 5:rcm 把 shim 裝在 `/opt/rcm/bin/`,Day 4 式的 `ls /usr/bin` 檢查會誤判成「沒裝」 {#mine-5}

**症狀**:Job `Complete`、log 印 `shim installed`,但沿用 Day 4 的取樣器(錨定 `/usr/bin` 與 `/usr/local/bin`)比對,結果 IDENTICAL——看起來什麼都沒裝。

**根因**:rcm 用自己的目錄,並在 `runtime_type` 直接寫絕對路徑,所以不需要進 `PATH`。官方文件沒寫落點。

**判斷準則**:換一條佈建路線就要重新確認落點。可靠來源優先序:`containerd config dump` 的 `runtime_type` → `rcm-lock.json` → install Job 的 log。**不要用「掃某幾個目錄」當存在性檢查,那是在賭別人跟你放同一個地方。**

### 地雷 6:AKS 官方文件教你安裝一個已封存退休的專案 {#mine-6}

**現象**:`deploy-spinkube` 文件(`updated_at: 2026-07-03`,無任何橫幅)的安裝步驟是 `helm repo add kwasm http://kwasm.sh/kwasm-operator/` 加節點 annotation 觸發。

**事實**(查證 2026-08-11):KWasm 已於 2026-05-15 封存退休。另外三處落差:helm repo 是明文 `http://`;annotation 觸發撐不過 AKS 節點輪替(上游 issue #222 自己記載,Day 5 實測到的節點層揮發是同一件事);釘的版本全面落後(operator v0.5.0、cert-manager v1.14.3)。

**判斷準則**:雲廠商文件引用第三方 helm repo 時,先查那個 repo 的 `archived` 欄位與 README 首段。文件的 `updated_at` 只代表檔案被碰過,不代表第三方連結被複查過。(那條路沒有實測跑過,判定靠封存狀態,不是失敗現場。)

### 地雷 7:上游說的「支援 containerd 2.x」,支援的是設定檔語法版本 {#mine-7}

[地雷 1](#mine-1) 的續集。上游 issue #371「support containerd 2.0」已 completed 關閉——從 issue 列表看,這件事解決了;而修它的 PR 內文自述「or, more accurately, containerd config **syntax** version 3」,那個 syntax 沒有出現在任何使用者文件裡,且該 PR 的 AKS 測試格未勾選。`main` 與 v0.2.0 的判斷程式碼逐字相同(查證 2026-08-11),**沒有修復在路上**。

**判斷準則**:「支援 X 2.x」要追問**用什麼判斷**。這也決定了回報要怎麼寫:不是「請支援 containerd 2.x」,是「請不要用設定檔語法版本推論 containerd 版本」。

## 帶得走的東西

- **自動化會把人的判斷換成一個啟發式,而啟發式錯的時候不會舉手。** Day 4 手動路線之所以沒踩這顆雷,是因為動手前先離線 parse 過;rcm 用一個字串比對代替了那次驗證。**評估一個 operator,要看的不是它做了多少事,是它在哪些地方用猜的。**
- **「所有指標都綠」的環境可以是壞的。** Shim READY、節點 provisioned、Job Complete、log 無 warning——四個綠燈,pod 起不來。**每一層的「成功」只代表那一層自己的動作做完了,沒有一層驗過下一層真的收到。**
- **叢集層的名字是全域資源。** RuntimeClass 沒有 namespace,`wasmtime-spin-v2` 這種通用名字誰都可能先佔住,而 controller 對同名物件的處理方式(對帳/跳過/覆蓋)決定了殘留會不會變成陷阱。
- **供應鏈與紀錄是自動化真正的價值所在。** sha256 校驗、lock 檔、宣告式範圍——這三格 rcm 都比手動做得好,而它們恰好是手動流程最常省略的。
- **對照組要提前種。** Day 1 那顆「handler 不存在」的 RuntimeClass在今天長成了兩顆地雷的觸發器;Day 4 的手動安裝在今天變成了對照表的另一半。**這個 sprint 的每一天都在替後面的天數準備對照材料。**

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[runtime-class-manager](https://github.com/spinframework/runtime-class-manager)** —— 今天的主角。README 寫明它管「RuntimeClass 與 containerd shim 二進位檔的建立與安裝」;`internal/containerd/configure.go` 就是地雷 1 那段 `strings.Contains` 的出處,值得整檔讀完。
- **[containerd-shim-spin](https://github.com/spinframework/containerd-shim-spin)** —— shim 本體。release 附的 Shim CR 是今天逐字使用的那份,注意它註解裡自己說明了為什麼 RuntimeClass 叫 `wasmtime-spin-v2`。
- **[Day 4 的地雷 2](sprint3-day4-wasmedge.md#mine-2)** —— 站內連結。1.x plugin 路徑被靜默丟棄的完整機制與離線驗證法,今天 rcm 踩的就是它。

## 下一步

節點側裝好了,但**修那個字串是手動的**——而 Day 5 已經證明節點層改動撐不過一次停機。所以 [Day 7](sprint3-day7-spinkube-operator.md) 叢集重啟後的前五分鐘有一個一次性的觀察窗口:**新 VM 上,rcm 會自動重新佈建嗎?** 標籤還在嗎?如果它重佈建,它會再寫一次錯的路徑嗎?

之後才是 Day 7 的正題:裝那個停了 13 個月的 spin-operator,以及本 sprint 最重要的一個驗證——**不經 operator、只用 Deployment + `runtimeClassName` 跑同一支程式**。如果成立,就證明了 operator 是便利層,真正的執行機制是 Day 1 建立的那條 RuntimeClass 鏈。

---

!!! quote ""
    SpinKube 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
