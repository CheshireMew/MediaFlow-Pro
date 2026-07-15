#pragma once

#include <QImage>
#include <QElapsedTimer>
#include <QLibrary>
#include <QObject>
#include <QPointer>
#include <QString>
#include <QTimer>

#include <cstdint>

class QAudioSink;
class QIODevice;

class MltRuntime final : public QObject
{
    Q_OBJECT

public:
    explicit MltRuntime(QObject *parent = nullptr);
    ~MltRuntime() override;

public slots:
    void openGraph(
        const QString &graphPath,
        const QString &runtimeRoot,
        bool sourceHdr,
        bool outputHdr);
    void play();
    void pause();
    void seek(int frame);
    void setPlaybackRate(double rate);
    void setPreviewSize(int width, int height);
    void shutdown();

signals:
    void frameReady(const QImage &image, int frame, int duration);
    void positionChanged(int frame);
    void durationChanged(int duration);
    void playingChanged(bool playing);
    void droppedFramesChanged(int droppedFrames);
    void clockDriftChanged(double milliseconds);
    void audioClockActiveChanged(bool active);
    void errorOccurred(const QString &message);

private slots:
    void decodeNextFrame();

private:
    using MltRepository = void *;
    using MltProfile = void *;
    using MltProducer = void *;
    using MltService = void *;
    using MltFrame = void *;
    using MltPosition = std::int64_t;

    enum MltImageFormat {
        ImageNone = 0,
        ImageRgb = 1,
        ImageRgba = 2,
        ImageRgba64 = 10,
    };

    enum MltAudioFormat {
        AudioNone = 0,
        AudioS16 = 1,
        AudioS32 = 2,
        AudioFloat = 3,
        AudioS32Le = 4,
        AudioF32Le = 5,
    };

    struct Api {
        using FactoryInit = MltRepository (*)(const char *);
        using FactoryClose = void (*)();
        using ProfileInit = MltProfile (*)(const char *);
        using ProfileClose = void (*)(MltProfile);
        using FactoryProducer = MltProducer (*)(MltProfile, const char *, const void *);
        using ProducerService = MltService (*)(MltProducer);
        using ProducerSeek = int (*)(MltProducer, MltPosition);
        using ProducerLength = MltPosition (*)(MltProducer);
        using ProducerFps = double (*)(MltProducer);
        using ProducerClose = void (*)(MltProducer);
        using ServiceGetFrame = int (*)(MltService, MltFrame *, int);
        using FrameGetImage = int (*)(MltFrame, std::uint8_t **, MltImageFormat *, int *, int *, int);
        using FrameGetAudio = int (*)(MltFrame, void **, MltAudioFormat *, int *, int *, int *);
        using FrameClose = void (*)(MltFrame);

        FactoryInit factoryInit = nullptr;
        FactoryClose factoryClose = nullptr;
        ProfileInit profileInit = nullptr;
        ProfileClose profileClose = nullptr;
        FactoryProducer factoryProducer = nullptr;
        ProducerService producerService = nullptr;
        ProducerSeek producerSeek = nullptr;
        ProducerLength producerLength = nullptr;
        ProducerFps producerFps = nullptr;
        ProducerClose producerClose = nullptr;
        ServiceGetFrame serviceGetFrame = nullptr;
        FrameGetImage frameGetImage = nullptr;
        FrameGetAudio frameGetAudio = nullptr;
        FrameClose frameClose = nullptr;
    };

    template<typename T>
    bool resolve(T &target, const char *name)
    {
        target = reinterpret_cast<T>(m_library.resolve(name));
        if (!target) {
            emit errorOccurred(QStringLiteral("MLT runtime is missing symbol: %1").arg(QString::fromLatin1(name)));
            return false;
        }
        return true;
    }

    bool loadApi(const QString &runtimeRoot);
    void closeGraph();
    void resetAudio();
    bool writeAudio(MltFrame frame);
    void fillAudioBuffer();
    void setPlaying(bool playing);
    void restartPlaybackClock();
    int timerInterval() const;

    QLibrary m_library;
    QString m_runtimeRoot;
    Api m_api;
    MltRepository m_repository = nullptr;
    MltProfile m_profile = nullptr;
    MltProducer m_producer = nullptr;
    QTimer *m_timer = nullptr;
    QAudioSink *m_audioSink = nullptr;
    QIODevice *m_audioDevice = nullptr;
    int m_position = 0;
    int m_duration = 0;
    int m_droppedFrames = 0;
    double m_fps = 30.0;
    double m_rate = 1.0;
    double m_positionAccumulator = 0.0;
    bool m_playing = false;
    bool m_sourceHdr = false;
    bool m_outputHdr = false;
    bool m_audioClockActive = false;
    qint64 m_lastDecodeAt = 0;
    qint64 m_audioAnchorUs = 0;
    int m_playbackAnchorFrame = 0;
    int m_audioQueuedThroughFrame = -1;
    double m_clockDriftMs = 0.0;
    int m_previewWidth = 960;
    int m_previewHeight = 540;
    QElapsedTimer m_playbackClock;
};
