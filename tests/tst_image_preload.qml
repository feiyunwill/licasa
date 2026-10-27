import QtQuick
import QtTest
import "../qml"
import "../qml/FileUrls.js" as FileUrls

Item {
    id: visualRoot
    width: 640
    height: 480

    Component {
        id: viewportComponent
        ImageViewport {
            property url sourceFile: "file:///photo.png"
            width: 320
            height: 240
            fullScreenMode: false
            fullscreenBackdropOpacity: 0.7
            transparencyCheckerboardEnabled: false
            transparencyCheckerboardOpacity: 0.6
            windowedTransparencyCheckerboardEnabled: false
            windowedTransparencyOpacity: 0.6
            hasImage: true
            imageX: 0
            imageY: 0
            imageWidth: 640
            imageHeight: 480
            currentScale: 0.5
            providerSource: "image://previewtest/photo.png"
            previewPhase: true
            rawFastPhase: false
            rawPreviewFirst: false
            previewFirstPreloadEnabled: FileUrls.previewFirstFullResolution(sourceFile)
            parallelFullResolutionEnabled: !previewFirstPreloadEnabled
            previewSourceWidth: 160
            previewSourceHeight: 120
            fullScreenWheelSensitivity: 1.0015
            windowedWheelSensitivity: 1.0009
            maxWheelDeltaPerEvent: 120
            interactionBlocked: false
            canOpen: function() { return true }
        }
    }

    Component {
        id: mainComponent
        Main {
            currentImageUrl: ""
            residentImageUrl: ""
            windowSession: QtObject {
                property url initialImageUrl: ""
                property bool startHidden: true
                property bool persistentShell: true
                signal openRequested(url source)
            }
            imageProbe: QtObject {
                function inspect(source) {
                    return { size: Qt.size(640, 480), withinBudget: true }
                }
            }
            photoAssetProbe: QtObject {
                signal infoReady(url source, var info)
                function cancel() {}
                function request(source) {}
            }
            motionPhotoSession: null
            motionPhotoFrameSaveCoordinator: null
            formatSupport: QtObject {
                signal directoryIndexReady()
                function canOpen(source) { return true }
                function prepareDirectoryIndex(source) {}
                function hasDirectoryIndex(source) { return true }
                function nearbyImages(source, radius, direction) {
                    return ["file:///prepared-neighbor.png"]
                }
            }
            imageSaveService: QtObject {
                property bool busy: false
                signal sizeEstimateReady(url sourceUrl, int requestId, int byteCount, string errorMessage)
                signal saveCompleted(url sourceUrl, url destinationUrl)
                signal saveFailed(url sourceUrl, string message)
                signal frameSaveCompleted(url sourceUrl, url destinationUrl)
                signal frameSaveFailed(url sourceUrl, string message)
                function cancelSizeEstimate() {}
            }
            nativeWindowOps: QtObject {
                signal screensChanged()
                function gestureWindowSupported() { return false }
                function currentScreenGeometry(window) { return { x: 0, y: 0, width: 1360, height: 860 } }
                function currentWindowGeometry(window) {
                    return { x: window.x, y: window.y, width: window.width, height: window.height }
                }
                function windowDevicePixelRatio(window) { return 1 }
                function traceZoom(phase, scale) {}
                function releaseWindowResources(window) {}
                function trimProcessMemory() {}
            }
            backgroundModeManager: QtObject {
                property bool enabled: false
                property bool supported: false
                signal errorOccurred(string message)
            }
            viewerPreferences: QtObject {
                property bool startInFullscreen: true
                property real fullscreenBackgroundOpacity: 0.7
                property bool transparencyCheckerboardEnabled: false
                property real transparencyCheckerboardOpacity: 0.6
                property bool windowedTransparencyCheckerboardEnabled: false
                property real windowedTransparencyOpacity: 0.6
                property bool animatedImagePlaybackEnabled: true
                property real animationSpeed: 1
                property bool smoothImageScalingEnabled: true
                property bool mipmapImageScalingEnabled: false
                property bool pixelAlignedRenderingEnabled: false
                property bool fullResolutionRenderingEnabled: true
                property bool colorManagedRenderingEnabled: true
                property real cropShieldOpacity: 0.5
                property int maximumImageMemoryMiB: 8192
                property int minimumImageMemoryMiB: 384
                property int maximumImageMemoryLimitMiB: 32768
                property int maximumImageMegapixels: 512
                signal errorOccurred(string message)
            }
            windowManager: QtObject {
                property url defaultImageUrl: "file:///default.png"
                property var nearbyRequests: []
                property var prepared: ["file:///previous-neighbor.png"]
                signal navigationKeyReleased(var window, int direction)
                signal quitBlocked(string reason)
                function hasDecodedFullBase(source) { return false }
                function releasePictureResources() { prepared = [] }
                function releasePictureResourcesForNavigation(source) {}
                function prepareNearbyPreviews(sources, size, memory) {
                    nearbyRequests.push(sources)
                    prepared = sources
                }
            }
        }
    }

    TestCase {
        name: "ImagePreload"
        when: windowShown
        property var viewport: null
        property var mainWindow: null

        function init() { testPreviewRequests.reset() }
        function cleanup() {
            if (mainWindow) {
                mainWindow.clearImageState()
                mainWindow.destroy()
                mainWindow = null
            }
            if (viewport)
                viewport.releaseStaticImages()
            tryVerify(function() {
                return testPreviewRequests.requests().every(function(request) {
                    return request.finished
                })
            }, 5000)
            viewport = null
        }
        function makeMain() {
            mainWindow = mainComponent.createObject(null)
            verify(mainWindow)
            const image = findChild(mainWindow, "imageViewport")
            verify(image)
            // Keep the real Main hidden while the controlled provider and
            // explicit frameSwapped signal establish the presentation order.
            image.sourceOverride = "image://previewtest/main?licasa_stage=full"
            image.sourceOverrideSize = Qt.size(640, 480)
            image.animatedSource = ""
            for (let childIndex = 0; childIndex < mainWindow.data.length; ++childIndex) {
                const child = mainWindow.data[childIndex]
                if (!child.contentItem)
                    continue
                for (let itemIndex = 0; itemIndex < child.contentItem.children.length; ++itemIndex) {
                    const item = child.contentItem.children[itemIndex]
                    if (item.animatedSource !== undefined)
                        item.animatedSource = ""
                }
            }
            return mainWindow
        }

        function test_mainDefersNeighborsUntilSelectedFrame() {
            const main = makeMain()
            const manager = main.windowManager
            main.loadImageUrl("file:///selected.png", true)
            main.formatSupport.directoryIndexReady()
            wait(0)
            compare(manager.nearbyRequests.length, 0)
            compare(manager.prepared.length, 1)
            main.frameSwapped()
            wait(0)
            compare(manager.nearbyRequests.length, 0)

            release(requests(1)[0])
            tryCompare(main, "nearbyPreviewsReadyForFrame", true, 5000)
            wait(0)
            compare(main.visible, false)
            compare(manager.nearbyRequests.length, 0)
            compare(manager.prepared[0], "file:///previous-neighbor.png")
            main.displayedFileName = "stale.png"
            main.frameSwapped()
            wait(0)
            compare(manager.nearbyRequests.length, 0)
            main.displayedFileName = main.currentFileName
            main.frameSwapped()
            tryCompare(main, "nearbyPreviewsAwaitingFrame", false)
            tryVerify(function() { return manager.nearbyRequests.length === 1 })
            compare(manager.prepared[0], "file:///prepared-neighbor.png")
        }

        function test_mainLoadAndClearRetirePendingNeighborRefresh() {
            const main = makeMain()
            const manager = main.windowManager
            main.loadImageUrl("file:///selected.png", true)
            release(requests(1)[0])
            tryCompare(main, "nearbyPreviewsReadyForFrame", true, 5000)
            main.formatSupport.directoryIndexReady()
            wait(0)
            compare(manager.nearbyRequests.length, 0)
            main.loadImageUrl("file:///replacement.png", true)
            compare(main.nearbyPreviewsReadyForFrame, false)
            main.frameSwapped()
            wait(0)
            compare(manager.nearbyRequests.length, 0)
            findChild(main, "imageViewport").sourceOverride =
                "image://previewtest/replacement?licasa_stage=full"
            tryVerify(function() {
                return testPreviewRequests.requests().some(function(request) {
                    return request.source.indexOf("replacement") >= 0
                        && !request.cancelled && !request.finished
                })
            }, 5000)
            release(testPreviewRequests.requests().filter(function(request) {
                return request.source.indexOf("replacement") >= 0
                    && !request.cancelled && !request.finished
            })[0])
            tryCompare(main, "nearbyPreviewsReadyForFrame", true, 5000)
            main.formatSupport.directoryIndexReady()
            wait(0)
            compare(main.nearbyPreviewsRefreshPending, true)
            compare(manager.nearbyRequests.length, 0)
            main.clearImageState()
            compare(main.nearbyPreviewsAwaitingFrame, false)
            compare(main.nearbyPreviewsRefreshPending, false)
            main.frameSwapped()
            wait(0)
            compare(manager.nearbyRequests.length, 0)
        }
        function make(sourceFile) {
            const source = sourceFile || "file:///photo.png"
            viewport = createTemporaryObject(viewportComponent, visualRoot, {
                sourceFile: source,
                providerSource: "image://previewtest/" + FileUrls.fileName(source)
            })
            verify(viewport)
            return viewport
        }
        function requests(count) {
            tryVerify(function() { return testPreviewRequests.requests().length >= count }, 5000)
            return testPreviewRequests.requests()
        }
        function stage(request) { return FileUrls.queryValue(request.source, "licasa_stage") }
        function release(request, fail) {
            verify(testPreviewRequests.release(request.id, fail === true))
        }
        function readyPreview() {
            const preview = requests(1)[0]
            compare(stage(preview), "preview")
            release(preview)
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            return requests(2)[1]
        }

        function test_formatPolicy_data() {
            return [
                { tag: "png", url: "file:///UPPER.PNG", previewFirst: true },
                { tag: "ppm", url: "file:///photo.ppm", previewFirst: true },
                { tag: "pnm", url: "file:///photo.pnm", previewFirst: true },
                { tag: "pgm", url: "file:///photo.pgm", previewFirst: true },
                { tag: "pbm", url: "file:///photo.pbm", previewFirst: true },
                { tag: "raw", url: "file:///photo.dng", previewFirst: false },
                { tag: "jpeg", url: "file:///photo.jpg", previewFirst: false }
            ]
        }
        function test_formatPolicy(data) {
            make(data.url)
            compare(viewport.previewFirstPreloadEnabled, data.previewFirst)
            const first = requests(data.previewFirst ? 1 : 2)
            if (data.previewFirst) {
                compare(first.length, 1)
                compare(stage(first[0]), "preview")
                compare(FileUrls.queryValue(first[0].source, "licasa_parallel"), "")
                compare(viewport.speculativeFullKey, "")
                compare(String(findChild(viewport, "staticImageC").source), "")
            } else {
                compare(first.length, 2)
                verify(first.some(function(request) {
                    return FileUrls.queryValue(request.source, "licasa_parallel") === "preview"
                }))
                verify(first.some(function(request) {
                    return FileUrls.queryValue(request.source, "licasa_parallel") === "full"
                }))
            }
        }

        function test_previewStartsFullBeforeReadySignalAndReusesC() {
            make()
            const c = findChild(viewport, "staticImageC")
            const readyStates = []
            viewport.imageReady.connect(function() {
                readyStates.push({ slot: viewport.activeImageSlot, full: String(c.source) })
            })
            const first = requests(1)
            compare(first.length, 1)
            compare(String(c.source), "")
            const full = readyPreview()
            compare(readyStates.length, 1)
            verify(readyStates[0].slot !== 2)
            compare(readyStates[0].full, viewport.fullRequestKey())
            compare(stage(full), "full")
            compare(FileUrls.queryValue(full.source, "licasa_parallel"), "")
            const key = viewport.fullRequestKey()
            release(full)
            tryCompare(c, "status", Image.Ready, 5000)
            verify(viewport.activeImageSlot !== 2)
            viewport.previewPhase = false
            compare(viewport.fullRequestKey(), key)
            tryCompare(viewport, "activeImageSlot", 2, 5000)
            compare(String(c.source), key)
            compare(testPreviewRequests.requests().length, 2)
            compare(viewport.activeParallelPairId, "")
        }

        function test_navigationCancelsOldPreloadAndIgnoresItsCompletion() {
            make()
            const oldFull = readyPreview()
            viewport.providerSource = "image://previewtest/next.png"
            const next = requests(3)[2]
            tryVerify(function() { return testPreviewRequests.requests()[1].cancelled }, 5000)
            verify(viewport.activeRequestKey.indexOf("photo.png") >= 0)
            release(next)
            tryVerify(function() { return viewport.activeRequestKey.indexOf("next.png") >= 0 }, 5000)
            const newFull = requests(4)[3]
            verify(newFull.source.indexOf("next.png") >= 0)
            verify(viewport.activeImageSlot !== 2)
            compare(FileUrls.queryValue(newFull.source, "licasa_parallel"), "")
            // Releasing an already cancelled response cannot activate its old C raster.
            testPreviewRequests.release(oldFull.id, false)
            verify(viewport.activeRequestKey.indexOf("next.png") >= 0)
            release(newFull)
            viewport.previewPhase = false
            tryCompare(viewport, "activeImageSlot", 2, 5000)
            verify(viewport.activeRequestKey.indexOf("next.png") >= 0)
        }

        function test_clearCancelsFullWithoutReopening() {
            make()
            readyPreview()
            viewport.releaseStaticImages()
            tryVerify(function() { return testPreviewRequests.requests()[1].cancelled }, 5000)
            compare(viewport.activeImageSlot, -1)
            compare(viewport.pendingImageSlot, -1)
            compare(viewport.speculativeFullKey, "")
            compare(String(findChild(viewport, "staticImageC").source), "")
            compare(viewport.imageStatus, Image.Loading)
            compare(testPreviewRequests.requests().length, 2)
        }

        function test_enablingAfterBrowsePreloadsWithoutRepeatingPreview() {
            make()
            viewport.previewFirstPreloadEnabled = false
            viewport.parallelFullResolutionEnabled = false
            const preview = requests(1)[0]
            release(preview)
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            compare(testPreviewRequests.requests().length, 1)
            const key = viewport.activeRequestKey
            viewport.previewFirstPreloadEnabled = true
            const full = requests(2)[1]
            compare(stage(full), "full")
            compare(viewport.activeRequestKey, key)
            viewport.previewFirstPreloadEnabled = false
            tryVerify(function() { return testPreviewRequests.requests()[1].cancelled }, 5000)
            compare(testPreviewRequests.requests().length, 2)
            viewport.previewFirstPreloadEnabled = true
            const resumed = requests(3)[2]
            compare(stage(resumed), "full")
            compare(viewport.activeRequestKey, key)
            release(resumed)
            viewport.previewPhase = false
            tryCompare(viewport, "activeImageSlot", 2, 5000)
            compare(testPreviewRequests.requests().length, 3)
        }

        function test_modeChangeWaitsForSameTurnSourcePublication() {
            make("file:///photo.jpg")
            viewport.parallelFullResolutionEnabled = false
            release(requests(1)[0])
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            compare(testPreviewRequests.requests().length, 1)
            viewport.sourceFile = "file:///next.png"
            // Main publishes selected-file metadata before its new provider key.
            // That change must not synchronously preload the previous picture.
            compare(String(findChild(viewport, "staticImageC").source), "")
            viewport.providerSource = "image://previewtest/next.png"
            const next = requests(2)[1]
            verify(next.source.indexOf("next.png") >= 0)
            compare(stage(next), "preview")
            release(next)
            const full = requests(3)[2]
            verify(full.source.indexOf("next.png") >= 0)
            compare(stage(full), "full")
            release(full)
            viewport.previewPhase = false
            tryCompare(viewport, "activeImageSlot", 2, 5000)
        }

        function test_navigationPreviewIsUnpairedWithoutCurrentEagerFull() {
            make("file:///photo.dng")
            const pair = requests(2)
            const preview = pair.filter(function(request) { return stage(request) === "preview" })[0]
            release(preview)
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            viewport.providerSource = "image://previewtest/next.dng"
            const next = requests(3)[2]
            compare(stage(next), "preview")
            compare(FileUrls.queryValue(next.source, "licasa_parallel"), "")
            compare(FileUrls.queryValue(next.source, "licasa_pair"), "")
            release(next)
            tryVerify(function() { return viewport.activeRequestKey.indexOf("next.dng") >= 0 }, 5000)
            compare(testPreviewRequests.requests().length, 3)
        }

        function test_sourceOverrideSizeChangeReloadsSameUrl() {
            make()
            viewport.providerSource = ""
            viewport.sourceOverride = "image://previewtest/override?licasa_stage=preview"
            viewport.sourceOverrideSize = Qt.size(160, 120)
            const initial = requests(1)[0]
            compare(initial.width, 160)
            compare(initial.height, 120)
            release(initial)
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            const key = viewport.activeRequestKey
            viewport.sourceOverrideSize = Qt.size(320, 240)
            const replacement = requests(2)[1]
            compare(replacement.source, initial.source)
            compare(replacement.width, 320)
            compare(replacement.height, 240)
            release(replacement)
            tryVerify(function() { return viewport.pendingImageSlot === -1 }, 5000)
            compare(viewport.activeRequestKey, key)
            compare(testPreviewRequests.requests().length, 2)
        }

        function test_fullErrorFallsBackWhileKeepingReadyPreview() {
            make()
            const full = readyPreview()
            const previewSlot = viewport.activeImageSlot
            release(full, true)
            tryCompare(findChild(viewport, "staticImageC"), "status", Image.Error, 5000)
            viewport.previewPhase = false
            const fallback = requests(3)[2]
            compare(stage(fallback), "full")
            compare(viewport.activeImageSlot, previewSlot)
            compare(viewport.imageStatus, Image.Ready)
            release(fallback)
            tryVerify(function() { return viewport.pendingImageSlot === -1 }, 5000)
            verify(viewport.activeImageSlot !== previewSlot)
            compare(viewport.imageStatus, Image.Ready)
        }

        function test_promotedFullFailureKeepsPresentedPreview() {
            make()
            const full = readyPreview()
            const previewKey = viewport.activeRequestKey
            const previewSlot = viewport.activeImageSlot
            let failures = 0
            viewport.imageRequestFailed.connect(function() { failures += 1 })

            viewport.previewPhase = false
            tryCompare(viewport, "pendingImageSlot", 2, 5000)
            release(full, true)
            tryCompare(viewport, "pendingImageSlot", -1, 5000)
            compare(failures, 1)
            compare(viewport.imageStatus, Image.Ready)
            compare(viewport.activeRequestKey, previewKey)
            compare(viewport.activeImageSlot, previewSlot)
            compare(testPreviewRequests.requests().length, 2)
        }

        function test_failedReplacementKeepsPresentedPreviewAndAllowsNextOpen() {
            make()
            viewport.previewFirstPreloadEnabled = false
            viewport.parallelFullResolutionEnabled = false
            release(requests(1)[0])
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            const previewKey = viewport.activeRequestKey
            let failures = 0
            viewport.imageRequestFailed.connect(function() { failures += 1 })

            viewport.providerSource = "image://previewtest/failed.png"
            const replacement = requests(2)[1]
            release(replacement, true)
            tryCompare(viewport, "pendingImageSlot", -1, 5000)
            compare(failures, 1)
            compare(viewport.imageStatus, Image.Ready)
            compare(viewport.activeRequestKey, previewKey)

            viewport.providerSource = "image://previewtest/retry.png"
            release(requests(3)[2])
            tryVerify(function() {
                return viewport.activeRequestKey.indexOf("retry.png") >= 0
            }, 5000)
            compare(viewport.imageStatus, Image.Ready)
            compare(failures, 1)
        }

        function test_newPreviewCanReuseFadingFullSlot() {
            make()
            release(readyPreview())
            viewport.previewPhase = false
            tryCompare(viewport, "activeImageSlot", 2, 5000)
            viewport.previewPhase = true
            viewport.providerSource = "image://previewtest/another.png"
            const preview = requests(3)[2]
            release(preview)
            const full = requests(4)[3]
            verify(viewport.activeImageSlot !== 2)
            verify(viewport.fadingImageSlot !== 2)
            verify(String(findChild(viewport, "staticImageC").source).indexOf("another.png") >= 0)
            release(full)
            viewport.previewPhase = false
            tryCompare(viewport, "activeImageSlot", 2, 5000)
            verify(viewport.activeRequestKey.indexOf("another.png") >= 0)
        }

        function test_presentedPairComesFromActiveRasterDespiteNewGeneration() {
            make("file:///photo.dng")
            const pair = requests(2)
            const preview = pair.filter(function(request) { return stage(request) === "preview" })[0]
            const presented = FileUrls.queryValue(preview.source, "licasa_pair")
            release(preview)
            tryCompare(viewport, "imageStatus", Image.Ready, 5000)
            compare(viewport.activeParallelPairId, presented)
            viewport.parallelPairGeneration += 7
            verify(viewport.parallelPairId !== presented)
            compare(viewport.activeParallelPairId, presented)
            viewport.releaseStaticImages()
            compare(viewport.activeParallelPairId, "")
        }
    }
}
