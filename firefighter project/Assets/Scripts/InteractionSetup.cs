using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

/// <summary>
/// Adds XR interaction components to objects so they can be hovered and grabbed in VR,
/// which in turn lets <see cref="InteractionEventReporter"/> and
/// <see cref="InteractionAnimationController"/> receive interaction events for them.
///
/// <see cref="EnvironmentAgent"/> calls <see cref="MakeInteractable"/> on every object
/// spawned by PlaceObject / CreateObjectByName, and InteractionAnimationController can
/// call it on pre-placed scene objects at startup.
///
/// Structural elements (names containing "cube", "wall", "floor", "ceiling" or "plane",
/// case-insensitive) are skipped so they remain static scenery.
///
/// Setup: added automatically to the EnvironmentAgent GameObject (AI_Manager) through
/// <c>RequireComponent</c>.
/// </summary>
public class InteractionSetup : MonoBehaviour
{
    /// <summary>Intended interaction layer index for interactable objects. Currently not applied by this component.</summary>
    [Tooltip("Interaction layer index for interactable objects (should match the XR Interactor). Currently not applied by this component.")]
    public int interactionLayer = 0;

    /// <summary>If true, grabbed objects follow the hand with velocity tracking (physics) instead of instantaneous movement.</summary>
    [Tooltip("If true, grabbed objects move with velocity tracking (physics) instead of instantaneous movement.")]
    public bool usePhysicsOnGrab = false;

    /// <summary>
    /// Adds a Collider (if none exists), a kinematic Rigidbody without gravity and an
    /// XRGrabInteractable to <paramref name="obj"/> so the player can point at and grab
    /// it in VR. Safe to call multiple times: objects that already have an
    /// XRBaseInteractable, and structural objects, are skipped.
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
            // Enforce a minimum size so ray casts can hit tiny objects.
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
        grab.throwOnDetach = false;  // Don't fling medical equipment on release.

        // Keep the object where the user drops it (don't snap back).
        grab.trackPosition = true;
        grab.trackRotation = true;

        Debug.Log($"[InteractionSetup] Made interactable: {obj.name}");
    }

    /// <summary>
    /// Removes the first XRBaseInteractable found on the object or its children so it
    /// becomes static scenery again.
    /// </summary>
    public void MakeNonInteractable(GameObject obj)
    {
        if (obj == null) return;
        var interactable = obj.GetComponentInChildren<XRBaseInteractable>();
        if (interactable != null) Destroy(interactable);
        // Leave the Rigidbody and Collider in place: they do no harm, and removing
        // them could break physics queries (SnapToFloor, etc.).
    }

    // ---- Helpers ----

    /// <summary>
    /// Returns true for empty names and for names containing "cube", "wall", "floor",
    /// "ceiling" or "plane" (case-insensitive).
    /// </summary>
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
    /// Computes a world-space AABB that encloses every Renderer in the hierarchy.
    /// Falls back to a 0.3 m cube at the object's position if no renderers are found.
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
