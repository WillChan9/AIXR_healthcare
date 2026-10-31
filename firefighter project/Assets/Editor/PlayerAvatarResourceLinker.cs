#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

/// <summary>
/// Editor-only utility that copies the Kevin Iglesias "Human Basic Motions" dummy
/// prefabs into <c>Assets/Resources/Prefabs/</c> so that
/// <see cref="PlayerAvatarController"/> can load them at runtime with
/// <c>Resources.Load()</c> (used by the Male / Female avatar presets).
///
/// Runs automatically on every domain reload (script compile, project open, etc.).
/// It is idempotent: a prefab is only copied if the destination does not exist yet.
/// Requires the Kevin Iglesias "Human Animations" asset to be imported; otherwise a
/// warning is logged and nothing is copied.
/// </summary>
[InitializeOnLoad]
public static class PlayerAvatarResourceLinker
{
    /// <summary>Source prefab path and destination path (inside Resources) for each prefab to copy.</summary>
    private static readonly (string src, string dst)[] Prefabs =
    {
        (
            "Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/Prefabs/Human_BasicMotionsDummy_M.prefab",
            "Assets/Resources/Prefabs/Human_BasicMotionsDummy_M.prefab"
        ),
        (
            "Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/Prefabs/Human_BasicMotionsDummy_F.prefab",
            "Assets/Resources/Prefabs/Human_BasicMotionsDummy_F.prefab"
        ),
    };

    /// <summary>Called by Unity on domain reload.</summary>
    static PlayerAvatarResourceLinker()
    {
        EnsurePrefabsLinked();
    }

    /// <summary>Copies any missing prefab into Resources and refreshes the AssetDatabase if anything changed.</summary>
    private static void EnsurePrefabsLinked()
    {
        bool anyNew = false;

        foreach (var (src, dst) in Prefabs)
        {
            if (AssetDatabase.LoadAssetAtPath<GameObject>(dst) != null)
                continue; // already in Resources

            if (AssetDatabase.LoadAssetAtPath<GameObject>(src) == null)
            {
                Debug.LogWarning($"[PlayerAvatarResourceLinker] Source not found: {src}. Kevin Iglesias 'Human Animations' asset must be imported.");
                continue;
            }

            bool ok = AssetDatabase.CopyAsset(src, dst);
            if (ok)
            {
                Debug.Log($"[PlayerAvatarResourceLinker] Linked {System.IO.Path.GetFileName(dst)} → Resources/Prefabs/");
                anyNew = true;
            }
            else
            {
                Debug.LogWarning($"[PlayerAvatarResourceLinker] Failed to copy {src} → {dst}");
            }
        }

        if (anyNew)
            AssetDatabase.Refresh();
    }
}
#endif
