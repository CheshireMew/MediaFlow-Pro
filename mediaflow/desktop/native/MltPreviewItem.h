#pragma once

#include <QImage>
#include <QMutex>
#include <QQueue>
#include <QQuickItem>
#include <QThread>
#include <QTimer>

#include <atomic>

class MltRuntime;

class MltPreviewItem : public QQuickItem
{
    Q_OBJECT
    Q_PROPERTY(QString source READ source WRITE setSource NOTIFY sourceChanged)
    Q_PROPERTY(QString runtimeRoot READ runtimeRoot WRITE setRuntimeRoot NOTIFY runtimeRootChanged)
    Q_PROPERTY(QString mltLibrary READ mltLibrary WRITE setMltLibrary NOTIFY mltLibraryChanged)
    Q_PROPERTY(QString mltRepository READ mltRepository WRITE setMltRepository NOTIFY mltRepositoryChanged)
    Q_PROPERTY(QString mltData READ mltData WRITE setMltData NOTIFY mltDataChanged)
    Q_PROPERTY(int reloadToken READ reloadToken WRITE setReloadToken NOTIFY reloadTokenChanged)
    Q_PROPERTY(bool playing READ playing NOTIFY playingChanged)
    Q_PROPERTY(bool buffering READ buffering NOTIFY bufferingChanged)
    Q_PROPERTY(int bufferedFrames READ bufferedFrames NOTIFY bufferedFramesChanged)
    Q_PROPERTY(int position READ position NOTIFY positionChanged)
    Q_PROPERTY(int duration READ duration NOTIFY durationChanged)
    Q_PROPERTY(double playbackRate READ playbackRate WRITE setPlaybackRate NOTIFY playbackRateChanged)
    Q_PROPERTY(double volume READ volume WRITE setVolume NOTIFY volumeChanged)
    Q_PROPERTY(int droppedFrames READ droppedFrames NOTIFY droppedFramesChanged)
    Q_PROPERTY(QString errorString READ errorString NOTIFY errorStringChanged)
    Q_PROPERTY(bool hdrEnabled READ hdrEnabled WRITE setHdrEnabled NOTIFY hdrEnabledChanged)
    Q_PROPERTY(bool hdrActive READ hdrActive NOTIFY hdrActiveChanged)

public:
    explicit MltPreviewItem(QQuickItem *parent = nullptr);
    ~MltPreviewItem() override;

    QString source() const { return m_source; }
    void setSource(const QString &value);
    QString runtimeRoot() const { return m_runtimeRoot; }
    void setRuntimeRoot(const QString &value);
    QString mltLibrary() const { return m_mltLibrary; }
    void setMltLibrary(const QString &value);
    QString mltRepository() const { return m_mltRepository; }
    void setMltRepository(const QString &value);
    QString mltData() const { return m_mltData; }
    void setMltData(const QString &value);
    int reloadToken() const { return m_reloadToken; }
    void setReloadToken(int value);
    bool playing() const { return m_playing; }
    bool buffering() const { return m_buffering; }
    int bufferedFrames() const { return m_bufferedFrames; }
    int position() const { return m_position; }
    int duration() const { return m_duration; }
    double playbackRate() const { return m_playbackRate; }
    void setPlaybackRate(double value);
    double volume() const { return m_volume; }
    void setVolume(double value);
    int droppedFrames() const { return m_droppedFrames; }
    QString errorString() const { return m_errorString; }
    bool hdrEnabled() const { return m_hdrEnabled; }
    void setHdrEnabled(bool value);
    bool hdrActive() const { return m_hdrActive; }

    Q_INVOKABLE void play();
    Q_INVOKABLE void playRange(int startFrame, int endFrame);
    Q_INVOKABLE void pause();
    Q_INVOKABLE void seek(int frame);
    Q_INVOKABLE void reload();

signals:
    void sourceChanged();
    void runtimeRootChanged();
    void mltLibraryChanged();
    void mltRepositoryChanged();
    void mltDataChanged();
    void reloadTokenChanged();
    void playingChanged();
    void bufferingChanged();
    void bufferedFramesChanged();
    void positionChanged();
    void durationChanged();
    void playbackRateChanged();
    void volumeChanged();
    void droppedFramesChanged();
    void errorStringChanged();
    void hdrEnabledChanged();
    void hdrActiveChanged();

    void openRequested(
        const QString &graphPath,
        const QString &runtimeRoot,
        const QString &mltLibrary,
        const QString &mltRepository,
        const QString &mltData,
        bool sourceHdr,
        bool outputHdr,
        int initialFrame,
        quint64 requestId);
    void closeRequested(quint64 requestId);
    void playRequested(quint64 requestId);
    void playRangeRequested(int startFrame, int endFrame, quint64 requestId);
    void pauseRequested(quint64 requestId);
    void seekRequested(int frame, quint64 requestId);
    void playbackRateRequested(double rate);
    void volumeRequested(double volume);
    void previewSizeRequested(int width, int height);

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private slots:
    void queueFrame(const QImage &image, int frame, int duration, quint64 requestId);
    void deliverPendingFrame();
    void receiveError(const QString &message, quint64 requestId);

private:
    static constexpr qsizetype MaxPendingPlaybackFrames = 8;

    struct PendingFrame
    {
        QImage image;
        int position = 0;
        int duration = 0;
        quint64 requestId = 0;
        bool trackDrops = false;
    };

    quint64 beginRequest(bool preservePosition);
    void resetPresentationState(bool preservePosition);
    void clearError();
    void scheduleOpen(bool preservePosition = true);
    void openIfReady();
    bool screenSupportsHdr() const;

    QString m_source;
    QString m_runtimeRoot;
    QString m_mltLibrary;
    QString m_mltRepository;
    QString m_mltData;
    int m_reloadToken = 0;
    bool m_playing = false;
    bool m_buffering = false;
    int m_bufferedFrames = 0;
    int m_position = 0;
    int m_duration = 0;
    double m_playbackRate = 1.0;
    double m_volume = 1.0;
    int m_droppedFrames = 0;
    QString m_errorString;
    bool m_hdrEnabled = false;
    bool m_hdrActive = false;
    QImage m_frame;
    QQueue<PendingFrame> m_pendingFrames;
    int m_requestedPosition = 0;
    int m_lastPlaybackFrame = -1;
    bool m_seekPending = false;
    int m_seekRetryAttempts = 0;
    QTimer m_seekRetryTimer;
    bool m_frameDeliveryScheduled = false;
    bool m_openScheduled = false;
    std::atomic<quint64> m_requestId{0};
    std::atomic<bool> m_queuePlaybackFrames{false};
    std::atomic<bool> m_trackPlaybackDrops{false};
    std::atomic<int> m_pendingDroppedFrames{0};
    QMutex m_frameMutex;
    QThread m_workerThread;
    MltRuntime *m_runtime = nullptr;
};
