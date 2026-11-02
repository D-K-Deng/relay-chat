$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
py -3 scripts/build_windows.py
exit $LASTEXITCODE
