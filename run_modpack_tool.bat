@echo off
setlocal

:: Launches HaXr's Modpack Tool from this repository.
:: Creates the venv on first run and re-installs dependencies only when
:: requirements.txt has changed since the last install.

set "REPO_DIR=%~dp0"
set "VENV_DIR=%REPO_DIR%venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQS=%REPO_DIR%requirements.txt"
set "REQS_INSTALLED=%VENV_DIR%\requirements.installed"

pushd "%REPO_DIR%" >nul

if exist "%VENV_PY%" goto CHECK_DEPS

:: ----------------------------------------
:: Create the virtual environment
:: ----------------------------------------
echo Creating Python virtual environment...
set "PYTHON_CMD="
for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_CMD=%%P"
if not defined PYTHON_CMD for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_CMD=%%P"
if not defined PYTHON_CMD for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_CMD=%%P"

if not defined PYTHON_CMD (
    echo No usable Python interpreter found. Install Python 3.11 and re-run.
    goto FAIL
)

"%PYTHON_CMD%" -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo Failed to create virtual environment.
    goto FAIL
)

:: ----------------------------------------
:: Install dependencies when requirements.txt changed
:: ----------------------------------------
:CHECK_DEPS
if not exist "%REQS_INSTALLED%" goto INSTALL_DEPS
fc /b "%REQS%" "%REQS_INSTALLED%" >nul 2>&1
if not errorlevel 1 goto RUN

:INSTALL_DEPS
echo Installing dependencies from requirements.txt...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%REQS%"
if errorlevel 1 (
    echo Dependency installation failed.
    goto FAIL
)
copy /y "%REQS%" "%REQS_INSTALLED%" >nul

:: ----------------------------------------
:: Run the tool
:: ----------------------------------------
:RUN
"%VENV_PY%" "%REPO_DIR%modpack_export.py"
if errorlevel 1 (
    echo The tool exited with an error.
    goto FAIL
)

popd >nul
exit /b 0

:FAIL
popd >nul
pause
exit /b 1
