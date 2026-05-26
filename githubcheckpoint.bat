@echo off
REM Commit all changes in this repo and push to origin. Pass the commit message as arguments
REM (one quoted string or multiple words joined with spaces). No other text is added to the commit.
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  echo usage: %~nx0 ^<commit message^> >&2
  echo   %~nx0 "Fix voice-dictation start from UNC" >&2
  exit /b 1
)

set "GITHUB_CHECKPOINT_MSGFILE=%TEMP%\githubcheckpoint-msg-%RANDOM%.txt"
set "GIT_SAFE=-c safe.directory=%CD%"

python -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]); m=' '.join(sys.argv[2:]).strip(); (sys.exit(1) if not m else p.write_text(m+'\n', encoding='utf-8'))" "%GITHUB_CHECKPOINT_MSGFILE%" %*
if errorlevel 1 (
  echo error: empty commit message >&2
  del "%GITHUB_CHECKPOINT_MSGFILE%" 2>nul
  exit /b 1
)

git %GIT_SAFE% add -A
git %GIT_SAFE% diff --cached --quiet
if errorlevel 1 goto have_changes
echo Nothing to commit.
del "%GITHUB_CHECKPOINT_MSGFILE%" 2>nul
exit /b 0

:have_changes
git %GIT_SAFE% commit -F "%GITHUB_CHECKPOINT_MSGFILE%"
set "GC_EXIT=%ERRORLEVEL%"
del "%GITHUB_CHECKPOINT_MSGFILE%" 2>nul
if not "%GC_EXIT%"=="0" exit /b %GC_EXIT%

git %GIT_SAFE% push
exit /b %ERRORLEVEL%
