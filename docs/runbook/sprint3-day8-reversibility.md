# Day 8: 拆得掉嗎——能用的 SpinKube,一定拆不乾淨

![SpinKube 官方標誌](../assets/logos/spinkube-icon-color.svg){ align=right width="88" }

> [Day 1](sprint3-day1-three-generations.md) 存的節點基準,等的就是今天。把 SpinKube 全部拆掉,逐字比對——**而規則在 [Day 5](sprint3-day5-cost-measurement.md#mine-1) 就被改過了:整天不停機,一個 VM 生命週期內做完**,否則「比對相同」分不出是拆乾淨還是 VM 被換掉。

!!! abstract "你在課程的哪裡"
    - **Day 6–7**:SpinKube 裝好、跑通,節點上有 83.9 MiB 的 shim 與 6 行設定。
    - **今天**:由上而下逐層拆,每層比一次快照;主戲是兩個情境的解除安裝對照。
    - **Day 9**:把三條路線的判定收成決策表。

Day 6 讀 rcm 原始碼時預埋了一個問題:它的 `RemoveRuntime()` 是字串比對後整段刪除,而節點上那段設定**被手動修過 plugin domain**——字串已經對不上了。今天把這件事的兩面都採證。

## 今天要走的路

| 步驟 | 做什麼 |
|---|---|
| 1 | 解除安裝的觸發機制到底是什麼(假設錯了) |
| 2 | 情境 A:設定沒手改過,字串對得上 |
| 3 | 情境 B:手改過 domain——主戲 |
| 4 | 由上而下逐層拆,每層比一次 |
| 5 | 最終比對 + 手動清理清單 |

## 步驟 1: 觸發機制——原本的假設是錯的

直覺的假設是「移除 `spin=true` 標籤就會觸發解除安裝」。**錯,而且錯得有代價。**

移除標籤後觀察四分半鐘:`kubectl get shim` 掉到 `NODES 0`,**但沒有任何 uninstall Job,節點上的設定與二進位檔一動也沒動**。讀原始碼,整支 controller 只有一處會走到 `UNINSTALL`:

```go
// Shim has been requested for deletion, delete the child resources
if !shimResource.DeletionTimestamp.IsZero() {
    err := sr.handleDeleteShim(ctx, &shimResource, nodes)
    …
}
```

**唯一的入口是 Shim CR 的刪除。** 而 `handleDeleteShim` 迭代的是**刪除當下**符合 `nodeSelector` 的節點,還要求節點帶著 `<shim 名>` 標籤——兩個條件疊在一起。

### 順序錯了,節點被永久孤兒化

在標籤已移除的狀態下刪 Shim CR,1 秒完成、沒有 Job:

```console
$ kubectl -n rcm get jobs
No resources found                       ← install Job 隨 ownerReference 被 GC
$ kubectl get runtimeclass wasmtime-spin-v2
Error from server (NotFound)             ← RuntimeClass 也被 GC(Day 6 預告的行為,驗到了)
```

**叢集裡已經沒有任何物件記得這顆節點被改過,而節點上 83.9 MiB 的二進位檔與六行設定原封不動。** 之後不論怎麼操作 rcm 都沒有東西會回來清它——觸發清理的那個物件已經被刪掉了。這是[地雷 1](#mine-1)。

**拆除順序寫死:先刪 CR,再動標籤。** 反過來就是孤兒,而官方文件沒有寫刪除順序。

## 步驟 2: 情境 A——字串對得上的時候,它真的很乾淨

前置:設定是 rcm 自己寫的原字串(沒修 domain)。刪 Shim CR,**uninstall Job 立刻出現、5 秒 Complete**:

| 項目 | 結果 |
|---|---|
| `config.toml` | 1247 B → **999 B,逐字回到 Day 1 基準** |
| `/opt/rcm/bin/containerd-shim-spin-v2` | **已刪除** |
| `rcm-lock.json` | 168 B → 16 B(`{"shims": {}}`) |
| 節點標籤 `spin-v2` | 已移除 |
| RuntimeClass | 隨 ownerReference 被 GC |

三份關鍵快照對 Day 1 基準 **IDENTICAL**。留下的只有 `/opt/rcm/` 目錄殼與人自己貼的 `spin=true`。

**這是「乾淨」的基準線,也是情境 B 的對照。**

## 步驟 3: 情境 B——主戲,而失敗的方式比預期更安靜

重新佈建、照 [Day 6 地雷 1](sprint3-day6-spinkube-shim.md#mine-1) 的修法改 plugin domain、先跑一次 [Day 7 的最小 Deployment](sprint3-day7-spinkube-operator.md#gate-b) 確認**拆之前是好的**(沒有這步,「拆完不能跑」就沒有對照意義)。

途中先撞到一顆插隊的雷:**第一次刪 Shim CR,uninstall Job 根本沒跑**——controller log 明明寫 `Deploying uninstall-Job`,但那顆 `Complete 1/1` 的 Job 是情境 A 留下的舊物件(UID 沒變)。rcm 用 server-side apply 加固定 Job 名,同名舊 Job 還在、manifest 逐字相同,**apply 就是 no-op:不報錯、不建新物件、pod 不重跑**([地雷 3](#mine-3))。清掉舊 Job 重來。

第二次,真正的字串比對。Day 6 的預測是會看到 `runtime config does not exist, skipping`。**實測連 skipping 都沒有**——uninstall Job 的 log 只有兩行,退出碼 0:

```text
INFO uninstall called shim=spin-v2
INFO restarting containerd
```

而 `config.toml` **1253 B 進、1253 B 出,一個字都沒動**。原因在原始碼裡,守衛與比對用的不是同一個字串:

```go
// Warn if config.toml does not contain the runtimeName
if !strings.Contains(string(data), runtimeName) {          // guard: just "spin-v2"
    l.Warn("runtime config does not exist, skipping")
    return false, nil
}
cfg := generateConfig(shimPath, runtimeName, c.runtimeOptions, data)
modifiedData := strings.ReplaceAll(string(data), cfg, "")  // match: the whole block
err = afero.WriteFile(c.hostFs, c.configPath, []byte(modifiedData), 0o644)
…
return true, nil                                           // hardcoded
```

**守衛只找 `spin-v2` 五個字元**(手改過的區塊裡當然還有,通過);**比對用整段 `cfg`**(`generateConfig` 依 `version = 3` 判斷產出 1.x 版本,跟磁碟上的 2.x 版本對不上,`ReplaceAll` 一個字沒換);**回傳值寫死 `true`**,不檢查有沒有真的變。

**於是它把一份逐字相同的檔案寫回磁碟、重啟 containerd、回報成功。** 這是[地雷 4](#mine-4)。

### 殘留的後果:第三種失敗形狀

二進位檔**被刪掉了**(檔案清除走 `rcm-lock.json` 的絕對路徑,跟設定內容無關——兩條路徑、兩種成敗判準),設定卻留著。**節點繼續宣告 `spin-v2` 這個 handler,而它指向的檔案已經不存在。** 丟一顆 pod:

```console
day8-dead-handler-probe   0/1   ContainerCreating          ← 每 10–15 秒重試,永遠不會成功
Warning  FailedCreatePodSandBox  … failed to start shim: failed to resolve runtime path:
  invalid custom binary path: stat /opt/rcm/bin/containerd-shim-spin-v2: no such file or directory
```

到這裡,三條路線一共收齊了**三種失敗形狀**:

| 形狀 | 條件 | 樣子 |
|---|---|---|
| `Pending` | RuntimeClass 有 `scheduling`,沒有節點命中 | 排不上去 |
| `no runtime for "…" is configured` | 節點沒有這個 handler | 排得上,kubelet 拒絕 |
| **`invalid custom binary path`** | **handler 在、指向的檔案不在** | **排得上、handler 查得到,containerd 起 shim 時才炸** |

**第三種最難追**:`kubectl get runtimeclass`、`describe node` 的 `runtimeHandlers`、`containerd config dump` **三個地方全都說沒問題**,只有真的去 `ls` 那個路徑才看得出來。

## 步驟 4: 逐層拆——只有四層會碰節點

十三個快照時點,七份取樣逐份比。結論濃縮成一句:

**只有 install Job、手改 domain、uninstall Job、手動清理四層會碰節點。** spin-operator、cert-manager、helm chart、CRD、namespace——裝與拆都完全不碰節點,七份取樣逐字 IDENTICAL。

拆 spin-operator 時撞到另一顆順序雷:**先 `helm uninstall` 了 operator,兩顆 `SpinAppExecutor` 就再也刪不掉**——它們帶著 `core.spinkube.dev/finalizer`,而處理 finalizer 的 controller 剛剛被刪了。`kubectl delete` 掛住 **6 分 40 秒**(預設 `--wait=true`,在等一個永遠不會消失的物件),只能 patch 掉 finalizer([地雷 2](#mine-2))。

## 步驟 5: 最終比對與手動清理清單

手動收尾五步:

```bash
# 1. cut the RCM block out of config.toml (uninstall left it behind)
sed -i '/^# RCM runtime config for spin-v2$/,$d' /etc/containerd/config.toml
# 2. remove the backup left by the domain fix — rcm never knew it existed
rm -f /etc/containerd/config.toml.rcm-1x-backup
# 3. the directory shell survives even scenario A
rm -rf /opt/rcm
# 4. without this, runtimeHandlers keeps advertising spin-v2
systemctl restart containerd
# 5. the label you applied yourself — rcm only cleans its own
kubectl label node --all spin-
```

**五步的共通點:沒有一步是 rcm 會幫你做的**,而且第 1、2、4 步在情境 A 裡並不需要——「要不要收尾」取決於有沒有人手改過那個檔案,**而這件事沒有任何指令查得出來**(`kubectl get shim` 在兩種情況下都是 `READY 1/1`)。

最終比對:

```text
day8-final 對 Day 1 基準:
IDENTICAL  containerd-config.toml
IDENTICAL  containerd-config-dump.toml
IDENTICAL  node-runtimehandlers.json

day8-final 對 day8-s0(同一顆 VM、七份全比):
五份 IDENTICAL;唯二 diff = 檔案 mtime + 取樣器的空白壓縮差異
```

**全程同一個 VM 生命週期,沒有中途停機,所以「相同」只有「拆乾淨」一種解釋。** 順帶一個誠實的註記:`config.toml` 被寫過三次,內容回到原樣但 **mtime 回不去**——內容可逆,時間戳不可逆,這是這條路線可逆性的實質上限。

## 驗收 checkpoint

| 要求 | 判定 | 說明 |
|---|---|---|
| 移除全部 SpinKube 元件後,節點與 Day 1 基準逐字比對 | **通過,但要加一個條件** | 官方路徑拆完**不足以**回到基準(殘留六行設定、死 handler、`/opt/rcm/`);**加五步手動收尾後**三份關鍵快照逐字相同 |
| 比對的解釋唯一性 | 達成 | 同一 VM 生命週期,中途未停機 |
| 兩個情境的對照 | 達成 | A:5 秒逐字還原;B:4 秒「成功」,設定一字未動 |

!!! danger "這一天最重要的一句話"
    **在 AKS 上,能用的 SpinKube 一定拆不乾淨。** 不修 plugin domain 就沒有 handler、跑不了 wasm(Day 6);修了 domain,自動化解除安裝就必然失效(今天)。**兩件事在文件上完全沒有連結,而它們是同一個字串比對的兩面。**

## 誠實的差距

- **`RemoveRuntime` 的失效只驗了「手改 domain」這一種偏離。** 改路徑、改選項值、插註解,從原始碼看都會落到同一個 `ReplaceAll` 不命中,但沒有實測。
- **沒有量解除安裝對執行中 pod 的影響。** 拆之前刻意先刪光了測試 pod——如果拆時還有 Spin pod 在跑,containerd 重啟會怎樣、pod 會不會被殺,**完全沒驗,而那在正式環境是最重要的一格。**
- **手動清理五步不是上游文件提供的,是本章實測歸納出來的**(rcm、spin-operator、SpinKube 官網都沒有解除安裝後的收尾章節),只在今天這顆節點上驗過。
- **只有單節點。** #151 在多節點上會更難發現——移除一顆節點的標籤,`kubectl get shim` 只是 `NODES` 少 1,完全不像有問題。
- **`/opt/rcm/` 目錄殼連情境 A 也清不掉,沒有查為什麼**;留著空 lock 檔會不會影響下次安裝,沒測。
- **四條上游 bug 仍未回報**(#148/#151/#153/#154),持續累積的欠債。
- 開機時間 3 分 45 秒對 Day 7 的 7 分 51 秒,差一倍,無法歸因——**「開機大約要幾分鐘」這種數字寫不出來,不硬給**。

## 地雷記錄

### 地雷 1:先移標籤再刪 Shim CR,節點被永久孤兒化 {#mine-1}

**症狀**:移標籤後 `kubectl get shim` 掉到 `NODES 0`,看起來像「這顆節點不歸我管了」。再刪 Shim CR:1 秒完成、沒有 Job、install Job 與 RuntimeClass 隨 GC 消失——**叢集不留任何痕跡,節點上 83.9 MiB 與六行設定原封不動**。

**根因**:唯一的 uninstall 入口是 `DeletionTimestamp`;而 `handleDeleteShim` 收到的節點清單是**刪除當下**符合 `nodeSelector` 的——標籤先移掉,清單就是空的,for 迴圈一圈都不跑。

**判斷準則**:**刪除順序寫死——先刪 CR,再動標籤。** 反過來就是孤兒,而且沒有任何 CR 可以再觸發清理,只能手動清或等 VM 揮發。官方文件沒有寫刪除順序,`NODES 0` 也完全不暗示節點還髒著。

### 地雷 2:先 `helm uninstall` operator,`SpinAppExecutor` 就再也刪不掉 {#mine-2}

**症狀**:`helm uninstall spin-operator` 2 秒成功;接著刪 `SpinAppExecutor`,`kubectl delete` **直接掛住不返回**,等了 6 分 40 秒。

**根因**:物件帶 `core.spinkube.dev/finalizer`,而處理它的 controller 剛剛被 uninstall 掉了。沒有 controller,finalizer 永遠不會被移除。

**繞法**:`kubectl patch … --type=merge -p '{"metadata":{"finalizers":null}}'`。

**判斷準則**:**帶 finalizer 的 CR,要在它的 controller 還活著時刪。拆除順序一律由上而下:先 CR、再 controller、最後 CRD。** 這條對所有 operator 都成立,但 SpinKube 只給安裝順序、沒給拆除順序。另外:`kubectl delete` 掛住時看不出是慢還是永遠不會好——**先用 `--timeout=60s`,超時再查 finalizer**。

### 地雷 3:uninstall Job 名字固定 + server-side apply,同名舊 Job 還在就整個 no-op {#mine-3}

**症狀**:刪 Shim CR,controller log 寫 `Deploying uninstall-Job`,Shim 正常消失、RuntimeClass 正常 GC、`get jobs` 也有一顆 `Complete 1/1`——**但節點什麼都沒清**。那顆 Job 是 12 分鐘前的舊物件,UID 沒變。

**根因**:`deployJobOnNode` 用 server-side apply(`Force: true`)而不是 `Create`,Job 名 `<節點名>-<shim 名>-uninstall` 不帶亂數。同名舊 Job 在、manifest 逐字相同 → no-op,`Patch` 回傳 nil,上層判定成功。而 rcm **只給 install Job 設 ownerReference**,uninstall Job 沒有 owner、不隨任何東西消失,chart 也沒設 TTL——**「同一顆節點做第二次解除安裝必定 no-op」是寫死的,不是機率**。

**判斷準則**:**解除安裝前先 `kubectl -n rcm delete job --all`;驗證是否真的跑過,比對 Job 的 UID 或 `completionTime`,不能只看 `Complete 1/1`。**

### 地雷 4:手改過 domain 之後,解除安裝回報成功、重啟 containerd、設定原封不動 {#mine-4}

**預期**(Day 6 讀原始碼寫下的):會看到 `runtime config does not exist, skipping`。

**實測**:**沒有那行警告。** Job 兩行 log、退出碼 0;`config.toml` 1253 B 進 1253 B 出;dump 命中 3;`runtimeHandlers` 仍有 `spin-v2`;而 shim 二進位檔**被刪掉了**。

**根因**:守衛與比對用的不是同一個字串——守衛 `Contains("spin-v2")` 通過,比對用 `generateConfig` 產出的整段(1.x domain)對不上磁碟(2.x domain),`ReplaceAll` 零置換;**回傳值寫死 `true`**,上層跳過 `nothing changed` 分支,重啟、回報成功。

**後果**:節點宣告一個指向已刪檔案的 handler——第三種失敗形狀,三個查詢介面都說沒問題。

**判斷準則**:**解除安裝之後不要相信 Job 的退出碼。量兩件事:`containerd config dump | grep -c <handler>` 必須是 0,`runtime_type` 指向的路徑必須不存在。** 只有前者是 3、後者不存在,就是這顆雷。

**這也是 Day 6 那個修法的隱藏代價**:修 domain 是唯一能讓 SpinKube 在 AKS 跑起來的做法,而它同時讓自動化解除安裝失效。

### 地雷 5:能同時緩解兩顆雷的開關存在、預設關著、沒有任何文件提 {#mine-5}

孤兒 Job 在三天內造成兩種失效([Day 7 地雷 1](sprint3-day7-spinkube-operator.md#mine-1) 的 panic 迴圈、本章[地雷 3](#mine-3) 的 no-op)。緩解的開關其實早就有:helm value **`rcm.nodeInstallerJob.ttl`**,預設 `0`——而 `0` 走不進 `ttl > 0` 的條件,等於預設關著,文件也沒提。

**判斷準則**:裝 rcm 一律帶 `--set rcm.nodeInstallerJob.ttl=<秒>`。它緩解 install Job 的孤兒問題;刪除順序與手改設定那兩顆雷它管不到,那些沒有開關可繞。(依 values 與原始碼判定,效果未在叢集上復現。)

## 這條路線收尾:SpinKube 適合誰

三天實測走完,配上 [Day 0 那五條](sprint3-day0-wasm-concepts.md#health-method)的查證(2026-08-11):

**體質要分層講**。上層的 `spin` CLI 健康(近 13 週 219 個 commit、機器人 3%);**中間那層——shim、rcm、operator 三個元件——default branch 最近 7–8 週全是零**,唯一在動的是 dependabot(三個 repo 41 個 open PR,27 個是它開的)。這三天踩到的四顆確定性 bug,上游三顆零回報、一顆開了 27 個月沒人跟進——全公開 GitHub 上寫過 rcm `Shim` CR 的檔案只有 7 份(對照 SpinApp 的 245 份),**那條解除安裝路徑很少有人走第二次,bug 才安靜得下來**。

**適合**:wasm 工作負載必須長得像一般 Kubernetes 工作負載——三條裡唯一做得到,而且這個優勢是結構性的:執行層是 containerd(Graduated)加一支 shim,operator 與 rcm 都只是便利層,Day 7 證明拿掉照跑。**只用 RuntimeClass + shim 兩層、把 rcm 與 operator 當可選件**,是最穩的用法。

**不適合**:沒有人能負責節點層 runbook 的團隊。在 AKS 上它不開箱可用(要手改設定),而手改過就拆不乾淨(今天的主戲)——節點清理是你自己的工作,上面那五步是起點。

## 帶得走的東西

- **「拆乾淨」要驗,而且要在對照組成立的條件下驗。** 今天的比對之所以只有一種解釋,是因為整天沒停機;情境 B 之所以有意義,是因為先有情境 A 定義了「乾淨」長什麼樣。
- **自動化的解除安裝比安裝脆弱得多。** 安裝面對的是已知狀態(乾淨節點),解除安裝面對的是未知狀態(可能被手改過)。四個前提缺一不可,而其中一個(有沒有人改過檔案)**沒有任何指令查得出來**。
- **退出碼 0 與 `Complete 1/1` 只證明行程正常結束,不證明它做了你以為的事。** 今天兩顆雷(#153、#154)的共通形狀:每一層都回報成功,而動作本身是 no-op。
- **finalizer 是一份契約,拆除順序是它的履約條件。** 由上而下:CR → controller → CRD。順序反了,掛住的不是指令,是物件的一生。
- **內容可逆,時間戳不可逆。** mtime 是這條路線上唯一無法還原的東西——而它剛好也是「這顆節點被動過」唯一剩下的證據。

## 延伸閱讀

想往下深挖,從這幾份開始:

- **[Kubernetes:Finalizers](https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/)** —— 地雷 2 的機制。「The target object remains in a terminating state while the control plane, or other components, take the actions defined by the finalizers」——那個 "other components" 被你 uninstall 掉時會怎樣,文件沒說,今天量了。
- **[Kubernetes:Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)** —— 地雷 3 的機制。[Day 3](sprint3-day3-wasmcloud-distributed.md#mine-4) 撞過它的欄位所有權,今天撞的是它的冪等性:對一個已存在且相同的物件 apply,就是什麼都不做。
- **[runtime-class-manager](https://github.com/spinframework/runtime-class-manager)** —— `internal/containerd/configure.go` 的 `RemoveRuntime()` 不到三十行,守衛、比對、寫死的回傳值都在裡面,值得對照地雷 4 逐行讀。

## 下一步

三條路線的動手實測到今天全部結束,而每一條的判定都已經在它收尾那天給了:wasmCloud 在 [Day 3](sprint3-day3-wasmcloud-distributed.md)、WasmEdge 在 [Day 4](sprint3-day4-wasmedge.md)、SpinKube 在本章上一節。

[Day 9](sprint3-day9-decision-matrix.md) 把三份判定收成一張決策表——每一格標明是實測、查證還是推論,連同這個 sprint 的總帳。

---

!!! quote ""
    SpinKube 標誌為 CNCF(Linux Foundation)官方資產,此處作社群教學用途。
