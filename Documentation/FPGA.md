# FPGA 原始工程

原始工程保存在 GitHub 的 `FPGA/7100/` 与 `FPGA/Fudan/`，不再分别挂多个 Release 压缩包。唯一部署包只携带当前启动镜像，工程重建在开发环境进行。

## 7100

目标器件为 `xc7z100ffg900-2`，使用 Vivado / SDK 2018.3。`network_adc/src/` 保存 RTL、约束和 BD 脚本，`sdk/` 保存处理器工程、BSP 与启动代码。

从工程根目录运行开发环境中的 Vivado：

```sh
vivado -mode batch -source FPGA/7100/build_adc.tcl
```

`adc_boot.bif` 给出 BOOT 镜像组成，组件位于完整开发环境的 `assets/fpga7100/`。更新启动介质时使用当前启动方式，确认启动开关与写入位置。部署包中的 `FPGA7100/BOOT.bin` 是可供该流程使用的镜像。

## 复旦微 PL

入口为 `FPGA/Fudan/fpai_demo_vivado.xpr`。开发环境需要 Vivado 2018.3，以及匹配的复旦微 Procise、器件库、IP 和授权。Git 中不包含供应商工具安装环境；大型或受限的第三方库需在开发环境中提供。

六路 FFT 路径为 FIR → FIFO → 帧复用 → `axis_fft_window` → FFT → 结果缓存。每通道 4096 点，前 64 点保留时间位置但置零；后 4032 点乘周期 Hann 窗。PHA 波束合成支路保持独立。

- 窗系数位于 `fpai_demo_vivado.srcs/adc_dsp/coe/`。
- 窗模块为 `fpai_demo_vivado.srcs/adc_dsp/axis_fft_window.v`。
- `tools/generate_fft_window.py` 生成系数；`tools/integrate_fft_window.tcl` 维护集成。
- PL 版本 `0x00010001` 对应 PL 加窗，PS 不再重复 IFFT/加窗/FFT。

实现后需经过对应 Procise 的 `DISABLE_ICAP,VCCM` 转换，再将 BIT 转成 Linux 加载格式：

```sh
python FPGA/Fudan/bit_to_bin.py converted.bit aoa_console.bin
```

`bit_to_bin.py` 是实际转换工具。更换位流时需要同时确认 PL 数据格式、版本、PS 解码与模型输入一致。供应商网表和工具版本会影响重建，不以源码文件齐全代替综合、实现及时序验证。
