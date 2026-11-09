# Windows launcher for the review app: creates .venv-win once, installs, starts Streamlit.
# Usage (PowerShell, from promo-finder/):  .\scripts\run_app.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".venv-win")) { py -3 -m venv .venv-win }
& .\.venv-win\Scripts\python.exe -m pip install -q -e ".[app]"
& .\.venv-win\Scripts\python.exe -m streamlit run app/review.py
