# Day 5: 換一個工具看同一批事件——Tetragon 與 TracingPolicy

![Tetragon 官方標誌](../assets/logos/tetragon-icon-color.svg){ align=right width="95" }

> Falco 用使用者空間的規則引擎把 syscall 事件變成有名字的告警。Tetragon 走另一條路:過濾在核心裡做完,輸出的是紀錄而不是判斷。今天把 Tetragon 裝在同一座叢集上,寫第一條 TracingPolicy,然後讓兩套工具同時看同一批動作——最值得記住的結論來自它們**答案一樣錯**的那一格。

!!! abstract "你在課程的哪裡"
    - **Day 3–4**:Falco 裝好了、讀得懂規則語言、補過兩個出廠規則的洞,也知道調校誤報要交出多少偵測力。
    - **今天**:裝 Tetragon、拆解一條 TracingPolicy、量出「核心裡過濾」跟「使用者空間比對」差在哪,然後四個偏離動作兩套工具同時看。驗收是 TracingPolicy 產出的事件能對回自己的操作。
    - **Day 6**:今天所有跟攔截有關的開關都刻意關著。下一章才把它從「看」切到「殺」。

## 兩套工具的形狀不一樣,不是好壞

Falco 交付的是**判斷**:一筆告警帶規則名、嚴重度、MITRE 分類,人看到就知道發生了什麼。代價是沒寫規則的事一律看不見([Day 3 地雷 4](sprint2-day3-falco-basics.md#mine-4))。

Tetragon 交付的是**紀錄**:行程執行了什麼、參數是什麼、父行程是誰、屬於哪顆 pod。它不告訴你這件事好不好,那是你的事。

這個差別會一路貫穿今天的每一個數字,所以先擺在前面。今天走六步:

| 步驟 | 做什麼 | 為什麼排在這 |
|---|---|---|
| 1 | 安裝 | 它裝出來的東西跟 Falco 不一樣,而且有一樣是 Helm 沒告訴你的 |
| 2 | 量出廠狀態的事件率 | **同一顆節點同一段時間,量到 0 與每分鐘 1915 筆兩個答案**,只做一半會得到錯的結論 |
| 3 | `tetra` CLI 與 pod 身分 | 事件從哪來、身分怎麼查出來,決定了步驟 5 的結果 |
| 4 | 第一條 TracingPolicy | 驗收,以及核心層過濾的實證 |
| 5 | 四個偏離,兩套工具同時看 | **今天的重點** |
| 6 | 成本 | 資源、事件量、失效形狀 |

## 步驟 1: 安裝,以及 Helm 沒有告訴你的那兩個 CRD

Tetragon 跟 Cilium 出自同一個組織,但**它不需要 Cilium 當 CNI**。這座叢集跑的是 Azure CNI overlay,Tetragon 裝下去完全沒有抱怨,也沒有任何一段設定提到 CNI——兩者在 datapath 上互不相干。

版本現查,不假設:

```console
$ helm repo add cilium https://helm.cilium.io/ && helm repo update cilium
$ helm search repo cilium/tetragon --versions | head -3
cilium/tetragon   1.7.0   1.7.0   Helm chart for Tetragon
cilium/tetragon   1.6.1   1.6.1
cilium/tetragon   1.6.0   1.6.0
```

chart 版本與 app 版本同號。這跟 Falco 的 `falco-9.1.0` 對 `0.44.1` 是兩條獨立版本線不一樣,抄錯會誤判升級範圍。

安裝時 values 幾乎是空的,而這是刻意的:Day 3 對 Falco 釘死 `driver.kind` 是因為 `auto` 會藏起實際載入的東西;Tetragon 沒有這個選擇要做,而「出廠狀態下它會吐什麼」這個問題的誠實答案,就是不要先去動它的事件管線。

```console
$ helm install tetragon cilium/tetragon --version 1.7.0 -n tetragon --create-namespace --wait
STATUS: deployed                    ← 21 秒
```

### 它建了什麼

```console
$ helm template cilium/tetragon --version 1.7.0 | grep '^kind:' | sort | uniq -c
   2 ClusterRole        2 ClusterRoleBinding    2 ConfigMap
   1 DaemonSet          1 Deployment            1 Role
   1 RoleBinding        2 Service               2 ServiceAccount
```

跟 Falco 的形狀差在多一個 Deployment:

| 元件 | 形狀 | 做什麼 |
|---|---|---|
| `tetragon` | **DaemonSet**,每節點一顆,2 個容器 | `tetragon`(privileged,掛 BPF 程式、寫 JSON 到節點上的檔案)加 `export-stdout`(把那個檔案 `tail -q -F` 到 stdout) |
| `tetragon-operator` | **Deployment**,單副本 | 只做一件事:建 CRD。之後基本閒置(實測 1m / 19Mi) |

那個 `export-stdout` sidecar 看起來像實作細節,其實是步驟 2 的主角。

### 啟動日誌裡的三行

```console
$ kubectl -n tetragon logs <tetragon-pod> -c tetragon | head -60
```

```text
msg="BTF discovery: default kernel btf file found" btf-file=/sys/kernel/btf/vmlinux
msg="BPF detected features: override_return: true, buildid: true, kprobe_multi: false,
     uprobe_multi: true, fmodret: true, fmodret_syscall: true, signal: true, large: true,
     link_pin: true, lsm: true, …"
msg="Loading sensor" name=__base__
msg="Available sensors" sensors=__base__
```

三件事:

1. **BTF 直接用節點自帶的 `/sys/kernel/btf/vmlinux`**——[Day 0](sprint2-day0-ebpf-concepts.md) 驗過的那個檔案,今天拿到第二個使用者(第一個是 Day 1 的 bpftrace)。
2. **`lsm: true`。** Day 0 從開機參數推論 BPF LSM 是開的,今天 Tetragon **自己偵測後回報同一個答案**。連同 `override_return`、`signal`、`fmodret`——Day 6 攔截需要的四項核心能力這座叢集全有。今天一項都不用。
3. **`kprobe_multi: false`。** 這顆核心沒有這個功能,所以一個符號要掛一支 kprobe,不能一支程式掛一批。今天只用一個符號所以無感;寫到幾十個掛勾的策略時,這是載入時間與核心記憶體的差別。

零策略時只有 `__base__` 一個 sensor。**這就是「出廠狀態」的定義**,下一步要量的就是它。

CRD 的部分有一個坑,見[地雷 1](#mine-1)。

## 步驟 2: 出廠狀態到底吵不吵——一邊是 0,一邊是每分鐘 1915 筆 {#step-2}

### 答案一:匯出的事件流

132 秒的閒置窗口,沒有任何人為動作:

```text
Tetragon 匯出事件:0 筆 / 132 秒
同窗口 Falco 告警:0 筆
```

**兩邊都是零。** 如果只做到這裡,結論會是「Tetragon 出廠跟 Falco 一樣安靜」——而這是錯的。

先看一件小事:安裝當下就有 23 筆事件,而且時間戳往前跨了好幾分鐘:

```text
03:12:36  process_exec  kai-scheduler/binder-…/binder    /workspace/app
03:16:26  process_exec  falco/falco-…/falco              /usr/bin/falco
03:17:26  process_exec  tetragon/tetragon-…/…            /bin/sh …
```

第一筆比 Tetragon 自己啟動早了五分鐘。啟動日誌解釋了原因:

```text
msg="Read ProcFS /procRoot appended 182/244 entries"
```

Tetragon 開機時掃 `/proc` 把既有行程重建進 process cache,**而且把它們當成事件送出去**。Falco 也重建行程表,但不會拿它當告警。這個差別對事故調查有用(Tetragon 起來之後你立刻有一份「現在誰在跑」的快照),對告警管線是雜訊——每次 DaemonSet 滾動就是每節點上百筆。

### 答案二:agent 裡面的事件流

`tetra` CLI 直接接 agent 的 unix socket,**繞過匯出時的過濾**。同一顆節點、60 秒:

```console
$ kubectl -n tetragon exec <tetragon-pod> -c tetragon -- timeout 60 tetra getevents
```

| 量到的東西 | 數字 |
|---|---|
| 事件數 | **1915 筆 / 60 秒 = 1915 筆/分鐘/節點** |
| 平均大小 | 3556 B/筆 |
| 換算 | 6.8 MB/分鐘/節點 ≈ **9.8 GB/天/節點** |
| `process_exit` | 1175 |
| `process_exec` | 740 |
| 命名空間為空字串(主機行程) | **1898(99.1%)** |
| 任何應用命名空間 | **0** |

前幾名的執行檔全部是節點自己的代理程式:

```text
376  /usr/bin/jq
210  /etc/node-problem-detector.d/plugin/check_spot_eviction.sh
204  /etc/node-problem-detector.d/plugin/check_scheduledevent.sh
202  /usr/bin/curl
180  /etc/node-problem-detector.d/plugin/check_scheduledevent_consolidated.sh
 86  /usr/bin/cat        62 /usr/bin/dirname     60 /usr/bin/uuidgen
```

node-problem-detector 每隔幾秒跑一輪 shell script,每個 script 再 fork `curl`、`jq`、`mktemp`、`uuidgen`。**這是受管節點的正常狀態,跟工作負載一點關係都沒有**,而 Tetragon 全部看見、全部編碼、全部送進匯出層,然後在最後一關丟掉。丟掉它的是[地雷 2](#mine-2)。

事件本身也比想像中大,見[地雷 3](#mine-3)。

## 步驟 3: `tetra` CLI、事件從哪來、pod 身分怎麼查出來

CLI 不必另外下載,agent 映像裡就有,版本自動對齊:

```console
$ kubectl -n tetragon exec <tetragon-pod> -c tetragon -- tetra version
CLI version: v1.7.0
```

它接的是 agent 的 gRPC socket。**這條路跟 `kubectl logs -c export-stdout` 是兩個不同的東西**,差別就是步驟 2 那 1915 倍:

```text
BPF programs (ring buffer)
        │
        ▼
  tetragon agent  ──── gRPC unix socket ────►  tetra getevents   ← 沒有過濾
        │
        ├── export-allowlist / export-denylist / field-filters
        ▼
  節點上的 JSON 檔
        │
        ▼
  export-stdout (tail -q -F)  ──►  kubectl logs   ← 過濾之後
```

順帶一個會浪費時間的小坑:`kubectl logs <tetragon-pod>` 不加 `-c` 拿到的是第一個容器,也就是 agent 自己的文字日誌,**裡面一筆事件都沒有**。要看事件必須 `-c export-stdout`。

抽事件的腳本後面幾章都會用:

```bash
cat > tetra-events.sh <<'EOF'
#!/usr/bin/env bash
# Pull Tetragon events out of the export-stdout sidecar (NOT the agent container).
#   --types    histogram by event type
#   --compact  one line per event: time, pod, binary, args
#   --since    passed to kubectl logs (default 10m)
set -euo pipefail
SINCE=10m; MODE=raw
while [ $# -gt 0 ]; do
  case "$1" in
    --types) MODE=types ;;
    --compact) MODE=compact ;;
    --since) SINCE="$2"; shift ;;
  esac
  shift
done
EV=$(kubectl -n tetragon logs -l app.kubernetes.io/name=tetragon -c export-stdout \
       --since="$SINCE" --prefix=false 2>/dev/null | grep '^{' || true)
case "$MODE" in
  types)   echo "$EV" | jq -r 'keys[] | select(startswith("process_"))' | sort | uniq -c ;;
  compact) echo "$EV" | jq -r '(.process_exec//.process_exit//.process_kprobe) as $p
             | "\(.time)  \($p.process.pod.namespace // "-")/\($p.process.pod.name // "-")  \($p.process.binary) \($p.process.arguments // "")"' ;;
  *)       echo "$EV" ;;
esac
EOF
chmod +x tetra-events.sh
```

### 一次普通的 `kubectl exec` 長什麼樣

```console
$ kubectl -n ebpf-lab exec baseline-nginx -- cat /etc/shadow
```

事件流裡是兩段:

```text
03:31:22.949  process_exec  ebpf-lab/baseline-nginx/nginx  /proc/self/fd/6 'init'   parent=/usr/bin/runc
03:31:22.951  process_exit  ebpf-lab/baseline-nginx/nginx  /proc/self/fd/6 'init'   parent=/usr/bin/runc
03:31:22.962  process_exec  ebpf-lab/baseline-nginx/nginx  /usr/bin/cat '/etc/shadow'  parent=/usr/bin/containerd-shim-runc-v2
03:31:22.964  process_exit  ebpf-lab/baseline-nginx/nginx  /usr/bin/cat '/etc/shadow'  parent=/usr/bin/containerd-shim-runc-v2
```

前兩筆是 runc 的 init helper,後兩筆才是真正的指令。**`parent=containerd-shim-runc-v2` 就是 Falco 那條 `container_entrypoint` macro 在看的同一件事**([Day 3 地雷 6](sprint2-day3-falco-basics.md#mine-6))——同一個核心事實,兩個工具各自的表達方式:Falco 把它寫成規則條件,Tetragon 直接把 `parent` 印出來讓你自己判斷。

### pod 身分是怎麼來的

事件裡的 pod 區塊:

```json
"pod": {
  "namespace": "ebpf-lab", "name": "baseline-nginx",
  "container": { "id": "containerd://ce8a862c00d9…", "name": "nginx",
    "image": { "name": "mcr.microsoft.com/mirror/docker/library/nginx:1.25" },
    "pid": 23 },
  "pod_labels": {"app": "baseline-nginx"},
  "workload": "baseline-nginx", "workload_kind": "Pod"
}
```

比 Falco 多的東西很實在:**容器內的 pid**(不是主機 pid)、pod labels、workload 與 workload kind。Falco 的 `container.*` 給不到後兩者。

但**來源機制**才是重點:

| | Falco | Tetragon |
|---|---|---|
| 核心事件帶了什麼 | pid、cgroup id | pid、cgroup id(BPF 在 `execve` 當下寫進 map) |
| 誰把它變成 pod 名字 | container plugin,用 cgroup 路徑挖出 container id,再打**容器執行期的 socket** 反查 | agent 用 **Kubernetes API informer** 維護一張 cgroup id 對 pod 的表,事件出核心後查表 |
| 查表在哪一層 | 使用者空間 | 使用者空間 |
| **身分的依據** | **cgroup** | **cgroup** |

兩條路的實作差很多,但**最後一欄一樣**。這一欄就是步驟 5 的答案,而它早在 [Day 2 的地雷 2](sprint2-day2-bpftrace-kubernetes.md#mine-2) 就寫好了。

## 步驟 4: 第一條 TracingPolicy {#step-4}

```bash
cat > tracingpolicy-file-open.yaml <<'EOF'
apiVersion: cilium.io/v1alpha1
kind: TracingPolicyNamespaced          # scoped to one namespace, not the cluster
metadata:
  name: lab-etc-read
  namespace: ebpf-lab
spec:
  kprobes:
    - call: "security_file_permission"
      syscall: false                   # kernel-function ABI, not the syscall ABI
      return: true
      args:
        - index: 0
          type: "file"                 # struct file * — CO-RE resolves f_path to a string
        - index: 1
          type: "int"                  # mask: MAY_EXEC=1, MAY_WRITE=2, MAY_READ=4
      returnArg:
        index: 0
        type: "int"
      selectors:
        - matchArgs:                   # both entries in one selector are AND-ed
            - index: 0
              operator: "Prefix"
              values: ["/etc/"]
            - index: 1
              operator: "Equal"
              values: ["4"]
EOF
kubectl apply -f tracingpolicy-file-open.yaml
```

逐項說明為什麼長這樣:

**`kind: TracingPolicyNamespaced`** 是「只看指定命名空間」這個需求的正解,而它跟 Falco 的 `k8s.ns.name = "ebpf-lab"` 是完全不同的東西。Tetragon 有一張 BPF map,內容是「策略 id 對一組 cgroup id」,由 agent 從 Kubernetes 的 pod informer 填。**kprobe 程式進去的第一件事就是查這張表**,不在名單上的 cgroup 直接返回,事件從來沒有離開核心。Falco 的命名空間條件是在事件已經複製到使用者空間、行程資訊已經反查完之後才評估的。

**`call: "security_file_permission"`** 是核心符號。挑它而不是挑 `sys_openat`:拿到一個檔案的內容有十幾條路(`open`、`openat`、`openat2`、對已開啟 fd 的 `mmap`⋯⋯),而 `security_file_permission` 是核心自己把存取檢查匯流過去的那個點,**一個掛勾蓋掉全部**。代價是它觸發得非常頻繁,這正是選擇器存在的理由。

它是一個 LSM 掛勾函式,但這裡掛的是 **kprobe**,只看不管。Day 6 的攔截走的是另一條路。

**`syscall: false`** 告訴 Tetragon 用核心函式的呼叫慣例讀參數。寫錯不會載入失敗,會**讀到看起來很合理的垃圾**。

**`type: "file"`** 是 CO-RE 的變現點:Tetragon 拿 `struct file *`,靠 BTF 走 `f_path` 解出路徑字串。策略檔裡沒有任何一個位元組跟這顆核心的版本有關。

**選擇器的組合規則**是今天最該記住的語法:**同一個選擇器裡的多個 `matchArgs` 是 AND,不同選擇器之間是 OR**。所以上面那段讀作「路徑以 `/etc/` 開頭**而且** mask 等於 4」。

套用之後**不要相信 `kubectl apply` 的輸出**,理由見[地雷 4](#mine-4)。唯一的驗收條件是:

```console
$ kubectl -n tetragon exec <tetragon-pod> -c tetragon -- tetra tracingpolicy list
ID  NAME          STATE     FILTERID  NAMESPACE  SENSORS         KERNELMEMORY  MODE
3   lab-etc-read  enabled   3         ebpf-lab   generic_kprobe  462.15 kB     monitor_only
```

`MODE: monitor_only` 是 agent 自己判定的——**今天的「只觀察」不是靠自律,是策略裡沒有任何動作,agent 據此標記的狀態**。`KERNELMEMORY 462.15 kB` 是一條策略在核心裡的固定成本,這個數字 Falco 沒有對應物。

### 驗收

```console
$ kubectl -n ebpf-lab exec baseline-nginx -- cat /etc/shadow > /dev/null
```

```json
{
  "process_kprobe": {
    "process": {
      "pid": 25115, "uid": 0, "binary": "/usr/bin/cat", "arguments": "/etc/shadow",
      "pod": { "namespace": "ebpf-lab", "name": "baseline-nginx",
               "container": { "name": "nginx", "pid": 23 } }
    },
    "parent": { "pid": 21569, "binary": "/usr/bin/containerd-shim-runc-v2" },
    "function_name": "security_file_permission",
    "args": [ {"file_arg": {"path": "/etc/shadow", "permission": "-rw-r-----"}},
              {"int_arg": 4} ],
    "return": {"int_arg": 0},
    "action": "KPROBE_ACTION_POST",
    "policy_name": "lab-etc-read"
  },
  "node_name": "<node-a>",
  "time": "2026-08-07T03:26:20.860883032Z"
}
```

逐項對得上:`policy_name` 是剛寫的那條、`function_name` 是挑的那個掛勾、`file_arg.path` 是讀的那個檔、`int_arg: 4` 是選擇器要求的 mask、`process.binary` 加 `arguments` 是打的那行指令、`pod` 是 exec 進去的那顆,時間差 **1.0 毫秒**。

`return.int_arg: 0` 是核心允許了這次讀取——**Day 6 要改的就是這個值**。

### 核心層過濾:3000 次讀取的實驗

策略只看 `/etc/` 開頭。在同一顆 pod 裡讀 3000 次**不符合**的檔案:

```console
$ kubectl -n ebpf-lab exec baseline-nginx -- bash -c \
      'echo hello > /tmp/decoy; for i in $(seq 1 3000); do cat /tmp/decoy > /dev/null; done'

策略 kprobe 事件增量:0
```

**一筆都沒有。** BPF 程式對每一次呼叫做了前綴比對,不符合就返回,事件連 ring buffer 都沒進去。Falco 的等價做法是把事件撈到使用者空間、填完行程與容器欄位、再拿 27 條規則的條件去比——這就是 [Day 3 地雷 7](sprint2-day3-falco-basics.md#mine-7) 那句「沒有告警不等於沒有成本」。

同一個 burst 唯一冒出來的 6 筆來自 `bash` 自己:`/etc/nsswitch.conf` 四次、`/etc/passwd` 兩次,是 bash 啟動時的 NSS 查詢。這 6 筆本身就是提醒:**`/etc/` 這個前綴對任何跑 shell 的容器都不是低頻**。真要用在生產,前綴得縮到 `/etc/shadow` 這種等級,或者加 `matchBinaries` 把 shell 自己的查詢排掉。

**但這一段最重要的數字在別的地方**,見[地雷 5](#mine-5)。

## 步驟 5: 四個偏離,兩套工具同時看

Falco 維持 [Day 4](sprint2-day4-falco-custom-rules.md) 結束時的狀態(25 條出廠加 2 條自訂),Tetragon 是 `__base__` 加一條 `lab-etc-read`。一次動作,兩份輸出。

| 偏離 | Falco 說了什麼 | 怪到哪顆 pod | Tetragon 說了什麼 | 怪到哪顆 pod |
|---|---|---|---|---|
| **A** `bash -c "…" & wait`(有 pty) | 2 筆:出廠 `Terminal shell in container` 加自訂 `Shell spawned by non-runtime parent` | `baseline-nginx` ✓ | **18 筆**:3 exec、3 exit、12 kprobe。無規則名、無嚴重度 | `baseline-nginx` ✓ |
| **B** 同上但**無終端機** | **1 筆**:只有自訂規則。出廠規則靜音 | `baseline-nginx` ✓ | **18 筆,與 A 完全相同的組成** | `baseline-nginx` ✓ |
| **C** `nsenter` 進 `baseline-nginx` 讀 `/etc/shadow` | 2 筆 | **呼叫端** ✗ | **34 筆**,含 `nsenter` 指令與完整參數 | **呼叫端** ✗ |
| **D** 直接在 `baseline-nginx` 裡讀 `/etc/shadow` | 1 筆 | `baseline-nginx` ✓ | **8 筆**:2 exec、2 exit、**4 kprobe(策略產生的)** | `baseline-nginx` ✓ |

C 那一列是今天的頭條([地雷 6](#mine-6)),B 那一列是分工的核心([地雷 7](#mine-7))。

### 訊噪比

四個偏離的總帳:

| | Falco | Tetragon |
|---|---|---|
| 事件或告警總數 | **6** | **78** |
| 每一筆都對得回一個動作嗎 | 是 | 是 |
| 帶規則名與嚴重度嗎 | **是**(含 MITRE T1059 / T1555) | **否** |
| 需要人再判斷嗎 | 否 | **是** |

**13 倍。** 而這是在「只有一條窄策略、四個動作、10 秒鐘」的條件下量的。步驟 4 那個 3000 次 burst 才是規模長什麼樣。

## 步驟 6: 成本

### 資源

閒置、一條策略載入中:

| 元件 | CPU | 記憶體 | QoS |
|---|---|---|---|
| **Tetragon** agent(每節點) | **5–11m** | **83–120Mi** | **BestEffort** |
| Tetragon `export-stdout`(每節點) | 1m | <1Mi | — |
| Tetragon operator(單顆) | 1m | 19Mi | Burstable |
| **Falco**(每節點) | **13–27m** | **84–90Mi** | Burstable |

**Tetragon 的 CPU 明顯比 Falco 低**,而這完全合理:Falco 對每一個 `execve` 都要在使用者空間跑一遍 27 條規則的布林運算,Tetragon 零策略時只是把事件搬出來。記憶體則是 Tetragon 略高,因為 process cache 與 `execve_map` 是預先配置的。

再加上策略的核心記憶體:462.15 kB。這一項 Falco 完全沒有對應物。

有一件事值得單獨記:上表刻意排除了被 `tetra getevents` 抽過兩輪的那顆 agent。它量到 11m / **163Mi**,而另外兩顆是 88Mi 與 120Mi。**`tetra getevents` 不是免費的診斷指令**,而且記憶體抽完不會馬上還回來——在一顆吃緊的節點上對著 BestEffort 的 agent 開串流,等於自己把它推向 OOM。

Tetragon 的部署預設見[地雷 8](#mine-8)。

### 閒置事件率

| 狀態 | 窗口 | Tetragon 匯出事件 | Falco 告警 |
|---|---|---|---|
| 零策略 | 132 秒 | **0** | **0** |
| 一條策略載入中 | 195 秒 | **4** | **0** |

那 4 筆是自己造成的:窗口頭尾各跑一次 `kubectl exec` 進 tetragon 命名空間查策略狀態,而該命名空間不在預設的排除清單上。扣掉自己的觀測動作,兩種狀態都是零。

**而這個零是排除清單的零,不是「沒有事情發生」的零**([地雷 2](#mine-2))。真實的核心層事件率是 1915 筆/分鐘/節點。

## 誠實的差距

- **`enable-process-ns` 沒有測。** [地雷 6](#mine-6) 提到改用命名空間當身分依據也許能修正 `nsenter` 的歸屬,Tetragon 有這個選項且預設關閉,沒有開來驗證,不知道夠不夠。
- **一個計數器沒查清楚。** `tetra tracingpolicy list -o json` 有 `stats.action_counters.post`,實測一次 1 位元組的讀取讓它增加 5、匯出事件只增加 1。合理的猜測是進入與返回各自計數,但**沒有驗證就不寫成結論**。可以確定的只有:**`post` 不是事件數,拿它估事件量會高估好幾倍。**
- **只驗了一個掛勾。** 今天整章的核心層過濾論述建立在一條 kprobe 策略上,tracepoint、uprobe、LSM 掛勾的行為沒有測。
- **事件量沒有長時間觀察。** 1915 筆/分鐘是 60 秒窗口的外推,不是跑一天的結果。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| Tetragon 不需要 Cilium | 在 Azure CNI overlay 的叢集上安裝完成且 agent 正常掛載 BPF | DaemonSet 3/3,無任何 CNI 相關錯誤 |
| 策略真的載入 | `tetra tracingpolicy list` 的 `STATE` 是 `enabled`,不是 `kubectl apply` 成功 | `enabled` / `generic_kprobe` / `462.15 kB` |
| **策略事件對得回操作** | 事件的 `policy_name`、`file_arg.path`、`int_arg`、`process.binary`、`pod` 五項全部對應到自己做的那一件事 | 五項全中,延遲 1.0 毫秒 |
| 核心層過濾是真的 | 3000 次不符合選擇器的操作,策略事件數 | **0 筆** |
| 出廠事件率(匯出後) | 閒置窗口的匯出事件數 | 0 筆 / 132 秒 |
| 出廠事件率(核心側) | 同一顆節點直接接 gRPC 的事件數 | **1915 筆/分鐘** |
| BPF LSM 可用 | agent 自己回報的能力字串 | `lsm: true`、`override_return: true`、`signal: true`、`fmodret: true` |

## 地雷記錄

### 地雷 1:CRD 不是 Helm 裝的,是 operator 在執行期建的 {#mine-1}

**症狀**:`helm install --wait` 回報成功,自動化流程接著套用 TracingPolicy,偶爾失敗說找不到那個資源類型。

**根因**:Helm 只會自動安裝 chart 裡 `crds/` 目錄的東西,而這個 chart 把 CRD 放在 `crds-yaml/`:

```console
$ helm pull cilium/tetragon --version 1.7.0 --untar && ls tetragon/
Chart.yaml  crds-yaml/  LICENSE  README.md  templates/  values.yaml
                ↑ 不是 crds/

$ helm template cilium/tetragon --version 1.7.0 --include-crds | grep -c CustomResourceDefinition
0
```

改由 operator 在執行期建立。實測時序剛好對:

```text
11:17:19  helm install 開始
11:17:27  tracingpolicies.cilium.io          建立
11:17:29  tracingpoliciesnamespaced.cilium.io 建立
11:17:40  helm 回 STATUS: deployed
```

但**順序對是運氣,不是保證**——`--wait` 等的是 DaemonSet 與 Deployment 就緒,不是 CRD 存在。

**後果**:`helm template --include-crds` 印不出它們、GitOps 工具按 manifest 修剪時不會管理它們、`helm uninstall` 也不會帶走它們。

**修法**:在同一份自動化裡先裝 Tetragon 再套策略的話,要自己等 CRD 出現,不能靠 `helm --wait`。

### 地雷 2:預設排除清單裡的 `""` 是「整台主機」 {#mine-2}

**症狀**:匯出的事件流安靜得不合理——閒置窗口 0 筆,看起來比 Falco 還乾淨。

**根因**:chart 的預設值:

```yaml
export-denylist: |-
  {"health_check":true}
  {"namespace":["", "cilium", "kube-system"]}
```

**空字串命名空間的意思是「這個行程不屬於任何 pod」,也就是主機上的每一個行程。** 同一顆節點直接接 gRPC 量到 1915 筆/分鐘,其中 1898 筆(99.1%)正是它。

三件事都是可以正當防守的預設——沒有人想要 9.8 GB/天/節點 的節點代理程式日誌。問題在別的地方:

1. **這是 chart 的預設,不是 Tetragon 的預設。** 讀 Tetragon 的概念文件不會知道,`kubectl get ds` 不會顯示,agent 啟動日誌裡有但埋在 40 個其他鍵值之間。
2. **它過濾在最貴的一關。** BPF 程式跑了、ring buffer 佔了、protobuf 解完了、pod 反查也做了,才在匯出層丟掉。核心層過濾的省錢效果在這一段完全沒有發生。
3. **它把整台主機從視野裡拿掉。** [地雷 6](#mine-6) 的攻擊路徑起點是「拿到節點 root」;一個在節點上直接跑、不在任何容器裡的行程,命名空間就是空字串——**出廠設定下,它一筆都不會出現在匯出的事件流裡**。

還有一個細節說明這份清單是怎麼寫出來的:chart 預設把 Tetragon 裝在 `kube-system`,所以它自己被排除。裝在別的命名空間,Tetragon 就會報自己。

### 地雷 3:每一筆事件都揹著整份節點標籤 {#mine-3}

```console
$ ./tetra-events.sh --size
events=23  total=101694 B  avg=4421 B/event  node_labels=2158 B/event (49%)
```

**一筆 4421 B 的事件裡,2158 B 是 `node_labels`**——雲端供應商塞在節點上的那四十幾個標籤,每一筆事件重複一次。以量到的原始速率算,**光是節點標籤就是 4.8 GB/天/節點**。

這個放大係數**是雲端供應商決定的,不在你的設定檔裡**——跟 [Day 4 地雷 4](sprint2-day4-falco-custom-rules.md#mine-4)(`ndots:5` 的六倍放大是叢集 DNS 設定決定的)是同一個家族。自架叢集的節點標籤大概十來個,同一份 chart、同一個工作負載,事件大小差兩倍以上。

**任何用文件範例事件大小做的成本估算,在受管叢集上會低估一半。**

關掉的方法是 `field-filters`,但那是一份要自己寫的欄位路徑清單,而且**沒有任何預設值或範例把 `node_labels` 列進去**。

### 地雷 4:`kubectl apply` 成功、物件健康、策略根本沒載入 {#mine-4}

**症狀**:四個 Kubernetes 端的指標全綠。

```console
$ kubectl apply -f tracingpolicy-file-open.yaml
tracingpolicynamespaced.cilium.io/lab-etc-read created      ← 成功
$ kubectl -n ebpf-lab get tracingpoliciesnamespaced
lab-etc-read   8s                                           ← 存在
$ kubectl -n ebpf-lab get tracingpolicynamespaced lab-etc-read -o json | jq .status
<absent>                                                    ← 沒有 status
$ kubectl -n ebpf-lab describe tracingpolicynamespaced lab-etc-read | tail -1
Events:  <none>                                             ← 沒有 event
```

**根因**:策略第一版帶了 `returnArgAction: "Post"`,那是各種範例裡最常見的寫法。實際狀態只有 agent 知道:

```console
$ kubectl -n tetragon exec <tetragon-pod> -c tetragon -- tetra tracingpolicy list
ID  NAME          STATE        FILTERID  NAMESPACE  SENSORS  KERNELMEMORY  MODE
2   lab-etc-read  load_error   2         ebpf-lab            0 B           unknown
```

```text
level=warn msg="adding tracing policy failed"
  error="… ReturnArgAction type 'Post' unsupported;
         omit returnArgAction or use 'TrackSock'/'UntrackSock'"
```

而 CRD schema 裡**正確答案就寫在那個欄位的說明文字上**,偏偏該欄位是 `'type': 'string'` 而**沒有 `enum`**——API server 只檢查型別,所以任何字串都收。

這是 [Day 4 地雷 2](sprint2-day4-falco-custom-rules.md#mine-2)(`evt.dir` 淘汰而 helm 全綠)的同一個形狀,**而且更難察覺**:Falco 至少把警告印在 pod 自己的日誌裡,這裡是「Kubernetes 原生、看起來就該有 status 的 CRD」什麼都不說。GitOps 面板會是綠的,`kubectl wait` 沒有東西可以等。

**驗收條件只有一個:`tetra tracingpolicy list` 的 `STATE` 是 `enabled`。**

### 地雷 5:策略讓它變準,不會讓它變安靜 {#mine-5}

同一個 3000 次讀取的 burst,同一個 50 秒窗口:

| | 事件數 |
|---|---|
| 策略產生的 `process_kprobe` | **0** |
| Tetragon 匯出的總事件 | **6016** |
| Falco 告警 | **0** |

那 6016 筆全是 3000 次 `cat` 的 exec 與 exit,來自**跟策略無關的 `__base__` sensor**——它永遠開著,不歸任何 TracingPolicy 管。

三個實務後果:

1. **「只裝了一條很窄的策略,所以很便宜」是錯的。** 事件量由工作負載的 exec 頻率決定,跟策略一個字都沒關係。一顆會 fork 的 CI runner、一個 shell 密集的 entrypoint,就足以把量拉上去。
2. **刪掉策略不會讓它安靜。** 回到零策略只是回到 `__base__`,也就是[地雷 2](#mine-2) 那個 1915 筆/分鐘。
3. **要調的旋鈕不在 TracingPolicy 裡**,在 chart 的匯出設定裡(排除清單、欄位過濾、速率限制),而那些是**使用者空間的**旋鈕——省的是下游頻寬與儲存,不省核心與 CPU。

跟 Falco 的分工在這裡變得很具體:**Falco 的成本是固定的(每個事件比對 27 條規則)而輸出接近零;Tetragon 的核心成本很低而輸出正比於工作負載的 exec 量。** 兩者都不是便宜,只是把帳單開在不同欄位。

### 地雷 6:`nsenter` 的歸屬,Tetragon 跟 Falco 錯得一樣 {#mine-6}

[Day 3 地雷 5](sprint2-day3-falco-basics.md#mine-5) 的結論是:Falco 看得見 `nsenter`,但因為身分是靠 cgroup 反查的,而 `nsenter` 不換 cgroup,所以告警被標成呼叫端。

Tetragon 的機制完全不同——身分不是事後反查容器執行期,是 BPF 在 `execve` 當下把 cgroup id 寫進 map,agent 再對 Kubernetes informer 查表。「在核心裡當場記錄」聽起來就該比「事後去問執行期」準。

實測:

```text
03:31:22.303  process_exec  <呼叫端 pod>  /usr/bin/nsenter
              '-t 21743 -m -u -i -n -p /bin/bash -c "hostname; cat /etc/shadow …"'
03:31:22.341  process_kprobe <呼叫端 pod>  /usr/bin/cat '/etc/shadow'
              fn=security_file_permission  policy=lab-etc-read
```

而事情發生在哪裡,指令自己回答了:

```console
$ … nsenter -t 21743 -m -u -i -n -p /bin/bash -c 'hostname; cat /etc/shadow …'
baseline-nginx                    ← hostname 回報的是被進入的那顆
```

對照同一次實驗裡直接 exec 進去做的同一件事:

```text
03:31:22.964  process_kprobe  ebpf-lab/baseline-nginx/nginx  /usr/bin/cat '/etc/shadow'
```

**兩筆事件,同一個檔案、同一支 binary、相隔 0.6 秒、貼著兩個不同的 pod 名字,其中一個是錯的。**

**為什麼「在核心層」救不了這件事:pod 不是核心的概念。** 核心知道 pid、mount namespace、UTS namespace、cgroup。「這是哪顆 pod」是把 cgroup id 對到 Kubernetes 物件得到的推論,而 `nsenter` 換的是命名空間、不換 cgroup。無論在核心裡把 cgroup id 記得多早、多準,記到的都是**發起者的** cgroup。

三個工具、三種架構、同一個根因:

| | 結果 |
|---|---|
| Day 2 bpftrace(綁 cgroup 過濾) | **完全看不到** |
| Day 3 Falco(全節點探針加執行期反查) | 看得到,**pod 認錯** |
| Day 5 Tetragon(全節點探針加核心層 cgroup id) | 看得到,**pod 認錯** |

好消息跟 Day 3 一樣:**訊號沒有消失,只是貼錯標籤**。而 Tetragon 多給了一樣 Falco 沒有的東西——**`nsenter` 這個指令本身連同完整參數就是一筆頭等事件,零策略、零規則**。Day 3 記過「出廠 25 條沒有一條在看 `nsenter`」;Tetragon 不需要有人先想到要看它。調查員拿到錯的 pod 名字之後,往上一層看 `parent=/usr/bin/nsenter`、再看 `-t 21743` 這個參數,**真正的目標就在裡面**。

### 地雷 7:Tetragon 的事件裡沒有 tty 欄位 {#mine-7}

偏離 A(包了 pty 的 fork shell)與偏離 B(沒有終端機的 web shell)在 Tetragon 的事件流裡**組成完全相同**:各 3 筆 exec、3 筆 exit、12 筆 kprobe,binary、parent、pod、參數全部對應得上,只有時間戳與 pid 不同。

**根因**:`process` 結構裡根本沒有這個欄位。

```text
process struct keys: arguments, auid, binary, cwd, docker, exec_id, flags,
                     in_init_tree, parent_exec_id, pid, pod, refcnt,
                     start_time, tid, uid
```

[Day 3 地雷 3](sprint2-day3-falco-basics.md#mine-3) 的整個故事、Day 4「`tty=0` 本身就是『不是真人打的 `kubectl exec`』的訊號」那個結論——**在 Tetragon 的預設事件裡不存在**。

反過來,B 這一格也是 Falco 出廠規則最難看的一格:只報 1 筆,而且是自訂規則報的,出廠的 `Terminal shell in container` 因為 `proc.tty != 0` 完全靜音。Tetragon 在這裡的 18 筆雖然沒有任何判斷,但它**至少沒有漏**。

**這一格就是工具分工的核心:Falco 有一個 Tetragon 沒有的欄位,Tetragon 有一個 Falco 沒有的保證——它不做判斷,所以不會判斷錯。**

### 地雷 8:chart 不給 resources 也不給 priorityClassName {#mine-8}

```console
$ kubectl -n tetragon get pod <tetragon-pod> -o jsonpath='{.status.qosClass} prio={.spec.priority}'
BestEffort prio=0
$ kubectl -n falco get pod <falco-pod> -o jsonpath='{.status.qosClass} prio={.spec.priority}'
Burstable prio=0
```

Falco 的 chart 至少給了 requests(超額 4–7 倍,[Day 3 地雷 7](sprint2-day3-falco-basics.md#mine-7)),所以它是 Burstable。Tetragon 的 DaemonSet 容器是 `resources: {}`——**BestEffort**,記憶體吃緊時第一批被 OOM kill。priority 兩邊都是 0,一樣低於每個系統元件。

而 `tolerations: [{operator: Exists}]` 表示**它容忍一切**。好處是不用像 Day 3 那樣替 Falco 補 toleration;壞處是它會排到任何一顆節點,包括你不想付這份監控成本的節點。

**Tetragon 的失效形狀跟 Falco 又不一樣**:Falco 被踢是「少看到事件」;Tetragon 的 agent 被踢,**BPF 程式還 pinned 在核心裡**,只是沒有人在讀 ring buffer——**核心的成本照付、事件全丟、健康檢查不會叫**。這是三種失效裡最糟的一種。

還有一個排程上的後果:兩套工具實測共吃 18–38m CPU、107–210Mi,而**宣告給排程器的 requests 只有 Falco 那一份**。Tetragon 對排程器是隱形的。

## 帶得走的東西

- **「在核心裡做」不會讓 pod 歸屬變準,因為 pod 不是核心的概念。** 核心只知道 pid、cgroup、命名空間;「哪顆 pod」永遠是把 cgroup 對到 Kubernetes 物件的推論。任何靠 cgroup 認身分的工具,都繼承同一組限制,換架構不會換掉它。
- **核心層過濾是真的、免費的、完美的——但它只管你自己寫的那條策略。** 3000 次不符合的操作產生 0 筆策略事件;同一個窗口卻匯出 6016 筆,因為底層 sensor 不歸策略管。省的地方跟花的地方不是同一個地方。
- **看起來安靜的預設值,可能只是把東西丟在最貴的一關。** 事件流的 0 筆與核心側的 1915 筆是同一顆節點同一段時間——差別是一行排除清單,而它過濾在 BPF、ring buffer、解碼、身分反查全部做完之後。
- **Falco 交付判斷,Tetragon 交付紀錄,而缺的那塊剛好是對方的長處。** 同樣四個動作,Falco 6 筆帶規則名與嚴重度,Tetragon 78 筆全部要人再看一遍;但沒寫規則的事 Falco 看不見,Tetragon 照記。
- **監控元件的資源宣告要自己補。** 兩套工具實測共吃的量,只有一半有告訴排程器。BestEffort 的監控元件在節點吃緊時第一個消失,而它消失的方式不會觸發任何健康檢查。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[TracingPolicy 的概念與組成](https://tetragon.io/docs/concepts/tracing-policy/)** —— 官方對「掛勾點加上核心內過濾的選擇器」這個結構的說明,對得上步驟 4 的逐欄拆解。
- **[選擇器的完整語法](https://tetragon.io/docs/concepts/tracing-policy/selectors/)** —— `matchArgs`、`matchBinaries`、`matchPIDs` 等各種過濾器與運算子;官方明載同一個選擇器內的條件是 AND、多個選擇器之間是 OR。
- **[Helm chart 參數表](https://tetragon.io/docs/reference/helm-chart/)** —— 地雷 2 與地雷 8 的一手來源:`exportDenyList` 的預設值(含那個空字串命名空間)與 `resources` 的空物件預設,都逐字列在這份表裡。
- **[Falco 規則的三個基本元件](https://falco.org/docs/concepts/rules/basic-elements/)** —— 對照組。把它跟 TracingPolicy 的結構並排讀,兩種設計哲學的差別比任何文字說明都清楚。

## 下一步

今天所有跟攔截有關的開關都刻意關著,而這座叢集的核心能力四項全有——agent 自己回報 `lsm`、`override_return`、`signal`、`fmodret` 都是 `true`,策略裡的 `MODE` 全程是 `monitor_only`。

Day 6 把那個 `return.int_arg: 0` 改掉:違規的行程直接 SIGKILL。然後面對兩個真正困難的問題——**SIGKILL 到底是阻止了操作,還是只是事後把行程殺掉**,以及**寫錯一條攔截規則,從應用程式那一側看起來是什麼樣子**。今天量到的 `nsenter` 歸屬錯誤,在攔截情境下也會從「標籤貼錯」變成別的東西。

---

!!! quote ""
    Tetragon 標誌為 Tetragon 專案之官方資產,此處作社群教學用途。
