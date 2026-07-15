#include "MltPreviewItem.h"

#include "MltRuntime.h"

#include <QMetaObject>
#include <QMutexLocker>
#include <QQuickWindow>
#include <QSGSimpleTextureNode>
#include <QSGTexture>

#ifdef Q_OS_WIN
#include <dxgi1_6.h>
#include <windows.h>
#endif

MltPreviewItem::MltPreviewItem(QQuickItem *parent)
    : QQuickItem(parent)
    , m_runtime(new MltRuntime)
{
    setFlag(ItemHasContents, true);
    m_runtime->moveToThread(&m_workerThread);
    connect(&m_workerThread, &QThread::finished, m_runtime, &QObject::deleteLater);
    connect(this, &MltPreviewItem::openRequested, m_runtime, &MltRuntime::openGraph, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::playRequested, m_runtime, &MltRuntime::play, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::pauseRequested, m_runtime, &MltRuntime::pause, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::seekRequested, m_runtime, &MltRuntime::seek, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::playbackRateRequested, m_runtime, &MltRuntime::setPlaybackRate, Qt::QueuedConnection);
    connect(this, &MltPreviewItem::previewSizeRequested, m_runtime, &MltRuntime::setPreviewSize, Qt::QueuedConnection);
    connect(m_runtime, &MltRuntime::frameReady, this, &MltPreviewItem::receiveFrame, Qt::QueuedConnection);
    connect(m_runtime, &MltRuntime::positionChanged, this, [this](int value) {
        if (m_position != value) {
            m_position = value;
            emit positionChanged();
        }
    });
    connect(m_runtime, &MltRuntime::durationChanged, this, [this](int value) {
        if (m_duration != value) {
            m_duration = value;
            emit durationChanged();
        }
    });
    connect(m_runtime, &MltRuntime::playingChanged, this, [this](bool value) {
        if (m_playing != value) {
            m_playing = value;
            emit playingChanged();
        }
    });
    connect(m_runtime, &MltRuntime::droppedFramesChanged, this, [this](int value) {
        if (m_droppedFrames != value) {
            m_droppedFrames = value;
            emit droppedFramesChanged();
        }
    });
    connect(m_runtime, &MltRuntime::clockDriftChanged, this, [this](double value) {
        if (!qFuzzyCompare(m_clockDriftMs, value)) {
            m_clockDriftMs = value;
            emit clockDriftChanged();
        }
    });
    connect(m_runtime, &MltRuntime::audioClockActiveChanged, this, [this](bool value) {
        if (m_audioClockActive != value) {
            m_audioClockActive = value;
            emit audioClockActiveChanged();
        }
    });
    connect(m_runtime, &MltRuntime::errorOccurred, this, &MltPreviewItem::receiveError, Qt::QueuedConnection);
    m_workerThread.setObjectName(QStringLiteral("MediaFlowMltPreview"));
    m_workerThread.start();
}

MltPreviewItem::~MltPreviewItem()
{
    if (m_runtime) {
        QMetaObject::invokeMethod(m_runtime, &MltRuntime::shutdown, Qt::BlockingQueuedConnection);
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
    openIfReady();
}

void MltPreviewItem::setRuntimeRoot(const QString &value)
{
    if (m_runtimeRoot == value)
        return;
    m_runtimeRoot = value;
    emit runtimeRootChanged();
    openIfReady();
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
    openIfReady();
}

void MltPreviewItem::play()
{
    emit playRequested();
}

void MltPreviewItem::pause()
{
    emit pauseRequested();
}

void MltPreviewItem::seek(int frame)
{
    emit seekRequested(frame);
}

void MltPreviewItem::reload()
{
    openIfReady();
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
    if (!node)
        node = new QSGSimpleTextureNode;

    QSGTexture *oldTexture = node->texture();
    node->setOwnsTexture(false);
    QSGTexture *texture = window()->createTextureFromImage(image);
    node->setTexture(texture);
    node->setOwnsTexture(true);
    delete oldTexture;
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

void MltPreviewItem::receiveFrame(const QImage &image, int frame, int duration)
{
    {
        const QMutexLocker locker(&m_frameMutex);
        m_frame = image;
    }
    if (m_position != frame) {
        m_position = frame;
        emit positionChanged();
    }
    if (m_duration != duration) {
        m_duration = duration;
        emit durationChanged();
    }
    update();
}

void MltPreviewItem::receiveError(const QString &message)
{
    if (m_errorString == message)
        return;
    m_errorString = message;
    emit errorStringChanged();
}

void MltPreviewItem::openIfReady()
{
    if (!m_source.isEmpty() && !m_runtimeRoot.isEmpty()) {
        m_errorString.clear();
        emit errorStringChanged();
        const bool active = m_hdrEnabled && screenSupportsHdr();
        if (m_hdrActive != active) {
            m_hdrActive = active;
            emit hdrActiveChanged();
        }
        emit previewSizeRequested(qMax(64, qRound(width())), qMax(64, qRound(height())));
        emit openRequested(m_source, m_runtimeRoot, m_hdrEnabled, m_hdrActive);
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
