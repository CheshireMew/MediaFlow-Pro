#include "MltRuntime.h"

#include <QAudioDevice>
#include <QAudioFormat>
#include <QAudioSink>
#include <QColorSpace>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QIODevice>
#include <QMediaDevices>
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
        && resolve(m_api.producerClose, "mlt_producer_close")
        && resolve(m_api.serviceGetFrame, "mlt_service_get_frame")
        && resolve(m_api.frameGetImage, "mlt_frame_get_image")
        && resolve(m_api.frameGetAudio, "mlt_frame_get_audio")
        && resolve(m_api.frameClose, "mlt_frame_close");
}

void MltRuntime::openGraph(
    const QString &graphPath,
    const QString &runtimeRoot,
    bool sourceHdr,
    bool outputHdr)
{
    shutdown();
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
    m_positionAccumulator = 0.0;
    m_droppedFrames = 0;
    m_clockDriftMs = 0.0;
    resetAudio();
    emit durationChanged(m_duration);
    emit droppedFramesChanged(0);
    emit clockDriftChanged(0.0);
    decodeNextFrame();
}

void MltRuntime::play()
{
    if (!m_producer)
        return;
    if (m_position >= m_duration - 1 && m_rate > 0)
        seek(0);
    if (m_position <= 0 && m_rate < 0)
        seek(m_duration - 1);
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
    m_position = qBound(0, frame, m_duration - 1);
    m_positionAccumulator = m_position;
    resetAudio();
    if (m_playing)
        restartPlaybackClock();
    decodeNextFrame();
}

void MltRuntime::setPlaybackRate(double rate)
{
    if (qFuzzyIsNull(rate))
        rate = 1.0;
    m_rate = qBound(-4.0, rate, 4.0);
    if (m_timer->isActive())
        m_timer->start(timerInterval());
    if (!qFuzzyCompare(m_rate, 1.0))
        resetAudio();
    if (m_playing)
        restartPlaybackClock();
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

void MltRuntime::decodeNextFrame()
{
    if (!m_producer)
        return;

    if (m_playing && m_lastDecodeAt != 0) {
        double elapsedSeconds = m_playbackClock.isValid()
            ? static_cast<double>(m_playbackClock.nsecsElapsed()) / 1'000'000'000.0
            : 0.0;
        if (m_audioSink && qFuzzyCompare(m_rate, 1.0)) {
            const qint64 processedUs = m_audioSink->processedUSecs();
            if (!m_audioClockActive && processedUs > m_audioAnchorUs) {
                m_audioClockActive = true;
                emit audioClockActiveChanged(true);
                elapsedSeconds = qMax<qint64>(0, processedUs - m_audioAnchorUs) / 1'000'000.0;
            } else if (m_audioClockActive) {
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
        const int target = qBound(
            0,
            qRound(m_playbackAnchorFrame + elapsedSeconds * m_fps * m_rate),
            m_duration - 1);
        if (target == m_position)
            return;
        const int expectedStep = qMax(1, qCeil(qAbs(m_rate)));
        const int skipped = qAbs(target - m_position) - expectedStep;
        if (skipped > 0) {
            m_droppedFrames += skipped;
            emit droppedFramesChanged(m_droppedFrames);
        }
        m_positionAccumulator = target;
        m_position = target;
    }

    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    m_lastDecodeAt = now;

    if (m_api.producerSeek(m_producer, m_position) != 0) {
        emit errorOccurred(QStringLiteral("MLT seek failed at frame %1").arg(m_position));
        setPlaying(false);
        return;
    }
    MltFrame frame = nullptr;
    if (m_api.serviceGetFrame(m_api.producerService(m_producer), &frame, 0) != 0 || !frame) {
        emit errorOccurred(QStringLiteral("MLT could not decode frame %1").arg(m_position));
        setPlaying(false);
        return;
    }

    std::uint8_t *pixels = nullptr;
    MltImageFormat format = m_sourceHdr ? ImageRgba64 : ImageRgba;
    int width = m_previewWidth;
    int height = m_previewHeight;
    const int imageResult = m_api.frameGetImage(frame, &pixels, &format, &width, &height, 0);
    if (imageResult == 0 && pixels && width > 0 && height > 0) {
        if (m_sourceHdr && format == ImageRgba64) {
            QImage image(pixels, width, height, width * 8, QImage::Format_RGBA64);
            image.setColorSpace(QColorSpace(QColorSpace::Bt2100Pq));
            const QColorSpace outputColor(
                m_outputHdr ? QColorSpace::SRgbLinear : QColorSpace::SRgb);
            const QImage::Format outputFormat = m_outputHdr
                ? QImage::Format_RGBA16FPx4
                : QImage::Format_RGBA8888;
            emit frameReady(
                image.convertedToColorSpace(outputColor, outputFormat),
                m_position,
                m_duration);
        } else {
            const QImage image(pixels, width, height, width * 4, QImage::Format_RGBA8888);
            emit frameReady(image.copy(), m_position, m_duration);
        }
    } else {
        emit errorOccurred(QStringLiteral("MLT returned an invalid image for frame %1").arg(m_position));
    }

    if (m_playing && qFuzzyCompare(m_rate, 1.0)
        && m_audioQueuedThroughFrame < m_position
        && writeAudio(frame)) {
        m_audioQueuedThroughFrame = m_position;
    }
    m_api.frameClose(frame);
    if (m_playing && qFuzzyCompare(m_rate, 1.0))
        fillAudioBuffer();
    emit positionChanged(m_position);

    if (!m_playing)
        return;
    if ((m_rate > 0 && m_position >= m_duration - 1)
        || (m_rate < 0 && m_position <= 0)) {
        setPlaying(false);
    }
}

void MltRuntime::closeGraph()
{
    resetAudio();
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
}

void MltRuntime::resetAudio()
{
    if (m_audioClockActive) {
        m_audioClockActive = false;
        emit audioClockActiveChanged(false);
    }
    if (m_audioSink) {
        m_audioSink->stop();
        delete m_audioSink;
    }
    m_audioSink = nullptr;
    m_audioDevice = nullptr;
    m_audioQueuedThroughFrame = -1;
    if (!m_producer || !qFuzzyCompare(m_rate, 1.0))
        return;
    const QAudioDevice output = QMediaDevices::defaultAudioOutput();
    if (output.isNull())
        return;
    QAudioFormat format;
    format.setSampleRate(48000);
    format.setChannelCount(2);
    format.setSampleFormat(QAudioFormat::Float);
    if (!output.isFormatSupported(format))
        return;
    m_audioSink = new QAudioSink(output, format, this);
    m_audioSink->setBufferSize(48000 * 2 * static_cast<int>(sizeof(float)) / 4);
    m_audioDevice = m_audioSink->start();
}

bool MltRuntime::writeAudio(MltFrame frame)
{
    if (!m_audioSink || !m_audioDevice)
        return false;
    void *samplesBuffer = nullptr;
    MltAudioFormat format = AudioF32Le;
    int frequency = 48000;
    int channels = 2;
    int samples = 0;
    if (m_api.frameGetAudio(
            frame,
            &samplesBuffer,
            &format,
            &frequency,
            &channels,
            &samples) != 0
        || !samplesBuffer || samples <= 0 || format != AudioF32Le) {
        return false;
    }
    const qint64 bytes = static_cast<qint64>(samples) * channels * sizeof(float);
    if (m_audioSink->bytesFree() < bytes)
        return false;
    return m_audioDevice->write(static_cast<const char *>(samplesBuffer), bytes) == bytes;
}

void MltRuntime::fillAudioBuffer()
{
    if (!m_audioSink || !m_audioDevice || !m_producer)
        return;
    int candidate = qMax(m_position + 1, m_audioQueuedThroughFrame + 1);
    while (candidate < m_duration && m_audioSink->bytesFree() > 0) {
        if (m_api.producerSeek(m_producer, candidate) != 0)
            break;
        MltFrame frame = nullptr;
        if (m_api.serviceGetFrame(m_api.producerService(m_producer), &frame, 0) != 0 || !frame)
            break;
        const bool written = writeAudio(frame);
        m_api.frameClose(frame);
        if (!written)
            break;
        m_audioQueuedThroughFrame = candidate;
        ++candidate;
    }
}

void MltRuntime::setPlaying(bool playing)
{
    if (m_playing == playing)
        return;
    m_playing = playing;
    m_lastDecodeAt = 0;
    if (playing) {
        restartPlaybackClock();
        m_timer->start(timerInterval());
    } else {
        m_timer->stop();
    }
    emit playingChanged(playing);
}

void MltRuntime::restartPlaybackClock()
{
    m_playbackAnchorFrame = m_position;
    m_positionAccumulator = m_position;
    m_audioAnchorUs = m_audioSink ? m_audioSink->processedUSecs() : 0;
    if (m_audioClockActive) {
        m_audioClockActive = false;
        emit audioClockActiveChanged(false);
    }
    m_playbackClock.restart();
}

int MltRuntime::timerInterval() const
{
    return qMax(1, qRound(500.0 / m_fps));
}
