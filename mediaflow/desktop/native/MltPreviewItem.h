#pragma once

#include <QImage>
#include <QMutex>
#include <QQuickItem>
#include <QThread>

class MltRuntime;

class MltPreviewItem : public QQuickItem
{
    Q_OBJECT
    Q_PROPERTY(QString source READ source WRITE setSource NOTIFY sourceChanged)
    Q_PROPERTY(QString runtimeRoot READ runtimeRoot WRITE setRuntimeRoot NOTIFY runtimeRootChanged)
    Q_PROPERTY(bool playing READ playing NOTIFY playingChanged)
    Q_PROPERTY(int position READ position NOTIFY positionChanged)
    Q_PROPERTY(int duration READ duration NOTIFY durationChanged)
    Q_PROPERTY(double playbackRate READ playbackRate WRITE setPlaybackRate NOTIFY playbackRateChanged)
    Q_PROPERTY(int droppedFrames READ droppedFrames NOTIFY droppedFramesChanged)
    Q_PROPERTY(QString errorString READ errorString NOTIFY errorStringChanged)
    Q_PROPERTY(bool hdrEnabled READ hdrEnabled WRITE setHdrEnabled NOTIFY hdrEnabledChanged)
    Q_PROPERTY(bool hdrActive READ hdrActive NOTIFY hdrActiveChanged)
    Q_PROPERTY(double clockDriftMs READ clockDriftMs NOTIFY clockDriftChanged)
    Q_PROPERTY(bool audioClockActive READ audioClockActive NOTIFY audioClockActiveChanged)

public:
    explicit MltPreviewItem(QQuickItem *parent = nullptr);
    ~MltPreviewItem() override;

    QString source() const { return m_source; }
    void setSource(const QString &value);
    QString runtimeRoot() const { return m_runtimeRoot; }
    void setRuntimeRoot(const QString &value);
    bool playing() const { return m_playing; }
    int position() const { return m_position; }
    int duration() const { return m_duration; }
    double playbackRate() const { return m_playbackRate; }
    void setPlaybackRate(double value);
    int droppedFrames() const { return m_droppedFrames; }
    QString errorString() const { return m_errorString; }
    bool hdrEnabled() const { return m_hdrEnabled; }
    void setHdrEnabled(bool value);
    bool hdrActive() const { return m_hdrActive; }
    double clockDriftMs() const { return m_clockDriftMs; }
    bool audioClockActive() const { return m_audioClockActive; }

    Q_INVOKABLE void play();
    Q_INVOKABLE void pause();
    Q_INVOKABLE void seek(int frame);
    Q_INVOKABLE void reload();

signals:
    void sourceChanged();
    void runtimeRootChanged();
    void playingChanged();
    void positionChanged();
    void durationChanged();
    void playbackRateChanged();
    void droppedFramesChanged();
    void errorStringChanged();
    void hdrEnabledChanged();
    void hdrActiveChanged();
    void clockDriftChanged();
    void audioClockActiveChanged();

    void openRequested(
        const QString &graphPath,
        const QString &runtimeRoot,
        bool sourceHdr,
        bool outputHdr);
    void playRequested();
    void pauseRequested();
    void seekRequested(int frame);
    void playbackRateRequested(double rate);
    void previewSizeRequested(int width, int height);

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private slots:
    void receiveFrame(const QImage &image, int frame, int duration);
    void receiveError(const QString &message);

private:
    void openIfReady();
    bool screenSupportsHdr() const;

    QString m_source;
    QString m_runtimeRoot;
    bool m_playing = false;
    int m_position = 0;
    int m_duration = 0;
    double m_playbackRate = 1.0;
    int m_droppedFrames = 0;
    QString m_errorString;
    bool m_hdrEnabled = false;
    bool m_hdrActive = false;
    double m_clockDriftMs = 0.0;
    bool m_audioClockActive = false;
    QImage m_frame;
    QMutex m_frameMutex;
    QThread m_workerThread;
    MltRuntime *m_runtime = nullptr;
};
