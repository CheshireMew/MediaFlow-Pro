#pragma once

#include <QImage>
#include <QLibrary>
#include <QObject>
#include <QString>

#include <atomic>
#include <cstdint>

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
        bool outputHdr,
        int initialFrame,
        quint64 requestId);
    void play(quint64 requestId);
    void playRange(int startFrame, int endFrame, quint64 requestId);
    void pause(quint64 requestId);
    void seek(int frame, quint64 requestId);
    void setPlaybackRate(double rate);
    void setVolume(double volume);
    void setPreviewSize(int width, int height);
    void close(quint64 requestId);
    void shutdown();

signals:
    void frameReady(const QImage &image, int frame, int duration, quint64 requestId);
    void positionChanged(int frame, quint64 requestId);
    void durationChanged(int duration, quint64 requestId);
    void playingChanged(bool playing, quint64 requestId);
    void droppedFramesChanged(int droppedFrames, quint64 requestId);
    void errorOccurred(const QString &message, quint64 requestId);

private:
    using MltRepository = void *;
    using MltProfile = void *;
    using MltProducer = void *;
    using MltService = void *;
    using MltFrame = void *;
    using MltConsumer = void *;
    using MltProperties = void *;
    using MltEvent = void *;
    using MltPosition = std::int64_t;

    struct MltEventData {
        union {
            int integer;
            void *pointer;
        } value;
    };

    enum MltImageFormat {
        ImageNone = 0,
        ImageRgb = 1,
        ImageRgba = 2,
        ImageRgba64 = 10,
    };

    struct Api {
        using FactoryInit = MltRepository (*)(const char *);
        using ProfileInit = MltProfile (*)(const char *);
        using ProfileClose = void (*)(MltProfile);
        using FactoryProducer = MltProducer (*)(MltProfile, const char *, const void *);
        using FactoryConsumer = MltConsumer (*)(MltProfile, const char *, const void *);
        using ProducerService = MltService (*)(MltProducer);
        using ProducerSeek = int (*)(MltProducer, MltPosition);
        using ProducerLength = MltPosition (*)(MltProducer);
        using ProducerFps = double (*)(MltProducer);
        using ProducerSetSpeed = int (*)(MltProducer, double);
        using ProducerClose = void (*)(MltProducer);
        using ServiceGetFrame = int (*)(MltService, MltFrame *, int);
        using FrameGetImage = int (*)(MltFrame, std::uint8_t **, MltImageFormat *, int *, int *, int);
        using FrameGetPosition = MltPosition (*)(MltFrame);
        using FrameClose = void (*)(MltFrame);
        using ConsumerProperties = MltProperties (*)(MltConsumer);
        using ConsumerConnect = int (*)(MltConsumer, MltService);
        using ConsumerStart = int (*)(MltConsumer);
        using ConsumerStop = int (*)(MltConsumer);
        using ConsumerPurge = void (*)(MltConsumer);
        using ConsumerClose = void (*)(MltConsumer);
        using PropertiesSet = int (*)(MltProperties, const char *, const char *);
        using PropertiesSetInt = int (*)(MltProperties, const char *, int);
        using PropertiesGetInt = int (*)(MltProperties, const char *);
        using EventListener = void (*)(MltProperties, void *, MltEventData);
        using EventsListen = MltEvent (*)(
            MltProperties,
            void *,
            const char *,
            EventListener);
        using EventDataToFrame = MltFrame (*)(MltEventData);

        FactoryInit factoryInit = nullptr;
        ProfileInit profileInit = nullptr;
        ProfileClose profileClose = nullptr;
        FactoryProducer factoryProducer = nullptr;
        FactoryConsumer factoryConsumer = nullptr;
        ProducerService producerService = nullptr;
        ProducerSeek producerSeek = nullptr;
        ProducerLength producerLength = nullptr;
        ProducerFps producerFps = nullptr;
        ProducerSetSpeed producerSetSpeed = nullptr;
        ProducerClose producerClose = nullptr;
        ServiceGetFrame serviceGetFrame = nullptr;
        FrameGetImage frameGetImage = nullptr;
        FrameGetPosition frameGetPosition = nullptr;
        FrameClose frameClose = nullptr;
        ConsumerProperties consumerProperties = nullptr;
        ConsumerConnect consumerConnect = nullptr;
        ConsumerStart consumerStart = nullptr;
        ConsumerStop consumerStop = nullptr;
        ConsumerPurge consumerPurge = nullptr;
        ConsumerClose consumerClose = nullptr;
        PropertiesSet propertiesSet = nullptr;
        PropertiesSetInt propertiesSetInt = nullptr;
        PropertiesGetInt propertiesGetInt = nullptr;
        EventsListen eventsListen = nullptr;
        EventDataToFrame eventDataToFrame = nullptr;
    };

    template<typename T>
    bool resolve(T &target, const char *name)
    {
        target = reinterpret_cast<T>(m_library.resolve(name));
        if (!target) {
            emit errorOccurred(
                QStringLiteral("MLT runtime is missing symbol: %1").arg(QString::fromLatin1(name)),
                m_requestId.load(std::memory_order_acquire));
            return false;
        }
        return true;
    }

    bool loadApi(const QString &runtimeRoot);
    void closeGraph();
    void performPendingSeek();
    bool seekImmediately(int frame);
    bool startConfiguredPlayback();
    bool startPlaybackConsumer();
    void closePlaybackConsumer();
    bool decodeStillFrame(int frame);
    bool readFrameImage(MltFrame frame, int position, QImage &image);
    static void onConsumerFrame(
        MltProperties owner,
        void *listenerData,
        MltEventData eventData);
    void deliverConsumerFrame(const QImage &image, int frame, int generation);
    void setPlaying(bool playing);

    QLibrary m_library;
    QString m_runtimeRoot;
    Api m_api;
    MltRepository m_repository = nullptr;
    MltProfile m_profile = nullptr;
    MltProducer m_producer = nullptr;
    MltConsumer m_consumer = nullptr;
    MltEvent m_frameEvent = nullptr;
    int m_position = 0;
    int m_duration = 0;
    int m_playbackStart = 0;
    int m_playbackEnd = 0;
    int m_pendingSeekFrame = -1;
    bool m_seekScheduled = false;
    int m_droppedFrames = 0;
    int m_consumerDropOffset = 0;
    int m_consumerDropBaseline = -1;
    double m_fps = 30.0;
    double m_rate = 1.0;
    double m_volume = 1.0;
    bool m_playing = false;
    bool m_sourceHdr = false;
    bool m_outputHdr = false;
    int m_previewWidth = 960;
    int m_previewHeight = 540;
    std::atomic<int> m_frameDuration{0};
    std::atomic<int> m_frameWidth{960};
    std::atomic<int> m_frameHeight{540};
    std::atomic<bool> m_frameSourceHdr{false};
    std::atomic<bool> m_frameOutputHdr{false};
    std::atomic<quint64> m_requestId{0};
    std::atomic<int> m_consumerGeneration{0};
};
