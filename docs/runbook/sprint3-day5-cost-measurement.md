# Day 5: 量成本——冷啟動的差異量不出來,而記憶體的差異方向跟宣傳相反

![WebAssembly 官方標誌](../assets/logos/webassembly-icon-color.svg){ align=right width="95" }

> 前四天刻意一個比較宣稱都沒做,全部留到今天。這一章的主角不是三欄數字,是**怎麼判斷一個數字算不算數**——包括一個帶信賴區間的否定結論,以及一欄交白卷。

!!! abstract "你在課程的哪裡"
    - **Day 2–4**:三條路線各自跑起來了,每一章都標明「這個數字只跑一次、不作比較宣稱」。
    - **今天**:把那些註記兌現。同一份原始碼編三個目標,在同一顆節點上量。
    - **Day 6**:第三條路線 SpinKube,那一天會真的派 operator 去改節點。

## 今天要走的路

| 步驟 | 做什麼 |
|---|---|
| 1 | 驗證:節點層改動撐不過 VM 汰換 |
| 2 | 一份原始碼,三個目標 |
| 3 | 方法學試跑——先量地板 |
| 4 | 冷啟動,兩批獨立的 n=50 |
| 5 | 記憶體 |

## 步驟 1: 先驗一個性質——節點層改動撐不過 VM 汰換

[Day 4](sprint3-day4-wasmedge.md) 結束時,節點上還留著 wasmedge 的 shim 與那 8 行設定。**如果「VM 汰換會抹掉節點層的手動改動」這個性質成立,它們現在應該已經不在了**——先驗這件事,Day 8 的可逆性驗收要靠它。

還沒動任何東西時的快照,對照 Day 1 基準:

```console
$ diff <Day1 基準>/baseline-containerd-config.toml day5-s0-containerd-config.toml
（無輸出,exit=0）                              ← 與 Day 1 基準逐字相同

$ cat day5-s0-shim-binaries.txt
-rwxr-xr-x 1 root root  8805376 /usr/bin/containerd-shim-runc-v2
-rwxr-xr-x 1 root root 10015640 /usr/bin/runc
                                               ← 那支 109 MiB 的 shim 不見了

$ kubectl get node -o jsonpath='{…runtimeHandlers}'
['', 'runc', 'untrusted']                      ← wasmedge 不在了
```

**成立。** Day 4 加的 8 行設定、109 MiB 的 shim、`runtimeHandlers` 裡的 `wasmedge`,全部隨 VM 消失。

### 但 RuntimeClass 留下來了

```console
$ kubectl get runtimeclass
kata-vm-isolation   kata
runc                runc
wasm-day1-runc      runc
wasm-day1-untrusted untrusted
wasmedge            wasmedge          ← 還在
wasmtime-spin-v2    wasmtime-spin-v2
```

RuntimeClass 住在 etcd,不隨 VM 生命週期消失。**於是叢集重啟後的那一刻,它處於一個特定狀態:`wasmedge` 這個名字在,而節點上沒有任何東西實作它。**

跟 [Day 1 地雷 2](sprint3-day1-three-generations.md#mine-2) 那個出廠就壞掉的 `kata-vm-isolation` 變成同一類——差別只在這次是自己造成的。記成[地雷 1](#mine-1)。

!!! warning "這件事改變了 Day 8 的驗收設計"
    Day 8 原訂「移除全部 SpinKube 元件之後,節點的 containerd 設定與 Day 1 基準逐字比對」。前提成立,**但今天的結果同時暴露一個弱點**:

    節點層改動本來就撐不過一次停機,所以「比對結果相同」有兩種解釋——**解除安裝乾淨,或 VM 被換掉了**。兩者在 diff 上長得一模一樣。

    **Day 8 要驗的是前者,所以那天的解除安裝與比對必須在同一個 VM 生命週期內完成,中途不能停機。**

因為這個結果,今天要量 wasmedge 就得先重裝一次。重裝一次就回到 Day 4 的狀態——**順帶證明 Day 4 的安裝程序是可重現的。**

## 步驟 2: 一份原始碼,三個目標

[Day 4](sprint3-day4-wasmedge.md#gate) 證明了跨執行期的「同一支程式」不存在——wasmCloud 吃 component、wasmedge shim 只吃 core module,差在第 5 個位元組。**所以今天要自己編。**

```bash
# one crate, one release profile, three target triples
rustup target add wasm32-wasip1 wasm32-wasip2 x86_64-unknown-linux-musl
for T in wasm32-wasip1 wasm32-wasip2 x86_64-unknown-linux-musl; do
  cargo build --release --target "$T"
done
```

```toml
[profile.release]
opt-level = "s"
lto = true
strip = true
panic = "abort"
```

**三者唯一的差別是 target triple。** 程式本身刻意做兩件事:一次性模式印一行就結束(冷啟動取樣量的就是這個),`stay <secs>` 模式常駐(記憶體從 cgroup 讀出來,不用猜)。

打包成四個 OCI reference:

| 後綴 | 內容 | 給誰 |
|---|---|---|
| `-wasm` | core module,空 base,tar rootfs | wasmedge shim |
| `-native-scratch` | 原生 ELF,空 base,tar rootfs | runc,**無 base image** |
| `-native-debian` | 原生 ELF 疊在 `debian:12-slim` 上 | runc,**有 base image** |
| `-comp` | component,`application/wasm` artifact | wasmCloud |

`-wasm` 與 `-native-scratch` **是逐字相同的打包配方**——那是映像大小唯一有可比性的前提。

## 步驟 3: 先量地板

量測在**節點上、用 `ctr`** 進行,把 kubelet、scheduler、API server 的來回排除在外。四個設計要點:

- **`harness` 是 null control**:同一支 `ctr` 二進位檔做一次 no-op RPC。**兩個 runtime 之間的差異必須大於這個地板加上樣本離散度,否則不算結果。**
- 每個 runtime 先跑一次暖機,第一個樣本不付 page cache 的錢。
- 兩個 runtime **交錯**取樣,抵銷時間漂移。
- 統計用**中位數與 IQR**,不用平均與標準差——樣本有硬地板與長右尾,平均數不是中間。

```text
harness   n=50   median 16.0 ms   IQR 1.0   (min 15.0, max 30.0)
```

**16 毫秒,IQR 只有 1 毫秒。** 量具本身的雜訊小到可以忽略,所以後面量到的離散度是被測物的。

## 步驟 4: 冷啟動——兩批獨立的 n=50

```text
=== 批次 A
label       n     min     p25  median     p75     max     IQR
harness    50    15.0    16.0    16.0    17.0    30.0     1.0
wasmedge   50   147.0   156.0   167.0   178.5   197.0    22.5
runc       50   153.0   159.5   173.5   185.5   279.0    26.0

=== 批次 B
harness    50    15.0    16.0    16.0    17.0    28.0     1.0
wasmedge   50   147.0   154.0   161.0   169.0   196.0    15.0
runc       50   148.0   165.0   171.5   181.8   255.0    16.8
```

用 bootstrap(10000 次重抽,固定亂數種子)算中位數差的 95% 信賴區間:

```text
=== 批次 A:wasmedge vs runc
observed difference = -6.5 ms
bootstrap 95% CI    = [-16.0, +1.0] ms
verdict: NOT MEASURABLE (CI covers 0)

=== 批次 B:wasmedge vs runc
observed difference = -10.5 ms
bootstrap 95% CI    = [-16.5, -5.0] ms
verdict: measurable
```

**兩批獨立取樣的判定不一致。** 批次 A 說量不出來,批次 B 說量得出來——但只差 10.5 毫秒,而 CI 下緣只到 -5。

先把五組中位數畫在同一把尺上——**wasmedge 與 runc 幾乎貼在一起,而兩者都離地板很遠**:

<svg viewBox="0 0 660 210" role="img" aria-label="冷啟動中位數(毫秒,每批 n=50)" style="font-family: var(--md-text-font-family, sans-serif)" width="100%" style="max-width:660px; font-family: var(--md-text-font-family, sans-serif);">
<text x="0" y="18" font-size="15" font-weight="600" fill="var(--md-default-fg-color)">冷啟動中位數(毫秒,每批 n=50)</text>
<text x="160" y="50" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">地板(空指令)</text>
<rect x="170" y="34" width="33" height="22" rx="2" fill="#9aa0a6"/>
<text x="211" y="50" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">16</text>
<text x="160" y="84" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">wasmedge 批次A</text>
<rect x="170" y="68" width="342" height="22" rx="2" fill="#e8590c"/>
<text x="520" y="84" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">167</text>
<text x="160" y="118" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">runc 批次A</text>
<rect x="170" y="102" width="356" height="22" rx="2" fill="#4c7bd9"/>
<text x="534" y="118" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">173.5</text>
<text x="160" y="152" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">wasmedge 批次B</text>
<rect x="170" y="136" width="330" height="22" rx="2" fill="#e8590c"/>
<text x="508" y="152" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">161</text>
<text x="160" y="186" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">runc 批次B</text>
<rect x="170" y="170" width="352" height="22" rx="2" fill="#4c7bd9"/>
<text x="530" y="186" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">171.5</text>
</svg>


對照兩者與地板的距離,那個就非常清楚:

```text
wasmedge vs harness   批次 A CI = [+144.0, +157.0] ms   measurable
                      批次 B CI = [+141.0, +149.5] ms   measurable
```

### 判定

!!! note "能說什麼、不能說什麼"
    **不能說**:誰的冷啟動比較快。在這個量測平面、這個工作負載、n=50 兩批的條件下,**wasmedge 與 runc 的差異落在雜訊邊緣。**

    **可以說**:兩者都在 **160–175 毫秒**這個量級,而**那 150 毫秒的絕大部分是容器管線本身**(`ctr` → containerd → shim → cgroup 建立),不是執行期在跑 wasm 或跑原生碼。

這一條直接牴觸「wasm 冷啟動比容器快一個數量級」這類說法。**至少在「用 containerd 當容器跑」這條路上,那個數量級不存在——因為省不掉的是管線,不是執行期。**

**這不代表 wasm 的快啟動是假的。** 它代表的是:**那個優勢發生在別的地方**(例如一個行程內載入上千個實例、或 FaaS 的 per-request 啟動),而**不會出現在「把 wasm 當成 Pod 來跑」這條路上**。要拿到那個優勢,得用 Day 2 那種 host 模型,而 host 模型的代價 Day 2 已經量過了。

## 步驟 5: 記憶體——差異方向跟宣傳相反

### shim 的常駐與邊際成本

用 PSS 不用 RSS(shim 的 forked 行程之間共享頁面,RSS 會重複計算),取 1／2／3 個並行容器:

```text
wasmedge n=1 shim_pss_KiB=49235  shim_procs=4
wasmedge n=2 shim_pss_KiB=58774  shim_procs=8
wasmedge n=3 shim_pss_KiB=68210  shim_procs=12
runc     n=1 shim_pss_KiB=4709   shim_procs=1
runc     n=2 shim_pss_KiB=9904   shim_procs=2
runc     n=3 shim_pss_KiB=13885  shim_procs=3
```

線性擬合:

| | 常駐 | 每多一個容器 | 每個容器的 shim 行程數 |
|---|---|---|---|
| **wasmedge** | **38.8 MiB** | **+9.3 MiB** | **4** |
| **runc** | 0.1 MiB | +4.5 MiB | 1 |

畫出來一眼就看到兩件事:**wasmedge 起點就高(常駐成本),而且每多一個容器,跟 runc 的差距愈拉愈開**:

<svg viewBox="0 0 660 235" role="img" aria-label="shim 記憶體對並行容器數" width="100%" style="max-width:660px; font-family: var(--md-text-font-family, sans-serif);">
<text x="0" y="18" font-size="15" font-weight="600" fill="var(--md-default-fg-color)">shim 記憶體 PSS(MiB)對並行容器數</text>
<rect x="0" y="30" width="12" height="12" rx="2" fill="#e8590c"/><text x="18" y="41" font-size="12" fill="var(--md-default-fg-color)">wasmedge</text><rect x="106" y="30" width="12" height="12" rx="2" fill="#4c7bd9"/><text x="124" y="41" font-size="12" fill="var(--md-default-fg-color)">runc</text>
<text x="100" y="80" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">1 個容器</text>
<rect x="110" y="56" width="316" height="18" rx="2" fill="#e8590c"/>
<text x="434" y="70" font-size="12.5" font-weight="600" fill="var(--md-default-fg-color)">48.1</text>
<rect x="110" y="79" width="30" height="18" rx="2" fill="#4c7bd9"/>
<text x="148" y="93" font-size="12.5" font-weight="600" fill="var(--md-default-fg-color)">4.6</text>
<text x="100" y="139" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">2 個容器</text>
<rect x="110" y="115" width="377" height="18" rx="2" fill="#e8590c"/>
<text x="495" y="129" font-size="12.5" font-weight="600" fill="var(--md-default-fg-color)">57.4</text>
<rect x="110" y="138" width="64" height="18" rx="2" fill="#4c7bd9"/>
<text x="182" y="152" font-size="12.5" font-weight="600" fill="var(--md-default-fg-color)">9.7</text>
<text x="100" y="198" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">3 個容器</text>
<rect x="110" y="174" width="438" height="18" rx="2" fill="#e8590c"/>
<text x="556" y="188" font-size="12.5" font-weight="600" fill="var(--md-default-fg-color)">66.6</text>
<rect x="110" y="197" width="89" height="18" rx="2" fill="#4c7bd9"/>
<text x="207" y="211" font-size="12.5" font-weight="600" fill="var(--md-default-fg-color)">13.6</text>
</svg>

**兩個結構性差異**:wasmedge 有 38.8 MiB 的常駐成本(整個 WasmEdge 執行期靜態連結在 shim 裡),而且**每個 wasm 容器開 4 個 shim 行程,runc 開 1 個**。記成[地雷 2](#mine-2)。

### 容器自身的 cgroup

同一份原始碼,`stay` 模式常駐時讀 cgroup:

```text
--- wasmedge
    memory.current = 1601536 bytes   (1.53 MiB)
    PID   RSS COMMAND
  61034 15064 youki:[2:INIT]
--- runc
    memory.current =  294912 bytes   (0.28 MiB)
    PID   RSS COMMAND
  61077   316 payload
```

**wasm 版的常駐記憶體是原生版的 5.4 倍。**

(順帶一個實作細節:wasm 容器裡的行程叫 `youki:[2:INIT]`——runwasi 內部用 youki 這個 Rust 的 OCI 執行期函式庫。)

### 磁碟

```text
/usr/bin/containerd-shim-runc-v2              8.4 MiB
/usr/local/bin/containerd-shim-wasmedge-v1  109.2 MiB    (13.0 倍)
```

<svg viewBox="0 0 660 108" role="img" aria-label="shim 二進位檔大小(MiB)" style="font-family: var(--md-text-font-family, sans-serif)" width="100%" style="max-width:660px; font-family: var(--md-text-font-family, sans-serif);">
<text x="0" y="18" font-size="15" font-weight="600" fill="var(--md-default-fg-color)">shim 二進位檔大小(MiB)</text>
<text x="160" y="50" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">runc 的 shim</text>
<rect x="170" y="34" width="30" height="22" rx="2" fill="#4c7bd9"/>
<text x="208" y="50" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">8.4</text>
<text x="160" y="84" font-size="13" text-anchor="end" fill="var(--md-default-fg-color)">wasmedge 的 shim</text>
<rect x="170" y="68" width="389" height="22" rx="2" fill="#e8590c"/>
<text x="567" y="84" font-size="13" font-weight="600" fill="var(--md-default-fg-color)">109.2</text>
</svg>


### wasmCloud 那一側:一個量不到的格子

想在**乾淨的 host** 上量單一元件的記憶體(避開 [Day 2](sprint3-day2-wasmcloud.md) 那個「卸載元件不會把記憶體還給 host 行程」的陷阱),做法是刪掉一顆 host pod 讓它重建:

```text
new host pod: hostgroup-default-…
t+4s   working_set_bytes=4800512  workloads_started=0
…
t+160s working_set_bytes=4837376  workloads_started=0
```

**160 秒內 `workloads_started` 一直是 0。**

**這個量測失敗了,而失敗的原因是 [Day 3 的發現](sprint3-day3-wasmcloud-distributed.md)**:wasmCloud 不重新平衡,新加入的 host 不會分到既有的 workload。所以「乾淨 host + 一個元件」這個組合沒拿到。記成[地雷 3](#mine-3)。

拿到的只有兩端:空 host 4.80 MB、帶 6 個 workload 的 host 26.65 MB。**那 6 個是 blobby 與 hello-world 混在一起,不能相除。**

### wasmCloud 的啟動時間不能跟前兩者比

```text
wasmcloud_ready  n=20  min 1422  p25 1979.5  median 2302  p75 5049.2  max 12699  IQR 3069.8
```

中位數 2.3 秒,**IQR 3.07 秒——離散度比中位數還大**。

**但這個數字不能跟步驟 4 的 160–175 毫秒放在同一欄。** 兩者量的是不同的東西:

| | 量什麼 | 包含 |
|---|---|---|
| 步驟 4 | `ctr` 層 | containerd → shim → cgroup |
| wasmCloud | 端到端 | API server → operator 對帳 → NATS → host 拉取 → 啟動 → EndpointSlice → kube-proxy |

**要公平比,得讓 wasmedge 也走一次完整的 Pod → kubelet → CRI 路徑,今天沒做。**

## 驗收 checkpoint

| 要求 | 判定 | 說明 |
|---|---|---|
| **同一支程式** | **達成** | 一份原始碼、同一份 release profile,只換 target triple。**同時補掉 Day 4 誠實的差距第 5 條** |
| 同一顆節點 | 達成 | 全部在同一顆 `Standard_D2as_v5` 上 |
| **冷啟動** | **部分達成** | wasmedge 與 runc 有 2×n=50 的乾淨數據,**結論是「差異落在雜訊邊緣、不能排名」**;wasmCloud 量測平面不同,不可比 |
| **記憶體足跡** | **部分達成** | shim 常駐與邊際、容器 cgroup 都有;**wasmCloud 的「每個元件多少」沒拿到** |
| **映像大小** | **未達成** | 打包配方設計完整,**但成品大小沒有落地** |
| **方法學限制寫清楚** | 達成 | null control、bootstrap CI、兩批獨立重複、判定不一致如實記錄 |

**整體:部分通過。** 最有價值的產出不是三欄數字,是**那個帶信賴區間的否定結論**,以及記憶體的結構性差異。

## 誠實的差距

- **映像大小整欄沒有數據。** 打包配方設計完整(含／不含 base image 兩版),但成品大小只印在建置 pod 的 stdout,沒有落地。**這一欄要補做才算完整。**
- **wasmCloud 沒有可比的冷啟動數字。** 只有端到端的 2.3 秒,而那條路徑包含整個 Kubernetes 控制平面。
- **wasmCloud 的「每個元件多少記憶體」沒拿到。**
- **冷啟動兩批判定不一致,沒有跑第三批。** 要定論需要更大的 n 或更多批次,所以結論只能是「落在雜訊邊緣」。
- **容器對照組只用了 scratch base。** `-native-debian` 打包了但沒拿來量,所以「跟一般應用容器比」這個更貼近現實的對照沒有。**這一點對地雷 2 的適用範圍很重要。**
- **只有單節點、單一工作負載。** 那支 payload 印一行字就結束,沒有 I/O、沒有網路、沒有大量記憶體配置——**結論不能外推到真實工作負載。**
- **Day 4 的 `use_local_image_pull = true` 副作用仍未評估。**

## 地雷記錄

### 地雷 1:停機抹掉節點層改動,而 RuntimeClass 留在 etcd {#mine-1}

**症狀**:Day 4 裝好、驗過會動的 `wasmedge` RuntimeClass,節點被汰換一次之後(本例:叢集停機重啟)`kubectl get runtimeclass` 照樣列得出來,但任何指名它的 pod 都會卡在 `ContainerCreating`,訊息 `no runtime for "wasmedge" is configured`。

**根因**:兩層的生命週期不同。RuntimeClass 是 etcd 裡的物件,跨 VM 生命週期存活;handler 的實作(`config.toml` 那幾行加磁碟上的 shim)寫在 VM 的作業系統磁碟上,換執行個體就沒了。**而這兩層之間沒有人負責校驗。**

**判斷準則**:**任何節點層的手動改動,都要假設它撐不過一次 VM 汰換——停機重啟、節點映像升級、spot 回收都算。** 判斷 handler 在不在,唯一可靠的來源是 `kubectl get node -o jsonpath='{…status.runtimeHandlers}'`,**不是 `kubectl get runtimeclass`**。

**對 Day 8 的影響**:「解除安裝之後節點與基準相同」有兩種解釋。**Day 8 的解除安裝與比對必須在同一個 VM 生命週期內完成。**

### 地雷 2:每個 wasm 容器要開 4 個 shim 行程,而 runc 是 1 個 {#mine-2}

**症狀**:節點上並行跑 3 個 wasm 容器時,`pgrep -fc containerd-shim` 數到 12 個行程;同樣 3 個原生容器只有 3 個。

```text
wasmedge  常駐 38.8 MiB  +9.3 MiB/容器  4 行程/容器
runc      常駐  0.1 MiB  +4.5 MiB/容器  1 行程/容器
```

**根因**:整個 WasmEdge 執行期靜態連結在那支 109 MiB 的 shim 裡(runc 的 shim 是 8.4 MiB),而 runwasi 的 shim 模型對每個容器多開一組行程。

**為什麼要記**:「wasm 比容器輕」這個說法在節點層**不成立**——**同一份原始碼,wasm 版容器的 cgroup 是原生版的 5.4 倍,shim 那一側更是差一個數量級。輕的是 wasm 檔案,不是跑起來的行程。** 做容量規劃時要算的是後者。

**適用範圍要講清楚**:對照組是 scratch base 的靜態 musl 二進位檔,**那是容器這一側的最佳情況**。跟一個疊在 `debian:12-slim` 上的應用比,結論可能不同——**而那個對照今天沒量。**

### 地雷 3:想在乾淨的 host 上量 wasmCloud 的單一元件,會被「不重新平衡」擋住 {#mine-3}

**症狀**:刪掉一顆 host pod 換一顆乾淨的,等 160 秒,`workloads_started` 一直是 0,量不到任何元件的成本。

**根因**:[Day 3 已經量過](sprint3-day3-wasmcloud-distributed.md)——wasmCloud 不重新平衡,新加入的 host 不會分到既有的 workload。刪 pod 換新的這個做法,拿到的是一顆永遠空著的 host。

**修法**:要量乾淨 host 上的單一元件,得**先有乾淨 host,再部署一個新的 `WorkloadDeployment`**,順序不能反;或把 host group 縮到 1 顆強迫所有 workload 集中,再一個一個加。

**這顆的形狀值得記**:**前面幾天量到的行為特性,會變成後面實驗設計的約束。** 那些發現不只是知識,也是限制。

## 帶得走的東西

- **null control 是一個數字能不能用的前提。** 先量「什麼都不做要多久」(這裡是 16 毫秒、IQR 1),才知道後面 160 毫秒裡有多少是量具的。沒有這個地板值,任何比較都在猜。
- **一批數據不算數,兩批才看得出邊界。** 今天兩批獨立的 n=50 給出**相反的判定**——這不是實驗失敗,這是實驗告訴你「差異就在你的解析度邊緣」。只跑一批的話,不論拿到哪一批都會寫出過度自信的結論。
- **「量不出來」是一個結論,不是沒有結論。** 帶信賴區間的否定比一個沒有區間的排名有用得多:`[-16.0, +1.0] ms` 明確告訴讀者「差異即使存在也小於 16 毫秒」,而那對決策已經夠了。
- **輕的是檔案,不是行程。** wasm 二進位檔確實小,但跑起來之後,執行期要常駐、每個實例要開行程。**容量規劃時把檔案大小當行程成本,會低估一個數量級。**
- **前一天的發現會變成後一天的實驗約束。** Day 3 量到「wasmCloud 不重新平衡」,今天它讓一個看起來很自然的量測方法整個失效。**前面章節的結論,同時是後面實驗的約束條件。**
- **比較之前先確認兩邊在同一個量測平面上。** `ctr` 層的 165 毫秒與端到端的 2.3 秒不是同一件事的兩個數字,把它們放進同一張表就是在誤導。

## 延伸閱讀

- **[containerd 的 shim 說明](https://github.com/containerd/containerd/blob/main/core/runtime/v2/README.md)** —— 解釋 shim 為什麼是獨立行程、為什麼 containerd 重啟不會殺掉容器(Day 4 量到的那件事),以及 shim 的生命週期模型——地雷 2 那個「每個容器 4 個行程」要從這裡理解。
- **[containerd/runwasi](https://github.com/containerd/runwasi)** —— 今天量的 wasmedge shim。`crates/containerd-shim-wasmedge` 底下看得到它用 youki 當 OCI 層,那正是 cgroup 裡那個 `youki:[2:INIT]` 的來源。
- **[Linux 核心的 smaps_rollup 文件](https://docs.kernel.org/filesystems/proc.html)** —— PSS 與 RSS 的差別。量多行程共享記憶體的東西時,用 RSS 會系統性高估,而 shim 剛好就是這種東西。

## 下一步

三條路線的成本輪廓到這裡出來了兩條半:**冷啟動在容器這條路上分不出高下,記憶體則是 wasm 明顯較重,而 wasmCloud 因為量測平面不同無法並列。**

[Day 6](sprint3-day6-spinkube-shim.md) 走最後一條路線 **SpinKube**,也是唯一一條會**派 operator 去替你改節點**的。前兩條的節點改動都是自己動手(wasmCloud 零改動、WasmEdge 8 行加一個 shim),那一天要看的是:**當這件事交給一個 controller 自動化之後,它到底在節點上做了什麼、又留下什麼。**

而今天步驟 1 的結果已經先替 Day 8 改了規則:**那天的解除安裝與比對必須在同一個 VM 生命週期內完成,中途不能停機**——否則驗到的是「VM 被換掉了」,不是「拆得乾淨」。

---

!!! quote ""
    WebAssembly 標誌為 WebAssembly 專案之官方資產(CC0 1.0),此處作社群教學用途。
