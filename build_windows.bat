@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
 echo 打包需要 Python 3.11 或更新的 64 位版本：https://www.python.org/downloads/windows/
 pause
 exit /b 1
)
if not exist .build-venv\Scripts\python.exe py -3 -m venv .build-venv
if not exist .build-venv\Scripts\python.exe exit /b 1
.build-venv\Scripts\python.exe -m pip install -r requirements-desktop.txt "pyinstaller>=6,<7"
if errorlevel 1 (
 echo 依赖安装失败。
 pause
 exit /b 1
)
.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean photo-sieve.spec
if errorlevel 1 (
 echo 打包失败，请保留错误信息。
 pause
 exit /b 1
)
.build-venv\Scripts\python.exe package_release.py
if errorlevel 1 (
 echo 打包压缩文件失败。
 pause
 exit /b 1
)
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
 .build-venv\Scripts\python.exe -c "from zipfile import ZipFile; ZipFile('release/PhotoSieve-Windows-x64.zip').extractall('dist/install-payload')"
 for /f %%V in ('.build-venv\Scripts\python.exe -c "from version import APP_VERSION; print(APP_VERSION)"') do "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=%%V installer.iss
 if errorlevel 1 exit /b 1
 echo 安装程序：release\PhotoSieve-Setup-Windows-x64.exe
) else (
 echo 如需生成安装程序，请安装 Inno Setup 6：https://jrsoftware.org/isdl.php
)
echo 完成：release\PhotoSieve-Windows-x64.zip 和更新清单
pause
