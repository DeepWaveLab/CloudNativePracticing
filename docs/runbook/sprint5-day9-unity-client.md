# Day 9: 兩位玩家都收到 ACK，就能互相看到嗎？

![Nakama 官方標誌](../assets/logos/nakama-icon-color.png){ align=right width="72" }

你把 Python client 換成用 Unity 製作的 client。Unity 是遊戲引擎，負責場景、畫面與遊戲邏輯；但兩位玩家都收到 `ACK`，畫面卻只有自己的角色在移動。連線明明成功了，還少了什麼？

少的是把 A 的位置交給 B、把 B 的位置交給 A 的遊戲程式。本章先用 Nakama 官方 SDK 重做登入與配對，再把只會回覆發送者的測試伺服器換成 relay server。你會用兩個視窗、雙方 log 與 GameServer 狀態，分別判斷「連得上」「收到對方資料」與「對戰正常結束」，而不把一個 `ok=true` 當成全部成功。

## Unity client 與 relay server 各自負責什麼

先看玩家端：**SDK** 是把常用 API 與連線處理包成函式的程式庫。第一部分用 nakama-unity 對應 Day 7 手動送出的請求；下表可以用來追查某個 SDK 呼叫實際對應哪一段流程：

| 步驟 | Day 7 Python（直接打 API） | Day 9 Unity（nakama-unity SDK） |
|---|---|---|
| 登入 | `POST /v2/account/authenticate/device` | `client.AuthenticateDeviceAsync()` |
| 開 realtime socket | 手寫 WebSocket 握手與 frame | `client.NewSocket()` + `ConnectAsync()` |
| 排隊 | 送 `matchmaker_add` JSON | `socket.AddMatchmakerAsync("*", 2, 2)` |
| 收配對結果 | 自己解析訊息類型 | `ReceivedMatchmakerMatched`、`ReceivedNotification` 事件 |
| RPC | body 要送 JSON 字串 | `client.RpcAsync(session, "get_assignment", "{}")` |
| UDP 經 Quilkin | `socket` | `System.Net.Sockets.UdpClient` |

SDK 省掉的是 WebSocket 與訊息格式的細節；UDP 那一段 SDK 不管，因為遊戲封包本來就不經過 Nakama。

到 Part 1 為止，Fleet 裡跑的仍是 `simple-game-server`。它收到什麼就原樣回給送的人，不會轉給同一場的另一個玩家，所以就算 Unity 畫了兩個角色，A 也看不到 B 在動。第二部分換成 **relay server**，也就是收到一位玩家資料後，會轉發給其他玩家的伺服器：每個玩家送來的位置，轉給同一顆 server 上的其他玩家。其他元件都不變，Nakama 照樣負責登入、配對與 allocation，Quilkin 照樣用 token 轉送。

「relay」在 Nakama 的文件裡還有另一個意思，兩者要分清楚：

| | Nakama client relayed multiplayer | 本章的 relay server |
|---|---|---|
| 遊戲封包走哪裡 | Nakama 的 realtime socket（WebSocket，TCP） | UDP，經 Quilkin 到 Agones 上的 GameServer |
| server 是誰 | Nakama 本身轉發 | 一顆由 Agones 管理的 dedicated server |
| 生命週期 | 跟著 Nakama 的 match | 跟著 GameServer：`Ready` → `Allocated` → `Shutdown` |

本章用後者：relay 部署成 Agones 的 GameServer，分配與生命週期都由 Agones 管理，Day 8 看過的 shutdown、補位與節點失效行為也都適用。

看圖時先找出兩條不同路徑：登入、配對與查詢仍走 Nakama；位置資料則經 Quilkin 到 relay，再轉給另一位玩家。

![Unity 透過 Nakama 取得 assignment，再經 Quilkin 與 relay 交換位置資料](../assets/diagrams/s5d9-relay.svg)

替換的是 GameServer 內的程式與選取它的 Fleet 設定。Nakama 不處理這些位置封包，Quilkin 也不決定角色應該移到哪裡；relay 才負責本章的玩家間轉送。

Unity 有兩種執行檔角色：**Editor** 負責建置專案，**player** 是建置後供玩家執行的程式。本章以 Editor 的 `-batchmode` 執行建置方法，再用 player 的 `-logFile` 保存結果。需要觀察方塊與截圖時仍會開啟有畫面的 player；背景 player 則用於第一部分的連線測試。

## 開始之前

Part 1 需要 Day 7 的演練環境：演練節點池、`nakama-lab`（含 `get_assignment` 的 module）與 `lab-game-server` Fleet。如果 Day 8 已拆除環境，先依 [Day 7](sprint5-day7-python-client-e2e.md) 重建到 module 載入、Fleet Ready 與 healthcheck 成功；若已保留，仍需確認這些條件。Part 2 再部署 relay Fleet，並把 allocation selector 切到 relay。

本章需要 macOS、Unity 6000.3.19f1、已啟用的 Unity 授權，以及 nakama-unity v3.22.0；下方路徑與建置目標都是 macOS standalone。Part 2 另外需要 Azure Container Registry（ACR），也就是 Azure 用來儲存與提供 container image 的服務，並讓叢集有權限拉取 image。本章透過 ACR Tasks 在雲端 build image，本機不需要 Docker。完整檔案在 [labs/sprint5/day9/](https://github.com/DeepWaveLab/CloudNativePracticing/tree/main/labs/sprint5/day9)。

先在課程儲存庫的 `labs/sprint5/day9/` 目錄操作。本章會在這裡建立 `cnp-day9-unity` 子目錄；後續 `$UNITY`、`$APP` 與相對路徑沿用同一個 shell。完整 C# 檔案才包含輔助函式與模式切換，不能只用下方節錄建置。

## Part 1：用 nakama-unity 重做玩家流程

### 步驟 1:用命令列建專案並加入 nakama-unity

`UNITY` 指向 Unity 執行檔（macOS 的 Hub 安裝路徑如下）。先建空專案：

```console
$ UNITY=/Applications/Unity/Hub/Editor/6000.3.19f1/Unity.app/Contents/MacOS/Unity
$ $UNITY -batchmode -nographics -createProject cnp-day9-unity -logFile day9-create.log -quit
$ echo $?
0
```

batchmode 需要已啟用的授權，建專案的 log 裡會看到授權類型：

```text
[Licensing::Module] License group:
  Product: Unity Personal Version
```

官方 README 提供用 git URL 加入套件的方式，在新專案的 `cnp-day9-unity/Packages/manifest.json` 之 `dependencies` 物件加入以下項目，保留原有套件並注意逗號；版本固定在 tag（完整檔案：[manifest.json](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/manifest.json)）：

```json
"com.heroiclabs.nakama-unity": "https://github.com/heroiclabs/nakama-unity.git?path=/Packages/Nakama#v3.22.0"
```

下次用 batchmode 開這個專案時，Package Manager 會自動抓下套件。

### 步驟 2:寫 client

先建立新專案內的 `Assets/Scripts/` 目錄，將本章完整的 `Day9Client.cs` 複製到 `cnp-day9-unity/Assets/Scripts/Day9Client.cs`。這個檔案（完整檔案：[Day9Client.cs](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/Day9Client.cs)）定義一個 MonoBehaviour，也就是可掛在 Unity 場景物件上的程式元件；稍後建置腳本會建立場景並掛上它，不需要手動拖曳。參數從命令列讀取（`-player`、`-nakamaPort`、`-screenshot` 等），這樣同一個 build 可以用不同身分跑兩次。主要流程：

```csharp
var client = new Client("http", host, port, "defaultkey", UnityWebRequestAdapter.Instance);
var session = await client.AuthenticateDeviceAsync($"sprint5-day9-{playerName}-{Guid.NewGuid():N}");
Log($"login user={session.UserId.Substring(0, 8)} created={session.Created}");

var socket = client.NewSocket();
var matched = new TaskCompletionSource<IMatchmakerMatched>();
var assigned = new TaskCompletionSource<Assignment>();
socket.ReceivedMatchmakerMatched += m => matched.TrySetResult(m);
socket.ReceivedNotification += n =>
{
    if (n.Code == 6006) assigned.TrySetResult(JsonUtility.FromJson<Assignment>(n.Content));
};
await socket.ConnectAsync(session);

var ticket = await socket.AddMatchmakerAsync("*", 2, 2);
```

跟 Day 7 一樣，要同時等到 `matchmaker_matched` 與 6006 通知才往下走，兩者的先後順序不固定：

```csharp
var timeout = Task.Delay(TimeSpan.FromSeconds(60));
if (await Task.WhenAny(Task.WhenAll(matched.Task, assigned.Task), timeout) == timeout)
{
    throw new TimeoutException($"matched={matched.Task.IsCompleted} assignment={assigned.Task.IsCompleted}");
}
```

RPC 由 SDK 處理字串編碼，回傳的 `Payload` 是 JSON 字串：

```csharp
var rpc = await client.RpcAsync(session, "get_assignment", "{}");
var fromRpc = JsonUtility.FromJson<Assignment>(rpc.Payload);
```

`Assignment` 的欄位名稱必須跟 Day 7 Lua module 寫入的 key 一致（`gameserver`、`proxy`、`routing_token`、`direct`），`JsonUtility` 才解析得到。UDP 那一段用 `UdpClient`，把 token 接在 payload 後面，3 秒內沒回應就回傳 null。這是逾時觀察；還要搭配正確 token 成功的對照，才能判斷路由條件：

```csharp
var bytes = Encoding.UTF8.GetBytes(payload);
await udp.SendAsync(bytes, bytes.Length, parts[0], int.Parse(parts[1]));
var receive = udp.ReceiveAsync();
if (await Task.WhenAny(receive, Task.Delay(3000)) != receive) return null;
```

每一步都呼叫 `Log()`，同時寫進 `Debug.Log`（進 player log）與畫面上 `OnGUI` 顯示的文字。`OnGUI` 的白字壓在預設天空盒的地平線亮帶上不易辨識，client 啟動時把攝影機背景設成深色純色（`CameraClearFlags.SolidColor`）。

### 步驟 3:用 `-executeMethod` build macOS player

再建立新專案內的 `Assets/Editor/` 目錄，把完整 `Day9Build.cs` 複製到 `cnp-day9-unity/Assets/Editor/Day9Build.cs`。這份只在 Editor 執行的腳本（完整檔案：[Day9Build.cs](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/Day9Build.cs)）做三件事：建一個場景、放一個掛著 `Day9Client` 的物件、build 出 macOS player。

```csharp
public static void BuildMac()
{
    var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
    var client = new GameObject("Day9Client").AddComponent<Day9Client>();
    client.localMaterial = CreateMaterial("Assets/Materials/Local.mat", new Color(0.2f, 0.8f, 0.4f));
    client.remoteMaterial = CreateMaterial("Assets/Materials/Remote.mat", new Color(0.95f, 0.45f, 0.2f));
    System.IO.Directory.CreateDirectory("Assets/Scenes");
    EditorSceneManager.SaveScene(scene, ScenePath);

    PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
    PlayerSettings.runInBackground = true;

    var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
    {
        scenes = new[] { ScenePath },
        locationPathName = "Build/Day9Client.app",
        target = BuildTarget.StandaloneOSX,
        options = BuildOptions.None,
    });
    Debug.Log($"[day9-build] result={report.summary.result} size={report.summary.totalSize} errors={report.summary.totalErrors}");
    EditorApplication.Exit(report.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded ? 0 : 1);
}
```

兩個 material（材質資產）決定第二部分方塊的顏色，也讓建置保留需要的 shader（繪製物體的程式）。完整腳本的 `CreateMaterial()` 會建立使用 Standard shader 的材質，再指派給場景中 `Day9Client` 的欄位，讓建置時保留被引用的 shader；client 在執行期用 `sharedMaterial` 將材質套到方塊上。這樣可避免 Standard shader 因未被引用而遭建置剝除，導致方塊顯示代表 shader 錯誤的洋紅色。`runInBackground` 要打開，否則兩個視窗同時跑時，失去焦點的那個會暫停更新。

```console
$ $UNITY -batchmode -nographics -projectPath cnp-day9-unity -executeMethod Day9Build.BuildMac -logFile day9-build.log
$ echo $?
0
```

```text
com.heroiclabs.nakama-unity@https://github.com/heroiclabs/nakama-unity.git?path=/Packages/Nakama#v3.22.0 (location: .../Library/PackageCache/com.heroiclabs.nakama-unity@903c3cd9c59d)
Build Finished, Result: Success.
[day9-build] result=Succeeded size=107192976 errors=0
```

建置成功時，log 應顯示 `Build Finished, Result: Success.`、`result=Succeeded` 與 `errors=0`，並產生可執行的 `Build/Day9Client.app`。若 log 出現 `Error: Access token is unavailable; failed to update`，仍須確認上述結果與產物，不能只憑授權類型判斷建置成功。

### 步驟 4:跑兩個 player

啟動 player 前，確認 `lab-game-server` Fleet 有 Ready 的 server，並把 `nakama-lab` 的 Nakama port-forward 到本機 17350（`kubectl -n nakama-lab port-forward svc/nakama 17350:7350`）。port-forward 保留在另一個終端機。以下末尾的 `&` 讓兩個 player 同時執行，否則第一個會一直等待配對。A 開視窗並在結束前截圖，B 用 `-batchmode -nographics` 在背景跑：

```console
$ APP=cnp-day9-unity/Build/Day9Client.app/Contents/MacOS/Day9Client
$ $APP -logFile player-a.log -player unity-a -nakamaPort 17350 -screenshot player-a.png \
    -screen-width 1280 -screen-height 720 -screen-fullscreen 0 &
$ $APP -batchmode -nographics -logFile player-b.log -player unity-b -nakamaPort 17350 &
```

本次兩個 player 都以 exit code 0 結束；自己執行時要等兩個背景程序結束，再讀 log。從兩份 log 挑出 client 印的狀態行（下面節錄登入到結果的部分）：

```console
$ grep -hF '[day9]' player-a.log player-b.log
[day9] unity-a: login user=3509bbe2 created=True
[day9] unity-a: matchmaker ticket 90e22260
[day9] unity-a: matchmaker_matched users=2
[day9] unity-a: notification 6006 gameserver=lab-game-server-4f7v7-ddw75 token=wrb
[day9] unity-a: rpc get_assignment found=True matches_notification=True
[day9] unity-a: udp via proxy with token -> ACK: hello-unity-a
[day9] unity-a: udp via proxy with wrong token -> (no reply)
[day9] unity-a: RESULT ok=true gameserver=lab-game-server-4f7v7-ddw75 token=wrb
[day9] unity-b: login user=ee99bee2 created=True
[day9] unity-b: matchmaker ticket b791dbb6
[day9] unity-b: matchmaker_matched users=2
[day9] unity-b: notification 6006 gameserver=lab-game-server-4f7v7-ddw75 token=wrb
[day9] unity-b: rpc get_assignment found=True matches_notification=True
[day9] unity-b: udp via proxy with token -> ACK: hello-unity-b
[day9] unity-b: udp via proxy with wrong token -> (no reply)
[day9] unity-b: RESULT ok=true gameserver=lab-game-server-4f7v7-ddw75 token=wrb
```

GameServer 這一側的對照跟 Day 7 相同。下方名稱與 `wrb` 是實測值，請換成自己收到的 GameServer 與 token：

```console
$ kubectl get gs lab-game-server-4f7v7-ddw75 \
    -o jsonpath='{.status.state} {.metadata.labels.agones\.dev/fleet} tokens={.metadata.annotations.quilkin\.dev/tokens}'
Allocated lab-game-server tokens=d3Ji
$ echo -n wrb | base64
d3Ji
$ kubectl logs lab-game-server-4f7v7-ddw75 -c simple-game-server | grep 'Received UDP'
2026/09/29 05:02:25 Received UDP: hello-unity-b
2026/09/29 05:02:25 Received UDP: hello-unity-a
```

player A 視窗會顯示登入、配對、RPC 比對與 UDP 測試的結果。下圖可對照各階段狀態及最後的 `RESULT ok=true`；GameServer 名稱與 token 以各次配對收到的值為準：

![Unity player A 的畫面：依序列出登入、matchmaker ticket、配對成立、6006 通知的 GameServer 與 token、RPC 比對結果、UDP 經 Quilkin 收到 ACK、錯誤 token 無回應，以及 RESULT ok=true](../assets/screenshots/sprint5-day9/sdk-flow-player-a.png)

## Part 2：relay server 與多人畫面同步

### 步驟 5:用 Go 寫 relay server

relay server 用 Go 加 Agones Go SDK（v1.60.0）寫（完整檔案：[relay-server/main.go](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/relay-server/main.go)），協定是三種純文字訊息：

| 訊息 | 意思 | relay 的反應 |
|---|---|---|
| `JOIN <player>` | 玩家加入 | 用來源位址記下這個玩家，回 `WELCOME <player>` |
| `POS <player> <seq> <x> <z>` | 玩家目前的位置 | 原樣轉給其他所有玩家 |
| `BYE <player>` | 玩家離開 | 從名單移除 |

client 送出的每個封包尾端都帶著 routing token，Quilkin 轉送前已經剝掉，所以 relay 收到的是乾淨的訊息。轉發的核心：

```go
case "POS":
	p, ok := r.peers[key]
	if !ok {
		return
	}
	p.lastSeen = time.Now()
	for otherKey, other := range r.peers {
		if otherKey == key {
			continue
		}
		r.send(other.addr, msg)
		r.forwarded[p.name+"->"+other.name]++
		if n := r.forwarded[p.name+"->"+other.name]; n == 1 || n%50 == 0 {
			log.Printf("relay %s->%s count=%d last=%q", p.name, other.name, n, msg)
		}
	}
```

`key` 是封包的來源位址（IP:port）。所有玩家都經過 Quilkin 進來，relay 看到的是 proxy 的來源 IP 與 port，並以整個 IP:port 作為 key。這次兩位玩家剛好來自同一個 proxy IP、不同 port；多個 proxy 時不能只用 port 作為身分。回傳給玩家的封包也送到這個位址，Quilkin 會把它轉回對應的 client。

跟 Agones 的互動有四個：啟動後呼叫 `Ready()`；每 2 秒呼叫 `Health()`；用 `WatchGameServer()` 得知自己被分配；對戰結束時呼叫 `Shutdown()`。結束條件是已被分配、至少收到過一個封包，而且所有玩家都離開，或最後一個封包已超過 60 秒。注意 `lastPkt.IsZero()`：分配後完全沒有玩家送封包的情況，這段程式不會自行結束：

```go
func (r *relay) shouldShutdown() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.allocated || r.lastPkt.IsZero() {
		return false
	}
	return len(r.peers) == 0 || time.Since(r.lastPkt) > idleTimeout
}
```

這就是 Day 8 步驟 2 講的 graceful shutdown：由 game server 自己宣告對戰結束，Agones 不必等 health check 失敗。

### 步驟 6:build image 並部署 relay Fleet

image 分成編譯與執行兩層（multi-stage build）；編譯層有 Go 工具鏈，最終層用 distroless，只帶執行這支程式所需的檔案（完整檔案：[relay-server/Dockerfile](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/relay-server/Dockerfile)）：

```dockerfile
FROM golang:1.26 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /relay .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /relay /relay
EXPOSE 7654/udp
ENTRYPOINT ["/relay"]
```

在本章的 `relay-server/` 目錄執行下列建置，讓最後的 `.` 包含 Dockerfile、`main.go`、`go.mod` 與 `go.sum`。`<ACR-NAME>` 換成自己的 registry 名稱；ACR Tasks 在雲端建置並儲存 image：

```console
$ az acr build -r <ACR-NAME> -t cnp-day9-relay:0.1.0 --platform linux/amd64 .
```

```text
    repository: cnp-day9-relay
    tag: 0.1.0
    digest: sha256:bb1dff40c35c8c41b0b62d35a90b95fc5af20c34e515db166d3ec8aeed3a7463
Run ID: cef was successful after 1m21s
```

前提是叢集要有權限拉這個 registry 的 image，可以用 `az aks check-acr -g <RESOURCE-GROUP> -n <CLUSTER> --acr <ACR-NAME>.azurecr.io` 確認；沒有權限時，授權方式見 [AKS 與 Azure Container Registry 整合](https://learn.microsoft.com/en-us/azure/aks/cluster-container-registry-integration)。接著 Fleet 由 Day 7 的 `lab-game-server` 改出（完整檔案：[relay-fleet.yaml](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/relay-fleet.yaml)）：名稱改成 `lab-relay-server`、container 改用 relay image，port 仍是 7654。

建置完成後回到 `labs/sprint5/day9/`。把 `relay-fleet.yaml` 中 `<ACR-NAME>.azurecr.io` 換成剛才建置的 registry，再套用完整檔案。不要把範例 digest 當成自己建置必然得到的值。

Nakama 這一側改兩處。第一處是 Lua module：allocation selector 原本寫死 `lab-game-server`，改成從 runtime env 讀（完整檔案：[nakama-lab-module.yaml](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/nakama-lab-module.yaml)）：

```lua
selectors = { { matchLabels = { ["agones.dev/fleet"] = context.env["FLEET_NAME"] } } },
```

第二處是 Nakama 啟動參數，加上 `--runtime.env "FLEET_NAME=lab-relay-server"`（[nakama-lab.yaml](https://github.com/DeepWaveLab/CloudNativePracticing/blob/main/labs/sprint5/day9/nakama-lab.yaml)）。套用修改後的 module 與 Deployment，等 Nakama rollout 完成；Nakama 的 pod 換過之後，原本的 port-forward 會失效，要重開一次（見 [Day 6 的 port-forward 失效說明](sprint5-day6-matchmaking.md#mine-3)）。部署 relay Fleet 後，兩顆 relay 都 Ready：

```text
NAME                           STATE   ADDRESS         PORT   NODE
lab-relay-server-zn6v7-5k8cb   Ready   <NODE-IP>       7020   aks-gslab-29120627-vmss000000
lab-relay-server-zn6v7-7rxkc   Ready   <NODE-IP>       7843   aks-gslab-29120627-vmss000000
```

relay 的 log 第一行是 `relay listening on :7654, marked Ready`，代表它已經呼叫過 `Ready()`。

### 步驟 7:client 加上 relay 模式

前面複製的完整 `Day9Client.cs` 已包含 `-relay <秒數>` 參數；不需再另寫一份 client。拿到 assignment 之後，改走 relay 流程：先用錯的 token 送一次 `JOIN` 確認被擋下，再用正確 token 加入，然後每 50 毫秒送一次自己的位置，讓方塊繞著圓心轉。兩個 player 的相位差半圈，畫面上會在圓的兩端：

```csharp
while (Time.realtimeSinceStartup - start < relaySeconds)
{
    var t = Time.realtimeSinceStartup - start;
    var pos = new Vector3(Mathf.Cos(t * 1.5f + phase) * 4f, 0.5f, Mathf.Sin(t * 1.5f + phase) * 4f);
    local.transform.position = pos;
    sent++;
    var msg = Encoding.UTF8.GetBytes($"POS {playerName} {sent} {pos.x:F2} {pos.z:F2}" + token);
    await udp.SendAsync(msg, msg.Length);
    if (framesDir != null && t >= nextShot)
    {
        ScreenCapture.CaptureScreenshot(System.IO.Path.Combine(framesDir, $"frame-{frame++:D3}.png"));
        nextShot += 0.25f;
    }
    await Task.Delay(50);
}
```

同時另一個迴圈接收對方的 `POS`，更新代表對方的方塊位置，並分別計數：

```csharp
var f = Encoding.UTF8.GetString(receive.Result.Buffer).Trim().Split(' ');
if (f.Length < 5 || f[0] != "POS" || f[1] == playerName) continue;
receivedFrom[f[1]] = receivedFrom.TryGetValue(f[1], out var n) ? n + 1 : 1;
var x = float.Parse(f[3], System.Globalization.CultureInfo.InvariantCulture);
var z = float.Parse(f[4], System.Globalization.CultureInfo.InvariantCulture);
Avatar(f[1], false).transform.position = new Vector3(x, 0.5f, z);
```

時間到了送 `BYE`，最後判定：收到對方的封包數至少是自己送出數的一半，才算 `RESULT ok=true`。解析座標時固定用 `InvariantCulture`，避免系統語系把小數點當成逗號。

### 步驟 8:兩個視窗互相看到

確認 Nakama 的 selector 已指向 `lab-relay-server`，且 relay Fleet 有 Ready 後，回到前面的 Unity 專案上層目錄執行。這一步兩個 player 都開視窗，各自每 0.25 秒存一張畫面；若重跑，先換用新的畫面輸出目錄，避免混入前一次結果：

```console
$ mkdir -p frames-a frames-b
$ $APP -logFile relay-a.log -player unity-a -nakamaPort 17350 -relay 10 -frames frames-a -screenshot relay-a.png \
    -screen-width 960 -screen-height 540 -screen-fullscreen 0 &
$ $APP -logFile relay-b.log -player unity-b -nakamaPort 17350 -relay 10 -frames frames-b -screenshot relay-b.png \
    -screen-width 960 -screen-height 540 -screen-fullscreen 0 &
```

兩個 player 都以 exit code 0 結束：

```console
$ grep -hF '[day9]' relay-a.log relay-b.log | grep -E 'notification|JOIN|RELAY|RESULT'
[day9] unity-a: notification 6006 gameserver=lab-relay-server-zn6v7-hbck9 token=sa4
[day9] unity-a: rpc get_assignment found=True matches_notification=True
[day9] unity-a: relay JOIN with wrong token -> (no reply)
[day9] unity-a: relay JOIN with token -> WELCOME unity-a
[day9] unity-a: RELAY sent=153 received=unity-b:154
[day9] unity-a: RESULT ok=true gameserver=lab-relay-server-zn6v7-hbck9 token=sa4
[day9] unity-b: notification 6006 gameserver=lab-relay-server-zn6v7-hbck9 token=sa4
[day9] unity-b: rpc get_assignment found=True matches_notification=True
[day9] unity-b: relay JOIN with wrong token -> (no reply)
[day9] unity-b: relay JOIN with token -> WELCOME unity-b
[day9] unity-b: RELAY sent=154 received=unity-a:153
[day9] unity-b: RESULT ok=true gameserver=lab-relay-server-zn6v7-hbck9 token=sa4
```

10 秒內 A 送出 153、B 收到 A 的 153；B 送出 154、A 收到 B 的 154。這支持本次雙向轉發與計數相符，不能證明正式網路零丟包：程式沒有按序號完整檢查重複、遺失或亂序，而且兩個 player 在同一台 Mac 上。

想把兩個視窗的逐格畫面左右並排成 GIF，可以用 ffmpeg：

```console
$ ffmpeg -framerate 4 -i frames-a/frame-%03d.png -framerate 4 -i frames-b/frame-%03d.png \
    -filter_complex "[0:v]scale=480:-1[a];[1:v]scale=480:-1[b];[a][b]hstack=inputs=2,split[s0][s1];[s0]palettegen=max_colors=64[p];[s1][p]paletteuse=dither=bayer" \
    -loop 0 relay-two-players.gif
```

![兩個 Unity 視窗左右並排的動畫：左邊是 player A、右邊是 player B，每個視窗裡綠色方塊是自己、橘色方塊是對方，兩個方塊沿著同一個圓相隔半圈移動](../assets/screenshots/sprint5-day9/relay-two-players.gif)

綠色方塊是自己，橘色是對方。A 視窗裡的橘色方塊，就是 B 視窗裡的綠色方塊，位置互相對應。結束時兩個視窗的畫面：

![player A 視窗結束時的畫面：綠色方塊在上方、橘色方塊在下方，狀態文字顯示 RELAY sent=153 received=unity-b:154 與 RESULT ok=true](../assets/screenshots/sprint5-day9/relay-player-a.png)

![player B 視窗結束時的畫面：兩個方塊的位置與 A 視窗對應，狀態文字顯示 RESULT ok=true](../assets/screenshots/sprint5-day9/relay-player-b.png)

relay server 這一側的 log 與 GameServer 狀態：

```text
2026/09/29 05:18:00 relay listening on :7654, marked Ready
2026/09/29 05:29:26 state Allocated tokens=c2E0
2026/09/29 05:29:31 join player=unity-a from=<PROXY-EGRESS-IP>:1026 peers=1
2026/09/29 05:29:31 join player=unity-b from=<PROXY-EGRESS-IP>:1028 peers=2
2026/09/29 05:29:31 relay unity-a->unity-b count=1 last="POS unity-a 1 4.00 0.00"
2026/09/29 05:29:31 relay unity-b->unity-a count=1 last="POS unity-b 1 -4.00 0.00"
2026/09/29 05:29:34 relay unity-a->unity-b count=50 last="POS unity-a 50 0.49 -3.97"
2026/09/29 05:29:37 relay unity-b->unity-a count=100 last="POS unity-b 100 3.87 1.03"
2026/09/29 05:29:41 relay unity-a->unity-b count=150 last="POS unity-a 150 -1.92 3.51"
2026/09/29 05:29:41 relay unity-b->unity-a count=150 last="POS unity-b 150 1.73 -3.61"
2026/09/29 05:29:41 bye player=unity-a peers=1
2026/09/29 05:29:41 bye player=unity-b peers=0
2026/09/29 05:29:42 session ended, calling SDK Shutdown
```

```text
05:29:26 lab-relay-server-zn6v7-hbck9   Allocated
05:29:42 lab-relay-server-zn6v7-hbck9   Shutdown
05:29:42 lab-relay-server-zn6v7-q6b8f   PortAllocation
05:29:44 lab-relay-server-zn6v7-q6b8f   Ready
```

對照 relay 的 log：

- `c2E0` 是 `sa4` 的 base64，跟 client 收到的 token 相同。
- 兩個玩家的來源都是同一個 proxy 出口 IP，port 分別是 1026 與 1028。這份封包沒有攜帶玩家原始 IP；程式以來源 IP:port 區分連線，並未驗證玩家身分。
- 兩人都 `bye` 之後 1 秒，relay 呼叫 SDK `Shutdown()`，GameServer 轉成 `Shutdown`，Fleet 在 2 秒內補出新的 Ready。這次 relay 在收到兩人的 BYE 後主動收尾；第一部分只傳 hello，沒有送 `simple-game-server` 支援的 EXIT，因此需要另行清理。

### 步驟 9:收尾

relay 在對戰結束時會自己呼叫 `Shutdown()`，不需要手動刪 GameServer。不再使用時，依序刪掉 `lab-game-server` Fleet、`lab-relay-server` Fleet、`nakama-lab` namespace、`nakama-lab-gameserverallocation-create` Role/RoleBinding，最後刪掉演練節點池：

```console
$ az aks nodepool delete -g <RESOURCE-GROUP> --cluster-name <CLUSTER> -n gslab
```

## 想一想

1. 兩位玩家都收到 echo 的 ACK，為什麼還不能證明他們收到彼此的位置？
2. relay 回 `WELCOME`，是否代表它已驗證這位玩家在配對名單上？從本章協定與程式找依據。
3. relay 已 Allocated，卻從未收到封包，`shouldShutdown()` 會自動釋放它嗎？該補哪一類逾時條件？

## 小結

SDK 改變的是 client 呼叫 API 的方式；玩家是否收到彼此資料，仍由遊戲伺服器程式決定。echo 證明連通性，relay 的雙方收發與畫面支持位置轉送，而 BYE、Shutdown 與新 Ready 串起對戰收尾。這份 relay 沒有驗證玩家身分或位置合理性，也還有未處理的閒置條件，不能把演示成功當成完整遊戲後端。

## 正式環境還要補的

WebGL、iOS、Android 的網路 API 限制各不相同，要上這些平台得分別驗證。Nakama 要有對外入口與 TLS，不能靠 port-forward。兩個 player 在同一台機器上時網路條件相同，收發數字不能代表真實玩家的網路狀況。

relay 是純轉發：不驗證位置是否合理，也不檢查 `JOIN` 的玩家是不是這場配對名單上的人。後續需要在加入時驗證配對資格；例如設計 server-to-server RPC 查詢名單，但本章沒有實作這支驗證 API。client 也要補上玩家輸入與位置更新處理：插值是在已收到的位置之間計算中間位置，讓移動平順；預測是在伺服器回覆前，先依本機輸入計算角色動作，減少操作等待。延遲補償則依網路延遲調整事件判定，例如伺服器回看玩家發出射擊時的狀態來判斷是否命中。本章沒有實作這些功能。

要阻止 client 任意宣告不合理位置，server 需要驗證輸入與遊戲規則，必要時自行計算權威狀態；單純轉發做不到這件事。一個做法是用 Unity 做 dedicated server build（需要另外安裝 Dedicated Server Build Support 模組），搭配 Agones 的 Unity SDK 呼叫 `Ready()`、`Health()`、`Shutdown()`。

## 延伸閱讀

想再深入，可以從這幾份開始：

- **[nakama-unity](https://github.com/heroiclabs/nakama-unity)** —— 官方 Unity client 的安裝方式（含 git URL 釘版本）與 session、socket、RPC 的用法範例。
- **[Unity Editor 命令列參數](https://docs.unity3d.com/6000.3/Documentation/Manual/EditorCommandLineArguments.html)** —— `-batchmode`、`-createProject`、`-executeMethod`、`-logFile` 的說明，對照步驟 1 與步驟 3。
- **[Unity Player 命令列參數](https://docs.unity3d.com/6000.3/Documentation/Manual/PlayerCommandLineArguments.html)** —— player 的 `-batchmode`、`-nographics`、`-logFile` 與 `-screen-*` 視窗參數。
- **[Agones Go SDK](https://agones.dev/site/docs/guides/client-sdks/go/)** —— relay server 用到的 `Ready`、`Health`、`Shutdown` 等生命週期呼叫。
- **[Agones Unity SDK](https://agones.dev/site/docs/guides/client-sdks/unity/)** —— 用 Unity 寫 dedicated server 時對應的 SDK 與安裝方式。
- **[Nakama client relayed multiplayer](https://heroiclabs.com/docs/nakama/concepts/multiplayer/relayed/)** —— Nakama 內建的轉發式多人模式，與本章 Agones 上的 relay server 對照。

## 下一步

[Day 10](sprint5-day10-decision-matrix.md) 不動手：回顧這十天的架構怎麼接起來、實務上什麼情況值得用 Agones 與 Quilkin、後端用 Nakama 還是自建，並列出這門課刻意沒教的主題。

---

!!! quote ""
    Nakama 標誌為 Heroic Labs 之官方資產，此處作社群教學用途。
