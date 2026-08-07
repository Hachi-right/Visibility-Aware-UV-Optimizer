@echo off
setlocal

if "%~1"=="" (
  echo Usage: %~nx0 WORKTREE_PATH 1>&2
  exit /b 2
)

set "WORKTREE=%~f1"
if not exist "%WORKTREE%\" (
  echo Worktree path does not exist: %WORKTREE% 1>&2
  exit /b 2
)

where codegraph >nul 2>nul
if errorlevel 1 (
  echo codegraph command not found. Install it with: npm install -g @colbymchenry/codegraph 1>&2
  exit /b 127
)

pushd "%WORKTREE%" || exit /b 2
codegraph init .
set "CODEGRAPH_INIT_EXIT=%ERRORLEVEL%"
popd

exit /b %CODEGRAPH_INIT_EXIT%
