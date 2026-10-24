@echo off
setlocal enabledelayedexpansion

set "CLIENT_DIR=%~dp0"
set "PROJECT_DIR=%~dp0.."
set "PY=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "ENV_FILE=%CLIENT_DIR%.env"

if not exist "%PY%" (
  echo !! 未找到虚拟环境，请先执行: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

if exist "%ENV_FILE%" (
  echo ==^> 从 %ENV_FILE% 加载环境变量
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    set "line_key=%%A"
    set "line_val=%%B"
    for /f "tokens=* delims= " %%K in ("!line_key!") do set "line_key=%%K"
    if not "!line_key!"=="" if not "!line_val!"=="" (
      if not defined !line_key! set "!line_key!=!line_val!"
    )
  )
)

"%PY%" "%CLIENT_DIR%asr_client.py"
endlocal