#include "MltPreviewItem.h"

#include <QQmlExtensionPlugin>
#include <qqml.h>

class MediaFlowNativePlugin final : public QQmlExtensionPlugin
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)

public:
    void registerTypes(const char *uri) override
    {
        Q_ASSERT(QByteArray(uri) == QByteArray("MediaFlow.Native"));
        qmlRegisterType<MltPreviewItem>(uri, 1, 0, "MltPreviewItem");
    }
};

#include "plugin.moc"
