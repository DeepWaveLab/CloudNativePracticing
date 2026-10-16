# Day 1：GameServer 已 Ready，外部玩家連得到嗎？

![Agones 官方標誌](../assets/logos/agones-icon-color.svg){ align=right width="90" }

第一個 GameServer 已經 Ready，但玩家從自己的電腦送出 UDP 訊息卻沒有回應。是遊戲程式沒準備好、連錯了動態埠，還是節點前的防火牆沒有放行？這章會依序確認控制元件、伺服器位址與外部 UDP 回應，讓你能分辨「伺服器已準備好」和「玩家真的連得上」。

## Agones 與 GameServer

Deployment 會維持副本數，但不知道哪個副本正持有一場對戰的記憶體狀態。遊戲進行中若換成另一個 Pod，新的副本不會自動接續原 session。Agones 用自訂資源定義（CRD，擴充 Kubernetes 可接受的物件種類）表達遊戲伺服器，並管好它從啟動、可以接受玩家連線、被分配給玩家，到結束的整個生命週期。

最小的物件是 `GameServer`。一個 `GameServer` 對應一個 Pod，裡面執行遊戲程式與 SDK sidecar；遊戲程式透過 sidecar 回報準備與健康狀態。Agones 會動態分配遊戲用的 hostPort，並把入口回報在 `GameServer.status.address` 與 `status.ports`。Deployment 的 Pod 也能設定 hostPort，但 Deployment 本身不提供這套遊戲入口分配與回報機制。本章玩家直接連這組 node IP + port，中間沒有 Service。

先看玩家封包要經過哪些位置。GameServer Ready 只涵蓋伺服器狀態，還沒有驗證圖中的整條外部路徑。

![外部 UDP 經 NSG 與節點 hostPort 抵達 GameServer](../assets/diagrams/s5d1-external-udp.svg)

後面最後一步會從叢集外送出請求，補上這段路徑的證據。

game server pod 用 `nodeSelector` 釘在 `role=gameserver` 的節點池，因為這組節點有公網 IP；`portPolicy: Dynamic` 讓 Agones 挑一個 hostPort，外部 client 連的是 `GameServer.status.address` 與 `status.ports[].port`。

**你需要什麼**：

- 一座 AKS 叢集，本章驗證組合為 Kubernetes 1.34.10 與 Agones 1.60.0；使用其他組合前，先依 Agones 對應版本的支援矩陣確認。
- 一個有節點公網 IP 的 game-server 節點池（步驟 1）。沒有節點公網 IP，client 就打不到 node:hostPort，只能改走 LoadBalancer 或其他路由層，那是 Day 4 的做法。
- 本機的 `kubectl`、`helm`、`az` 與 `nc`。

動手前用 `kubectl config current-context` 確認 kubectl 指向你的叢集。

## 步驟 1:建立 game-server 節點池

game-server 節點池要有節點公網 IP，並帶上 `role=gameserver` label，讓後面的 `GameServer` 用 `nodeSelector` 排上來：

```console
$ az aks nodepool add -g <RESOURCE-GROUP> --cluster-name <CLUSTER> -n gamesrv \
    --node-count 2 --node-vm-size Standard_D2s_v5 --enable-node-public-ip \
    --labels role=gameserver --mode User
```

`<RESOURCE-GROUP>` 與 `<CLUSTER>` 是你建立 AKS 時用的 resource group 與叢集名稱，可以用 `az aks list -o table` 查到。建好後用 `kubectl get nodes -o wide` 確認 gamesrv 節點的 `EXTERNAL-IP` 欄位有值。

## 步驟 2:安裝 Agones 1.60

本課 Agones 1.60.0 與 Kubernetes 1.34.10 的安裝曾遇到 CRD 欄位驗證問題（見[疑難排解：CRD 安裝失敗](#mine-1)）。下面直接採用驗證成功的分開安裝流程。做法是把 CRD 與控制面拆開裝：CRD 用 `kubectl create --validate=false`，Helm release 則關掉 CRD 安裝。

**CRD**：Agones chart 的 5 個 CRD 放在 `templates/crds/`。先加入 Agones 的 Helm repo，再用 `helm template --show-only` 只渲染這 5 個檔案，交給 `kubectl create --validate=false`：

```console
$ helm repo add agones https://agones.dev/chart/stable
$ helm repo update
$ helm template agones agones/agones --version 1.60.0 -n agones-system \
    --show-only templates/crds/fleet.yaml \
    --show-only templates/crds/fleetautoscaler.yaml \
    --show-only templates/crds/gameserver.yaml \
    --show-only templates/crds/gameserverallocationpolicy.yaml \
    --show-only templates/crds/gameserverset.yaml \
  | kubectl create --validate=false -f -
```

用 `create` 避開更新時的 typed patch 路徑；`--validate=false` 在支援 server-side field validation 的環境要求忽略未知欄位檢查，不是讓 API server 接受任何無效資源。這是針對本章已知額外欄位的處理，不能當成所有安裝錯誤的通用修法。建立成功時會印出 5 行 `customresourcedefinition... created`，對應 `fleets`、`fleetautoscalers`、`gameservers`、`gameserverallocationpolicies`、`gameserversets` 這 5 個 CRD。

**控制面**：用 Helm 安裝，但 chart 不要碰 CRD：

```console
$ helm install agones agones/agones --version 1.60.0 \
    --set agones.crds.install=false \
    -n agones-system --create-namespace --wait
STATUS: deployed
```

裝完後，`kubectl -n agones-system get pods` 在這組驗證配置看到 9 個 Pod 全部 `1/1 Running`：`agones-allocator` 3 顆、`agones-controller` 2 顆、`agones-extensions` 2 顆、`agones-ping` 2 顆。

controller 是持續協調資源狀態的控制元件。`agones-controller` 處理 `GameServer`、`Fleet` 與 `GameServerSet`。`agones-allocator` 是給叢集外呼叫 allocation 用的 gRPC／REST 服務；在叢集內建立 allocation 時，直接對 Kubernetes API 建立 `GameServerAllocation` 就好，不需要經過它。`agones-ping` 提供讓 client 量測延遲的 ping 服務。

## 步驟 3:建立第一顆 GameServer

[`gameserver.yaml`](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day1/gameserver.yaml) 的主體如下，映像是 Agones 範例 `simple-game-server:0.43`，容器 port 是 `7654`，hostPort 由 Agones 動態配置：

```yaml
apiVersion: agones.dev/v1
kind: GameServer
metadata:
  generateName: simple-game-server-
spec:
  ports:
    - name: default
      portPolicy: Dynamic
      containerPort: 7654
  template:
    spec:
      nodeSelector:
        role: gameserver
      containers:
        - name: simple-game-server
          image: us-docker.pkg.dev/agones-images/examples/simple-game-server:0.43
          resources:
            requests: {memory: 64Mi, cpu: 20m}
            limits: {memory: 64Mi, cpu: 20m}
```

因為 metadata 用的是 `generateName`，這份檔案要用 `kubectl create`，不能用 `kubectl apply`（見[疑難排解：generateName 無法使用 apply](#mine-2)）：

```console
$ kubectl create -f gameserver.yaml
gameserver.agones.dev/simple-game-server-8vlf6 created
$ kubectl get gameserver -o wide
NAME                       STATE   ADDRESS          PORT   NODE
simple-game-server-8vlf6   Ready   <NODE-IP>   7092   aks-gamesrv-18046246-vmss000000
```

看三個欄位：`STATE` 是 `Ready`，`ADDRESS` 是 game-server 節點的公網 IP，`PORT` 是 Agones 配出的動態 hostPort。

## 步驟 4:放行 Agones 使用的 UDP port range

AKS 節點前面還有 NSG（Network Security Group），也就是控制進出網路流量的規則。`GameServer` 已經拿到 hostPort，不代表 Internet 可以打進去。這裡直接放行 `7000-8000/UDP`：

```console
$ az network nsg rule create -g <NODE-RESOURCE-GROUP> \
    --nsg-name <NSG-NAME> -n allow-agones-udp \
    --priority 1000 --direction Inbound --access Allow --protocol Udp \
    --destination-port-ranges 7000-8000 --source-address-prefixes Internet
```

`<NODE-RESOURCE-GROUP>` 是 AKS 自動建立、放節點 VM 的 resource group（名稱以 `MC_` 開頭），可以用 `az aks show -g <RESOURCE-GROUP> -n <CLUSTER> --query nodeResourceGroup -o tsv` 查到；`<NSG-NAME>` 用 `az network nsg list -g <NODE-RESOURCE-GROUP> --query "[].name" -o tsv` 查。

這裡放的是 port range，因為 `portPolicy: Dynamic` 不保證下一顆 server 還是同一個 port。Fleet 與 autoscaler 會陸續建立新的 server，只放單一 port，後面的 server 會隨機連不上。

## 步驟 5:從叢集外用 UDP 直連

回到本機，對 `GameServer` status 回報的 address 與 port 送 UDP 封包。下面的 7092 是這次分配值，請連同 `<NODE-IP>` 一起換成你剛查到的結果：

```console
$ printf 'Hello Agones from client\n' | nc -u -w4 <NODE-IP> 7092
ACK: Hello Agones from client
```

client 沒有打 Kubernetes Service，也沒有打 LoadBalancer，直接打到 node 公網 IP 與 Agones 分配的 hostPort。收到 `ACK` 表示封包穿過 NSG、進到該 node 的 hostPort，最後被 `simple-game-server` 處理。

## 疑難排解

### 安裝 CRD 時出現 typed patch 或 unknown field 錯誤 {#mine-1}

**症狀**：`helm install agones` 對 `fleets`、`gameservers`、`gameserversets` 三個 CRD 報 `server-side apply failed ... failed to create typed patch object`。改用 `kubectl apply --server-side` 會撞同一個錯；改 `kubectl create` 又會報 `strict decoding error: unknown field "...x-kubernetes-patch-strategy"` 與 `"...x-kubernetes-patch-merge-key"`。

**根因**：這三個 CRD 內嵌 `PodTemplateSpec` schema，帶了 `x-kubernetes-patch-strategy` 與 `x-kubernetes-patch-merge-key`。這些不是合法的 CRD structural schema 擴充；驗證環境的嚴格欄位檢查會拒收，server-side apply 也建不出 typed patch。Agones chart 又把 CRD 放在 `templates/crds/`，所以 `--skip-crds` 不會跳過它們。

**修法**：先從 Helm template 抽出 5 個 CRD，用 `kubectl create -f - --validate=false` 建立；接著 `helm install` 加 `--set agones.crds.install=false`，讓 chart 不再碰 CRD。若曾安裝失敗，先確認 release 狀態與殘留物件的擁有者，再處理本次安裝留下的資源。清單可能同時包含叢集與命名空間物件，不能直接刪除同類型的所有物件。新建練習環境依前述流程即可，不需預先清理其他資源。

### kubectl apply 不接受 generateName {#mine-2}

**症狀**：對使用 `metadata.generateName` 的 `GameServer` manifest 下 `kubectl apply`，會得到 `error: cannot use generate name with apply`。

**根因**：`apply` 要能追蹤同一個物件的宣告狀態；`generateName` 的語意是每次建立新物件，沒有固定名稱可讓 apply 對齊。

**修法**：建立這類一次性 `GameServer` 時用 `kubectl create -f gameserver.yaml`。Day 2 的 `Fleet` 有固定 `metadata.name`，就可以回到 `kubectl apply`。

## 想一想

1. GameServer Ready，卻沒有外部 ACK，除了遊戲程式外，還要查哪兩個入口條件？
2. 第二次建立 GameServer 分配到另一個 hostPort，為什麼只放行 7092 會出問題？
3. 使用 generateName 的檔案重複執行 create，會更新原伺服器，還是建立新實例？

## 小結

先確認 Agones 控制元件與 GameServer Ready，再使用 status 的實際位址和動態埠，從叢集外取得 UDP 回應。這樣才同時驗到伺服器狀態與外部路徑；Ready 本身不會替你開放 NSG。[Day 2](sprint5-day2-fleet-lifecycle.md) 會把單一 GameServer 包成 Fleet，觀察副本不足時如何補建。

## 延伸閱讀

想再深入，可以從這幾份開始：

- **[Agones on Azure Kubernetes Service](https://agones.dev/site/docs/installation/creating-cluster/aks/)** —— 官方的 AKS 建叢集說明，包含 `--enable-node-public-ip` 與 NSG 放行 7000–8000/UDP 的做法。
- **[Install Agones using Helm](https://agones.dev/site/docs/installation/install-agones/helm/)** —— Helm 安裝參數，包括本章用到的 `agones.crds.install`。
- **[建立第一顆 GameServer](https://agones.dev/site/docs/getting-started/create-gameserver/)** —— 對照本章的 `kubectl get gs`、node address、port 與 `nc -u` 連線方式。
- **[Agones releases](https://github.com/googleforgames/agones/releases)** —— 本章使用 `1.60.0`，升級前先看這裡的 release notes。

---

!!! quote ""
    Agones 標誌為 CNCF（Linux Foundation）官方資產，此處作社群教學用途。
