#include "MltPreviewItem.h"

#include "MltRuntime.h"

#include <QDir>
#include <QFileInfo>
#include <QLibrary>
#include <QMetaObject>
#include <QMutexLocker>
#include <QQuickWindow>
#include <QSet>
#include <QSGSimpleTextureNode>
#include <QtQuick/private/qsgplaintexture_p.h>

#include <memory>
#include <vector>

#ifdef Q_OS_WIN
#include <dxgi1_6.h>
#include <windows.h>
#endif

namespace {
#ifdef Q_OS_LINUX
struct LinuxRuntimeLibraries final
{
    QMutex mutex;
    QSet<QString> preparedDirectories;
    std::vector<std::unique_ptr<QLibrary>> libraries;
};

LinuxRuntimeLibraries &linuxRuntimeLibraries()
{
    static LinuxRuntimeLibraries state;
    return state;
}

void preloadMltRuntimeLibraries(const QString &mltLibrary)
{
    const QFileInfo mltLibraryInfo(mltLibrary);
    if (!mltLibraryInfo.isFile())
        return;
    const QString directoryPath = mltLibraryInfo.absolutePath();
    LinuxRuntimeLibraries &state = linuxRuntimeLibraries();
    const QMutexLocker locker(&state.mutex);
    if (state.preparedDirectories.contains(directoryPath))
        return;

    QStringList filters;
    filters << QStringLiteral("*.so") << QStringLiteral("*.so.*");
    QSet<QString> pending;
    const QFileInfoList entries = QDir(directoryPath).entryInfoList(
        filters,
        QDir::Files,
        QDir::Name);
    for (const QFileInfo &entry : entries) {
        if (entry.fileName().startsWith(QStringLiteral("libQt")))
            continue;
        const QString canonical = entry.canonicalFilePath();
        pending.insert(canonical.isEmpty() ? entry.absoluteFilePath() : canonical);
    }

    bool madeProgress = true;
    while (madeProgress && !pending.isEmpty()) {
        madeProgress = false;
        QStringList candidates = pending.values();
        candidates.sort(Qt::CaseSensitive);
        for (const QString &candidate : candidates) {
            auto library = std::make_unique<QLibrary>(candidate);
            library->setLoadHints(
                QLibrary::ExportExternalSymbolsHint
                | QLibrary::PreventUnloadHint);
            if (!library->load())
                continue;
            pending.remove(candidate);
            state.libraries.push_back(std::move(library));
            madeProgress = true;
        }
    }
    state.preparedDirectories.insert(directoryPath);
}
#else
void preloadMltRuntimeLibraries(const QString &)
{
}
#endif
}

MltPreviewItem::MltPreviewItem(QQuickItem *parent)
    : QQuickItem(parent)
    , m_runtime(new MltRuntime)
{
    setFlag(ItemHasContents, true);
    m_runtime->moveToThread(&m_workerThread);
    connect(this, &MltPreviewItem::openRequested, m_runtime, &MltRuntime::openGraph, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::closeRequested, m_runtime, &MltRuntime::close, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::playRequested, m_runtime, &MltRuntime::play, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::playRangeRequested, m_runtime, &MltRuntime::playRange, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::pauseRequested, m_runtime, &MltRuntime::pause, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::seekRequested, m_runtime, &MltRuntime::seek, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::playbackRateRequested, m_runtime, &MltRuntime::setPlaybackRate, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::volumeRequested, m_runtime, &MltRuntime::setVolume, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::previewSizeRequested, m_runtime, &MltRuntime::setPreviewSize, Qt::QueuedConnection);
    m_seekRetryTimer.setInterval(50);
    m_seekRetryTimer.setSingleShot(true);
    connect(&m_seekRetryTimer, &QTimer::timeout, this, [this]() {
        if (!m_seekPending)
            return;
        if (m_duration <= 0) {
            m_seekRetryTimer.start();
            return;
        }
        const int expectedFrame = qBound(0, m_requestedPosition, m_duration - 1);
        if (m_position == expectedFrame) {
            m_seekPending = false;
            m_seekRetryAttempts = 0;
            return;
        }
        if (m_seekRetryAttempts >= 80)
            return;
        ++m_seekRetryAttempts;
        emit seekRequested(expectedFrame, m_requestId.load(std::memory_order_acquire));
        m_seekRetryTimer.start();
    });
    connect(m_runtime, &MltRuntime::frameReady, this, &MltPreviewItem::queueFrame, Qt::DirectConnection);
    connect(m_runtime, &MltRuntime::durationChanged, this, [this](int value, quint64 requestId) {
        if (requestId != m_requestId.load(std::memory_order_acquire))
            return;
        if (m_duration != value) {
            m_duration = value;
            emit durationChanged();
        }
    });
    connect(m_runtime, &MltRuntime::playingChanged, this, [this](bool value, quint64 requestId) {
        if (requestId != m_requestId.load(std::memory_order_acquire))
            return;
        m_queuePlaybackFrames.store(value, std::memory_order_release);
        if (m_playing != value) {
            m_playing = value;
            m_lastPlaybackFrame = -1;
            emit playingChanged();
        }
    });
    connect(
        m_runtime,
        &MltRuntime::bufferStateChanged,
        this,
        [this](bool buffering, int bufferedFrames, quint64 requestId) {
            if (requestId != m_requestId.load(std::memory_order_acquire))
                return;
            if (m_buffering != buffering) {
                m_buffering = buffering;
                emit bufferingChanged();
            }
            if (m_bufferedFrames != bufferedFrames) {
                m_bufferedFrames = bufferedFrames;
                emit bufferedFramesChanged();
            }
        });
    connect(
        m_runtime,
        &MltRuntime::framesDropped,
        this,
        [this](int count, quint64 requestId) {
            if (requestId != m_requestId.load(std::memory_order_acquire) || count <= 0)
                return;
            m_pendingDroppedFrames.fetch_add(count, std::memory_order_acq_rel);
        },
        Qt::DirectConnection);
    connect(m_runtime, &MltRuntime::errorOccurred, this, &MltPreviewItem::receiveError, Qt::QueuedConnection);
    m_workerThread.setObjectName(QStringLiteral("MediaFlowMltPreview"));
    m_workerThread.start();
}

MltPreviewItem::~MltPreviewItem()
{
    if (m_runtime) {
        MltRuntime *runtime = m_runtime;
        m_runtime = nullptr;
        QMetaObject::invokeMethod(
            runtime,
            [runtime]() {
                runtime->shutdown();
                delete runtime;
            },
            Qt::BlockingQueuedConnection);
    }
    m_workerThread.quit();
    m_workerThread.wait();
}

void MltPreviewItem::setSource(const QString &value)
{
    if (m_source == value)
        return;
    m_source = value;
    emit sourceChanged();
    if (m_source.isEmpty()) {
        const quint64 requestId = beginRequest(false);
        emit closeRequested(requestId);
        return;
    }
    scheduleOpen();
}

void MltPreviewItem::setRuntimeRoot(const QString &value)
{
    if (m_runtimeRoot == value)
        return;
    m_runtimeRoot = value;
    emit runtimeRootChanged();
    scheduleOpen();
}

void MltPreviewItem::setMltLibrary(const QString &value)
{
    if (m_mltLibrary == value)
        return;
    preloadMltRuntimeLibraries(value);
    m_mltLibrary = value;
    emit mltLibraryChanged();
    scheduleOpen();
}

void MltPreviewItem::setMltRepository(const QString &value)
{
    if (m_mltRepository == value)
        return;
    m_mltRepository = value;
    emit mltRepositoryChanged();
    scheduleOpen();
}

void MltPreviewItem::setMltData(const QString &value)
{
    if (m_mltData == value)
        return;
    m_mltData = value;
    emit mltDataChanged();
    scheduleOpen();
}

void MltPreviewItem::setReloadToken(int value)
{
    if (m_reloadToken == value)
        return;
    m_reloadToken = value;
    emit reloadTokenChanged();
    scheduleOpen();
}

void MltPreviewItem::setPlaybackRate(double value)
{
    value = qBound(-4.0, value, 4.0);
    if (qFuzzyIsNull(value))
        value = 1.0;
    if (qFuzzyCompare(m_playbackRate, value))
        return;
    m_playbackRate = value;
    emit playbackRateChanged();
    emit playbackRateRequested(value);
}

void MltPreviewItem::setVolume(double value)
{
    value = qBound(0.0, value, 1.0);
    if (qFuzzyCompare(m_volume, value))
        return;
    m_volume = value;
    emit volumeChanged();
    emit volumeRequested(value);
}

void MltPreviewItem::setHdrEnabled(bool value)
{
    if (m_hdrEnabled == value)
        return;
    m_hdrEnabled = value;
    emit hdrEnabledChanged();
    const bool active = value && screenSupportsHdr();
    if (m_hdrActive != active) {
        m_hdrActive = active;
        emit hdrActiveChanged();
    }
    scheduleOpen();
}

void MltPreviewItem::play()
{
    clearError();
    m_queuePlaybackFrames.store(true, std::memory_order_release);
    emit playRequested(m_requestId.load(std::memory_order_acquire));
}

void MltPreviewItem::playRange(int startFrame, int endFrame)
{
    clearError();
    m_requestedPosition = qMax(0, startFrame);
    m_seekPending = false;
    m_seekRetryAttempts = 0;
    m_seekRetryTimer.stop();
    m_queuePlaybackFrames.store(true, std::memory_order_release);
    emit playRangeRequested(
        startFrame,
        endFrame,
        m_requestId.load(std::memory_order_acquire));
}

void MltPreviewItem::pause()
{
    emit pauseRequested(m_requestId.load(std::memory_order_acquire));
}

void MltPreviewItem::seek(int frame)
{
    clearError();
    m_requestedPosition = qMax(0, frame);
    m_seekPending = true;
    m_seekRetryAttempts = 0;
    emit seekRequested(frame, m_requestId.load(std::memory_order_acquire));
    m_seekRetryTimer.start();
}

void MltPreviewItem::reload()
{
    scheduleOpen();
}

QSGNode *MltPreviewItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    QImage image;
    {
        const QMutexLocker locker(&m_frameMutex);
        image = m_frame;
    }
    if (image.isNull() || !window()) {
        delete oldNode;
        return nullptr;
    }

    auto *node = static_cast<QSGSimpleTextureNode *>(oldNode);
    QSGPlainTexture *texture = nullptr;
    if (!node) {
        node = new QSGSimpleTextureNode;
        texture = new QSGPlainTexture;
        node->setTexture(texture);
        node->setOwnsTexture(true);
    } else {
        texture = static_cast<QSGPlainTexture *>(node->texture());
    }
    texture->setImage(image);
    texture->setFiltering(QSGTexture::Linear);
    node->markDirty(QSGNode::DirtyMaterial);
    const QSizeF source = image.size();
    QSizeF fitted = source;
    fitted.scale(boundingRect().size(), Qt::KeepAspectRatio);
    const QPointF origin(
        (width() - fitted.width()) / 2.0,
        (height() - fitted.height()) / 2.0);
    node->setRect(QRectF(origin, fitted));
    return node;
}

void MltPreviewItem::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    if (newGeometry.size() != oldGeometry.size()) {
        emit previewSizeRequested(
            qMax(64, qRound(newGeometry.width())),
            qMax(64, qRound(newGeometry.height())));
    }
}

void MltPreviewItem::queueFrame(
    const QImage &image,
    int frame,
    int duration,
    quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    bool scheduleDelivery = false;
    int evictedFrames = 0;
    {
        const QMutexLocker locker(&m_frameMutex);
        if (!m_queuePlaybackFrames.load(std::memory_order_acquire)) {
            m_pendingFrames.clear();
        } else {
            while (m_pendingFrames.size() >= MaxPendingPlaybackFrames) {
                m_pendingFrames.dequeue();
                ++evictedFrames;
            }
        }
        m_pendingFrames.enqueue(PendingFrame{image, frame, duration, requestId});
        if (!m_frameDeliveryScheduled) {
            m_frameDeliveryScheduled = true;
            scheduleDelivery = true;
        }
    }
    if (evictedFrames > 0)
        m_pendingDroppedFrames.fetch_add(evictedFrames, std::memory_order_acq_rel);
    if (scheduleDelivery) {
        QMetaObject::invokeMethod(
            this,
            &MltPreviewItem::deliverPendingFrame,
            Qt::QueuedConnection);
    }
}

void MltPreviewItem::deliverPendingFrame()
{
    PendingFrame pending;
    bool scheduleNext = false;
    {
        const QMutexLocker locker(&m_frameMutex);
        if (m_pendingFrames.isEmpty()) {
            m_frameDeliveryScheduled = false;
            return;
        }
        pending = m_pendingFrames.dequeue();
        scheduleNext = !m_pendingFrames.isEmpty();
        if (!scheduleNext)
            m_frameDeliveryScheduled = false;
    }
    if (scheduleNext) {
        QMetaObject::invokeMethod(
            this,
            &MltPreviewItem::deliverPendingFrame,
            Qt::QueuedConnection);
    }
    if (pending.requestId != m_requestId.load(std::memory_order_acquire))
        return;
    if (m_playing && qFuzzyCompare(qAbs(m_playbackRate), 1.0)) {
        const int queuedDropped = m_pendingDroppedFrames.exchange(
            0,
            std::memory_order_acq_rel);
        int dropped = 0;
        if (m_lastPlaybackFrame >= 0) {
            int observedDropped = pending.image.isNull() ? 1 : 0;
            const int direction = m_playbackRate < 0.0 ? -1 : 1;
            const int advance = (pending.position - m_lastPlaybackFrame) * direction;
            if (advance > 1)
                observedDropped = qMax(observedDropped, advance - 1);
            dropped = qMax(observedDropped, queuedDropped);
        }
        m_lastPlaybackFrame = pending.position;
        if (dropped > 0) {
            m_droppedFrames += dropped;
            emit droppedFramesChanged();
        }
    }
    if (m_position != pending.position) {
        m_position = pending.position;
        emit positionChanged();
    }
    const int expectedFrame = qBound(0, m_requestedPosition, qMax(0, pending.duration - 1));
    if (!m_seekPending || pending.position == expectedFrame) {
        m_requestedPosition = pending.position;
        m_seekPending = false;
        m_seekRetryAttempts = 0;
        m_seekRetryTimer.stop();
    }
    if (m_duration != pending.duration) {
        m_duration = pending.duration;
        emit durationChanged();
    }
    if (!pending.image.isNull()) {
        {
            const QMutexLocker locker(&m_frameMutex);
            if (pending.requestId != m_requestId.load(std::memory_order_acquire))
                return;
            m_frame = pending.image;
        }
        update();
    }
}

void MltPreviewItem::receiveError(const QString &message, quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    m_queuePlaybackFrames.store(false, std::memory_order_release);
    m_pendingDroppedFrames.store(0, std::memory_order_release);
    if (m_errorString == message)
        return;
    m_errorString = message;
    if (m_buffering) {
        m_buffering = false;
        emit bufferingChanged();
    }
    if (m_bufferedFrames != 0) {
        m_bufferedFrames = 0;
        emit bufferedFramesChanged();
    }
    emit errorStringChanged();
}

quint64 MltPreviewItem::beginRequest(bool preservePosition)
{
    if (preservePosition && !m_seekPending)
        m_requestedPosition = m_position;
    else if (!preservePosition) {
        m_requestedPosition = 0;
        m_seekPending = false;
        m_seekRetryAttempts = 0;
        m_seekRetryTimer.stop();
    }
    const quint64 requestId = m_requestId.fetch_add(1, std::memory_order_acq_rel) + 1;
    resetPresentationState(preservePosition);
    return requestId;
}

void MltPreviewItem::resetPresentationState(bool preservePosition)
{
    {
        const QMutexLocker locker(&m_frameMutex);
        m_frame = QImage();
        m_pendingFrames.clear();
        m_frameDeliveryScheduled = false;
    }
    m_queuePlaybackFrames.store(false, std::memory_order_release);
    m_pendingDroppedFrames.store(0, std::memory_order_release);
    if (m_playing) {
        m_playing = false;
        emit playingChanged();
    }
    if (m_buffering) {
        m_buffering = false;
        emit bufferingChanged();
    }
    if (m_bufferedFrames != 0) {
        m_bufferedFrames = 0;
        emit bufferedFramesChanged();
    }
    if (!preservePosition && m_position != 0) {
        m_position = 0;
        emit positionChanged();
    }
    if (m_duration != 0) {
        m_duration = 0;
        emit durationChanged();
    }
    if (m_droppedFrames != 0) {
        m_droppedFrames = 0;
        emit droppedFramesChanged();
    }
    m_lastPlaybackFrame = -1;
    clearError();
    update();
}

void MltPreviewItem::clearError()
{
    if (m_errorString.isEmpty())
        return;
    m_errorString.clear();
    emit errorStringChanged();
}

void MltPreviewItem::scheduleOpen(bool preservePosition)
{
    beginRequest(preservePosition);
    if (m_openScheduled)
        return;
    m_openScheduled = true;
    QMetaObject::invokeMethod(
        this,
        [this]() {
            m_openScheduled = false;
            openIfReady();
        },
        Qt::QueuedConnection);
}

void MltPreviewItem::openIfReady()
{
    if (!m_source.isEmpty()
        && !m_runtimeRoot.isEmpty()
        && !m_mltLibrary.isEmpty()
        && !m_mltRepository.isEmpty()
        && !m_mltData.isEmpty()) {
        clearError();
        const bool active = m_hdrEnabled && screenSupportsHdr();
        if (m_hdrActive != active) {
            m_hdrActive = active;
            emit hdrActiveChanged();
        }
        emit previewSizeRequested(qMax(64, qRound(width())), qMax(64, qRound(height())));
        emit openRequested(
            m_source,
            m_runtimeRoot,
            m_mltLibrary,
            m_mltRepository,
            m_mltData,
            m_hdrEnabled,
            m_hdrActive,
            m_requestedPosition,
            m_requestId.load(std::memory_order_acquire));
    }
}

bool MltPreviewItem::screenSupportsHdr() const
{
#ifdef Q_OS_WIN
    if (!window())
        return false;
    const HWND hwnd = reinterpret_cast<HWND>(window()->winId());
    const HMONITOR monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    IDXGIFactory1 *factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void **>(&factory))))
        return false;
    bool supported = false;
    for (UINT adapterIndex = 0; !supported; ++adapterIndex) {
        IDXGIAdapter1 *adapter = nullptr;
        if (factory->EnumAdapters1(adapterIndex, &adapter) == DXGI_ERROR_NOT_FOUND)
            break;
        for (UINT outputIndex = 0; !supported; ++outputIndex) {
            IDXGIOutput *output = nullptr;
            if (adapter->EnumOutputs(outputIndex, &output) == DXGI_ERROR_NOT_FOUND)
                break;
            IDXGIOutput6 *output6 = nullptr;
            if (SUCCEEDED(output->QueryInterface(
                    __uuidof(IDXGIOutput6), reinterpret_cast<void **>(&output6)))) {
                DXGI_OUTPUT_DESC1 description{};
                if (SUCCEEDED(output6->GetDesc1(&description))
                    && description.Monitor == monitor
                    && description.BitsPerColor >= 10
                    && description.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020) {
                    supported = true;
                }
                output6->Release();
            }
            output->Release();
        }
        adapter->Release();
    }
    factory->Release();
    return supported;
#else
    return false;
#endif
}
