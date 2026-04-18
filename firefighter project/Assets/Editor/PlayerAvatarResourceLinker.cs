#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

/// <summary>
/// Ensures the Kevin Iglesias dummy prefabs are present under Assets/Resources/Prefabs/
/// so PlayerAvatarController can load them at runtime via Resources.Load().
///
/// Runs automatically on every domain reload (script compile, project open, etc.).
/// Idempotent — skips the copy if the file already exists.
/// </summary>
[InitializeOnLoad]
public static class PlayerAvatarResourceLinker
{
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

    static PlayerAvatarResourceLinker()
    {
        EnsurePrefabsLinked();
    }

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
