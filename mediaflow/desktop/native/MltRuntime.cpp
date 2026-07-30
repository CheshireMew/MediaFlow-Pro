#include "MltRuntime.h"

#include <QByteArray>
#include <QColorSpace>
#include <QDir>
#include <QFileInfo>
#include <QMetaObject>
#include <QMutex>
#include <QMutexLocker>
#include <QSize>
#include <QTimer>
#include <QtMath>

#include <utility>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace {
struct MltProcessState final
{
    QMutex mutex;
    QString runtimeRoot;
    void *repository = nullptr;
};

MltProcessState &mltProcessState()
{
    static MltProcessState state;
    return state;
}

class ScopedEnvironmentVariable final
{
public:
    ScopedEnvironmentVariable(const char *name, const QByteArray &value)
        : m_name(name)
        , m_hadPreviousValue(qEnvironmentVariableIsSet(name))
        , m_previousValue(qgetenv(name))
    {
        qputenv(m_name.constData(), value);
#ifdef Q_OS_WIN
        const QString wideName = QString::fromLatin1(name);
        const QString wideValue = QString::fromUtf8(value);
        SetEnvironmentVariableW(
            reinterpret_cast<LPCWSTR>(wideName.utf16()),
            reinterpret_cast<LPCWSTR>(wideValue.utf16()));
        setLegacyCrtEnvironment(wideName, wideValue);
#endif
    }

    ~ScopedEnvironmentVariable()
    {
        if (m_hadPreviousValue)
            qputenv(m_name.constData(), m_previousValue);
        else
            qunsetenv(m_name.constData());
#ifdef Q_OS_WIN
        const QString wideName = QString::fromLatin1(m_name);
        const QString wideValue = QString::fromUtf8(m_previousValue);
        SetEnvironmentVariableW(
            reinterpret_cast<LPCWSTR>(wideName.utf16()),
            m_hadPreviousValue ? reinterpret_cast<LPCWSTR>(wideValue.utf16()) : nullptr);
        setLegacyCrtEnvironment(
            wideName,
            m_hadPreviousValue ? wideValue : QString());
#endif
    }

    ScopedEnvironmentVariable(const ScopedEnvironmentVariable &) = delete;
    ScopedEnvironmentVariable &operator=(const ScopedEnvironmentVariable &) = delete;

private:
#ifdef Q_OS_WIN
    static void setLegacyCrtEnvironment(const QString &name, const QString &value)
    {
        const HMODULE runtime = LoadLibraryW(L"msvcrt.dll");
        if (!runtime)
            return;
        using WPutEnv = int (__cdecl *)(const wchar_t *, const wchar_t *);
        const auto putEnvironment = reinterpret_cast<WPutEnv>(
            GetProcAddress(runtime, "_wputenv_s"));
        if (putEnvironment) {
            putEnvironment(
                reinterpret_cast<const wchar_t *>(name.utf16()),
                reinterpret_cast<const wchar_t *>(value.utf16()));
        }
        FreeLibrary(runtime);
    }
#endif

    QByteArray m_name;
    bool m_hadPreviousValue;
    QByteArray m_previousValue;
};
}

#ifdef Q_OS_WIN
namespace {
class ScopedDllDirectory final
{
public:
    explicit ScopedDllDirectory(const QString &directory)
    {
        if (directory.isEmpty())
            return;

        wchar_t previousDirectory[32768]{};
        const DWORD length = GetDllDirectoryW(
            static_cast<DWORD>(sizeof(previousDirectory) / sizeof(previousDirectory[0])),
            previousDirectory);
        if (length > 0) {
            m_hadPreviousDirectory = true;
            m_previousDirectory = QString::fromWCharArray(previousDirectory, length);
        }
        m_active = SetDllDirectoryW(reinterpret_cast<LPCWSTR>(directory.utf16())) != 0;
    }

    ~ScopedDllDirectory()
    {
        if (!m_active)
            return;
        SetDllDirectoryW(
            m_hadPreviousDirectory
                ? reinterpret_cast<LPCWSTR>(m_previousDirectory.utf16())
                : nullptr);
    }

    ScopedDllDirectory(const ScopedDllDirectory &) = delete;
    ScopedDllDirectory &operator=(const ScopedDllDirectory &) = delete;

private:
    QString m_previousDirectory;
    bool m_hadPreviousDirectory = false;
    bool m_active = false;
};
}
#endif

MltRuntime::MltRuntime(QObject *parent)
    : QObject(parent)
    , m_presentationTimer(new QTimer(this))
{
    m_presentationTimer->setSingleShot(true);
    m_presentationTimer->setTimerType(Qt::PreciseTimer);
    connect(
        m_presentationTimer,
        &QTimer::timeout,
        this,
        &MltRuntime::presentNextFrame);
}

MltRuntime::~MltRuntime()
{
    shutdown();
}

bool MltRuntime::loadApi(const QString &runtimeRoot)
{
    if (m_library.isLoaded())
        return true;

    const QDir root(runtimeRoot);
    const QString libraryPath = root.filePath(QStringLiteral("libmlt-7.dll"));
    if (!QFileInfo::exists(libraryPath)) {
        emit errorOccurred(
            QStringLiteral("MLT runtime was not found: %1").arg(libraryPath),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }

    m_library.setFileName(libraryPath);
    m_library.setLoadHints(
        QLibrary::ResolveAllSymbolsHint | QLibrary::PreventUnloadHint);
    if (!m_library.load()) {
        emit errorOccurred(
            QStringLiteral("Unable to load MLT runtime: %1").arg(m_library.errorString()),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }

    m_api = Api{};
    const bool resolved = resolve(m_api.factoryInit, "mlt_factory_init")
        && resolve(m_api.profileInit, "mlt_profile_init")
        && resolve(m_api.profileClose, "mlt_profile_close")
        && resolve(m_api.factoryProducer, "mlt_factory_producer")
        && resolve(m_api.factoryConsumer, "mlt_factory_consumer")
        && resolve(m_api.producerService, "mlt_producer_service")
        && resolve(m_api.producerSeek, "mlt_producer_seek")
        && resolve(m_api.producerLength, "mlt_producer_get_length")
        && resolve(m_api.producerFps, "mlt_producer_get_fps")
        && resolve(m_api.producerSetSpeed, "mlt_producer_set_speed")
        && resolve(m_api.producerClose, "mlt_producer_close")
        && resolve(m_api.serviceGetFrame, "mlt_service_get_frame")
        && resolve(m_api.frameGetImage, "mlt_frame_get_image")
        && resolve(m_api.frameGetPosition, "mlt_frame_get_position")
        && resolve(m_api.frameClose, "mlt_frame_close")
        && resolve(m_api.consumerProperties, "mlt_consumer_properties")
        && resolve(m_api.consumerConnect, "mlt_consumer_connect")
        && resolve(m_api.consumerStart, "mlt_consumer_start")
        && resolve(m_api.consumerStop, "mlt_consumer_stop")
        && resolve(m_api.consumerPurge, "mlt_consumer_purge")
        && resolve(m_api.consumerClose, "mlt_consumer_close")
        && resolve(m_api.propertiesSet, "mlt_properties_set")
        && resolve(m_api.propertiesSetInt, "mlt_properties_set_int")
        && resolve(m_api.eventsListen, "mlt_events_listen")
        && resolve(m_api.eventDataToFrame, "mlt_event_data_to_frame");
    if (!resolved) {
        m_library.unload();
        m_api = Api{};
    }
    return resolved;
}

void MltRuntime::openGraph(
    const QString &graphPath,
    const QString &runtimeRoot,
    bool sourceHdr,
    bool outputHdr,
    int initialFrame,
    quint64 requestId)
{
    m_requestId.store(requestId, std::memory_order_release);
    setPlaying(false);
#ifdef Q_OS_WIN
    const ScopedDllDirectory previousDllSearch(m_runtimeRoot);
#endif
    closeGraph();
    const QString normalizedRuntimeRoot = QDir(runtimeRoot).absolutePath();
    m_runtimeRoot = normalizedRuntimeRoot;
    emit durationChanged(0, requestId);
    if (!QFileInfo(graphPath).isFile()) {
        emit errorOccurred(QStringLiteral("MLT graph was not found: %1").arg(graphPath), requestId);
        return;
    }

#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    const QDir runtime(m_runtimeRoot);
    const QString repositoryPath = QFileInfo::exists(runtime.filePath(QStringLiteral("lib/mlt-preview")))
        ? runtime.filePath(QStringLiteral("lib/mlt-preview"))
        : runtime.filePath(QStringLiteral("lib/mlt"));
    const QByteArray encodedRepository = QDir::toNativeSeparators(repositoryPath).toUtf8();
    const QByteArray encodedData = QDir::toNativeSeparators(
        runtime.filePath(QStringLiteral("share/mlt"))).toUtf8();
    const ScopedEnvironmentVariable mltData("MLT_DATA", encodedData);
    const ScopedEnvironmentVariable repositoryDeny(
        "MLT_REPOSITORY_DENY",
        QByteArrayLiteral("libmltqt6:libmltglaxnimate-qt6"));
    if (!loadApi(m_runtimeRoot))
        return;

    m_sourceHdr = sourceHdr;
    m_outputHdr = outputHdr;
    m_frameSourceHdr.store(sourceHdr, std::memory_order_release);
    m_frameOutputHdr.store(outputHdr, std::memory_order_release);
    {
        MltProcessState &processState = mltProcessState();
        const QMutexLocker locker(&processState.mutex);
        if (processState.repository
            && processState.runtimeRoot != normalizedRuntimeRoot) {
            emit errorOccurred(
                QStringLiteral("MLT runtime cannot change while the application is running"),
                requestId);
            return;
        }
        if (!processState.repository) {
            processState.repository = m_api.factoryInit(encodedRepository.constData());
            if (!processState.repository) {
                emit errorOccurred(QStringLiteral("MLT factory initialization failed"), requestId);
                return;
            }
            processState.runtimeRoot = normalizedRuntimeRoot;
        }
        m_repository = processState.repository;
    }
    m_profile = m_api.profileInit(nullptr);
    if (!m_profile) {
        emit errorOccurred(QStringLiteral("MLT profile initialization failed"), requestId);
        closeGraph();
        return;
    }

    const QByteArray encodedPath = QDir::toNativeSeparators(graphPath).toUtf8();
    m_producer = m_api.factoryProducer(m_profile, "xml", encodedPath.constData());
    if (!m_producer) {
        emit errorOccurred(QStringLiteral("MLT could not open the compiled timeline graph"), requestId);
        closeGraph();
        return;
    }

    m_duration = qMax(1, static_cast<int>(m_api.producerLength(m_producer)));
    m_frameDuration.store(m_duration, std::memory_order_release);
    m_fps = m_api.producerFps(m_producer);
    if (m_fps <= 0.0)
        m_fps = 30.0;
    m_position = 0;
    m_playbackStart = 0;
    m_playbackEnd = m_duration;
    if (!decodeStillFrame(qBound(0, initialFrame, m_duration - 1)))
        return;
    emit durationChanged(m_duration, requestId);
}

void MltRuntime::play(quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    if (!m_producer || m_playing)
        return;
    m_playbackStart = 0;
    m_playbackEnd = m_duration;
    if (m_pendingSeekFrame >= 0) {
        const int pendingFrame = std::exchange(m_pendingSeekFrame, -1);
        m_seekScheduled = false;
        if (!seekImmediately(pendingFrame))
            return;
    }
    startConfiguredPlayback();
}

void MltRuntime::playRange(int startFrame, int endFrame, quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    if (!m_producer)
        return;
    if (m_pendingSeekFrame >= 0) {
        const int pendingFrame = std::exchange(m_pendingSeekFrame, -1);
        m_seekScheduled = false;
        if (!seekImmediately(pendingFrame))
            return;
    }
    m_playbackStart = qBound(0, startFrame, m_duration - 1);
    m_playbackEnd = qBound(m_playbackStart + 1, endFrame, m_duration);
    if (m_playing) {
        setPlaying(false);
        closePlaybackConsumer();
    }
    startConfiguredPlayback();
}

void MltRuntime::pause(quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    if (!m_playing && !m_consumer)
        return;
    setPlaying(false);
    closePlaybackConsumer();
}

void MltRuntime::seek(int frame, quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    if (!m_producer)
        return;
    m_pendingSeekFrame = qBound(0, frame, m_duration - 1);
    if (m_seekScheduled)
        return;
    m_seekScheduled = true;
    QMetaObject::invokeMethod(
        this,
        &MltRuntime::performPendingSeek,
        Qt::QueuedConnection);
}

void MltRuntime::performPendingSeek()
{
    m_seekScheduled = false;
    if (!m_producer || m_pendingSeekFrame < 0)
        return;
    const int frame = std::exchange(m_pendingSeekFrame, -1);
    seekImmediately(frame);
}

bool MltRuntime::seekImmediately(int frame)
{
    if (!m_producer)
        return false;
    const bool resume = m_playing;
    if (resume)
        setPlaying(false);
    closePlaybackConsumer();
    m_position = qBound(0, frame, m_duration - 1);
    if (!decodeStillFrame(m_position))
        return false;
    if (resume)
        startConfiguredPlayback();
    return true;
}

bool MltRuntime::startConfiguredPlayback()
{
    if (!m_producer || m_playing)
        return false;
    m_playbackStart = qBound(0, m_playbackStart, m_duration - 1);
    m_playbackEnd = qBound(m_playbackStart + 1, m_playbackEnd, m_duration);
    if (m_rate > 0.0
        && (m_position < m_playbackStart || m_position >= m_playbackEnd - 1)
        && !seekImmediately(m_playbackStart)) {
        return false;
    }
    if (m_rate < 0.0
        && (m_position <= m_playbackStart || m_position >= m_playbackEnd)
        && !seekImmediately(m_playbackEnd - 1)) {
        return false;
    }
    if (m_playbackEnd - m_playbackStart == 1) {
        if (m_position != m_playbackStart)
            return seekImmediately(m_playbackStart);
        return true;
    }
    if (!startPlaybackConsumer())
        return false;
    setPlaying(true);
    return true;
}

void MltRuntime::setPlaybackRate(double rate)
{
    if (qFuzzyIsNull(rate))
        rate = 1.0;
    const double bounded = qBound(-4.0, rate, 4.0);
    if (qFuzzyCompare(m_rate, bounded))
        return;
    const bool resume = m_playing;
    if (resume)
        pause(m_requestId.load(std::memory_order_acquire));
    m_rate = bounded;
    if (resume)
        startConfiguredPlayback();
}

void MltRuntime::setVolume(double volume)
{
    m_volume = qBound(0.0, volume, 1.0);
    if (m_consumer) {
        const QByteArray encodedVolume = QByteArray::number(m_volume, 'f', 4);
        m_api.propertiesSet(
            m_api.consumerProperties(m_consumer),
            "volume",
            encodedVolume.constData());
    }
}

void MltRuntime::setPreviewSize(int width, int height)
{
    QSize requested(qMax(64, width), qMax(64, height));
    requested.scale(QSize(640, 360), Qt::KeepAspectRatio);
    m_previewWidth = requested.width();
    m_previewHeight = requested.height();
    m_frameWidth.store(m_previewWidth, std::memory_order_release);
    m_frameHeight.store(m_previewHeight, std::memory_order_release);
    if (m_consumer) {
        MltProperties properties = m_api.consumerProperties(m_consumer);
        m_api.propertiesSetInt(properties, "width", m_previewWidth);
        m_api.propertiesSetInt(properties, "height", m_previewHeight);
    }
}

void MltRuntime::close(quint64 requestId)
{
    if (requestId < m_requestId.load(std::memory_order_acquire))
        return;
    m_requestId.store(requestId, std::memory_order_release);
    setPlaying(false);
#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    closeGraph();
}

void MltRuntime::shutdown()
{
    setPlaying(false);
#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    closeGraph();
    m_repository = nullptr;
    m_api = Api{};
    m_runtimeRoot.clear();
}

void MltRuntime::closeGraph()
{
    m_pendingSeekFrame = -1;
    m_seekScheduled = false;
    closePlaybackConsumer();
    if (m_producer && m_api.producerClose)
        m_api.producerClose(m_producer);
    m_producer = nullptr;
    if (m_profile && m_api.profileClose)
        m_api.profileClose(m_profile);
    m_profile = nullptr;
    m_position = 0;
    m_duration = 0;
    m_frameDuration.store(0, std::memory_order_release);
    m_playbackStart = 0;
    m_playbackEnd = 0;
}

bool MltRuntime::startPlaybackConsumer()
{
    closePlaybackConsumer();
    if (!m_producer || !m_profile)
        return false;

#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    if (m_api.producerSeek(m_producer, m_position) != 0) {
        emit errorOccurred(
            QStringLiteral("MLT seek failed at frame %1").arg(m_position),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    m_api.producerSetSpeed(m_producer, m_rate);
    m_consumer = m_api.factoryConsumer(m_profile, "sdl2_audio", nullptr);
    if (!m_consumer)
        m_consumer = m_api.factoryConsumer(m_profile, "rtaudio", nullptr);
    if (!m_consumer) {
        emit errorOccurred(
            QStringLiteral("MLT could not initialize an audio playback consumer"),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }

    MltProperties properties = m_api.consumerProperties(m_consumer);
    m_api.propertiesSetInt(properties, "real_time", -2);
    const int playbackBufferFrames = qBound(
        24,
        qRound(m_fps),
        60);
    m_api.propertiesSetInt(properties, "buffer", playbackBufferFrames);
    m_api.propertiesSetInt(properties, "prefill", qMin(4, playbackBufferFrames));
    m_api.propertiesSetInt(properties, "width", m_previewWidth);
    m_api.propertiesSetInt(properties, "height", m_previewHeight);
    m_api.propertiesSetInt(properties, "progressive", 1);
    m_api.propertiesSetInt(properties, "frequency", 48000);
    m_api.propertiesSetInt(properties, "channels", 2);
    m_api.propertiesSetInt(properties, "audio_buffer", 2048);
    m_api.propertiesSetInt(properties, "scrub_audio", 0);
    m_api.propertiesSetInt(properties, "audio_off", qFuzzyCompare(m_rate, 1.0) ? 0 : 1);
    m_api.propertiesSet(properties, "mlt_audio_format", "s16");
    m_api.propertiesSet(properties, "mlt_image_format", m_sourceHdr ? "rgba64" : "rgba");
    m_api.propertiesSet(properties, "rescale", "bilinear");
    const QByteArray encodedVolume = QByteArray::number(m_volume, 'f', 4);
    m_api.propertiesSet(properties, "volume", encodedVolume.constData());

    m_renderSequence.store(0, std::memory_order_release);
    m_renderQueueCapacity.store(playbackBufferFrames * 2, std::memory_order_release);
    m_nextPresentationSequence = 0;
    m_nextPresentationDeadlineNs = 0;
    m_presentationGeneration = -1;
    m_currentDeadlineMisses = 0;
    m_presentationStarted = false;
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        m_renderedFrames.clear();
    }
    m_renderEvent = m_api.eventsListen(
        properties,
        this,
        "consumer-frame-render",
        &MltRuntime::onConsumerFrameRendered);
    m_showEvent = m_api.eventsListen(
        properties,
        this,
        "consumer-frame-show",
        &MltRuntime::onConsumerPlaybackStarted);
    if (!m_renderEvent
        || !m_showEvent
        || m_api.consumerConnect(m_consumer, m_api.producerService(m_producer)) != 0
        || m_api.consumerStart(m_consumer) != 0) {
        emit errorOccurred(
            QStringLiteral("MLT audio playback consumer failed to start"),
            m_requestId.load(std::memory_order_acquire));
        closePlaybackConsumer();
        return false;
    }
    return true;
}

void MltRuntime::closePlaybackConsumer()
{
    m_consumerGeneration.fetch_add(1, std::memory_order_acq_rel);
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        m_renderQueueNotFull.wakeAll();
    }
    if (m_presentationTimer)
        m_presentationTimer->stop();
    m_presentationGeneration = -1;
    m_currentDeadlineMisses = 0;
    m_presentationStarted = false;
    if (m_producer)
        m_api.producerSetSpeed(m_producer, 0.0);
    if (m_consumer) {
        if (m_api.consumerPurge)
            m_api.consumerPurge(m_consumer);
        if (m_api.consumerStop)
            m_api.consumerStop(m_consumer);
    }
    if (m_consumer && m_api.consumerClose)
        m_api.consumerClose(m_consumer);
    m_consumer = nullptr;
    m_renderEvent = nullptr;
    m_showEvent = nullptr;
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        m_renderedFrames.clear();
        m_renderQueueNotFull.wakeAll();
    }
}

bool MltRuntime::decodeStillFrame(int frameNumber)
{
    if (!m_producer)
        return false;
    m_api.producerSetSpeed(m_producer, 0.0);
    if (m_api.producerSeek(m_producer, frameNumber) != 0) {
        emit errorOccurred(
            QStringLiteral("MLT seek failed at frame %1").arg(frameNumber),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    MltFrame frame = nullptr;
    if (m_api.serviceGetFrame(m_api.producerService(m_producer), &frame, 0) != 0 || !frame) {
        emit errorOccurred(
            QStringLiteral("MLT could not decode frame %1").arg(frameNumber),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    QImage image;
    const bool imageReady = readFrameImage(frame, frameNumber, image);
    m_api.frameClose(frame);
    if (!imageReady)
        return false;
    m_position = frameNumber;
    const quint64 requestId = m_requestId.load(std::memory_order_acquire);
    emit frameReady(image, frameNumber, m_duration, requestId);
    return true;
}

bool MltRuntime::readFrameImage(MltFrame frame, int position, QImage &result)
{
    std::uint8_t *pixels = nullptr;
    const bool sourceHdr = m_frameSourceHdr.load(std::memory_order_acquire);
    const bool outputHdr = m_frameOutputHdr.load(std::memory_order_acquire);
    MltImageFormat format = sourceHdr ? ImageRgba64 : ImageRgba;
    int width = m_frameWidth.load(std::memory_order_acquire);
    int height = m_frameHeight.load(std::memory_order_acquire);
    const int imageResult = m_api.frameGetImage(frame, &pixels, &format, &width, &height, 0);
    if (imageResult != 0 || !pixels || width <= 0 || height <= 0) {
        emit errorOccurred(
            QStringLiteral("MLT returned an invalid image for frame %1").arg(position),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    if (sourceHdr && format == ImageRgba64) {
        QImage image(pixels, width, height, width * 8, QImage::Format_RGBA64);
        image.setColorSpace(QColorSpace(QColorSpace::Bt2100Pq));
        const QColorSpace outputColor(
            outputHdr ? QColorSpace::SRgbLinear : QColorSpace::SRgb);
        const QImage::Format outputFormat = outputHdr
            ? QImage::Format_RGBA16FPx4_Premultiplied
            : QImage::Format_RGBX8888;
        result = image.convertedToColorSpace(outputColor, outputFormat);
    } else {
        const QImage image(pixels, width, height, width * 4, QImage::Format_RGBX8888);
        result = image.copy();
    }
    return !result.isNull();
}

void MltRuntime::onConsumerFrameRendered(
    MltProperties,
    void *listenerData,
    MltEventData eventData)
{
    auto *runtime = static_cast<MltRuntime *>(listenerData);
    if (!runtime)
        return;
    MltFrame frame = runtime->m_api.eventDataToFrame(eventData);
    if (!frame)
        return;

    const int generation = runtime->m_consumerGeneration.load(std::memory_order_acquire);
    const quint64 sequence = runtime->m_renderSequence.fetch_add(
        1,
        std::memory_order_acq_rel);
    const int position = qBound(
        0,
        static_cast<int>(runtime->m_api.frameGetPosition(frame)),
        qMax(0, runtime->m_frameDuration.load(std::memory_order_acquire) - 1));
    QImage image;
    runtime->readFrameImage(frame, position, image);
    {
        QMutexLocker locker(&runtime->m_renderedFramesMutex);
        const int capacity = runtime->m_renderQueueCapacity.load(std::memory_order_acquire);
        while (runtime->m_renderedFrames.size() >= capacity
               && generation
                   == runtime->m_consumerGeneration.load(std::memory_order_acquire)) {
            runtime->m_renderQueueNotFull.wait(&runtime->m_renderedFramesMutex);
        }
        if (generation != runtime->m_consumerGeneration.load(std::memory_order_acquire))
            return;
        runtime->m_renderedFrames.insert(
            sequence,
            RenderedFrame{std::move(image), position});
    }
}

void MltRuntime::onConsumerPlaybackStarted(
    MltProperties,
    void *listenerData,
    MltEventData)
{
    auto *runtime = static_cast<MltRuntime *>(listenerData);
    if (!runtime)
        return;
    const int generation = runtime->m_consumerGeneration.load(std::memory_order_acquire);
    const int previousGeneration = runtime->m_presentationStartGeneration.exchange(
        generation,
        std::memory_order_acq_rel);
    if (previousGeneration == generation)
        return;
    QMetaObject::invokeMethod(
        runtime,
        [runtime, generation]() {
            runtime->beginPresentation(generation);
        },
        Qt::QueuedConnection);
}

void MltRuntime::beginPresentation(int generation)
{
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_consumer
        || !m_playing
        || m_presentationGeneration == generation) {
        return;
    }
    m_presentationGeneration = generation;
    m_nextPresentationSequence = 0;
    m_nextPresentationDeadlineNs = 0;
    m_currentDeadlineMisses = 0;
    m_presentationStarted = false;
    presentNextFrame();
}

void MltRuntime::presentNextFrame()
{
    const int generation = m_presentationGeneration;
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_consumer
        || !m_playing) {
        return;
    }

    if (m_presentationStarted) {
        const qint64 remainingNs = m_nextPresentationDeadlineNs
            - m_presentationClock.nsecsElapsed();
        if (remainingNs > 0) {
            m_presentationTimer->start(
                qMax(1, static_cast<int>((remainingNs + 999999) / 1000000)));
            return;
        }
    }

    RenderedFrame rendered;
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        auto frame = m_renderedFrames.find(m_nextPresentationSequence);
        if (frame == m_renderedFrames.end()) {
            if (m_presentationStarted) {
                const qint64 frameIntervalNs = qMax(
                    1LL,
                    qRound64(1000000000.0 / m_fps));
                const qint64 overdueNs = m_presentationClock.nsecsElapsed()
                    - m_nextPresentationDeadlineNs;
                const int deadlineMisses = qMax(
                    0,
                    static_cast<int>(overdueNs / frameIntervalNs));
                if (deadlineMisses > m_currentDeadlineMisses) {
                    emit presentationDeadlineMissed(
                        deadlineMisses - m_currentDeadlineMisses,
                        m_requestId.load(std::memory_order_acquire));
                    m_currentDeadlineMisses = deadlineMisses;
                }
            }
            m_presentationTimer->start(1);
            return;
        }
        rendered = std::move(frame.value());
        m_renderedFrames.erase(frame);
        m_renderQueueNotFull.wakeOne();
    }

    m_currentDeadlineMisses = 0;
    if (!m_presentationStarted) {
        m_presentationClock.start();
        m_presentationStarted = true;
    }
    ++m_nextPresentationSequence;
    deliverPresentationFrame(rendered.image, rendered.position, generation);
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_consumer
        || !m_playing) {
        return;
    }

    const qint64 frameIntervalNs = qMax(
        1LL,
        qRound64(1000000000.0 / m_fps));
    const qint64 nowNs = m_presentationClock.nsecsElapsed();
    m_nextPresentationDeadlineNs += frameIntervalNs;
    if (nowNs > m_nextPresentationDeadlineNs + frameIntervalNs)
        m_nextPresentationDeadlineNs = nowNs + frameIntervalNs;
    scheduleNextPresentation();
}

void MltRuntime::scheduleNextPresentation()
{
    const qint64 remainingNs = m_nextPresentationDeadlineNs
        - m_presentationClock.nsecsElapsed();
    m_presentationTimer->start(
        qMax(1, static_cast<int>((qMax(0LL, remainingNs) + 999999) / 1000000)));
}

void MltRuntime::deliverPresentationFrame(
    const QImage &image,
    int frame,
    int generation)
{
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_consumer
        || !m_playing) {
        return;
    }

    m_position = frame;
    const quint64 requestId = m_requestId.load(std::memory_order_acquire);
    emit frameReady(image, frame, m_duration, requestId);
    if ((m_rate > 0.0 && frame >= m_playbackEnd - 1)
        || (m_rate < 0.0 && frame <= m_playbackStart)) {
        pause(requestId);
    }
}

void MltRuntime::setPlaying(bool playing)
{
    if (m_playing == playing)
        return;
    m_playing = playing;
    emit playingChanged(playing, m_requestId.load(std::memory_order_acquire));
}
