@echo off
setlocal enabledelayedexpansion

:: Define virtual environment directory outside the repo
set VENV_DIR=venv
set UPDATE_DEPS=0

:: -------------------------------
:: Check for Git
:: -------------------------------
echo Checking for Git...
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Git is not installed. Installing Git...

    if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe" (
        echo Installing Git using winget...
        "%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe" install --id Git.Git -e --source winget
    ) else (
        echo winget is not available. Please install Git manually...
        pause
        exit /b 1
    )

    git --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo Git installation failed. Exiting...
        pause
        exit /b 1
    )
)

:: -------------------------------
:: Detect Python 3.11 (even if MS Store)
:: -------------------------------
echo Searching for Python 3.11...

set "PYTHON_CMD="
set "PREFERRED_VERS=3.11"

:: 1. Use py launcher if available
where py >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%v in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_CMD=%%v"
)

:: 2. Try known install locations (MS Store, user, system)
if not defined PYTHON_CMD (
    for %%P in (
        "%LocalAppData%\Microsoft\WindowsApps\python3.11.exe"
        "%ProgramFiles%\Python311\python.exe"
        "%ProgramFiles(x86)%\Python311\python.exe"
        "%UserProfile%\AppData\Local\Programs\Python\Python311\python.exe"
    ) do (
        if exist %%P (
            set "PYTHON_CMD=%%P"
            goto :FOUND_PYTHON
        )
    )
)

:: 3. Fallback: search PATH for any python.exe and check version
if not defined PYTHON_CMD (
    for /f "delims=" %%F in ('where python 2^>nul') do (
        for /f "delims=" %%V in ('"%%F" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul') do (
            if "%%V"=="%PREFERRED_VERS%" (
                set "PYTHON_CMD=%%F"
                goto :FOUND_PYTHON
            )
        )
    )
)

:FOUND_PYTHON

:: If still not found, fail
if not defined PYTHON_CMD (
    echo Python %PREFERRED_VERS% not found.
    echo Please install Python 3.11 from: https://www.python.org/downloads/release/python-3110/
    pause
    exit /b 1
)

:: Show which Python will be used
"%PYTHON_CMD%" -c "import sys; print(f'Using Python {sys.version_info.major}.{sys.version_info.minor} at {sys.executable}')"

:: -------------------------------
:: Repository Setup
:: -------------------------------
set REPO_URL=https://github.com/HaXrDEV/Modpack-CLI-Tool
set REPO_DIR=Modpack-CLI-Tool

:: Get remote HEAD commit hash
for /f "tokens=1" %%i in ('git ls-remote %REPO_URL% HEAD') do set "REMOTE_HASH=%%i"

:: Check if local repo exists
if exist %REPO_DIR% (
    pushd %REPO_DIR% >nul

    if exist .git (
        for /f "delims=" %%j in ('git rev-parse HEAD') do set "LOCAL_HASH=%%j"

        echo Remote: [!REMOTE_HASH!]
        echo Local : [!LOCAL_HASH!]

        if /i "!LOCAL_HASH!"=="!REMOTE_HASH!" (
            echo Repository is up to date. Skipping clone.
            popd >nul
            goto SETUP_ENV
        ) else (
            echo Repository is outdated. Re-cloning...
            popd >nul
            rmdir /s /q %REPO_DIR%
            set UPDATE_DEPS=1
        )
    ) else (
        echo Directory exists but is not a Git repository. Deleting...
        popd >nul
        rmdir /s /q %REPO_DIR%
        set UPDATE_DEPS=1
    )
) else (
    set UPDATE_DEPS=1
)

:: Clone the repo if needed
if %UPDATE_DEPS%==1 (
    echo Cloning repository...
    git clone %REPO_URL%

    if not exist %REPO_DIR% (
        echo Repository clone failed. Exiting...
        pause
        exit /b 1
    )
)

:: -------------------------------
:: Set up virtual environment
:: -------------------------------
:SETUP_ENV
if not exist %VENV_DIR% (
    echo Creating Python virtual environment in "%cd%\%VENV_DIR%"...
    "%PYTHON_CMD%" -m venv %VENV_DIR%
    if %errorlevel% neq 0 (
        echo Failed to create virtual environment. Exiting...
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)

:: Activate the virtual environment
call %VENV_DIR%\Scripts\activate.bat

:: Move into the repo
cd %REPO_DIR%

:: Install dependencies if repo was updated
if %UPDATE_DEPS%==1 (
    if exist requirements.txt (
        echo Installing dependencies from requirements.txt...
        "%PYTHON_CMD%" -m pip install --upgrade pip --quiet >nul 2>&1
        "%PYTHON_CMD%" -m pip install -r requirements.txt
        if %errorlevel% neq 0 (
            echo Failed to install dependencies. Exiting...
            pause
            exit /b 1
        )
    ) else (
        echo No requirements.txt found. Skipping dependency installation.
    )
) else (
    echo Dependencies update not needed; skipping installation.
)

:: -------------------------------
:: Run the main script
:: -------------------------------
echo Running Modpack-Export.py...
"%PYTHON_CMD%" Modpack-Export.py
if %errorlevel% neq 0 (
    echo Python script failed. Exiting...
    pause
    exit /b 1
)

echo Script execution completed successfully.
pause
exit /b 0
