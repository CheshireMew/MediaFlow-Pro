param(
    [ValidateSet("transcript_clustering", "community_1")]
    [string]$Backend = "transcript_clustering",
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Device = "auto"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load_environment.ps1")

if ($Backend -eq "transcript_clustering") {
    @'
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_tools import RuntimeToolService
from mediaflow.infrastructure.settings_repository import ServiceSettingsRepository

repository = ServiceSettingsRepository()
settings = repository.load()
service = RuntimeToolService(settings, RuntimeContext.discover().paths)
installed = service.install_speaker_clustering(
    progress=lambda item: print(
        f"{item.message_code}: {item.percent:.1f}%"
        if item.percent is not None
        else item.message_code,
        flush=True,
    )
)
settings.speaker_diarization.backend = "transcript_clustering"
settings.speaker_diarization.clustering_python_executable = installed["python"]
settings.speaker_diarization.embedding_model_path = installed["model"]
repository.save(settings)
print(f"Speaker clustering Python: {installed['python']}")
print(f"3D-Speaker model: {installed['model']}")
'@ | & $env:MEDIAFLOW_PYTHON -
    if ($LASTEXITCODE -ne 0) {
        throw "Local speaker clustering setup failed with exit code $LASTEXITCODE"
    }
    exit 0
}

$environmentRoot = Join-Path $env:MEDIAFLOW_DEV_ROOT "pyannote"
$python = Join-Path $environmentRoot "Scripts\python.exe"
$env:PIP_CACHE_DIR = Join-Path $env:MEDIAFLOW_DEV_ROOT "pip-cache"

if (-not (Test-Path -LiteralPath $python)) {
    & $env:MEDIAFLOW_PYTHON -m venv $environmentRoot
}

$resolvedDevice = $Device
if ($resolvedDevice -eq "auto") {
    $resolvedDevice = if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        "cuda"
    } else {
        "cpu"
    }
}

& $python -m pip install --disable-pip-version-check "setuptools==81.0.0"
if ($resolvedDevice -eq "cuda") {
    & $python -m pip install --disable-pip-version-check `
        "torch==2.11.0+cu128" `
        "torchaudio==2.11.0+cu128" `
        --index-url "https://download.pytorch.org/whl/cu128"
} else {
    & $python -m pip install --disable-pip-version-check `
        "torch==2.11.0+cpu" `
        "torchaudio==2.11.0+cpu" `
        --index-url "https://download.pytorch.org/whl/cpu"
}
& $python -m pip install --disable-pip-version-check "pyannote.audio==4.0.7"
& $python -m pip check
& $python -c (
    "import warnings; " +
    "warnings.filterwarnings('ignore', message=r'\s*torchcodec is not installed correctly'); " +
    "import pyannote.audio, torch; " +
    "print('pyannote=' + pyannote.audio.__version__); " +
    "print('torch=' + torch.__version__); " +
    "print('cuda=' + str(torch.cuda.is_available())); " +
    "print('gpu=' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'))"
)

Write-Output "Speaker diarization Python: $python"
Write-Output "Selected device: $resolvedDevice"
