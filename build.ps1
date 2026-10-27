# Builds Idea Atlas into .\program\ (IdeaAtlas.exe + its support files), next to the data\ folder.
#   powershell -ExecutionPolicy Bypass -File build.ps1
# A plain program folder (PyInstaller "onedir", no UPX) rather than a single self-extracting exe.
# The app runs only while its window is open, so building just requires that window to be closed.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$prog = Join-Path $root 'program'
Set-Location $root

& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { & $py -m pip install pyinstaller }

& $py tools\make_icon.py
& $py -m unittest discover -s tests -q
if ($LASTEXITCODE -ne 0) { throw 'tests failed; not building' }

if (Get-Process IdeaAtlas -ErrorAction SilentlyContinue) {
    throw 'Idea Atlas is open. Close its window (the app then exits by itself) and run the build again.'
}

& $py -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --name IdeaAtlas `
    --icon "$root\app\static\atlas.ico" `
    --add-data "$root\app\static;static" `
    --paths "$root\app" `
    --distpath build\dist --workpath build\work --specpath build `
    --exclude-module tkinter --exclude-module unittest --exclude-module pydoc --exclude-module anthropic `
    --exclude-module pydantic --exclude-module httpx --exclude-module asyncio --exclude-module xml.dom `
    --exclude-module pypdf `
    "$root\app\run_atlas.py"
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

if (Test-Path $prog) { Remove-Item -LiteralPath $prog -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $root 'build\dist\IdeaAtlas') -Destination $prog -Recurse
$ver = & $py -c "import sys; sys.path.insert(0,'app'); import atlas; print(atlas.__version__)"
$size = [math]::Round(((Get-ChildItem $prog -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Built Idea Atlas v$ver in $prog ($size MB). Start it with program\IdeaAtlas.exe."
