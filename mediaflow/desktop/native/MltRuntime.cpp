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

#include <iterator>
#include <utility>
#include <vector>

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
#ifdef Q_OS_WIN
        const QString wideName = QString::fromLatin1(name);
        const DWORD required = GetEnvironmentVariableW(
            reinterpret_cast<LPCWSTR>(wideName.utf16()),
            nullptr,
            0);
        if (required > 0) {
            std::vector<wchar_t> buffer(required);
            const DWORD length = GetEnvironmentVariableW(
                reinterpret_cast<LPCWSTR>(wideName.utf16()),
                buffer.data(),
                required);
            m_hadPreviousValue = length > 0;
            m_previousValue = m_hadPreviousValue
                ? QString::fromWCharArray(buffer.data(), static_cast<qsizetype>(length)).toUtf8()
                : QByteArray();
        } else {
            m_hadPreviousValue = false;
            m_previousValue.clear();
        }
#endif
        qputenv(m_name.constData(), value);
#ifdef Q_OS_WIN
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

bool MltRuntime::loadApi(const QString &mltLibrary)
{
    if (m_library.isLoaded())
        return true;
    if (!QFileInfo(mltLibrary).isFile()) {
        emit errorOccurred(
            QStringLiteral("MLT runtime library was not found: %1").arg(mltLibrary),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    m_library.setFileName(mltLibrary);
    m_library.setLoadHints(
        QLibrary::ResolveAllSymbolsHint
        | QLibrary::ExportExternalSymbolsHint
        | QLibrary::PreventUnloadHint);
    if (!m_library.load()) {
        emit errorOccurred(
            QStringLiteral("Unable to load MLT runtime from %1: %2")
                .arg(mltLibrary, m_library.errorString()),
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
        && resolve(m_api.consumerClose, "mlt_consumer_close")
        && resolve(m_api.propertiesSet, "mlt_properties_set")
        && resolve(m_api.propertiesSetInt, "mlt_properties_set_int")
        && resolve(m_api.eventsListen, "mlt_events_listen")
        && resolve(m_api.eventsDisconnect, "mlt_events_disconnect")
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
    const QString &mltLibrary,
    const QString &mltRepository,
    const QString &mltData,
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
    if (!QFileInfo(mltRepository).isDir()) {
        emit errorOccurred(
            QStringLiteral("MLT repository was not found: %1").arg(mltRepository),
            requestId);
        return;
    }
    if (!QFileInfo(mltData).isDir()) {
        emit errorOccurred(
            QStringLiteral("MLT data directory was not found: %1").arg(mltData),
            requestId);
        return;
    }
    const QByteArray encodedRepository = QDir::toNativeSeparators(mltRepository).toUtf8();
    const QByteArray encodedData = QDir::toNativeSeparators(mltData).toUtf8();
    const ScopedEnvironmentVariable repositoryEnvironment(
        "MLT_REPOSITORY",
        encodedRepository);
    const ScopedEnvironmentVariable dataEnvironment("MLT_DATA", encodedData);
    const ScopedEnvironmentVariable repositoryDeny(
        "MLT_REPOSITORY_DENY",
        QByteArrayLiteral("libmltqt6:libmltglaxnimate-qt6:libmltopencv"));
    if (!loadApi(mltLibrary))
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
    m_previewProfile = m_api.profileInit(nullptr);
    if (!m_previewProfile) {
        emit errorOccurred(QStringLiteral("MLT profile initialization failed"), requestId);
        closeGraph();
        return;
    }

    const QByteArray encodedPath = QDir::toNativeSeparators(graphPath).toUtf8();
    m_previewProducer = m_api.factoryProducer(
        m_previewProfile,
        "xml",
        encodedPath.constData());
    if (!m_previewProducer) {
        emit errorOccurred(QStringLiteral("MLT could not open the compiled timeline graph"), requestId);
        closeGraph();
        return;
    }

    m_duration = qMax(1, static_cast<int>(m_api.producerLength(m_previewProducer)));
    m_frameDuration.store(m_duration, std::memory_order_release);
    m_fps = m_api.producerFps(m_previewProducer);
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
    if (!m_previewProducer || m_playing)
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
    if (!m_previewProducer)
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
    if (!m_playing && !m_previewConsumer)
        return;
    setPlaying(false);
    closePlaybackConsumer();
}

void MltRuntime::seek(int frame, quint64 requestId)
{
    if (requestId != m_requestId.load(std::memory_order_acquire))
        return;
    if (!m_previewProducer)
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
    if (!m_previewProducer || m_pendingSeekFrame < 0)
        return;
    const int frame = std::exchange(m_pendingSeekFrame, -1);
    seekImmediately(frame);
}

bool MltRuntime::seekImmediately(int frame)
{
    if (!m_previewProducer)
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
    if (!m_previewProducer || m_playing)
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
    if (m_previewConsumer) {
        const QByteArray encodedVolume = QByteArray::number(m_volume, 'f', 4);
        m_api.propertiesSet(
            m_api.consumerProperties(m_previewConsumer),
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
    if (m_previewProducer && m_api.producerClose)
        m_api.producerClose(m_previewProducer);
    m_previewProducer = nullptr;
    if (m_previewProfile && m_api.profileClose)
        m_api.profileClose(m_previewProfile);
    m_previewProfile = nullptr;
    m_position = 0;
    m_duration = 0;
    m_frameDuration.store(0, std::memory_order_release);
    m_playbackStart = 0;
    m_playbackEnd = 0;
}

bool MltRuntime::startPlaybackConsumer()
{
    closePlaybackConsumer();
    if (!m_previewProducer || !m_previewProfile) {
        return false;
    }

#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    if (m_api.producerSeek(m_previewProducer, m_position) != 0) {
        emit errorOccurred(
            QStringLiteral("MLT seek failed at frame %1").arg(m_position),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    m_api.producerSetSpeed(m_previewProducer, m_rate);

    m_previewConsumer = m_api.factoryConsumer(m_previewProfile, "sdl2_audio", nullptr);
    if (!m_previewConsumer)
        m_previewConsumer = m_api.factoryConsumer(m_previewProfile, "rtaudio", nullptr);
    if (!m_previewConsumer) {
        emit errorOccurred(
            QStringLiteral("MLT could not initialize the preview consumer"),
            m_requestId.load(std::memory_order_acquire));
        closePlaybackConsumer();
        return false;
    }

    const int bufferFrames = qBound(24, qRound(m_fps), 60);
    MltProperties previewProperties = m_api.consumerProperties(m_previewConsumer);
    m_api.propertiesSetInt(previewProperties, "real_time", -1);
    m_api.propertiesSetInt(previewProperties, "buffer", bufferFrames);
    m_api.propertiesSetInt(previewProperties, "prefill", qMin(4, bufferFrames));
    m_api.propertiesSetInt(previewProperties, "video_off", 0);
    m_api.propertiesSetInt(previewProperties, "frequency", 48000);
    m_api.propertiesSetInt(previewProperties, "channels", 2);
    m_api.propertiesSetInt(previewProperties, "audio_buffer", 2048);
    m_api.propertiesSetInt(previewProperties, "scrub_audio", 0);
    m_api.propertiesSetInt(
        previewProperties,
        "audio_off",
        qFuzzyCompare(m_rate, 1.0) ? 0 : 1);
    m_api.propertiesSet(previewProperties, "mlt_audio_format", "s16");
    const QByteArray encodedVolume = QByteArray::number(m_volume, 'f', 4);
    m_api.propertiesSet(previewProperties, "volume", encodedVolume.constData());
    m_api.propertiesSetInt(previewProperties, "width", m_previewWidth);
    m_api.propertiesSetInt(previewProperties, "height", m_previewHeight);
    m_api.propertiesSetInt(previewProperties, "progressive", 1);
    m_api.propertiesSet(
        previewProperties,
        "mlt_image_format",
        m_sourceHdr ? "rgba64" : "rgba");
    m_api.propertiesSet(previewProperties, "rescale", "bilinear");

    m_audioClockPosition.store(m_position, std::memory_order_release);
    m_presentationStartGeneration.store(-1, std::memory_order_release);
    m_renderQueueCapacity.store(
        qBound(24, qRound(m_fps), 60),
        std::memory_order_release);
    m_presentationGeneration = -1;
    m_waitingForMissingFrame = false;
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        m_renderedFrames.clear();
    }
    m_frameShowEvent = m_api.eventsListen(
        previewProperties,
        this,
        "consumer-frame-show",
        &MltRuntime::onFrameShown);
    m_playbackConsumerActive.store(true, std::memory_order_release);
    if (!m_frameShowEvent
        || m_api.consumerConnect(
            m_previewConsumer,
            m_api.producerService(m_previewProducer)) != 0
        || m_api.consumerStart(m_previewConsumer) != 0) {
        emit errorOccurred(
            QStringLiteral("MLT preview consumer failed to start"),
            m_requestId.load(std::memory_order_acquire));
        closePlaybackConsumer();
        return false;
    }
    return true;
}

void MltRuntime::closePlaybackConsumer()
{
    m_playbackConsumerActive.store(false, std::memory_order_release);
    m_consumerGeneration.fetch_add(1, std::memory_order_acq_rel);
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        m_renderQueueNotFull.wakeAll();
    }
    m_presentationTimer->stop();
    m_presentationGeneration = -1;
    m_waitingForMissingFrame = false;
    if (m_previewConsumer && m_api.consumerStop)
        m_api.consumerStop(m_previewConsumer);
    if (m_previewProducer)
        m_api.producerSetSpeed(m_previewProducer, 0.0);
    waitForConsumerCallbacks();
    if (m_previewConsumer) {
        m_api.eventsDisconnect(m_api.consumerProperties(m_previewConsumer), this);
    }
    if (m_previewConsumer && m_api.consumerClose)
        m_api.consumerClose(m_previewConsumer);
    m_previewConsumer = nullptr;
    m_frameShowEvent = nullptr;
    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        m_renderedFrames.clear();
        m_renderQueueNotFull.wakeAll();
    }
}

bool MltRuntime::beginConsumerCallback(int &generation)
{
    if (!m_playbackConsumerActive.load(std::memory_order_acquire))
        return false;
    m_consumerCallbacksInFlight.fetch_add(1, std::memory_order_acq_rel);
    if (!m_playbackConsumerActive.load(std::memory_order_acquire)) {
        endConsumerCallback();
        return false;
    }
    generation = m_consumerGeneration.load(std::memory_order_acquire);
    return true;
}

void MltRuntime::endConsumerCallback()
{
    if (m_consumerCallbacksInFlight.fetch_sub(1, std::memory_order_acq_rel) == 1) {
        const QMutexLocker locker(&m_consumerCallbacksMutex);
        m_consumerCallbacksDrained.wakeAll();
    }
}

void MltRuntime::waitForConsumerCallbacks()
{
    QMutexLocker locker(&m_consumerCallbacksMutex);
    while (m_consumerCallbacksInFlight.load(std::memory_order_acquire) > 0)
        m_consumerCallbacksDrained.wait(&m_consumerCallbacksMutex);
}

bool MltRuntime::decodeStillFrame(int frameNumber)
{
    if (!m_previewProducer)
        return false;
    m_api.producerSetSpeed(m_previewProducer, 0.0);
    if (m_api.producerSeek(m_previewProducer, frameNumber) != 0) {
        emit errorOccurred(
            QStringLiteral("MLT seek failed at frame %1").arg(frameNumber),
            m_requestId.load(std::memory_order_acquire));
        return false;
    }
    MltFrame frame = nullptr;
    if (m_api.serviceGetFrame(
        m_api.producerService(m_previewProducer),
        &frame,
        0) != 0 || !frame) {
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

void MltRuntime::onFrameShown(
    MltProperties,
    void *listenerData,
    MltEventData eventData)
{
    auto *runtime = static_cast<MltRuntime *>(listenerData);
    if (!runtime)
        return;
    int generation = -1;
    if (!runtime->beginConsumerCallback(generation))
        return;
    MltFrame frame = runtime->m_api.eventDataToFrame(eventData);
    if (!frame) {
        runtime->endConsumerCallback();
        return;
    }

    const int position = qBound(
        0,
        static_cast<int>(runtime->m_api.frameGetPosition(frame)),
        qMax(0, runtime->m_frameDuration.load(std::memory_order_acquire) - 1));
    QImage image;
    if (!runtime->readFrameImage(frame, position, image)) {
        runtime->endConsumerCallback();
        return;
    }
    if (!runtime->m_playbackConsumerActive.load(std::memory_order_acquire)
        || generation
            != runtime->m_consumerGeneration.load(std::memory_order_acquire)) {
        runtime->endConsumerCallback();
        return;
    }
    {
        QMutexLocker locker(&runtime->m_renderedFramesMutex);
        const int capacity = runtime->m_renderQueueCapacity.load(std::memory_order_acquire);
        while (runtime->m_renderedFrames.size() >= capacity
               && runtime->m_playbackConsumerActive.load(std::memory_order_acquire)
               && generation
                   == runtime->m_consumerGeneration.load(std::memory_order_acquire)) {
            runtime->m_renderQueueNotFull.wait(&runtime->m_renderedFramesMutex);
        }
        if (runtime->m_playbackConsumerActive.load(std::memory_order_acquire)
            && generation
                == runtime->m_consumerGeneration.load(std::memory_order_acquire)) {
            runtime->m_renderedFrames.insert(
                position,
                RenderedFrame{std::move(image), position});
        }
    }
    runtime->m_audioClockPosition.store(position, std::memory_order_release);
    const int previousGeneration = runtime->m_presentationStartGeneration.exchange(
        generation,
        std::memory_order_acq_rel);
    if (previousGeneration == generation) {
        runtime->endConsumerCallback();
        return;
    }

    QMetaObject::invokeMethod(
        runtime,
        [runtime, generation]() {
            runtime->beginPresentation(generation);
        },
        Qt::QueuedConnection);
    runtime->endConsumerCallback();
}

void MltRuntime::beginPresentation(int generation)
{
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_previewConsumer
        || !m_playing
        || m_presentationGeneration == generation) {
        return;
    }
    m_presentationGeneration = generation;
    m_expectedPresentationPosition = m_position;
    m_lastPresentationPosition = -1;
    m_waitingForMissingFrame = false;
    m_nextCadenceDeadlineNs = 0;
    m_cadenceClock.start();
    setBufferState(true, 0);
    presentNextFrame();
}

void MltRuntime::presentNextFrame()
{
    const int generation = m_presentationGeneration;
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_previewConsumer
        || !m_playing) {
        return;
    }

    const int direction = m_rate < 0.0 ? -1 : 1;
    const bool unitStep = qFuzzyCompare(qAbs(m_rate), 1.0);
    const int audioPosition = m_audioClockPosition.load(std::memory_order_acquire);
    const qint64 nominalIntervalNs = qMax(
        1LL,
        qRound64(1000000000.0 / m_fps));
    const int nominalIntervalMs = qMax(
        1,
        static_cast<int>((nominalIntervalNs + 999999) / 1000000));
    const int allowedLeadFrames = qMax(1, qCeil(qAbs(m_rate)));
    RenderedFrame rendered;
    bool frameReady = false;

    {
        const QMutexLocker locker(&m_renderedFramesMutex);
        if (unitStep) {
            if (direction > 0) {
                while (!m_renderedFrames.isEmpty()
                       && m_renderedFrames.firstKey() < m_expectedPresentationPosition) {
                    m_renderedFrames.erase(m_renderedFrames.begin());
                }
            } else {
                while (!m_renderedFrames.isEmpty()
                       && m_renderedFrames.lastKey() > m_expectedPresentationPosition) {
                    m_renderedFrames.erase(std::prev(m_renderedFrames.end()));
                }
            }
        } else if (m_lastPresentationPosition >= 0) {
            if (direction > 0) {
                while (!m_renderedFrames.isEmpty()
                       && m_renderedFrames.firstKey() < m_lastPresentationPosition) {
                    m_renderedFrames.erase(m_renderedFrames.begin());
                }
            } else {
                while (!m_renderedFrames.isEmpty()
                       && m_renderedFrames.lastKey() > m_lastPresentationPosition) {
                    m_renderedFrames.erase(std::prev(m_renderedFrames.end()));
                }
            }
        }

        auto candidate = m_renderedFrames.end();
        if (unitStep)
            candidate = m_renderedFrames.find(m_expectedPresentationPosition);
        else if (!m_renderedFrames.isEmpty())
            candidate = direction > 0
                ? m_renderedFrames.begin()
                : std::prev(m_renderedFrames.end());

        if (candidate == m_renderedFrames.end() && unitStep && !m_renderedFrames.isEmpty()) {
            auto future = direction > 0
                ? m_renderedFrames.lowerBound(m_expectedPresentationPosition)
                : m_renderedFrames.upperBound(m_expectedPresentationPosition);
            if (direction < 0) {
                if (future == m_renderedFrames.begin())
                    future = m_renderedFrames.end();
                else
                    --future;
            }
            if (future != m_renderedFrames.end()) {
                if (!m_waitingForMissingFrame) {
                    m_missingFrameDeadline = QDeadlineTimer(
                        nominalIntervalMs,
                        Qt::PreciseTimer);
                    m_waitingForMissingFrame = true;
                }
                const int audioAdvance = (
                    audioPosition - m_expectedPresentationPosition) * direction;
                if (m_missingFrameDeadline.hasExpired() && audioAdvance > 0) {
                    candidate = future;
                    m_expectedPresentationPosition = candidate.key();
                }
            }
        }

        if (candidate != m_renderedFrames.end()) {
            rendered = std::move(candidate.value());
            m_renderedFrames.erase(candidate);
            m_renderQueueNotFull.wakeOne();
            frameReady = true;
            m_waitingForMissingFrame = false;
        }
        m_renderQueueNotFull.wakeAll();
    }

    if (!frameReady) {
        int queuedFrames = 0;
        {
            const QMutexLocker locker(&m_renderedFramesMutex);
            queuedFrames = m_renderedFrames.size();
        }
        setBufferState(true, queuedFrames);
        m_presentationTimer->start(1);
        return;
    }

    setBufferState(false, 0);

    m_lastPresentationPosition = rendered.position;
    if (unitStep)
        m_expectedPresentationPosition = rendered.position + direction;
    deliverPresentationFrame(rendered.image, rendered.position, generation);
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_previewConsumer
        || !m_playing) {
        return;
    }

    const int currentAudioPosition = m_audioClockPosition.load(std::memory_order_acquire);
    const int lagFrames = (currentAudioPosition - rendered.position) * direction;
    const qint64 nowNs = m_cadenceClock.nsecsElapsed();
    m_nextCadenceDeadlineNs = qMin(
        m_nextCadenceDeadlineNs + nominalIntervalNs,
        nowNs + nominalIntervalNs);

    qint64 remainingNs = m_nextCadenceDeadlineNs - nowNs;
    if (remainingNs <= 0 || lagFrames > allowedLeadFrames) {
        const int catchUpIntervalMs = qMax(1, qFloor(500.0 / m_fps));
        m_presentationTimer->start(catchUpIntervalMs);
        return;
    }
    m_presentationTimer->start(
        qMax(1, static_cast<int>((qMax(0LL, remainingNs) + 999999) / 1000000)));
}

void MltRuntime::deliverPresentationFrame(
    const QImage &image,
    int frame,
    int generation)
{
    if (generation != m_consumerGeneration.load(std::memory_order_acquire)
        || !m_previewConsumer
        || !m_playing) {
        return;
    }

    const int boundaryFrame = m_rate > 0.0
        ? m_playbackEnd - 1
        : m_playbackStart;
    const bool crossedBoundary = m_rate > 0.0
        ? frame > boundaryFrame
        : frame < boundaryFrame;
    if (crossedBoundary) {
        const quint64 requestId = m_requestId.load(std::memory_order_acquire);
        pause(requestId);
        if (m_position != boundaryFrame)
            decodeStillFrame(boundaryFrame);
        return;
    }

    m_position = frame;
    const quint64 requestId = m_requestId.load(std::memory_order_acquire);
    emit frameReady(image, frame, m_duration, requestId);
    if (frame == boundaryFrame) {
        pause(requestId);
    }
}

void MltRuntime::setPlaying(bool playing)
{
    if (m_playing == playing)
        return;
    m_playing = playing;
    if (!playing)
        setBufferState(false, 0);
    emit playingChanged(playing, m_requestId.load(std::memory_order_acquire));
}

void MltRuntime::setBufferState(bool buffering, int bufferedFrames)
{
    const int boundedFrames = qMax(0, bufferedFrames);
    if (m_buffering == buffering && m_bufferedFrames == boundedFrames)
        return;
    m_buffering = buffering;
    m_bufferedFrames = boundedFrames;
    emit bufferStateChanged(
        buffering,
        boundedFrames,
        m_requestId.load(std::memory_order_acquire));
}
