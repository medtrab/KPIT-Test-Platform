@echo off
:: ============================================================
::  setup_venv.bat  - Creation du venv Python pour Jenkins CI
::  Usage : setup_venv.bat <workspace_path>
::  NOTE : pas de blocs IF (...) multilignes - utilise GOTO
::         car %PATH% contient des () qui cassent le parser cmd.
:: ============================================================

setlocal disabledelayedexpansion

SET WORKSPACE=%~1
IF "%WORKSPACE%"=="" SET WORKSPACE=%CD%

SET VENV=%WORKSPACE%\.venv
SET PIP=%VENV%\Scripts\pip.exe
SET PYTHON=%VENV%\Scripts\python.exe
SET REQ=%WORKSPACE%\ci\requirements-ci.txt

echo.
echo ====================================================
echo   WipeWash CI - Setup venv Python
echo   Workspace : %WORKSPACE%
echo ====================================================
echo.

:: -- Verifier Python ---------------------------------------------------------
where python >nul 2>&1
IF ERRORLEVEL 1 GOTO python_missing

python --version
FOR /F "tokens=2 delims= " %%V IN ('python --version 2^>^&1') DO SET PY_VER=%%V
echo Python detecte : %PY_VER%
GOTO create_venv

:python_missing
echo [ERREUR] Python introuvable dans le PATH.
exit /b 2

:: -- Creer le venv -----------------------------------------------------------
:create_venv
IF EXIST "%VENV%\Scripts\activate.bat" GOTO venv_exists

echo [SETUP] Creation du venv dans %VENV%...
python -m venv "%VENV%"
IF ERRORLEVEL 1 GOTO venv_error
echo [SETUP] Venv cree.
GOTO upgrade_pip

:venv_exists
echo [SETUP] Venv existant detecte - reutilisation.
GOTO upgrade_pip

:venv_error
echo [ERREUR] Impossible de creer le venv.
exit /b 2

:: -- Mettre a jour pip -------------------------------------------------------
:upgrade_pip
echo [SETUP] Mise a jour pip...
"%PYTHON%" -m pip install --quiet --upgrade pip
IF ERRORLEVEL 1 echo [WARN] Mise a jour pip echouee (non bloquant).

:: -- Installer les dependances -----------------------------------------------
IF NOT EXIST "%REQ%" GOTO install_minimal

echo [SETUP] Installation des dependances depuis %REQ%...
"%PIP%" install --quiet -r "%REQ%"
IF ERRORLEVEL 1 GOTO deps_error
echo [SETUP] Dependances installees.
GOTO verify

:install_minimal
echo [WARN] requirements-ci.txt introuvable : %REQ%
echo [SETUP] Installation minimale (redis + jinja2 + matplotlib)...
"%PIP%" install --quiet redis jinja2 matplotlib
GOTO verify

:deps_error
echo [ERREUR] Installation des dependances echouee.
exit /b 2

:: -- Verification finale -----------------------------------------------------
:verify
echo.
echo [SETUP] Verification des modules critiques...
"%PYTHON%" -c "import redis; print('  redis     OK')"
"%PYTHON%" -c "import jinja2; print('  jinja2    OK')"
"%PYTHON%" -c "import matplotlib; print('  matplotlib OK')"

echo.
echo [SETUP] Environnement Python pret.
echo ====================================================
echo.

endlocal
exit /b 0
