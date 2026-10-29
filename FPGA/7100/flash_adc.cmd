@echo off
cd /d "%~dp0"
call "C:\Xilinx\SDK\2018.3\bin\program_flash.bat" -f "%~dp0../../assets/fpga7100/BOOT.bin" -offset 0 -flash_type qspi-x8-dual_parallel -fsbl "%~dp0../../assets/fpga7100/fsbl.elf" -blank_check -verify -url tcp:127.0.0.1:3121
if errorlevel 1 exit /b 1
pause
