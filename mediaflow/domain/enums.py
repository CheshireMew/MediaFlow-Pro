from enum import Enum


class DomainEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ColorMode(DomainEnum):
    SDR_BT709 = "sdr_bt709"
    HDR10_BT2020_PQ = "hdr10_bt2020_pq"


class AssetKind(DomainEnum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    SUBTITLE = "subtitle"


class AssetOrigin(DomainEnum):
    EXTERNAL = "external"
    DOWNLOAD = "download"
    GENERATED = "generated"


class AssetStatus(DomainEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    ANALYZING = "analyzing"
    ERROR = "error"


class SequenceKind(DomainEnum):
    MAIN = "main"
    SHORT = "short"


class TrackKind(DomainEnum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"


class TransitionKind(DomainEnum):
    FADE = "fade"
    DISSOLVE = "dissolve"
    FADE_BLACK = "fade_black"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    ZOOM = "zoom"


class AudioEffectKind(DomainEnum):
    PARAMETRIC_EQ = "parametric_eq"
    HIGH_PASS = "high_pass"
    LOW_PASS = "low_pass"
    COMPRESSOR = "compressor"
    LIMITER = "limiter"
    NOISE_GATE = "noise_gate"
    RNNOISE = "rnnoise"
    CHANNEL_MAP = "channel_map"
    LOUDNESS_NORMALIZE = "loudness_normalize"
    DUCKING = "ducking"


class ExportFormat(DomainEnum):
    H264 = "h264"
    HEVC = "hevc"
    AV1 = "av1"
    PRORES = "prores"
    AUDIO = "audio"


class TaskKind(DomainEnum):
    ANALYZE = "analyze"
    DOWNLOAD = "download"
    PROXY = "proxy"
    WAVEFORM = "waveform"
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"
    HIGHLIGHT = "highlight"
    EXPORT = "export"


class TaskStatus(DomainEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStage(DomainEnum):
    DOWNLOAD = "download"
    PREPARE_MEDIA = "prepare_media"
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"
    HIGHLIGHT = "highlight"
    CREATE_SHORTS = "create_shorts"
    EXPORT = "export"
    COMPLETE = "complete"


class WorkflowStatus(DomainEnum):
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
