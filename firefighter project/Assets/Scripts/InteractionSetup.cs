using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

/// <summary>
/// Automatically adds XR interaction components to AI-spawned objects so they
/// become grabbable / hoverable in VR.  Attach to the same GameObject as
/// <see cref="EnvironmentAgent"/> (the AI_Manager).
///
/// After EnvironmentAgent.PlaceObject / CreateObjectByName spawns a GameObject,
/// call <see cref="MakeInteractable"/> on it.
///
/// Structural elements (names containing "Cube", "wall", "floor", "ceiling")
/// are skipped — they should remain static scenery.
/// </summary>
public class InteractionSetup : MonoBehaviour
{
    [Tooltip("Layer index to assign to interactable objects (must match XR Interactor interaction layer).")]
    public int interactionLayer = 0;

    [Tooltip("If true, grabbed objects use physics (Rigidbody kinematic=false while held).")]
    public bool usePhysicsOnGrab = false;

    /// <summary>
    /// Add Collider + Rigidbody + XRGrabInteractable to the given object so the
    /// player can point at / grab it in VR.  Safe to call multiple times — skips
    /// objects that already have an XRBaseInteractable.
    /// </summary>
    public void MakeInteractable(GameObject obj)
    {
        if (obj == null) return;
        if (IsStructural(obj.name)) return;
        if (obj.GetComponentInChildren<XRBaseInteractable>() != null) return;

        // Collider
        // If the object (or any child) already has a collider, keep it.
        // Otherwise, add a BoxCollider that wraps the entire renderer bounds.
        if (obj.GetComponentInChildren<Collider>() == null)
        {
            Bounds bounds = CalculateRendererBounds(obj);
            BoxCollider box = obj.AddComponent<BoxCollider>();
            // Transform world bounds into local space of the root object.
            box.center = obj.transform.InverseTransformPoint(bounds.center);
            box.size   = obj.transform.InverseTransformVector(bounds.size);
            // Ensure minimum size so ray-cast can hit tiny objects
            box.size = Vector3.Max(box.size, Vector3.one * 0.05f);
        }

        // Rigidbody
        Rigidbody rb = obj.GetComponent<Rigidbody>();
        if (rb == null)
        {
            rb = obj.AddComponent<Rigidbody>();
        }
        rb.isKinematic = true;
        rb.useGravity  = false;

        // XRGrabInteractable
        var grab = obj.AddComponent<XRGrabInteractable>();
        grab.movementType = usePhysicsOnGrab
            ? XRGrabInteractable.MovementType.VelocityTracking
            : XRGrabInteractable.MovementType.Instantaneous;
        grab.throwOnDetach = false;  // don't fling medical equipment

        // Keep the object where the user drops it (don't snap back)
        grab.trackPosition = true;
        grab.trackRotation = true;

        Debug.Log($"[InteractionSetup] Made interactable: {obj.name}");
    }

    /// <summary>
    /// Strip interaction components so the object becomes static scenery again.
    /// </summary>
    public void MakeNonInteractable(GameObject obj)
    {
        if (obj == null) return;
        var interactable = obj.GetComponentInChildren<XRBaseInteractable>();
        if (interactable != null) Destroy(interactable);
        // Leave Rigidbody and Collider in place — they don't hurt and removing
        // them could break physics queries (SnapToFloor, etc.).
    }

    // ---- Helpers ----

    private static bool IsStructural(string name)
    {
        if (string.IsNullOrEmpty(name)) return true;
        string lower = name.ToLowerInvariant();
        return lower.Contains("cube")
            || lower.Contains("wall")
            || lower.Contains("floor")
            || lower.Contains("ceiling")
            || lower.Contains("plane");
    }

    /// <summary>
    /// Compute a world-space AABB that encloses every Renderer in the hierarchy.
    /// Falls back to a 0.3m cube at the object's position if no renderers are found.
    /// </summary>
    private static Bounds CalculateRendererBounds(GameObject root)
    {
        Renderer[] renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0)
            return new Bounds(root.transform.position, Vector3.one * 0.3f);

        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++)
            bounds.Encapsulate(renderers[i].bounds);
        return bounds;
    }
}
