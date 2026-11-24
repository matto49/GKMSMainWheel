@echo off
rem build the native core from this clone's sources.
rem Usage: build.cmd <out-dir>    writes <out-dir>\gkmscore.dll and <out-dir>\build.txt
setlocal
set "SRC=%~dp0"
for %%I in ("%~dp0..\..") do set "GKMS_REPO=%%~fI"
if exist "%GKMS_REPO%\gkms.local.cmd" call "%GKMS_REPO%\gkms.local.cmd"
set "B=%~f1"
if "%B%"=="" (echo usage: build.cmd ^<out-dir^> & exit /b 2)
if not defined GKMS_VCVARS (echo GKMS_VCVARS is not set in "%GKMS_REPO%\gkms.local.cmd" & exit /b 2)
if not exist "%B%" mkdir "%B%"
copy /y "%SRC%*.h" "%B%\" > nul
copy /y "%SRC%*.cpp" "%B%\" > nul
call "%GKMS_VCVARS%" > "%B%\vcvars.txt" 2>&1
cd /d "%B%"
cl /nologo /utf-8 /O2 /Ob3 /arch:AVX2 /std:c++17 /LD /MD /fp:precise /EHs-c- /DNDEBUG %GKMS_CL_DEFINES% api.cpp /Fe:gkmscore.dll > "%B%\build.txt" 2>&1
set "RC=%errorlevel%"
echo exit %RC% >> "%B%\build.txt"
exit /b %RC%
