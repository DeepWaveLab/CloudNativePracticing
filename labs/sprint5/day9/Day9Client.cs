using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using Nakama;
using UnityEngine;

[Serializable]
public class Assignment
{
    public bool found;
    public string gameserver;
    public string proxy;
    public string routing_token;
    public string direct;
}

public class Day9Client : MonoBehaviour
{
    public Material localMaterial;
    public Material remoteMaterial;
    private readonly List<string> lines = new List<string>();
    private string playerName = "unity-player";
    private string host = "127.0.0.1";
    private int port = 7350;
    private string screenshotPath;
    private string framesDir;
    private float relaySeconds;
    private bool failed;

    private void Log(string line)
    {
        lines.Add(line);
        Debug.Log($"[day9] {playerName}: {line}");
    }

    private static string Arg(string name, string fallback)
    {
        var args = Environment.GetCommandLineArgs();
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == name) return args[i + 1];
        }
        return fallback;
    }

    private async void Start()
    {
        playerName = Arg("-player", playerName);
        host = Arg("-nakamaHost", host);
        port = int.Parse(Arg("-nakamaPort", port.ToString()));
        screenshotPath = Arg("-screenshot", null);
        framesDir = Arg("-frames", null);
        relaySeconds = float.Parse(Arg("-relay", "0"), System.Globalization.CultureInfo.InvariantCulture);
        Application.runInBackground = true;
        if (Camera.main != null)
        {
            Camera.main.clearFlags = CameraClearFlags.SolidColor;
            Camera.main.backgroundColor = new Color(0.08f, 0.1f, 0.14f);
        }

        try
        {
            await Run();
        }
        catch (Exception e)
        {
            failed = true;
            Log($"FAILED {e.GetType().Name}: {e.Message}");
        }

        if (!string.IsNullOrEmpty(screenshotPath))
        {
            await Task.Delay(500);
            ScreenCapture.CaptureScreenshot(screenshotPath);
            await Task.Delay(1500);
            Log($"screenshot {screenshotPath}");
        }
        Application.Quit(failed ? 1 : 0);
    }

    private async Task Run()
    {
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
        Log($"matchmaker ticket {ticket.Ticket.Substring(0, 8)}");

        var timeout = Task.Delay(TimeSpan.FromSeconds(60));
        if (await Task.WhenAny(Task.WhenAll(matched.Task, assigned.Task), timeout) == timeout)
        {
            throw new TimeoutException($"matched={matched.Task.IsCompleted} assignment={assigned.Task.IsCompleted}");
        }
        var match = matched.Task.Result;
        var assignment = assigned.Task.Result;
        Log($"matchmaker_matched users={match.Users.Count()}");
        Log($"notification 6006 gameserver={assignment.gameserver} token={assignment.routing_token}");

        var rpc = await client.RpcAsync(session, "get_assignment", "{}");
        var fromRpc = JsonUtility.FromJson<Assignment>(rpc.Payload);
        var rpcMatches = fromRpc.found && fromRpc.gameserver == assignment.gameserver && fromRpc.routing_token == assignment.routing_token;
        Log($"rpc get_assignment found={fromRpc.found} matches_notification={rpcMatches}");

        await socket.CloseAsync();

        if (relaySeconds > 0)
        {
            var relayOk = await RunRelay(assignment);
            failed = !(rpcMatches && relayOk);
            Log($"RESULT ok={(!failed).ToString().ToLower()} gameserver={assignment.gameserver} token={assignment.routing_token}");
            return;
        }

        var good = await UdpRoundtrip(assignment.proxy, $"hello-{playerName}" + assignment.routing_token);
        Log($"udp via proxy with token -> {good ?? "(no reply)"}");
        var bad = await UdpRoundtrip(assignment.proxy, $"hello-{playerName}" + "zz0");
        Log($"udp via proxy with wrong token -> {bad ?? "(no reply)"}");

        var ok = rpcMatches && good != null && good.StartsWith("ACK") && bad == null;
        failed = !ok;
        Log($"RESULT ok={ok.ToString().ToLower()} gameserver={assignment.gameserver} token={assignment.routing_token}");
    }

    private readonly Dictionary<string, GameObject> avatars = new Dictionary<string, GameObject>();
    private int sent;
    private readonly Dictionary<string, int> receivedFrom = new Dictionary<string, int>();

    private GameObject Avatar(string name, bool local)
    {
        if (avatars.TryGetValue(name, out var existing)) return existing;
        var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.name = name;
        cube.GetComponent<Renderer>().sharedMaterial = local ? localMaterial : remoteMaterial;
        avatars[name] = cube;
        return cube;
    }

    private async Task<bool> RunRelay(Assignment assignment)
    {
        var parts = assignment.proxy.Split(':');
        var token = assignment.routing_token;

        var rejected = await UdpRoundtrip(assignment.proxy, $"JOIN {playerName}" + "zz0");
        Log($"relay JOIN with wrong token -> {rejected ?? "(no reply)"}");

        var cam = Camera.main;
        cam.transform.position = new Vector3(0, 12, -6);
        cam.transform.LookAt(Vector3.zero);
        new GameObject("Light").AddComponent<Light>().type = LightType.Directional;
        var local = Avatar(playerName, true);

        using (var udp = new UdpClient())
        {
            udp.Connect(parts[0], int.Parse(parts[1]));
            var join = Encoding.UTF8.GetBytes($"JOIN {playerName}" + token);
            await udp.SendAsync(join, join.Length);
            var welcome = udp.ReceiveAsync();
            if (await Task.WhenAny(welcome, Task.Delay(3000)) != welcome)
            {
                Log("relay JOIN with token -> (no reply)");
                return false;
            }
            Log($"relay JOIN with token -> {Encoding.UTF8.GetString(welcome.Result.Buffer).Trim()}");

            var phase = playerName.EndsWith("b") ? Mathf.PI : 0f;
            var receiving = ReceiveLoop(udp);
            var start = Time.realtimeSinceStartup;
            var frame = 0;
            var nextShot = 0f;
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

            var bye = Encoding.UTF8.GetBytes($"BYE {playerName}" + token);
            await udp.SendAsync(bye, bye.Length);
            relayDone = true;
            await Task.WhenAny(receiving, Task.Delay(500));
        }

        var summary = string.Join(",", receivedFrom.Select(kv => $"{kv.Key}:{kv.Value}"));
        var minReceived = receivedFrom.Count == 0 ? 0 : receivedFrom.Values.Min();
        Log($"RELAY sent={sent} received={(summary == "" ? "none" : summary)}");
        return receivedFrom.Count >= 1 && minReceived >= sent / 2;
    }

    private bool relayDone;

    private async Task ReceiveLoop(UdpClient udp)
    {
        while (!relayDone)
        {
            var receive = udp.ReceiveAsync();
            if (await Task.WhenAny(receive, Task.Delay(500)) != receive) continue;
            var f = Encoding.UTF8.GetString(receive.Result.Buffer).Trim().Split(' ');
            if (f.Length < 5 || f[0] != "POS" || f[1] == playerName) continue;
            receivedFrom[f[1]] = receivedFrom.TryGetValue(f[1], out var n) ? n + 1 : 1;
            var x = float.Parse(f[3], System.Globalization.CultureInfo.InvariantCulture);
            var z = float.Parse(f[4], System.Globalization.CultureInfo.InvariantCulture);
            Avatar(f[1], false).transform.position = new Vector3(x, 0.5f, z);
        }
    }

    private static async Task<string> UdpRoundtrip(string endpoint, string payload)
    {
        var parts = endpoint.Split(':');
        using (var udp = new UdpClient())
        {
            var bytes = Encoding.UTF8.GetBytes(payload);
            await udp.SendAsync(bytes, bytes.Length, parts[0], int.Parse(parts[1]));
            var receive = udp.ReceiveAsync();
            if (await Task.WhenAny(receive, Task.Delay(3000)) != receive) return null;
            return Encoding.UTF8.GetString(receive.Result.Buffer).Trim();
        }
    }

    private void OnGUI()
    {
        GUI.skin.label.fontSize = 22;
        GUILayout.BeginArea(new Rect(20, 20, Screen.width - 40, Screen.height - 40));
        GUILayout.Label($"Sprint 5 Day 9 · {playerName}");
        if (relaySeconds > 0) GUILayout.Label($"relay sent={sent} received={string.Join(",", receivedFrom.Select(kv => $"{kv.Key}:{kv.Value}"))}");
        foreach (var line in lines) GUILayout.Label(line);
        GUILayout.EndArea();
    }
}
