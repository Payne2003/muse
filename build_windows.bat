@echo off
REM Build Muse Mailbox cho Windows 10/11 (can Python 3.11+)
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt pyinstaller || goto :err
python -m pytest -q || goto :err
pyinstaller packaging\MuseMailbox.spec --noconfirm --clean || goto :err
echo.
echo Xong: dist\MuseMailbox\MuseMailbox.exe
echo (Tuy chon) Tao bo cai: iscc packaging\installer.iss
goto :eof
:err
echo Build that bai.
exit /b 1
