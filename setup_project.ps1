# setup_project.ps1
# PowerShell script to automate the ResolveFlow-Assistant project initialization.
# Designed by Senior DevOps / Python Engineer.

$ErrorActionPreference = "Stop"

# Use script directory if available (when run as script), fallback to current directory
$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { $pwd.Path }

Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host "   ResolveFlow-Assistant Project Workspace Setup Tool" -ForegroundColor Cyan
Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host "Project Root: $ProjectRoot" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Step 1: Pre-flight Checks (Verify Python and Git)
# ---------------------------------------------------------------------------
Write-Host "`n[1/7] Running pre-flight checks..." -ForegroundColor Yellow

$PythonPath = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonPath) {
    Write-Error "Python executable not found in PATH. Please install Python (3.8+) and try again."
    exit 1
} else {
    $pythonVersion = python --version 2>&1
    Write-Host "  ✔ Found Python: $pythonVersion" -ForegroundColor Green
}

$GitPath = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitPath) {
    Write-Error "Git executable not found in PATH. Please install Git and try again."
    exit 1
} else {
    $gitVersion = git --version
    Write-Host "  ✔ Found Git: $gitVersion" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 2: Initialize Directory Structure
# ---------------------------------------------------------------------------
Write-Host "`n[2/7] Creating directory structure..." -ForegroundColor Yellow
$Directories = @(
    'assets',
    'docs',
    'src',
    'src/core',
    'src/ui',
    'tests'
)

foreach ($dir in $Directories) {
    $targetPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $targetPath)) {
        New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
        Write-Host "  ✔ Created: $dir" -ForegroundColor Green
    } else {
        Write-Host "  ➖ Already exists: $dir" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Step 3: Auto-create Empty __init__.py files
# ---------------------------------------------------------------------------
Write-Host "`n[3/7] Creating initial empty python package files..." -ForegroundColor Yellow
$EmptyFiles = @(
    'src/__init__.py',
    'src/core/__init__.py',
    'src/ui/__init__.py',
    'tests/__init__.py'
)

foreach ($file in $EmptyFiles) {
    $filePath = Join-Path $ProjectRoot $file
    if (-not (Test-Path $filePath)) {
        New-Item -ItemType File -Path $filePath -Force | Out-Null
        Write-Host "  ✔ Created: $file" -ForegroundColor Green
    } else {
        Write-Host "  ➖ Already exists: $file" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Step 4: Generate README.md
# ---------------------------------------------------------------------------
Write-Host "`n[4/7] Generating README.md..." -ForegroundColor Yellow
$ReadmeContent = @(
    '# ResolveFlow Assistant',
    '',
    'ResolveFlow Assistant là một trợ lý thông minh giúp tự động hóa quy trình hậu kỳ trong DaVinci Resolve.',
    'Dự án sử dụng thư viện `faster-whisper` để thực hiện chuyển đổi giọng nói thành văn bản nhanh chóng, kết hợp với `ffmpeg` để xử lý và đồng bộ âm thanh/hình ảnh hiệu quả.',
    '',
    '## Cấu trúc thư mục dự án',
    '',
    '```text',
    'ResolveFlow-Assistant/',
    '├── assets/             # Chứa tài nguyên dự án (hình ảnh, icon, logo, mẫu)',
    '├── docs/               # Tài liệu hướng dẫn sử dụng và đặc tả kỹ thuật',
    '├── src/                # Mã nguồn chính của ứng dụng',
    '│   ├── core/           # Logic xử lý nghiệp vụ chính (xử lý audio, gọi API DaVinci Resolve)',
    '│   └── ui/             # Giao diện người dùng đồ họa (GUI)',
    '└── tests/              # Các kịch bản kiểm thử (unit tests và integration tests)',
    '```',
    '',
    '## Yêu cầu hệ thống',
    '- Windows 10 / 11',
    '- Python 3.8 trở lên',
    '- [FFmpeg](https://ffmpeg.org/) (được thêm vào cấu hình biến môi trường PATH)',
    '',
    '## Cài đặt & Hướng dẫn nhanh',
    '',
    '1. **Kích hoạt Virtual Environment (Môi trường ảo):**',
    '   ```powershell',
    '   .\venv\Scripts\Activate.ps1',
    '   ```',
    '',
    '2. **Cài đặt các thư viện cần thiết:**',
    '   ```powershell',
    '   pip install -r requirements.txt',
    '   ```',
    '',
    '3. **Chạy hoặc phát triển ứng dụng:**',
    '   Viết mã nguồn xử lý trong thư mục `src/` và chạy các file tương ứng.',
    '   ```'
) -join "`r`n"

$ReadmeFile = Join-Path $ProjectRoot 'README.md'
[System.IO.File]::WriteAllText($ReadmeFile, $ReadmeContent, [System.Text.Encoding]::UTF8)
Write-Host "  ✔ Created: README.md (UTF-8)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5: Generate .gitignore (Python/DaVinci/OS standard)
# ---------------------------------------------------------------------------
Write-Host "`n[5/7] Generating .gitignore..." -ForegroundColor Yellow
$GitignoreContent = @(
    '# ==========================================',
    '# Python Standard Ignore',
    '# ==========================================',
    'venv/',
    '__pycache__/',
    '*.pyc',
    '*.pyo',
    '*.pyd',
    '.Python',
    'env/',
    'pip-log.txt',
    'pip-delete-this-directory.txt',
    '.tox/',
    '.coverage',
    '.cache/',
    'nosetests.xml',
    'coverage.xml',
    '*.cover',
    '*.log',
    '.pytest_cache/',
    '',
    '# ==========================================',
    '# ResolveFlow Specific / Media Files',
    '# ==========================================',
    '*.mp4',
    '*.mov',
    '*.wav',
    '*.mp3',
    '*.mkv',
    '*.avi',
    '*.m4a',
    '',
    '# ==========================================',
    '# Environment Configuration',
    '# ==========================================',
    '.env',
    '.env.local',
    '.env.*.local',
    '',
    '# ==========================================',
    '# IDE & System files',
    '# ==========================================',
    '.vscode/',
    '.idea/',
    '*.suo',
    '*.ntvs*',
    '*.njsproj',
    '*.sln',
    '*.swp',
    '*~',
    'Thumbs.db',
    'ehthumbs.db',
    'Desktop.ini',
    '.DS_Store'
) -join "`r`n"

$GitignoreFile = Join-Path $ProjectRoot '.gitignore'
[System.IO.File]::WriteAllText($GitignoreFile, $GitignoreContent, [System.Text.Encoding]::UTF8)
Write-Host "  ✔ Created: .gitignore (UTF-8)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 6: Generate requirements.txt
# ---------------------------------------------------------------------------
Write-Host "`n[6/7] Generating requirements.txt..." -ForegroundColor Yellow
$RequirementsContent = @(
    'faster-whisper',
    'ffmpeg-python',
    'numpy',
    'pydantic'
) -join "`r`n"

$RequirementsFile = Join-Path $ProjectRoot 'requirements.txt'
[System.IO.File]::WriteAllText($RequirementsFile, $RequirementsContent, [System.Text.Encoding]::UTF8)
Write-Host "  ✔ Created: requirements.txt (UTF-8)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 7: Create and Configure Python Virtual Environment (venv)
# ---------------------------------------------------------------------------
Write-Host "`n[7/7] Setting up Python virtual environment..." -ForegroundColor Yellow
$VenvPath = Join-Path $ProjectRoot 'venv'

if (-not (Test-Path $VenvPath)) {
    Write-Host "  Creating venv environment (this may take a few seconds)..." -ForegroundColor DarkGray
    python -m venv $VenvPath
    Write-Host "  ✔ Virtual environment 'venv' created successfully." -ForegroundColor Green
} else {
    Write-Host "  ➖ Virtual environment 'venv' already exists. Skipping creation." -ForegroundColor DarkGray
}

Write-Host "  Upgrading pip inside virtual environment..." -ForegroundColor DarkGray
$VenvPython = Join-Path $VenvPath 'Scripts/python.exe'

try {
    # Execute pip upgrade inside the venv directly
    & $VenvPython -m pip install --upgrade pip --quiet
    Write-Host "  ✔ Pip upgraded successfully!" -ForegroundColor Green
} catch {
    Write-Host "  ⚠ Warning: Failed to automatically upgrade pip. You may need to upgrade it manually." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Step 8: Initialize Git repository
# ---------------------------------------------------------------------------
Write-Host "`n[8/8] Initializing Git repository..." -ForegroundColor Yellow
$GitDir = Join-Path $ProjectRoot '.git'

if (-not (Test-Path $GitDir)) {
    # Run git init inside the project folder
    Push-Location $ProjectRoot
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        git init
        
        # Set default branch name to main
        git checkout -b main 2>$null
        if ($LASTEXITCODE -ne 0) {
            git branch -M main
        }
        
        # Stage files and create initial commit
        git add .
        git commit -m 'chore: initial project skeleton and configurations'
        
        Write-Host "  ✔ Git repository initialized with default branch 'main'." -ForegroundColor Green
        Write-Host "  ✔ Initial commit created: 'chore: initial project skeleton and configurations'." -ForegroundColor Green
    } catch {
        Write-Host "  ⚠ Error initializing Git: $_" -ForegroundColor Red
    } finally {
        $ErrorActionPreference = $oldEAP
        Pop-Location
    }
} else {
    Write-Host "  ➖ Git repository already exists. Skipping Git initialization." -ForegroundColor DarkGray
}

Write-Host "`n=======================================================================" -ForegroundColor Green
Write-Host " Setup complete! ResolveFlow-Assistant project is ready to build." -ForegroundColor Green
Write-Host " Activate virtual environment: .\venv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host " Install packages manually if needed: pip install -r requirements.txt" -ForegroundColor Green
Write-Host "=======================================================================" -ForegroundColor Green
