from pathlib import Path
import unittest


SOURCE = (
    Path(__file__).resolve().parents[1] / "roblox_plugin" / "src" / "MeshSync.luau"
).read_text(encoding="utf-8")


class StudioPluginUndoSourceTests(unittest.TestCase):
    def test_replaced_instances_are_unparented_for_change_history_undo(self):
        self.assertIn("local function removeUndoably(instance: Instance)", SOURCE)
        self.assertIn("instance.Parent = nil", SOURCE)
        for destructive_call in (
            "old:Destroy()",
            "oldPart:Destroy()",
            "previousRoot:Destroy()",
            "existingVariant:Destroy()",
            "legacyGenerated:Destroy()",
            "current:Destroy()",
        ):
            self.assertNotIn(destructive_call, SOURCE)

    def test_one_export_button_routes_csg_and_ordinary_selection(self):
        self.assertEqual(SOURCE.count('Text = "Send Selection to Blender"'), 1)
        self.assertNotIn('Text = "Send Union as CSG to Blender"', SOURCE)
        self.assertNotIn("csgTreeButton", SOURCE)
        self.assertIn("local function sendCurrentSelectionToBlender()", SOURCE)
        self.assertIn("sendSelectionToBlender()", SOURCE)
        self.assertIn("local function sendCsgSelectionToBlender(selectedOperations: { Instance }?, ordinaryTransfer: any?)", SOURCE)
        self.assertIn("instance:GetDescendants()", SOURCE)
        self.assertIn("sendCsgSelectionToBlender(operations, {", SOURCE)
        self.assertNotIn("sendCompletedCsgMeshesToBlender", SOURCE)
        self.assertNotIn("Content.fromObject(operation)", SOURCE)
        self.assertIn('part:FindFirstChildWhichIsA("DataModelMesh")', SOURCE)
        self.assertIn("record.meshScale", SOURCE)
        self.assertIn("record.meshOffset", SOURCE)
        self.assertIn("record.specialMeshType", SOURCE)
        self.assertIn('Sphere = "Ball"', SOURCE)
        self.assertIn("record.meshHash = meshHashOrError", SOURCE)
        self.assertIn("所有権または共有権限の関係で読み取れないため、Blenderへ送信しません", SOURCE)
        self.assertIn("phase1Document.meshBlobs", SOURCE)
        self.assertIn("Blenderには送信していません", SOURCE)
        self.assertIn("local cachedError = unreadableMeshSources[source]", SOURCE)
        self.assertNotIn('recordSkip(part, "Unreadable MeshPart"', SOURCE)
        self.assertNotIn("Union／Intersectと通常オブジェクトの同時送信には未対応です。", SOURCE)
        self.assertIn("collectStudioSelection(excludedOperations, true)", SOURCE)
        self.assertIn("local function operationHierarchy(original: Instance)", SOURCE)
        self.assertIn("parentId = parentId", SOURCE)
        self.assertIn("primaryCollectionId = primaryCollectionId", SOURCE)
        self.assertIn("collectionIds = collectionIds", SOURCE)
        self.assertIn("hierarchyIds = hierarchyIds", SOURCE)
        self.assertIn("ordinaryTransfer.imageBlobs", SOURCE)
        self.assertIn('csgReference.role = "negative"', SOURCE)
        self.assertIn("supported Part or nested CSG operation", SOURCE)

    def test_complete_sync_scopes_remove_only_tracked_descendants(self):
        self.assertIn('local replaceScopes = expectTable(document.replaceScopes or {}, "replaceScopes")', SOURCE)
        self.assertIn('scope.mode ~= "REPLACE_DESCENDANTS"', SOURCE)
        self.assertIn("local protectedHierarchyIds = table.clone(replaceScopeIds)", SOURCE)
        self.assertIn("ensureHierarchy(rootFolder, hierarchy, protectedHierarchyIds)", SOURCE)
        self.assertIn('descendant:GetAttribute("BlenderObjectId")', SOURCE)
        self.assertIn("not incomingIds[objectId]", SOURCE)
        self.assertIn("#container:GetChildren() == 0", SOURCE)
        self.assertIn("removedInstances = removedInstances", SOURCE)

    def test_hierarchy_reparenting_avoids_transient_circular_references(self):
        self.assertIn("if parent:IsDescendantOf(current) then", SOURCE)
        self.assertIn("parent.Parent = current.Parent or rootFolder", SOURCE)
        self.assertIn("current.Parent = parent", SOURCE)
        self.assertIn('error("Manifest hierarchy contains a cycle")', SOURCE)
        self.assertIn('error("Manifest contains duplicate hierarchy IDs")', SOURCE)

    def test_merged_meshpart_source_ids_are_removed_undoably(self):
        self.assertIn("instanceData.replacesObjectIds or {}", SOURCE)
        self.assertIn("cannot replace an object included in the same revision", SOURCE)
        self.assertIn("Replacement object ID {replacementId} is claimed more than once", SOURCE)
        self.assertIn("replacementIds[objectId]", SOURCE)
        self.assertIn("removeUndoably(descendant)", SOURCE)

    def test_transport_roots_are_virtual_and_legacy_wrappers_are_unwrapped(self):
        self.assertIn("local rootFolder: Instance = workspace", SOURCE)
        self.assertIn("local function isGeneratedDocumentWrapper", SOURCE)
        self.assertIn('child.Parent = workspace', SOURCE)
        self.assertIn('removeUndoably(previousRoot)', SOURCE)
        self.assertIn('part:SetAttribute("BlenderDocumentModelId", modelData.id)', SOURCE)
        self.assertIn('hierarchyInstance:SetAttribute("BlenderDocumentModelId", modelId)', SOURCE)
        self.assertIn("local function virtualDocumentMetadata", SOURCE)
        self.assertNotIn('rootFolder = Instance.new("Folder")', SOURCE)
        self.assertNotIn('rootFolder.Name = expectString(modelData.name, "model.name")', SOURCE)

    def test_texture_tint_and_surface_source_are_restored(self):
        self.assertIn('appearance.textureSource = "SURFACE_APPEARANCE"', SOURCE)
        self.assertIn('appearance.textureSource = "MESHPART_TEXTURE"', SOURCE)
        self.assertIn('appearance.textureSource = "MATERIAL_VARIANT"', SOURCE)
        self.assertIn('appearance.textureSource == "SURFACE_APPEARANCE"', SOURCE)
        self.assertIn('part.Color = colorFromArray(appearance.color, Color3.new(1, 1, 1))', SOURCE)


if __name__ == "__main__":
    unittest.main()
