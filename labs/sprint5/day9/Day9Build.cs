using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class Day9Build
{
    private const string ScenePath = "Assets/Scenes/Day9.unity";

    private static Material CreateMaterial(string path, Color color)
    {
        System.IO.Directory.CreateDirectory("Assets/Materials");
        var material = new Material(Shader.Find("Standard")) { color = color };
        AssetDatabase.CreateAsset(material, path);
        return material;
    }

    public static void BuildMac()
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
        var client = new GameObject("Day9Client").AddComponent<Day9Client>();
        client.localMaterial = CreateMaterial("Assets/Materials/Local.mat", new Color(0.2f, 0.8f, 0.4f));
        client.remoteMaterial = CreateMaterial("Assets/Materials/Remote.mat", new Color(0.95f, 0.45f, 0.2f));
        System.IO.Directory.CreateDirectory("Assets/Scenes");
        EditorSceneManager.SaveScene(scene, ScenePath);

        PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.defaultScreenWidth = 1280;
        PlayerSettings.defaultScreenHeight = 720;
        PlayerSettings.runInBackground = true;
        PlayerSettings.productName = "Day9Client";

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
}
