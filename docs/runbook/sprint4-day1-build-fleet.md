# Day 1: 建一個 fleet——三座叢集、兩座加入 hub

![KubeFleet 官方標誌](../assets/logos/kubefleet-icon-color.svg){ align=right width="84" }

> [Day 0](sprint4-day0-multicluster-concepts.md) 把 hub-spoke 講完了:一座 hub 當控制平面、member 主動拉。今天真的建出來——一座 hub、兩座 member,用 `MemberCluster` 物件讓 member 加入,親眼看 `JOINED` 從 `Unknown` 翻成 `True`。

!!! abstract "你在課程的哪裡"
    - **Day 0**:多叢集是什麼問題、KubeFleet 的 hub-spoke 模型。純概念。
    - **今天**:把模型變成三座真的叢集,裝 KubeFleet v0.3.1,讓兩座 member 加入 hub,驗到 heartbeat 與資源回報。
    - **Day 2**:在這三座叢集上,用 `ClusterResourcePlacement` 把工作負載散到指定的 member。

## 先解決一個名詞:kind 有兩個意思

今天會一直用到 **kind** 這個字,而它在本章有**兩個完全無關**的意思,先切開:

- **kind(這個工具)**:全名 Kubernetes-in-Docker,**把一整座 Kubernetes 叢集跑進 Docker 容器裡**的測試工具。一台機器上可以用它開好幾座叢集,開完即丟。下文一律寫「kind 這個工具」。
- **YAML 的 `kind:` 欄位**:例如 `kind: MemberCluster`,指這份 YAML 描述的 Kubernetes **物件型別**。

兩者只是**撞名**,毫無關係。之所以今天用 kind 這個工具,是因為多叢集實驗要好幾座叢集,在雲上各開一座真叢集又慢又貴;kind 這個工具能在**一台機器裡**開三座,幾十秒建好、實驗完連機器一起丟。

!!! note "為什麼所有東西都在一台雲端 VM 裡,本機一個字都不裝"
    這門課有一條硬規矩:**不在你自己的電腦上裝任何東西**。所以今天的做法是先開一台 Azure Linux VM,把 Docker、kind 這個工具、`kubectl`、`helm` **全部裝進那台 VM**,本機只用 SSH 連進去操作。實驗做完整台 VM 刪掉,本機的 `~/.kube/config` 從頭到尾沒被碰過。

## 今天要走的路

| 步驟 | 做什麼 | 產出 |
|---|---|---|
| 0 | 開一台 Azure VM,VM 內裝齊工具鏈 | 一台乾淨的 Linux 操作環境 |
| 1 | 用 kind 這個工具開三座叢集(hub ×1 + member ×2) | 三個 kubeconfig context |
| 2 | 在 hub 裝 KubeFleet 的 hub-agent | hub 上多出 27 個 CRD |
| 3 | 讓兩座 member 加入(`MemberCluster` 物件) | member 上跑起 member-agent |
| 4 | 驗收:`JOINED=True` + heartbeat + 資源回報 | gate 通過 |

## 步驟 0: 一台 VM,工具鏈全裝在裡面

VM 開在 japaneast,規格 `Standard_D4as_v5`(4 vCPU / 15 GiB),映像 Ubuntu 24.04:

```sh
az vm create \
  --resource-group souch-cloudnative-sprint1 \
  --name cnp-fleet-vm \
  --image Ubuntu2404 \
  --size Standard_D4as_v5 \
  --location japaneast \
  --admin-username azureuser \
  --ssh-key-values <你的公鑰>.pub \
  --os-disk-size-gb 64 \
  --public-ip-sku Standard \
  --nsg-rule SSH \
  --tags sprint=sprint4 purpose=kubefleet-lab owner=你
```

SSH 進去之後,VM 內裝這四樣(實測版本):

| 工具 | 版本 | 裝法 |
|---|---|---|
| Docker Engine | `29.7.2`(containerd `v2.3.3`) | Docker 官方 apt repo,原生安裝 |
| kubectl | `v1.36.3` | dl.k8s.io stable |
| kind(這個工具) | `v0.27.0` | GitHub release 二進位檔 |
| helm | `v3.21.3` | get-helm-3 指令稿 |

!!! info "為什麼裝原生 Docker,而不是 colima"
    colima 的用途是「在**沒有 Linux 核心**的機器(主要是 macOS)上,起一台 Linux 虛擬機來跑 Docker」。今天的宿主本身已經是一台 Linux VM、有 Linux 核心,直接裝 **Docker Engine 原生**就滿足「讓 kind 這個工具有 Docker 可用」的需求。在 Linux VM 裡再疊 colima,等於在 VM 裡再開一台 VM,只會拖慢。兩種做法對後面的步驟等價——kind 這個工具看到的都是一個可用的 Docker。

## 步驟 1: 用 kind 開三座叢集

一台機器上開 hub ×1 + member ×2。今天的 gate 只需要 hub + 1 member,但 Day 2 的展示要用到 2 member,所以一次開到位。節點映像釘 `kindest/node:v1.32.8`:

```
### kind create cluster --name kf-hub-01 ###
>>> kf-hub-01 created in 35s
### kind create cluster --name kf-member-01 ###
>>> kf-member-01 created in 17s
### kind create cluster --name kf-member-02 ###
>>> kf-member-02 created in 23s
### kubeconfig contexts ###
NAME                CLUSTER
kind-kf-hub-01      kind-kf-hub-01
kind-kf-member-01   kind-kf-member-01
kind-kf-member-02   kind-kf-member-02
```

**三座叢集全在同一台 VM 的 Docker 網路裡**,各自有一個容器內位址。member 之後要連 hub,用的是這個 Docker 網路位址,不是 kubeconfig 裡那個 `127.0.0.1:<對映埠>`:

```
kf-hub-01-control-plane    = 172.18.0.2   ← member 之後連這個
kf-member-01-control-plane = 172.18.0.3
kf-member-02-control-plane = 172.18.0.4
```

三座叢集加起來 75 秒建好。**這是 kind 這個工具的價值**:同一台機器、幾十秒、三座隔離的叢集。

## 步驟 2: 在 hub 裝 hub-agent

KubeFleet 的 helm chart 放在 OCI registry(不是傳統 helm repo),v0.3.1:

```sh
helm upgrade --install hub-agent \
    oci://ghcr.io/kubefleet-dev/kubefleet/charts/hub-agent \
    --version 0.3.1 --namespace fleet-system --create-namespace \
    --set logFileMaxSize=100000
```

輸出:

```
Pulled: ghcr.io/kubefleet-dev/kubefleet/charts/hub-agent:0.3.1
STATUS: deployed
>>> hub-agent helm install returned in 2s
deployment "hub-agent" successfully rolled out
### hub-agent pods ###
NAME                        READY   STATUS    RESTARTS   AGE
hub-agent-fb4d5dd97-wsgb7   1/1     Running   0          10s
```

**hub-agent 一裝上,就在 hub 建了 27 個 CRD**——分屬 `placement` 與 `cluster` 兩個 API group。這批 CRD 就是後面幾天的操作對象(CRP、override、staged update run 等),Day 4 會用到;今天先記得「裝 hub-agent = hub 長出這一整套 fleet 的 API」。

## 步驟 3: member 加入——`MemberCluster` 物件

官方 quickstart 附一支 `join-member-clusters.sh`,一次帶兩座 member:

```sh
./join-member-clusters.sh 0.3.1 kind-kf-hub-01 https://172.18.0.2:6443/ \
    kind-kf-member-01 kind-kf-member-02
```

這支指令稿把 [Day 0](sprint4-day0-multicluster-concepts.md) 講的 join 五步自動化:在 hub 建 `MemberCluster` 物件(心跳週期設 15 秒)、建 ServiceAccount 與 token、在 member 上放 hub 的 kubeconfig、`helm install member-agent`。輸出:

```
membercluster.cluster.kubernetes-fleet.io/kind-kf-member-01 created
NAME: member-agent ... STATUS: deployed
membercluster.cluster.kubernetes-fleet.io/kind-kf-member-02 created
NAME: member-agent ... STATUS: deployed
>>> JOIN SCRIPT TOTAL 4s
```

指令稿跑完的當下,兩座 member 都還是 `JOINED=Unknown`(agent 剛起、還在拉狀態)。**member-01 幾秒後轉 `True`;但 member-02 卡住不 join。** 追下去踩到今天最值得記的一顆雷。

## 地雷 #159:一台機器跑三座 kind 叢集,第三座的 kube-proxy 會因 inotify 額度用罄而崩潰

**症狀非常會騙人。** member-02 的 member-agent 一直 `CrashLoopBackOff`,容器日誌只說找不到 token:

```
E ... memberagent/main.go:216] "Failed to retrieve token file from the path %s"
    err="stat /config/token: no such file or directory" /config/token="(MISSING)"
```

而負責補 token 的 sidecar 每 30 秒回報一次「token 資料遺失或為空」:

```
E ... token_refresher.go:75] "Failed to FetchToken"
    err="the token data is missing or empty in secret "
```

**這個訊息會把人帶去完全錯的方向。** 它說「token 遺失或為空」,第一反應是「join 指令稿把空 token 寫進 secret 了」。但實際比對成功的 member-01 與卡住的 member-02:兩者的 token secret 都在、都是同一個合法的 1364 字元 JWT、命名空間與鍵名完全一樣。**token 根本沒問題。**

**真正的線索藏在時間裡。** sidecar 從「開始抓 token」到「報錯」之間**固定間隔 30 秒**——那是一次 API 請求逾時,不是資料為空。往 member-02 的系統元件看:

```
kube-proxy-jmggc   0/1   CrashLoopBackOff
# kube-proxy 日誌:
E ... run.go:72] "command failed" err="failed complete: too many open files"
```

**根因是一條連鎖反應**:kube-proxy 因為 `too many open files` 起不來 → member-02 叢集內的 Service 網路不通(連 `kubernetes.default.svc` 都連不上)→ sidecar 讀 secret 逾時 → 回傳空物件 → member-agent 拿不到 token → crashloop → hub 上永遠是 `Unknown`。

那個 `too many open files` 來自宿主的 inotify 額度用完了。量一下預設值:

```
fs.inotify.max_user_instances = 128     ← 三座 kind 叢集把它吃光了
fs.inotify.max_user_watches   = 125779
```

三座 kind 叢集疊在一台機器上,每座一堆容器與控制器,把 `max_user_instances`(預設才 **128**)用罄,**第三座**的 kube-proxy 就分不到 inotify。第二座建立的 member-01 沒事,第三座的 member-02 中招。

**修法**——把額度調高,重啟受影響的 pod:

```sh
sudo sysctl -w fs.inotify.max_user_instances=8192
sudo sysctl -w fs.inotify.max_user_watches=1048576
# 寫進 /etc/sysctl.d/99-kind-inotify.conf,重開機後仍在
kubectl --context kind-kf-member-02 delete pod -n kube-system -l k8s-app=kube-proxy
kubectl --context kind-kf-member-02 delete pod -n fleet-system -l app.kubernetes.io/name=member-agent
```

kube-proxy 一恢復,member-02 幾秒內就 `JOINED=True`。

!!! warning "這一步要在開多座叢集之前先做"
    kind 這個工具的官方文件確實有「Pod errors due to too many open files」這一條、要你調高 `fs.inotify.*`。但 **KubeFleet 的 quickstart 從頭到尾要你開兩座以上叢集,卻完全沒提這個前置**;而它爆出來的症狀又偽裝成 KubeFleet 自己的「token 為空」。所以:**開多座 kind 叢集之前就先把上面兩個 sysctl 調好**;真的遇到 member-agent 喊 token 遺失,**先去看那座 member 的 kube-proxy 是不是掛了**,別急著重建 token。詳見本章末[地雷記錄](#mine-159)。

## 步驟 4: 驗收——JOINED、heartbeat、資源回報

修掉 #159 之後,在 hub 上查:

```
NAME                JOINED   AGE     MEMBER-AGENT-LAST-SEEN   NODE-COUNT   AVAILABLE-CPU   AVAILABLE-MEMORY
kind-kf-member-01   True     9m57s   5s                       1            2850m           15763040Ki
kind-kf-member-02   True     9m55s   5s                       1            2850m           15763040Ki
```

兩座都 `JOINED=True`、最近一次心跳 `5s` 前、各回報 1 個節點與可用資源。`MemberCluster` 頂層的兩個 condition 逐字:

```json
[
  {"type":"ReadyToJoin","status":"True","reason":"MemberClusterReadyToJoin",
   "message":"Member cluster is ready to join the fleet"},
  {"type":"Joined","status":"True","reason":"MemberClusterJoined",
   "message":"Member cluster has successfully joined the fleet"}
]
```

member-agent 回報的心跳與健康度:

```json
[{"type":"MemberAgent",
  "lastReceivedHeartbeat":"2026-08-13T07:54:26Z",
  "conditions":[
    {"type":"Healthy","status":"True","reason":"InternalMemberClusterHealthy"},
    {"type":"Joined","status":"True","reason":"InternalMemberClusterJoined"}]}]
```

**逐項對照 gate:**

| gate 要件 | 證據 | |
|---|---|---|
| member 成功 join | 兩座 `JOINED=True`,`Joined` condition `status=True` | ✅ |
| hub 上看得到 Joined | 上面的 `kubectl get membercluster` 輸出 | ✅ |
| 看得到 heartbeat | `MEMBER-AGENT-LAST-SEEN=5s`、`lastReceivedHeartbeat` 有時間戳 | ✅ |
| 看得到資源用量 | `NODE-COUNT=1`、`AVAILABLE-CPU=2850m`、`AVAILABLE-MEMORY≈15Gi` | ✅ |

**gate:通過。** 一座 hub、兩座 member,fleet 建起來了。

## 順手確認:Day 4 想教的機制,v0.3.1 都有

hub-agent 一裝好,查 hub 上的 API 資源就能知道 v0.3.1 到底提供哪些進階能力(這決定 Day 4 教什麼)。實測結果:

| Day 4 候選機制 | v0.3.1 有嗎 | 對應物件 |
|---|---|---|
| **envelope**(在 hub 上「不真的生效」的包裝資源) | 有 | `ClusterResourceEnvelope` / `ResourceEnvelope` |
| **staged update run**(分階段推進 + 卡關審核) | 有 | `ClusterStagedUpdateRun` + `ClusterStagedUpdateStrategy` |
| **override**(對單一叢集改欄位) | 有 | `ClusterResourceOverride` / `ResourceOverride` |
| **member taint / toleration**(排除某些叢集) | 有(beta 級) | `MemberCluster.spec.taints` + CRP 的 `tolerations` |
| **property-based 排程** | 有入口 | CRP 的 `policy.affinity` |

**四大機制在 v0.3.1 都有物件落地**,Day 4 不需要因為「缺哪個」而降級(taint/toleration 是 beta 級,屆時會註明)。

## 誠實的差距

- **沒用 colima,改原生 Docker Engine**(理由見步驟 0)。開工前那條「colima + kind」的驗證路,在 Linux VM 上以「原生 Docker + kind」達成,結論(quickstart 走得完)成立,但不是逐字照 colima 走。
- **join 指令稿把 hub 的 kubeconfig secret 建在 member 的 `default` 命名空間**,不是 `fleet-system`。這是官方指令稿的行為,member-agent 也確實去 `default` 讀,兩者一致、能正常運作。只記錄,不是雷。
- **#159 的追查中段走過兩次冤枉路**(重建 secret、重裝 agent,都沒用),直到量 kube-proxy 才定位。上面呈現的是最終正確根因;那兩次無效嘗試不構成教材,只在此據實記一筆。
- **property-based 排程只確認了入口存在**(`policy.affinity`),完整的 property selector 欄位留到 Day 4 才展開。

## 地雷記錄

### 地雷 #159:一台機器跑三座 kind 叢集,第三座 kube-proxy 因 inotify 額度用罄而崩潰,且錯誤訊息偽裝成「token 為空」 {#mine-159}

**症狀**:第三座叢集的 member-agent 一直 `CrashLoopBackOff`,喊 `token file ... no such file`;補 token 的 sidecar 每 30 秒回報一次「token 資料遺失或為空」。表面看完全像 join 時 token 沒寫進去。

**根因**:宿主 `fs.inotify.max_user_instances` 預設 128,被三座 kind 叢集吃光;第三座的 kube-proxy 以 `too many open files` 崩潰 → 該叢集 Service 網路不通 → sidecar 讀自己叢集的 secret 逾時 → 回空物件 → member-agent 拿不到 token。**「token 為空」是最末端的假象,真凶在最上游的 inotify。**

**判斷準則**:member-agent 喊 token 遺失時,**先看那座 member 的 kube-proxy 與 coredns 是不是 Ready**,再看 token secret;sidecar「固定間隔 30 秒才報錯」是逾時(不是資料為空)的訊號。

**修法**:開多座 kind 叢集**之前**就把 `fs.inotify.max_user_instances` 調到 8192、`max_user_watches` 調到 1048576 並寫進 `/etc/sysctl.d/`,再重啟 kube-proxy 與 member-agent。

**為什麼收錄**:kind 這個工具的文件有這條前置,但 **KubeFleet 的 quickstart 要人開多座叢集卻沒提**,而下游症狀又把人往「token 沒寫對」帶——「官方該講而沒講、且錯誤訊息會誤導」的典型。

## 帶得走的東西

- **多叢集實驗不必開多座雲叢集**。kind 這個工具讓你在一台 VM 裡開三座、幾十秒建好、實驗完連 VM 一起丟。這也是「本機零安裝」能成立的關鍵——髒的是一台可拋棄的雲 VM,不是你的電腦。
- **裝 hub-agent = hub 長出整套 fleet 的 API**(27 個 CRD)。member 加入靠的是 hub 上一個 `MemberCluster` 物件,其餘五步自動化完成。
- **`JOINED=True` 不只是「連上了」**,它同時帶回心跳與資源用量(CPU / 記憶體 / 節點數)——這些數字之後就是排程器決定「散到哪座」的依據。
- **錯誤訊息會分層,最外層那層常在說謊。** 「token 為空」是連鎖反應的末端,真凶是最上游的 inotify 額度。遇到 member 不 join,先查那座叢集的基礎網路元件,別先動 token。
- **同一台機器疊多座 kind 叢集有隱藏門檻**(inotify),官方 quickstart 沒寫。開之前先調 sysctl。

## 延伸閱讀

- **[KubeFleet Quickstart](https://kubefleet.dev/docs/getting-started/)** —— 今天用的 hub-agent / member-agent 安裝與 join 指令稿的來源。它**沒寫**的部分才是雷:要你開多座 kind 叢集,卻沒提 `fs.inotify.*` 前置(地雷 #159 的成因)。
- **[kind 這個工具的 "Known Issues"](https://kind.sigs.k8s.io/docs/user/known-issues/)** —— 「Pod errors due to too many open files」那一條,就是 #159 要人事先調高的 `fs.inotify.max_user_instances` / `max_user_watches`。
- **[KubeFleet MemberCluster 概念頁](https://kubefleet.dev/docs/concepts/cluster/)** —— join 五步、`Joined` 與 `Healthy` 兩組 condition 的官方定義,對照今天實測的 status 逐字。

## 下一步

fleet 建好了、兩座 member 都在回報心跳。[Day 2](sprint4-day2-crp-placement.md) 開始用它做事:在 hub 上建一個工作負載,用 `ClusterResourcePlacement` 把它散到指定的 member——先散到全部、再只散一座、再依標籤選,並逐段讀懂 placement status 的六個階段。**環境續用今天這三座叢集,不用重建。**

---

!!! quote ""
    KubeFleet 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
