@echo off
setlocal EnableExtensions

title AutoAgent Environment Installer

set "PROJECT_ROOT=%~dp0.."
set "REQUIREMENTS_FILE=%PROJECT_ROOT%\requirements-ui.txt"
set "PYTHON_EXE="

echo ============================================================
echo AutoAgent Environment Installer
echo Project: %PROJECT_ROOT%
echo ============================================================
echo.

if not exist "%REQUIREMENTS_FILE%" goto requirements_missing

call :find_python
if defined PYTHON_EXE goto python_ready

where winget >nul 2>nul
if errorlevel 1 goto winget_missing

echo [1/4] Installing Python 3.14 with winget...
winget install --id Python.Python.3.14 --exact --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_install_failed

call :find_python
if not defined PYTHON_EXE goto python_not_found

:python_ready
echo Python 3.14: %PYTHON_EXE%
"%PYTHON_EXE%" --version
if errorlevel 1 goto python_not_found
for %%P in ("%PYTHON_EXE%") do set "PYTHONW_EXE=%%~dpPpythonw.exe"

echo.
echo [2/4] Initializing and upgrading pip...
"%PYTHON_EXE%" -m ensurepip --upgrade
if errorlevel 1 goto pip_failed
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto pip_failed

echo.
echo [3/4] Installing AutoAgent dependencies...
"%PYTHON_EXE%" -m pip install --upgrade -r "%REQUIREMENTS_FILE%"
if errorlevel 1 goto dependencies_failed

echo.
echo [4/4] Verifying dependencies...
"%PYTHON_EXE%" -c "import cv2, numpy, PySide6, requests, uiautomator2; print('Dependency verification passed')"
if errorlevel 1 goto verification_failed

echo.
echo ============================================================
echo Installation completed successfully.
echo Python: %PYTHON_EXE%
echo Start command:
echo "%PYTHONW_EXE%" "%PROJECT_ROOT%\AutoAgent.pyw"
echo ============================================================
echo.
pause
exit /b 0

:find_python
set "PYTHON_EXE="

if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python314\python.exe"
if defined PYTHON_EXE exit /b 0

if exist "%ProgramFiles%\Python314\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python314\python.exe"
if defined PYTHON_EXE exit /b 0

if exist "%ProgramFiles(x86)%\Python314\python.exe" set "PYTHON_EXE=%ProgramFiles(x86)%\Python314\python.exe"
if defined PYTHON_EXE exit /b 0

where py >nul 2>nul
if errorlevel 1 exit /b 0

for /f "delims=" %%P in ('py -3.14 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
exit /b 0

:requirements_missing
echo ERROR: Requirements file not found:
echo %REQUIREMENTS_FILE%
goto failed

:winget_missing
echo ERROR: winget was not found.
echo Install or update App Installer from Microsoft Store.
goto failed

:python_install_failed
echo ERROR: Python 3.14 installation failed.
goto failed

:python_not_found
echo ERROR: Python 3.14 was installed but could not be located.
echo Close this window and run this script again.
goto failed

:pip_failed
echo ERROR: pip initialization or upgrade failed.
goto failed

:dependencies_failed
echo ERROR: Python dependency installation failed.
goto failed

:verification_failed
echo ERROR: Dependency verification failed.
goto failed

:failed
echo.
echo Installation did not complete. Review the error above and retry.
echo.
pause
exit /b 1
