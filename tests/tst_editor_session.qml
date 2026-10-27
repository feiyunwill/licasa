import QtQuick
import QtTest
import "../qml"

TestCase {
    name: "EditorSessionHistory"

    Component {
        id: sessionComponent
        EditorSession {}
    }

    function test_equalSnapshotsIgnorePropertyOrder() {
        const session = createTemporaryObject(sessionComponent, this)
        const current = session.snapshot()
        const reordered = {}
        for (const key of Object.keys(current).reverse())
            reordered[key] = current[key]
        verify(session.snapshotsEqual(current, reordered))
        session.commitCheckpoint(reordered)
        verify(!session.canUndo)
        reordered.exposure = 0.25
        verify(!session.snapshotsEqual(current, reordered))
    }

    function test_presetPreservesGeometryAndResetIsUndoable() {
        const session = createTemporaryObject(sessionComponent, this)
        session.cropX = 0.2
        session.cropWidth = 0.5
        session.quarterTurns = 1
        session.flipHorizontal = true
        session.applyPreset("soft")
        compare(session.cropX, 0.2)
        compare(session.cropWidth, 0.5)
        compare(session.quarterTurns, 1)
        compare(session.flipHorizontal, true)
        const values = session.editValues()
        compare(values.soften, session.blur)
        verify(values.blur === undefined)
        values.cropX = 0.9
        compare(session.cropX, 0.2)
        session.reset()
        verify(!session.hasAdjustments)
        session.undo()
        compare(session.cropX, 0.2)
        compare(session.blur, 0.08)
        session.redo()
        verify(!session.hasAdjustments)
    }

    function test_historyRemainsBoundedAndNewEditsDiscardRedo() {
        const session = createTemporaryObject(sessionComponent, this)
        for (let i = 1; i <= 60; ++i) {
            session.beginInteraction()
            session.setAdjustment("exposure", i / 100)
            session.finishInteraction()
        }
        compare(session.undoStack.length, 50)
        session.undo()
        verify(session.canRedo)
        session.beginInteraction()
        session.setAdjustment("contrast", 0.4)
        session.finishInteraction()
        verify(!session.canRedo)
        compare(session.undoStack.length, 50)
    }

    function test_clearRetiresDraftInteractionAndCanPreserveExportSettings() {
        const session = createTemporaryObject(sessionComponent, this)
        session.exportFormat = "webp"
        session.exportScale = 0.5
        session.exportQuality = 72
        session.beginInteraction()
        session.setAdjustment("exposure", 0.4)
        session.beginCrop()
        session.updateDraftCrop(0.2, 0.3, 0.5, 0.4)
        session.compareOriginal = true

        session.clear(false)
        session.finishInteraction()
        verify(!session.hasAdjustments)
        verify(!session.cropMode)
        verify(!session.compareOriginal)
        verify(!session.canUndo)
        verify(!session.canRedo)
        compare(session.interactionSnapshot, null)
        compare(session.draftCropX, 0)
        compare(session.draftCropY, 0)
        compare(session.draftCropWidth, 1)
        compare(session.draftCropHeight, 1)
        compare(session.exportFormat, "webp")
        compare(session.exportScale, 0.5)
        compare(session.exportQuality, 72)

        session.clear(true)
        compare(session.exportFormat, "original")
        compare(session.exportScale, 1)
        compare(session.exportQuality, 95)
    }

    function test_rotationAndMirrorCyclesPreserveCropAndHistory() {
        const session = createTemporaryObject(sessionComponent, this)
        session.beginCrop()
        session.updateDraftCrop(0.1, 0.2, 0.4, 0.6)
        session.applyDraftCrop()
        const original = session.snapshot()

        for (let turn = 0; turn < 4; ++turn)
            session.rotateRight()
        fuzzyCompare(session.cropX, original.cropX, 0.000001)
        fuzzyCompare(session.cropY, original.cropY, 0.000001)
        fuzzyCompare(session.cropWidth, original.cropWidth, 0.000001)
        fuzzyCompare(session.cropHeight, original.cropHeight, 0.000001)
        compare(session.quarterTurns, 0)

        session.toggleFlipHorizontal()
        session.toggleFlipHorizontal()
        session.toggleFlipVertical()
        session.toggleFlipVertical()
        fuzzyCompare(session.cropX, original.cropX, 0.000001)
        fuzzyCompare(session.cropY, original.cropY, 0.000001)
        verify(!session.flipHorizontal)
        verify(!session.flipVertical)

        session.rotateLeft()
        session.resetRotation()
        fuzzyCompare(session.cropX, original.cropX, 0.000001)
        fuzzyCompare(session.cropY, original.cropY, 0.000001)
        compare(session.quarterTurns, 0)
        session.undo()
        compare(session.quarterTurns, 3)
        session.redo()
        compare(session.quarterTurns, 0)
    }

    function test_cancelCropPreservesAppliedEditsAndRedo() {
        const session = createTemporaryObject(sessionComponent, this)
        session.applyPreset("soft")
        session.undo()
        verify(session.canRedo)
        session.beginCrop()
        session.updateDraftCrop(-1, 2, Infinity, NaN)
        verify(session.draftCropX >= 0 && session.draftCropX < 1)
        verify(session.draftCropY >= 0 && session.draftCropY < 1)
        verify(session.draftCropWidth > 0)
        verify(session.draftCropHeight > 0)
        verify(session.draftCropX + session.draftCropWidth <= 1)
        verify(session.draftCropY + session.draftCropHeight <= 1)
        session.cancelCrop()
        verify(!session.cropMode)
        verify(!session.hasAdjustments)
        verify(!session.canUndo)
        verify(session.canRedo)
        session.redo()
        compare(session.blur, 0.08)
    }
}
