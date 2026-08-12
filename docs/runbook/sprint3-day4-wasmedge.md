# Day 4: WasmEdge——第 5 步才動到節點,而「同一支程式」不存在

![WasmEdge 官方標誌](../assets/logos/wasmedge-icon-color.svg){ align=right width="82" }

> 三條路線的第二條。[Day 2](sprint3-day2-wasmcloud.md) 的 wasmCloud 完全不碰節點(五份 diff 零差異),Day 6 的 SpinKube 會改 containerd 設定。**WasmEdge 卡在中間**——今天要量出它到底落在哪,而答案精確到「第幾步」。

!!! abstract "你在課程的哪裡"
    - **Day 2–3**:wasmCloud。不改節點,代價換成它自己的一整套抽象。
    - **今天**:WasmEdge。**這條路要自己動節點**,所以每做完一步就比一次基準,要能講出「第幾步開始不一樣」。
    - **Day 5**:三條路線的啟動成本與資源實測。今天刻意一個效能數字都不宣稱。

動手前有一個要先驗證的前提:「WasmEdge 在 AKS 的 containerd 版本上有沒有可用的整合路徑?」如果沒有,今天要降級成「在單一 pod 內跑 WasmEdge」。**結論是有,不用降級**,但找到它的過程本身就是三顆地雷。

## 今天要走的路

| 步驟 | 做什麼 | 節點變了嗎 |
|---|---|---|
| 1 | 先確認整合路徑存在:三層證據 | — |
| 2 | 把 shim 放上節點 | **沒有** |
| 3 | 改 containerd 設定 + 重啟 | **從這裡開始不一樣** |
| 4 | 建 RuntimeClass | 沒有(只動 API server) |
| 5 | 讓映像解得開 | 有 |
| 6 | 驗收:兩個執行期互換 | — |

## 步驟 1: 整合路徑存在嗎——三層證據

Day 3 建立的方法:**判定「這條路走不通」要查三層**,不是試兩下沒成功就下結論。

### 第一層:宣告面說了什麼

**Azure 自己那條路,兩層宣告面都說有:**

```console
$ az aks nodepool add --help
    --workload-runtime : Allowed values: KataCcIsolation, KataMshvVmIsolation,
                         KataVmIsolation, OCIContainer, WasmWasi.

$ az feature register --namespace microsoft.ContainerService --name WasmNodePoolPreview
{ "properties": { "state": "Registered" } }        ← 立刻就 Registered
```

**第三層才說沒有:**

```console
$ az aks nodepool add … --workload-runtime WasmWasi
ERROR: (InvalidWorkloadRuntimeSettingError) The agent pool wasi1 has a deprecated workload runtime
WasmWasi. WebAssembly System Interface (WASI) node pools (preview) are no longer supported on AKS.
                                     送出到回覆 8 秒,未產生任何資源
```

這顆是[地雷 1](#mine-1)。而**它建議的替代品是 SpinKube,那是 Day 6–7 的主題,不是 WasmEdge**——AKS 當年那條路裝的也是 Spin／slight 的 shim。**這裡的「不通」指 Azure 那條受管路線,不是 WasmEdge 本身。**

**WasmEdge 官方文件有兩條路,一條過期、一條轉介:**

| 文件 | 內容 | 判定 |
|---|---|---|
| `…/kubernetes/kubernetes-containerd-crun` | 步驟寫 `go1.17.1`、`git checkout v1.22.2`、`./hack/local-up-cluster.sh` | **Kubernetes 1.22 世代**,本叢集是 1.35.6 |
| `…/cri-runtime/containerd` | 只說「containerd-shim `runwasi` 專案支援 WasmEdge」 | **轉介到 containerd/runwasi**,這才是活的那條 |

**而 runwasi 自己內部就有版本矛盾:**

```toml
# runwasi 的文件(quickstart.md)——containerd 2.x 的路徑
[plugins."io.containerd.cri.v1.runtime".containerd.runtimes.wasm]
```

```dockerfile
# 同一個 tag 裡,runwasi 自己的 CI(test/k8s/Dockerfile,kind v1.29.1)——1.x 的路徑
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.wasm]
```

**同一份原始碼樹,文件與 CI 用兩個不同的 plugin 路徑。** 這直接決定了步驟 3 要先做離線驗證,見[地雷 2](#mine-2)。

### 第二層:節點上實際載入了什麼

```console
$ containerd --version
containerd github.com/containerd/containerd/v2 2.3.3-2

$ (逐一 command -v)
  crun / youki / wasmedge                        NOT FOUND
  containerd-shim-wasmedge-v1 / -wasmtime-v1     NOT FOUND
  containerd-shim-spin-v2 / -slight-v1           NOT FOUND

$ find /usr /opt /var/lib -maxdepth 4 -type f -name "*wasm*"
/usr/share/mime/application/wasm.xml              ← 命中數 1
$ find /usr/bin /usr/local/bin -maxdepth 1 -type f | wc -l
947                                               ← 證明 find 有在工作,上面那個 1 是真的
```

**AKS 的節點映像上沒有任何 wasm 執行期。** 那第二行 `wc -l` 是 Day 3 學到的紀律:**拿零命中當證據,要同時證明輸入非空。**

### 第三層:綁定粒度

```text
RuntimeClass.handler → 節點 config.toml 的 runtimes.<name> → runtime_type 指到的 shim 二進位檔
```

`/etc/containerd/` 底下只有 `config.toml` 與 `kubenet_template.conf`,**沒有 drop-in 目錄、`config.toml` 裡沒有 `imports`**。要改就是改那一個檔,而且改完只有 `systemctl restart` 有用。

### 裁決

!!! success "可用路徑存在,不需要降級"
    是 containerd/runwasi 的 `containerd-shim-wasmedge-v1` v0.6.1(2026-06-12 發佈)。

    但三件事要一起講:**(1)** AKS 內建的 WasmWasi node pool 已退役,而 az CLI 與 feature flag 兩層還在誤導;**(2)** WasmEdge 官方的 crun 教學是 Kubernetes 1.22 世代的東西,照做會浪費一整天;**(3)** 這條路要自己動節點——**這正是它在三條路線裡的位置**。

## 步驟 2: 把 shim 放上節點——零差異

先在本機驗雜湊,再讓節點自己抓:

```console
$ shasum -a 256 containerd-shim-wasmedge-x86_64-linux-musl.tar.gz
99f7f56c64a0524ea1ec3922cd095b108825a513d0212db75a5dfb08ba196cce
$ curl -sL …/SHA256SUMS
99f7f56c64a0524ea1ec3922cd095b108825a513d0212db75a5dfb08ba196cce  …    ← 相符
```

```console
$ install -m 0755 containerd-shim-wasmedge-v1 /usr/local/bin/
-rwxr-xr-x 1 root root 114524904 /usr/local/bin/containerd-shim-wasmedge-v1

$ /usr/local/bin/containerd-shim-wasmedge-v1 -v
  Runtime: wasmedge    Version: 0.6.1    Revision: 5a8fc8e9ee0df28
```

**壓縮檔 37.3 MiB,解開 109 MiB。** 對照節點原有的 `containerd-shim-runc-v2` 是 8.4 MiB——**整個 WasmEdge 執行期靜態連結在裡面,13 倍大。**

**第一次比對:**

```console
containerd-config.toml           IDENTICAL
containerd-config-dump.toml      IDENTICAL
node-runtimehandlers.json        IDENTICAL
runtimeclasses.txt               IDENTICAL
shim-binaries.txt                DIFFERS
      > -rwxr-xr-x 1 root root 114524904 /usr/local/bin/containerd-shim-wasmedge-v1
```

**放一個 109 MiB 的執行期上去,containerd 一無所知。** 檔案系統多一個檔案,這是全部。

## 步驟 3: 改 containerd 設定——節點從這一步開始不一樣

### 先在副本上離線驗證,不拿節點賭

步驟 1 已經知道 runwasi 的文件與 CI 用兩個不同路徑。`containerd -c <file> config dump` 只解析不啟動,**零風險**:

```console
$ containerd -c /tmp/cfg-1x.toml config dump      # io.containerd.grpc.v1.cri…runtimes.wasmedge
  有效設定行數: 308
  命中 wasmedge 的行數: 0
  與未加任何東西的基準相比: IDENTICAL

$ containerd -c /tmp/cfg-2x.toml config dump      # io.containerd.cri.v1.runtime…runtimes.wasmedge
  有效設定行數: 323
    111:  [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.wasmedge]
    112:    runtime_type = 'io.containerd.wasmedge.v1'
```

**1.x 路徑那一段整個被丟掉,而且沒有任何錯誤或警告。** 這裡的零命中有兩重證明:`grep -c` 是 0,**同時** dump 有 308 行且與基準逐字相同——不是指令失敗。

**改節點之前先在副本上 dump,是這條路線最划算的一個習慣。** 詳見[地雷 2](#mine-2)。

### 套用、重啟,以及重啟的實際代價

```console
$ systemctl reload containerd
Failed to reload containerd.service: Job type reload is not applicable for unit containerd.service.

$ kill -HUP $(pidof containerd)
$ kubectl get node -o jsonpath='{…runtimeHandlers}'
['', 'runc', 'untrusted']                       ← SIGHUP 沒有讓新 runtime 生效

$ systemctl restart containerd
$ kubectl get node -o jsonpath='{…runtimeHandlers}'
['', 'runc', 'untrusted', 'wasmedge']           ← 生效
```

**只有 `systemctl restart` 有用。** 而重啟的代價量出來是零:

```console
$ diff pods-pre-restart.txt pods-post-restart.txt
IDENTICAL——21 個 pod,沒有任何一個被重建或重啟

$ kubectl get node -o jsonpath='{…conditions[?(@.type=="Ready")]}'
True  lastTransition=07:17:03Z       ← 早於 07:24:37 的重啟,節點沒掉出 Ready
```

**containerd 重啟不會殺掉既有容器**(shim 是獨立行程,unit 檔是 `KillMode=process`),節點也沒有掉出 `Ready`。**這是這條路線比想像中溫和的地方。**

### 第二次比對:節點從這裡開始不一樣

```console
containerd-config.toml           DIFFERS
      > [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.wasmedge]
      >   runtime_type = "io.containerd.wasmedge.v1"
containerd-config-dump.toml      DIFFERS(新增 15 行,containerd 把預設值全部展開)
node-runtimehandlers.json        DIFFERS
      >   "name": "wasmedge"
      >   "features": { "recursiveReadOnlyMounts": false, "userNamespaces": false }
shim-binaries.txt                IDENTICAL
runtimeclasses.txt               IDENTICAL
```

**新 handler 的 `recursiveReadOnlyMounts` 與 `userNamespaces` 都是 `false`,而 `runc` 兩個都是 `true`。** 這是 kubelet 從 shim 問出來的能力回報——**Day 8 談「wasm 工作負載拿不到哪些 Pod 功能」時,第一手依據就是它。**

## 步驟 4: RuntimeClass——只動 API server

```bash
cat > runtimeclass-wasmedge.yaml <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: wasmedge
handler: wasmedge      # must match runtimes.<name> in config.toml, NOT runtime_type
EOF
```

**第三次比對:只有 `runtimeclasses.txt` 變了,節點四份全部 IDENTICAL。** 這一步完全在 etcd 裡——正是 [Day 1](sprint3-day1-three-generations.md) 七層對照表裡「叢集物件」那一列。

## 步驟 5: 跑起來之前還缺一塊

第一次部署 runwasi 的示範映像:

```console
$ kubectl -n wasmlab get pod wasmedge-runwasi-demo
NAME                    READY   STATUS                 AGE
wasmedge-runwasi-demo   0/1     CreateContainerError   25s

Events:
  Normal   Pulled   Successfully pulled image … Image size: 2265395 bytes.
  Warning  Failed   Error: failed to create containerd container:
                    parent snapshot sha256:5e274030… does not exist: not found
```

**映像拉成功、shim 有了、handler 有了,卡在 snapshot。** 追下去:

```console
$ ctr -n k8s.io images ls | grep wasi-demo-app
…runwasi/wasi-demo-app:latest   2.2 MiB   wasip1/wasm   io.cri-containerd.image=managed
                                          ^^^^^^^^^^^
$ ctr -n k8s.io snapshots ls | wc -l
265
$ ctr -n k8s.io snapshots ls | grep 5e274030
（無輸出——265 個 snapshot 裡沒有這一個,layer 從來沒有被解開）
```

**映像的平台是 `wasip1/wasm`,節點的 snapshotter 只處理 `linux/amd64`。** containerd 把 blob 收進 content store,但不解壓縮成 snapshot。這是[地雷 3](#mine-3),而它的修法前面有一段很自然會走進去的岔路:

```console
# 岔路:runtime_platforms,名字看起來完全就是為這件事準備的
[plugins."io.containerd.cri.v1.images".runtime_platforms.wasmedge]
  platform = 'wasip1/wasm'          ← 設定確實進了有效設定
                                       套用、重啟、重拉,行為一模一樣
```

真正有效的是同一段設定的另一個欄位:

```toml
[plugins."io.containerd.cri.v1.images"]
  use_local_image_pull = true       # default false → CRI hands pulling to the transfer
                                    # service, which ignores runtime_platforms
```

```console
$ kubectl -n wasmlab logs wasmedge-runwasi-demo
hello-from-wasmedge
exiting
```

**WasmEdge 在 AKS 上跑起來了。**

!!! warning "`use_local_image_pull` 是一個影響整台節點的開關"
    它把 CRI 的拉取路徑從 transfer service 換回本地實作,**對一般容器映像的拉取行為、效能、registry 鏡像設定有沒有影響,沒有測。**

    另外「只留 `use_local_image_pull`、拿掉 `runtime_platforms` 還通不通」也沒拆開驗——**所以最小必要設定是哪幾行,今天答不出來。**

### 累計侵入度

| 項目 | 相對 Day 1 基準的變化 |
|---|---|
| `/etc/containerd/config.toml` | **8 行新增**、0 行移除 |
| `containerd config dump` | **21 行新增**、1 行變更 |
| kubelet 的 `runtimeHandlers` | **多一個 `wasmedge`**(兩項 feature 皆 false) |
| `/usr/local/bin` | **多一個 109 MiB 的 shim** |
| RuntimeClass | 多一個(在 etcd,不在節點) |
| containerd 重啟 | **2 次,pod 零重啟、節點沒掉出 Ready** |

**「第幾步開始節點就不一樣了」的答案是第 3 步。前兩步(含把 109 MiB 的執行期放上去)containerd 完全無感。**

三條路線的位置到這裡確定了:**wasmCloud 零改動 → WasmEdge 8 行設定 + 一個 shim → SpinKube(Day 6)還沒量**。「由低到高」的排序成立。

## 驗收:原訂的「同一支 wasm 程式」不存在 {#gate}

原訂驗收是「同一支 wasm 程式在 WasmEdge 與 wasmCloud 上各跑一次」。**這個共同變因不存在。**

### 三份打包,實際查 registry

```console
--- wasmcloud/components/hello-world:0.1.0
  config.mediaType   = application/vnd.wasm.config.v0+json                            (469 B)
  layer[0].mediaType = application/wasm                                               (224241 B)
--- containerd/runwasi/wasi-demo-app:latest
  config.mediaType   = application/vnd.oci.image.config.v1+json                       (281 B)
  layer[0].mediaType = application/vnd.oci.image.layer.v1.tar                         (2264576 B)
--- containerd/runwasi/wasi-demo-oci:latest
  config.mediaType   = application/vnd.oci.image.config.v1+json                       (320 B)
  layer[0].mediaType = application/vnd.bytecodealliance.wasm.component.layer.v0+wasm  (2262850 B)
```

**而後兩份裡的 wasm 是同一份位元組:**

```console
$ shasum -a 256 wasi-demo-app.wasm demo-oci.wasm
245c955714067729ab7b3fdd26681caa8bbc5d09ba054a846aa7f5bd9a393f45  wasi-demo-app.wasm
245c955714067729ab7b3fdd26681caa8bbc5d09ba054a846aa7f5bd9a393f45  demo-oci.wasm
```

**同一支程式、兩種打包**——這一組把「打包格式」與「wasm 格式」兩個變因分開了。

### 差異在第 5 個位元組上

```console
$ xxd -l 8 -g 1 <各檔>
  hello-world.wasm      00 61 73 6d 0d 00 01 00    version=0x000d layer=0x0001 → component
  wasi-demo-app.wasm    00 61 73 6d 01 00 00 00    version=0x0001 layer=0x0000 → core module
  wasi-demo-oci.wasm    00 61 73 6d 01 00 00 00    同上
```

前四個位元組 `\0asm` 三者相同。**第 5 到第 8 個位元組就分家了。**

### 把 wasmCloud 的元件交給 WasmEdge

不給 `command` 時失敗在打包層(`no command specified`——那份 config JSON 裡沒有 `Entrypoint`,那個位置放的是 `component.exports/imports`)。補上 `command` 之後,失敗前進到格式層:

```console
$ kubectl -n wasmlab logs wasmedge-wasmcloud-cmd
[error] loading failed: illegal opcode, Code: 0x117
[error]     This instruction or syntax requires enabling Component Model proposal
[error]     Bytecode offset: 0x00000004
[error]     At AST node: component
```

**`Bytecode offset: 0x00000004`——WasmEdge 讀到第 5 個位元組就停了,跟上面的 hexdump 完全對得起來。**

那能不能叫它開啟 component model?**不能,而且沒有任何外部開關**,見[地雷 5](#mine-5)。

### 反方向:把 core module 交給 wasmCloud

```console
$ kubectl -n wasmcloud get workload runwasi-core-module-… -o jsonpath='{.status}'
  Sync    False   "workload is not operational: WORKLOAD_STATE_NOT_FOUND"
  Ready   False   "workload is not placed or not synced"

# 三顆 host 的 log 逐行看
  log 共 10 / 14 / 12 行,其中 8 行是 pull_component 的 WARN
  提到 runwasi 且含 'Starting workload' 的行數 = 0
  對照:同一批 log 裡 blobby / hello-world 的 'Starting workload' = 1 / 2 / 2
```

**八行拉取 WARN、零行 `Starting workload`、零行錯誤,然後每 60 秒換一顆 host 無限重試。** 見[地雷 6](#mine-6)。

**Day 3 的[地雷 7](sprint3-day3-wasmcloud-distributed.md#mine-7) 在這裡從噪音變成診斷工具**:那顆的判斷準則是「看下一行有沒有 `Starting workload`」——今天下一行沒有,這就是唯一的訊號。

### 判定

| | 判定 | 支撐證據 |
|---|---|---|
| **原訂驗收**:同一支程式兩邊各跑一次 | **已改寫** | 「同一支 wasm 程式」在這兩個執行期之間**不存在**;已發佈的成品裡沒有一支兩邊都吃得下 |
| **(A)** 同一支 core module、兩種打包各跑一次 | **過** | 兩份映像裡的 wasm **SHA256 相同**,兩顆 pod 分別 `Completed` 與 `Running` 並輸出預期文字 |
| **(B)** 雙向互換,失敗點定位到位元組／原始碼 | **過** | 正向 `illegal opcode` @ `offset 0x00000004`,與 hexdump 對得起來;反向三顆 host `Starting workload` 全 0、log 非空、無限重試無錯誤 |
| **(C)** 兩邊各自跑自己的程式 | **過** | wasmCloud 回 `200` + `Hello from wasmCloud!`;WasmEdge 回 `hello-from-wasmedge / exiting` |

**原本要驗什麼**:一支程式、兩個執行期,藉此把「執行期差異」孤立出來。
**為什麼表達不出來**:這兩個執行期要的不是同一種 wasm,那個共同變因不存在。
**改成驗什麼**:把兩個變因拆開各驗一次。**結論反而比原訂的更明確——擋住互通的是 wasm 格式,不是打包。**

### 三種打包格式對照表

| | wasmCloud `hello-world` | runwasi `wasi-demo-app` | runwasi `wasi-demo-oci` |
|---|---|---|---|
| config mediaType | `…vnd.wasm.config.v0+json` | `…vnd.oci.image.config.v1+json` | `…vnd.oci.image.config.v1+json` |
| layer mediaType | `application/wasm` | `…image.layer.v1.tar` | `…wasm.component.layer.v0+wasm` |
| layer 大小 | 219 KiB | 2.16 MiB | 2.16 MiB |
| `architecture` / `os` | `wasm` / **`wasip2`** | `wasm` / `wasip1` | `wasm` / `wasip1` |
| rootfs | **沒有這個欄位** | `diff_ids` 有 1 個 | `diff_ids` **是空的** |
| 進入點 | **沒有**,改成 `component.exports/imports` 列 WIT 介面 | `Entrypoint` | `Entrypoint` + `containerd.runwasi.layers` label |
| wasm 二進位 | **component** | **core module** | 同左,**SHA256 與左欄相同** |
| 誰讀得懂 | 只有 wasmCloud | WasmEdge | WasmEdge |

**同一份原始碼要不要重編?要。** `wasip2` 的 component 與 `wasip1` 的 core module 是兩個編譯目標(`wasm32-wasip2` vs `wasm32-wasip1`),而且 component 那一側還要 `wasm-tools component` 的加工與一份 WIT 世界定義。**這不是「換個容器打包」,是換編譯目標。**

## 誠實的差距

- **`runtime_platforms` 到底有沒有用,沒有單獨拆開驗。** 量測順序是先加它(無效)、再加 `use_local_image_pull`(有效),兩個一起留著。**所以「最小必要設定是哪幾行」答不出來。**
- **`use_local_image_pull = true` 的副作用沒評估。** 這是一個影響整台節點所有工作負載的開關,對一般容器映像的拉取行為、效能、registry 鏡像設定有沒有影響,今天沒測。
- **crun 那條路完全沒有實測。** 判定它不可行靠的是文件過期加上節點沒有 crun 二進位檔。**沒有真的裝一個帶 WasmEdge 的 crun 試。** 兩條路都通的話,侵入度與效能可能不一樣。
- **啟動時間與記憶體一個數字都沒量。** 只有非控制條件下的映像拉取時間,沒有對照組。**不作任何比較宣稱**,那是 Day 5 的事。
- **109 MiB 的 shim 對節點的實際負擔沒量。** 只知道檔案大小,沒看它跑起來常駐多少記憶體、每個 wasm 容器多開一個 shim 行程的成本。
- **「同一份原始碼編兩次」沒有做。** 打包差異表是拿三個現成的已發佈成品比出來的。「要重編」這個結論是從格式差異推出來的,**推理成立但沒有正面實測。**
- **沒有測 WasmEdge 這條路的多副本／服務化。** 只跑了一次性 pod 與一顆長跑 pod,沒有 Deployment + Service。**兩邊的對照因此不對稱。**
- **節點改動的可逆性今天沒驗。** 推論是「`stop`/`start` 換新 VM,改動自動消失」,依據是連續四次換 VM 加上今天的基準與 Day 1 逐字相同。**但沒有真的 stop 完再 start 確認**,Day 5 開頭會補上這一步。

## 地雷記錄

### 地雷 1:WasmWasi node pool 已退役,但 az CLI 與 feature flag 兩層都還說可以 {#mine-1}

**症狀**:`--help` 把 `WasmWasi` 列在 `Allowed values` 裡;`az feature register` 立刻回 `Registered`。真正下指令才拿到 `InvalidWorkloadRuntimeSettingError`。

**根因**:退役只做在 RP 的驗證層。az CLI 的參數列舉與 `Microsoft.Features` 的旗標定義都沒有一起下架。

**判斷準則**:**AKS 的預覽功能不能靠 `--help` 或 `az feature list` 判斷存不存在,只能實際送一次請求。** 好消息是這種請求失敗得很快(8 秒)而且不產生資源,**成本是零——「試一下」在這裡是合理的偵察手段,不是浪費。**

### 地雷 2:1.x 的 plugin 路徑被靜默丟棄,而那個鍵還活著所以 grep 得到 {#mine-2}

**症狀**:照多數教學(以及 runwasi 自己的 CI)寫 `[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.wasmedge]`,containerd 收下、不報錯、有效設定裡完全沒有這一段。

**根因**:containerd 2.x 把 CRI 的 runtime 設定搬走了。**但 `io.containerd.grpc.v1.cri` 沒有消失**——它仍是有效設定裡的一個 plugin,只剩 streaming server 的五個欄位:

```console
  [plugins.'io.containerd.grpc.v1.cri']
    disable_tcp_service = true
    stream_server_address = '127.0.0.1'
    …                                    ← runtimes 已經搬走了
```

**所以「grep 得到這個鍵」不能當成「這篇教學適用」的證據。** `containerd config migrate` 也救不了:輸出 308 行、`wasmedge` 零命中,`version` 從 2 升到 4,那一段就是消失。

**這顆是 [Day 1 地雷 1](sprint3-day1-three-generations.md#mine-1) 的續集**,但更難察覺——那一顆是「路徑錯了」,這一顆是「路徑錯了而且錯的那個鍵還在」。

**修法／追查順序**:**改節點之前先在副本上 `containerd -c <file> config dump`**,零風險、不用重啟。順帶:`systemctl reload` 回 `Job type reload is not applicable`,`kill -HUP` 送得出去但新 runtime 不會生效,**只有 `systemctl restart` 有用**。

### 地雷 3:shim 裝好、handler 也有了,pod 還是起不來 {#mine-3}

**症狀**:`CreateContainerError`,訊息是 `parent snapshot sha256:… does not exist: not found`。而映像明明拉成功。

**根因**:映像的平台是 `wasip1/wasm`,節點的 snapshotter 是 `overlayfs`／`linux/amd64`。containerd 把 blob 收進 content store 但**不解壓縮成 snapshot**,shim 要讀 rootfs 裡的 `.wasm` 就讀不到。**錯誤訊息只講 snapshot 不存在,一個字都沒提平台不合。**

**修法**:`use_local_image_pull = true`。預設 `false` 時 CRI 把拉取交給 transfer service,那條路徑不看 CRI 的 `runtime_platforms`。

**容易走進的岔路**:先加了 `runtime_platforms.wasmedge`——**名字看起來完全就是為這件事準備的,設定也確實進了有效設定,但行為一模一樣。**

**追查順序**:`ctr -n k8s.io images ls` 看 `PLATFORMS` 欄 → `ctr snapshots ls | grep <digest>` 確認沒解開 → `containerd config dump` 找 `use_local_image_pull`。

### 地雷 4:同一個映像會有兩種失敗,而只有第二種說得出原因 {#mine-4}

**症狀 A**(不給 command):`failed to generate spec: no command specified`。
**症狀 B**(補上 command):容器起來了,然後 `Error`,log 是 `illegal opcode … Bytecode offset: 0x00000004`。

**根因**:A 是打包層——wasmCloud 的 config JSON 裡沒有 `Entrypoint` 也沒有 `Cmd`,CRI 生不出 OCI spec。B 才是真的:**wasm 二進位檔是 component 不是 core module**。

**為什麼要記**:**症狀 A 會讓人以為「只是參數沒給對」而放棄追下去。** 補一個 `command` 不是修好,而是**讓失敗前進到說得出原因的那一層**。

**判斷準則**:`no command specified` 是打包層的問題,`Bytecode offset: 0x00000004` 是格式層的問題。

### 地雷 5:runwasi 的 wasmedge shim 沒開 component model,而同 repo 的 wasmtime shim 有 {#mine-5}

**症狀**:想讓 WasmEdge 吃 component,找不到任何 pod annotation、環境變數或 containerd `options` 可以開。

**根因**三層:`containerd-shim-wasmedge` 用的是 `wasmedge_sdk::{Module, Store, Vm}` 與 `Module::from_bytes`——**core module 的 API**;`Config` 在 `Default` 裡寫死,沒有任何 proposal 開關;而

```console
$ grep -rniE "component_model|ComponentModel|enable_component" crates/
containerd-shim-wasmtime/src/instance.rs:69:   config.wasm_component_model(true);
containerd-shim-wasmtime/src/instance.rs:141:  config.wasm_component_model(true);
containerd-shim-wasmtime/src/tests.rs:333:     config.wasm_component_model(true);
命中 3 行;crates 下共 92 個 .rs 檔(證明輸入非空)
```

**同一個 repo,wasmtime 的 shim 明確開了,wasmedge 的沒有。**

**為什麼要記**:**「WasmEdge 支援 component model」跟「runwasi 的 wasmedge shim 支援 component model」是兩回事。** 前者是執行期的能力,後者是 shim 有沒有把它打開。**要 component + Kubernetes,今天可用的組合是 wasmtime 的 shim。**

這跟 [Day 3 地雷 1](sprint3-day3-wasmcloud-distributed.md#mine-1) 是同一種形狀:**功能在原始碼裡,但沒有編進你實際跑的那個二進位檔。**

### 地雷 6:wasmCloud 收到不是 component 的映像時完全靜默,只會無限重試 {#mine-6}

**症狀**:`READY: Unknown`,`Sync: False`,訊息只有 `WORKLOAD_STATE_NOT_FOUND`。三顆 host 的 log 共 10／14／12 行,**只有拉取 WARN、`Starting workload` 零行、錯誤零行**。每 60 秒換一顆 host 重來。

**根因**:host 拉得到映像、認不得裡面的東西,而這條路徑沒有任何錯誤回報。對照組同一批 log 裡 `blobby`／`hello-world` 的 `Starting workload` 是 1／2／2,**證明那行 log 本來就會出現,不是被過濾掉了。**

**判斷準則**:**[Day 3 地雷 7](sprint3-day3-wasmcloud-distributed.md#mine-7) 的規則在這裡從噪音處理升級成唯一的診斷工具**——沒有下一行 `Starting workload`,就是這個映像 wasmCloud 吃不下。

**副作用**:這個重試迴圈**每 60 秒重拉一次映像**,會持續產生流量與 log。實驗結束時記得把 `WorkloadDeployment` 刪掉。

## 帶得走的東西

- **「第幾步開始節點不一樣」是一個值得每次都問的問題。** 把 109 MiB 的執行期放上節點,containerd 完全無感;真正的分水嶺是那 8 行設定加一次重啟。**分不清這兩件事,就會把「複製檔案」和「改變節點行為」當成同一件事來評估風險。**
- **containerd 重啟比想像中溫和,但要自己量。** 21 個 pod 的 `RESTARTS` 逐行 diff 相同、節點沒掉出 `Ready`——因為 shim 是獨立行程、unit 檔是 `KillMode=process`。**這是可以量的,不要用猜的。**
- **改節點之前先在副本上 `config dump`。** 零風險、不用重啟,而且會當場告訴你那段設定會不會被靜默丟掉。這條路線上這個習慣直接省下一次「改完重啟才發現沒生效」。
- **失敗訊息會分層,而第一層通常在說謊。** `no command specified` 聽起來像參數沒給對,補上之後才看得到真正的 `Bytecode offset: 0x00000004`。**把失敗往前推一層,比在第一層猜原因有效。**
- **「這個專案支援 X」跟「你正在跑的那個二進位檔支援 X」是兩回事。** 今天的 wasmedge shim 與 Day 3 的 wasmCloud provider 是同一種形狀:功能在原始碼裡,沒編進成品。**判斷一個能力能不能用,要看你手上那個檔案,不是看 repo。**
- **wasm 不是一種格式,是兩種。** core module 與 component 差在第 5 個位元組,而生態系目前是照這條線分裂的。**「把它編成 wasm」不是一個明確的指令**,要先問編給誰。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[containerd/runwasi](https://github.com/containerd/runwasi)** —— 今天用的 shim。值得對照著看的是 `docs/src/getting-started/quickstart.md`(containerd 2.x 路徑)與 `test/k8s/Dockerfile`(1.x 路徑)——**同一個 tag 裡兩種寫法**,地雷 2 的成因。
- **[containerd 的 CRI 設定文件](https://github.com/containerd/containerd/blob/main/docs/cri/config.md)** —— `use_local_image_pull` 與 `runtime_platforms` 都在這裡。地雷 3 走的那段岔路,讀這一份可以少走。
- **[WasmEdge 的 containerd 整合頁](https://wasmedge.org/docs/develop/deploy/cri-runtime/containerd)** —— 值得看的是它**沒有**寫什麼:全文只說「runwasi 支援 WasmEdge」就轉介出去,而站上另一份 crun 教學還停在 Kubernetes 1.22 世代。

## 這條路線收尾:WasmEdge + runwasi 適合誰

今天的實測配上 [Day 0 那五條](sprint3-day0-wasm-concepts.md#health-method)的查證(2026-08-11):

**體質要拆成兩半看**。WasmEdge 本體健康——近 13 週 291 個 commit、作者分布最均勻(最高單一作者 35/100、12 位)。**但上 Kubernetes 的那一段是 runwasi**,而 runwasi 是另一回事:default branch 靜止 8 週、最近 100 筆 commit 一半是 dependabot。它是這條路線唯一的自動化入口,而它幾乎只剩機器人在動。標準層也落後:release notes 沒有 WASI 0.3 字樣,上一代的完成度還掛著 open 追蹤 issue。

**適合**:想搞懂 RuntimeClass → handler → shim 這條原生執行鏈——每一步手做、看得見,教學價值三條最高。既有程式碼是 core module 且短期不搬 component 的話,相容性也是優勢。

**不適合**:上線。佈建與拆除全手動、沒有自動化、要自己維護腳本,而唯一能自動化它的元件正在停滯。

## 下一步

三條路線的前兩條走完了,侵入度的位置也量出來了:**wasmCloud 零改動,WasmEdge 8 行設定加一個 109 MiB 的 shim。** Day 6 的 SpinKube 是最後一條,也是唯一一條會派 operator 去替你改節點的。

但在那之前,**[Day 5](sprint3-day5-cost-measurement.md) 要把成本量出來**。Day 2、Day 3、Day 4 各自留了量測陷阱:記憶體不會歸還、元件不同不能比、啟動時間各只跑一次、109 MiB 的 shim 常駐成本未知。那一天要先做方法學試跑,確認雜訊小於效果,再開始量。

另外,節點上這些改動不必急著拆:「VM 汰換會抹掉節點層改動」這個性質,Day 5 開頭會直接驗證——**Day 8 的可逆性驗收要靠它**。

---

!!! quote ""
    WasmEdge 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
