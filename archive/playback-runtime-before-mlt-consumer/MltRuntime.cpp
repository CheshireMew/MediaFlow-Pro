#include "MltRuntime.h"

#include <QColorSpace>
#include <QDir>
#include <QFileInfo>
#include <QMetaObject>
#include <QSize>
#include <QtMath>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace {
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
    , m_timer(new QTimer(this))
{
    m_timer->setTimerType(Qt::PreciseTimer);
    connect(m_timer, &QTimer::timeout, this, &MltRuntime::decodeNextFrame);
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
        emit errorOccurred(QStringLiteral("MLT runtime was not found: %1").arg(libraryPath));
        return false;
    }

    m_library.setFileName(libraryPath);
    m_library.setLoadHints(QLibrary::ResolveAllSymbolsHint);
    if (!m_library.load()) {
        emit errorOccurred(QStringLiteral("Unable to load MLT runtime: %1").arg(m_library.errorString()));
        return false;
    }

    return resolve(m_api.factoryInit, "mlt_factory_init")
        && resolve(m_api.factoryClose, "mlt_factory_close")
        && resolve(m_api.profileInit, "mlt_profile_init")
        && resolve(m_api.profileClose, "mlt_profile_close")
        && resolve(m_api.factoryProducer, "mlt_factory_producer")
        && resolve(m_api.producerService, "mlt_producer_service")
        && resolve(m_api.producerSeek, "mlt_producer_seek")
        && resolve(m_api.producerLength, "mlt_producer_get_length")
        && resolve(m_api.producerFps, "mlt_producer_get_fps")
        && resolve(m_api.producerSetSpeed, "mlt_producer_set_speed")
        && resolve(m_api.producerClose, "mlt_producer_close")
        && resolve(m_api.serviceGetFrame, "mlt_service_get_frame")
        && resolve(m_api.frameGetImage, "mlt_frame_get_image")
        && resolve(m_api.frameGetAudio, "mlt_frame_get_audio")
        && resolve(m_api.frameClose, "mlt_frame_close")
        && resolve(m_api.consumerNew, "mlt_consumer_new")
        && resolve(m_api.consumerProperties, "mlt_consumer_properties")
        && resolve(m_api.consumerConnect, "mlt_consumer_connect")
        && resolve(m_api.consumerStart, "mlt_consumer_start")
        && resolve(m_api.consumerStop, "mlt_consumer_stop")
        && resolve(m_api.consumerPurge, "mlt_consumer_purge")
        && resolve(m_api.consumerRtFrame, "mlt_consumer_rt_frame")
        && resolve(m_api.consumerClose, "mlt_consumer_close")
        && resolve(m_api.propertiesSet, "mlt_properties_set")
        && resolve(m_api.propertiesSetInt, "mlt_properties_set_int");
}

void MltRuntime::openGraph(
    const QString &graphPath,
    const QString &runtimeRoot,
    bool sourceHdr,
    bool outputHdr)
{
    shutdown();
    emit durationChanged(0);
    if (!QFileInfo(graphPath).isFile()) {
        emit errorOccurred(QStringLiteral("MLT graph was not found: %1").arg(graphPath));
        return;
    }
    m_runtimeRoot = runtimeRoot;
#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(runtimeRoot);
#endif
    const QDir runtime(runtimeRoot);
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
    if (!loadApi(runtimeRoot))
        return;
    m_sourceHdr = sourceHdr;
    m_outputHdr = outputHdr;

    m_repository = m_api.factoryInit(encodedRepository.constData());
    if (!m_repository) {
        emit errorOccurred(QStringLiteral("MLT factory initialization failed"));
        return;
    }
    m_profile = m_api.profileInit(nullptr);
    if (!m_profile) {
        emit errorOccurred(QStringLiteral("MLT profile initialization failed"));
        closeGraph();
        return;
    }
    const QByteArray encodedPath = QDir::toNativeSeparators(graphPath).toUtf8();
    m_producer = m_api.factoryProducer(m_profile, "xml", encodedPath.constData());
    if (!m_producer) {
        emit errorOccurred(QStringLiteral("MLT could not open the compiled timeline graph"));
        closeGraph();
        return;
    }

    m_duration = qMax(1, static_cast<int>(m_api.producerLength(m_producer)));
    m_fps = m_api.producerFps(m_producer);
    if (m_fps <= 0.0)
        m_fps = 30.0;
    m_position = 0;
    m_droppedFrames = 0;
    m_clockDriftMs = 0.0;
    m_graphReadyPending = false;
    m_graphReadyPublished = false;
    resetAudio();
    resetSequentialDecoder(0);
    emit droppedFramesChanged(0);
    emit clockDriftChanged(0.0);
    if (!decodeStillFrame(0))
        return;
    if (qFuzzyCompare(m_rate, 1.0) && !primeForwardPlayback())
        return;
    if (!m_audioSink
        || m_audioSink->state() == QAudio::IdleState
        || m_audioSink->state() == QAudio::ActiveState) {
        publishGraphReady();
    } else {
        m_graphReadyPending = true;
    }
}

void MltRuntime::play()
{
    if (!m_producer)
        return;
    if (m_position >= m_duration - 1 && m_rate > 0)
        seek(0);
    if (m_position <= 0 && m_rate < 0)
        seek(m_duration - 1);
    if (m_playing)
        return;
    if (m_rate > 0.0 && !primeForwardPlayback())
        return;
    setPlaying(true);
}

void MltRuntime::pause()
{
    setPlaying(false);
}

void MltRuntime::seek(int frame)
{
    if (!m_producer)
        return;
    const bool resume = m_playing;
    if (resume)
        setPlaying(false);
    m_position = qBound(0, frame, m_duration - 1);
    resetSequentialDecoder(m_position);
    if (!decodeStillFrame(m_position))
        return;
    resetAudio();
    if (qFuzzyCompare(m_rate, 1.0) && !primeForwardPlayback())
        return;
    if (resume)
        play();
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
        setPlaying(false);
    m_rate = bounded;
    closeReadAheadConsumer();
    resetAudio();
    resetSequentialDecoder(m_position);
    if (resume)
        play();
}

void MltRuntime::setVolume(double volume)
{
    m_volume = qBound(0.0, volume, 1.0);
    if (m_audioSink)
        m_audioSink->setVolume(static_cast<float>(m_volume));
}

void MltRuntime::setPreviewSize(int width, int height)
{
    QSize requested(qMax(64, width), qMax(64, height));
    requested.scale(QSize(640, 360), Qt::KeepAspectRatio);
    m_previewWidth = requested.width();
    m_previewHeight = requested.height();
}

void MltRuntime::shutdown()
{
    setPlaying(false);
#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    closeGraph();
    if (m_library.isLoaded())
        m_library.unload();
    m_runtimeRoot.clear();
}

void MltRuntime::close()
{
    setPlaying(false);
#ifdef Q_OS_WIN
    const ScopedDllDirectory dllSearch(m_runtimeRoot);
#endif
    closeGraph();
}

void MltRuntime::decodeNextFrame()
{
    if (!m_producer)
        return;
    if (!m_playing)
        return;

    const int target = playbackTarget();
    if (m_rate > 0.0) {
        flushPendingAudio();
        if (!topUpForwardPlayback(target)) {
            setPlaying(false);
            return;
        }
        presentForwardFrame(target);
        flushPendingAudio();
    } else {
        presentReverseFrame(target);
    }

    if ((m_rate > 0.0 && target >= m_duration - 1)
        || (m_rate < 0.0 && target <= 0)) {
        setPlaying(false);
    }
}

void MltRuntime::closeGraph()
{
    resetAudio();
    closeReadAheadConsumer();
    if (m_producer && m_api.producerClose)
        m_api.producerClose(m_producer);
    m_producer = nullptr;
    if (m_profile && m_api.profileClose)
        m_api.profileClose(m_profile);
    m_profile = nullptr;
    if (m_repository && m_api.factoryClose)
        m_api.factoryClose();
    m_repository = nullptr;
    m_position = 0;
    m_duration = 0;
    m_graphReadyPending = false;
    m_graphReadyPublished = false;
    m_decodedFrames.clear();
    m_sequentialDecoderPositioned = false;
    m_nextDecodeFrame = 0;
}

bool MltRuntime::startReadAheadConsumer(int frame)
{
    closeReadAheadConsumer();
    if (!m_producer || !m_profile)
        return false;
    if (m_api.producerSeek(m_producer, frame) != 0) {
        emit errorOccurred(QStringLiteral("MLT seek failed at frame %1").arg(frame));
        return false;
    }
    m_api.producerSetSpeed(m_producer, 1.0);
    m_consumer = m_api.consumerNew(m_profile);
    if (!m_consumer) {
        emit errorOccurred(QStringLiteral("MLT read-ahead consumer initialization failed"));
        return false;
    }
    MltProperties properties = m_api.consumerProperties(m_consumer);
    m_api.propertiesSetInt(properties, "real_time", -1);
    m_api.propertiesSetInt(properties, "buffer", qMax(8, lookAheadFrames() * 2));
    m_api.propertiesSetInt(properties, "prefill", 2);
    m_api.propertiesSetInt(properties, "width", m_previewWidth);
    m_api.propertiesSetInt(properties, "height", m_previewHeight);
    m_api.propertiesSetInt(properties, "progressive", 1);
    m_api.propertiesSetInt(properties, "frequency", m_audioFormat.isValid() ? m_audioFormat.sampleRate() : 48000);
    m_api.propertiesSetInt(properties, "channels", m_audioFormat.isValid() ? m_audioFormat.channelCount() : 2);
    m_api.propertiesSetInt(properties, "audio_off", m_audioSink ? 0 : 1);
    m_api.propertiesSet(properties, "mlt_audio_format", m_mltAudioFormat == AudioF32Le ? "f32le" : "s16");
    m_api.propertiesSet(properties, "mlt_image_format", m_sourceHdr ? "rgba64" : "rgba");
    m_api.propertiesSet(properties, "rescale", "bilinear");
    if (m_api.consumerConnect(m_consumer, m_api.producerService(m_producer)) != 0
        || m_api.consumerStart(m_consumer) != 0) {
        emit errorOccurred(QStringLiteral("MLT read-ahead consumer failed to start"));
        closeReadAheadConsumer();
        return false;
    }
    m_nextDecodeFrame = frame;
    m_sequentialDecoderPositioned = true;
    return true;
}

void MltRuntime::closeReadAheadConsumer()
{
    if (!m_consumer)
        return;
    if (m_api.consumerPurge)
        m_api.consumerPurge(m_consumer);
    if (m_api.consumerStop)
        m_api.consumerStop(m_consumer);
    if (m_api.consumerClose)
        m_api.consumerClose(m_consumer);
    m_consumer = nullptr;
}

void MltRuntime::resetAudio()
{
    if (m_audioClockActive) {
        m_audioClockActive = false;
        emit audioClockActiveChanged(false);
    }
    if (m_audioSink) {
        m_audioSink->reset();
        delete m_audioSink;
    }
    m_audioSink = nullptr;
    m_audioDevice = nullptr;
    m_audioFormat = QAudioFormat();
    m_mltAudioFormat = AudioNone;
    m_pendingAudio.clear();
}

bool MltRuntime::prepareAudio()
{
    resetAudio();
    if (!m_producer || !qFuzzyCompare(m_rate, 1.0))
        return false;
    const QAudioDevice output = QMediaDevices::defaultAudioOutput();
    if (output.isNull())
        return false;

    QList<QAudioFormat> candidates;
    for (const QAudioFormat::SampleFormat sampleFormat
         : {QAudioFormat::Float, QAudioFormat::Int16}) {
        QAudioFormat candidate;
        candidate.setSampleRate(48000);
        candidate.setChannelCount(2);
        candidate.setSampleFormat(sampleFormat);
        candidates.append(candidate);
    }
    const QAudioFormat preferred = output.preferredFormat();
    if (preferred.sampleFormat() == QAudioFormat::Float
        || preferred.sampleFormat() == QAudioFormat::Int16) {
        candidates.append(preferred);
    }
    for (const QAudioFormat &candidate : candidates) {
        if (output.isFormatSupported(candidate)) {
            m_audioFormat = candidate;
            break;
        }
    }
    if (!m_audioFormat.isValid())
        return false;

    m_mltAudioFormat = m_audioFormat.sampleFormat() == QAudioFormat::Float
        ? AudioF32Le
        : AudioS16;
    m_audioSink = new QAudioSink(output, m_audioFormat, this);
    m_audioSink->setVolume(static_cast<float>(m_volume));
    m_audioSink->setBufferSize(m_audioFormat.bytesForDuration(600'000));
    connect(m_audioSink, &QAudioSink::stateChanged, this, [this](QAudio::State state) {
        if (m_graphReadyPending
            && (state == QAudio::IdleState
                || state == QAudio::ActiveState
                || state == QAudio::StoppedState)) {
            publishGraphReady();
        }
        if (state == QAudio::StoppedState
            && m_audioSink
            && m_audioSink->error() != QAudio::NoError
            && m_audioClockActive) {
            m_audioClockActive = false;
            emit audioClockActiveChanged(false);
        }
    });
    m_audioDevice = m_audioSink->start();
    if (!m_audioDevice) {
        resetAudio();
        return false;
    }
    return true;
}

bool MltRuntime::appendFrameAudio(MltFrame frame)
{
    if (!m_audioSink || !m_audioDevice || !m_audioFormat.isValid())
        return false;
    void *samplesBuffer = nullptr;
    MltAudioFormat format = m_mltAudioFormat;
    int frequency = m_audioFormat.sampleRate();
    int channels = m_audioFormat.channelCount();
    int samples = 0;
    if (m_api.frameGetAudio(
            frame,
            &samplesBuffer,
            &format,
            &frequency,
            &channels,
            &samples) != 0
        || !samplesBuffer
        || samples <= 0
        || format != m_mltAudioFormat
        || frequency != m_audioFormat.sampleRate()
        || channels != m_audioFormat.channelCount()) {
        return false;
    }
    const qint64 bytes = static_cast<qint64>(samples)
        * channels
        * m_audioFormat.bytesPerSample();
    m_pendingAudio.append(static_cast<const char *>(samplesBuffer), bytes);
    flushPendingAudio();
    return true;
}

void MltRuntime::flushPendingAudio()
{
    if (!m_playing || !m_audioSink || !m_audioDevice || m_pendingAudio.isEmpty())
        return;
    QElapsedTimer flushTimer;
    flushTimer.start();
    int writes = 0;
    while (!m_pendingAudio.isEmpty() && m_audioSink->bytesFree() > 0) {
        const qint64 writable = qMin<qint64>(
            m_audioSink->bytesFree(),
            m_pendingAudio.size());
        QElapsedTimer writeTimer;
        writeTimer.start();
        const qint64 written = m_audioDevice->write(m_pendingAudio.constData(), writable);
        if (writeTimer.elapsed() >= 20)
            qInfo() << "MediaFlow audio write" << writable << "bytes took" << writeTimer.elapsed() << "ms";
        if (written <= 0)
            return;
        ++writes;
        m_pendingAudio.remove(0, written);
    }
    if (flushTimer.elapsed() >= 20)
        qInfo() << "MediaFlow audio flush took" << flushTimer.elapsed() << "ms in" << writes << "writes";
}

bool MltRuntime::decodeSequentialFrame(int frameNumber)
{
    if (!m_producer || frameNumber < 0 || frameNumber >= m_duration)
        return false;
    if (!m_consumer || !m_sequentialDecoderPositioned || m_nextDecodeFrame != frameNumber) {
        if (!startReadAheadConsumer(frameNumber))
            return false;
    }

    MltFrame frame = m_api.consumerRtFrame(m_consumer);
    if (!frame) {
        emit errorOccurred(QStringLiteral("MLT could not decode frame %1").arg(frameNumber));
        return false;
    }
    m_nextDecodeFrame = frameNumber + 1;

    QImage image;
    const bool imageReady = readFrameImage(frame, frameNumber, image);
    bool audioReady = true;
    if (m_audioSink)
        audioReady = appendFrameAudio(frame);
    m_api.frameClose(frame);
    if (!imageReady)
        return false;
    if (m_audioSink && !audioReady) {
        emit errorOccurred(QStringLiteral("MLT returned invalid audio at frame %1").arg(frameNumber));
        resetAudio();
    }
    m_decodedFrames.push_back({std::move(image), frameNumber});
    return true;
}

bool MltRuntime::decodeStillFrame(int frameNumber)
{
    closeReadAheadConsumer();
    resetSequentialDecoder(frameNumber);
    if (m_api.producerSeek(m_producer, frameNumber) != 0) {
        emit errorOccurred(QStringLiteral("MLT seek failed at frame %1").arg(frameNumber));
        return false;
    }
    MltFrame frame = nullptr;
    if (m_api.serviceGetFrame(m_api.producerService(m_producer), &frame, 0) != 0 || !frame) {
        emit errorOccurred(QStringLiteral("MLT could not decode frame %1").arg(frameNumber));
        return false;
    }
    QImage image;
    const bool imageReady = readFrameImage(frame, frameNumber, image);
    m_api.frameClose(frame);
    if (!imageReady)
        return false;
    m_nextDecodeFrame = frameNumber + 1;
    m_sequentialDecoderPositioned = false;
    m_position = frameNumber;
    emit frameReady(image, frameNumber, m_duration);
    emit positionChanged(frameNumber);
    return true;
}

bool MltRuntime::readFrameImage(MltFrame frame, int position, QImage &result)
{
    std::uint8_t *pixels = nullptr;
    MltImageFormat format = m_sourceHdr ? ImageRgba64 : ImageRgba;
    int width = m_previewWidth;
    int height = m_previewHeight;
    const int imageResult = m_api.frameGetImage(frame, &pixels, &format, &width, &height, 0);
    if (imageResult != 0 || !pixels || width <= 0 || height <= 0) {
        emit errorOccurred(QStringLiteral("MLT returned an invalid image for frame %1").arg(position));
        return false;
    }
    if (m_sourceHdr && format == ImageRgba64) {
        QImage image(pixels, width, height, width * 8, QImage::Format_RGBA64);
        image.setColorSpace(QColorSpace(QColorSpace::Bt2100Pq));
        const QColorSpace outputColor(
            m_outputHdr ? QColorSpace::SRgbLinear : QColorSpace::SRgb);
        const QImage::Format outputFormat = m_outputHdr
            ? QImage::Format_RGBA16FPx4
            : QImage::Format_RGBA8888;
        result = image.convertedToColorSpace(outputColor, outputFormat);
    } else {
        const QImage image(pixels, width, height, width * 4, QImage::Format_RGBA8888);
        result = image.copy();
    }
    return !result.isNull();
}

bool MltRuntime::primeForwardPlayback()
{
    const bool reusableConsumer = m_consumer
        && m_sequentialDecoderPositioned
        && !m_decodedFrames.empty()
        && m_decodedFrames.front().position == m_position
        && m_nextDecodeFrame > m_position
        && (!qFuzzyCompare(m_rate, 1.0) || m_audioSink);
    if (!reusableConsumer) {
        m_decodedFrames.clear();
        closeReadAheadConsumer();
        resetSequentialDecoder(m_position);
        if (qFuzzyCompare(m_rate, 1.0) && !m_audioSink)
            prepareAudio();
        if (!startReadAheadConsumer(m_position))
            return false;
    }
    const int last = qMin(m_duration - 1, m_position + lookAheadFrames());
    while (m_nextDecodeFrame <= last) {
        if (!decodeSequentialFrame(m_nextDecodeFrame))
            return false;
    }
    flushPendingAudio();
    return !m_decodedFrames.empty();
}

bool MltRuntime::topUpForwardPlayback(int target)
{
    const int last = qMin(
        m_duration - 1,
        qMax(target, m_position) + lookAheadFrames());
    int budget = lookAheadFrames() * 2;
    while (m_nextDecodeFrame <= last && budget-- > 0) {
        if (!decodeSequentialFrame(m_nextDecodeFrame))
            return false;
    }
    return true;
}

void MltRuntime::presentForwardFrame(int target)
{
    if (m_decodedFrames.empty())
        return;
    DecodedFrame selected;
    bool hasSelected = false;
    while (!m_decodedFrames.empty() && m_decodedFrames.front().position <= target) {
        selected = std::move(m_decodedFrames.front());
        m_decodedFrames.pop_front();
        hasSelected = true;
    }
    if (!hasSelected)
        return;
    const int expectedStep = qMax(1, qCeil(m_rate));
    const int skipped = selected.position - m_position - expectedStep;
    if (skipped > 0) {
        m_droppedFrames += skipped;
        emit droppedFramesChanged(m_droppedFrames);
    }
    m_position = selected.position;
    emit frameReady(selected.image, selected.position, m_duration);
    emit positionChanged(selected.position);
}

void MltRuntime::presentReverseFrame(int target)
{
    if (target == m_position)
        return;
    const int expectedStep = qMax(1, qCeil(qAbs(m_rate)));
    const int skipped = qAbs(target - m_position) - expectedStep;
    if (skipped > 0) {
        m_droppedFrames += skipped;
        emit droppedFramesChanged(m_droppedFrames);
    }
    decodeStillFrame(target);
}

void MltRuntime::resetSequentialDecoder(int frame)
{
    m_decodedFrames.clear();
    m_nextDecodeFrame = qBound(0, frame, qMax(0, m_duration - 1));
    m_sequentialDecoderPositioned = false;
}

void MltRuntime::publishGraphReady()
{
    if (m_graphReadyPublished || !m_producer || m_duration <= 0)
        return;
    m_graphReadyPending = false;
    m_graphReadyPublished = true;
    emit durationChanged(m_duration);
}

void MltRuntime::setPlaying(bool playing)
{
    if (m_playing == playing)
        return;
    m_playing = playing;
    if (playing) {
        restartPlaybackClock();
        if (m_audioSink && m_audioSink->state() == QAudio::SuspendedState)
            m_audioSink->resume();
        flushPendingAudio();
        m_timer->start(timerInterval());
    } else {
        m_timer->stop();
        if (m_audioSink)
            m_audioSink->suspend();
    }
    emit playingChanged(playing);
}

void MltRuntime::restartPlaybackClock()
{
    m_playbackAnchorFrame = m_position;
    m_audioAnchorUs = m_audioSink ? m_audioSink->processedUSecs() : 0;
    if (m_audioClockActive) {
        m_audioClockActive = false;
        emit audioClockActiveChanged(false);
    }
    m_playbackClock.restart();
}

int MltRuntime::playbackTarget()
{
    double elapsedSeconds = m_playbackClock.isValid()
        ? static_cast<double>(m_playbackClock.nsecsElapsed()) / 1'000'000'000.0
        : 0.0;
    if (m_audioSink && qFuzzyCompare(m_rate, 1.0)) {
        const qint64 processedUs = m_audioSink->processedUSecs();
        if (processedUs > m_audioAnchorUs) {
            if (!m_audioClockActive) {
                m_audioClockActive = true;
                emit audioClockActiveChanged(true);
            }
            elapsedSeconds = qMax<qint64>(0, processedUs - m_audioAnchorUs) / 1'000'000.0;
            const double videoSeconds
                = qAbs(static_cast<double>(m_position - m_playbackAnchorFrame)) / m_fps;
            const double drift = (videoSeconds - elapsedSeconds) * 1000.0;
            if (!qFuzzyCompare(m_clockDriftMs, drift)) {
                m_clockDriftMs = drift;
                emit clockDriftChanged(drift);
            }
        }
    }
    return qBound(
        0,
        qRound(m_playbackAnchorFrame + elapsedSeconds * m_fps * m_rate),
        m_duration - 1);
}

int MltRuntime::lookAheadFrames() const
{
    return qMax(4, qCeil(m_fps * 0.6));
}

int MltRuntime::timerInterval() const
{
    return qMax(1, qRound(500.0 / m_fps));
}
