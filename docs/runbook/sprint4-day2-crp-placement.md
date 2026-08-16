# Day 2: 把工作負載散到指定的叢集——ClusterResourcePlacement

![KubeFleet 官方標誌](../assets/logos/kubefleet-icon-color.svg){ align=right width="84" }

> [Day 1](sprint4-day1-build-fleet.md) 建好了 hub + 兩座 member。今天用 KubeFleet 最核心的物件 `ClusterResourcePlacement`(下稱 CRP),把一個工作負載從 hub 散到 member:先散給全部、再只散一座、再依標籤挑,並逐段讀懂散佈的六個階段。

!!! abstract "你在課程的哪裡"
    - **Day 1**:fleet 建起來,兩座 member `JOINED=True`。
    - **今天**:第一次讓 fleet 做事——CRP 選什麼資源、散到哪些叢集,以及怎麼從 status 確認「到了沒」。
    - **Day 3**:同一個 CRP 加上 rollout 策略與 override,讓推展「推得穩、可差異化」。

環境**續用 Day 1 的三座 kind 叢集**(hub + 2 member,同一台 VM),不重建;Day 1 調好的 `fs.inotify.*` 也還在。名詞沿用 Day 1:**kind 指把 Kubernetes 跑進容器的那個工具**,與 YAML 的 `kind:` 欄位無關。

## 今天要走的路

在 hub 上建一個中性命名的 namespace `workload-demo`(裡面放一個 `web` Deployment),再用三個 CRP 依序、彼此隔離地驗三種選法。每驗完一種就把 CRP 刪掉(**刪 CRP 會連帶把資源從 member 收走**),確保下一種的觀察是乾淨的。

| 階段 | 選法 | 預期 |
|---|---|---|
| 1 | **PickAll**:選所有健康且已 join 的 member | 兩座 member 都拿到 |
| 2 | **PickN(N=1)**:由排程器挑 1 座 | 只有一座拿到,另一座查無 |
| 3 | **依標籤**:只選帶 `environment=canary` 的 member | 只有貼標籤的那座拿到 |

三個 CRP 都選同一個資源(整個 `workload-demo` namespace),只差 `policy` 一段:

```yaml
# PickAll:選所有健康且已 join 的 member
policy: { placementType: PickAll }

# PickN,N=1:由排程器挑 1 座
policy: { placementType: PickN, numberOfClusters: 1 }

# 依標籤:PickAll + clusterAffinity,只選 environment=canary
policy:
  placementType: PickAll
  affinity:
    clusterAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        clusterSelectorTerms:
          - labelSelector: { matchLabels: { environment: canary } }
```

!!! note "選一個 namespace,等於選它底下所有東西"
    CRP 選取的是叢集層級的資源;而**選一個 namespace 時,它底下所有物件(包括這裡的 `web` Deployment)會一起被散佈**([Day 0](sprint4-day0-multicluster-concepts.md) 查過的語意)。所以下面一律用「member 上有沒有 `web` Deployment」當作「資源到了沒」的判準。

## 開場先踩一顆:namespace 名字別撞到保留字

**第一次把要散佈的 namespace 取名 `fleet-demo`,CRP 直接排不出去。** `kubectl get crp` 只顯示 `SCHEDULED False`,一個資源都沒散出去,member 上查無 namespace。表格不會告訴你為什麼,真正的原因要挖 status:

```
kubectl get crp demo-pickall -o jsonpath='{.status}'
{
  "conditions": [{
    "type": "ClusterResourcePlacementScheduled",
    "status": "False",
    "reason": "InvalidResourceSelectors",
    "message": "... namespace fleet-demo is not allowed to propagate"
  }]
}
```

**根因**:hub-agent 帶了 guard-rail,會**拒絕散佈名字落在 KubeFleet 保留集合裡的 namespace**。`fleet-demo` 撞到 `fleet-` 這個保留前綴(KubeFleet 自己的系統 namespace 如 `fleet-system` 都用它),於是被擋。把 namespace 改成中性的 `workload-demo`,同一份 CRP 立刻排得出去。這是[地雷 #160](#mine-160),下面三個階段都是改名後的乾淨結果。

## 階段 1: PickAll——散到所有 member

```
### STAGE 1: PickAll ###
clusterresourceplacement.../demo-pickall created
  -> demo-pickall Available after 3 polls
  PickAll propagate time: 7s
```

```
NAME           GEN   SCHEDULED   AVAILABLE   AGE
demo-pickall   1     True        True        7s
```

**這是 gate 要求「可逐一解讀」的那六段 status**,全部 `True`:

```
ClusterResourcePlacementScheduled=True        (SchedulingPolicyFulfilled)
ClusterResourcePlacementRolloutStarted=True   (RolloutStarted)
ClusterResourcePlacementOverridden=True       (NoOverrideSpecified)
ClusterResourcePlacementWorkSynchronized=True (WorkSynchronized)
ClusterResourcePlacementApplied=True          (ApplySucceeded)
ClusterResourcePlacementAvailable=True        (ResourceAvailable)
```

逐段讀,這六段就是一份資源從 hub 到 member 的完整旅程:

| status 段 | 這一步做完了什麼 |
|---|---|
| `Scheduled` | 排程器已依 policy 決定要放哪些叢集(PickAll 選到了合格叢集) |
| `RolloutStarted` | rollout 控制器已依策略開始把資源推向被選叢集 |
| `Overridden` | override 規則已套用(這裡沒有 override,原樣) |
| `WorkSynchronized` | 已在各 member 產生要部署的 manifest |
| `Applied` | manifest 已實際套到 member |
| `Available` | 套上去的資源在 member 上已就緒可用 |

每座 member 各自也有一份同樣六段的 per-cluster status,兩座都全 `True`。**驗:兩座 member 都有 `web`:**

```
member-01 web deploy: PRESENT      member-02 web deploy: PRESENT
web    1/1     1            1                web    1/1     1            1
```

## 階段 2: PickN(N=1)——只散一座,證明另一座沒有

先刪掉上一個 CRP(資源從兩座 member 一起收走),再套 PickN:

```
### STAGE 2: PickN N=1 ###
  -> demo-pickn Available after 5 polls
  PickN propagate time: 13s
-- selected clusters --
kind-kf-member-02 | Scheduled=True
```

status 六段一樣全 `True`。排程器在沒有任何 affinity 提示下,自己挑了 member-02。**這就是 gate 的核心——恰好一座有、另一座沒有:**

```
member-01 web deploy: ABSENT
member-02 web deploy: PRESENT
```

`numberOfClusters: 1` 生效:只有被選中的 member-02 拿到 `web`,沒被選中的 member-01 查不到。**至於選中哪一座,是排程器的決定**(這次是 member-02);沒有 affinity 提示時,不保證是哪一座——gate 要的是「數目對、其餘沒有」,這點成立。

## 階段 3: 依標籤——只散到帶 canary 標籤的 member

先刪掉上一個 CRP,只給 **member-01** 貼上標籤,再套依標籤選的 CRP:

```
kubectl label membercluster kind-kf-member-01 environment=canary --overwrite
NAME                JOINED   ... ENVIRONMENT
kind-kf-member-01   True     ... canary
kind-kf-member-02   True     ...            ← 沒貼
```

```
  -> demo-bylabel Available after 3 polls
  by-label propagate time: 6s
-- selected clusters --
kind-kf-member-01 | Scheduled=True
```

**驗:只有帶標籤的 member-01 有,member-02 連 namespace 都沒有:**

```
member-01 (canary)    web deploy: PRESENT
member-02 (unlabeled) web deploy: ABSENT
# 直接查 member-02:
Error from server (NotFound): namespaces "workload-demo" not found
```

`clusterAffinity` 的 `matchLabels: {environment: canary}` 生效:CRP 只落在被貼標籤的叢集;沒貼的 member-02 甚至沒有 `workload-demo` 這個 namespace。**這正是 Day 0 說的「靠標籤與屬性做宣告式放置」——把 canary 標籤貼在哪座,工作負載就去哪座。**

## gate 判定:通過

| gate 要件 | 證據 | |
|---|---|---|
| CRP 把資源放到指定叢集 | PickAll→兩座皆有;PickN→member-02;依標籤→member-01 | ✅ |
| 未被選中的叢集查不到 | PickN:member-01 `ABSENT`;依標籤:member-02 連 namespace 都 `NotFound` | ✅ |
| placement status 每段可逐一解讀 | 六段 `Scheduled / RolloutStarted / Overridden / WorkSynchronized / Applied / Available` 逐字列出並對照解讀 | ✅ |

**gate:通過。** 三種選法各自的散佈時間都在 **6–13 秒**之間——CRP 的反應很快。

## 收工:整台 VM 刪除,兩天成本一次算清

Day 1 與 Day 2 共用同一台 `cnp-fleet-vm`,做完後整台刪除:

```
=== [1/5] delete VM ===        rc=0
=== [2/5] delete NIC ===       rc=0
=== [3/5] delete public IP === rc=0
=== [4/5] delete NSG ===       rc=0
=== [5/5] delete OS disk ===   rc=0
=== delete leftover VNet ===   rc=0
```

| 項目 | 數字 |
|---|---|
| VM 規格 / 單價 | `Standard_D4as_v5`,japaneast 隨用隨付 **US$0.224/hr**(≈ NT$7.2/hr) |
| VM 存活時長 | 約 **28 分鐘** |
| 兩天合計成本 | **約 NT$4** |

前半的 KubeFleet 課程,**一台 VM 裝下三座叢集、兩天連跑、做完整台清除,零殘留**。這是「本機零安裝」路線的完整樣貌:所有東西都在一台可拋棄的雲 VM 裡進出。

## 誠實的差距

- **#160 是第一次跑就踩到的**;階段 1–3 是改名 `workload-demo` 後重跑的乾淨結果,可重現。
- **PickN 選中哪一座由排程器決定**(這次 member-02),無 affinity 提示時不保證;gate 要的是「恰好 N 座、其餘沒有」,成立。
- **三個 CRP 都選整個 namespace**,沒有單獨驗「只選 namespace 內某一個物件」的細粒度選取——不在今天 gate 範圍。
- **沒有驗 member 離線 / 資源不足時 PickN 會不會改選**。今天兩座都健康,排程器的容錯行為沒觸發。

## 地雷記錄

### 地雷 #160:散佈用的 namespace 撞到 KubeFleet 保留前綴,CRP 卡在 `Scheduled=False`,真正原因只藏在 status 裡 {#mine-160}

**症狀**:CRP 建好後 `kubectl get crp` 顯示 `SCHEDULED False`,資源一個都沒散出去,member 上查無 namespace。表格不給任何原因。

**根因**:hub-agent 的 guard-rail 會拒絕散佈名字落在保留集合裡的 namespace。`fleet-demo` 撞到 `fleet-` 保留前綴(`kube-` 同理),被擋。真正的訊息 `namespace ... is not allowed to propagate` 藏在 `.status.conditions[].message`,不挖看不到。

**判斷準則**:CRP 排不出去,**先看 `.status.conditions` 的 `message`,不要只看表格的 `SCHEDULED False`**。

**修法**:散佈用的 namespace 別用 `fleet-` / `kube-` 開頭,改中性名(如 `workload-demo`)即解。

**為什麼收錄**:官方 quickstart 的範例 namespace 剛好不撞前綴,文件**從未說明** namespace 命名有保留字;而失敗時的關鍵訊息藏在 status 深處,表格只給一個沒頭沒尾的 `False`。

## 帶得走的東西

- **CRP 是 fleet 的「散佈遙控器」**:選什麼(resourceSelectors)、散到哪(policy)兩件事分開寫。同一份資源選取,只換 policy 就能從「散給全部」變成「只散一座」或「依標籤挑」。
- **選一個 namespace 就散整包**。要控制散佈的粒度,先想清楚 namespace 裡裝了什麼。
- **status 六段是一條可讀的旅程**,不是六個布林值。`Scheduled` 是「決定放哪」,`Applied` 是「真的套上去了」,`Available` 才是「就緒可用」——排不出去、推不動、套不上,分別卡在不同段,一看就知道問題在哪一步。
- **宣告式放置靠標籤驅動**:canary 標籤貼在哪座 member,工作負載就落在哪座。這是「藍綠 / 分批」這類做法在多叢集層的底層機制——但記得 [Day 0](sprint4-day0-multicluster-concepts.md#一多叢集是什麼問題) 說過,KubeFleet 給的是這個能力,不是替你保證合規邊界。
- **CRP 排不出去,先看 status 的 message**。表格上的 `SCHEDULED False` 只是結果,原因藏在 `.status.conditions` 裡。

## 延伸閱讀

- **[ClusterResourcePlacement 概念頁](https://kubefleet.dev/docs/concepts/crp/)** —— CRP 的 resourceSelectors / policy / strategy 三段結構,以及 PickAll / PickFixed / PickN 的官方定義,對照今天三種選法的實測。
- **[KubeFleet Placement 使用文件](https://kubefleet.dev/docs/how-tos/crp/)** —— 逐段解讀 placement status 的六個 condition(Scheduled → Available)的官方說明。
- **[clusterAffinity / label selector 文件](https://kubefleet.dev/docs/how-tos/affinity/)** —— 階段 3「依標籤選」用到的 `requiredDuringSchedulingIgnoredDuringExecution` 與 `matchLabels` 語法出處。

## 下一步

前半的「散得出去」到這裡驗完了:CRP 能把工作負載精準地放到你要的 member。**[Day 3](sprint4-day3-rollout-override.md) 要問的是「推得穩不穩、能不能差異化」**——同一個 CRP 加上 rollout 策略(分批推、出問題能停),再用 `ClusterResourceOverride` 讓不同叢集收到略有差異的設定。今天看到那個一直是 `NoOverrideSpecified` 的 `Overridden` 段,Day 3 會讓它變成非空。

Day 3 需要重建環境(本日已把 VM 刪掉),開頭會重跑一次 Day 1 的建叢集流程——**記得先調 `fs.inotify.*`**([地雷 #159](sprint4-day1-build-fleet.md#mine-159))。

---

!!! quote ""
    KubeFleet 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
